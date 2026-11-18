"""Exactly one before/after pair on copied real prices and a transient gate.

The initial resource denial is injected identically, not claimed as historical
telemetry. Recovery uses the real current resource budget, never forced workers.
The supplied snapshot directory contains only copied prices and public scope.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from workbench import scan_parallel
from workbench.service import Service
from workbench.store import ROOT, Store, digest, dumps
from workbench.readiness import audit_scope, save_directory
from tools.benchmark_tickflow_parallel import KEYS


def baseline_module(ref, filename):
    name = 'workbench._benchmark_' + Path(filename).stem
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / 'workbench' / filename)
    sys.modules[name] = module
    code = subprocess.check_output(['git', 'show', f'{ref}:workbench/{filename}'], cwd=ROOT)
    exec(compile(code, module.__file__, 'exec'), module.__dict__)
    return module


def seed(folder, rows, day, target):
    store = Store(target)
    fields = ['id','code','timeframe','source','adjustment','start','end','rows','path','sha256','created']
    with store.connect() as db:
        db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       [tuple(row[key] for key in fields) for row in rows])
    for row in rows:
        content = (folder/'snapshots'/row['path']).read_bytes()
        if digest(content) != row['sha256']:
            raise ValueError('Copied sample SHA mismatch')
        store.write_artifact(row['path'], content)
    scope = folder/'scope'/'sources'/'tickflow'
    store.write_artifact('sources/tickflow/trading_calendar.json',
                         (scope/'trading_calendar.json').read_bytes())
    directory = pd.read_csv(scope/'directories'/f'{day}.csv', dtype=str)
    save_directory(store, directory[directory.code.isin({r['code'] for r in rows})], day, source='tickflow')
    audit = audit_scope(store, ['沪深主板'], day, source='tickflow', timeframe='daily', verify_files=True)
    if not audit['complete'] or audit['ready'] != len(rows):
        raise ValueError('Sample must independently pass its real scan gate: '+dumps(audit['gaps']))
    return store


def run(store, rows, day, service_class):
    service = service_class(store)
    start = time.perf_counter()
    job = service.submit('scan', dict(source='tickflow', boards=['沪深主板'],
        datasets=[r['id'] for r in rows], strategies=KEYS, timeframes=['daily'], asof=day))
    service.pool.shutdown(wait=True)
    elapsed = time.perf_counter()-start
    row = store.rows('SELECT status,result FROM jobs WHERE id=?', (job,))[0]
    report = json.loads(row['result'])
    if row['status'] != 'completed' or report['calculated'] != len(rows)*len(KEYS):
        raise ValueError('Incomplete scan or nonempty judgment cache: '+dumps(report))
    business = {key:report.get(key) for key in ('success','signals','no_signal','errors',
        'observation_ids','datasets','strategy_versions','calculation_versions','reused','calculated','checkpoints')}
    observations = store.rows('SELECT id,code,strategy,version,timeframe,asof,setup_date,dataset_id,payload '
                              'FROM observations ORDER BY id')
    cache = store.rows('SELECT key,signal FROM scan_cache ORDER BY key')
    identity = digest(dumps(dict(business=business, observations=observations, cache=cache,
                                 coverage=report['coverage'])).encode())
    result = dict(seconds=elapsed, digest=identity, calculated=report['calculated'],
        reused=report['reused'], signals=report['signals'], ready=report['coverage']['ready'],
        execution=report.get('execution'), decisions=report.get('parallel_decisions', []))
    print(dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    folder = args.snapshot.resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    rows, day = manifest['rows'], manifest['asof']
    if not 80 <= len(rows) <= 120:
        raise ValueError('Only the authorized 80–120 stock sample is allowed')
    if any((folder/mode).exists() for mode in ('before','after')):
        raise ValueError('Pair already started; do not silently rerun performance tests')
    os.environ[scan_parallel.WORKERS_ENV] = '2'
    if scan_parallel.worker_selection()['workers'] < 2:
        raise ValueError('Current physical/commit budget still requires serial; do not force workers')
    for prefix in ('MTR','THREE_K','GAP_PINBAR','AWIL','STRUCTURAL_GAP'):
        os.environ[f'APLUS_{prefix}_FAST_REJECT_MODE'] = 'OFF'
    before_store = seed(folder, rows, day, folder/'before')
    after_store = seed(folder, rows, day, folder/'after')
    original_open, original_selection = scan_parallel.open_computations, scan_parallel.worker_selection
    old_parallel = baseline_module(args.baseline, 'scan_parallel.py')
    old_service = baseline_module(args.baseline, 'service.py')
    # The same initial transient resource denial is presented to each version.
    old_parallel.worker_count = lambda: 1
    try:
        scan_parallel.open_computations = old_parallel.open_computations
        before = run(before_store, rows, day, old_service.Service)
        gc.collect()
        probes = []
        def recovered_selection():
            decision = original_selection()
            probes.append(True)
            return (dict(decision, workers=1, reason='memory_limit', retryable=True,
                         available_bytes=256*1024**2) if len(probes)==1 else decision)
        scan_parallel.open_computations = original_open
        scan_parallel.worker_selection = recovered_selection
        after = run(after_store, rows, day, Service)
    finally:
        scan_parallel.open_computations, scan_parallel.worker_selection = original_open, original_selection
    result = dict(baseline=args.baseline, stocks=len(rows), asof=day, strategies=KEYS,
        scenario='injected initial resource denial; subsequent probes use real current resources',
        cache='empty judgment cache; equally prepared quality; OS file cache not cleared',
        before=before, after=after, same_results=before['digest']==after['digest'],
        speedup=before['seconds']/after['seconds'])
    (folder/'comparison.json').write_text(dumps(result), encoding='utf-8')
    if not result['same_results'] or not after['execution'] or after['execution'].get('fallback'):
        raise ValueError('Comparison failed; analyze evidence before any further run')
    print(dumps(result), flush=True)


if __name__ == '__main__':
    main()
