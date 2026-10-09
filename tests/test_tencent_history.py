import json
import threading
from unittest.mock import Mock

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars, scope
from workbench.tencent import Tencent, TencentHTTP, decode_history
from workbench.market import save_dataset, load_dataset
from workbench.sources import source_file, coverage_state, set_market_source, market_source
from workbench.readiness import save_directory, audit_scope
from workbench.history_quality import require_research_history
from workbench.sync import sync_stock
from workbench.service import Service
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.tickflow_integrity import integrity_report
from gui.data import latest_scan_date, candidate_source_for_date, latest_market_dataset


def response(code, frame, field='day'):
    symbol=code.replace('.','')
    rows=[[r.date,str(r.open),str(r.close),str(r.high),str(r.low),str(r.volume)]
          for r in frame.itertuples()]
    return dict(code=0,data={symbol:{field:rows}})


@pytest.mark.parametrize('code,volume',[('sh.600000',100000),('sz.000906',100000),
    ('sh.688981',1000),('bj.920002',100000)])
def test_raw_history_identity_and_board_volume_units(code,volume):
    data=bars(10); end=data.date.iloc[-1]
    actual=decode_history(code,response(code,data),data.date.iloc[0],end)
    assert actual.volume.iloc[0]==volume
    assert actual.attrs['adjustment']=='不复权'
    with pytest.raises(ValueError):decode_history(code,response('sh.600001',data),'2016-01-01',end)
    with pytest.raises(ValueError):decode_history(code,response(code,data,'qfqday'),'2016-01-01',end)
    with pytest.raises(ValueError):decode_history(code,response(code,data),'2016-01-01',data.date.iloc[-2])


def test_backward_pagination_has_no_duplicate_or_infinite_loop(monkeypatch):
    data=bars(1000); provider=Tencent(); requests=[]
    def query(op,args):
        requests.append((op,args))
        visible=data[data.date<=args[1]].iloc[-640:]
        return response(args[0],visible)
    monkeypatch.setattr(provider,'_query',query)
    fetched=provider.fetch('sh.600000',data.date.iloc[0],data.date.iloc[-1])
    assert len(fetched)==1000 and len(requests)==2 and fetched.date.is_unique
    assert requests[1][1][1]<data.date.iloc[-640]
    monkeypatch.setattr(provider,'_query',lambda *a:response('sh.600000',data.iloc[-640:]))
    with pytest.raises(ValueError,match='结束日|分页'):provider.fetch('sh.600000',data.date.iloc[0],data.date.iloc[-1])


def test_catalog_does_not_use_prices_or_infer_trade_status(monkeypatch):
    http=TencentHTTP()
    monkeypatch.setattr(http,'request',lambda *a:[dict(symbol='sz000906',code='000906',name='公开名称',
        trade='999999',volume='999999',open='999999')])
    rows=http.directory_page(1)
    assert rows==[dict(code='sz.000906',code_name='公开名称',tradeStatus='unknown')]
    monkeypatch.setattr(http,'request',lambda *a:[dict(symbol='sz302132',code='302132',name='新前缀')])
    assert http.directory_page(1)==[dict(code='sz.302132',code_name='新前缀',tradeStatus='unknown')]
    monkeypatch.setattr(http,'request',lambda *a:[dict(symbol='sz302132',code='000906')])
    with pytest.raises(ValueError,match='身份不一致'):http.directory_page(1)
    provider=Tencent(); responses=iter([101,[dict(code=f'sh.{600000+i}') for i in range(100)],
                                       [dict(code='sh.600100')],101])
    monkeypatch.setattr(provider,'_query',lambda *a:next(responses))
    assert len(provider.universe('2026-10-08'))==101
    responses=iter([101,[dict(code=f'sh.{600000+i}') for i in range(100)],[],101])
    with pytest.raises(ValueError,match='分页数量'):provider.universe('2026-10-08')


