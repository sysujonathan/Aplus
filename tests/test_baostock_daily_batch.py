import json

import pandas as pd
import pytest

from workbench.baostock_daily_batch import (
    apply_one_day_tail, one_day_tail_candidates, validate_daily_archive,
)
from workbench.market import load_dataset, save_dataset
from workbench.sources import coverage_state, save_coverage
from workbench.store import Store


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


def test_process_adapter_exposes_bounded_bulk_operations():
    from workbench.provider_process import BaoStock
    from unittest.mock import patch
    provider = BaoStock()
    with patch.object(provider, '_query') as query:
        provider.daily_astock(DAY)
        assert query.call_args.args == ('daily_astock', [DAY], provider.basics_timeout)
        provider.daily_adjust_factor(DAY)
        assert query.call_args.args == ('daily_adjust_factor', [DAY], provider.basics_timeout)
