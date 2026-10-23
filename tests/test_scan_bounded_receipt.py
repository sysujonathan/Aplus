import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.test_workbench import frame, store, wait_for
from workbench.market import save_dataset
from workbench.scan_receipt import receipt_for_read
from workbench.service import Service
from workbench.store import dumps, now


def _spec(dataset_ids, *, force=False):
    return dict(datasets=dataset_ids, strategies=['STRATEGY_3K'], timeframe='daily',
                asof='2024-12-31', force=force)


def test_running_job_keeps_bounded_summary_then_terminal_contract(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'csv', '前复权')
           for code in ('sh.600000', 'sz.000001')]
    entered, release = threading.Event(), threading.Event()
    calls = []

    def calculate(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 2:
            entered.set()
            assert release.wait(10)
        return None

    monkeypatch.setattr('workbench.service.signal_at_end', calculate)
    service = Service(store)
    try:
        job = service.submit('scan', _spec(ids))
        assert entered.wait(10)
        running = store.rows('SELECT status,progress,total,result FROM jobs WHERE id=?', (job,))[0]
        summary = json.loads(running['result'])
        assert running['status'] == 'running' and running['progress'] == 1
        assert summary['_receipt'] == 'scan-progress-v1'
        assert summary['success'] == summary['no_signal'] == summary['calculated'] == 1
        assert 'datasets' not in summary and 'errors' not in summary and 'observation_ids' not in summary
        assert len(running['result'].encode('utf-8')) < 512
        display = receipt_for_read(store, job, running['result'])
        assert display['datasets'] == ids and display['success'] == 1
        assert display['errors'] == [] and display['observation_ids'] == []

        release.set()
        terminal = wait_for(store, job)
        report = json.loads(terminal['result'])
        assert terminal['status'] == 'completed'
        assert report['success'] == report['no_signal'] == report['calculated'] == 2
        assert report['datasets'] == ids and report['errors'] == []
        assert report['observation_ids'] == [] and report['checkpoints']['daily']['signals'] == 0
        assert '_receipt' not in report
    finally:
        release.set()
        service.pool.shutdown()


def test_terminal_receipt_is_identical_to_growing_progress_writer(store, frame, monkeypatch):
    import workbench.service as service_module

    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    signal = dict(asof=frame.date.iloc[-1], setup_date=frame.date.iloc[-1],
                  entry=20, stop=19, target=22)
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *_a, **_k: signal)
    original_begin = service_module.begin_receipt
    original_progress = service_module.progress_receipt

    def old_begin(target_store, target_job, report, _timeframe):
        target_store.execute('UPDATE jobs SET result=? WHERE id=?',
                             (dumps(report), target_job))

    service = Service(store)
    try:
        monkeypatch.setattr(service_module, 'begin_receipt', old_begin)
        monkeypatch.setattr(service_module, 'progress_receipt', lambda report, _timeframe: report)
        growing = wait_for(store, service.submit('scan', _spec([dataset_id], force=True)))

        monkeypatch.setattr(service_module, 'begin_receipt', original_begin)
        monkeypatch.setattr(service_module, 'progress_receipt', original_progress)
        bounded = wait_for(store, service.submit('scan', _spec([dataset_id], force=True)))

        assert growing['status'] == bounded['status'] == 'completed'
        assert json.loads(growing['result']) == json.loads(bounded['result'])
    finally:
        service.pool.shutdown()


class HardCrash(BaseException):
    pass


@pytest.mark.parametrize(
    ('crash_point', 'committed'),
    [('before_stock_transaction', False),
     ('inside_stock_transaction', False),
     ('after_stock_commit', True)],
)
def test_restart_rebuilds_exactly_the_last_committed_stock(
        store, frame, monkeypatch, crash_point, committed):
    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    service = Service(store)
    job = 'crash-' + crash_point
    spec = _spec([dataset_id], force=True)
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  (job, 'scan', 'running', now(), dumps(spec)))
    service.cancel_flags[job] = threading.Event()
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *_a, **_k: None)

    def crash(point):
        if point == crash_point:
            raise HardCrash(point)

    monkeypatch.setattr('workbench.service._scan_crash_probe', crash)
    try:
        with pytest.raises(HardCrash, match=crash_point):
            service._scan(job, spec)
    finally:
        service.pool.shutdown()

    assert json.loads(store.rows('SELECT result FROM jobs WHERE id=?', (job,))[0]['result'])['_receipt'] == 'scan-progress-v1'
    restarted = Service(store)
    try:
        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        report = json.loads(row['result'])
        assert row['status'] == 'interrupted'
        assert report['success'] == report['no_signal'] == report['calculated'] == int(committed)
        assert report['signals'] == report['reused'] == 0
        assert report['errors'] == [] and report['observation_ids'] == []
        assert report['datasets'] == [dataset_id]
        assert '_receipt' not in report and report['checkpoints'] == {}
        assert len(store.rows('SELECT * FROM scan_cache')) == int(committed)
        assert len(store.rows("SELECT * FROM events WHERE action='调用策略'")) == int(committed)

        monkeypatch.setattr('workbench.service._scan_crash_probe', lambda _point: None)
        resumed = wait_for(store, restarted.submit('scan', _spec([dataset_id])))
        resumed_report = json.loads(resumed['result'])
        assert resumed['status'] == 'completed'
        assert resumed_report['reused'] == int(committed)
        assert resumed_report['calculated'] == int(not committed)
        assert resumed_report['success'] == 1
        assert len(store.rows('SELECT * FROM scan_cache')) == 1
    finally:
        restarted.pool.shutdown()


