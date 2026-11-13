"""Daily maintenance uses real readiness/receipts, isolated synthetic prices."""
import copy
import json
from threading import Event
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars
from tests.test_trading_status_review import market
from workbench.market import save_dataset, load_dataset, weekly_bars
from workbench.history_quality import describe_history, save_quality, quality_for, scan_windows
from workbench.readiness import audit_scope, save_directory
from workbench.status_maintenance import maintain_daily_status, select_targets, ATTEMPT_PREFIX
from workbench.status_review import verify_status_targets
from workbench.store import Store, dumps
from workbench.trading_status import status_facts, save_status_rows


def setup(store, count=1, tail=False):
    full = bars(900)
    codes = [f'sh.{600000+n:06}' for n in range(count)]
    first, day = market(store,full,'tickflow',codes)
    hole = full.date.iloc[-1 if tail else -20]
    for code in codes:
        data = full[full.date != hole]
        did = save_dataset(store,code,data,'tickflow','前复权')
        save_quality(store,did,describe_history(store,code,data,first,day))
    return codes,full,hole,day


def receipt(store, day, *, daily_only=False):
    from workbench.tickflow_integrity import integrity_report
    periods = {'daily':audit_scope(store,['沪深主板'],day,source='tickflow',timeframe='daily',verify_files=True)} if daily_only else None
    integrity = integrity_report(store,['沪深主板'],day,period_audits=periods)
    return dict(source='tickflow',scan_readiness=integrity['scan'],scan_periods=integrity['periods'],
        integrity=integrity,errors=copy.deepcopy(integrity['scan']['gaps']))


def run(store, result, day, job='test', **spec):
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
        (job,'sync','running','2026-11-13',dumps({})))
    stop = Event()
    def check(_job):
        if stop.is_set(): raise InterruptedError('stopped fixture')
    service = SimpleNamespace(store=store,cancel_flags={job:stop},check_stop=check,
                              progress=lambda *args:None)
    return maintain_daily_status(service,job,dict(source='tickflow',boards=['沪深主板'],
        end=day,scan_timeframe='daily',**spec),result)


@pytest.fixture
def vendor(monkeypatch):
    import workbench.status_maintenance as maintenance
    calls=[]
    class Vendor:
        outcome='0'
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def trading_status(self,code,start,end):
            calls.append((code,start,end))
            if self.outcome == 'error': raise TimeoutError('status-only timeout fixture')
            rows=[] if self.outcome=='unknown' else [dict(code=code,date=start,tradestatus=self.outcome,volume='')]
            return pd.DataFrame(rows,columns=['date','code','tradestatus','volume'])
    monkeypatch.setattr(maintenance,'BaoStock',Vendor)
    monkeypatch.setattr(maintenance,'baostock_connection_active',lambda:False)
    # No actual vendor traffic in this fixture; production pacing separately tested.
    original=maintenance.verify_status_targets
    monkeypatch.setattr(maintenance,'verify_status_targets',lambda *a,**k:original(*a,**dict(k,min_interval=0)))
    return Vendor,calls


