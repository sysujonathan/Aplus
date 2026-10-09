import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from tests.test_workbench import store
from workbench.gap_review import (classify_review, review_evidence, review_receipt,
                                 review_summary, repair_selection, review_selection, pending_count)


def receipt(source='tencent'):
    gaps = [dict(code='sh.600222', category='input_gap', dates=['2025-12-02'], error='历史缺日'),
            dict(code='sh.600293', category='stale_tail', error='行情只到 2026-09-28'),
            dict(code='sh.601091', category='insufficient_bars', error='11根',
                 window=dict(start='2026-09-17', end='2026-10-09', rows=11)),
            dict(code='sh.601313', category='missing_dataset', error='本地缺少行情'),
            dict(code='sz.002525', category='missing_dataset', error='本地缺少行情')]
    scan = dict(source=source, expected=6, ready=1, gaps=gaps, suspended=0,
                complete=False, scope_valid=True, scan_allowed=True, expected_day='2026-10-09')
    return dict(source=source, asof='2026-10-09', timeframe='daily', scan=scan,
                repair_codes=[g['code'] for g in gaps], scan_repair_codes=[g['code'] for g in gaps],
                retry_codes=['sh.600222'], current_halts=[])


def evidence():
    return dict(statuses={'sh.600293': {'tradeStatus': '0'}}, status_path='directories/2026-10-09.csv',
                basics={'sh.601091': {'ipoDate': '2026-09-17'}}, basics_path='directories/2026-10-09-basics.csv',
                calendar=dict(start='2026-01-01', end='2026-10-09',
                              trading_days=pd.bdate_range('2026-09-17', periods=11).strftime('%Y-%m-%d').tolist()))


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
def test_all_sources_use_same_classifications_without_changing_gate(source):
    view = receipt(source); before = copy.deepcopy(view)
    review = classify_review(view, evidence())
    assert {g['id']: g['count'] for g in review['groups']} == dict(history=1, halt=1, short=1, identity=2)
    assert '新股' in next(r for r in review['rows'] if r['group']=='short')['reason']
    assert repair_selection(review, ['history', 'halt', 'short', 'identity']) == ['sh.600222']
    assert review['ready']==1 and view==before
    assert '忽略仅移出' in review_summary(review)


def test_absence_alone_does_not_prove_halt_or_new_listing():
    review=classify_review(receipt())
    assert next(r for r in review['rows'] if r['code']=='sh.600293')['group']=='download'
    assert '新股' not in next(r for r in review['rows'] if r['code']=='sh.601091')['reason']
    ev=evidence(); ev['calendar']['trading_days']=[]
    assert '新股' not in next(r for r in classify_review(receipt(),ev)['rows'] if r['group']=='short')['reason']


def test_weekly_shortfall_never_confused_with_daily_new_listing():
    view=receipt(); view['timeframe']='weekly'
    row=next(r for r in classify_review(view,evidence())['rows'] if r['group']=='short')
    assert '周K' in row['reason'] and '新股' not in row['reason']


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
def test_decisions_persist_and_only_reduce_pending_statistics(store, source):
    view=receipt(source); review=classify_review(view,evidence())
    chosen=review_selection(review,['history'])
    store.set_gap_review_decisions(source,chosen,'ignored')
    stored=store.gap_review_decisions(source)
    restored=classify_review(view,evidence(),stored)
    assert restored['pending']==4 and restored['ignored']==1 and restored['excluded']==5
    assert restored['ready']==1 and restored['scan_allowed'] is True
    assert repair_selection(restored,['history'])==[]
    store.set_gap_review_decisions(source,chosen,'pending')
    assert classify_review(view,evidence(),store.gap_review_decisions(source))['pending']==5
    assert store.rows('SELECT count(*) n FROM datasets')[0]['n']==0
    assert store.rows('SELECT count(*) n FROM sync_coverage')[0]['n']==0
    assert store.rows("SELECT key FROM meta WHERE key LIKE 'market_quality:%'")==[]
    other='tickflow' if source!='tickflow' else 'tencent'
    assert store.gap_review_decisions(other)=={}


@pytest.mark.parametrize('change', ['date','period','hole','proof','category'])
def test_changed_issue_cannot_inherit_an_ignore(change):
    view=receipt(); ev=evidence(); review=classify_review(view,ev)
    row=review['rows'][0]; decisions={row['key']:dict(state='ignored')}
    if change=='date': view['asof']='2026-10-12'
    if change=='period': view['timeframe']='weekly'
    if change=='hole': view['scan']['gaps'][0]['dates'].append('2025-12-03')
    if change=='proof': ev['statuses'][row['code']]={'tradeStatus':'0'}
    if change=='category': view['scan']['gaps'][0]['category']='missing_dataset'
    assert classify_review(view,ev,decisions)['ignored']==0


