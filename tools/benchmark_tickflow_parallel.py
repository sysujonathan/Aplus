"""Compare complete scan Service runs on isolated copies of real TickFlow bytes.

Source SQLite uses mode=ro, query_only and a read transaction; no Store is
opened at the source. No vendor requests, production scans or account reads.
This subset benchmark excludes full-market directory/history preflight.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.service import Service
from workbench.store import Store, digest, dumps

KEYS = ['MTR_MASTER', 'STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP',
        'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2', 'STRATEGY_AWIL']


def snapshot(source, output, count):
    with sqlite3.connect((source / 'workbench.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = [dict(row) for row in db.execute('''
            SELECT * FROM (SELECT *, ROW_NUMBER() OVER
                (PARTITION BY code ORDER BY end DESC,created DESC,rowid DESC) AS rank
                FROM datasets WHERE source='tickflow' AND timeframe='daily'
                AND adjustment='前复权') WHERE rank=1 AND rows>=125 ORDER BY code''')]
        day = max(row['end'] for row in rows)
        rows = [row for row in rows if row['end'] == day]
        if len(rows) < count:
            raise ValueError('同一最新日期真实 TickFlow 样本不足')
        chosen = [rows[i * len(rows) // count] for i in range(count)]
        copied = []
        for row in chosen:
            relative = Path(row['path'])
            original = (source / relative).resolve()
            if not original.is_relative_to(source):
                raise ValueError('源行情路径越界')
            content = original.read_bytes()
            if digest(content) != row['sha256']:
                raise ValueError('源快照校验失败，未使用')
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            copied.append({key: value for key, value in row.items() if key != 'rank'})
    return copied, day


def run(root, rows, day, workers):
    store = Store(root)
    with store.connect() as db:
        db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       [tuple(row.values()) for row in rows])
    # Reuse identical immutable files without timing fixture generation.
    for row in rows:
        target = store.root / row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(root.parent / 'snapshots' / row['path'], target)
    os.environ['APLUS_TICKFLOW_SCAN_WORKERS'] = str(workers)
    for prefix in ('MTR', 'THREE_K', 'GAP_PINBAR', 'AWIL', 'STRUCTURAL_GAP'):
        os.environ['APLUS_' + prefix + '_FAST_REJECT_MODE'] = 'OFF'
    service = Service(store)
    start = time.perf_counter()
    job = service.submit('scan', dict(datasets=[row['id'] for row in rows], strategies=KEYS,
                                     timeframe='daily', asof=day))
    service.pool.shutdown(wait=True)
    elapsed = time.perf_counter() - start
    task = store.rows('SELECT status,result FROM jobs WHERE id=?', (job,))[0]
    report = json.loads(task['result'])
    observations = store.rows('SELECT id,code,strategy,version,timeframe,asof,setup_date,dataset_id,payload '
                              'FROM observations ORDER BY id')
    cache = store.rows('SELECT key,signal FROM scan_cache ORDER BY key')
    business = {key: report.get(key) for key in
                ('success', 'signals', 'errors', 'no_signal', 'observation_ids', 'datasets',
                 'strategy_versions', 'reused', 'calculated', 'checkpoints')}
    fingerprint = digest(dumps(dict(report=business, observations=observations, cache=cache)).encode())
    if task['status'] != 'completed' or report['calculated'] != len(rows) * len(KEYS):
        raise ValueError('扫描不完整或复用了缓存，不作为冷扫描性能证据')
    if workers > 1 and (report.get('execution', {}).get('mode') != 'processes'
                        or report['execution'].get('fallback')):
        raise ValueError('并行未生效或发生回退，不作为性能证据')
    return dict(seconds=round(elapsed, 6), business_digest=fingerprint,
                signals=report['signals'], calculated=report['calculated'],
                execution=report.get('execution', dict(mode='serial', workers=1)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=192)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if args.count < 64 or args.rounds < 1 or not 2 <= args.workers <= 8:
        parser.error('count>=64, rounds>=1, workers=2..8')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, day = snapshot(args.source.resolve(), output / 'snapshots', args.count)
    results = []
    for number in range(args.rounds):
        for workers in ([1, args.workers] if number % 2 == 0 else [args.workers, 1]):
            result = run(output / f'round-{number}-workers-{workers}', rows, day, workers)
            result.update(round=number, requested_workers=workers)
            results.append(result)
            print(dumps(result), flush=True)
    if len({item['business_digest'] for item in results}) != 1:
        raise ValueError('业务结果不一致，不能报告提速')
    serial = statistics.median(item['seconds'] for item in results if item['requested_workers'] == 1)
    parallel = statistics.median(item['seconds'] for item in results if item['requested_workers'] != 1)
    summary = dict(count=len(rows), asof=day, rounds=args.rounds,
                   median_history_rows=statistics.median(row['rows'] for row in rows),
                   serial_seconds=serial, parallel_seconds=parallel,
                   speedup=serial / parallel, reduction_pct=(1 - parallel / serial) * 100,
                   results=results,
                   cache_state='empty judgment cache; OS file cache not cleared',
                   scope='real TickFlow copied bytes, full scan Service; excludes market preflight and network update')
    (output / 'result.json').write_text(dumps(summary), encoding='utf-8')
    print(dumps(summary), flush=True)


if __name__ == '__main__':
    main()
