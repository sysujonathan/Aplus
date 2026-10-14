import json
from unittest.mock import Mock

import pandas as pd
import pytest

from workbench.baostock_daily_batch import (
    apply_one_day_tail, one_day_tail_candidates, validate_daily_archive,
)
from workbench.market import load_dataset, save_dataset
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.service import Service
from workbench.sources import coverage_state, save_coverage
from workbench.store import Store
from tests.test_workbench import wait_for


DAY = '2026-10-12'
PREVIOUS = '2026-10-09'
CODES = ['sh.600000', 'sz.000001', 'sh.600004']


def old_bars(close=10.0):
    return pd.DataFrame({
        'date': ['2026-10-08', PREVIOUS],
        'open': [9.8, 9.9], 'high': [10.1, 10.2], 'low': [9.7, 9.8],
        'close': [9.9, close], 'volume': [1000.0, 1100.0],
    })


def archive():
    return pd.DataFrame([{
        'date': DAY, 'code': code, 'open': '10.1', 'high': '10.4',
        'low': '10.0', 'close': '10.3', 'preclose': '10.0',
        'volume': '1200', 'adjustflag': '3', 'tradestatus': '1',
    } for code in CODES])


def seeded(tmp_path):
    store = Store(tmp_path)
    store.write_artifact('trading_calendar.json', json.dumps({
        'start': '2026-10-08', 'end': DAY,
        'trading_days': ['2026-10-08', PREVIOUS, DAY],
    }).encode())
    for code in CODES:
        did = save_dataset(store, code, old_bars(), 'baostock', '前复权')
        save_coverage(store, code, did, '2026-10-08', PREVIOUS)
    return store


def test_one_day_archive_appends_only_safe_tails_and_preserves_fallbacks(tmp_path):
    store = seeded(tmp_path)
    factors = pd.DataFrame([{'code': CODES[1], 'dividOperateDate': DAY}])
    data = archive()
    data.loc[data.code == CODES[2], 'preclose'] = '9.5'
    before = len(store.rows('SELECT id FROM datasets'))
    assert one_day_tail_candidates(store, CODES, '2026-10-08', DAY) == CODES
    saved, fallback = apply_one_day_tail(store, CODES, data, factors,
                                         '2026-10-08', DAY)
    assert set(saved) == {CODES[0]}
    assert fallback == {
        CODES[1]: '当日存在复权事件',
        CODES[2]: '前收盘与既有前复权尾部不一致',
    }
    frame, record = load_dataset(store, saved[CODES[0]])
    assert frame.date.tolist() == ['2026-10-08', PREVIOUS, DAY]
    assert frame.iloc[-1].close == pytest.approx(10.3)
    assert record['adjustment'] == '前复权'
    assert coverage_state(store, CODES[0])[0]['end'] == DAY
    assert coverage_state(store, CODES[1])[0]['end'] == PREVIOUS
    assert len(store.rows('SELECT id FROM datasets')) == before + 1


def test_bad_or_cancelled_daily_archive_never_advances_coverage(tmp_path):
    store = seeded(tmp_path)
    invalid = archive().drop(columns=['preclose'])
    with pytest.raises(ValueError, match='缺少字段'):
        validate_daily_archive(invalid, DAY)
    before = dict(coverage_state(store, CODES[0])[0])
    with pytest.raises(InterruptedError):
        apply_one_day_tail(store, [CODES[0]], archive(), pd.DataFrame(),
                           '2026-10-08', DAY,
                           check_stop=lambda: (_ for _ in ()).throw(InterruptedError('stop')))
    assert coverage_state(store, CODES[0])[0] == before


def test_official_archive_extra_securities_do_not_disable_supported_stock_tails(tmp_path):
    store = seeded(tmp_path)
    extra = pd.concat([archive().iloc[[0]].assign(code=code)
                       for code in ['sh.689009', 'sz.302132', 'sh.510300']], ignore_index=True)
    data = pd.concat([archive(), extra], ignore_index=True)
    saved, fallback = apply_one_day_tail(store, CODES, data, pd.DataFrame(),
                                         '2026-10-08', DAY)
    assert set(saved) == set(CODES) and not fallback
    assert all(coverage_state(store, code)[0]['end'] == DAY for code in CODES)
    assert not store.rows("SELECT id FROM datasets WHERE code IN ('sh.689009','sz.302132','sh.510300')")


@pytest.mark.parametrize('fault', ['identity', 'duplicate', 'date'])
def test_extra_security_still_cannot_hide_invalid_archive_identity(fault):
    extra = archive().iloc[[0]].assign(code='sh.689009')
    if fault == 'identity':
        extra['code'] = 'not-a-security'
    elif fault == 'date':
        extra['date'] = PREVIOUS
    else:
        extra = pd.concat([extra, extra], ignore_index=True)
    with pytest.raises(ValueError):
        validate_daily_archive(pd.concat([archive(), extra], ignore_index=True), DAY)


