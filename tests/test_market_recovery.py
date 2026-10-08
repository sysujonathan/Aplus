import json
import threading
from datetime import datetime,timedelta

import pandas as pd
import pytest

from tests.test_workbench import store,frame,wait_for
from workbench.store import Store
from workbench.sources import market_source,set_market_source,coverage_state
from workbench.provider_guard import ProviderError,ProviderGuard,ConnectionLease
from workbench.market import save_dataset,load_dataset
from workbench.sync import sync_stock
from workbench.service import Service
from workbench.readiness import extend_exchange_calendar,audit_scope,save_calendar
from workbench.tickflow import decode_bars,symbol_of,TickFlow
from workbench.holding_quotes import CHINA,select_quotes,fetch_quotes
from gui.data import latest_market_dataset,candidate_source_for_date,latest_market_date
from workbench.suspensions import announcement_evidence
from workbench.readiness import check_response_dates


def test_cooldown_survives_restart_and_does_not_block_other_source(store):
    clock=[1000.]
    guard=ProviderGuard(store,'baostock',lambda:clock[0])
    error=ProviderError('baostock','login','restricted','10001011',.1)
    state=guard.failure(error)
    assert state['until']==22600
    with pytest.raises(ValueError,match='暂停请求'):
        ProviderGuard(Store(store.root),'baostock',lambda:1001).check()
    ProviderGuard(store,'tickflow',lambda:1001).check()
    assert state['last_error']['error_code']=='10001011'
    clock[0]=22601
    guard.check()


def test_network_failure_window_does_not_need_consecutive_stocks(store):
    guard=ProviderGuard(store,'baostock',lambda:1000)
    for _ in range(3):
        state=guard.failure(TimeoutError('network'))
    assert len(state['failures'])==3 and state['until']==1120


def test_os_connection_lease_is_exclusive_and_released(tmp_path):
    first=ConnectionLease(root=tmp_path).acquire()
    with pytest.raises(ProviderError,match='占用'):
        ConnectionLease(root=tmp_path).acquire()
    first.release()
    second=ConnectionLease(root=tmp_path).acquire()
    second.release()


def test_three_network_failures_then_new_job_is_blocked(store,monkeypatch):
    calls=[]
    class Broken:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fetch(self,*args):
            calls.append(args)
            raise ProviderError('baostock','fetch','网络接收错误','10002007')
    monkeypatch.setattr('workbench.service.BaoStock',Broken)
    service=Service(store)
    try:
        spec={'codes':['sh.600000','sh.600001','sh.600002','sh.600003'],'start':'2020-01-01','end':'2020-01-31'}
        row=wait_for(store,service.submit('sync',spec))
        result=json.loads(row['result'])
        assert len(calls)==3 and result['connections']==1
        assert result['errors'][0]['error_code']=='10002007'
        with pytest.raises(ValueError,match='暂停请求'):
            service.submit('sync',spec)
        assert len(calls)==3
    finally: service.pool.shutdown()


def test_preflight_login_restriction_is_durable_and_diagnosed(store,monkeypatch):
    class Broken:
        def __enter__(self): raise ProviderError('baostock','login','黑名单用户','10001011',.12)
        def __exit__(self,*args): pass
    monkeypatch.setattr('workbench.service.BaoStock',Broken)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(boards=['沪深主板'],start='2020-01-01',end='2020-01-31')))
        result=json.loads(row['result'])
        assert row['status']=='failed'
        assert result['errors'][0]['operation']=='login'
        assert result['errors'][0]['error_code']=='10001011'
        with pytest.raises(ValueError,match='暂停请求'): ProviderGuard(Store(store.root),'baostock').check()
    finally: service.pool.shutdown()