def test_three_sources_and_cooldown_are_independent(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    for source in ('tickflow','tencent'):
        store.write_artifact(f'sources/{source}/trading_calendar.json',
            (store.root/'trading_calendar.json').read_bytes())
        save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['unknown'])),last,source=source)
    originals={name:source_file(store,'universe.csv',name).read_bytes() for name in ('baostock','tickflow')}
    b=save_dataset(store,'sh.600000',data,'baostock','前复权')
    t=save_dataset(store,'sh.600000',data,'tickflow','前复权')
    calls=[]
    class Provider:
        def fetch(self,*a):calls.append(a); return data.copy()
    did,action=sync_stock(store,Provider,'sh.600000',first,last,source='tencent')
    assert len(calls)==1 and action=='downloaded' and did not in (b,t)
    assert load_dataset(store,did)[1]['adjustment']=='不复权'
    assert coverage_state(store,'sh.600000')==[] and coverage_state(store,'sh.600000','tickflow')==[]
    assert coverage_state(store,'sh.600000','tencent')[0]['dataset_id']==did
    assert audit_scope(store,['沪深主板'],last,source='tencent',timeframe='daily',verify_files=True)['complete']
    receipt=integrity_report(store,['沪深主板'],last,source='tencent')
    assert receipt['source']=='tencent' and receipt['periods']['daily']['ready']==1
    ProviderGuard(store,'baostock').failure(ProviderError('baostock','fetch','blacklist','10001011'))
    ProviderGuard(store,'tencent').check()
    for source in ('tencent','tickflow','baostock','tencent'):
        set_market_source(store,source)
        assert market_source(store)==source
    assert latest_market_dataset(store,'sh.600000')['id']==did
    from workbench.h2_replay_service import available_snapshots
    assert [r['id'] for r in available_snapshots(store)[0]]==[did]
    assert candidate_source_for_date(store)=='tencent' and latest_scan_date(store) is None
    assert all(source_file(store,'universe.csv',s).read_bytes()==value for s,value in originals.items())
    assert sync_stock(store,Provider,'sh.600000',first,last,source='tencent')[1]=='skipped' and len(calls)==1


def test_tencent_recent_gap_blocks_and_history_start_is_not_certified(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['unknown'])),last,source='tencent')
    class Provider:
        def fetch(self,*a):return data.drop(index=200)
    did,_=sync_stock(store,Provider,'sh.600000',first,last,source='tencent')
    assert not audit_scope(store,['沪深主板'],last,source='tencent',timeframe='daily')['scan_allowed']
    with pytest.raises(ValueError,match='历史研究输入'):require_research_history(store,load_dataset(store,did)[1],last)
    with pytest.raises(ValueError,match='历史起点|交易日历'):require_research_history(store,load_dataset(store,did)[1],last,'2016-01-01')


def test_holiday_at_research_start_is_not_a_missing_bar(store):
    data=bars(); data['date']=pd.bdate_range('2024-01-02',periods=len(data)).strftime('%Y-%m-%d')
    first,last=scope(store,data,['sh.600000'])
    did=save_dataset(store,'sh.600000',data,'tencent','不复权')
    calendar=json.loads((store.root/'trading_calendar.json').read_text())
    calendar['start']='2024-01-01'
    store.write_artifact('sources/tencent/trading_calendar.json',json.dumps(calendar).encode())
    require_research_history(store,load_dataset(store,did)[1],last,'2024-01-01')


def test_tencent_sync_then_scan_and_mixed_backtest_rejection(store,monkeypatch):
    data=bars(700); first,last=scope(store,data,['sh.600000'])
    service=Service(store); calls=[]
    class Provider:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def universe(self,*a):return pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['unknown']))
        def fetch(self,*a):calls.append(a);return data.copy()
    monkeypatch.setattr('workbench.tencent_service.Tencent',Provider)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *a,**kw:None)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tencent',boards=['沪深主板'],start=first,end=last)))
        result=json.loads(row['result']); assert row['status']=='completed',row['message']
        did=result['datasets'][0]
        scan=wait_for(store,service.submit('scan',dict(source='tencent',boards=['沪深主板'],datasets=[did],
            strategies=['STRATEGY_STRUCTURAL_GAP'],timeframes=['daily','weekly'],asof=last)))
        report=json.loads(scan['result'])
        assert scan['status']=='completed' and report['success']==2
        assert set(report['coverage_by_timeframe'])=={'daily','weekly'}
        other=save_dataset(store,'sh.600000',data,'tickflow','前复权')
        bt=wait_for(store,service.submit('backtest',dict(datasets=[did,other],source='tencent')))
        assert bt['status']=='failed' and '一个行情来源' in bt['message']
        assert store.rows("SELECT value FROM meta WHERE key='tencent_integrity'")
        assert not store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")
        monkeypatch.setattr(Provider,'universe',lambda *a:pytest.fail('同日已核验目录不应重新请求'))
        row=wait_for(store,service.submit('sync',dict(source='tencent',boards=['沪深主板'],start=first,end=last)))
        assert json.loads(row['result'])['catalog_reused'] and row['status']=='completed'
    finally:service.pool.shutdown()


