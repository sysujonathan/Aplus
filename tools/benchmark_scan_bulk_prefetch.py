"""Reproducible system-path benchmark for scan read prefetching.

The fixture is synthetic and isolated, but the timed section uses Service.submit,
the real cache/version keys, per-file SHA-256 verification, per-stock durable
transactions, events and the final receipt. It never opens the user's runtime.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd


# Running this file by absolute path from a base checkout must import that
# checkout's workbench package, which permits exact base-vs-branch comparison.
sys.path.insert(0, str(Path.cwd()))

from workbench import market  # noqa: E402
from workbench.scan_cache import cached_answers, cache_key, calculation_versions, compatible_engines  # noqa: E402
from workbench.service import Service  # noqa: E402
from workbench.store import Store, digest, dumps, now  # noqa: E402
from workbench.strategies import catalog, scan_engine_version  # noqa: E402


STRATEGIES = [
    'MTR_MASTER',
    'STRATEGY_3K',
    'STRATEGY_STRUCTURAL_GAP',
    'STRATEGY_GAP_PINBAR',
    'STRATEGY_GAP_H2',
    'STRATEGY_AWIL',
]


def fixture(store, count):
    x = np.arange(160)
    close = 20 + np.sin(x / 4) * 2 + x / 100
    frame = pd.DataFrame({
        'date': pd.bdate_range('2024-01-01', periods=160).strftime('%Y-%m-%d'),
        'open': close - .1, 'high': close + .5, 'low': close - .5,
        'close': close, 'volume': 10000.,
    })[market.FIELDS]
    content = frame.to_csv(index=False, float_format='%.12g', lineterminator='\n').encode('utf-8')
    sha = digest(content)
    created = now()
    datasets = []
    market_dir = store.root / 'market'
    market_dir.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        code = f'sh.{600000 + number:06d}'
        identity = digest(f'{code}|daily|csv|前复权|{sha}'.encode())
        relative = f'market/{identity}.csv'
        (store.root / relative).write_bytes(content)
        datasets.append((identity, code, 'daily', 'csv', '前复权', frame.date.iloc[0],
                         frame.date.iloc[-1], len(frame), relative, sha, created))
    with store.connect() as db:
        db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)', datasets)

    selected = [catalog(store)[key] for key in STRATEGIES]
    versions = calculation_versions(selected)
    engine = scan_engine_version(include_frozen=False)
    asof = frame.date.iloc[-1]
    answers = [(cache_key(row[0], strategy.id, versions[strategy.id], 'daily', asof, engine),
                'null', created) for row in datasets for strategy in selected]
    with store.connect() as db:
        db.executemany('INSERT INTO scan_cache VALUES(?,?,?)', answers)
    return [row[0] for row in datasets], asof


def wait_for(store, job):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        if row['status'] not in {'queued', 'running'}:
            return row
        time.sleep(.01)
    raise TimeoutError('benchmark scan did not finish')


def run_once(count):
    with tempfile.TemporaryDirectory(prefix='aplus-prefetch-') as root:
        store = Store(root)
        dataset_ids, asof = fixture(store, count)
        query_counts = {'dataset_single': 0, 'dataset_batch': 0, 'cache_batch': 0}
        original_rows = store.rows

        def counted(sql, args=()):
            if 'FROM datasets WHERE id=?' in sql:
                query_counts['dataset_single'] += 1
            elif 'FROM datasets WHERE id IN' in sql:
                query_counts['dataset_batch'] += 1
            if 'FROM scan_cache WHERE key IN' in sql:
                query_counts['cache_batch'] += 1
            return original_rows(sql, args)

        store.rows = counted
        service = Service(store)
        started = time.perf_counter()
        try:
            row = wait_for(store, service.submit('scan', dict(
                datasets=dataset_ids, strategies=STRATEGIES,
                timeframe='daily', asof=asof)))
        finally:
            service.pool.shutdown()
        elapsed = time.perf_counter() - started
        report = json.loads(row['result'])
        expected = count * len(STRATEGIES)
        if row['status'] != 'completed' or report['success'] != expected or report['reused'] != expected:
            raise AssertionError((row['status'], report))
        return elapsed, query_counts


def measure_prefetch_memory(count):
    """Approximate incremental Python memory retained by the prefetch maps."""
    with tempfile.TemporaryDirectory(prefix='aplus-prefetch-memory-') as root:
        store = Store(root)
        dataset_ids, asof = fixture(store, count)
        selected = [catalog(store)[key] for key in STRATEGIES]
        versions = calculation_versions(selected)
        compatible = compatible_engines(selected)
        engine = scan_engine_version(include_frozen=False)
        legacy_engine = scan_engine_version()
        tracemalloc.start()
        lookups, lookup_keys = [], []
        for dataset_id in dataset_ids:
            keys = {s.id:cache_key(dataset_id,s.id,versions[s.id],'daily',asof,engine) for s in selected}
            legacy = {s.id:cache_key(dataset_id,s.id,s.version,'daily',asof,legacy_engine) for s in selected}
            bridged = {s.id:cache_key(dataset_id,s.id,versions[s.id],'daily',asof,compatible[s.id])
                       for s in selected if s.id in compatible}
            lookups.append((keys, legacy, bridged))
            lookup_keys.extend([*keys.values(), *legacy.values(), *bridged.values()])
        signals = cached_answers(store, lookup_keys)
        records = market.dataset_records(store, dataset_ids)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {
            'datasets': count,
            'retained_mib': round(current / 1024 / 1024, 3),
            'peak_mib': round(peak / 1024 / 1024, 3),
            'metadata_rows': len(records),
            'cache_rows': len(signals),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', nargs='+', type=int, default=[60, 300, 1000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--memory-only', action='store_true')
    args = parser.parse_args()
    if args.memory_only:
        print(dumps({'fixture': 'isolated synthetic prefetch allocation',
                     'results': [measure_prefetch_memory(count) for count in args.sizes]}))
        return
    results = []
    for count in args.sizes:
        rounds = [run_once(count) for _ in range(args.repeats)]
        results.append({
            'datasets': count,
            'strategies': len(STRATEGIES),
            'seconds': [round(item[0], 6) for item in rounds],
            'median_seconds': round(statistics.median(item[0] for item in rounds), 6),
            'queries': rounds[0][1],
        })
    print(dumps({'fixture': 'isolated synthetic cached full-scan system path', 'results': results}))


if __name__ == '__main__':
    main()