def test_tickflow_decoder_china_time_shares_and_invalid_columns():
    stamp=int(datetime(2026,9,30,tzinfo=CHINA).timestamp()*1000)
    raw=dict(timestamp=[stamp],open=[5],high=[6],low=[4],close=[5.5],volume=[20])
    decoded=decode_bars('sz.002259',raw,'2026-09-01','2026-09-30')
    assert decoded.date.tolist()==['2026-09-30'] and decoded.volume.tolist()==[2000]
    assert decoded.attrs['adjustment']=='前复权'
    assert symbol_of('000906')=='000906.SZ'
    with pytest.raises(ValueError,match='长度'):
        decode_bars('sz.002259',{**raw,'close':[]},'2026-09-01','2026-09-30')


def test_source_switch_full_history_and_separate_coverage(store,frame):
    class P:
        calls=[]
        def fetch(self,code,start,end):
            self.calls.append((start,end))
            return frame[frame.date.between(start,end)].copy()
    p=P(); start,end=frame.date.iloc[0],frame.date.iloc[-1]
    old,_=sync_stock(store,lambda:p,'sh.600000',start,end)
    set_market_source(store,'tickflow')
    new,action=sync_stock(store,lambda:p,'sh.600000',start,end,source='tickflow')
    assert action=='downloaded' and old!=new and len(p.calls)==2
    assert coverage_state(store,'sh.600000')[0]['dataset_id']==old
    assert coverage_state(store,'sh.600000','tickflow')[0]['dataset_id']==new
    assert load_dataset(store,old)[1]['source']=='baostock'
    assert latest_market_dataset(store,'sh.600000')['id']==new
    assert latest_market_date(store)==end
    assert market_source(Store(store.root))=='tickflow'


def test_published_calendar_extends_existing_without_holiday_zero(store):
    save_calendar(store,pd.DataFrame({'calendar_date':['2026-09-30'],'is_trading_day':['1']}),'2026-09-30','2026-09-30')
    result=extend_exchange_calendar(store,'2026-09-30','2026-10-09')
    assert result['trading_days']==['2026-09-30','2026-10-08','2026-10-09']
    with pytest.raises(ValueError,match='交易日历'):
        extend_exchange_calendar(store,'2016-01-01','2026-10-09')


def test_unknown_historical_calendar_is_not_invented(store):
    with pytest.raises(ValueError,match='历史交易日历'):
        extend_exchange_calendar(store,'2016-01-01','2026-10-09')
    assert not (store.root/'trading_calendar.json').exists()


def test_tickflow_batch_sync_never_calls_baostock(store,monkeypatch):
    bars=pd.DataFrame(dict(date=['2026-09-29','2026-09-30'],open=[5,5],high=[6,6],low=[4,4],close=[5,5],volume=[100,100]))
    save_dataset(store,'sh.600000',bars,'baostock','前复权')
    class Fake:
        batches=[]
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def preload(self,codes,start,end): self.batches.append((codes,start,end))
        def fetch(self,code,start,end): return bars[bars.date.between(start,end)].copy()
    monkeypatch.setattr('workbench.tickflow.TickFlow',Fake)
    monkeypatch.setattr('workbench.service.BaoStock',lambda:pytest.fail('must not connect to BaoStock'))
    set_market_source(store,'tickflow')
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(codes=['sh.600000','sz.000001'],start='2026-09-29',end='2026-09-30')))
        assert row['status']=='completed',row['message']
        assert len(Fake.batches)==1 and len(Fake.batches[0][0])==2
        second=wait_for(store,service.submit('sync',dict(codes=['sh.600000','sz.000001'],start='2026-09-29',end='2026-09-30')))
        assert json.loads(second['result'])['skipped']==2 and len(Fake.batches)==1
    finally: service.pool.shutdown()


@pytest.mark.parametrize('now',['2026-10-09T00:09:00','2026-10-10T10:00:00','2026-10-09T08:00:00'])
def test_completed_quote_survives_midnight_and_weekend(now):
    live={'sz.000906':dict(price=6.07,previous_close=6.11,date='2026-10-08',quote_time='2026-10-08T16:30:00+08:00',source='sina')}
    history={'sz.000906':dict(price=6.11,date='2026-09-30',source='history')}
    chosen=select_quotes(history,live,['sz.000906'],set(),datetime.fromisoformat(now).replace(tzinfo=CHINA),None)
    assert chosen['sz.000906']['price']==6.07 and chosen['sz.000906']['source']=='sina'


