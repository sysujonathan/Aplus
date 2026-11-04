from copy import deepcopy
import os
import pytest

from workbench.store import Store
from workbench.watch_views import WatchViews, import_codes, export_rows
from workbench.workspace_layouts import new_layout, validate_layout, drawing_visible, merge_drawings


def test_watch_views_preserve_original_pool_and_no_duplicate_trading_state(tmp_path):
    store = Store(tmp_path)
    store.add_manual_watch('159570', 'ETF', '原备注')
    original = [dict(r) for r in store.rows('SELECT * FROM watchlist')]
    views = WatchViews(store)
    first = views.create('T+0')
    views.add('159570')
    views.flag('159570', 'blue')
    views.reorder(['sz.159570'])
    reopened = WatchViews(Store(tmp_path))
    assert reopened.collection()['codes'] == ['sz.159570']
    assert reopened.data['flags'] == {'sz.159570':'blue'}
    assert reopened.visible([{'code':'sz.159570','name':'ETF'},{'code':'sh.600000','name':'股票'}])[0]['name']=='ETF'
    reopened.delete()
    assert [dict(r) for r in store.rows('SELECT * FROM watchlist')] == original
    assert not store.rows('SELECT * FROM observations') and not store.rows('SELECT * FROM plans')
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']=='9'


def test_ui_preferences_cas_and_atomic_csv_validation(tmp_path):
    store=Store(tmp_path)
    a,b=WatchViews(store),WatchViews(store)
    a.create('一')
    with pytest.raises(ValueError,match='另一窗口'):
        b.create('二')
    with pytest.raises(ValueError):
        import_codes('代码\n159570\nnot-a-security\n')
    assert import_codes('\ufeff代码,名称\n159570,ETF\n159570,ETF\n600000,股票')==['sz.159570','sh.600000']
    assert "'=SUM" in export_rows([{'code':'sz.159570','name':'=SUM(A1)','notes':'普通备注'}])
    assert [item['name'] for item in WatchViews(store).data['lists']]==['一']


def object_(identity, kind='global', layout='l1', pane='0'):
    return dict(id=identity,kind='trend',points=[['2026-10-01',10],['2026-10-02',11]],
                web={'shareScope':{'kind':kind,'layout':layout,'pane':pane}})


def test_drawing_visibility_and_per_object_concurrent_merge():
    workspace={'layout':'l1','pane':'0'}
    private=object_('private','pane',pane='1')
    shared=object_('shared')
    base=[private,shared]
    assert not drawing_visible(private,workspace)
    assert drawing_visible(shared,{'layout':'l2','pane':'5'})
    assert drawing_visible({'id':'legacy'},workspace)
    current=deepcopy(base);current[0]['points'][0][1]=9
    incoming=deepcopy([shared]);incoming[0]['points'][0][1]=8
    merged=merge_drawings(base,current,incoming,workspace)
    assert {o['id']:o['points'][0][1] for o in merged}=={'private':9,'shared':8}
    assert merge_drawings(base,current,[],workspace)==[current[0]]
    current[1]['points'][0][1]=7
    with pytest.raises(ValueError,match='同一图形'):
        merge_drawings(base,current,incoming,workspace)
    with pytest.raises(ValueError):
        merge_drawings([],base,[private],workspace)


@pytest.mark.parametrize('rows,columns',[(1,1),(2,2),(2,3),(2,4),(3,3),(3,2)])
def test_saved_layout_round_trip(tmp_path, rows, columns):
    store=Store(tmp_path)
    layout=new_layout('多周期',rows,columns)
    layout['panes']=[{'item':None,'timeframe':period} for period in ['daily','weekly','m5'][:rows*columns]]
    store.save_ui_preferences('chart_layouts',{'layouts':[layout],'active':layout['id']},0)
    actual=store.load_ui_preferences('chart_layouts')['data']['layouts'][0]
    assert validate_layout(actual)==layout
    with pytest.raises(ValueError):validate_layout({**layout,'rows':4})


