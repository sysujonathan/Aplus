"""Native loading loop, real phase events, failures, and early watchdog handoff."""
import threading
import time

import pytest


@pytest.mark.native_process
def test_bootstrap_paints_while_factory_is_slow_and_trace_disabled(monkeypatch):
    from gui import startup_loading as loading
    from workbench import startup_metrics as metrics
    monkeypatch.setenv('APLUS_STARTUP_TRACE','0')
    observed=[];beats=[];threads=[]
    original=loading.LoadingPanel.status
    def status(panel,text):
        observed.append(text);original(panel,text)
        if not beats:
            def beat():
                beats.append(time.monotonic());panel.after(20,beat)
            panel.after(0,beat)
    monkeypatch.setattr(loading.LoadingPanel,'status',status)
    def work():
        threads.append(threading.get_ident())
        with metrics.span('window.import'):time.sleep(.3)
        return 'ready'
    try:
        assert loading.initialize(work)=='ready'
        assert threads==[threads[0]] and threads[0]!=threading.get_ident()
        assert len(beats)>=5
        assert '正在准备交易工作台…' in observed
    finally:
        if metrics.early_watchdog:metrics.early_watchdog.stop();metrics.early_watchdog=None


@pytest.mark.native_process
def test_bootstrap_failure_is_visible_and_retry_really_reexecutes(monkeypatch):
    from gui import startup_loading as loading
    calls=[];observed=[]
    original=loading.LoadingPanel.status
    def status(panel,text):
        observed.append(text);original(panel,text)
        if '失败' in text:
            def retry():
                buttons=[w for w in panel.winfo_children() if w.winfo_class()=='Button']
                next(w for w in buttons if w.cget('text')=='重试').invoke()
            panel.after(50,retry)
    monkeypatch.setattr(loading.LoadingPanel,'status',status)
    def work():
        calls.append(1)
        if len(calls)==1:raise OSError('injected locked database')
        return 'recovered'
    from workbench import startup_metrics as metrics
    try:
        assert loading.initialize(work)=='recovered'
        assert len(calls)==2
        assert any('失败' in text and 'injected locked database' in text for text in observed)
    finally:
        if metrics.early_watchdog:metrics.early_watchdog.stop();metrics.early_watchdog=None


@pytest.mark.native_process
def test_loader_geometry_candles_loop_and_destroy_at_scaled_dpi():
    import tkinter as tk
    from ttkbootstrap import Style
    from gui.startup_loading import LoadingPanel,CANDLES
    root=tk.Tk();root.withdraw()
    Style(theme='darkly')
    try:
        for scale in (1,1.25,1.5):
            root.tk.call('tk','scaling',scale*96/72)
            panel=LoadingPanel(root);root.update_idletasks()
            assert panel.canvas.cget('background')=='#0c141b'
            assert abs(panel.winfo_width()-300*panel.scale)<=2
            coords=panel.canvas.coords(panel.status_item)
            panel.status('正在加载关注列表…');panel.status('很长的阶段文字'*30)
            assert panel.canvas.coords(panel.status_item)==coords
            assert panel.status_font.measure(panel.canvas.itemcget(panel.status_item,'text'))<=260*panel.scale
            assert all(low<=min(o,c)<=max(o,c)<=high for o,high,low,c in CANDLES)
            panel._start-=2.8;panel._animate();assert panel.cycles>=1
            panel.destroy()
    finally:root.destroy()


def test_backend_failure_does_not_open_debug_shell(monkeypatch):
    import launch_dashboard
    monkeypatch.setattr(launch_dashboard,'build_backend',lambda:(None,None))
    with pytest.raises(RuntimeError,match='空白工作台'):
        launch_dashboard.prepare_workbench()


def test_backup_maintenance_waits_for_lists_and_active_chart(monkeypatch):
    from types import SimpleNamespace as NS
    from gui.main_window import AplusMainWindow
    import threading
    queued=[];calls=[]
    host=NS(error=None,ready=lambda:False)
    app=NS(_closing=False,watch=NS(_loading=True),candidates=NS(_loading=False),
           toolbar=NS(_dates_pending=False),chart=NS(_active_slot=0,
               _slots=[NS(_embedded=host,_observation_poll=None)]),
           after=lambda delay,fn:queued.append(fn))
    class Thread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(threading,'Thread',Thread)
    AplusMainWindow.schedule_startup_maintenance(app,lambda:calls.append('maintenance'))
    queued.pop(0)();assert calls==[]
    app.watch._loading=False
    queued.pop(0)();assert calls==[]
    host.ready=lambda:True
    queued.pop(0)();assert calls==['maintenance']
