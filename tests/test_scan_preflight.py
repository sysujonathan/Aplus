import builtins
import json

import pandas as pd
import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_tickflow_scan_readiness import bars, scope, snapshot
from workbench.history_quality import describe_history, quality_for, save_cached_quality
from workbench.market import save_dataset
from workbench.readiness import audit_scope
from workbench.service import Service
from workbench.store import Store
from gui.toolbar import format_header_data_status


def test_history_minimum_is_not_recomputed_for_each_calendar_day(monkeypatch):
    data=bars(2200).drop(index=[10,2100])
    days=pd.bdate_range('1990-01-01','2030-01-01').strftime('%Y-%m-%d').tolist()
    cal=dict(start='1990-01-01',end='2030-01-01',trading_days=days)
    calls=[]
    def counted(values):
        calls.append(1)
        return builtins.min(values)
    monkeypatch.setattr('workbench.history_quality.min',counted,raising=False)
    q=describe_history(None,'sh.600001',data,data.date.iloc[0],data.date.iloc[-1],cal)
    assert len(calls)==1
    assert len(q['missing_dates'])==2 and q['windows']['daily']['rows']==300


def test_preflight_can_stop_before_loading_next_stock(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    ids=[snapshot(store,c,data,first,last) for c in ['sh.600000','sh.600001']]
    for did in ids:
        store.execute('DELETE FROM meta WHERE key=?',('market_quality:'+did,))
    import workbench.history_quality as h
    original=h.load_dataset; calls=[]; svc=Service(store)
    def stop_after_one(*a,**kw):
        calls.append(a[1]); result=original(*a,**kw)
        for flag in svc.cancel_flags.values(): flag.set()
        return result
    monkeypatch.setattr(h,'load_dataset',stop_after_one)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *a,**k:pytest.fail('calculated after stop'))
    try:
        row=wait_for(store,svc.submit('scan',dict(source='tickflow',boards=['沪深主板'],datasets=ids,
            strategies=['STRATEGY_3K'],timeframes=['daily'],asof=last)))
        assert row['status']=='cancelled' and len(calls)==1
        assert row['total']==2 and '任务已停止' in row['message']
    finally:
        svc.pool.shutdown()


def test_scope_progress_and_cancellation_are_not_coverage_errors(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    snapshot(store,'sh.600000',data,first,last)
    updates=[]
    result=audit_scope(store,['沪深主板'],last,source='tickflow',timeframe='daily',
        progress=lambda *args:updates.append(args))
    assert result['complete'] and updates[0][0]==0 and updates[-1][:2]==(1,1)
    def stopped(): raise InterruptedError('stop')
    with pytest.raises(InterruptedError):
        audit_scope(store,['沪深主板'],last,source='tickflow',check_stop=stopped)


def test_multiple_periods_audit_once_each_and_persist_checks(store,monkeypatch):
    data=bars(900); first,last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data,first,last)
    store.execute('DELETE FROM meta WHERE key=?',('market_quality:'+did,))
    import workbench.service as module
    original=module.audit_scope; calls=[]
    def audited(*a,**kw):
        calls.append(kw['timeframe']);return original(*a,**kw)
    monkeypatch.setattr(module,'audit_scope',audited)
    monkeypatch.setattr(module,'signal_at_end',lambda *a,**k:None)
    svc=Service(store)
    try:
        row=wait_for(store,svc.submit('scan',dict(source='tickflow',boards=['沪深主板'],datasets=[did],
            strategies=['STRATEGY_3K','STRATEGY_STRUCTURAL_GAP'],timeframes=['daily','weekly'],asof=last)))
        assert row['status']=='completed' and calls==['daily','weekly'], row['message']
        fresh=Store(store.root)
        monkeypatch.setattr('workbench.history_quality.load_dataset',lambda *a:pytest.fail('reparsed after restart'))
        assert audit_scope(fresh,['沪深主板'],last,source='tickflow',timeframe='daily',history_cache_only=True)['ready']==1
    finally:
        svc.pool.shutdown()


def test_historical_date_check_does_not_replace_current_certificate(store):
    data=bars();first,last=scope(store,data,['sh.600000']);did=snapshot(store,'sh.600000',data,first,last)
    before=store.rows('SELECT value FROM meta WHERE key=?',('market_quality:'+did,))[0]['value']
    record=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    earlier=data.date.iloc[-2]
    quality_for(store,record,earlier)
    assert save_cached_quality(store,[did],earlier)==0
    assert before==store.rows('SELECT value FROM meta WHERE key=?',('market_quality:'+did,))[0]['value']


def test_stop_between_strategies_commits_first_judgment(store,frame,monkeypatch):
    did=save_dataset(store,'sh.600000',frame,'baostock','前复权');svc=Service(store);calls=[]
    def calc(*a,**kw):
        calls.append(1)
        for flag in svc.cancel_flags.values():flag.set()
        return None
    monkeypatch.setattr('workbench.service.signal_at_end',calc)
    try:
        row=wait_for(store,svc.submit('scan',dict(datasets=[did],strategies=['STRATEGY_3K','STRATEGY_STRUCTURAL_GAP'],
            timeframe='daily',asof=frame.date.iloc[-1])))
        assert row['status']=='cancelled' and len(calls)==1
        assert json.loads(row['result'])['success']==1 and len(store.rows('SELECT * FROM scan_cache'))==1
    finally:
        svc.pool.shutdown()


def test_pending_quality_is_not_labelled_a_price_gap():
    text=format_header_data_status('2026-10-08','2026-09-30',['沪深主板'],dict(source='tickflow',
        expected=3,ready=0,gaps=[dict(category='quality_pending'),dict(category='missing_dataset')]))
    assert '待核验 1' in text and '排除 1' in text