def test_tencent_cache_hits_do_not_reset_network_failure_budget(store,monkeypatch):
    data=bars(); codes=[f'sh.{600000+i}' for i in range(7)]
    first,last=scope(store,data,codes)
    for code in codes[1:5:2]:
        sync_stock(store,lambda:Mock(fetch=Mock(return_value=data.copy())),code,first,last,source='tencent')
    calls=[]
    class Provider:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def universe(self,*a):return pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*len(codes)))
        def fetch(self,code,*a):
            calls.append(code)
            raise ProviderError('tencent','history_page','simulated timeout')
    monkeypatch.setattr('workbench.tencent_service.Tencent',Provider)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tencent',boards=['沪深主板'],start=first,end=last)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and result['skipped']==2
        assert calls==[codes[0],codes[2],codes[4]]
        assert result['network_failures']==3 and result['processed']==5 and result['remaining']==2
        assert result['cooldown']
    finally:service.pool.shutdown()


@pytest.mark.parametrize('source',['tickflow','tencent'])
def test_current_day_week_bootstrap_is_not_ten_year_history(store,monkeypatch,source):
    from gui.toolbar import sync_start_date
    set_market_source(store,source)
    monkeypatch.setattr('gui.toolbar._two_years_ago',lambda:'2024-10-08')
    assert sync_start_date(store)=='2024-10-08'
    assert sync_start_date(store,timeframe='weekly')=='2019-01-01'
    assert sync_start_date(store,True,'weekly')=='2016-01-01'


def test_exact_day_public_catalog_bootstrap_never_borrows_prices(store):
    from workbench.tencent_service import local_public_catalog
    data=bars(); first,last=scope(store,data,['sh.600000'])
    path=source_file(store,'universe.csv','baostock'); original=path.read_bytes()
    directory,origin=local_public_catalog(store,last)
    assert origin=='baostock' and list(directory.columns)==['code','tradeStatus']
    assert directory.tradeStatus.eq('unknown').all()
    assert local_public_catalog(store,'2026-10-08')==(None,None)
    assert path.read_bytes()==original and not coverage_state(store,'sh.600000','tencent')


def test_returning_to_baostock_does_not_display_tencent_candidates(store):
    data=bars(); did=save_dataset(store,'sh.600000',data,'tencent','不复权')
    stamp=data.date.iloc[-1]
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        ('tencent-observation','tencent-test-job','sh.600000','STRATEGY_GAP_H2','1','daily',stamp,stamp,did,'{}',stamp))
    set_market_source(store,'tencent')
    assert latest_scan_date(store)==stamp
    set_market_source(store,'baostock')
    assert latest_scan_date(store) is None and candidate_source_for_date(store)=='baostock'


@pytest.mark.parametrize('failures,expected,limited,total',[
    ({'sh.600000':None},5,False,5),
    ({'sh.600000':None,'sh.600001':None,'sh.600002':None},3,True,5),
    ({'sh.600000':'429'},1,True,5),
    ({f'sh.{600000+i}':None for i in range(0,19,2)},19,True,21),
])
def test_tencent_isolated_failure_continues_but_outage_and_limits_stop(store,monkeypatch,failures,expected,limited,total):
    data=bars(); codes=[f'sh.{600000+i}' for i in range(total)]
    first,last=scope(store,data,codes); calls=[]
    class Provider:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def universe(self,*a):return pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*len(codes)))
        def fetch(self,code,*a):
            calls.append(code)
            if code in failures:
                raise ProviderError('tencent','history_page','simulated timeout',failures[code])
            return data.copy()
    monkeypatch.setattr('workbench.tencent_service.Tencent',Provider)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tencent',boards=['沪深主板'],start=first,end=last)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and len(calls)==expected and len(set(calls))==expected
        assert bool(result.get('cooldown'))==limited
        assert result['remaining']==len(codes)-expected
        assert result['success']==sum(c not in failures for c in calls)
        assert not ProviderGuard(store,'baostock').state()
        if not limited:
            calls.clear(); failures.clear()
            retry=wait_for(store,service.submit('sync',dict(source='tencent',boards=['沪深主板'],start=first,end=last)))
            assert retry['status']=='completed' and calls==['sh.600000']
    finally:service.pool.shutdown()