def test_old_identity_not_applied_before_change_date():
    view=receipt(); view['asof']='2010-01-01'
    assert not any(r['group']=='identity' for r in classify_review(view)['rows'])


def test_outside_history_is_opt_in_and_cannot_change_current_counts():
    view=receipt(); view['outside_input_history']=[dict(code='sz.000001',dates=['2016-01-04'])]
    view['repair_codes'].append('sz.000001')
    assert not any(r['group']=='outside' for r in classify_review(view)['rows'])
    review=classify_review(view,include_history=True)
    assert repair_selection(review,['outside'])==['sz.000001'] and review['pending']==5


def test_baostock_dated_facts_read_only_and_no_future_or_wrong_day_status(store):
    store.write_artifact('universe.csv', b'code,code_name,tradeStatus\nsh.600293,A,unknown\n')
    store.write_artifact('directories/2026-10-08.csv', b'code,tradeStatus\nsh.600293,0\n')
    store.write_artifact('directories/2026-10-12-basics.csv', b'code,ipoDate\nsh.601091,2026-09-17\n')
    before=store.rows('SELECT * FROM meta')
    ev=review_evidence(store,'2026-10-09','baostock')
    assert not ev['statuses'] and not ev['basics']
    assert store.rows('SELECT * FROM meta')==before
    store.write_artifact('directories/2026-10-09.csv', b'code,tradeStatus\nsh.600293,0\n')
    ev=review_evidence(store,'2026-10-09','baostock')
    assert classify_review(receipt('baostock'),ev)['groups'][1]['id']=='halt'


def test_toolbar_count_matches_dialog_after_ignore(store):
    view=receipt('baostock'); ev=review_evidence(store,view['asof'],'baostock')
    review=classify_review(view,ev)
    store.set_gap_review_decisions('baostock',review_selection(review,['history']),'ignored')
    assert pending_count(store,view['scan'],'daily')==4


def test_pending_verification_counts_honor_ignore_for_each_source(store):
    for source in ('baostock','tickflow','tencent'):
        view=receipt(source)
        view['scan']['gaps']=[dict(code='sh.600000',category='quality_pending',error='等待核验')]
        review=classify_review(view,review_evidence(store,view['asof'],source))
        store.set_gap_review_decisions(source,review['rows'],'ignored')
        assert pending_count(store,view['scan'],'daily')==0
        assert review['ready']==1 and len(view['scan']['gaps'])==1


def test_bad_decision_rolls_back_and_unknown_state_rejected(store):
    with pytest.raises(ValueError): store.set_gap_review_decisions('tencent',[],'allow_scan')
    with pytest.raises(ValueError): store.set_gap_review_decisions('unknown',[],'pending')
    row=classify_review(receipt())['rows'][0]
    with pytest.raises(ValueError):
        store.set_gap_review_decisions('tencent',[row,dict(key='bad',code='sh.600293')],'ignored')
    assert store.gap_review_decisions('tencent')=={}


def test_baostock_receipt_uses_same_entry_without_network(store):
    from tests.test_tickflow_scan_readiness import bars, scope
    from workbench.readiness import save_directory
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    save_directory(store,pd.DataFrame(dict(code=['sh.600000','sh.600001'],tradeStatus=['1','0'])),last)
    view=review_receipt(store,'baostock',['沪深主板'],last,'daily')
    review=classify_review(view,review_evidence(store,last,'baostock'))
    assert {g['id']:g['count'] for g in review['groups']}==dict(halt=1,download=1)
    assert not review['scan_allowed'] and repair_selection(review,['download'])==['sh.600000']