def test_delayed_minute_layout_keeps_saved_period_until_command_ack(tmp_path):
    from types import SimpleNamespace
    from gui.workspace_controls import WorkspaceControls
    store = Store(tmp_path)
    layout = new_layout('分钟布局')
    desired = {'timeframe':'m5','range':['2026-10-30 10:00','2026-10-30 11:00']}
    class Host:
        _command_number = 0
        def command(self, kind, value):
            self._command_number += 1
            return True
    slot = SimpleNamespace(_workspace_restore=desired, _embedded=Host(), _accept_intraday=lambda _:None)
    controls = WorkspaceControls.__new__(WorkspaceControls)
    controls.grid = SimpleNamespace(_slots=[slot], _display_items=[None], _shape=(1,1), _active_slot=0)
    controls.store, controls.current, controls.layouts = store, layout, [layout]
    controls.states, controls.revision, controls._load_error = {}, 0, None
    controls.caption = controls.period = SimpleNamespace(set=lambda _:None)
    controls.sync = {key:SimpleNamespace(get=lambda:False) for key in ('timeframe','crosshair','time','range')}
    controls.schedule_save = lambda:None
    daily = dict(timeframe='daily',available=['daily'],ack=0)
    controls.state_changed(0, daily)
    controls.save()
    assert store.load_ui_preferences('chart_layouts')['data']['layouts'][0]['panes'][0]['timeframe']=='m5'
    controls.state_changed(0, {**daily,'available':['daily','m5']})
    assert slot._workspace_restore and slot._embedded._command_number==1
    controls.state_changed(0, {**daily,'timeframe':'m5'})  # queued is not yet acknowledged
    assert slot._workspace_restore
    controls.state_changed(0, dict(timeframe='m5',available=['m5'],ack=1,range=desired['range']))
    assert slot._workspace_restore is None
    controls.save()
    assert store.load_ui_preferences('chart_layouts')['data']['layouts'][0]['panes'][0]['range']==desired['range']


def test_instrument_precision_preserves_value_and_average_cost():
    from gui.trade_management import _price
    assert _price(1.286,'sz.159570')=='1.286'
    assert _price(1.258,'sz.159570')=='1.258'
    assert _price(10.12,'sh.600000')=='10.12'
    assert _price(1.2867,'sz.159570',cost=True)=='1.2867'


def test_corrupt_preferences_do_not_break_watch_or_overwrite_original(tmp_path):
    store=Store(tmp_path)
    store.add_manual_watch('159570','合成ETF','原记录')
    original='{"revision":1,"data":{"lists":[{"id":"x","codes":null}]}}'
    store.execute('INSERT INTO meta VALUES(?,?)',('ui:watch_views',original))
    views=WatchViews(store)
    assert views.error and views.data['active']=='all'
    assert len(views.visible([{'code':'sz.159570','name':'ETF'}]))==1
    with pytest.raises(ValueError,match='原设置已保留'):views.create('不能覆盖')
    assert store.rows('SELECT value FROM meta WHERE key=?',('ui:watch_views',))[0]['value']==original


def test_cross_period_projection_cache_cannot_conflict_or_erase_remote_edit():
    base=object_('shared');base['web']['a']={'date':'2026-10-01','index':1,'value':10}
    base['web']['createdAtIndex']=1
    incoming=deepcopy(base);incoming['web']['a']['index']=99;incoming['web']['createdAtIndex']=99
    current=deepcopy(base);current['points'][0][1]=12
    assert merge_drawings([base],[current],[incoming],{'layout':'l1','pane':'0'})==[current]


def test_real_bridge_merges_peer_drawings_and_preserves_alerts_and_per_pane_view(tmp_path):
    from gui.chart_bridge import workspace_boot, ChartBridge
    from tests.test_chart_embedded_workflow import setup
    store, frame, _=setup(tmp_path)
    context=dict(frame=frame,record={'source':'test','adjustment':'qfq'},code='sz.000001',
                 timeframe='daily',context_kind='watch')
    def bridge(pane):
        return ChartBridge(store,workspace_boot(store,{**context,'workspace':{'layout':'l1','pane':pane}}))
    a,b=bridge('0'),bridge('1')
    def state(identity,pane):
        return {'timeframe':'daily','view':{'count':70},'objects':[{'id':identity,'type':'trend',
            'a':{'index':0,'value':9},'b':{'index':2,'value':11},
            'shareScope':{'kind':'pane','layout':'l1','pane':pane}}]}
    a.save_state(state('a','0'),0)
    b.save_state(state('b','1'),0)
    document=store.chart_document(a.identity)
    assert {d['id'] for d in document['drawings']}=={'a','b'}
    assert document['alerts']==[]
    assert set(document['settings']['pane_states'])=={'l1:0','l1:1'}
    assert all('objects' not in s for s in document['settings']['pane_states'].values())
    reopened=bridge('0').bootstrap()
    assert [d['id'] for d in reopened['drawings']]==['a']
    assert reopened['state']['view']['count']==70
    detached=ChartBridge(store,workspace_boot(store,context))
    assert not detached.bootstrap()['drawings']
    detached.save_state({'timeframe':'daily','objects':[]})
    assert {d['id'] for d in store.chart_document(a.identity)['drawings']}=={'a','b'}


