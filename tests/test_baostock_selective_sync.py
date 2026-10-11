import json
from datetime import date, timedelta
from threading import Event

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_incremental_sync import Provider
from tests.test_tickflow_scan_readiness import bars, scope
from workbench.history_quality import describe_history, save_quality, quality_for
from workbench.market import load_dataset, save_dataset
from workbench.provider_guard import ProviderError
from workbench.readiness import save_calendar
from workbench.service import Service
from workbench.sources import coverage_state, save_coverage
from workbench.sync import sync_stock
from workbench.sync_batch import sync_results
from workbench.trading_status import save_status_rows

CODE = 'sh.600000'


def seed(store, data, missing=(), start=None, end=None):
    first, last = scope(store, data, [CODE])
    old = data.drop(index=list(missing))
    if start or end:
        old = old[old.date.between(start or first, end or last)]
    did = save_dataset(store, CODE, old, 'baostock', '前复权')
    save_coverage(store, CODE, did, start or first, end or last)
    save_quality(store, did, describe_history(store, CODE, old, start or first, end or last,
                                            source='baostock'))
    return did, first, last


def test_daily_repair_fetches_only_current_hole_and_preserves_older_gap(store):
    data = bars(650)
    old, first, last = seed(store, data, [10, 600])
    provider = Provider(data)
    did, action = sync_stock(store, lambda:provider, CODE, data.date.iloc[150], last,
                             repair_timeframe='daily', batch_writes=True)
    assert action == 'updated'
    assert provider.calls == [(data.date.iloc[599], data.date.iloc[601])]
    saved, record = load_dataset(store, did)
    assert len(saved) == 649 and data.date.iloc[10] not in set(saved.date)
    assert data.date.iloc[600] in set(saved.date)
    assert quality_for(store, record)['missing_dates'] == [data.date.iloc[10]]
    assert len(load_dataset(store, old)[0]) == 648


def test_weekly_repair_recovers_a_whole_missing_week_without_older_research(store):
    data = bars(1900)
    old, first, last = seed(store, data, [10, *range(600, 605)])
    provider = Provider(data)
    did, _ = sync_stock(store, lambda:provider, CODE, first, last, repair_timeframe='weekly')
    assert provider.calls == [(data.date.iloc[599], data.date.iloc[605])]
    saved, record = load_dataset(store, did)
    assert len(saved) == 1899 and quality_for(store, record)['missing_dates'] == [data.date.iloc[10]]
    assert len(load_dataset(store, old)[0]) == 1894


def test_fragmented_gaps_do_not_multiply_provider_requests(store):
    data = bars(650)
    seed(store, data, [400, 420, 440, 460, 480])
    provider = Provider(data)
    did, _ = sync_stock(store, lambda:provider, CODE, data.date.iloc[0], data.date.iloc[-1],
                       repair_timeframe='daily')
    assert provider.calls == [(data.date.iloc[399], data.date.iloc[481])]
    assert len(load_dataset(store, did)[0]) == len(data)


def test_proven_halt_is_rechecked_offline_without_fabricating_a_bar(store):
    data = bars()
    old, first, last = seed(store, data, [350])
    day = data.date.iloc[350]
    save_status_rows(store, CODE, day, day, [dict(date=day, code=CODE, tradestatus='0', volume='0')])
    def offline(): raise AssertionError('Known halt must not connect')
    did, action = sync_stock(store, offline, CODE, first, last, repair_timeframe='daily')
    assert (did, action) == (old, 'skipped')
    saved, record = load_dataset(store, did)
    assert day not in set(saved.date) and quality_for(store, record)['missing_dates'] == []


def test_changed_gap_anchor_refreshes_same_source_whole_history(store):
    data = bars(650)
    old, first, last = seed(store, data, [600])
    revised = data.copy()
    revised[['open', 'high', 'low', 'close']] *= .8
    provider = Provider(revised)
    did, action = sync_stock(store, lambda:provider, CODE, data.date.iloc[150], last,
                             repair_timeframe='daily')
    assert action == 'refreshed'
    assert provider.calls == [(data.date.iloc[599], data.date.iloc[601]), (first, last)]
    pd.testing.assert_frame_equal(load_dataset(store, did)[0], revised, check_exact=False)
    assert load_dataset(store, old)[0].close.iloc[0] == 10


