import json
import os

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars, scope, snapshot
from workbench.readiness import save_directory, audit_scope, extend_exchange_calendar
from workbench.sources import source_file, directory_file, directory_date, coverage_state, set_market_source
from workbench.tickflow_integrity import integrity_report
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.service import Service
from workbench.market import load_dataset
from workbench.suspensions import announcement_evidence
from workbench.history_quality import quality_for


def test_source_directories_and_calendars_are_independent_and_legacy_only_read(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['1'])),last)
    original=(store.root/'universe.csv').read_bytes()
    calendar=(store.root/'trading_calendar.json').read_bytes()
    save_directory(store,pd.DataFrame(dict(code=['sz.000001'],tradeStatus=['unknown'])),last,source='tickflow')
    extend_exchange_calendar(store,first,last,source='tickflow')
    assert (store.root/'universe.csv').read_bytes()==original
    assert (store.root/'trading_calendar.json').read_bytes()==calendar
    assert directory_file(store,'tickflow')==source_file(store,'universe.csv','tickflow')
    assert directory_date(store,'tickflow')==last
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['1'])),last)
    assert 'sz.000001' in directory_file(store,'tickflow').read_text(encoding='utf-8-sig')
    assert 'sh.600000' in directory_file(store,'baostock').read_text(encoding='utf-8-sig')
    store.execute("UPDATE meta SET value='2000-01-01' WHERE key='universe_date'")
    assert directory_date(store,'tickflow')==last


def test_baostock_directory_is_never_used_as_tickflow_directory(store):
    data=bars(); _,last=scope(store,data,['sh.600000'])
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['1'])),last)
    assert not audit_scope(store,['沪深主板'],last,source='tickflow',timeframe='daily')['scope_valid']


