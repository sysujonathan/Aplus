import json

import pandas as pd
import pytest

from tests.test_workbench import store, frame, wait_for
from workbench.market import save_dataset
from workbench.readiness import audit_scope, expected_day, save_calendar, check_response_dates
from workbench.service import Service
from workbench.sync import sync_stock


def setup_scope(store, codes, start, end, suspended=()):
    dates = pd.date_range(start, end)
    save_calendar(store, pd.DataFrame({'calendar_date': dates.strftime('%Y-%m-%d'),
                  'is_trading_day': [str(int(d.weekday() < 5)) for d in dates]}), start, end)
    directory = pd.DataFrame({'code': codes, 'tradeStatus': ['0' if c in suspended else '1' for c in codes]})
    store.write_artifact('universe.csv', directory.to_csv(index=False).encode())
    store.execute("INSERT INTO meta VALUES('universe_date',?)", (expected_day(store, end),))


def test_3000_expected_2990_available_never_silently_shrinks(store, frame):
    codes = [f'sh.{prefix}{i:03}' for prefix in ['600','601','603'] for i in range(1000)]
    end = frame.date.iloc[-1]
    setup_scope(store, codes, frame.date.iloc[0], end)
    did = save_dataset(store, codes[0], frame, 'baostock', '前复权')
    # Metadata fixture uses one valid file to avoid creating 2990 large CSVs.
    with store.connect() as db:
        for code in codes[1:2990]:
            db.execute('INSERT INTO datasets SELECT ?,?,timeframe,source,adjustment,start,end,rows,path,sha256,created FROM datasets WHERE id=?', (code, code, did))
    result = audit_scope(store, ['沪深主板'], end)
    assert result['expected'] == 3000 and result['ready'] == 2990
    assert len(result['gaps']) == 10 and not result['complete']


def test_weekend_suspension_and_young_stock_are_not_missing(store, frame):
    setup_scope(store, ['sh.600000','sh.600001'], '2024-01-01', '2024-01-07', ['sh.600001'])
    save_dataset(store, 'sh.600000', frame.iloc[:5], 'baostock', '前复权')
    result = audit_scope(store, ['沪深主板'], '2024-01-07')
    assert result['expected_day'] == '2024-01-05'
    assert result['ready'] == 1 and result['suspended'] == 1 and result['complete']


def test_stale_missing_file_and_stale_directory_are_reported(store, frame):
    end = frame.date.iloc[-1]
    setup_scope(store, ['sh.600000','sh.600001'], frame.date.iloc[0], end)
    save_dataset(store, 'sh.600000', frame.iloc[:-1], 'baostock', '前复权')
    did = save_dataset(store, 'sh.600001', frame, 'baostock', '前复权')
    store.execute("UPDATE datasets SET path='market/absent.csv' WHERE id=?", (did,))
    result = audit_scope(store, ['沪深主板'], end)
    assert len(result['gaps']) == 2
    store.execute("UPDATE meta SET value='2020-01-01' WHERE key='universe_date'")
    assert '股票目录日期' in audit_scope(store, ['沪深主板'], end)['gaps'][0]['error']


def test_calendar_missing_days_fails_and_holiday_not_expected(store):
    dates = ['2024-10-01','2024-10-02','2024-10-03']
    with pytest.raises(ValueError, match='不完整'):
        save_calendar(store, pd.DataFrame({'calendar_date': dates[:2], 'is_trading_day':['0','0']}), dates[0], dates[-1])
    save_calendar(store, pd.DataFrame({'calendar_date':['2024-09-30'] + dates,
                  'is_trading_day':['1','0','0','0']}), '2024-09-30', dates[-1])
    assert expected_day(store, dates[-1]) == '2024-09-30'


def test_response_holes_are_rejected_except_proven_suspension(store, frame):
    end = frame.date.iloc[4]
    setup_scope(store, ['sh.600000'], frame.date.iloc[0], end)
    broken = frame.iloc[:5].drop(index=2)
    with pytest.raises(ValueError, match='缺少 1'):
        check_response_dates(store, broken, frame.date.iloc[0], end)
    broken.attrs['returned_dates'] = frame.date.iloc[:5].tolist()
    broken.attrs['suspended_dates'] = [frame.date.iloc[2]]
    check_response_dates(store, broken, frame.date.iloc[0], end)