def test_local_repair_missing_front_anchor_is_rejected_even_with_back_anchor(store):
    data = bars(650)
    old, first, last = seed(store, data, [600])
    provider = Provider(data.drop(index=599))
    with pytest.raises(ValueError, match='局部补拉返回不完整'):
        sync_stock(store, lambda:provider, CODE, first, last, repair_timeframe='daily')
    assert provider.calls == [(data.date.iloc[599], data.date.iloc[601])]
    assert coverage_state(store, CODE)[0]['dataset_id'] == old


@pytest.mark.parametrize('failure', [RuntimeError('test network error'),
    ProviderError('baostock', 'history', '黑名单用户', '10001011')])
def test_prefix_failure_preserves_saved_latest_tail_and_error_type(store, failure):
    data = bars()
    old, first, last = seed(store, data, start=data.date.iloc[50], end=data.date.iloc[-2])
    class Failing(Provider):
        def fetch(self, code, a, b):
            if a == first:
                self.calls.append((a, b))
                raise failure
            return super().fetch(code, a, b)
    provider = Failing(data)
    with pytest.raises(type(failure)) as caught:
        sync_stock(store, lambda:provider, CODE, first, last, batch_writes=True)
    state = coverage_state(store, CODE)[0]
    assert state['end'] == last and state['start'] == data.date.iloc[50]
    assert state['dataset_id'] == caught.value.latest_dataset_id != old
    assert len(load_dataset(store, state['dataset_id'])[0]) == 350
    assert len(load_dataset(store, old)[0]) == 349
    assert provider.calls == [(data.date.iloc[-2], last), (first, data.date.iloc[50])]


def test_unpublished_tail_does_not_retry_or_start_prefix(store):
    data = bars()
    old, first, last = seed(store, data, start=data.date.iloc[50], end=data.date.iloc[-2])
    provider = Provider(data.iloc[:-1])
    with pytest.raises(ValueError):
        sync_stock(store, lambda:provider, CODE, first, last)
    assert provider.calls == [(data.date.iloc[-2], last)]
    assert coverage_state(store, CODE)[0]['dataset_id'] == old


def test_blacklist_stops_market_after_prefix_and_reports_preserved_tail(store, monkeypatch):
    data = bars()
    old, first, last = seed(store, data, start=data.date.iloc[50], end=data.date.iloc[-2])
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def fetch(self, code, a, b):
            calls.append((code, a, b))
            if a == first:
                raise ProviderError('baostock', 'history', '黑名单用户', '10001011')
            return data[data.date.between(a, b)].copy()
    monkeypatch.setattr('workbench.service.BaoStock', Connection)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(source='baostock', codes=[CODE, 'sh.600001'],
                                                         start=first, end=last)))
        report = json.loads(row['result'])
        assert row['status'] == 'partial' and report['remaining'] == 1 and report['success'] == 0
        assert report['errors'][0]['error_code'] == '10001011'
        assert report['errors'][0]['latest_saved']
        assert report['partial_saved'] == 1 and '最新尾部已保存' in report['errors'][0]['error']
        assert report['datasets'] == [coverage_state(store, CODE)[0]['dataset_id']]
        assert len(calls) == 2 and all(c[0] == CODE for c in calls)
        assert report['timings']['history_requests'] == 2
        assert report['timings']['history_request_seconds'] >= 0
    finally:
        service.pool.shutdown()


def test_baostock_batch_write_rolls_back_coverage_without_network_transaction(store, monkeypatch):
    data = bars()
    old, first, last = seed(store, data, end=data.date.iloc[-2])
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def fetch(self, code, a, b):
            assert getattr(store._atomic_local, 'connection', None) is None
            return data[data.date.between(a, b)].copy()
    original = store.event
    def fail(job, action, *args, **kwargs):
        if action == '保存同步进度': raise ValueError('test atomic failure')
        return original(job, action, *args, **kwargs)
    monkeypatch.setattr(store, 'event', fail)
    results = list(sync_results(store, Connection, [CODE], first, last, None, False,
                               Event(), Event(), 1, lambda *a:None))
    assert isinstance(results[0][-1], ValueError)
    assert coverage_state(store, CODE)[0]['dataset_id'] == old
    assert len(store.rows('SELECT id FROM datasets')) == 1