def test_service_bulk_scope_saves_market_without_per_stock_downloads(tmp_path, monkeypatch):
    codes = [f'sh.{600000 + index:06d}' for index in range(50)]
    store = seeded(tmp_path)
    for code in codes:
        did = save_dataset(store, code, old_bars(), 'baostock', '前复权')
        save_coverage(store, code, did, '2026-10-08', PREVIOUS)
    directory = pd.DataFrame(dict(code=codes, code_name=['测试'] * 50, tradeStatus=['1'] * 50))
    basics = pd.DataFrame(dict(code=codes, ipoDate=['2000-01-01'] * 50,
                              outDate=[''] * 50, type=['1'] * 50, status=['1'] * 50))
    data = pd.concat([archive().iloc[[0]].assign(code=code)
                      for code in codes + ['sh.689009', 'sz.302132']], ignore_index=True)
    provider = Mock()
    provider.__enter__ = Mock(return_value=provider)
    provider.__exit__ = Mock(return_value=False)
    provider.universe.return_value = directory
    provider.basics.return_value = basics
    provider.daily_astock.return_value = data
    provider.daily_adjust_factor.return_value = pd.DataFrame()
    remaining = []
    def per_stock(_store, _factory, requested, *_args, **_kwargs):
        remaining.extend(requested)
        yield from ()
    monkeypatch.setattr('workbench.service.BaoStock', lambda: provider)
    monkeypatch.setattr('workbench.service.sync_results', per_stock)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(
            boards=['沪深主板'], start='2026-10-08', end=DAY)))
        result = json.loads(row['result'])
        assert result['daily_batch'] == dict(eligible=50, saved=50, fallback=0)
        assert not remaining
        assert result['timings']['history_requests'] == 0
        provider.daily_astock.assert_called_once_with(DAY)
    finally:
        service.pool.shutdown()


def test_process_adapter_exposes_bounded_bulk_operations():
    from workbench.provider_process import BaoStock
    from unittest.mock import patch
    provider = BaoStock()
    with patch.object(provider, '_query') as query:
        provider.daily_astock(DAY)
        assert query.call_args.args == ('daily_astock', [DAY], provider.basics_timeout)
        provider.daily_adjust_factor(DAY)
        assert query.call_args.args == ('daily_adjust_factor', [DAY], provider.basics_timeout)


@pytest.mark.parametrize(('failure_at', 'code'), [
    ('daily_astock', '10001011'),
    ('daily_adjust_factor', '429'),
])
def test_bulk_provider_restriction_stops_before_any_per_stock_request(
        tmp_path, monkeypatch, failure_at, code):
    codes = [f'sh.{600000 + index:06d}' for index in range(50)]
    directory = pd.DataFrame({
        'code': codes,
        'code_name': [f'测试{index}' for index in range(50)],
        'tradeStatus': ['1'] * 50,
    })
    basics = pd.DataFrame({
        'code': codes, 'ipoDate': ['2000-01-01'] * 50,
        'outDate': [''] * 50, 'type': ['1'] * 50, 'status': ['1'] * 50,
    })
    store = Store(tmp_path)
    store.write_artifact('trading_calendar.json', json.dumps({
        'start': '2026-10-08', 'end': DAY,
        'trading_days': ['2026-10-08', PREVIOUS, DAY],
    }).encode())
    calls = []

    class RestrictedBulkProvider:
        def __enter__(self):
            calls.append('connect')
            return self

        def __exit__(self, *_args):
            calls.append('disconnect')

        def universe(self, _day):
            return directory.copy()

        def basics(self):
            return basics.copy()

        def daily_astock(self, _day):
            calls.append('daily_astock')
            if failure_at == 'daily_astock':
                raise ProviderError('baostock', 'daily_astock', '供应商限制访问', code)
            return pd.DataFrame()

        def daily_adjust_factor(self, _day):
            calls.append('daily_adjust_factor')
            raise ProviderError('baostock', 'daily_adjust_factor', 'Too Many Requests', code)

    per_stock = Mock(side_effect=AssertionError('限制访问后不应进入逐股请求'))
    monkeypatch.setattr('workbench.service.BaoStock', RestrictedBulkProvider)
    monkeypatch.setattr('workbench.service.sync_results', per_stock)
    monkeypatch.setattr('workbench.baostock_daily_batch.one_day_tail_candidates',
                        lambda *_args, **_kwargs: codes)
    service = Service(store)
    try:
        job = service.submit('sync', {
            'boards': ['沪深主板'], 'start': '2026-10-08', 'end': DAY,
        })
        row = wait_for(store, job)
        result = json.loads(row['result'])
        assert row['status'] == 'failed'
        assert result['errors'][0]['error_code'] == code
        assert not per_stock.called
        assert not any(call == 'fetch' for call in calls)
        assert calls.count('connect') == 1
        state = ProviderGuard(store, 'baostock').state()
        assert state['last_error']['error_code'] == code
        assert len(state['failures']) == 1  # outer job handler must not count it twice
    finally:
        service.pool.shutdown()