def test_manual_stop_materializes_full_partial_receipt(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'csv', '前复权')
           for code in ('sh.600000', 'sz.000001')]
    service = Service(store)
    calls = []

    def calculate(*_args, **_kwargs):
        calls.append(1)
        service.cancel_flags[next(iter(service.cancel_flags))].set()
        return None

    monkeypatch.setattr('workbench.service.signal_at_end', calculate)
    try:
        row = wait_for(store, service.submit('scan', _spec(ids)))
        report = json.loads(row['result'])
        assert row['status'] == 'cancelled'
        assert report['success'] == report['no_signal'] == report['calculated'] == 1
        assert report['datasets'] == ids and report['errors'] == []
        assert report['observation_ids'] == [] and '_receipt' not in report
    finally:
        service.pool.shutdown()


def test_rebuilt_receipt_preserves_signal_and_error_order(store, frame, monkeypatch):
    ids = [save_dataset(store, code, frame, 'csv', '前复权')
           for code in ('sh.600000', 'sz.000001')]
    service = Service(store)
    job = 'signal-error-crash'
    spec = _spec(ids, force=True)
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  (job, 'scan', 'running', now(), dumps(spec)))
    service.cancel_flags[job] = threading.Event()
    signal = dict(asof=frame.date.iloc[-1], setup_date=frame.date.iloc[-1],
                  entry=20, stop=19, target=22)
    calls = []

    def calculate(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return signal
        raise ValueError('合法的策略失败样本')

    commits = []
    monkeypatch.setattr('workbench.service.signal_at_end', calculate)
    monkeypatch.setattr('workbench.service._scan_crash_probe',
                        lambda point: (commits.append(point) if point == 'after_stock_commit' else None))
    try:
        report = service._scan(job, spec)
        # Simulate power loss after the second durable commit, before terminal receipt publication.
        assert len(commits) == 2
        assert report['signals'] == 1 and len(report['errors']) == 1
    finally:
        service.pool.shutdown()

    restarted = Service(store)
    try:
        rebuilt = json.loads(store.rows('SELECT result FROM jobs WHERE id=?', (job,))[0]['result'])
        assert rebuilt['signals'] == 1 and len(rebuilt['observation_ids']) == 1
        assert rebuilt['errors'] == [{
            'code': 'sz.000001', 'strategy': 'STRATEGY_3K', 'timeframe': 'daily',
            'error': '合法的策略失败样本'}]
        assert rebuilt['success'] == 1 and rebuilt['calculated'] == 1
    finally:
        restarted.pool.shutdown()


def test_stock_transaction_write_error_rolls_back_and_keeps_full_failure_receipt(
        store, frame, monkeypatch):
    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    raised = False

    def fail_once(point):
        nonlocal raised
        if point == 'inside_stock_transaction' and not raised:
            raised = True
            raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr('workbench.service.signal_at_end', lambda *_a, **_k: None)
    monkeypatch.setattr('workbench.service._scan_crash_probe', fail_once)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', _spec([dataset_id], force=True)))
        report = json.loads(row['result'])
        assert row['status'] == 'failed' and row['message'] == 'database is locked'
        assert report['success'] == report['calculated'] == 0
        assert report['datasets'] == [dataset_id]
        assert report['errors'][-1]['error'] == 'database is locked'
        assert '_receipt' not in report
        assert not store.rows('SELECT * FROM scan_cache')
        assert not store.rows("SELECT * FROM events WHERE action='调用策略'")
    finally:
        service.pool.shutdown()


@pytest.mark.skipif(os.name != 'nt', reason='Windows hard-crash recovery contract')
@pytest.mark.parametrize(
    ('crash_point', 'committed'),
    [('before_stock_transaction', False),
     ('inside_stock_transaction', False),
     ('after_stock_commit', True)],
)
def test_windows_process_kill_recovers_transaction_boundary(
        store, frame, crash_point, committed):
    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    job = 'process-' + crash_point
    child = r'''
import os
import sys
import threading

import workbench.service as service_module
from workbench.service import Service
from workbench.store import Store, dumps, now

root, job, point, dataset_id = sys.argv[1:]
store = Store(root)
service = Service(store)
spec = dict(datasets=[dataset_id], strategies=['STRATEGY_3K'], timeframe='daily',
            asof='2024-12-31', force=True)
store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
              (job, 'scan', 'running', now(), dumps(spec)))
service.cancel_flags[job] = threading.Event()
service_module.signal_at_end = lambda *_args, **_kwargs: None
def kill_here(current):
    if current == point:
        os._exit(77)
service_module._scan_crash_probe = kill_here
service._scan(job, spec)
raise SystemExit(2)
'''
    completed = subprocess.run(
        [sys.executable, '-c', child, str(store.root), job, crash_point, dataset_id],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 77, completed.stdout + completed.stderr
    assert store.rows('SELECT status FROM jobs WHERE id=?', (job,))[0]['status'] == 'running'

    restarted = Service(store)
    try:
        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        report = json.loads(row['result'])
        assert row['status'] == 'interrupted'
        assert report['success'] == report['calculated'] == report['no_signal'] == int(committed)
        assert report['datasets'] == [dataset_id] and report['errors'] == []
        assert len(store.rows('SELECT * FROM scan_cache')) == int(committed)
    finally:
        restarted.pool.shutdown()
