import json
import threading
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workbench.backtest import Assumptions, run_study, simulate, summarize
from workbench.market import code_of, load_dataset, save_dataset, validate_bars, weekly_bars
from workbench.service import Service, demo_data, import_csv
from workbench.store import ROOT, Store, dumps, now
from workbench.strategies import calculate, catalog, register, set_active, signal_at_end, verify_frozen


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'isolated-runtime')


@pytest.fixture
def frame():
    x = np.arange(160)
    close = 20+np.sin(x/4)*2+x/100
    return pd.DataFrame({'date':pd.bdate_range('2024-01-01',periods=160).strftime('%Y-%m-%d'),
                         'open':close-.1,'high':close+.5,'low':close-.5,'close':close,'volume':10000.})


def wait_for(store,job):
    for _ in range(300):
        row = store.rows('SELECT * FROM jobs WHERE id=?',(job,))[0]
        if row['status'] not in {'queued','running'}:
            return row
        time.sleep(.02)
    raise AssertionError('Job did not finish')


def plugin(store):
    return register(store,(ROOT/'examples/new_strategy.py').read_bytes(),'ExampleStrategy','Test',{},['daily'])


def test_frozen_integrity():
    assert len(verify_frozen())==64


@pytest.mark.parametrize('key', ['MTR_MASTER','STRATEGY_3K','STRATEGY_STRUCTURAL_GAP','STRATEGY_GAP_PINBAR',
                                  'STRATEGY_GAP_H2','STRATEGY_GAP_H2_ENHANCED','STRATEGY_AWIL','STRATEGY_MONTHLY_RANGE_BREAK'])
def test_original_strategies_run_unchanged(store,frame,key):
    spec = catalog(store)[key]
    _,result = calculate(spec,frame)
    assert len(result)==len(frame)
    assert (result.close==frame.close).all()
    # Adapter must exactly preserve the original strategy calculation on same input.
    from core.calculator import add_indicators
    original_input = frame.copy()
    original_input['trade_date'] = original_input.date
    expected = spec.cls().calculate_signals(add_indicators(original_input))
    pd.testing.assert_frame_equal(result,expected)


@pytest.mark.parametrize('value,expected',[('600000','sh.600000'),('000001','sz.000001'),('300750','sz.300750'),('sh.600000','sh.600000')])
def test_codes(value,expected):
    assert code_of(value)==expected


def test_validate_no_future_fill(frame):
    bad = frame.copy()
    bad.loc[3,'close'] = np.nan
    with pytest.raises(ValueError): validate_bars(bad)
    bad = frame.copy()
    bad.loc[2,'low'] = 1000
    with pytest.raises(ValueError): validate_bars(bad)
    with pytest.raises(ValueError): validate_bars(pd.concat([frame,frame.iloc[:1]]))


def test_snapshot_immutable_and_tamper_detection(store,frame):
    a = save_dataset(store,'600000',frame,'csv','前复权')
    b = save_dataset(store,'600000',frame,'csv','前复权')
    assert a==b
    data,record = load_dataset(store,a)
    assert len(data)==len(frame)
    changed = frame.copy()
    changed.loc[0,'volume'] += 1
    c = save_dataset(store,'600000',changed,'csv','前复权')
    assert c!=a
    (store.root/record['path']).write_bytes(b'corruption')
    with pytest.raises(ValueError,match='校验'): load_dataset(store,a)


def test_path_boundary(store):
    with pytest.raises(ValueError): store.write_artifact('../escaped.txt',b'x')
    with pytest.raises(ValueError): store.read_artifact('../escaped.txt')


def test_weekly_excludes_open_week(frame):
    weekly = weekly_bars(frame,'2024-01-10')
    assert weekly.date.max()=='2024-01-05'
    first = frame.iloc[:5]
    assert weekly.iloc[0].high==first.high.max()


def test_daily_prefix_does_not_see_future(store,frame):
    key = plugin(store)
    spec = catalog(store)[key]
    before = signal_at_end(spec,frame.iloc[:130])
    changed = frame.copy()
    changed.loc[130:,'high'] = 999
    assert signal_at_end(spec,changed.iloc[:130])==before


def make_bars(rows):
    return pd.DataFrame([{'date':f'2024-01-{i+1:02d}','open':o,'high':h,'low':l,'close':c,'volume':1000.}
                         for i,(o,h,l,c) in enumerate(rows)])


SIGNAL = dict(entry=10.,stop=9.,target=12.,setup_date='2024-01-01')


def test_next_bar_entry_and_t_plus_one():
    df = make_bars([(9.5,15,8,10),(10,13,8,10),(10,13,9.5,12)])
    result = simulate(df,0,SIGNAL,Assumptions(slippage_bps=0,commission_bps=0,sell_tax_bps=0))
    assert result['entry_date']=='2024-01-02'
    assert result['exit_date']=='2024-01-03'
    assert result['reason']=='target'


