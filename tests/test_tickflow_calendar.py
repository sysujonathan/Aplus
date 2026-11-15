"""Calendar preparation must reach same-source weekly sync without weakening gates."""
import json

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from workbench.market import load_dataset, save_dataset
from workbench.readiness import audit_scope, extend_exchange_calendar
from workbench.service import Service
from workbench.sources import coverage_state, save_coverage, source_file
from workbench.store import dumps


def write_calendar(store, name, start, end, days):
    payload = dict(start=start, end=end, trading_days=days)
    store.write_artifact(name, dumps(payload).encode())
    return payload


def test_short_calendar_extends_backward_and_forward_then_syncs_weekly(store, monkeypatch):
    start, end = '2019-01-01', '2026-11-13'
    own = source_file(store, 'trading_calendar.json', 'tickflow')
    short_days = pd.bdate_range('2026-11-02', '2026-11-12').strftime('%Y-%m-%d').tolist()
    write_calendar(store, str(own.relative_to(store.root)), short_days[0], short_days[-1], short_days)
    seed = pd.DataFrame(dict(date=short_days, open=10., high=11., low=9., close=10., volume=1000.))
    old_id = save_dataset(store, 'sh.600000', seed, 'tickflow', '前复权')
    save_coverage(store, 'sh.600000', old_id, short_days[0], short_days[-1], 'tickflow')
    old_record = store.rows('SELECT * FROM datasets WHERE id=?', (old_id,))[0]
    old_bytes = (store.root / old_record['path']).read_bytes()
    calls = []

    class Provider:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def universe(self, day):
            return pd.DataFrame(dict(code=['sh.600000', 'sh.600001'], tradeStatus=['unknown'] * 2))
        def preload(self, codes, left, right):
            calendar = json.loads(own.read_text(encoding='utf-8'))
            assert (calendar['start'], calendar['end']) == (start, end)
            assert (left, right) == (start, end)  # Earlier real daily bars, not synthetic weekly bars.
            dates = calendar['trading_days']
            assert {year: sum(d.startswith(str(year)) for d in dates) for year in range(2019, 2024)} == {
                2019: 244, 2020: 243, 2021: 243, 2022: 242, 2023: 242}
            assert not set(['2019-05-02', '2019-05-03', '2020-01-31']) & set(dates)
            assert {'2019-05-06', '2020-02-03', '2026-11-13'} <= set(dates)
            calls.append((list(codes), left, right))
            self.data = pd.DataFrame(dict(date=dates, open=10., high=11., low=9., close=10., volume=1000.))
        def fetch(self, code, left, right):
            data = self.data if code == 'sh.600000' else self.data.iloc[-80:]
            return data[data.date.between(left, right)].copy()

    monkeypatch.setattr('workbench.tickflow.TickFlow', Provider)
    monkeypatch.setattr('workbench.service.BaoStock', lambda: pytest.fail('must not connect BaoStock'))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(source='tickflow', boards=['沪深主板'],
            start=start, end=end, scan_timeframe='weekly')))
        report = json.loads(row['result'])
        # Existing snapshots and first downloads keep their original request groups.
        assert calls == [(['sh.600000'], start, end), (['sh.600001'], start, end)]
        assert report['success'] == 2 and report['processed'] == 2
        saved, record = load_dataset(store, coverage_state(store, 'sh.600000', 'tickflow')[0]['dataset_id'])
        assert saved.date.iloc[0] == '2019-01-02' and saved.date.iloc[-1] == end
        assert record['source'] == 'tickflow' and record['timeframe'] == 'daily'
        assert (store.root / old_record['path']).read_bytes() == old_bytes
        assert row['status'] == 'partial'  # Downloads completed, but one stock still fails the 125-bar gate.
        weekly = report['scan_readiness']
        assert weekly['ready'] == 1 and not weekly['complete']
        assert weekly['eligible_ids'] == [record['id']]
        assert weekly['gaps'][0]['category'] == 'insufficient_bars'
        assert weekly['gaps'][0]['window']['rows'] < 125
        assert audit_scope(store, ['沪深主板'], end, source='tickflow', timeframe='weekly',
                           verify_files=True)['ready'] == 1
        assert not store.rows('SELECT * FROM observations') and not store.rows('SELECT * FROM plans')
    finally:
        service.pool.shutdown()


def test_no_reliable_history_refuses_before_provider_or_calendar_write(store, monkeypatch):
    own = source_file(store, 'trading_calendar.json', 'tickflow')
    write_calendar(store, str(own.relative_to(store.root)), '2024-01-01', '2026-11-13', ['2026-11-13'])
    before = own.read_bytes()
    monkeypatch.setattr('workbench.tickflow.TickFlow', lambda: pytest.fail('calendar must fail before requests'))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(source='tickflow', boards=['沪深主板'],
            start='2018-01-01', end='2026-11-13', scan_timeframe='weekly')))
        assert row['status'] == 'failed' and '2018 年缺少已核验历史交易日历' in row['message']
        assert own.read_bytes() == before
        assert not store.rows('SELECT * FROM datasets')
    finally:
        service.pool.shutdown()


def test_complete_local_history_reused_before_notices_even_with_short_own_calendar(store, monkeypatch):
    # Public source-neutral calendar can cover years not shipped by TickFlow.
    shared = write_calendar(store, 'trading_calendar.json', '2018-01-01', '2026-12-31',
                            ['2018-01-02', '2019-01-02', '2026-11-13'])
    original = (store.root / 'trading_calendar.json').read_bytes()
    own = source_file(store, 'trading_calendar.json', 'tickflow')
    short = write_calendar(store, str(own.relative_to(store.root)), '2026-11-13', '2026-12-31', ['2026-11-13'])
    monkeypatch.setattr('workbench.tickflow_calendar.HISTORICAL_SCHEDULES', {})
    monkeypatch.setattr('workbench.tickflow_calendar.SCHEDULES', {})
    payload = extend_exchange_calendar(store, '2018-01-01', '2026-11-13', source='tickflow')
    assert payload['trading_days'] == shared['trading_days']
    assert payload['end'] == short['end']  # Never shrink an existing longer tail.
    assert (store.root / 'trading_calendar.json').read_bytes() == original
    before = own.read_bytes()
    events = len(store.rows('SELECT seq FROM events'))
    assert extend_exchange_calendar(store, '2018-01-01', '2026-11-13', source='tickflow') == payload
    assert own.read_bytes() == before and len(store.rows('SELECT seq FROM events')) == events