def test_incomplete_update_preserves_old_snapshot(store, frame):
    from tests.test_incremental_sync import Provider
    provider = Provider(frame.iloc[:4])
    first, _ = sync_stock(store, lambda:provider, 'sh.600000', frame.date.iloc[0], frame.date.iloc[3])
    setup_scope(store, ['sh.600000'], frame.date.iloc[0], frame.date.iloc[4])
    with pytest.raises(ValueError, match='缺少 1'):
        sync_stock(store, lambda:provider, 'sh.600000', frame.date.iloc[0], frame.date.iloc[4])
    assert store.rows('SELECT dataset_id FROM sync_coverage')[0]['dataset_id'] == first


def test_backend_blocks_incomplete_scan_without_calling_strategy(store, frame, monkeypatch):
    end = frame.date.iloc[-1]
    setup_scope(store, ['sh.600000','sh.600001'], frame.date.iloc[0], end)
    did = save_dataset(store, 'sh.600000', frame, 'baostock', '前复权')
    def forbidden(*args):
        raise AssertionError('Must not call strategy on an incomplete universe')
    monkeypatch.setattr('workbench.service.signal_at_end', forbidden)
    service = Service(store)
    row = wait_for(store, service.submit('scan', {'datasets':[did], 'source':'baostock', 'boards':['沪深主板'],
                         'strategies':['MTR_MASTER'], 'timeframe':'daily', 'asof':end}))
    service.pool.shutdown()
    report = json.loads(row['result'])
    assert row['status'] == 'partial' and report['success'] == 0
    assert report['coverage']['expected'] == 2 and not store.rows('SELECT * FROM observations')


def test_short_directory_does_not_replace_existing_universe(store):
    from workbench.readiness import save_directory
    old = pd.DataFrame({'code':['sh.600000','sh.600001'], 'tradeStatus':['1','1']})
    save_directory(store, old, '2024-01-01')
    before = (store.root/'universe.csv').read_bytes()
    with pytest.raises(ValueError, match='缺少 1 只'):
        save_directory(store, old.iloc[:1], '2024-01-02')
    assert (store.root/'universe.csv').read_bytes() == before
    assert store.rows("SELECT value FROM meta WHERE key='universe_date'")[0]['value']=='2024-01-01'


def test_board_sync_checks_dates_and_repeat_reuses_all_bars(store, frame, monkeypatch):
    calls = []
    class Provider:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def calendar(self, start, end):
            dates = pd.date_range(start,end)
            return pd.DataFrame({'calendar_date':dates.strftime('%Y-%m-%d'),
                                 'is_trading_day':[str(int(d.weekday()<5)) for d in dates]})
        def universe(self, day):
            return pd.DataFrame({'code':['sh.600000','sh.600001'], 'tradeStatus':['1','1']})
        def fetch(self, code, start, end):
            calls.append(code)
            return frame.loc[frame.date.between(start,end)].copy()
    monkeypatch.setattr('workbench.service.BaoStock', Provider)
    service = Service(store)
    spec = {'boards':['沪深主板'],'start':frame.date.iloc[0],'end':frame.date.iloc[-1]}
    first = wait_for(store, service.submit('sync',spec))
    assert first['status']=='completed', first['message']
    assert json.loads(first['result'])['coverage']['ready']==2
    second = wait_for(store, service.submit('sync',spec))
    service.pool.shutdown()
    assert second['status']=='completed'
    assert json.loads(second['result'])['skipped']==2 and len(calls)==2


def test_two_connections_are_bounded_and_reused(store, frame):
    import threading
    import time
    from workbench.sync_batch import sync_results
    lock = threading.Lock()
    instances, current, maximum = [], [0], [0]
    class Provider:
        def __enter__(self):
            instances.append(self)
            return self
        def __exit__(self, *args): self.closed = True
        def fetch(self, *args):
            with lock:
                current[0] += 1
                maximum[0] = max(maximum[0],current[0])
            time.sleep(.1)
            with lock: current[0] -= 1
            return frame
    codes = [f'sh.{600000+i}' for i in range(6)]
    results = list(sync_results(store,Provider,codes,frame.date.iloc[0],frame.date.iloc[-1],None,False,
                   threading.Event(),threading.Event(),2,lambda *args:None))
    assert len(results)==6 and all(r[3] is None for r in results)
    assert maximum[0]==2 and len(instances)==2 and all(p.closed for p in instances)
