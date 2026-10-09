import json

import pandas as pd
import pytest

from tests.test_workbench import store, frame, wait_for
from workbench.market import save_dataset, load_dataset
from workbench.readiness import save_calendar, save_directory, audit_scope, filter_tickflow_directory
from workbench.history_quality import describe_history, save_quality, require_research_history
from workbench.sync import sync_stock
from workbench.sources import coverage_state
from workbench.service import Service
from workbench.strategies import prepare, prepare_indicators


def bars(count=400):
    dates = pd.bdate_range('2018-01-01', periods=count).strftime('%Y-%m-%d')
    return pd.DataFrame(dict(date=dates, open=10., high=11., low=9., close=10., volume=1000.))


def scope(store, data, codes):
    first, last = data.date.iloc[0], data.date.iloc[-1]
    dates = pd.date_range(first, last)
    save_calendar(store, pd.DataFrame(dict(calendar_date=dates.strftime('%Y-%m-%d'),
        is_trading_day=[str(int(d.weekday() < 5)) for d in dates])), first, last)
    save_directory(store, pd.DataFrame(dict(code=codes, tradeStatus=['unknown']*len(codes))), last)
    return first, last


def snapshot(store, code, data, first, last):
    did = save_dataset(store, code, data, 'tickflow', '前复权')
    save_quality(store, did, describe_history(store, code, data, first, last))
    return did


def test_old_hole_is_saved_without_certifying_full_history_or_changing_scan_input(store):
    data = bars(); first, last = scope(store, data, ['sh.600000'])
    returned = data.drop(index=10)
    class Provider:
        calls = 0
        def fetch(self, *args):
            self.calls += 1
            return returned.copy()
    provider = Provider()
    did, action = sync_stock(store, lambda:provider, 'sh.600000', first, last, source='tickflow')
    saved, record = load_dataset(store, did)
    assert len(saved) == 399 and data.date.iloc[10] not in set(saved.date)
    q = json.loads(store.rows('SELECT value FROM meta WHERE key=?', ('market_quality:'+did,))[0]['value'])
    assert q['missing_dates'] == [data.date.iloc[10]] and not q['complete']
    audit = audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')
    assert audit['complete'] and audit['scan_allowed'] and len(audit['warnings']) == 1
    pd.testing.assert_frame_equal(prepare_indicators(prepare(saved)), prepare_indicators(prepare(data)))
    with pytest.raises(ValueError, match='历史研究输入'):
        require_research_history(store, record, last)
    assert sync_stock(store, lambda:provider, 'sh.600000', first, last, source='tickflow')[1] == 'skipped'
    assert provider.calls == 1 and coverage_state(store, 'sh.600000') == []


def test_recent_gap_excludes_only_that_stock_and_returns_partial_scan(store, monkeypatch):
    data = bars(); first, last = scope(store, data, ['sh.600000','sh.600001','sh.600002'])
    good = snapshot(store, 'sh.600000', data.drop(index=10), first, last)
    bad = snapshot(store, 'sh.600001', data.drop(index=200), first, last)
    called = []
    def calculate(strategy, frame, **kwargs):
        called.append(frame.attrs['code'])
        assert len(kwargs['prepared']) == 300
        return None
    monkeypatch.setattr('workbench.service.signal_at_end', calculate)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', dict(source='tickflow', boards=['沪深主板'],
            datasets=[good,bad], strategies=['MTR_MASTER'], timeframes=['daily'], asof=last)))
        report = json.loads(row['result'])
        assert row['status'] == 'partial' and report['success'] == 1 and report['no_signal'] == 1
        assert called == ['sh.600000'] and report['coverage']['ready'] == 1
        assert report['coverage']['expected'] == 3 and len(report['errors']) == 2
        assert '300' in report['errors'][0]['error'] and report['coverage']['scan_allowed']
        assert not store.rows('SELECT * FROM observations')
    finally:
        service.pool.shutdown()


def test_all_recent_gaps_or_stale_directory_still_block_scan(store):
    data = bars(); first, last = scope(store, data, ['sh.600000'])
    snapshot(store, 'sh.600000', data.drop(index=200), first, last)
    assert not audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')['scan_allowed']
    store.execute("UPDATE meta SET value='2023-01-01' WHERE key='universe_date'")
    audit = audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')
    assert not audit['scope_valid'] and not audit['scan_allowed']


def test_unknown_tail_is_not_promoted_to_fresh_success(store):
    data = bars(); first, last = scope(store, data, ['sh.600000'])
    class Provider:
        def fetch(self, *args): return data.iloc[:-1].copy()
    did, _ = sync_stock(store, lambda:Provider(), 'sh.600000', first, last, source='tickflow')
    assert coverage_state(store, 'sh.600000', 'tickflow')[0]['end'] == data.date.iloc[-2]
    audit = audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')
    assert audit['ready'] == 0 and not audit['scan_allowed']


def test_gap_dates_after_historical_asof_do_not_block_earlier_scan(store):
    data = bars(700); first, last = scope(store, data, ['sh.600000'])
    snapshot(store, 'sh.600000', data.drop(index=600), first, last)
    earlier = data.date.iloc[500]
    store.execute("UPDATE meta SET value=? WHERE key='universe_date'", (earlier,))
    assert audit_scope(store, ['沪深主板'], earlier, source='tickflow', timeframe='daily')['complete']


