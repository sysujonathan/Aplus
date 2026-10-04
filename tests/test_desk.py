import json
import numpy as np
import pandas as pd
import pytest

from tests.test_workbench import store, frame, wait_for
from workbench.store import Store, now, dumps, ROOT
from workbench.market import save_dataset
from workbench.readiness import save_directory
from workbench.service import Service


def observation(store, frame, oid='signal'):
    did=save_dataset(store,'sh.600000',frame,'baostock','前复权')
    from workbench.strategies import catalog
    strategy=catalog(store)['MTR_MASTER']
    p={'entry':22.,'stop':20.,'target':26.,'warning':'','asof':frame.date.iloc[-1],'setup_date':frame.date.iloc[-1]}
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (oid,'scan','sh.600000',strategy.id,strategy.version,'daily',p['asof'],p['setup_date'],did,dumps(p),now()))
    return oid,did


def test_watch_survives_restart_new_scans_and_archiving_without_plan(store,frame):
    oid,_=observation(store,frame)
    store.watch(oid)
    store.update_watch('sh.600000','等回调')
    reopened=Store(store.root)
    reopened.watch(oid)
    rows=reopened.rows('SELECT * FROM watchlist')
    assert len(rows)==1 and rows[0]['notes']=='等回调'
    assert not reopened.rows('SELECT * FROM plans')
    reopened.update_watch('sh.600000','暂时放弃',False)
    assert not reopened.rows('SELECT * FROM watchlist WHERE active=1')
    reopened.watch(oid)
    assert reopened.rows('SELECT * FROM watchlist')[0]['notes']=='暂时放弃'
    assert reopened.rows('SELECT * FROM observations')[0]['id']==oid


def basic(codes, ipo=None, out=None):
    return pd.DataFrame({'code':codes,'type':['1']*len(codes),'status':['1']*len(codes),
                         'ipoDate':ipo or ['2000-01-01']*len(codes),'outDate':out or ['']*len(codes)})


def directory(codes, status=None):
    return pd.DataFrame({'code':codes,'tradeStatus':status or ['1']*len(codes),'code_name':['新名称']*len(codes)})


def test_new_listing_rename_suspension_and_proven_delisting(store):
    save_directory(store,directory(['sh.600000','sh.600001']),'2024-01-01')
    new=directory(['sh.600000','sh.600002'],['0','1'])
    info=basic(['sh.600000','sh.600001','sh.600002'],
               ['2000-01-01','2000-01-01','2024-01-02'],['','2024-01-02',''])
    save_directory(store,new,'2024-01-02',basics=info)
    saved=pd.read_csv(store.root/'universe.csv',dtype=str)
    assert set(saved.code)=={'sh.600000','sh.600002'}
    assert saved.tradeStatus.tolist()==['0','1']
    assert (store.root/'directories/2024-01-01.csv').exists()
    assert any('确认退市' in e['action'] for e in store.rows('SELECT * FROM events'))


def test_missing_live_stock_is_not_delisting_and_future_ipo_not_required(store):
    info=basic(['sh.600000','sh.600001','sh.600002'],['2000-01-01','2000-01-01','2025-01-01'])
    with pytest.raises(ValueError,match='应在市却缺少 1'):
        save_directory(store,directory(['sh.600000']),'2024-01-02',basics=info)
    save_directory(store,directory(['sh.600000','sh.600001']),'2024-01-02',basics=info)


def test_dual_timeframe_resume_keeps_daily_when_interrupted_before_weekly(store,monkeypatch):
    frame=pd.DataFrame({'date':pd.bdate_range('2020-01-01',periods=800).strftime('%Y-%m-%d'),
                        'open':20.,'high':21.,'low':19.,'close':20.5,'volume':10000.})
    did=save_dataset(store,'sh.600000',frame,'csv','前复权')
    service=Service(store)
    spec={'datasets':[did],'strategies':['STRATEGY_GAP_H2'],'timeframes':['daily','weekly'],'asof':frame.date.iloc[-1]}
    stop_once=[True]
    def signal(strategy,data,**kwargs):
        if stop_once[0]:
            stop_once[0]=False
            next(iter(service.cancel_flags.values())).set()
        return {'asof':data.date.iloc[-1],'setup_date':data.date.iloc[-1],'entry':20.,'stop':19.,'target':22.}
    monkeypatch.setattr('workbench.service.signal_at_end',signal)
    first=wait_for(store,service.submit('scan',spec))
    assert first['status']=='cancelled',first['message']
    assert len(json.loads(first['result'])['observation_ids'])==1
    second=wait_for(store,service.submit('scan',spec))
    service.pool.shutdown()
    r=json.loads(second['result'])
    assert second['status']=='completed',second['message']
    assert r['signals']==2 and r['reused']==1 and r['calculated']==1
    assert {o['timeframe'] for o in store.rows('SELECT * FROM observations')}=={'daily','weekly'}


def test_dashboard_has_only_market_source_and_persistent_watch(tmp_path,monkeypatch,frame):
    from streamlit.testing.v1 import AppTest
    from app import resources
    resources.clear()
    root=tmp_path/'desk'
    monkeypatch.setenv('A_WORKBENCH_HOME',str(root))
    store=Store(root)
    oid,did=observation(store,frame)
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  ('scan','scan','completed',now(),dumps({'source':'baostock','datasets':[did],
                    'strategies':['MTR_MASTER'],'timeframes':['daily'],'asof':frame.date.iloc[-1]}),
                   dumps({'signals':1,'observation_ids':[oid]})))
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
    assert not app.error and not app.exception, [e.value for e in app.error]
    assert not any(s.label=='行情来源' for s in app.selectbox)
    assert not app.tabs
    assert any(c.label=='同时扫描周线' for c in app.checkbox)
    assert not any(b.label=='更新股票目录' for b in app.button)
    next(b for b in app.button if b.label=='＋ 加入关注').click().run()
    assert not app.error and len(store.rows('SELECT * FROM watchlist'))==1
    assert not store.rows('SELECT * FROM plans')
    next(t for t in app.text_area if t.label=='观察什么、何时放弃').set_value('等突破')
    next(b for b in app.button if b.label=='保存关注笔记').click().run()
    assert store.rows('SELECT notes FROM watchlist')[0]['notes']=='等突破'


def test_v3_upgrade_preserves_existing_records(store,frame):
    oid,_=observation(store,frame)
    store.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    store.execute('DROP TABLE watchlist')
    reopened=Store(store.root)
    assert reopened.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']=='8'
    assert reopened.rows('SELECT id FROM observations')[0]['id']==oid
    reopened.watch(oid)
