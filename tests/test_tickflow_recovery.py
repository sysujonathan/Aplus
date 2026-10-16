"""Bounded recovery with a virtual clock, never real network/sleep/runtime."""
import json
import ssl
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_premarket_resume import seeded
from workbench.market import save_dataset
from workbench.sources import save_coverage
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.tickflow import TickFlowHTTP
from workbench.tickflow_recovery import request_with_recovery
from workbench.service import Service


@pytest.fixture
def clock(monkeypatch):
    import workbench.tickflow_recovery as recovery
    class Clock:
        value = time.time()
        elapsed = 0
        def time(self): return self.value
        def monotonic(self): return self.elapsed
        def advance(self, seconds):
            self.value += seconds
            self.elapsed += seconds
    clock = Clock()
    monkeypatch.setattr(recovery, 'time', clock)
    monkeypatch.setattr(recovery, 'ProviderGuard', lambda st, source: ProviderGuard(st, source, clock.time))
    return clock


def fake_service(store, clock, cancel=False, on_wait=None):
    class Token:
        stopped = False
        def wait(self, seconds):
            clock.advance(seconds)
            if on_wait: on_wait()
            self.stopped = cancel
            return self.stopped
    token = Token()
    messages = []
    def check(*_):
        if token.stopped: raise InterruptedError('stopped')
    return SimpleNamespace(store=store,cancel_flags={'job':token},check_stop=check,
                           progress=lambda *args:messages.append(args[-1])), messages


def eof():
    error = ProviderError('tickflow', 'batch', 'SSL: UNEXPECTED_EOF_WHILE_READING')
    error.transient_transport = True
    return error


def test_ssl_disconnect_waits_for_persistent_guard_then_repeats_only_failed_request(store, clock):
    service, messages = fake_service(store, clock)
    calls = []
    report = dict(processed=100,requested=200)
    def request():
        calls.append(clock.time())
        if len(calls) == 1: raise eof()
        return 'same batch complete'
    assert request_with_recovery(service,'job',request,{'used':False},report) == 'same batch complete'
    assert len(calls) == 2 and calls[1]-calls[0] >= 30
    assert messages and all('等待' in m for m in messages)
    assert report['transport_recovery']['recovered']
    assert len(ProviderGuard(store,'tickflow').state()['failures']) == 1  # Not double counted.


@pytest.mark.parametrize('error', [
    ProviderError('tickflow','batch','limited','403'),
    ProviderError('tickflow','batch','limited','429'),
    ProviderError('tickflow','batch','limited','10001011'),
    ProviderError('tickflow','batch','限制访问'),
    ProviderError('tickflow','batch','unknown error'),
    ProviderError('tickflow','batch','HTTP 503','503'),
])
def test_limits_or_unclassified_errors_never_wait_retry_or_clear_guard(store, clock, error):
    # Even a malformed transient marker cannot override explicit restrictions.
    if error.code or '限制' in str(error): error.transient_transport = True
    service, messages = fake_service(store, clock)
    calls = []
    def request():
        calls.append(1)
        raise error
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,{'used':False},{})
    assert calls == [1] and messages == [] and clock.elapsed == 0
    assert ProviderGuard(store,'tickflow').state()['until'] > clock.time()


def test_persistent_disconnect_stops_after_one_recovery_not_one_retry_per_batch(store, clock):
    service, _ = fake_service(store, clock)
    budget, calls = {'used':False}, []
    def request():
        calls.append(1)
        raise eof()
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,budget,{})
    assert calls == [1,1] and budget['used']
    clock.advance(61)
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,budget,{})
    assert calls == [1,1,1]  # Budget belongs to whole continue invocation.


def test_cancel_during_cooldown_never_reconnects(store, clock):
    service, _ = fake_service(store, clock, cancel=True)
    calls = []
    def request():
        calls.append(1)
        raise eof()
    with pytest.raises(InterruptedError):
        request_with_recovery(service,'job',request,{'used':False},{})
    assert calls == [1]


def test_long_cooldown_or_new_restriction_is_not_bypassed(store, clock):
    def restrict():
        ProviderGuard(store,'tickflow',clock.time).failure(ProviderError('tickflow','batch','blacklist','10001011'))
    service, _ = fake_service(store,clock,on_wait=restrict)
    calls = []
    def request():
        calls.append(1)
        raise eof()
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,{'used':False},{})
    assert calls == [1]
    assert ProviderGuard(store,'tickflow').state()['last_error']['error_code'] == '10001011'


def test_explicit_manual_request_and_excessive_retry_after_never_auto_retry(store, clock):
    service, _ = fake_service(store, clock)
    calls = []
    def request():
        calls.append(1)
        error = eof()
        error.retry_after = 600
        raise error
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,None,{})
    assert calls == [1]
    clock.advance(601)
    with pytest.raises(ProviderError):
        request_with_recovery(service,'job',request,{'used':False},{})
    assert calls == [1,1] and clock.elapsed == 601