def test_daily_ready_does_not_certify_weekly_input(store):
    data = bars(1700); first, last = scope(store, data, ['sh.600000'])
    snapshot(store, 'sh.600000', data.drop(index=500), first, last)
    assert audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')['complete']
    weekly = audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='weekly')
    assert not weekly['scan_allowed'] and 'weekly' in weekly['gaps'][0]['error']


def test_weekly_scan_without_daily_is_accepted_and_minimum_uses_weeks(store, monkeypatch):
    data = bars(700); first, last = scope(store, data, ['sh.600000'])
    did = snapshot(store, 'sh.600000', data, first, last)
    called = []
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *args, **kwargs: called.append(len(args[1])) or None)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', dict(source='tickflow', boards=['沪深主板'],
            datasets=[did], strategies=['STRATEGY_STRUCTURAL_GAP'], timeframes=['weekly'], asof=last)))
        assert row['status'] == 'completed' and called and 125 <= called[0] < 300, row['message']
    finally:
        service.pool.shutdown()


def test_delisted_is_excluded_only_with_dated_archive_unknown_is_retained(store):
    directory = pd.DataFrame(dict(code=['sh.600000','sh.600001','sz.002525'], tradeStatus=['unknown']*3))
    save_directory(store, directory, '2024-01-01')
    basics = pd.DataFrame(dict(code=['sh.600000','sh.600001'], type=['1','1'],
        outDate=['','2024-01-02']))
    store.write_artifact('directories/2024-01-01-basics.csv', basics.to_csv(index=False).encode())
    eligible, retired, excluded = filter_tickflow_directory(store, directory, '2024-01-03')
    assert excluded == ['sh.600001'] and eligible.code.tolist() == ['sh.600000','sz.002525']
    save_directory(store, eligible, '2024-01-03', retired_codes=retired)
    assert (store.root/'directories/2024-01-01.csv').exists()


def test_invalid_bars_and_nontrading_dates_are_not_accepted_as_quality(store):
    data = bars(); first, last = scope(store, data, ['sh.600000'])
    with pytest.raises(ValueError, match='非交易日'):
        describe_history(store, 'sh.600000', pd.concat([data, data.iloc[:1].assign(date='2023-01-07')]), first, last)


def test_incremental_refresh_keeps_unknown_history_without_redownloading_it(store):
    data = bars(401); first, last = scope(store, data, ['sh.600000'])
    calls = []
    class Provider:
        def fetch(self, code, start, end):
            calls.append((start,end))
            return data[data.date.between(start,end)].drop(index=10, errors='ignore').copy()
    provider = Provider()
    old, _ = sync_stock(store, lambda:provider, 'sh.600000', first, data.date.iloc[-2], source='tickflow')
    new, action = sync_stock(store, lambda:provider, 'sh.600000', first, last, source='tickflow')
    assert action == 'updated' and len(calls) == 2 and calls[1][0] == data.date.iloc[-2]
    assert len(load_dataset(store, new)[0]) == 400 and old != new
    assert audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')['complete']


def test_weekly_gate_ignores_unfinished_week_but_daily_does_not(store):
    data = bars(704)  # Thursday; only the preceding Friday's weekly bar is complete.
    first, last = scope(store, data, ['sh.600000'])
    assert pd.Timestamp(last).weekday() == 3
    snapshot(store, 'sh.600000', data.drop(index=702), first, last)
    assert not audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='daily')['scan_allowed']
    assert audit_scope(store, ['沪深主板'], last, source='tickflow', timeframe='weekly')['complete']


def test_partial_daily_does_not_prevent_a_valid_weekly_scan(store, monkeypatch):
    data = bars(1700); first, last = scope(store, data, ['sh.600000','sh.600001'])
    good = snapshot(store, 'sh.600000', data, first, last)
    bad = snapshot(store, 'sh.600001', data.drop(index=1600), first, last)
    calls = []
    def calculate(strategy, data, **kwargs):
        calls.append((data.attrs['code'], len(data)))
        return None
    monkeypatch.setattr('workbench.service.signal_at_end', calculate)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', dict(source='tickflow', boards=['沪深主板'],
            datasets=[good,bad], strategies=['STRATEGY_STRUCTURAL_GAP'], timeframes=['daily','weekly'], asof=last)))
        result = json.loads(row['result'])
        assert row['status'] == 'partial' and result['success'] == 2
        assert len(calls) == 2 and calls[0][1] > calls[1][1]
        assert len(result['errors']) == 2
    finally:
        service.pool.shutdown()


def test_tampered_tickflow_file_still_fails_before_strategy_call(store, monkeypatch):
    data = bars(); first, last = scope(store, data, ['sh.600000'])
    did = snapshot(store, 'sh.600000', data, first, last)
    r = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
    store.write_artifact(r['path'], b'changed bytes')
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *args, **kw: pytest.fail('tampered file reached strategy'))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', dict(source='tickflow', boards=['沪深主板'], datasets=[did],
            strategies=['MTR_MASTER'], timeframes=['daily'], asof=last)))
        result = json.loads(row['result'])
        assert row['status'] == 'partial' and result['success'] == 0 and '校验' in result['errors'][0]['error']
    finally:
        service.pool.shutdown()
