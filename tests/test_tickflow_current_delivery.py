"""Current scan certification is not an all-history announcement project."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars, scope, snapshot
from tests.test_tickflow_integrity import provider_for
from workbench.tickflow_integrity import integrity_report, integrity_view
from workbench.readiness import audit_scope, save_directory
from workbench.market import save_dataset, load_dataset
from workbench.service import Service
from workbench.sources import set_market_source, market_source, coverage_state, save_coverage


def test_receipt_certifies_periods_independently_and_counts_partition_scope(store):
    data=bars(1700)
    codes=['sh.600000','sh.600001','sh.600002','sh.600003','sh.600004']
    first,last=scope(store,data,codes)
    snapshot(store,codes[0],data.drop(index=10),first,last)  # old for both periods
    snapshot(store,codes[1],data.drop(index=500),first,last)  # weekly only
    snapshot(store,codes[2],data.drop(index=1600),first,last)  # both periods
    snapshot(store,codes[3],data.iloc[-80:],first,last)  # immature, not fixable by redownload
    report=integrity_report(store,['沪深主板'],last)
    assert report['scan']['ready']==2
    assert report['scan_repair_codes']==[codes[2],codes[4]]
    assert codes[0] in {g['code'] for g in report['outside_input_history']}
    assert report['counts']['insufficient_bars']==1
    for tf,ready in [('daily',2),('weekly',1)]:
        view=integrity_view(report,tf)
        assert view['scan']['ready']==ready
        assert view['counts']['expected']==view['counts']['ready']+view['counts']['suspended']+view['counts']['excluded']
        assert sum(view['counts'][k] for k in ('insufficient_bars','input_gap','missing_dataset',
                   'stale_tail','invalid_file','quality_pending'))==view['counts']['excluded']
    assert integrity_view(report,'weekly')['scan_repair_codes']==[codes[1],codes[2],codes[4]]


def test_certification_checks_file_bytes_even_with_cached_quality(store):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    good=snapshot(store,'sh.600000',data,first,last)
    bad=snapshot(store,'sh.600001',data,first,last)
    record=store.rows('SELECT * FROM datasets WHERE id=?',(bad,))[0]
    store.write_artifact(record['path'],b'tampered')
    # The cheap UI gate intentionally does no disk hashing.
    assert audit_scope(store,['沪深主板'],last,source='tickflow',timeframe='daily')['ready']==2
    report=integrity_report(store,['沪深主板'],last)
    assert report['scan']['ready']==1 and report['scan']['eligible_ids']==[good]
    assert report['counts']['invalid_file']==1 and '校验' in report['scan']['gaps'][0]['error']


def test_scan_receipt_does_not_count_a_tampered_file_as_ready(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    good=snapshot(store,'sh.600000',data,first,last)
    bad=snapshot(store,'sh.600001',data,first,last)
    r=store.rows('SELECT * FROM datasets WHERE id=?',(bad,))[0]
    store.write_artifact(r['path'],b'damaged fixture')
    called=[]
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *a,**kw:called.append(a[1].attrs['code']) or None)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('scan',dict(source='tickflow',boards=['沪深主板'],datasets=[good,bad],
            strategies=['MTR_MASTER'],timeframes=['daily'],asof=last)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and result['coverage']['ready']==1 and result['total']==1
        assert called==['sh.600000'] and result['errors'][0]['category']=='invalid_file'
    finally:
        service.pool.shutdown()


def test_receipt_cannot_be_reused_for_other_date_or_boards(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    snapshot(store,'sh.600000',data,first,last)
    report=integrity_report(store,['沪深主板'],last)
    with pytest.raises(ValueError,match='过期'):
        integrity_view(report,'daily',asof=data.date.iloc[-2])
    with pytest.raises(ValueError,match='其他板块'):
        integrity_view(report,'daily',boards=['创业板'])
    legacy=dict(report); legacy.pop('periods')
    assert integrity_view(legacy,'daily') is legacy
    with pytest.raises(ValueError,match='旧回执'):
        integrity_view(legacy,'weekly')


def test_scan_repair_succeeds_when_input_fixed_but_older_history_remains(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    snapshot(store,'sh.600000',data.drop(index=[10,200]),first,last)
    untouched=snapshot(store,'sh.600001',data,first,last)
    fake=provider_for(monkeypatch,data.drop(index=10))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'],repair_scope='scan')))
        result=json.loads(row['result'])
        assert row['status']=='completed',(row['message'],result['errors'])
        assert result['repair_resolved']==['sh.600000'] and result['repair_remaining']==[]
        assert result['repair_history_pending']==['sh.600000']
        assert result['scan_readiness']['complete'] and result['integrity']['unknown_history']
        assert fake.calls==[['sh.600000']] and len(load_dataset(store,untouched)[0])==400
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('scope_kind',['scan','history'])
def test_scan_repair_no_improvement_remains_partial_and_only_one_batch(store,monkeypatch,scope_kind):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    snapshot(store,'sh.600000',data.drop(index=200),first,last)
    snapshot(store,'sh.600001',data,first,last)
    fake=provider_for(monkeypatch,data.drop(index=200))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'],repair_scope=scope_kind)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and result['repair_remaining']==['sh.600000']
        assert result['repair_resolved']==[] and fake.calls==[['sh.600000']]
    finally:
        service.pool.shutdown()


def test_current_certification_does_not_weaken_all_history_research_gate(store):
    from workbench.history_quality import require_research_history
    data=bars(); first,last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data.drop(index=10),first,last)
    assert integrity_report(store,['沪深主板'],last)['scan']['complete']
    record=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    with pytest.raises(ValueError,match='历史研究输入'):
        require_research_history(store,record,last)


def test_day_week_scan_receipt_keeps_both_scopes_not_only_last_period(store,monkeypatch):
    data=bars(1700); first,last=scope(store,data,['sh.600000','sh.600001'])
    good=snapshot(store,'sh.600000',data,first,last)
    weekly_bad=snapshot(store,'sh.600001',data.drop(index=500),first,last)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *a,**kw:None)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('scan',dict(source='tickflow',boards=['沪深主板'],
            datasets=[good,weekly_bad],strategies=['STRATEGY_STRUCTURAL_GAP'],timeframes=['daily','weekly'],asof=last)))
        report=json.loads(row['result'])
        assert row['status']=='partial'
        assert report['coverage_by_timeframe']['daily']['ready']==2
        assert report['coverage_by_timeframe']['weekly']['ready']==1
    finally:
        service.pool.shutdown()


def test_all_halted_scan_still_keeps_both_period_receipts(store):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data,first,last)
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['0'])),last,source='tickflow')
    service=Service(store)
    try:
        row=wait_for(store,service.submit('scan',dict(source='tickflow',boards=['沪深主板'],datasets=[did],
            strategies=['STRATEGY_STRUCTURAL_GAP'],timeframes=['daily','weekly'],asof=last)))
        report=json.loads(row['result'])
        assert row['status']=='completed' and report['success']==0 and report['total']==0
        assert set(report['coverage_by_timeframe'])=={'daily','weekly'}
        assert all(a['suspended']==1 and a['ready']==0 for a in report['coverage_by_timeframe'].values())
    finally:
        service.pool.shutdown()


def test_scan_repair_cannot_call_an_immature_replacement_resolved(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    # A previously absent stock returns valid but insufficient bars.
    snapshot(store,'sh.600001',data,first,last)
    provider_for(monkeypatch,data.iloc[-80:])
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'],repair_scope='scan')))
        report=json.loads(row['result'])
        assert row['status']=='partial' and report['repair_remaining']==['sh.600000']
        assert report['repair_resolved']==[]
        assert 'sh.600000' not in report['integrity']['scan_repair_codes']  # no pointless repeat
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('damage',['missing','changed'])
@pytest.mark.parametrize('covered',[True,False])
def test_explicit_repair_restores_only_original_hash_and_keeps_damaged_bytes(store,monkeypatch,damage,covered):
    from workbench.sources import save_coverage
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    did=snapshot(store,'sh.600000',data,first,last)
    if covered:
        save_coverage(store,'sh.600000',did,first,last,'tickflow')
    snapshot(store,'sh.600001',data,first,last)
    r=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    path=store.root/r['path']; original=path.read_bytes()
    if damage=='missing':
        path.unlink()  # this test's temporary fixture, not user files
    else:
        store.write_artifact(r['path'],b'damaged fixture')
    fake=provider_for(monkeypatch,data)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'],repair_scope='scan')))
        result=json.loads(row['result'])
        assert row['status']=='completed',row['message']
        assert path.read_bytes()==original and result['repair_remaining']==[]
        assert fake.calls==[['sh.600000']]
        backups=list((store.root/'quarantine/tickflow').glob('*.bin'))
        assert (len(backups)==1 and backups[0].read_bytes()==b'damaged fixture') if damage=='changed' else not backups
    finally:
        service.pool.shutdown()


def test_repair_rejects_changed_provider_history_for_a_corrupt_old_snapshot(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    did=snapshot(store,'sh.600000',data,first,last)
    save_coverage(store,'sh.600000',did,first,last,'tickflow')
    snapshot(store,'sh.600001',data,first,last)
    r=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    store.write_artifact(r['path'],b'damaged fixture')
    provider_for(monkeypatch,data.assign(close=10.5))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='tickflow',boards=['沪深主板'],start=first,
            end=last,repair_codes=['sh.600000'],repair_scope='scan')))
        result=json.loads(row['result'])
        assert row['status']=='partial' and result['repair_remaining']==['sh.600000']
        assert (store.root/r['path']).read_bytes()==b'damaged fixture'
        assert any('无法重建' in e['error'] for e in result['errors'])
    finally:
        service.pool.shutdown()


def test_switch_back_and_forth_preserves_datasets_coverage_and_reports(store,monkeypatch):
    data=bars(); first,last=scope(store,data,['sh.600000'])
    tf=snapshot(store,'sh.600000',data,first,last)
    save_coverage(store,'sh.600000',tf,first,last,'tickflow')
    bs=save_dataset(store,'sh.600000',data.assign(close=10.5),'baostock','前复权')
    save_coverage(store,'sh.600000',bs,first,last)
    # Isolate legacy inventories before the original source is updated.
    set_market_source(store,'baostock')
    save_directory(store,pd.DataFrame(dict(code=['sh.600000'],tradeStatus=['1'])),last)
    receipt=integrity_report(store,['沪深主板'],last)
    store.execute('INSERT INTO meta VALUES(?,?)',('tickflow_integrity',json.dumps(receipt)))
    snapshots=store.rows('SELECT * FROM datasets ORDER BY id')
    files={r['path']:(store.root/r['path']).read_bytes() for r in snapshots}
    before_meta=store.rows("SELECT * FROM meta WHERE key!='daily_market_source' ORDER BY key")
    before_bs=coverage_state(store,'sh.600000')
    before_tf=coverage_state(store,'sh.600000','tickflow')
    monkeypatch.setattr('workbench.tickflow.TickFlow',lambda:pytest.fail('switch should not connect'))
    monkeypatch.setattr('workbench.service.BaoStock',lambda:pytest.fail('switch should not connect'))
    for source in ['tickflow','baostock','tickflow','baostock']:
        set_market_source(store,source)
        assert market_source(store)==source
        audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily')
        assert audit['eligible_ids']==[tf if source=='tickflow' else bs]
    assert snapshots==store.rows('SELECT * FROM datasets ORDER BY id')
    assert before_meta==store.rows("SELECT * FROM meta WHERE key!='daily_market_source' ORDER BY key")
    assert before_bs==coverage_state(store,'sh.600000') and before_tf==coverage_state(store,'sh.600000','tickflow')
    assert all((store.root/path).read_bytes()==content for path,content in files.items())


@pytest.mark.skipif(os.name != 'nt',reason='Windows native Tk controls')
def test_native_period_switch_changes_repair_queue_without_network_or_store(tmp_path):
    result=subprocess.run([sys.executable,'-c',
        'from tests.test_tickflow_current_delivery import check_native_integrity; check_native_integrity()'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=35)
    assert result.returncode==0,result.stdout+result.stderr


def check_native_integrity():
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.tickflow_integrity import show_integrity
    gap=dict(code='sh.600001',error='周线输入未知缺口',category='input_gap')
    daily=dict(expected=2,ready=2,suspended=0,gaps=[],complete=True,scope_valid=True)
    weekly=dict(daily,ready=1,gaps=[gap],complete=False)
    report=dict(source='tickflow',asof='2026-10-08',timeframe='daily',scan=daily,boards=['沪深主板'],
        periods=dict(daily=daily,weekly=weekly),unknown_history=[],confirmed_history=[],repair_codes=['sh.600001'],
        scan_repair_codes=[],request_failures=[],current_halts=[])
    calls=[]; root=ttk.Window(themename='darkly'); root.withdraw()
    try:
        window=show_integrity(root,report,lambda *args:calls.append(args))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(window))
        combo=next(w for w in widgets if isinstance(w,ttk.Combobox))
        button=next(w for w in widgets if isinstance(w,ttk.Button) and str(w.cget('text')).startswith('继续补拉'))
        root.update()
        assert button.instate(['disabled'])
        combo.set('周线'); root.update()
        assert not button.instate(['disabled']) and '1 只' in button.cget('text')
        for width in (650,1000):
            window.geometry(f'{width}x580'); root.update()
            labels=[w for w in widgets if isinstance(w,ttk.Label) and int(w.cget('wraplength') or 0)>0]
            assert labels and all(w.winfo_reqwidth()<=w.winfo_width()+2 for w in labels), [
                (w.winfo_reqwidth(),w.winfo_width(),str(w.cget('wraplength'))) for w in labels]
        button.invoke(); root.update()
        assert calls==[(['sh.600001'],'weekly',False)]
    finally:
        root.destroy()