def test_ipc_preserves_typed_transport_marker_without_real_process():
    import io
    import queue
    from workbench.tickflow import TickFlow
    provider = TickFlow()
    provider.process = SimpleNamespace(stdin=io.StringIO())
    provider.messages = queue.Queue()
    provider.messages.put({'error': eof().detail()})
    provider._stop = lambda:None
    with pytest.raises(ProviderError) as caught:
        provider._request('batch',[],1)
    assert caught.value.transient_transport is True


def test_missing_prices_are_shown_as_not_scanned_in_parent_summary(store, monkeypatch):
    monkeypatch.setattr(Service,'_sync',lambda *_a:dict(success=1))
    monkeypatch.setattr(Service,'_scan',lambda *_a:dict(success=1,signals=0,errors=[{'code':'sz.002382'}],
        coverage_by_timeframe={'daily':dict(gaps=[dict(code='sz.002382',category='stale_tail')])}))
    service = Service(store)
    try:
        row = wait_for(store,service.submit('premarket',dict(sources=['tickflow'],boards=['沪深主板'],
            start='2024-01-01',timeframe='daily',strategies=['STRATEGY_GAP_H2'])))
        assert row['status'] == 'partial'
        summary = json.loads(row['result'])['sources']['tickflow']['scan_result']
        assert summary['not_scanned_count'] == 1 and summary['signals'] == 0
    finally:
        service.pool.shutdown()


def test_header_marks_same_day_partial_scan_instead_of_unqualified_checkmark():
    from gui.toolbar import format_header_data_status
    text = format_header_data_status('2026-10-15','2026-10-15',['沪深主板'],
        dict(source='tickflow',expected=2,ready=1,gaps=[dict(code='sz.002382',category='stale_tail')]))
    assert '信号 2026-10-15 ⚠部分' in text


@pytest.mark.parametrize('exception,transient', [
    (urllib.error.URLError(ssl.SSLEOFError('EOF')), True),
    (TimeoutError('timed out'), True),
    (urllib.error.URLError(ConnectionResetError('reset')), True),
    (urllib.error.URLError(ssl.SSLCertVerificationError('certificate')), False),
    (urllib.error.HTTPError('https://example.invalid',429,'Too many',{},None), False),
    (ValueError('bad JSON'), False),
])
def test_only_typed_transport_errors_receive_recoverable_ipc_flag(monkeypatch, exception, transient):
    provider = TickFlowHTTP()
    def opened(*_a, **_k): raise exception
    provider.opener = SimpleNamespace(open=opened)
    monkeypatch.setattr('workbench.tickflow.time.sleep', lambda *_:None)
    with pytest.raises(ProviderError) as caught:
        provider.request('/unit')
    assert caught.value.transient_transport == transient
    assert bool(caught.value.detail().get('transient_transport')) == transient


def test_one_key_recovers_missing_gh2_stock_and_only_scans_after_its_tail_is_saved(store, frame, clock, monkeypatch):
    # Reproduce the actual failure shape: an already-finished stock, then an
    # interrupted batch containing 蓝帆医疗. Detection is synthetic, never edits
    # frozen GH2 rules or asserts an invented market signal.
    codes = ('sz.002382','sh.600000')
    start, day, _ = seeded(store, frame, 'tickflow', codes=codes)
    store.execute("DELETE FROM datasets WHERE code='sz.002382'")
    did = save_dataset(store,codes[0],frame.iloc[:-1],'tickflow','前复权')
    save_coverage(store,codes[0],did,start,frame.date.iloc[-2],'tickflow')
    calls, seen = [], []
    class Provider:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def universe(self,*_): pytest.fail('应复用同日目录')
        def preload(self, group, a, b):
            calls.append(list(group))
            if len(calls) == 1: raise eof()
        def fetch(self,code,a,b): return frame.loc[frame.date.between(a,b)].copy()
    monkeypatch.setattr('workbench.tickflow.TickFlow',Provider)
    def scan(self,job,spec):
        from workbench.readiness import audit_scope
        audit = audit_scope(self.store,spec['boards'],day,source='tickflow',timeframe='daily',verify_files=True)
        assert audit['complete']
        seen.append(audit['ready'])
        return dict(success=2,signals=0,coverage=audit)
    monkeypatch.setattr(Service,'_scan',scan)
    # Virtual waiting only; actual parent/child orchestration and cancellation.
    monkeypatch.setattr('workbench.tickflow_recovery.wait', lambda token,seconds: clock.advance(seconds) or token.is_set())
    service = Service(store)
    try:
        job = service.submit('premarket',dict(sources=['tickflow'],boards=['沪深主板'],start=start,end=day,
                              strategies=['STRATEGY_GAP_H2'],timeframe='daily'))
        row = wait_for(store,job)
        assert row['status'] == 'completed', row['result']
        parent = json.loads(row['result'])
        child = parent['sources']['tickflow']['sync_job']
        result = json.loads(store.rows('SELECT result FROM jobs WHERE id=?',(child,))[0]['result'])
        assert calls == [[codes[0]],[codes[0]]] and result['scan_readiness']['ready'] == 2
        assert result['transport_recovery']['recovered'] and not result['errors']
        assert seen == [2]
    finally:
        service.pool.shutdown()
