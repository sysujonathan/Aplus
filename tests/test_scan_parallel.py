"""Real private subprocesses and the original durable Service contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tests.test_workbench import frame, store
from tests.test_h2_plan import h2_bars
from workbench.market import save_dataset
from workbench.scan_parallel import CPUWorker, OrderedComputations, open_computations, worker_count
from workbench.store import dumps
from workbench.strategies import catalog, prepare, prepare_indicators, signal_at_end
from workbench.service import Service


KEYS = ['MTR_MASTER', 'STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP',
        'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2', 'STRATEGY_AWIL']


def payload(store, did, keys=KEYS, timeframe='daily'):
    record = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
    specs = catalog(store)
    return dict(root=str(store.root), record=record, asof='2024-12-31',
                timeframe=timeframe, strategies=[dict(id=key, version=specs[key].version,
                                                      params=specs[key].params) for key in keys])


def run_scan(store, ids, keys=KEYS, **kwargs):
    service = Service(store)
    job = service.submit('scan', dict(datasets=ids, strategies=keys, timeframe='daily',
                                     asof='2024-12-31', **kwargs))
    service.pool.shutdown(wait=True)
    row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
    return service, row, json.loads(row['result'])


def enable(monkeypatch):
    monkeypatch.setattr('workbench.scan_parallel.MIN_STOCKS', 1)
    monkeypatch.setattr('workbench.scan_parallel.worker_count', lambda: 2)
    for prefix in ('MTR', 'THREE_K', 'GAP_PINBAR', 'AWIL', 'STRUCTURAL_GAP'):
        monkeypatch.setenv('APLUS_' + prefix + '_FAST_REJECT_MODE', 'OFF')


def business(report):
    return {key: report.get(key) for key in
            ('success', 'signals', 'errors', 'no_signal', 'observation_ids',
             'datasets', 'strategy_versions', 'reused', 'calculated', 'checkpoints')}


def test_real_worker_outputs_equal_original_six_detectors(store, frame):
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    task = payload(store, did)
    before = (store.root / 'workbench.sqlite3').read_bytes()
    files_before = sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob('*'))
    worker = CPUWorker()
    try:
        response = worker.compute(task, threading.Event())
        specs = catalog(store)
        data = prepare(frame, 'daily', task['asof'])
        data.attrs['code'] = 'sh.600000'
        enriched = prepare_indicators(data)
        expected = {key: dict(signal=signal_at_end(specs[key], data, prepared=enriched)) for key in KEYS}
        assert dumps(response['answers']) == dumps(expected)
    finally:
        worker.close()
    assert (store.root / 'workbench.sqlite3').read_bytes() == before
    assert sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob('*')) == files_before
    assert worker.process.poll() is not None


def test_parallel_service_payload_cache_order_and_repeat_match_serial(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001', 'sh.600001']]
    ids.insert(1, ids[0])  # Duplicate must reuse its already committed judgment.
    monkeypatch.setenv('APLUS_TICKFLOW_SCAN_WORKERS', '1')
    _, serial_row, serial = run_scan(store, ids)
    store.execute('DELETE FROM scan_cache')
    store.execute('DELETE FROM jobs')
    enable(monkeypatch)
    service, parallel_row, parallel = run_scan(store, ids)
    assert serial_row['status'] == parallel_row['status'] == 'completed'
    assert business(serial) == business(parallel)
    assert parallel['execution'] == dict(mode='processes', workers=2)
    assert all(pool.closed and all(w.process.poll() is not None for w in pool.processes)
               for pool in service._scan_compute_pools)
    _, _, reused = run_scan(store, ids)
    assert reused['reused'] == len(ids) * len(KEYS) and reused['calculated'] == 0
    assert 'execution' not in reused  # A completed stage starts no workers.
    calls = store.rows("SELECT detail FROM events WHERE job_id=? AND action='调用策略' ORDER BY seq",
                       (parallel_row['id'],))
    assert [(json.loads(row['detail'])['code'], json.loads(row['detail'])['strategy']) for row in calls] == [
        (code, key) for code in ['sh.600000', 'sz.000001', 'sh.600001'] for key in KEYS]


def test_worker_rejects_tamper_and_mismatched_version(store, frame):
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    worker = CPUWorker()
    try:
        task = payload(store, did)
        task['strategies'][0]['version'] = 'wrong'
        with pytest.raises(RuntimeError, match='版本或参数不一致'):
            worker.compute(task, threading.Event())
        task = payload(store, did)
        (store.root / task['record']['path']).write_text('broken')
        with pytest.raises(RuntimeError, match='校验不一致'):
            worker.compute(task, threading.Event())
    finally:
        worker.close()


def test_partial_strategy_cache_is_read_in_parent_and_only_missing_answers_are_computed(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001']]
    monkeypatch.setenv('APLUS_TICKFLOW_SCAN_WORKERS', '1')
    run_scan(store, ids, ['STRATEGY_3K'])
    _, _, serial = run_scan(store, ids)
    store.execute('DELETE FROM scan_cache')
    store.execute('DELETE FROM jobs')
    run_scan(store, ids, ['STRATEGY_3K'])
    enable(monkeypatch)
    _, row, parallel = run_scan(store, ids)
    assert row['status'] == 'completed' and business(serial) == business(parallel)
    assert parallel['reused'] == 2 and parallel['calculated'] == 10
    assert parallel['execution']['mode'] == 'processes'


def test_real_h2_pending_payload_and_weekly_results_survive_ipc(store, h2_bars):
    did = save_dataset(store, 'sh.600000', h2_bars, 'tickflow', '前复权')
    worker = CPUWorker()
    specs = catalog(store)
    try:
        task = payload(store, did, ['STRATEGY_GAP_H2'])
        task['asof'] = h2_bars.date.iloc[-1]
        answer = worker.compute(task, threading.Event())['answers']['STRATEGY_GAP_H2']['signal']
        assert answer['pending_state'] == 'PENDING'
        assert dumps(answer) == dumps(signal_at_end(specs['STRATEGY_GAP_H2'], h2_bars))
        task = payload(store, did, ['STRATEGY_GAP_PINBAR', 'STRATEGY_STRUCTURAL_GAP'], 'weekly')
        task['asof'] = h2_bars.date.iloc[-1]
        result = worker.compute(task, threading.Event())
        data = prepare(h2_bars, 'weekly', task['asof'])
        enriched = prepare_indicators(data)
        assert dumps(result['answers']) == dumps({key: dict(signal=signal_at_end(specs[key], data, prepared=enriched))
                                                for key in ['STRATEGY_GAP_PINBAR', 'STRATEGY_STRUCTURAL_GAP']})
    finally:
        worker.close()


def test_parallel_positive_observation_ids_and_prices_equal_serial(store, h2_bars, monkeypatch):
    import pandas as pd
    h2_bars['date'] = pd.bdate_range('2024-01-01', periods=len(h2_bars)).strftime('%Y-%m-%d')
    ids = [save_dataset(store, code, h2_bars, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001']]
    monkeypatch.setenv('APLUS_TICKFLOW_SCAN_WORKERS', '1')
    _, _, serial = run_scan(store, ids, ['STRATEGY_GAP_H2'], force=True)
    observations = store.rows('SELECT id,payload FROM observations ORDER BY id')
    enable(monkeypatch)
    _, row, parallel = run_scan(store, ids, ['STRATEGY_GAP_H2'], force=True)
    assert row['status'] == 'completed' and serial['signals'] == parallel['signals'] == 2
    assert business(serial) == business(parallel)
    assert observations == store.rows('SELECT id,payload FROM observations ORDER BY id')
    assert parallel['execution']['mode'] == 'processes'


def test_per_strategy_business_error_is_not_a_worker_crash(store, frame, monkeypatch):
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    from workbench.scan_worker import BuiltinsOnly, compute
    def failing(spec, *_args, **_kwargs):
        if spec.id == 'STRATEGY_3K':
            raise ValueError('business failure')
        return None
    monkeypatch.setattr('workbench.strategies.signal_at_end', failing)
    result = compute(payload(store, did), catalog(BuiltinsOnly()))
    assert result['answers']['STRATEGY_3K'] == dict(error='business failure')
    assert result['answers']['MTR_MASTER'] == dict(signal=None)
    assert len(result['answers']) == len(KEYS)


def test_prefetched_future_stock_is_rechecked_before_committing(store, frame, monkeypatch):
    enable(monkeypatch)
    ids = [save_dataset(store, code, frame, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001']]
    second = store.rows('SELECT path FROM datasets WHERE id=?', (ids[1],))[0]
    changed = False
    def tamper(point):
        nonlocal changed
        if point == 'after_stock_commit' and not changed:
            changed = True
            (store.root / second['path']).write_text('tampered after prefetch')
    monkeypatch.setattr('workbench.service._scan_crash_probe', tamper)
    _, row, report = run_scan(store, ids)
    assert row['status'] == 'partial'
    assert report['success'] == len(KEYS) and len(report['errors']) == len(KEYS)
    assert all('校验不一致' in item['error'] for item in report['errors'])
    assert len(store.rows('SELECT * FROM scan_cache')) == len(KEYS)


def test_cancel_interrupts_a_hung_private_process(monkeypatch):
    monkeypatch.setattr('workbench.scan_parallel.scan_command',
                        lambda: [sys.executable, '-B', '-c', 'import time;time.sleep(60)'])
    worker = CPUWorker()
    stop = threading.Event()
    timer = threading.Timer(.3, stop.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            worker.compute({}, stop)
    finally:
        worker.close()
        timer.join()
    assert worker.process.poll() is not None


def test_cpu_failure_falls_back_without_changing_business_results(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001']]
    monkeypatch.setenv('APLUS_TICKFLOW_SCAN_WORKERS', '1')
    _, _, serial = run_scan(store, ids, force=True)
    enable(monkeypatch)
    monkeypatch.setattr(OrderedComputations, '_compute',
                        lambda *_: (_ for _ in ()).throw(BrokenPipeError('private worker died')))
    _, row, parallel = run_scan(store, ids, force=True)
    assert row['status'] == 'completed' and business(parallel) == business(serial)
    assert 'private worker died' in parallel['execution']['fallback']


def test_real_child_exit_falls_back_and_preserves_cache(store, frame, monkeypatch):
    enable(monkeypatch)
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    monkeypatch.setattr('workbench.scan_parallel.scan_command',
                        lambda: [sys.executable, '-B', '-c', 'raise SystemExit(3)'])
    service, row, report = run_scan(store, [did])
    assert row['status'] == 'completed' and report['success'] == len(KEYS)
    assert report['execution']['fallback']
    assert len(store.rows('SELECT * FROM scan_cache')) == len(KEYS)
    assert all(w.process.poll() is not None for pool in service._scan_compute_pools for w in pool.processes)


def test_cancel_keeps_only_committed_stock_and_resume_reuses_it(store, frame, monkeypatch):
    enable(monkeypatch)
    ids = [save_dataset(store, code, frame, 'tickflow', '前复权')
           for code in ['sh.600000', 'sz.000001', 'sh.600001']]
    service = Service(store)
    original = service.check_stop

    def stop_after_first(job):
        row = store.rows('SELECT progress FROM jobs WHERE id=?', (job,))[0]
        if row['progress'] >= len(KEYS):
            service.cancel(job)
        original(job)

    monkeypatch.setattr(service, 'check_stop', stop_after_first)
    job = service.submit('scan', dict(datasets=ids, strategies=KEYS,
                                     timeframe='daily', asof='2024-12-31'))
    service.pool.shutdown(wait=True)
    row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
    assert row['status'] == 'cancelled'
    assert json.loads(row['result'])['success'] == len(KEYS)
    assert len(store.rows('SELECT * FROM scan_cache')) == len(KEYS)
    assert all(w.process.poll() is not None for pool in service._scan_compute_pools for w in pool.processes)
    _, row, report = run_scan(store, ids)
    assert row['status'] == 'completed' and report['reused'] == len(KEYS)


def test_pending_tasks_are_bounded_and_can_be_closed_without_results(store, frame):
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    tasks = [(i, payload(store, did)) for i in range(30)]
    pool = OrderedComputations(tasks, 2, threading.Event())
    assert len(pool.pending) == 4
    pool.close()
    pool.close()
    assert all(w.process.poll() is not None for w in pool.processes)


@pytest.mark.parametrize('value', ['1', '0', '-1', '9', 'invalid'])
def test_worker_limit_and_invalid_setting_use_serial(monkeypatch, value):
    monkeypatch.setenv('APLUS_TICKFLOW_SCAN_WORKERS', value)
    assert worker_count() == 1


def test_serial_sources_plugins_and_fast_reject_keep_existing_route(store, frame, monkeypatch):
    enable(monkeypatch)
    did = save_dataset(store, 'sh.600000', frame, 'baostock', '前复权')
    _, row, report = run_scan(store, [did])
    assert row['status'] == 'completed' and 'execution' not in report
    assert open_computations(store, {}, [], {}, [], {}, threading.Event(), True) is None


def test_source_and_installed_commands_dispatch_without_desktop(monkeypatch):
    import sys
    from workbench.process_entry import scan_command
    from tools import installed_entry
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert scan_command()[1:] == ['-B', '-u', '-m', 'workbench.scan_worker']
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert scan_command()[1:] == ['--scan-worker']
    monkeypatch.setattr(sys, 'argv', ['AplusApp.exe', '--scan-worker'])
    calls = []
    monkeypatch.setattr('workbench.installed_imports.install', lambda: calls.append('verified-loader'))
    monkeypatch.setattr('workbench.process_entry.restore_worker_stdio', lambda: calls.append('pipes'))
    monkeypatch.setattr('workbench.scan_worker.main', lambda: calls.append('compute'))
    installed_entry.main()
    assert calls == ['verified-loader', 'pipes', 'compute']


@pytest.mark.parametrize('point,committed', [('before_stock_transaction', False),
                                          ('inside_stock_transaction', False),
                                          ('after_stock_commit', True)])
def test_process_death_keeps_original_stock_transaction_boundary(store, frame, point, committed):
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    script = '''
import os,sys,threading
from workbench import service as module, scan_parallel
from workbench.service import Service
from workbench.store import Store,dumps,now
root,did,point=sys.argv[1:]
scan_parallel.MIN_STOCKS=1
scan_parallel.worker_count=lambda:2
store=Store(root)
svc=Service(store)
spec=dict(datasets=[did],strategies=['STRATEGY_3K'],timeframe='daily',asof='2024-12-31')
store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
              ('crash','scan','running',now(),dumps(spec)))
svc.cancel_flags['crash']=threading.Event()
def crash(current):
    if current==point: os._exit(77)
module._scan_crash_probe=crash
svc._scan('crash',spec)
raise SystemExit(2)
'''
    process = subprocess.run([sys.executable, '-B', '-c', script, str(store.root), did, point],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True,
                             env=dict(os.environ, APLUS_MTR_FAST_REJECT_MODE='OFF',
                                      APLUS_THREE_K_FAST_REJECT_MODE='OFF'), timeout=30)
    assert process.returncode == 77, process.stderr
    service = Service(store)
    try:
        row = store.rows("SELECT * FROM jobs WHERE id='crash'")[0]
        report = json.loads(row['result'])
        assert row['status'] == 'interrupted'
        assert report['success'] == report['calculated'] == int(committed)
        assert len(store.rows('SELECT * FROM scan_cache')) == int(committed)
    finally:
        service.pool.shutdown()