def test_explicit_force_keeps_whole_history_refresh_contract(store):
    data = bars(650)
    seed(store, data, [10, 600])
    provider = Provider(data)
    _, action = sync_stock(store, lambda:provider, CODE, data.date.iloc[150], data.date.iloc[-1],
                           force=True, repair_timeframe='daily')
    assert action == 'refreshed' and provider.calls == [(data.date.iloc[0], data.date.iloc[-1])]


def test_tail_moves_repair_window_forward_before_planning_gaps(store):
    data = bars(1000)
    old, first, last = seed(store, data, [400], end=data.date.iloc[649])
    provider = Provider(data)
    did, action = sync_stock(store, lambda:provider, CODE, first, last, repair_timeframe='daily')
    assert action == 'updated' and provider.calls == [(data.date.iloc[649], last)]
    saved, record = load_dataset(store, did)
    assert len(saved) == 999 and quality_for(store, record)['missing_dates'] == [data.date.iloc[400]]


def test_gap_failure_keeps_new_tail_and_stays_unready(store):
    data = bars(650)
    old, first, last = seed(store, data, [600], end=data.date.iloc[-2])
    class Failing(Provider):
        def fetch(self, code, a, b):
            if a == data.date.iloc[599]:
                self.calls.append((a, b))
                raise RuntimeError('test gap failure')
            return super().fetch(code, a, b)
    provider = Failing(data)
    with pytest.raises(RuntimeError) as caught:
        sync_stock(store, lambda:provider, CODE, first, last, repair_timeframe='daily', batch_writes=True)
    state = coverage_state(store, CODE)[0]
    assert state['dataset_id'] == caught.value.latest_dataset_id != old and state['end'] == last
    saved, record = load_dataset(store, state['dataset_id'])
    assert quality_for(store, record)['missing_dates'] == [data.date.iloc[600]]
    assert provider.calls == [(data.date.iloc[-2], last), (data.date.iloc[599], data.date.iloc[601])]


def test_weekend_end_does_not_repeat_tail_or_block_earlier_extension(store):
    data = bars()
    old, first, last = seed(store, data, start=data.date.iloc[50], end=data.date.iloc[-2])
    sunday = (date.fromisoformat(last)+timedelta(days=2)).isoformat()
    dates = pd.date_range(first, sunday)
    save_calendar(store, pd.DataFrame(dict(calendar_date=dates.strftime('%Y-%m-%d'),
        is_trading_day=[str(int(d.weekday() < 5)) for d in dates])), first, sunday)
    provider = Provider(data)
    did, action = sync_stock(store, lambda:provider, CODE, first, sunday)
    assert action == 'updated' and len(load_dataset(store, did)[0]) == len(data)
    assert provider.calls == [(data.date.iloc[-2], last), (first, data.date.iloc[50])]
    assert coverage_state(store, CODE)[0]['end'] == last


def test_user_stop_after_tail_preserves_cancelled_receipt(store, monkeypatch):
    data = bars()
    old, first, last = seed(store, data, start=data.date.iloc[50], end=data.date.iloc[-2])
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def fetch(self, code, a, b):
            calls.append((code, a, b))
            if a == first:
                self.cancel_event.events[0].set()
                raise InterruptedError('任务已停止')
            return data[data.date.between(a, b)].copy()
    monkeypatch.setattr('workbench.service.BaoStock', Connection)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(source='baostock', codes=[CODE, 'sh.600001'],
                                                         start=first, end=last)))
        report = json.loads(row['result'])
        assert row['status'] == 'cancelled' and '最新尾部已保存' in row['message']
        assert report['partial_saved'] == 1 and not report['errors'] and report['success'] == 0
        assert report['datasets'] == [coverage_state(store, CODE)[0]['dataset_id']]
        assert len(calls) == 2 and all(c[0] == CODE for c in calls)
    finally:
        service.pool.shutdown()
