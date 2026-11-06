"""Partner review reproductions: historical transport and mixed-source tracking."""
import time
from types import MethodType,SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from gui.chart_panel import ChartPanel
from gui.chart_embed import EmbeddedChartHost
from tests.test_shape_catcher import frame,template
from workbench.market import save_dataset
from workbench.store import Store,digest,dumps
from workbench.shape_scan import scan


def minutes():
    return pd.DataFrame(dict(date=['2026-10-20 14:55','2026-10-20 15:00'],
        open=[10.,10.1],high=[10.2,10.3],low=[9.9,10.],close=[10.1,10.2],volume=[100.,120.]))


def case_template(store,source='baostock',adjustment='qfq'):
    identifier=save_dataset(store,'600000',frame(),source,adjustment)
    record=store.rows('SELECT * FROM datasets WHERE id=?',(identifier,))[0]
    return store.save_shape_template(dict(name=f'{source}案例',kind='case',path=[1,2,3],tracking=True,
        origin=dict(dataset_id=identifier,sha256=record['sha256'],start='2026-07-01',cutoff='2026-08-03'),
        settings=dict(threshold=0)))


def test_same_symbol_live_then_historical_shape_never_sends_cached_or_late_minutes(monkeypatch):
    monkeypatch.setattr(EmbeddedChartHost,'available',staticmethod(lambda:True))
    host=SimpleNamespace(closed=lambda:False,show=Mock(),push_live=Mock(return_value=True))
    panel=SimpleNamespace(_interactive=True,store=True,_embedded=host,
        _workspace_context=dict(context_kind='watch',code='sh.600000'),
        header=Mock(),readout_frame=Mock(),readout=Mock(),_intraday_sent=None,
        _intraday_code='sh.600000',_code='sh.600000',_intraday_frame=minutes(),_hourly=None,
        _embedded_error=Mock())
    panel._publish_intraday=MethodType(ChartPanel._publish_intraday,panel)
    ChartPanel._show_interactive(panel)
    assert host.push_live.call_count==1
    panel._workspace_context=dict(context_kind='shape',code='sh.600000',shape={'end':'2026-08-03'})
    ChartPanel._show_interactive(panel)
    assert host.show.call_count==2
    assert host.push_live.call_count==1  # Same-code cache must stay out of the new token.
    panel._publish_intraday(minutes())  # A result arriving after the switch is also blocked.
    assert host.push_live.call_count==1
    panel._workspace_context=dict(context_kind='watch',code='sh.600000')
    ChartPanel._show_interactive(panel)
    assert host.push_live.call_count==2  # Returning to ordinary watch keeps its cache behavior.
    panel._embedded_error.assert_not_called()


def test_historical_shape_activation_invalidates_live_poller_without_restarting():
    poller=SimpleNamespace(invalidate=Mock())
    panel=SimpleNamespace(_workspace_context={'context_kind':'shape'},_intraday_on=True,_intraday_poller=poller)
    ChartPanel._accept_intraday(panel,True)
    assert panel._intraday_on is False
    poller.invalidate.assert_called_once()


@pytest.mark.native_process
def test_real_tile_switch_to_shape_keeps_prefix_and_blocks_retained_minutes(tmp_path,monkeypatch):
    import ttkbootstrap as ttk
    from gui.chart_items import chart_items
    from gui.chart_bridge import workspace_boot
    store=Store(tmp_path);save_dataset(store,'600000',frame(),'baostock','qfq')
    scan(store,[template(store)],'2026-08-03','baostock')
    item=chart_items(store.shape_candidates(('2026','08','03'),'baostock'),'candidate')[0]
    root=ttk.Window(themename='darkly')
    panel=ChartPanel(root,store=store,interactive=False);panel.pack(fill='both',expand=True)
    host=SimpleNamespace(closed=lambda:False,show=Mock(),push_live=Mock(return_value=True),ready=lambda:True)
    poller=SimpleNamespace(invalidate=Mock())
    try:
        panel._code=panel._intraday_code='sh.600000'
        panel._intraday_frame=minutes();panel._intraday_on=True;panel._intraday_poller=poller
        panel._workspace_context={'context_kind':'watch'}
        panel._embedded=host;panel._interactive=True
        panel._history_btn.pack()
        monkeypatch.setattr(EmbeddedChartHost,'available',staticmethod(lambda:True))
        panel.show_shape(store,item);panel.set_active(True);root.update()
        boot=workspace_boot(store,host.show.call_args.args[0])
        assert max(row['date'] for row in boot['bars'])=='2026-08-03'
        assert not any(key.startswith('m') and key!='monthly' for key in boot['series'])
        panel._publish_intraday(minutes())
        host.push_live.assert_not_called();assert panel._intraday_on is False
        assert not panel._history_btn.winfo_ismapped()
        assert poller.invalidate.called
    finally:root.destroy()


def wait_for_shape(runner):
    until=time.monotonic()+10
    while runner.active and time.monotonic()<until:time.sleep(.01)
    assert runner.active is None
    return runner.status()


def test_source_switch_keeps_classic_and_current_source_templates_running(tmp_path):
    from workbench.service import Service
    from workbench.sources import set_market_source
    store=Store(tmp_path)
    old=case_template(store,'baostock')
    current=case_template(store,'tickflow','前复权')
    neutral=template(store,tracking=True)
    set_market_source(store,'tickflow')
    service=Service(store);runner=service.shape_tasks()
    try:
        runner.tracked();status=wait_for_shape(runner)
        assert status['state']=='completed',status
        report=store.shape_run(status['run_id'])
        assert {t['id'] for t in report['templates']}=={current['id'],neutral['id']}
        assert {m['template_id'] for m in report['matches']}=={current['id'],neutral['id']}
        assert report['skipped_templates'][0]['id']==old['id']
        assert '来源不同' in report['skipped_templates'][0]['reason']
        assert old['name'] in status['message']
        assert all(t['tracking'] for t in store.shape_templates())
        assert not store.rows('SELECT * FROM observations') and not store.rows('SELECT * FROM plans')
    finally:runner.close();service.pool.shutdown()


