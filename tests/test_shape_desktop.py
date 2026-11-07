"""Public-list and chart contracts exercised using real native widgets."""
from unittest.mock import Mock,patch

import pytest

from tests.test_shape_catcher import frame,template
from workbench.store import Store
from workbench.market import save_dataset
from workbench.shape_scan import scan


@pytest.mark.native_process
def test_public_sources_dates_filters_width_and_background_refresh(tmp_path):
    import ttkbootstrap as ttk
    from gui.match_list import MatchList
    from gui.chart_items import chart_items
    store=Store(tmp_path)
    save_dataset(store,'600000',frame(),'baostock','qfq')
    t=template(store)
    report=scan(store,[t],'2026-10-20','baostock')
    root=ttk.Window(themename='darkly');root.geometry('400x500')
    selected,rows_changed=Mock(),Mock()
    panel=MatchList(root,selected,on_rows_changed=rows_changed,on_manage=lambda:None)
    panel.pack(fill='both',expand=True);panel.build_tabs([('MTR_MASTER','MTR')])
    panel.load_from_store(store,asof_filter=('2026','10','20'));root.update()
    try:
        assert len(panel.rows())==1
        assert chart_items(panel.rows(),'candidate')[0].shape['run_id']==report['id']
        panel.set_source('量化策略');root.update()
        assert panel.rows()==[]
        assert '策略 · MTR (0)' in panel.filter_box['values']
        panel.set_source('形态捕手');root.update()
        before=panel.tree._obs[panel.tree.selection()[0]]
        selected.reset_mock();rows_changed.reset_mock()
        panel.load_from_store(store,asof_filter=('2026','10','20'),notify=False);root.update()
        assert panel.origin=='形态捕手'
        assert panel.tree._obs[panel.tree.selection()[0]]==before
        selected.assert_not_called();rows_changed.assert_not_called()
        assert panel.consume_refresh() is True and panel.consume_refresh() is False
        assert int(panel.tree.column('code','width')) >= panel._listing_font.measure('888888')+12
        panel.load_from_store(store,asof_filter=('2026','10','19'));root.update()
        assert not panel.rows() and panel.origin=='形态捕手'
    finally:root.destroy()


@pytest.mark.native_process
def test_shape_dialog_template_draw_save_search_and_case_prefix(tmp_path):
    import time
    import ttkbootstrap as ttk
    from gui.shape_dialog import ShapeDialog
    from workbench.service import Service
    store=Store(tmp_path)
    save_dataset(store,'600000',frame(),'baostock','qfq')
    service=Service(store)
    root=ttk.Window(themename='darkly');root.geometry('1150x850')
    dialog=ShapeDialog(root,store,service,('2026','10','20'))
    root.update()
    try:
        t=dialog._save();assert t and t['kind']=='classic'
        dialog._new();dialog.tabs.select(dialog.draw_page)
        dialog.points=[(0,0),(20,80),(40,20),(60,80),(80,0)]
        dialog.name.set('本机手绘轮廓')
        t=dialog._save();assert t and t['kind']=='drawing'
        dialog._search()
        deadline=time.monotonic()+8
        while service.shape_tasks().active and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        assert service.shape_tasks().status()['state']=='completed'
        dialog._poll();assert dialog.report['templates'][0]['kind']=='drawing'
        dialog._new();dialog.tabs.select(dialog.case_page)
        dialog.code.set('600000');dialog.start.set('2026-07-01');dialog.cutoff.set('2026-08-03')
        dialog.name.set('真实行情模板');dialog._preview_case()
        t=dialog._save();assert t['origin']['cutoff']=='2026-08-03'
        assert dialog.saved.winfo_height()>100
    finally:
        dialog._close();root.destroy();service.shape_tasks().close();service.pool.shutdown()


@pytest.mark.native_process
def test_saved_shape_uses_production_chart_without_strategy_or_future_bars(tmp_path):
    import ttkbootstrap as ttk
    from gui.chart_items import chart_items
    from gui.chart_panel import ChartPanel
    from gui.chart_bridge import workspace_boot
    store=Store(tmp_path)
    save_dataset(store,'600000',frame(),'baostock','qfq')
    report=scan(store,[template(store)],'2026-10-01','baostock')
    row=store.shape_candidates(('2026','10','01'),'baostock')[0]
    root=ttk.Window(themename='darkly')
    panel=ChartPanel(root,store=store,interactive=False);panel.pack(fill='both',expand=True)
    try:
        panel.show_shape(store,chart_items([row],'candidate')[0]);root.update()
        boot=workspace_boot(store,panel._workspace_context)
        assert boot['contextKind']=='shape'
        assert max(v['date'] for v in boot['bars'])=='2026-10-01'
        assert boot['strategyEvidence']['items']==[] and boot['evidence']=={}
        assert boot['startTimeframe']=='daily'
        assert '形态研究' in boot['contextTitle']
        assert not store.rows('SELECT * FROM observations')
    finally:root.destroy()


def test_shape_only_startup_date_and_weekly_do_not_mix(tmp_path):
    from gui.data import candidate_dates,latest_candidate_date
    store=Store(tmp_path);save_dataset(store,'600000',frame(),'baostock','qfq')
    scan(store,[template(store)],'2026-10-01','baostock')
    assert latest_candidate_date(store)=='2026-10-01'
    assert '2026-10-01' in candidate_dates(store)
    assert '2026-10-01' not in candidate_dates(store,'weekly')


def test_focus_mode_only_changes_visibility_and_keeps_workspace_state():
    from gui.main_window import AplusMainWindow
    window=Mock()
    window.list_pane.grid_info.return_value={'row':0}
    window.chart.layout_count=4
    AplusMainWindow._toggle_lists(window)
    assert window._lists_override is False
    window._apply_list_layout.assert_called_once_with(4)
    window.chart.set_items.assert_not_called();window.chart.set_grid.assert_not_called()


def test_shape_and_previous_chart_items_roundtrip_existing_layout_store(tmp_path):
    from dataclasses import asdict
    from gui.chart_items import chart_items,ChartItem
    from workbench.workspace_layouts import new_layout,validate_layout
    store=Store(tmp_path);save_dataset(store,'600000',frame(),'baostock','qfq')
    scan(store,[template(store)],'2026-10-20','baostock')
    item=chart_items(store.shape_candidates(('2026','10','20'),'baostock'),'candidate')[0]
    layout=new_layout()
    layout['panes']=[dict(item=asdict(item),timeframe='daily',range=None)]
    store.save_ui_preferences('chart_layouts',dict(layouts=[layout],active=layout['id']),0)
    saved=store.load_ui_preferences('chart_layouts')['data']['layouts'][0]
    assert ChartItem(**validate_layout(saved)['panes'][0]['item'])==item
    old=asdict(ChartItem(code='sh.600000',name='',observation_id='old',source='baostock',mode='candidate'))
    for keep_shape in (False,True):
        if not keep_shape:old.pop('shape',None)
        else:old['shape']=None
        layout['panes']=[dict(item=old,timeframe='daily')]
        assert validate_layout(layout)['panes'][0]['item']['observation_id']=='old'