def test_switch_preserves_legacy_tickflow_inventory_before_original_source_updates(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    calendar=(store.root/'trading_calendar.json').read_bytes()
    set_market_source(store,'baostock')
    own=directory_file(store,'tickflow')
    assert own==source_file(store,'universe.csv','tickflow') and directory_date(store,'tickflow')==last
    save_directory(store,pd.DataFrame(dict(code=['sh.600000','sz.000001'],tradeStatus=['1','1'])),last)
    assert 'sz.000001' not in own.read_text(encoding='utf-8-sig')
    assert source_file(store,'trading_calendar.json','tickflow').read_bytes()==calendar


def test_integrity_classifies_historical_holes_without_turning_them_into_halts(store):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001','sh.600002'])
    snapshot(store,'sh.600000',data.drop(index=10),first,last)
    snapshot(store,'sh.600001',data.drop(index=200),first,last)
    report=integrity_report(store,['沪深主板'],last)
    assert report['scan']['ready']==1
    assert set(report['repair_codes'])=={'sh.600000','sh.600001','sh.600002'}
    assert set(report['scan_repair_codes'])=={'sh.600001','sh.600002'}
    assert len(report['evidence_pending'])==2 and not report['confirmed_history']


def test_new_listing_too_short_is_not_a_repair_queue(store):
    data=bars(100); first,last=scope(store,data,['sh.600000'])
    snapshot(store,'sh.600000',data,first,last)
    assert integrity_report(store,['沪深主板'],last)['repair_codes']==[]


def test_confirmed_no_resumption_halt_not_repaired_or_scanned(store):
    data=bars(); first,last=scope(store,data,['sh.601198'])
    # Explicit current-day evidence independently explains stale/no local bars.
    from workbench.readiness import save_calendar
    save_calendar(store,pd.DataFrame(dict(calendar_date=['2026-10-08'],is_trading_day=['1'])),
                  '2026-10-08','2026-10-08')
    save_directory(store,pd.DataFrame(dict(code=['sh.601198'],tradeStatus=['unknown'])),'2026-10-08')
    report=integrity_report(store,['沪深主板'],'2026-10-08')
    assert report['scan']['suspended']==1 and report['scan']['complete']
    assert report['repair_codes']==[]
    days,proof=announcement_evidence('sh.601198','2025-11-19','2025-12-18')
    assert '2025-11-20' in days and '2025-12-18' not in days and proof


def provider_for(monkeypatch,data,fail=False):
    class Fake:
        calls=[]
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def universe(self,end):
            return pd.DataFrame(dict(code=['sh.600000','sh.600001'],tradeStatus=['unknown']*2))
        def preload(self,codes,start,end):
            self.calls.append(list(codes))
            if fail:
                raise ProviderError('tickflow','batch','test HTTP 503','503')
        def fetch(self,code,start,end): return data[data.date.between(start,end)].copy()
    monkeypatch.setattr('workbench.tickflow.TickFlow',Fake)
    monkeypatch.setattr('workbench.service.BaoStock',lambda:pytest.fail('independent path connected to BaoStock'))
    return Fake


def test_targeted_repair_fetches_only_requested_code_then_rechecks(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    old=snapshot(store,'sh.600000',data.drop(index=200),first,last)
    untouched=snapshot(store,'sh.600001',data,first,last)
    fake=provider_for(monkeypatch,data)
    ProviderGuard(store,'baostock').failure(ProviderError('baostock','login','黑名单','10001011'))
    before=ProviderGuard(store,'baostock').state()
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'])))
        result=json.loads(row['result'])
        assert row['status']=='completed',row['message']
        assert fake.calls==[['sh.600000']] and not result['repair_remaining']
        assert result['integrity']['scan']['ready']==2
        assert load_dataset(store,old)[0].shape[0]==399  # immutable history retained
        assert load_dataset(store,untouched)[0].shape[0]==400
        assert ProviderGuard(store,'baostock').state()==before
        assert not coverage_state(store,'sh.600000')
        assert store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")
    finally:
        service.pool.shutdown()


def test_failed_batch_is_not_retried_and_still_has_whole_scope_integrity_receipt(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    fake=provider_for(monkeypatch,data,True)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,end=last)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and len(fake.calls)==1
        assert result['remaining']==0 and len(result['errors'])==2
        assert result['integrity']['scan']['ready']==0
        assert len(result['integrity']['scan_repair_codes'])==2
        assert result['integrity']['request_failures'][0]['error_code']=='503'
    finally:
        service.pool.shutdown()


def test_repair_that_returns_same_hole_is_partial_not_fake_complete(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    snapshot(store,'sh.600000',data.drop(index=10),first,last)
    snapshot(store,'sh.600001',data,first,last)
    fake=provider_for(monkeypatch,data.drop(index=10))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'])))
        result=json.loads(row['result'])
        assert row['status']=='partial' and result['repair_remaining']==['sh.600000']
        assert fake.calls==[['sh.600000']] and result['scan_readiness']['scan_allowed']
    finally:
        service.pool.shutdown()


def test_refresh_missing_old_real_bars_does_not_overwrite_usable_snapshot(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    old=snapshot(store,'sh.600000',data.drop(index=200),first,last)
    fake=provider_for(monkeypatch,data.drop(index=[200,201]))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'])))
        result=json.loads(row['result'])
        assert row['status']=='partial' and '保留原快照' in result['errors'][0]['error']
        assert load_dataset(store,old)[0].shape[0]==399 and fake.calls==[['sh.600000']]
    finally:
        service.pool.shutdown()


def test_changed_evidence_version_reassesses_quality_instead_of_stale_waiver(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data.drop(index=200),first,last)
    rows=store.rows('SELECT * FROM datasets WHERE id=?',(did,))
    key='market_quality:'+did
    bad=json.loads(store.rows('SELECT value FROM meta WHERE key=?',(key,))[0]['value'])
    bad['missing_dates']=[]; bad['evidence_version']='outdated'
    store.execute('UPDATE meta SET value=? WHERE key=?',(json.dumps(bad),key))
    assert quality_for(store,rows[0])['missing_dates']==[data.date.iloc[200]]


def test_ui_cache_only_gate_never_parses_legacy_full_history_on_main_thread(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data,first,last)
    store.execute('DELETE FROM meta WHERE key=?',('market_quality:'+did,))
    monkeypatch.setattr('workbench.history_quality.load_dataset',lambda *a:pytest.fail('UI parsed full history'))
    audit=audit_scope(store,['沪深主板'],last,source='tickflow',timeframe='daily',history_cache_only=True)
    assert not audit['scan_allowed'] and '后台登记完整性' in audit['gaps'][0]['error']


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Tk window')
def test_integrity_window_repair_selection_is_explicit_and_read_only(store):
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.tickflow_integrity import show_integrity
    data=bars(); first,last=scope(store,data,['sh.600000'])
    snapshot(store,'sh.600000',data.drop(index=10),first,last)
    report=integrity_report(store,['沪深主板'],last)
    root=ttk.Window(themename='darkly'); root.withdraw()
    before=store.rows('SELECT count(*) AS n FROM datasets')[0]['n']
    calls=[]
    try:
        show_integrity(root,report,lambda codes:calls.append(codes))
        root.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(root))
        repair=next(w for w in widgets if isinstance(w,ttk.Button) and str(w.cget('text')).startswith('仅补拉'))
        assert repair.instate(['disabled']) and '0 只' in repair.cget('text')
        checkbox=next(w for w in widgets if isinstance(w,ttk.Checkbutton))
        checkbox.invoke(); root.update()
        assert not repair.instate(['disabled']) and '1 只' in repair.cget('text')
        repair.invoke(); root.update()
        assert calls==[['sh.600000']] and store.rows('SELECT count(*) AS n FROM datasets')[0]['n']==before
    finally:
        root.destroy()
