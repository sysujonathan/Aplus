"""Read-only real-data validation for the three gap-family fast rejectors."""
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

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.validate_mtr_shadow import (  # noqa: E402
    business_snapshot, copy_snapshots, historical_dates, load_snapshots,
    readonly_records, spaced, wait_for,
)
from workbench.gap_family_acceleration import (  # noqa: E402
    CONFIGS, GapFamilyFastRejector, assess_latest_bar, current_binding_id,
)
from workbench.mtr_acceleration import (  # noqa: E402
    APPROVAL_ENV as MTR_APPROVAL, CANARY_RATE_ENV as MTR_RATE,
    MODE_ENV as MTR_MODE, current_binding_id as mtr_binding,
)
from workbench.service import Service  # noqa: E402
from workbench.store import Store, digest, dumps  # noqa: E402
from workbench.strategies import (  # noqa: E402
    calculate_prepared, catalog, prepare, prepare_indicators, signal_at_end,
)
from workbench.three_k_acceleration import (  # noqa: E402
    APPROVAL_ENV as K3_APPROVAL, CANARY_RATE_ENV as K3_RATE,
    MODE_ENV as K3_MODE, current_binding_id as k3_binding,
)


ALL_STRATEGIES = [
    'MTR_MASTER', 'STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP',
    'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2', 'STRATEGY_AWIL',
]


@contextmanager
def acceleration_modes(family='OFF', legacy='OFF'):
    names = [MTR_MODE, MTR_APPROVAL, MTR_RATE, K3_MODE, K3_APPROVAL, K3_RATE]
    for config in CONFIGS.values():
        names.extend([config.mode_env, config.approval_env, config.rate_env])
    before = {name: os.environ.get(name) for name in names}
    try:
        os.environ[MTR_MODE] = legacy
        os.environ[K3_MODE] = legacy
        if legacy == 'ACCELERATED':
            os.environ[MTR_APPROVAL], os.environ[MTR_RATE] = mtr_binding(), '0'
            os.environ[K3_APPROVAL], os.environ[K3_RATE] = k3_binding(), '0'
        for strategy_id, config in CONFIGS.items():
            strategy_mode = family.get(strategy_id, 'OFF') if isinstance(family, dict) else family
            os.environ[config.mode_env] = strategy_mode
            if strategy_mode == 'ACCELERATED':
                os.environ[config.approval_env] = current_binding_id(strategy_id)
                os.environ[config.rate_env] = '0'
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def generated_bars(seed, rows=180):
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.18, rows))
    opening = close + rng.normal(0, 0.12, rows)
    high = np.maximum(opening, close) + rng.uniform(0.04, 0.35, rows)
    low = np.minimum(opening, close) - rng.uniform(0.04, 0.35, rows)
    return pd.DataFrame({
        'date': pd.bdate_range('2025-01-02', periods=rows).strftime('%Y-%m-%d'),
        'open': opening, 'high': high, 'low': low, 'close': close,
        'volume': rng.integers(10_000, 1_000_000, rows).astype(float),
    })


def direct_validation(store, snapshots, dates, generated_count):
    specs = catalog(store)
    output = {}
    with acceleration_modes('SHADOW'):
        controllers = {strategy_id: GapFamilyFastRejector(
            store, f'validate-{strategy_id}', specs[strategy_id]) for strategy_id in CONFIGS}
    for strategy_id, controller in controllers.items():
        spec = specs[strategy_id]
        timeframes = CONFIGS[strategy_id].metadata['supported_timeframes']
        result = {'comparisons': 0, 'rejects': 0, 'passes': 0, 'signals': 0,
                  'differences': [], 'business_exceptions': 0,
                  'original_seconds': 0.0, 'gate_seconds': 0.0,
                  'passed_original_seconds': 0.0, 'by_timeframe': {}}
        for timeframe in timeframes:
            counts = {'comparisons': 0, 'rejects': 0, 'passes': 0, 'signals': 0}
            for day in dates:
                for snapshot in snapshots:
                    if not (snapshot['frame']['date'] <= day).any():
                        continue
                    raw = prepare(snapshot['frame'], timeframe, day)
                    if len(raw) < 125:
                        continue
                    prepared = prepare_indicators(raw)
                    started = time.perf_counter()
                    gate = assess_latest_bar(strategy_id, raw, prepared)
                    result['gate_seconds'] += time.perf_counter() - started
                    started = time.perf_counter()
                    try:
                        original = signal_at_end(spec, raw, prepared=prepared)
                    except Exception as exc:
                        result['business_exceptions'] += 1
                        original = {'__exception__': type(exc).__name__, 'message': str(exc)}
                    elapsed = time.perf_counter() - started
                    result['original_seconds'] += elapsed
                    if not gate.reject:
                        result['passed_original_seconds'] += elapsed
                    if '__exception__' in original if isinstance(original, dict) else False:
                        shadow = original
                    else:
                        shadow = controller.run(lambda original=original: original, raw, prepared,
                                                f'{strategy_id}|{timeframe}|{snapshot["record"]["code"]}|{day}').signal
                    counts['comparisons'] += 1
                    counts['rejects' if gate.safe and gate.reject else 'passes'] += 1
                    counts['signals'] += int(original is not None and not (
                        isinstance(original, dict) and '__exception__' in original))
                    if shadow != original or (gate.safe and gate.reject and original is not None):
                        result['differences'].append({
                            'code': snapshot['record']['code'], 'date': day,
                            'timeframe': timeframe, 'gate': gate.__dict__,
                            'original': original, 'shadow': shadow,
                        })
            result['by_timeframe'][timeframe] = counts
            for key in counts:
                result[key] += counts[key]
        generated_differences = []
        for seed in range(generated_count):
            raw = generated_bars(seed, 125 + seed % 176)
            prepared = prepare_indicators(raw)
            original = signal_at_end(spec, raw, prepared=prepared)
            gate = assess_latest_bar(strategy_id, raw, prepared)
            shadow = controller.run(lambda original=original: original, raw, prepared,
                                    f'{strategy_id}|generated|{seed}').signal
            if shadow != original or (gate.safe and gate.reject and original is not None):
                generated_differences.append(seed)
        projected = result['gate_seconds'] + result['passed_original_seconds']
        result.update(
            actual_mode_at_start='SHADOW', actual_mode_at_end=controller.mode,
            binding=controller.binding_id, fused=controller.fused,
            generated_comparisons=generated_count,
            generated_differences=generated_differences,
            projected_accelerated_seconds=round(projected, 6),
            detector_improvement_pct=round(
                (1 - projected / result['original_seconds']) * 100, 2)
            if result['original_seconds'] else None,
        )
        output[strategy_id] = result
    return output