def test_workspace_js_contract():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(['node',str(root/'tests/chart_workspace_controls.cjs')],cwd=root,capture_output=True,text=True,check=True)
    assert '"ok":true' in result.stdout


def test_csv_import_is_atomic_and_preserves_original_notes(tmp_path, monkeypatch):
    store=Store(tmp_path)
    store.add_manual_watch('159570','ETF','不可覆盖')
    views=WatchViews(store); views.create('导入表')
    data=deepcopy(views.data); data['lists'][0]['codes']=['sz.159570','sh.600000']
    write=store.execute
    def interrupted(sql,args=()):
        if 'INSERT INTO watchlist' in sql and args[0]=='sh.600000':raise OSError('模拟写入中断')
        return write(sql,args)
    monkeypatch.setattr(store,'execute',interrupted)
    with pytest.raises(OSError):store.import_manual_watches(['159570','600000'],data,views.revision)
    assert store.load_ui_preferences('watch_views')['revision']==views.revision
    assert not store.rows("SELECT code FROM watchlist WHERE code='sh.600000'")
    monkeypatch.setattr(store,'execute',write)
    store.import_manual_watches(['159570','600000'],data,views.revision)
    assert WatchViews(store).collection()['codes']==['sz.159570','sh.600000']
    assert store.rows("SELECT notes FROM watchlist WHERE code='sz.159570'")[0]['notes']=='不可覆盖'


def test_layout_copy_is_atomic_and_private_drawings_are_independent(tmp_path, monkeypatch):
    from tests.test_chart_embedded_workflow import setup
    store,_,boot=setup(tmp_path)
    first,second=new_layout('原布局'),new_layout('复制布局')
    private=object_('private','pane',layout=first['id'])
    shared=object_('global')
    doc=store.chart_document(boot['identity']);doc['drawings']=[private,shared]
    store.save_chart_document(boot['identity'],doc,0)
    store.save_ui_preferences('chart_layouts',{'layouts':[first],'active':first['id']},0)
    data={'layouts':[first,second],'active':second['id']}
    write=store.execute
    def interrupted(sql,args=()):
        if args and args[0]=='ui:chart_layouts':raise OSError('模拟设置保存失败')
        return write(sql,args)
    monkeypatch.setattr(store,'execute',interrupted)
    with pytest.raises(OSError):store.save_ui_preferences('chart_layouts',data,1,
        layout_copy=(first['id'],second['id'],[boot['identity']]))
    assert len(store.chart_document(boot['identity'])['drawings'])==2
    monkeypatch.setattr(store,'execute',write)
    store.save_ui_preferences('chart_layouts',data,1,layout_copy=(first['id'],second['id'],[boot['identity']]))
    drawings=store.chart_document(boot['identity'])['drawings']
    assert len(drawings)==3 and drawings[:2]==[private,shared]
    copied=drawings[2]
    assert copied['id'] not in ('private','global') and copied['web']['shareScope']['layout']==second['id']
    assert copied['points']==private['points']


@pytest.mark.skipif(os.name!='nt',reason='Real Windows WebView transport')
def test_native_workspace_commands_token_and_remote_drawing(tmp_path):
    import subprocess,sys,json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'tests/watch_refresh_probe.py'),str(tmp_path),'--workspace'],
                          cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))['real_webview']


@pytest.mark.native_process
def test_native_watch_actions_and_trade_surface(tmp_path):
    import tkinter as tk
    from gui.watch_panel import WatchPanel
    from gui.trade_management import TradeManagementFrame
    store=Store(tmp_path)
    store.add_manual_watch('159570','合成ETF','合成备注')
    root=tk.Tk();root.geometry('1200x800')
    try:
        watch=WatchPanel(root,store)
        watch.pack(fill='both',expand=True)
        root.update()
        assert watch.tree.bbox(watch.tree.get_children()[0])
        watch.views.create('T0');watch.views.add('159570');watch._choose_list(watch.views.data['active'])
        assert watch.rows()[0]['code']=='sz.159570'
        watch.views.flag('159570','red');watch._render_rows()
        assert 'flag:red' in watch.tree.item(watch.tree.get_children()[0],'tags')
        watch.pack_forget()
        page=TradeManagementFrame(root,store);page.pack(fill='both',expand=True);root.update()
        assert page.position_tree.winfo_height()>page.position_tree._measure_font.metrics('linespace')*2
        box=page._panel_canvas.bbox('all')
        assert box[3]>=page._panel_canvas.winfo_height()
        page._panel_canvas.yview_moveto(1);root.update()
        assert page._panel_canvas.yview()[1]==1
    finally:root.destroy()
