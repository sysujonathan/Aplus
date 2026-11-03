"""Manual synthetic benchmark; never reads the installed application's runtime.

Run with a NEW output directory. Timings are evidence, not pytest assertions.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from workbench import h2_plan, market

BASE = '9f74c295a2ecfb7aefccfe35f68edc03c8ad188f'


def baseline(path, name):
    module = types.ModuleType('workbench.' + name)
    module.__package__ = 'workbench'
    source = subprocess.check_output(['git', 'show', f'{BASE}:{path}'], cwd=ROOT).decode('utf-8')
    exec(compile(source, f'{path}@{BASE}', 'exec'), module.__dict__)
    return module


def sample(rng, size, volatility=.03, spread=.1, opening_sigma=.01):
    close = 20 + np.cumsum(rng.normal(0, volatility, size))
    opening = close + rng.normal(0, opening_sigma, size)
    return pd.DataFrame(dict(
        date=pd.bdate_range('2013-01-01', periods=size).strftime('%Y-%m-%d'),
        open=opening, high=np.maximum(opening, close) + spread,
        low=np.minimum(opening, close) - spread, close=close, volume=10000.))


def validation(old_market):
    rng = np.random.default_rng(42)
    results = {}
    for size in (300, 3000):
        frame = sample(rng, size)
        pd.testing.assert_frame_equal(old_market.validate_bars(frame), market.validate_bars(frame))
        times = {'before': [], 'after': []}
        for repeat in range(7):
            order = ('before', 'after') if repeat % 2 else ('after', 'before')
            for label in order:
                validate = old_market.validate_bars if label == 'before' else market.validate_bars
                start = time.perf_counter()
                for _ in range(70):
                    validate(frame)
                times[label].append((time.perf_counter() - start) * 1000 / 70)
        results[size] = {key: statistics.median(values) for key, values in times.items()}
        results[size]['ratio'] = results[size]['before'] / results[size]['after']
    return results


def scan(old_market, folder):
    from workbench.acceleration_profile import STRATEGIES
    from workbench.service import Service
    from workbench.store import Store
    # Only this probe process; do not approve or change anyone's saved profile.
    for _, prefix in STRATEGIES.values():
        os.environ[prefix + '_MODE'] = 'OFF'
    old_h2 = baseline('workbench/h2_plan.py', 'benchmark_h2')
    new_validate, new_plan = market.validate_bars, h2_plan.plan_at_end
    rng = np.random.default_rng(23)
    samples = [sample(rng, 3000, volatility=.07, spread=.15, opening_sigma=.02) for _ in range(20)]
    strategies = [value[0] for value in STRATEGIES.values()] + ['STRATEGY_GAP_H2']
    results, reference = [], None
    try:
        for repeat in range(3):
            order = ('before', 'after') if repeat % 2 else ('after', 'before')
            for label in order:
                market.validate_bars, h2_plan.plan_at_end = new_validate, new_plan
                store = Store(folder / f'scan-{repeat}-{label}')
                datasets = [market.save_dataset(store, f'sh.{600000+j}', frame, 'csv', '不复权')
                            for j, frame in enumerate(samples)]
                if label == 'before':
                    market.validate_bars, h2_plan.plan_at_end = old_market.validate_bars, old_h2.plan_at_end
                service = Service(store)
                try:
                    start = time.perf_counter()
                    job = service.submit('scan', dict(datasets=datasets, strategies=strategies,
                                                     timeframe='daily', asof='2024-12-31'))
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
                        if row['status'] not in ('queued', 'running'):
                            break
                        time.sleep(.01)
                    elapsed = time.perf_counter() - start
                    assert row['status'] == 'completed', row
                    report = json.loads(row['result'])
                    assert report['calculated'] == 120, report
                    rows = store.rows('SELECT code,strategy,payload FROM observations ORDER BY code,strategy')
                    signature = [(r['code'], r['strategy'], json.loads(r['payload'])) for r in rows]
                    if reference is None:
                        reference = signature
                    assert signature == reference
                    results.append(dict(round=repeat, label=label, seconds=elapsed,
                                        judgments=120, signals=report['signals']))
                finally:
                    service.pool.shutdown(wait=True)
    finally:
        market.validate_bars, h2_plan.plan_at_end = new_validate, new_plan
    summary = {label: statistics.median(r['seconds'] for r in results if r['label'] == label)
               for label in ('before', 'after')}
    summary.update(ratio=summary['before'] / summary['after'], equivalent=True,
                   synthetic=True, cases=results)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New isolated evidence directory')
    parser.add_argument('--scan', action='store_true', help='Also compare six-strategy Service jobs')
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    old = baseline('workbench/market.py', 'benchmark_market')
    evidence = dict(baseline=BASE, synthetic=True, validation_ms=validation(old))
    if args.scan:
        evidence['scan'] = scan(old, folder)
    (folder / 'result.json').write_bytes((json.dumps(evidence, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(evidence))