def historical_signals(store, records, source_home, count):
    specs = catalog(store)
    snapshots = load_snapshots(source_home, records, count)
    output = {}
    with acceleration_modes('SHADOW'):
        controllers = {strategy_id: GapFamilyFastRejector(
            store, f'signals-{strategy_id}', specs[strategy_id]) for strategy_id in CONFIGS}
    for strategy_id, controller in controllers.items():
        spec = specs[strategy_id]
        result = {'stocks': len(snapshots), 'candidate_rows': 0, 'formal_signals': 0,
                  'gate_failures': 0, 'payload_differences': 0, 'examples': [],
                  'by_timeframe': {}}
        for timeframe in CONFIGS[strategy_id].metadata['supported_timeframes']:
            counts = {'candidate_rows': 0, 'formal_signals': 0, 'gate_failures': 0}
            for snapshot in snapshots:
                raw = prepare(snapshot['frame'], timeframe, snapshot['record']['end'])
                if len(raw) < 125:
                    continue
                prepared = prepare_indicators(raw)
                _instance, calculated = calculate_prepared(spec, prepared)
                column = CONFIGS[strategy_id].metadata['signal_column']
                indexes = np.flatnonzero(calculated[column].fillna(False).to_numpy())
                offset = len(raw) - len(prepared)
                for index in indexes:
                    raw_index = offset + int(index)
                    if raw_index < 124:
                        continue
                    counts['candidate_rows'] += 1
                    prefix = raw.iloc[:raw_index + 1].reset_index(drop=True)
                    enriched = prepare_indicators(prefix)
                    original = signal_at_end(spec, prefix, prepared=enriched)
                    if original is None:
                        continue
                    counts['formal_signals'] += 1
                    gate = assess_latest_bar(strategy_id, prefix, enriched)
                    shadow = controller.run(lambda original=original: original, prefix, enriched,
                                            f'signal|{strategy_id}|{timeframe}|{snapshot["record"]["code"]}|{original["asof"]}').signal
                    if not gate.safe or gate.reject:
                        counts['gate_failures'] += 1
                    if shadow != original:
                        result['payload_differences'] += 1
                    if len(result['examples']) < 8:
                        result['examples'].append({
                            'code': snapshot['record']['code'], 'timeframe': timeframe,
                            'asof': original['asof'], 'setup_date': original['setup_date'],
                            'entry': original['entry'], 'stop': original['stop'],
                            'target': original['target'], 'score': original['score'],
                            'gate_safe': gate.safe, 'gate_reject': gate.reject,
                        })
            result['by_timeframe'][timeframe] = counts
            for key in counts:
                result[key] += counts[key]
        output[strategy_id] = result
    return output


