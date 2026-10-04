import json
import sqlite3

import pandas as pd
import pytest
from workbench.market import board_of, select_board_codes, code_of

from tests.test_workbench import store, frame, wait_for
from workbench.market import load_dataset, save_dataset
from workbench.store import Store, dumps, now
from workbench.sync import sync_stock
from workbench.service import Service


class Provider:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def fetch(self, code, start, end):
        self.calls.append((start, end))
        return self.frame.loc[self.frame.date.between(start, end)].copy()


def test_board_selection_excludes_indexes_and_funds():
    directory = pd.DataFrame({'code':['sh.600000','sz.000001','sz.300750','sh.688001',
                                     'bj.920002','sh.000001','sh.510300','sz.399001']})
    assert select_board_codes(directory,['沪深主板'])==['sh.600000','sz.000001']
    assert select_board_codes(directory,['创业板','科创板'])==['sz.300750','sh.688001']
    assert select_board_codes(directory,['北交所'])==['bj.920002']
    assert code_of('920002')=='bj.920002'
    with pytest.raises(ValueError,match='至少勾选'):
        select_board_codes(directory,[])
    with pytest.raises(ValueError,match='北交所'):
        select_board_codes(directory.iloc[:4],['沪深主板','北交所'])


def test_market_ui_has_four_boards_without_three_stock_input(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app import resources
    from workbench.store import ROOT
    resources.clear()
    monkeypatch.setenv('A_WORKBENCH_HOME',str(tmp_path/'board-ui'))
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
    app.sidebar.radio[0].set_value('交易工作台').run()
    assert not app.exception
    for name in ['沪深主板','创业板','科创板','北交所']:
        assert any(c.label==name for c in app.checkbox)
    assert not any('股票代码' in t.label for t in app.text_area)
    for c in app.checkbox:
        if c.label in ['沪深主板','创业板','科创板']:
            c.set_value(False)
    app.run()
    next(b for b in app.button if b.label=='同步市场数据').click().run()
    assert any('至少勾选' in e.value for e in app.error)


def test_repeat_and_narrow_do_not_connect(store, frame):
    p = Provider(frame)
    did, _ = sync_stock(store, lambda:p, 'sh.600000', '2023-01-01', frame.date.iloc[-1])
    def offline():
        raise AssertionError('Cached data must not connect')
    assert sync_stock(store, offline, 'sh.600000', '2023-01-01', frame.date.iloc[-1]) == (did,'skipped')
    assert sync_stock(store, offline, 'sh.600000', frame.date.iloc[50], frame.date.iloc[60]) == (did,'skipped')
    assert len(p.calls)==1


def test_repeat_sync_verifies_bytes_without_reparsing_csv(store, frame, monkeypatch):
    p = Provider(frame)
    did, _ = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    monkeypatch.setattr('workbench.market.pd.read_csv',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unchanged CSV was reparsed')))
    assert sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1]) == (did,'skipped')


def test_repeat_sync_still_rejects_tampered_cached_snapshot(store, frame):
    p = Provider(frame)
    did, _ = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    record = store.rows('SELECT path FROM datasets WHERE id=?', (did,))[0]
    (store.root / record['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='校验'):
        sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])


def test_extend_both_ends_and_preserve_snapshot(store, frame):
    p = Provider(frame)
    first, _ = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[30], frame.date.iloc[80])
    did, action = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    assert action=='updated'
    assert p.calls[1:]==[(frame.date.iloc[0],frame.date.iloc[30]),(frame.date.iloc[80],frame.date.iloc[-1])]
    pd.testing.assert_frame_equal(load_dataset(store,did)[0],frame,check_exact=False)
    assert len(load_dataset(store,first)[0])==51


def test_adjustment_change_refreshes_only_this_stock(store, frame):
    p = Provider(frame)
    first, _ = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[80])
    p.frame = frame.copy()
    p.frame.loc[:,['open','high','low','close']] *= .8
    did, action = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    assert action=='refreshed'
    assert p.calls[-2:]==[(frame.date.iloc[80],frame.date.iloc[-1]),(frame.date.iloc[0],frame.date.iloc[-1])]
    pd.testing.assert_frame_equal(load_dataset(store,did)[0],p.frame,check_exact=False)
    assert load_dataset(store,first)[0].close.iloc[0]==pytest.approx(frame.close.iloc[0])


def test_failure_and_unpublished_tail_do_not_advance(store, frame):
    p = Provider(frame.iloc[:81])
    first, _ = sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[80])
    sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    assert store.rows('SELECT * FROM sync_coverage')[0]['end']==frame.date.iloc[80]
    p.frame = frame.iloc[82:]
    with pytest.raises(ValueError,match='核对日'):
        sync_stock(store, lambda:p, 'sh.600000', frame.date.iloc[0], frame.date.iloc[-1])
    assert store.rows('SELECT * FROM sync_coverage')[0]['dataset_id']==first


def test_upgrade_reuses_v1_success_even_cancelled_job(store, frame):
    store.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    store.execute('DROP TABLE sync_coverage')
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('old','sync','cancelled',now(),dumps({'start':'2013-01-01','end':frame.date.iloc[-1]})))
    did = save_dataset(store,'sh.600000',frame,'baostock','前复权','old')
    upgraded = Store(store.root)
    assert upgraded.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']=='8'
    def offline():
        raise AssertionError('Upgrade must reuse successfully saved history')
    assert sync_stock(upgraded,offline,'sh.600000','2013-01-01',frame.date.iloc[-1])==(did,'skipped')


def test_partial_batch_retry_only_failed_stock(store, frame, monkeypatch):
    p = Provider(frame)
    fail = [True]
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fetch(self,code,start,end):
            if code=='sz.000001' and fail[0]: raise RuntimeError('test network interruption')
            return p.fetch(code,start,end)
    monkeypatch.setattr('workbench.service.BaoStock',Connection)
    service = Service(store)
    spec = {'codes':['sh.600000','sz.000001'],'start':frame.date.iloc[0],'end':frame.date.iloc[-1]}
    first = wait_for(store,service.submit('sync',spec))
    assert first['status']=='partial'
    fail[0]=False
    second = wait_for(store,service.submit('sync',spec))
    result=json.loads(second['result'])
    assert second['status']=='completed'
    assert result['skipped']==1 and result['downloaded']==1
    assert len(p.calls)==2
    service.pool.shutdown()


def test_force_refresh_does_not_erase_older_history(store, frame):
    p=Provider(frame)
    first,_=sync_stock(store,lambda:p,'sh.600000',frame.date.iloc[0],frame.date.iloc[-1])
    _,action=sync_stock(store,lambda:p,'sh.600000',frame.date.iloc[30],frame.date.iloc[50],force=True)
    assert action=='refreshed'
    assert p.calls[-1]==(frame.date.iloc[0],frame.date.iloc[-1])
    p.frame=frame.iloc[30:]
    with pytest.raises(ValueError,match='不完整'):
        sync_stock(store,lambda:p,'sh.600000',frame.date.iloc[0],frame.date.iloc[-1],force=True)
    assert store.rows('SELECT * FROM sync_coverage')[0]['dataset_id']==first