@pytest.mark.parametrize('kind',['case','chart'])
def test_new_real_template_rejects_hfq_at_save(tmp_path,kind):
    store=Store(tmp_path)
    identifier=save_dataset(store,'600000',frame(),'baostock','hfq')
    record=store.rows('SELECT * FROM datasets WHERE id=?',(identifier,))[0]
    with pytest.raises(ValueError,match='复权'):
        store.save_shape_template(dict(name='后复权模板',kind=kind,path=[1,2,3],
            origin=dict(dataset_id=identifier,sha256=record['sha256'],start='2026-07-01',cutoff='2026-08-03')))


def legacy_hfq_template(store):
    """Simulate a valid immutable template made by PR115 before the review fix."""
    value=case_template(store)
    identifier=save_dataset(store,'600000',frame(),'baostock','hfq')
    record=store.rows('SELECT * FROM datasets WHERE id=?',(identifier,))[0]
    definition={k:value[k] for k in ('id','name','kind','path','origin','algorithm')}
    definition['origin']=dict(definition['origin'],adjustment='hfq',dataset_id=identifier,sha256=record['sha256'])
    version=digest(dumps(definition).encode())
    saved=dict(definition,version=version,created=value['created'])
    store.execute('INSERT INTO meta VALUES(?,?)',(f"shape_template:{value['id']}:{version}",dumps(saved)))
    head=dict(id=value['id'],version=version,tracking=True,settings=value['settings'],revision=value['revision']+1)
    store.execute('UPDATE meta SET value=? WHERE key=?',(dumps(head),'shape_head:'+value['id']))
    # Current latest candidate remains qfq, so rejection must apply to the
    # template rather than accidentally rejecting a hfq candidate snapshot.
    save_dataset(store,'600000',frame('top_reversal'),'baostock','qfq')
    return next(t for t in store.shape_templates() if t['id']==value['id'])


def test_existing_hfq_template_is_rejected_manually_and_skipped_automatically(tmp_path):
    from workbench.service import Service
    store=Store(tmp_path);old=legacy_hfq_template(store)
    neutral=template(store,tracking=True)
    with pytest.raises(ValueError,match='复权'):scan(store,[old],'2026-10-20','baostock')
    service=Service(store);runner=service.shape_tasks()
    try:
        runner.tracked();status=wait_for_shape(runner)
        assert status['state']=='completed'
        report=store.shape_run(status['run_id'])
        assert [t['id'] for t in report['templates']]==[neutral['id']]
        assert '复权' in report['skipped_templates'][0]['reason']
    finally:runner.close();service.pool.shutdown()


@pytest.mark.parametrize('source,template_adjustment,candidate_adjustment',[
    ('baostock','前复权','qfq'),('baostock','2','前复权'),
    ('tickflow','qfq','前复权'),('tencent','不复权','none'),('tencent','raw','3')])
def test_equivalent_adjustment_aliases_match(source,template_adjustment,candidate_adjustment,tmp_path):
    store=Store(tmp_path);t=case_template(store,source,template_adjustment)
    save_dataset(store,'600000',frame('top_reversal'),source,candidate_adjustment)
    report=scan(store,[t],'2026-10-20',source)
    assert report['status']=='completed' and not report['errors'] and len(report['matches'])==1


def test_all_foreign_templates_make_explicit_skip_receipt_and_keep_tracking(tmp_path):
    from workbench.service import Service
    from workbench.sources import set_market_source
    store=Store(tmp_path);old=case_template(store)
    save_dataset(store,'600000',frame(),'tickflow','qfq');set_market_source(store,'tickflow')
    service=Service(store);runner=service.shape_tasks()
    try:
        runner.tracked();status=wait_for_shape(runner)
        assert status['state']=='skipped'
        report=store.shape_run(status['run_id'])
        assert not report['templates'] and not report['matches']
        assert report['skipped_templates'][0]['id']==old['id']
        assert store.shape_templates()[0]['tracking'] is True
    finally:runner.close();service.pool.shutdown()


@pytest.mark.native_process
def test_skipped_templates_are_visible_from_result_history(tmp_path):
    import ttkbootstrap as ttk
    from gui.shape_dialog import ShapeDialog
    from workbench.service import Service
    from workbench.sources import set_market_source
    store=Store(tmp_path);old=case_template(store)
    save_dataset(store,'600000',frame(),'tickflow','qfq');set_market_source(store,'tickflow')
    service=Service(store);runner=service.shape_tasks()
    root=ttk.Window(themename='darkly')
    dialog=ShapeDialog(root,store,service,('2026','10','20'))
    detail=None
    try:
        runner.tracked();status=wait_for_shape(runner)
        dialog._refresh_history();dialog._display_run(status['run_id']);root.update()
        assert str(dialog.skipped_button['state'])=='normal'
        assert '未运行模板 1' in dialog.status.get()
        detail,text=dialog._show_skipped_templates()
        assert old['name'] in text.get('1.0','end') and '来源不同' in text.get('1.0','end')
        dialog._resume();resumed=wait_for_shape(runner)
        assert resumed['state']=='skipped'
        assert store.shape_run(resumed['run_id'])['skipped_templates']==dialog.report['skipped_templates']
    finally:
        if detail:detail.destroy()
        dialog._close();root.destroy();runner.close();service.pool.shutdown()