def test_ambiguous_exit_uses_stop():
    df = make_bars([(9.5,10,9.2,9.8),(10,11,9.5,10.5),(10,13,8,11)])
    result = simulate(df,0,SIGNAL,Assumptions(slippage_bps=0))
    assert result['reason']=='stop'


def test_expired_entry_cannot_fill_later():
    df = make_bars([(9.5,9.9,9.2,9.8),(9.6,9.9,9.5,9.8),(10,11,9.5,10.5)])
    result = simulate(df,0,SIGNAL,Assumptions(wait_bars=1))
    assert result['status']=='not_triggered'


def test_gap_stop_and_costs():
    df = make_bars([(9.5,10,9.2,9.8),(10,11,9.5,10.5),(8,9,7,8)])
    result = simulate(df,0,SIGNAL,Assumptions(slippage_bps=10))
    assert result['reason']=='gap_stop'
    assert result['exit']<8
    assert result['entry']>10


def test_open_position_not_forced_win():
    df = make_bars([(9.5,10,9.2,9.8),(10,11,9.5,10.5)])
    result = simulate(df,0,SIGNAL,Assumptions())
    assert result['status']=='open'
    assert summarize([result])['closed_trades']==0


def test_lock_bar_does_not_fill():
    df = make_bars([(9.5,10,9.2,9.8),(10,10,10,10)])
    assert simulate(df,0,SIGNAL,Assumptions(wait_bars=1))['status']=='not_triggered'


def test_hold_timeout_waits_for_executable_bar():
    df = make_bars([(9.5,10,9.2,9.8),(10,11,9.5,10.5),(10,10,10,10),(10,11,9.5,10.5)])
    result = simulate(df,0,SIGNAL,Assumptions(holding_bars=1))
    assert result['reason']=='time'
    assert result['exit_date']=='2024-01-04'


def test_register_research_not_auto_active(store):
    key = plugin(store)
    assert catalog(store)[key].state=='research'
    with pytest.raises(ValueError,match='先完成'): set_active(store,key,True,'test')
    assert catalog(store)[key].state=='research'


def test_end_to_end_study_and_validation(store,frame):
    key = plugin(store)
    did = save_dataset(store,'sh.600000',frame,'csv','前复权')
    service = Service(store)
    j = service.submit('validate',{'strategy':key,'dataset':did,'timeframe':'daily'})
    result = wait_for(store,j)
    assert result['status']=='completed',result
    j = service.submit('backtest',{'strategy':key,'datasets':[did],'timeframe':'daily',
        'start':'2024-07-01','end':'2024-08-30','assumptions':{}})
    result = wait_for(store,j)
    assert result['status']=='completed',result
    report = json.loads(result['result'])
    assert (store.root/report['report_path']).exists()
    assert not store.rows('SELECT * FROM observations')
    assert not store.rows('SELECT * FROM plans')
    service.pool.shutdown()


def test_scan_errors_are_not_no_signal(store,frame):
    did = save_dataset(store,'sh.600000',frame.iloc[:20],'csv','前复权')
    service = Service(store)
    j = service.submit('scan',{'datasets':[did],'strategies':['STRATEGY_3K'],'timeframe':'daily','asof':'2024-08-30'})
    row = wait_for(store,j)
    assert row['status']=='partial'
    assert len(json.loads(row['result'])['errors'])==1
    service.pool.shutdown()


def test_plan_changes_preserve_history_and_identity(store):
    for oid in ['o1','o2']:
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (oid,'job','sh.600000','test','v1','daily','2024-01-02','2024-01-01','data','{}',now()))
    p1 = store.save_plan('o1','观察',10,9,12,100,'first')
    p2 = store.save_plan('o2','计划交易',10.1,9,12,100,'second')
    assert p1==p2
    assert len(store.rows('SELECT * FROM plans'))==1
    assert len(store.rows('SELECT * FROM plan_history'))==2
    with pytest.raises(ValueError): store.save_plan('o1','计划交易',8,9,12,100,'bad')


def test_restart_marks_incomplete(store):
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',('old','sync','running',now(),'{}'))
    service = Service(store)
    assert store.rows('SELECT status FROM jobs')[0]['status']=='interrupted'
    service.pool.shutdown()


def test_ui_pages_no_exceptions(tmp_path,monkeypatch):
    monkeypatch.setenv('A_WORKBENCH_HOME',str(tmp_path/'ui-runtime'))
    app = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
    assert not app.exception
    for page in ['市场数据','我的计划','回测研究','策略工厂','运行与文件','使用说明','日常扫描']:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, (page,list(app.exception))
        assert not app.error, (page,[e.value for e in app.error])
