"""Reproducible isolated benchmark for growing versus bounded scan receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import tempfile
import time
import tracemalloc
import threading
from pathlib import Path
from unittest.mock import patch

from workbench.scan_receipt import progress_receipt
from workbench.store import Store, dumps, now


def _base(size):
    datasets = [hashlib.sha256(f'dataset-{index}'.encode()).hexdigest() for index in range(size)]
    return dict(success=0, signals=0, errors=[], no_signal=0, observation_ids=[],
                datasets=datasets, timeframe='daily', strategy_versions={'STRATEGY_3K': 'frozen'},
                reused=0, calculated=0, engine_version='engine',
                calculation_versions={'STRATEGY_3K': 'calculation'},
                asof='2026-10-22', total=size, checkpoints={})


def _run(size, bounded):
    with tempfile.TemporaryDirectory(prefix='aplus-receipt-') as directory:
        path = Path(directory) / 'receipt.sqlite3'
        db = sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,result TEXT)')
        db.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY,detail TEXT)')
        db.execute("INSERT INTO jobs VALUES('job','{}')")
        db.commit()
        report = _base(size)
        serialized = 0
        sql = 1
        transactions = 1
        tracemalloc.start()
        started = time.perf_counter()
        if bounded:
            seed = dumps({'version': 'scan-progress-v1', 'timeframe': 'daily', 'base': report})
            summary = dumps(progress_receipt(report, 'daily'))
            db.execute('INSERT INTO events(detail) VALUES(?)', (seed,))
            db.execute("UPDATE jobs SET result=? WHERE id='job'", (summary,))
            db.commit()
            serialized += len(seed.encode()) + len(summary.encode())
            sql += 2
        else:
            payload = dumps(report)
            db.execute("UPDATE jobs SET result=? WHERE id='job'", (payload,))
            db.commit()
            serialized += len(payload.encode())
            sql += 1
        transactions += 1

        for index in range(size):
            report['success'] += 1
            report['calculated'] += 1
            if index % 20 == 0:
                report['signals'] += 1
                report['observation_ids'].append(hashlib.sha256(f'observation-{index}'.encode()).hexdigest())
                event = dumps({'observation': report['observation_ids'][-1], 'code': f'code-{index}'})
            elif index % 97 == 0:
                report['errors'].append(dict(code=f'code-{index}', strategy='STRATEGY_3K',
                                             timeframe='daily', error='benchmark error'))
                event = dumps(report['errors'][-1])
            else:
                report['no_signal'] += 1
                event = dumps({'code': f'code-{index}'})
            payload = dumps(progress_receipt(report, 'daily') if bounded else report)
            db.execute('INSERT INTO events(detail) VALUES(?)', (event,))
            db.execute("UPDATE jobs SET result=? WHERE id='job'", (payload,))
            db.commit()
            serialized += len(event.encode()) + len(payload.encode())
            sql += 2
            transactions += 1

        terminal = dumps(report)
        db.execute("UPDATE jobs SET result=? WHERE id='job'", (terminal,))
        db.commit()
        serialized += len(terminal.encode())
        sql += 1
        transactions += 1
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.close()
        return dict(seconds=elapsed, serialized_bytes=serialized, database_bytes=path.stat().st_size,
                    peak_bytes=peak, sql=sql, transactions=transactions)


def benchmark(sizes, rounds):
    output = {'kind': 'isolated-receipt-persistence', 'rounds': rounds, 'sizes': {}}
    for size in sizes:
        samples = {'growing': [], 'bounded': []}
        for round_index in range(rounds):
            order = ('growing', 'bounded') if round_index % 2 == 0 else ('bounded', 'growing')
            for name in order:
                samples[name].append(_run(size, name == 'bounded'))
        result = {}
        for name, rows in samples.items():
            result[name] = {key: statistics.median(row[key] for row in rows)
                            for key in rows[0]}
        result['improvement_pct'] = round(
            (1 - result['bounded']['seconds'] / result['growing']['seconds']) * 100, 2)
        result['serialization_reduction_pct'] = round(
            (1 - result['bounded']['serialized_bytes'] / result['growing']['serialized_bytes']) * 100, 2)
        output['sizes'][str(size)] = result
    return output


class _Frame:
    def __init__(self):
        self.attrs = {}

    def __len__(self):
        return 160


def _service_run(size, bounded):
    from workbench.service import Service
    datasets = [hashlib.sha256(f'service-dataset-{index}'.encode()).hexdigest()
                for index in range(size)]
    records = {dataset: dict(id=dataset, code=f'code-{index}')
               for index, dataset in enumerate(datasets)}
    with tempfile.TemporaryDirectory(prefix='aplus-scan-service-') as directory:
        store = Store(directory)
        service = Service(store)
        job = 'benchmark-job'
        spec = dict(datasets=datasets, strategies=['STRATEGY_3K'], timeframe='daily',
                    asof='2026-10-22', force=True)
        store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                      (job, 'scan', 'running', now(), dumps(spec)))
        service.cancel_flags[job] = threading.Event()

        def old_begin(target_store, target_job, report, _timeframe):
            target_store.execute('UPDATE jobs SET result=? WHERE id=?',
                                 (dumps(report), target_job))

        patches = [
            patch('workbench.service.dataset_records', lambda _store, _ids: records),
            patch('workbench.service.load_dataset',
                  lambda _store, dataset, _job=None, record=None: (_Frame(), records[dataset])),
            patch('workbench.service.prepare', lambda data, _timeframe, _asof: data),
            patch('workbench.service.prepare_indicators', lambda _data: None),
            patch('workbench.service.signal_at_end', lambda *_args, **_kwargs: None),
        ]
        if not bounded:
            patches.extend([
                patch('workbench.service.begin_receipt', old_begin),
                patch('workbench.service.progress_receipt', lambda report, _timeframe: report),
            ])
        tracemalloc.start()
        started = time.perf_counter()
        try:
            for active in patches:
                active.start()
            report = service._scan(job, spec)
            store.execute('UPDATE jobs SET result=? WHERE id=?', (dumps(report), job))
        finally:
            for active in reversed(patches):
                active.stop()
            service.pool.shutdown()
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result = json.loads(store.rows('SELECT result FROM jobs WHERE id=?', (job,))[0]['result'])
        assert result == report and result['success'] == size
        return dict(seconds=elapsed, database_bytes=store.path.stat().st_size,
                    peak_bytes=peak, sql=3 * size + (3 if bounded else 2),
                    transactions=size + 2)


def service_benchmark(sizes, rounds):
    output = {'kind': 'scan-service-orchestration', 'rounds': rounds, 'sizes': {}}
    for size in sizes:
        samples = {'growing': [], 'bounded': []}
        for round_index in range(rounds):
            order = ('growing', 'bounded') if round_index % 2 == 0 else ('bounded', 'growing')
            for name in order:
                samples[name].append(_service_run(size, name == 'bounded'))
        result = {}
        for name, rows in samples.items():
            result[name] = {key: statistics.median(row[key] for row in rows)
                            for key in rows[0]}
        result['improvement_pct'] = round(
            (1 - result['bounded']['seconds'] / result['growing']['seconds']) * 100, 2)
        output['sizes'][str(size)] = result
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', nargs='+', type=int, default=[60, 300, 1000])
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--service', action='store_true')
    args = parser.parse_args()
    runner = service_benchmark if args.service else benchmark
    print(json.dumps(runner(args.sizes, args.rounds), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
