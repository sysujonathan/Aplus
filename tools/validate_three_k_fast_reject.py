"""Reproducible real/synthetic 3K fast-reject validation on isolated stores."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.validate_mtr_shadow import (  # noqa: E402
    business_snapshot, copy_snapshots, historical_dates, load_snapshots,
    readonly_records, spaced, wait_for,
)
from workbench.service import Service  # noqa: E402
from workbench.store import Store, digest, dumps  # noqa: E402
from workbench.strategies import (  # noqa: E402
    calculate_prepared, catalog, prepare_indicators, signal_at_end,
)
from workbench.three_k_acceleration import (  # noqa: E402
    APPROVAL_ENV, CANARY_RATE_ENV, MODE_ENV, ThreeKFastRejector,
    assess_latest_bar, current_binding_id,
)


STRATEGIES = [
    'MTR_MASTER', 'STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP',
    'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2', 'STRATEGY_AWIL',
]


@contextmanager
def acceleration_mode(name):
    keys = (MODE_ENV, APPROVAL_ENV, CANARY_RATE_ENV)
    previous = {key: os.environ.get(key) for key in keys}
    os.environ[MODE_ENV] = name
    if name in {'CANARY', 'ACCELERATED'}:
        os.environ[APPROVAL_ENV] = current_binding_id()
        os.environ[CANARY_RATE_ENV] = '0'
    else:
        os.environ.pop(APPROVAL_ENV, None)
        os.environ.pop(CANARY_RATE_ENV, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def generated_bars(seed, rows=180):
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.18, rows))
    opening = close + rng.normal(0, 0.12, rows)
    high = np.maximum(opening, close) + rng.uniform(0.01, 0.35, rows)
    low = np.minimum(opening, close) - rng.uniform(0.01, 0.35, rows)
    return pd.DataFrame({
        'date': pd.bdate_range('2025-01-02', periods=rows).strftime('%Y-%m-%d'),
        'open': opening, 'high': high, 'low': low, 'close': close,
        'volume': rng.integers(1_000, 1_000_000, rows).astype(float),
    })


def direct_validation(spec, controller, snapshots, dates, generated_count):
    result = {
        'dates': dates, 'comparisons': 0, 'rejects': 0, 'passes': 0,
        'signals': 0, 'differences': [], 'original_seconds': 0.0,
        'gate_seconds': 0.0, 'passed_original_seconds': 0.0,
        'generated_comparisons': 0, 'generated_differences': [],
    }
    for day in dates:
        for snapshot in snapshots:
            frame = snapshot['frame'][snapshot['frame']['date'] <= day].reset_index(drop=True)
            if len(frame) < 125:
                continue
            prepared = prepare_indicators(frame)
            started = time.perf_counter()
            gate = assess_latest_bar(frame, prepared)
            result['gate_seconds'] += time.perf_counter() - started
            started = time.perf_counter()
            original = signal_at_end(spec, frame, prepared=prepared)
            original_seconds = time.perf_counter() - started
            result['original_seconds'] += original_seconds
            if not gate.reject:
                result['passed_original_seconds'] += original_seconds
            shadow = controller.run(lambda original=original: original, frame, prepared,
                                    f"{snapshot['record']['code']}|{day}").signal
            result['comparisons'] += 1
            result['rejects' if gate.safe and gate.reject else 'passes'] += 1
            result['signals'] += int(original is not None)
            if shadow != original or (gate.safe and gate.reject and original is not None):
                result['differences'].append({'code': snapshot['record']['code'],
                                              'date': day, 'original': original,
                                              'shadow': shadow})
    for seed in range(generated_count):
        frame = generated_bars(seed, 125 + seed % 176)
        prepared = prepare_indicators(frame)
        original = signal_at_end(spec, frame, prepared=prepared)
        gate = assess_latest_bar(frame, prepared)
        shadow = controller.run(lambda original=original: original, frame, prepared,
                                f'generated|{seed}').signal
        result['generated_comparisons'] += 1
        if shadow != original or (gate.safe and gate.reject and original is not None):
            result['generated_differences'].append(seed)
    projected = result['gate_seconds'] + result['passed_original_seconds']
    result['projected_accelerated_seconds'] = projected
    result['detector_improvement_pct'] = round(
        (1 - projected / result['original_seconds']) * 100, 2)
    return result


def historical_signal_validation(source_home, records, count, spec, controller):
    database = source_home / 'workbench.sqlite3'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        observed = {row[0] for row in db.execute(
            "SELECT DISTINCT code FROM observations WHERE strategy='STRATEGY_3K'")}
    chosen = {record['code']: record for record in spaced(records, count)}
    chosen.update((record['code'], record) for record in records if record['code'] in observed)
    snapshots = load_snapshots(source_home, list(chosen.values()), len(chosen))
    formal_signals = []
    candidates = 0
    for snapshot in snapshots:
        frame = snapshot['frame']
        prepared = prepare_indicators(frame)
        _instance, calculated = calculate_prepared(spec, prepared)
        offset = len(frame) - len(prepared)
        for index in np.flatnonzero(
                calculated['signal_3k_gap_test'].fillna(False).to_numpy()):
            raw_index = offset + index
            if raw_index < 124:
                continue
            candidates += 1
            prefix = frame.iloc[:raw_index + 1].reset_index(drop=True)
            enriched = prepare_indicators(prefix)
            original = signal_at_end(spec, prefix, prepared=enriched)
            if original is None:
                continue
            gate = assess_latest_bar(prefix, enriched)
            shadow = controller.run(lambda original=original: original, prefix, enriched,
                                    f"signal|{snapshot['record']['code']}|{original['asof']}").signal
            formal_signals.append({
                'code': snapshot['record']['code'], 'date': original['asof'],
                'gate_safe': gate.safe, 'gate_reject': gate.reject,
                'payload_equal': shadow == original, 'payload': original,
            })
    return {
        'stocks': len(snapshots), 'historical_candidate_rows': candidates,
        'formal_signals': len(formal_signals),
        'gate_failures': sum(row['gate_reject'] or not row['gate_safe']
                             for row in formal_signals),
        'payload_differences': sum(not row['payload_equal'] for row in formal_signals),
        'examples': formal_signals[:10],
    }


def service_once(name, snapshots):
    with tempfile.TemporaryDirectory(prefix='aplus-3k-service-') as directory:
        store = Store(directory)
        dataset_ids = copy_snapshots(store, snapshots)
        spec = dict(datasets=dataset_ids, strategies=STRATEGIES, timeframe='daily',
                    asof=min(snapshot['record']['end'] for snapshot in snapshots), force=True)
        service = Service(store)
        try:
            with acceleration_mode(name):
                started = time.perf_counter()
                row = wait_for(store, service.submit('scan', spec))
                elapsed = time.perf_counter() - started
            report = json.loads(row['result'])
            business = business_snapshot(store, report)
            return {'seconds': elapsed, 'status': row['status'],
                    'digest': digest(dumps(business).encode()), 'errors': report['errors']}
        finally:
            service.pool.shutdown(wait=True)


def service_benchmark(snapshots, rounds):
    samples = {name: [] for name in ('OFF', 'SHADOW', 'ACCELERATED')}
    orders = [('OFF', 'SHADOW', 'ACCELERATED'),
              ('ACCELERATED', 'OFF', 'SHADOW'),
              ('SHADOW', 'ACCELERATED', 'OFF')]
    for index in range(rounds):
        for name in orders[index % len(orders)]:
            samples[name].append(service_once(name, snapshots))
    digests = [row['digest'] for rows in samples.values() for row in rows]
    summary = {name: round(statistics.median(row['seconds'] for row in rows), 6)
               for name, rows in samples.items()}
    return {'stocks': len(snapshots), 'rounds': rounds, 'seconds': summary,
            'accelerated_change_vs_off_pct': round(
                (summary['ACCELERATED'] / summary['OFF'] - 1) * 100, 2),
            'business_equal': len(set(digests)) == 1, 'samples': samples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-home', required=True)
    parser.add_argument('--sample-stocks', type=int, default=300)
    parser.add_argument('--dates', type=int, default=5)
    parser.add_argument('--signal-stocks', type=int, default=1200)
    parser.add_argument('--generated', type=int, default=300)
    parser.add_argument('--service-stocks', type=int, default=60)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    source_home, schema, records, quality, sqlite_safety = readonly_records(
        args.source_home, 'baostock')
    snapshots = load_snapshots(source_home, records, args.sample_stocks)
    dates, _calendar = historical_dates(source_home, snapshots, args.dates)
    with tempfile.TemporaryDirectory(prefix='aplus-3k-binding-') as directory:
        store = Store(directory)
        spec = catalog(store)['STRATEGY_3K']
        with acceleration_mode('SHADOW'):
            controller = ThreeKFastRejector(store, 'three-k-validation', spec)
            actual_mode = controller.mode
            direct = direct_validation(spec, controller, snapshots, dates, args.generated)
            signals = historical_signal_validation(
                source_home, records, args.signal_stocks, spec, controller)
    performance_snapshots = spaced(snapshots, args.service_stocks)
    output = {
        'kind': 'three-k-fast-reject-validation-v1',
        'binding': current_binding_id(), 'actual_mode': actual_mode,
        'source': {'schema': schema, 'quality_rows': quality,
                   'sqlite_safety': sqlite_safety, 'network_used': False},
        'direct': direct, 'historical_signals': signals,
        'service': service_benchmark(performance_snapshots, args.rounds),
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + '\n', encoding='utf-8')
    if not args.quiet:
        print(encoded)
    if (actual_mode != 'SHADOW' or controller.fused or direct['differences']
            or direct['generated_differences'] or signals['gate_failures']
            or signals['payload_differences'] or not output['service']['business_equal']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