def test_older_quote_never_replaces_newer_saved_close():
    live={'sz.000906':dict(price=6.07,date='2026-10-08',quote_time='2026-10-08T16:30:00+08:00',source='sina')}
    history={'sz.000906':dict(price=6.2,date='2026-10-09',source='history')}
    chosen=select_quotes(history,live,['sz.000906'],set(),datetime(2026,10,10,10,tzinfo=CHINA),None)
    assert chosen['sz.000906']['price']==6.2


def test_public_halt_proof_has_exclusive_resume_and_no_unknown_stock():
    days,proof=announcement_evidence('sz.002259','2025-05-19','2025-05-20')
    assert days==['2025-05-19'] and proof[0]['resume']=='2025-05-20'
    assert all(url.startswith('https://') for url in proof[0]['urls'])
    assert announcement_evidence('sh.600000','2016-01-01','2026-10-09')==([],[])


def test_proven_halt_is_excused_but_another_missing_day_is_not(store):
    save_calendar(store,pd.DataFrame({'calendar_date':pd.date_range('2025-05-16','2025-05-20'),
                                    'is_trading_day':['1','0','0','1','1']}),'2025-05-16','2025-05-20')
    stamps=[int(datetime(2025,5,d,tzinfo=CHINA).timestamp()*1000) for d in [16,20]]
    raw=dict(timestamp=stamps,open=[5,5],high=[6,6],low=[4,4],close=[5,5],volume=[20,20])
    bars=decode_bars('sz.002259',raw,'2025-05-16','2025-05-20')
    check_response_dates(store,bars,'2025-05-16','2025-05-20')
    assert len(bars)==2 and bars.attrs['suspension_evidence']
    bars=bars.iloc[:1].copy()
    with pytest.raises(ValueError,match='2025-05-20'):
        check_response_dates(store,bars,'2025-05-16','2025-05-20')


def test_bar_during_proven_full_day_halt_is_not_silently_removed():
    stamp=int(datetime(2025,5,19,tzinfo=CHINA).timestamp()*1000)
    raw=dict(timestamp=[stamp],open=[5],high=[6],low=[4],close=[5],volume=[20])
    with pytest.raises(ValueError,match='公告冲突'):
        decode_bars('sz.002259',raw,'2025-05-19','2025-05-19')


def test_tickflow_unknown_listing_is_retained_not_silently_excluded(monkeypatch):
    provider=TickFlow()
    def query(operation,args):
        return [dict(symbol='600849.SH',name='unknown',ext={})] if args==['SH'] else []
    monkeypatch.setattr(provider,'_query',query)
    directory=provider.universe('2026-10-08')
    assert directory.code.tolist()==['sh.600849']
    assert directory.iloc[0].ipoDate=='' and directory.iloc[0].tradeStatus=='unknown'


def test_single_stock_refresh_preserves_rest_of_batch(monkeypatch):
    provider=TickFlow(); calls=[]
    stamp=int(datetime(2026,9,30,tzinfo=CHINA).timestamp()*1000)
    raw=dict(timestamp=[stamp],open=[5],high=[6],low=[4],close=[5],volume=[20])
    def query(operation,args):
        calls.append(args)
        return {symbol_of(code):raw for code in args[0]}
    monkeypatch.setattr(provider,'_query',query)
    provider.preload(['sh.600000','sz.000001'],'2026-09-30','2026-09-30')
    provider.fetch('sh.600000','2026-09-01','2026-09-30')
    provider.fetch('sz.000001','2026-09-30','2026-09-30')
    assert len(calls)==2 and len(provider.cache)==2
