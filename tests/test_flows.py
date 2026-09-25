import json
import threading
import time

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import ROOT, Store, dumps, now
from workbench.strategies import _setup_date, catalog, register, set_active
from tests.test_workbench import frame, store, wait_for


def breakout_frame(frame):
    frame = frame.copy()
    for i in range(135,len(frame)):
        price = 26+(i-135)*.8
        frame.loc[i,['open','high','low','close']] = [price-.2,price+.3,price-.4,price]
    return frame


def candidate(store):
    return register(store,(ROOT/'examples/new_strategy.py').read_bytes(),'ExampleStrategy','接口测试',{},['daily'])


def test_real_research_then_manual_promotion_then_scan(store,frame):
    key = candidate(store)
    did = save_dataset(store,'sh.600000',breakout_frame(frame),'csv','前复权')
    service = Service(store)
    job = service.submit('validate',{'strategy':key,'dataset':did,'timeframe':'daily'})
    assert wait_for(store,job)['status']=='completed'
    job = service.submit('backtest',{'strategy':key,'datasets':[did],'timeframe':'daily','start':'2024-01-01',
                                   'end':'2024-12-31','assumptions':{'holding_bars':3}})
    report = wait_for(store,job)
    assert report['status']=='completed',report
    assert json.loads(report['result'])['closed_trades']>0
    assert catalog(store)[key].state=='research'
    set_active(store,key,True,'测试数据模拟人工审批')
    spec = {'datasets':[did],'strategies':[key],'timeframe':'daily','asof':'2024-12-31'}
    j1 = service.submit('scan',spec)
    r1 = wait_for(store,j1)
    assert r1['status']=='completed',r1
    j2 = service.submit('scan',spec)
    r2 = wait_for(store,j2)
    assert r2['status']=='completed',r2
    assert json.loads(r1['result'])['observation_ids']==json.loads(r2['result'])['observation_ids']
    observations = store.rows('SELECT * FROM observations')
    assert len(observations)==1
    store.save_plan(observations[0]['id'],'观察',40,39,42,100,'测试人工判断')
    assert len(store.rows('SELECT * FROM plans'))==1
    set_active(store,key,False,'退回研究')
    assert catalog(store)[key].state=='research'
    service.pool.shutdown()


def test_h2_anchor_uses_original_projection():
    from core.strategies.gap_h2_strategy import GapH2Strategy
    instance = GapH2Strategy()
    df = pd.DataFrame({'date':['2024-01-01','2024-01-02','2024-01-03'],
                       'high':[10.,9.8,9.6],'low':[9.2,9.1,9.0],
                       'signal_gap_h2':[True,False,False],'sl_gap_h2':[8.,np.nan,np.nan],
                       'tp_gap_h2':[13.,np.nan,np.nan]})
    assert _setup_date(instance,df,instance.get_metadata())=='2024-01-01'


def test_cancel_job_and_busy_rejection(store):
    service = Service(store)
    entered = threading.Event()
    def slow(job,spec):
        entered.set()
        while not service.cancel_flags[job].is_set():
            time.sleep(.01)
        service.check_stop(job)
    service._universe = slow
    job = service.submit('universe',{})
    assert entered.wait(2)
    with pytest.raises(ValueError,match='已有任务'): service.submit('universe',{})
    service.cancel(job)
    assert wait_for(store,job)['status']=='cancelled'
    service.pool.shutdown()


def test_identity_keeps_strategy_timeframe_and_version_separate(store):
    ids = []
    for i,(strategy,version,tf) in enumerate([('s1','v1','daily'),('s2','v1','daily'),('s1','v1','weekly'),('s1','v2','daily')]):
        oid = f'o{i}'
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (oid,'job','sh.600000',strategy,version,tf,'2024-01-05','2024-01-01','data','{}',now()))
        ids.append(store.save_plan(oid,'观察',10,9,12,100,'test'))
    assert len(set(ids))==4


def test_demo_cannot_promote(store):
    key = candidate(store)
    spec = catalog(store)[key]
    store.execute('INSERT INTO validations VALUES(?,?,?,?,?,?,?)',('v',key,spec.version,'j',1,dumps({'timeframe':'daily'}),now()))
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  ('j','backtest','completed',now(),dumps({'strategy':key,'timeframe':'daily'}),
                   dumps({'strategy_version':spec.version,'closed_trades':10,'real_data':False})))
    with pytest.raises(ValueError,match='真实行情'): set_active(store,key,True,'不能靠演示数据启用')


def test_ui_with_saved_signal_and_plan(tmp_path,monkeypatch,frame):
    from app import resources
    resources.clear()
    root = tmp_path/'ui-with-results'
    monkeypatch.setenv('A_WORKBENCH_HOME',str(root))
    store = Store(root)
    did = save_dataset(store,'sh.600000',frame,'baostock','前复权')
    strategy = 'STRATEGY_3K'
    spec = catalog(store)[strategy]
    payload = {'entry':22.,'stop':20.,'target':26.,'score':1.,'rating':None,'warning':'',
               'close':21.,'asof':frame.date.iloc[-1],'setup_date':frame.date.iloc[-1]}
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  ('obs','scan','sh.600000',strategy,spec.version,'daily',payload['asof'],payload['setup_date'],did,dumps(payload),now()))
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  ('scan','scan','completed',now(),dumps({'datasets':[did],'strategies':[strategy],'timeframe':'daily','asof':'2024-12-31'}),
                   dumps({'success':1,'signals':1,'errors':[],'observation_ids':['obs'],'strategy_versions':{strategy:spec.version}})))
    app = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
    app.sidebar.radio[0].set_value('交易工作台').run()
    assert not app.exception
    assert not app.error, [e.value for e in app.error]
    button = next(b for b in app.button if b.label=='保存我的计划')
    button.click().run()
    assert not app.error
    assert len(store.rows('SELECT * FROM plans'))==1
    app.sidebar.radio[0].set_value('我的计划').run()
    assert not app.exception
    assert not app.error
