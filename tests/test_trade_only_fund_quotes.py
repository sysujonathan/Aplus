import threading

import pandas as pd
import pytest

from workbench.store import Store
from workbench.daily_returns import historical_daily_returns
from workbench.holding_quotes import saved_quotes
from workbench.watch_history import fetch_history
from workbench.scope import scan_datasets

CODE='sz.160723'


def test_fund_daily_cache_values_inventory_without_entering_scan_pool(tmp_path):
    store=Store(tmp_path)
    store.execute("INSERT INTO meta VALUES('daily_market_source','tickflow')")
    class Provider:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def fetch_unadjusted(self,code,start,end):
            assert code==CODE
            return pd.DataFrame(dict(date=['2026-11-02','2026-11-03'],open=[1.2,1.3],
                high=[1.3,1.4],low=[1.1,1.2],close=[1.25,1.35],volume=[100,200]))
    _,record=fetch_history(store,CODE,'baostock',threading.Event(),provider_factory=Provider,end='2026-11-03')
    before=store.path.read_bytes()
    quotes=saved_quotes(store,[CODE])
    assert quotes[CODE]==dict(price=1.35,previous_close=1.25,date='2026-11-03',source='history',adjusted=False)
    assert store.path.read_bytes()==before
    assert not scan_datasets(store,'tickflow')
    assert not store.rows('SELECT * FROM datasets')
    (store.root/record['path']).write_bytes(b'tampered synthetic test file')
    assert saved_quotes(store,[CODE])=={}  # never infer a usable price from a broken cache


def test_same_day_fund_round_trip_needs_only_real_fills_and_fees(tmp_path):
    store=Store(tmp_path)
    account=store.save_account('合成账户',10000,accounting_mode='history')
    position=store.save_position(account,CODE,'合成原油LOF',1.2,1.1,1.4,
        [{'date':'2026-11-04','price':1.2,'hands':2,'fees':1}])
    store.sell_position(position,[{'date':'2026-11-04','price':1.3,'hands':2}],2)
    missing={}
    result=historical_daily_returns(store,account,missing=missing)
    assert len(result)==1 and result[0]['pnl']==pytest.approx(17)
    assert not missing and not store.rows('SELECT * FROM datasets')
    assert not scan_datasets(store,'baostock')


def test_fund_overnight_inventory_without_closes_still_reports_missing(tmp_path):
    from workbench.daily_returns import calculate_daily_pnl
    gaps={}
    result=calculate_daily_pnl([dict(code=CODE,trade_date='2026-11-03',side='BUY',
        price=1.2,quantity=200,fees=1)],{},'2026-11-04',missing=gaps)
    assert result is None and CODE in gaps  # not zero P&L or a fabricated fill price