def test_baostock_targeted_repair_keeps_original_single_connection(store,monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    from tests.test_tickflow_scan_readiness import bars, scope
    from workbench.service import Service
    data=bars(); first,last=scope(store,data,['sh.600000','sh.600001'])
    class Fake:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def universe(self,day):
            return pd.DataFrame(dict(code=['sh.600000','sh.600001'],code_name=['A','B'],tradeStatus=['1','1']))
        def basics(self):
            return pd.DataFrame(dict(code=['sh.600000','sh.600001'],ipoDate=[first,first],outDate=['',''],type=['1','1'],status=['1','1']))
    monkeypatch.setattr('workbench.service.BaoStock',Fake)
    monkeypatch.setattr('workbench.service.completed_date',lambda:last)
    calls=[]
    def fake_results(*args,**kwargs):
        calls.append((list(args[2]),args[6],args[9]))
        yield from ()
    monkeypatch.setattr('workbench.service.sync_results',fake_results)
    service=SimpleNamespace(store=store,cancel_flags={'test':Event()},progress=lambda *a:None)
    report=Service._sync(service,'test',dict(source='baostock',boards=['沪深主板'],start=first,end=last,
                                           repair_codes=['sh.600000'],force=False))
    assert calls==[(['sh.600000'],True,1)] and report['requested']==1


def test_web_classification_uses_same_decisions_and_explicit_repair(tmp_path):
    from streamlit.testing.v1 import AppTest
    script = '''
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from workbench.store import Store
from workbench.gap_review_web import render_gap_review
from tests.test_gap_review import receipt, evidence
store=Store(Path(ROOT))
if 'requests' not in st.session_state: st.session_state['requests']=[]
def submit(kind,spec): st.session_state['requests'].append(dict(kind=kind,spec=spec))
with patch('workbench.gap_review_web.expected_day',return_value='2026-10-09'), patch('workbench.gap_review_web.review_receipt',return_value=receipt()), patch('workbench.gap_review_web.review_evidence',return_value=evidence()):
    render_gap_review(store,SimpleNamespace(submit=submit),'tencent',['沪深主板'],'2026-10-09',['daily'],'2024-10-09',False)
'''.replace('ROOT',repr(str(tmp_path/'web')))
    app=AppTest.from_string(script,default_timeout=30).run()
    assert not app.exception
    app.multiselect(key='gap_groups_tencent_daily').select('history').run()
    app.button(key='gap_ignored').click().run()
    assert not app.exception and '待处理 4 只' in app.caption[0].value
    assert app.session_state['requests']==[]
    app.button(key='gap_pending').click().run()
    assert '待处理 5 只' in app.caption[0].value
    app.checkbox(key='gap_repeat').check().run()
    next(b for b in app.button if b.label.startswith('继续补拉')).click().run()
    assert not app.exception
    requests=app.session_state['requests']
    assert len(requests)==1 and requests[0]['spec']['source']=='tencent'
    assert requests[0]['spec']['repair_codes']==['sh.600222']


@pytest.mark.skipif(os.name!='nt',reason='Windows native UI')
def test_compact_ui_decisions_restore_and_repair_is_explicit(tmp_path):
    result=subprocess.run([sys.executable,'-c',
        'from tests.test_gap_review import check_native_review; check_native_review()',str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def check_native_review():
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.tickflow_integrity import show_integrity
    from workbench.store import Store
    st=Store(Path(sys.argv[1])/'ui')
    view=receipt(); view.update(excluded=view['scan']['gaps'], unknown_history=[], confirmed_history=[],
                               request_failures=[], boards=['沪深主板'], outside_input_history=[])
    root=ttk.Window(themename='darkly'); root.withdraw(); calls=[]
    window=None
    try:
        window=show_integrity(root,view,lambda *a:calls.append(a),store=st)
        def descendants(w):
            for child in w.winfo_children():
                yield child; yield from descendants(child)
        widgets=list(descendants(window)); tree=next(w for w in widgets if isinstance(w,ttk.Treeview))
        buttons={str(w.cget('text')):w for w in widgets if isinstance(w,ttk.Button)}
        root.update(); assert all(not tree.item(iid,'open') for iid in tree.get_children())
        assert not any(isinstance(w,tk.Text) for w in widgets)
        tree.selection_set('group:history'); root.update(); buttons['忽略统计'].invoke(); root.update()
        assert tree.item('group:history','values')[1]=='0'
        buttons['待定／恢复统计'].invoke(); root.update(); assert tree.item('group:history','values')[1]=='1'
        tree.selection_set('group:identity'); root.update()
        repair=next(w for w in buttons.values() if str(w.cget('text')).startswith('继续补拉'))
        assert repair.instate(['disabled']) and calls==[]
        buttons['忽略统计'].invoke(); root.update(); window.destroy()
        window=show_integrity(root,view,lambda *a:calls.append(a),store=st); root.update()
        tree=next(w for w in descendants(window) if isinstance(w,ttk.Treeview))
        assert tree.item('group:identity','values')[2]=='已忽略统计'
        for width in (650,1000):
            window.geometry(f'{width}x590'); root.update()
            for widget in descendants(window):
                if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0):
                    assert widget.winfo_reqwidth()<=widget.winfo_width()+2
        window.destroy()
        from unittest.mock import patch
        window=show_integrity(root,view,lambda *a:False,store=st); root.update()
        tree=next(w for w in descendants(window) if isinstance(w,ttk.Treeview))
        tree.selection_set('group:history'); root.update()
        repair=next(w for w in descendants(window) if isinstance(w,ttk.Button) and str(w.cget('text')).startswith('继续补拉'))
        before=st.gap_review_decisions('tencent')
        with patch('gui.tickflow_integrity.messagebox.askyesno',return_value=True), patch('gui.tickflow_integrity.messagebox.showerror') as error:
            repair.invoke(); root.update()
            assert error.call_count==1 and window.winfo_exists()
        assert st.gap_review_decisions('tencent')==before
        assert st.rows('SELECT count(*) n FROM datasets')[0]['n']==0
    finally:
        root.destroy()
