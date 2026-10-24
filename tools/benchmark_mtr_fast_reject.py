"""Reproducible MTR fast-reject benchmark on isolated generated OHLCV.

The system-path measurement uses the real Service, file SHA verification,
public indicators, six frozen strategies, per-stock durable transactions,
bounded receipts and scan caches.  It never opens the user's runtime or data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.mtr_acceleration import (  # noqa: E402
    APPROVAL_ENV,
    CANARY_RATE_ENV,
    MODE_ENV,
    MTRFastRejector,
    current_binding_id,
)
from workbench.service import Service  # noqa: E402
from workbench.store import Store, digest, dumps, now  # noqa: E402
from workbench.strategies import catalog, prepare_indicators, signal_at_end  # noqa: E402


STRATEGIES = [
    'MTR_MASTER',
    'STRATEGY_3K',
    'STRATEGY_STRUCTURAL_GAP',
    'STRATEGY_GAP_PINBAR',
    'STRATEGY_GAP_H2',
    'STRATEGY_AWIL',
]


def generated_bars(seed, rows):
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.16, rows))
    opening = close + rng.normal(0, 0.11, rows)
    high = np.maximum(opening, close) + rng.uniform(0.04, 0.35, rows)
    low = np.minimum(opening, close) - rng.uniform(0.04, 0.35, rows)
    # The final bar is deliberately legal but not a frozen MTR signal bar.
    opening[-1] = close[-1] - 0.1
    high[-1] = close[-1] + 0.2
    low[-1] = close[-1] - 0.3
    return pd.DataFrame({
        'date': pd.bdate_range('2024-01-02', periods=rows).strftime('%Y-%m-%d'),
        'open': opening, 'high': high, 'low': low, 'close': close,
        'volume': rng.integers(10_000, 1_000_000, rows).astype(float),
    })


@contextmanager
def mode(name):
    names = [MODE_ENV, APPROVAL_ENV, CANARY_RATE_ENV]
    before = {key: os.environ.get(key) for key in names}
    try:
        os.environ[MODE_ENV] = name
        if name == 'ACCELERATED':
            os.environ[APPROVAL_ENV] = current_binding_id()
            os.environ[CANARY_RATE_ENV] = '0'
        else:
            os.environ.pop(APPROVAL_ENV, None)
            os.environ.pop(CANARY_RATE_ENV, None)
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def timed(call):
    tracemalloc.start()
    started = time.perf_counter()
    result = call()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak


def detector_once(samples, rows, include_indicators):
    frames = [generated_bars(seed, rows) for seed in range(samples)]
    prepared = None if include_indicators else [prepare_indicators(frame) for frame in frames]

    with tempfile.TemporaryDirectory(prefix='aplus-mtr-detector-') as root:
        store = Store(root)
        spec = catalog(store)['MTR_MASTER']
        with mode('ACCELERATED'):
            fast = MTRFastRejector(store, 'benchmark', spec)

        def run(accelerated):
            output = []
            for index, frame in enumerate(frames):
                enriched = prepare_indicators(frame) if include_indicators else prepared[index]
                original = lambda: signal_at_end(spec, frame, prepared=enriched)
                output.append(fast.run(original, frame, enriched, index).signal
                              if accelerated else original())
            return output

        baseline, baseline_seconds, baseline_peak = timed(lambda: run(False))
        accelerated, accelerated_seconds, accelerated_peak = timed(lambda: run(True))
    if accelerated != baseline:
        raise AssertionError('detector payload mismatch')
    return {
        'samples': samples,
        'bars': rows,
        'off_seconds': baseline_seconds,
        'accelerated_seconds': accelerated_seconds,
        'improvement_pct': (1 - accelerated_seconds / baseline_seconds) * 100,
        'off_peak_bytes': baseline_peak,
        'accelerated_peak_bytes': accelerated_peak,
    }


def fixture(store, count, rows):
    frame = generated_bars(20261023, rows)
    content = frame.to_csv(index=False, float_format='%.12g', lineterminator='\n').encode()
    sha = digest(content)
    created = now()
    datasets = []
    market = store.root / 'market'
    market.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        code = f'sh.{600000 + number:06d}'
        identity = digest(f'{code}|daily|csv|前复权|{sha}'.encode())
        relative = f'market/{identity}.csv'
        (store.root / relative).write_bytes(content)
        datasets.append((identity, code, 'daily', 'csv', '前复权', frame.date.iloc[0],
                         frame.date.iloc[-1], len(frame), relative, sha, created))
    with store.connect() as db:
        db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)', datasets)
    return [row[0] for row in datasets], frame.date.iloc[-1]


def wait_for(store, job):
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        if row['status'] not in {'queued', 'running'}:
            return row
        time.sleep(0.01)
    raise TimeoutError('benchmark scan did not finish')


def system_once(name, count, rows):
    with tempfile.TemporaryDirectory(prefix='aplus-mtr-system-') as root, mode(name):
        store = Store(root)
        datasets, asof = fixture(store, count, rows)
        spec = dict(datasets=datasets, strategies=STRATEGIES, timeframe='daily',
                    asof=asof, force=True)
        service = Service(store)
        try:
            row, elapsed, peak = timed(lambda: wait_for(store, service.submit('scan', spec)))
            report = json.loads(row['result'])
            if row['status'] != 'completed' or report['success'] != count * len(STRATEGIES):
                raise AssertionError((row['status'], report))
            cold_business = {
                'success': report['success'], 'signals': report['signals'],
                'no_signal': report['no_signal'], 'errors': report['errors'],
                'observations': store.rows(
                    'SELECT code,strategy,timeframe,asof,setup_date,dataset_id,payload '
                    'FROM observations ORDER BY code,strategy,asof'),
            }
            cache_spec = dict(spec)
            cache_spec.pop('force')
            cached, cached_seconds, _cached_peak = timed(
                lambda: wait_for(store, service.submit('scan', cache_spec)))
            cached_report = json.loads(cached['result'])
            if cached['status'] != 'completed' or not cached_report.get('stage_reused'):
                raise AssertionError((cached['status'], cached_report))
            return {
                'business': cold_business,
                'seconds': elapsed,
                'peak_bytes': peak,
                'cached_seconds': cached_seconds,
                'database_bytes': store.path.stat().st_size,
                'cache_rows': len(store.rows('SELECT key FROM scan_cache')),
                'event_rows': len(store.rows('SELECT seq FROM events')),
            }
        finally:
            service.pool.shutdown()


def rounded(row):
    return {key: (round(value, 6) if isinstance(value, float) else value)
            for key, value in row.items() if key != 'business'}


def write_output(path, encoded):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--datasets', type=int, default=60)
    parser.add_argument('--bars', type=int, default=300)
    parser.add_argument('--detector-samples', type=int, default=120)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    detector = {'detector_only': [], 'public_indicators_plus_mtr': []}
    system = {'OFF': [], 'SHADOW': [], 'ACCELERATED': []}
    for round_index in range(args.rounds):
        detector['detector_only'].append(
            detector_once(args.detector_samples, args.bars, include_indicators=False))
        detector['public_indicators_plus_mtr'].append(
            detector_once(args.detector_samples, args.bars, include_indicators=True))
        orders = [
            ['OFF', 'SHADOW', 'ACCELERATED'],
            ['ACCELERATED', 'OFF', 'SHADOW'],
            ['SHADOW', 'ACCELERATED', 'OFF'],
        ]
        order = orders[round_index % len(orders)]
        round_results = {name: system_once(name, args.datasets, args.bars) for name in order}
        if any(round_results['OFF']['business'] != round_results[name]['business']
               for name in ('SHADOW', 'ACCELERATED')):
            raise AssertionError('six-strategy system-path business mismatch')
        for name in system:
            system[name].append(round_results[name])

    detector_summary = {}
    for kind, rows in detector.items():
        detector_summary[kind] = {
            'samples': rows[0]['samples'], 'bars': rows[0]['bars'],
            'off_seconds': round(statistics.median(r['off_seconds'] for r in rows), 6),
            'accelerated_seconds': round(
                statistics.median(r['accelerated_seconds'] for r in rows), 6),
            'improvement_pct': round(statistics.median(r['improvement_pct'] for r in rows), 2),
            'off_peak_bytes': int(statistics.median(r['off_peak_bytes'] for r in rows)),
            'accelerated_peak_bytes': int(
                statistics.median(r['accelerated_peak_bytes'] for r in rows)),
        }
    system_summary = {
        name: rounded({key: statistics.median(row[key] for row in rows)
                       for key in rows[0] if key != 'business'})
        for name, rows in system.items()
    }
    off = system_summary['OFF']['seconds']
    accelerated = system_summary['ACCELERATED']['seconds']
    output = {
        'fixture': 'isolated generated legal OHLCV; not formal market data',
        'binding': current_binding_id(),
        'rounds': args.rounds,
        'detector': detector_summary,
        'six_strategy_cold_service': {
            'datasets': args.datasets, 'bars': args.bars,
            'OFF': system_summary['OFF'], 'SHADOW': system_summary['SHADOW'],
            'ACCELERATED': system_summary['ACCELERATED'],
            'improvement_pct': round((1 - accelerated / off) * 100, 2),
            'business_equal': True,
        },
    }
    encoded = dumps(output)
    if args.output:
        write_output(args.output, encoded)
    if not args.quiet:
        print(encoded)


if __name__ == '__main__':
    main()