@pytest.mark.parametrize('tail', [False,True])
def test_halt_resolves_only_affected_gap_without_price_or_ledger_changes(store,vendor,tail):
    codes,full,hole,day=setup(store,tail=tail)
    original=store.rows('SELECT * FROM datasets')
    before={r['id']:(store.root/r['path']).read_bytes() for r in original}
    report=run(store,receipt(store,day),day)
    audit=report['scan_readiness']
    assert not audit['gaps'] and audit['complete']
    assert (audit['ready'],audit['suspended']) == ((0,1) if tail else (1,0))
    assert report['errors']==[] and report['status_maintenance_summary']['halted']==1
    assert report['status_maintenance']['price_downloads']==0
    current=json.loads(store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")[0]['value'])
    assert current['scan']==audit
    assert store.rows('SELECT * FROM datasets')==original
    for row in original:
        assert (store.root/row['path']).read_bytes()==before[row['id']]
    assert not store.rows('SELECT * FROM plans') and not store.rows('SELECT * FROM observations')
    assert status_facts(Store(store.root),codes[0])[0][hole]=='0'


@pytest.mark.parametrize('outcome', ['1','unknown','error'])
def test_traded_unknown_and_network_failure_keep_actual_gap_and_prices(store,vendor,outcome):
    cls,calls=vendor; cls.outcome=outcome
    codes,_,hole,day=setup(store)
    result=run(store,receipt(store,day),day)
    assert result['scan_readiness']['ready']==0 and result['scan_readiness']['gaps']
    assert not status_facts(store,codes[0])[0].get(hole)=='0'
    assert len(calls)==1
    if outcome=='1':
        result=run(store,receipt(store,day),day,job='second')
        assert len(calls)==1  # A trading fact needs prices, not repeated status.


def test_unknown_backoff_survives_restart_but_new_missing_day_is_not_delayed(store,vendor):
    cls,calls=vendor; cls.outcome='unknown'
    codes,full,hole,day=setup(store)
    run(store,receipt(store,day),day)
    fresh=Store(store.root)
    run(fresh,receipt(fresh,day),day,job='second')
    assert len(calls)==1
    later=full.date.iloc[-10]
    data=full[~full.date.isin([hole,later])]
    did=save_dataset(fresh,codes[0],data,'tickflow','前复权')
    save_quality(fresh,did,describe_history(fresh,codes[0],data,full.date.iloc[0],day))
    run(fresh,receipt(fresh,day),day,job='third')
    assert len(calls)==2


def test_one_parent_job_has_one_five_stock_budget_and_next_job_rotates(store,vendor):
    _,calls=vendor
    codes,_,hole,day=setup(store,count=8)
    run(store,receipt(store,day),day,premarket_parent='parent')
    assert len(calls)==5
    run(store,receipt(store,day),day,job='same-parent',premarket_parent='parent')
    assert len(calls)==5
    run(Store(store.root),receipt(store,day),day,job='next-parent',premarket_parent='next')
    assert len(calls)==8 and {c for c,_,_ in calls}==set(codes)


def test_daily_priority_reserves_one_historical_slot_and_caches_normal_facts(store):
    codes,full,hole,day=setup(store,count=7)
    records={r['code']:r for r in store.rows('SELECT * FROM datasets')}
    daily=audit_scope(store,['沪深主板'],day,source='tickflow',timeframe='daily')
    weekly=copy.deepcopy(daily)
    daily['gaps']=daily['gaps'][:6]
    weekly['gaps']=weekly['gaps'][6:]
    selected,_=select_targets(store,dict(daily=daily,weekly=weekly),records,'tickflow',day)
    assert len(selected)==5 and codes[-1] in selected
    save_status_rows(store,codes[0],hole,hole,[dict(code=codes[0],date=hole,tradestatus='1',volume='')])
    selected,_=select_targets(store,dict(daily=daily,weekly=weekly),records,'tickflow',day)
    assert codes[0] not in selected


def test_warm_daily_receipt_still_drains_old_weekly_holes(store,vendor):
    _,calls=vendor
    codes,full,hole,day=setup(store)
    # Move the hole outside latest 300 daily bars, but inside weekly input.
    old=full.date.iloc[100]
    did=save_dataset(store,codes[0],full[full.date != old],'tickflow','前复权')
    save_quality(store,did,describe_history(store,codes[0],full[full.date!=old],full.date.iloc[0],day))
    report=run(store,receipt(store,day,daily_only=True),day)
    assert calls==[(codes[0],old,old)] and report['status_maintenance_summary']['halted']==1


@pytest.mark.parametrize('spec', [dict(scan_timeframe='weekly'),dict(force=True),dict(full_history=True),dict(repair_codes=['sh.600000'])])
def test_explicit_repair_research_and_weekly_tasks_do_not_add_automatic_network(store,vendor,spec):
    _,calls=vendor
    _,_,_,day=setup(store)
    result=receipt(store,day)
    # Use direct invocation so a duplicate scan_timeframe keyword is unambiguous.
    service=SimpleNamespace(store=store)
    original=copy.deepcopy(result)
    args=dict(source='tickflow',boards=['沪深主板'],scan_timeframe='daily')
    args.update(spec)
    out=maintain_daily_status(service,None,args,result)
    assert out==original and calls==[]


def test_existing_connection_and_persistent_cooldown_skip_without_penalizing_job(store,vendor,monkeypatch):
    import workbench.status_maintenance as maintenance
    _,calls=vendor; _,_,_,day=setup(store)
    monkeypatch.setattr(maintenance,'baostock_connection_active',lambda:True)
    run(store,receipt(store,day),day)
    assert not calls and not store.rows('SELECT value FROM meta WHERE key=?',('provider_guard:baostock',))


def test_sorted_windows_and_old_quality_are_rebuilt_without_new_price_dataset(store):
    codes,full,_,day=setup(store)
    shuffled=pd.concat([full.drop(index=range(200,300)),full.iloc[200:300]])
    assert scan_windows(shuffled,day)==scan_windows(full,day)
    did=save_dataset(store,codes[0],full,'tickflow','前复权')
    q=describe_history(store,codes[0],full,full.date.iloc[0],day)
    del q['window_version']; q['windows']['daily']['end']='2000-01-01'
    save_quality(store,did,q)
    record=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    with pytest.raises(ValueError,match='待核验'):
        quality_for(store,record,day,cache_only=True)
    rebuilt=quality_for(store,record,day)
    assert rebuilt['window_version']==1 and rebuilt['windows']['daily']['end']==day
    assert record==store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]


def test_service_sync_entry_runs_the_maintenance_and_persists_real_receipt(store,vendor,monkeypatch):
    from workbench.service import Service
    _,_,_,day=setup(store)
    monkeypatch.setattr(Service,'_sync_once',lambda self,job,spec:receipt(self.store,day))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],
            start='2018-01-01',end=day,scan_timeframe='daily')))
        assert row['status']=='completed',row['message']
        assert json.loads(row['result'])['status_maintenance_summary']['halted']==1
    finally:
        service.pool.shutdown()


