import json
from tests.test_workbench import store, frame, wait_for
from workbench.market import save_dataset
from workbench.service import Service


def test_cancel_persists_and_retry_reuses_no_signal(store,frame,monkeypatch):
    ids=[save_dataset(store,c,frame,'baostock','前复权') for c in ['sh.600000','sz.000001']]
    svc=Service(store)
    calls=[]
    def calc(strategy,data,**kwargs):
        calls.append(1)
        if len(calls)==1:
            for flag in svc.cancel_flags.values(): flag.set()
        return None
    monkeypatch.setattr('workbench.service.signal_at_end',calc)
    spec=dict(datasets=ids,strategies=['STRATEGY_3K'],timeframe='daily',asof='2024-12-31')
    first=wait_for(store,svc.submit('scan',spec))
    assert first['status']=='cancelled'
    assert json.loads(first['result'])['no_signal']==1
    second=wait_for(store,svc.submit('scan',spec))
    assert second['status']=='completed'
    assert json.loads(second['result'])['reused']==1 and len(calls)==2
    third=wait_for(store,svc.submit('scan',spec))
    assert json.loads(third['result'])['reused']==2 and len(calls)==2
    changed=wait_for(store,svc.submit('scan',dict(spec,asof='2024-12-30')))
    assert json.loads(changed['result'])['reused']==0 and len(calls)==4
    svc.pool.shutdown()


def test_failed_calculation_retried_and_hits_preserved(store,frame,monkeypatch):
    ids=[save_dataset(store,c,frame,'baostock','前复权') for c in ['sh.600000','sz.000001']]
    svc=Service(store)
    calls=[]
    signal=dict(asof=frame.date.iloc[-1],setup_date=frame.date.iloc[-1],entry=20,stop=19,target=22)
    def calc(strategy,data,**kwargs):
        calls.append(1)
        if len(calls)==2: raise RuntimeError('temporary failure')
        return signal
    monkeypatch.setattr('workbench.service.signal_at_end',calc)
    spec=dict(datasets=ids,strategies=['STRATEGY_3K'],timeframe='daily',asof='2024-12-31')
    first=wait_for(store,svc.submit('scan',spec))
    assert first['status']=='partial'
    oid=json.loads(first['result'])['observation_ids'][0]
    second=wait_for(store,svc.submit('scan',spec))
    report=json.loads(second['result'])
    assert second['status']=='completed' and report['reused']==1 and len(calls)==3
    assert oid in report['observation_ids'] and len(store.rows('SELECT * FROM observations'))==2
    svc.pool.shutdown()


def test_scan_enriches_each_stock_once_for_multiple_strategies(store,frame,monkeypatch):
    did = save_dataset(store,'sh.600000',frame,'baostock','前复权')
    from core.calculator import add_indicators as original
    calls = []
    def counted(data):
        calls.append(1)
        return original(data)
    prepared_ids = []
    def calc(strategy,data,**kwargs):
        assert kwargs['prepared'] is not None
        prepared_ids.append(id(kwargs['prepared']))
        return None
    monkeypatch.setattr('workbench.strategies.add_indicators',counted)
    monkeypatch.setattr('workbench.service.signal_at_end',calc)
    svc=Service(store)
    spec=dict(datasets=[did],strategies=['STRATEGY_3K','STRATEGY_STRUCTURAL_GAP'],
              timeframe='daily',asof='2024-12-31')
    row=wait_for(store,svc.submit('scan',spec))
    svc.pool.shutdown()
    assert row['status']=='completed'
    assert len(calls)==1 and len(prepared_ids)==2 and len(set(prepared_ids))==1


def test_shared_indicator_failure_remains_a_partial_scan_receipt(store,frame,monkeypatch):
    did = save_dataset(store,'sh.600000',frame,'baostock','前复权')
    monkeypatch.setattr('workbench.strategies.add_indicators',
                        lambda data: (_ for _ in ()).throw(ValueError('indicator fixture failure')))
    svc=Service(store)
    spec=dict(datasets=[did],strategies=['STRATEGY_3K','STRATEGY_STRUCTURAL_GAP'],
              timeframe='daily',asof='2024-12-31')
    row=wait_for(store,svc.submit('scan',spec))
    svc.pool.shutdown()
    report=json.loads(row['result'])
    assert row['status']=='partial' and len(report['errors'])==2
    assert all('indicator fixture failure' in error['error'] for error in report['errors'])