def service_once(name, snapshots, temp_root):
    with tempfile.TemporaryDirectory(prefix=f'aplus-gap-family-{name.lower()}-',
                                     dir=temp_root) as directory:
        store = Store(directory)
        dataset_ids = copy_snapshots(store, snapshots)
        spec = {'datasets': dataset_ids, 'strategies': ALL_STRATEGIES,
                'timeframe': 'daily',
                'asof': min(snapshot['record']['end'] for snapshot in snapshots),
                'force': True}
        isolated = {
            f'{strategy_id}_ACCELERATED': ({strategy_id: 'ACCELERATED'}, 'OFF')
            for strategy_id in CONFIGS
        }
        modes = {'OFF': ('OFF', 'OFF'), 'FAMILY_ACCELERATED': ('ACCELERATED', 'OFF'),
                 'FIVE_ACCELERATED': ('ACCELERATED', 'ACCELERATED'), **isolated}
        modes['GAP_PINBAR_AWIL_ACCELERATED'] = ({
            'STRATEGY_GAP_PINBAR': 'ACCELERATED', 'STRATEGY_AWIL': 'ACCELERATED'}, 'OFF')
        service = Service(store)
        try:
            with acceleration_modes(*modes[name]):
                started = time.perf_counter()
                row = wait_for(store, service.submit('scan', spec))
                seconds = time.perf_counter() - started
            report = json.loads(row['result'])
            business = business_snapshot(store, report)
            return {'seconds': round(seconds, 6), 'status': row['status'],
                    'business_digest': digest(dumps(business).encode()),
                    'errors': report['errors'], 'signals': report['signals'],
                    'events': len(store.rows('SELECT seq FROM events')),
                    'database_bytes': store.path.stat().st_size}
        finally:
            service.pool.shutdown(wait=True)


def service_benchmark(snapshots, rounds, temp_root):
    names = ('OFF', *(f'{strategy_id}_ACCELERATED' for strategy_id in CONFIGS),
             'GAP_PINBAR_AWIL_ACCELERATED', 'FAMILY_ACCELERATED', 'FIVE_ACCELERATED')
    orders = [names, tuple(reversed(names)), names[2:] + names[:2]]
    samples = {name: [] for name in names}
    for index in range(rounds):
        for name in orders[index % len(orders)]:
            samples[name].append(service_once(name, snapshots, temp_root))
    medians = {name: round(statistics.median(row['seconds'] for row in rows), 6)
               for name, rows in samples.items()}
    digests = [row['business_digest'] for rows in samples.values() for row in rows]
    return {
        'stocks': len(snapshots), 'rounds': rounds, 'seconds': medians,
        'family_change_vs_off_pct': round(
            (medians['FAMILY_ACCELERATED'] / medians['OFF'] - 1) * 100, 2),
        'five_change_vs_off_pct': round(
            (medians['FIVE_ACCELERATED'] / medians['OFF'] - 1) * 100, 2),
        'isolated_change_vs_off_pct': {
            strategy_id: round(
                (medians[f'{strategy_id}_ACCELERATED'] / medians['OFF'] - 1) * 100, 2)
            for strategy_id in CONFIGS
        },
        'structural_incremental_change_pct': round(
            (medians['FAMILY_ACCELERATED']
             / medians['GAP_PINBAR_AWIL_ACCELERATED'] - 1) * 100, 2),
        'business_equal_all_rounds': len(set(digests)) == 1,
        'samples': samples,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-home', required=True)
    parser.add_argument('--sample-stocks', type=int, default=300)
    parser.add_argument('--dates', type=int, default=5)
    parser.add_argument('--signal-stocks', type=int, default=1200)
    parser.add_argument('--generated', type=int, default=300)
    parser.add_argument('--service-stocks', type=int, default=60)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--temp-root')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(args.temp_root or output_path.parent).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    source_home, schema, records, quality, sqlite_safety = readonly_records(
        args.source_home, 'baostock')
    snapshots = load_snapshots(source_home, records, args.sample_stocks)
    dates, _calendar = historical_dates(source_home, snapshots, args.dates)
    with tempfile.TemporaryDirectory(prefix='aplus-gap-family-direct-',
                                     dir=temp_root) as directory:
        store = Store(directory)
        direct = direct_validation(store, snapshots, dates, args.generated)
        signals = historical_signals(store, records, source_home, args.signal_stocks)
    performance_snapshots = spaced(snapshots, args.service_stocks)
    output = {
        'kind': 'gap-family-fast-reject-validation-v1',
        'source': {'schema': schema, 'records': len(records), 'quality_rows': quality,
                   'sqlite_safety': sqlite_safety, 'network_used': False},
        'dates': dates, 'direct': direct, 'historical_signals': signals,
        'service': service_benchmark(performance_snapshots, args.rounds, temp_root),
    }
    output_path.write_text(dumps(output) + '\n', encoding='utf-8')
    print(json.dumps({
        'output': str(output_path),
        'direct': {key: {'comparisons': value['comparisons'],
                         'signals': value['signals'],
                         'differences': len(value['differences']),
                         'improvement_pct': value['detector_improvement_pct']}
                   for key, value in direct.items()},
        'historical_signals': {key: value['formal_signals']
                               for key, value in signals.items()},
        'service': output['service'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