def test_real_hung_status_subprocess_is_killed_without_waiting_normal_query_timeout(store,monkeypatch):
    from tests.test_provider_timeout import fake_worker
    from workbench.provider_process import BaoStock
    monkeypatch.setenv('LOCALAPPDATA',str(store.root))
    monkeypatch.setattr('workbench.provider_process.baostock_connection_active',lambda:False)
    processes=fake_worker(monkeypatch,'trading_status')
    report=verify_status_targets(store,{'sh.600000':['2026-11-12']},BaoStock,
        request_timeout=.5,budget_seconds=2)
    assert len(report['errors'])==1 and report['remaining']==['sh.600000']
    assert all(p.poll() is not None for p in processes)


def test_whole_batch_budget_preserves_first_fact_and_defers_rest(store,monkeypatch):
    import workbench.status_review as review
    clock=[0.]
    calls=[]
    monkeypatch.setattr(review.time,'monotonic',lambda:clock[0])
    class Slow:
        def __enter__(self):return self
        def __exit__(self,*args):calls.append('closed')
        def trading_status(self,code,a,b):
            calls.append(code); clock[0]+=2
            return pd.DataFrame([dict(code=code,date=a,tradestatus='0',volume='')])
    report=verify_status_targets(store,{c:['2026-11-12'] for c in ['sh.600000','sh.600001']},
        Slow,budget_seconds=1,request_timeout=1)
    assert report['budget_exhausted'] and report['remaining']==['sh.600001']
    assert calls==['sh.600000','closed'] and len(report['confirmed_halt'])==1
    assert status_facts(Store(store.root),'sh.600000')[0]=={'2026-11-12':'0'}


def test_cancel_after_first_response_preserves_fact_and_closes_connection(store):
    stop=Event();calls=[]
    class Vendor:
        def __enter__(self):return self
        def __exit__(self,*args):calls.append('closed')
        def trading_status(self,code,a,b):
            calls.append(code);stop.set()
            return pd.DataFrame([dict(code=code,date=a,tradestatus='0',volume='')])
    def check():
        if stop.is_set():raise InterruptedError('cancelled fixture')
    with pytest.raises(InterruptedError):
        verify_status_targets(store,{c:['2026-11-12'] for c in ['sh.600000','sh.600001']},
            Vendor,check_stop=check,cancel_event=stop)
    assert calls==['sh.600000','closed']
    assert status_facts(Store(store.root),'sh.600000')[0]['2026-11-12']=='0'


def test_real_incremental_repair_quality_matches_saved_sorted_snapshot(store):
    from workbench.sync import sync_stock
    code='sh.600000';full=bars();first,day=market(store,full,'tickflow')
    hole=full.date.iloc[200];old=full[full.date!=hole]
    class Vendor:
        def fetch(self,code,a,b):return full[full.date.between(a,b)].copy()
    class Original:
        def fetch(self,*args):return old.copy()
    sync_stock(store,lambda:Original(),code,first,day,source='tickflow')
    did,_=sync_stock(store,lambda:Vendor(),code,first,day,source='tickflow',repair_timeframe='daily')
    saved,_=load_dataset(store,did)
    q=json.loads(store.rows('SELECT value FROM meta WHERE key=?',('market_quality:'+did,))[0]['value'])
    assert q['windows']==scan_windows(saved,day) and q['windows']['daily']['end']==day
    assert not q['missing_dates'] and saved.date.is_monotonic_increasing


def test_subset_gate_rejects_out_of_scope_stock(store):
    _,_,_,day=setup(store)
    audit=audit_scope(store,['沪深主板'],day,source='tickflow',timeframe='daily',codes=['sz.000001'])
    assert not audit['scope_valid'] and not audit['scan_allowed']


def test_daily_maintenance_refreshes_only_selected_price_files(store,vendor,monkeypatch):
    import workbench.market as market_module
    _,_,_,day=setup(store,count=8)
    initial=receipt(store,day)
    checked=[];original=market_module.verify_dataset
    def verify(*args,**kwargs):
        checked.append(args[1]);return original(*args,**kwargs)
    monkeypatch.setattr(market_module,'verify_dataset',verify)
    report=run(store,initial,day)
    assert len(set(checked))==5 and len(checked)==10
    assert report['scan_readiness']['ready']==5 and len(report['scan_readiness']['gaps'])==3


def test_existing_unknown_can_be_retried_after_backoff(store,vendor):
    cls,calls=vendor;cls.outcome='unknown'
    codes,_,_,day=setup(store)
    run(store,receipt(store,day),day)
    key=ATTEMPT_PREFIX+codes[0]
    state=json.loads(store.rows('SELECT value FROM meta WHERE key=?',(key,))[0]['value'])
    state['next_check']=0
    store.execute('UPDATE meta SET value=? WHERE key=?',(dumps(state),key))
    run(Store(store.root),receipt(store,day),day,job='again')
    assert len(calls)==2
