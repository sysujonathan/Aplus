import json

import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_incremental_sync import Provider
from tests.test_tickflow_scan_readiness import scope
from workbench.sync import sync_stock
from workbench.sources import SOURCES, calendar_file
from workbench.sync_progress import sync_progress


@pytest.mark.parametrize('source', list(SOURCES))
def test_actual_ranges_cache_and_price_refresh(store, frame, source):
    scope(store, frame, ['sh.600000'])
    if source != 'baostock':
        store.write_artifact(str(calendar_file(store, source).relative_to(store.root)),
                             calendar_file(store).read_bytes())
    provider = Provider(frame)
    notices = []
    def run(start, end, **kwargs):
        return sync_stock(store, lambda: provider, 'sh.600000', start, end, source=source,
                          on_request=lambda *args: notices.append(args), **kwargs)
    first, last = frame.date.iloc[0], frame.date.iloc[-1]
    mid = frame.date.iloc[-2]
    run(first, mid)
    assert notices == [('history', first, mid)]
    notices.clear()
    run(first, last)
    assert notices == [('tail', mid, last)]  # actual overlap, not the two-year target
    notices.clear()
    run(first, last)
    assert notices == [('cached', None, None)]
    notices.clear()
    run(first, last, force=True)
    assert notices == [('repair', first, last)]
    notices.clear()
    provider.frame = frame.copy()
    provider.frame.loc[:, ['open', 'high', 'low', 'close']] *= .8
    end = frame.date.iloc[-2]
    run(first, end, force=True)
    # Forced refresh retains the existing longer snapshot range.
    assert notices == [('repair', first, last)]


@pytest.mark.parametrize('source', list(SOURCES))
def test_early_extension_and_changed_overlap_have_distinct_messages(store, frame, source):
    scope(store, frame, ['sh.600000'])
    if source != 'baostock':
        store.write_artifact(str(calendar_file(store, source).relative_to(store.root)),
                             calendar_file(store).read_bytes())
    provider = Provider(frame)
    left, mid, right = frame.date.iloc[0], frame.date.iloc[30], frame.date.iloc[-2]
    sync_stock(store, lambda: provider, 'sh.600000', mid, right, source=source)
    notices = []
    sync_stock(store, lambda: provider, 'sh.600000', left, right, source=source,
               on_request=lambda *args: notices.append(args))
    assert notices == [('prefix', left, mid)]
    provider.frame.loc[:, ['open', 'high', 'low', 'close']] *= .8
    notices.clear()
    last = frame.date.iloc[-1]
    sync_stock(store, lambda: provider, 'sh.600000', left, last, source=source,
               on_request=lambda *args: notices.append(args))
    assert notices == [('tail', right, last), ('refresh', left, last)]


@pytest.mark.parametrize('source', list(SOURCES))
def test_short_vocabulary_does_not_invent_default_range(source):
    message = sync_progress(source, 'tail', 'sh.600000', '2026-10-08', '2026-10-09')
    assert message == f'{SOURCES[source]} · 更新尾部 · sh.600000 · 2026-10-08～2026-10-09（含衔接核对日）'
    assert '2024' not in message
    assert sync_progress(source, 'cached', 'sh.600000') == f'{SOURCES[source]} · 核验缓存 · sh.600000'
    assert '获取历史' in sync_progress(source, 'history', '100只', '2024-10-09', '2026-10-09')


def test_tencent_service_shows_tail_instead_of_job_target(store, frame, monkeypatch):
    from workbench.service import Service
    scope(store, frame, ['sh.600000'])
    store.write_artifact(str(calendar_file(store, 'tencent').relative_to(store.root)),
                         calendar_file(store).read_bytes())
    provider = Provider(frame)
    first, mid, last = frame.date.iloc[0], frame.date.iloc[-2], frame.date.iloc[-1]
    sync_stock(store, lambda: provider, 'sh.600000', first, mid, source='tencent')
    class Connection(Provider):
        def __init__(self): super().__init__(frame)
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr('workbench.tencent_service.Tencent', Connection)
    service = Service(store)
    messages = []
    original = service.progress
    def progress(job, done, total, message):
        messages.append(message)
        original(job, done, total, message)
    monkeypatch.setattr(service, 'progress', progress)
    try:
        row = wait_for(store, service.submit('sync', dict(source='tencent', codes=['sh.600000'], start=first, end=last)))
        assert row['status'] == 'completed', row['message']
        assert sync_progress('tencent', 'tail', 'sh.600000', mid, last) in messages
        assert not any('获取历史' in m or f'{first}～{last}' in m for m in messages)
        assert json.loads(row['result'])['timings']['stock_processing_seconds'] >= 0
    finally:
        service.pool.shutdown()


def test_tencent_page_timings_count_success_and_failure(monkeypatch):
    from workbench.tencent import Tencent
    from tests.test_tencent_history import response
    from tests.test_tickflow_scan_readiness import bars
    from workbench.provider_guard import ProviderError
    data = bars(1000)
    provider = Tencent()
    monkeypatch.setattr(provider, '_query', lambda op, args: response(args[0], data[data.date <= args[1]].iloc[-640:]))
    provider.fetch('sh.600000', data.date.iloc[0], data.date.iloc[-1])
    assert provider.history_timings['history_page_requests'] == 2
    def failed(*args): raise ProviderError('tencent', 'history_page', 'test timeout')
    monkeypatch.setattr(provider, '_query', failed)
    with pytest.raises(ProviderError): provider.fetch('sh.600000', data.date.iloc[0], data.date.iloc[-1])
    assert provider.history_timings['history_page_requests'] == 3
    assert provider.history_timings['history_request_seconds'] >= 0
