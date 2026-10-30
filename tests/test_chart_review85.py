"""PR85 review: finite closed-session reads and request-owned status receipts."""
from datetime import datetime,timedelta
from types import MethodType,SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from gui import chart_embed as embed,closed_trade_chart as holding
from workbench.live_minutes import CHINA,minute_refresh_phase
from tests.test_live_minutes import minute_frame


class Text:
    def __init__(self):self.value=''
    def set(self,value):self.value=value
    def get(self):return self.value


def reader(monkeypatch,stamp,*,failure=False):
    clock=[0.];moment=[datetime(2026,10,30,15,0,1,tzinfo=CHINA)]
    frame=minute_frame().iloc[:2].copy()
    frame['date']=[(datetime.fromisoformat(stamp)-timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M'),stamp]
    class Poller:
        busy=False;delay=8
        def __init__(self):self.queued=None;self.calls=[]
        def take(self):
            value,self.queued=self.queued,None;return value
        def start(self,code):
            self.calls.append(clock[0]);self.queued=(None,'network failed') if failure else (frame,None)
            return True
    poller=Poller()
    panel=SimpleNamespace(_live_cancel=threading.Event(),top=SimpleNamespace(winfo_exists=lambda:True,after=Mock()),
        _live_poller=poller,_live_due=0.,_live_phase=minute_refresh_phase(moment[0].replace(hour=14,minute=59)),
        _live_boundary_attempts=0,_live_boundary_pending=False,_live_error='',_last_live_at=stamp,
        _hourly=SimpleNamespace(request=Mock()),data={'code':'sz.000001'},_auto_enabled=lambda:True,
        _live_calendar=lambda:None,live_var=Text(),live_label=Mock())
    def publish(value):panel._last_live_at=str(value.date.iloc[-1]);panel._live_error=''
    panel._publish_live=publish
    panel._show_live_status=MethodType(holding.HoldingTradeChartDialog._show_live_status,panel)
    panel._live_tick=MethodType(holding.HoldingTradeChartDialog._live_tick,panel)
    monkeypatch.setattr(holding,'china_now',lambda:moment[0])
    monkeypatch.setattr(holding.time,'monotonic',lambda:clock[0])
    return panel,clock,moment,frame


@pytest.mark.parametrize('stamp',['2026-10-29 14:55','2026-10-30 14:55'])
def test_stale_success_is_bounded_after_close_and_during_weekend(monkeypatch,stamp):
    panel,clock,moment,_=reader(monkeypatch,stamp)
    for second in range(900):clock[0]=second;panel._live_tick()
    calls=panel._live_poller.calls
    assert len(calls)==3 and all(b-a>=60 for a,b in zip(calls,calls[1:]))
    assert panel._hourly.request.call_count==3
    assert '待刷新' in panel.live_var.get() and '自动请求已停止' in panel.live_var.get()
    assert '自动暂停' not in panel.live_var.get()
    moment[0]=datetime(2026,10,31,10,tzinfo=CHINA)
    for second in range(900,1800):clock[0]=second;panel._live_tick()
    assert len(calls)==3 and panel._hourly.request.call_count==3
    moment[0]=moment[0].replace(hour=16)  # Same closed phase; no fresh budget.
    for second in range(1800,2000):clock[0]=second;panel._live_tick()
    assert len(calls)==3
    moment[0]=datetime(2026,11,2,9,31,tzinfo=CHINA);clock[0]=2000;panel._live_tick()
    assert len(calls)==4  # A new trading session resumes normal reads.


def test_noon_budget_stops_and_afternoon_resumes(monkeypatch):
    panel,clock,moment,_=reader(monkeypatch,'2026-10-30 10:50')
    moment[0]=datetime(2026,10,30,11,31,tzinfo=CHINA)
    panel._live_phase=minute_refresh_phase(moment[0].replace(hour=11,minute=29))
    for second in range(900):clock[0]=second;panel._live_tick()
    assert len(panel._live_poller.calls)==3
    moment[0]=moment[0].replace(hour=13,minute=1);clock[0]=900;panel._live_tick()
    assert len(panel._live_poller.calls)==4


def test_final_bar_acknowledges_completion_without_further_reads(monkeypatch):
    panel,clock,_,_=reader(monkeypatch,'2026-10-30 15:00')
    for second in range(900):clock[0]=second;panel._live_tick()
    assert len(panel._live_poller.calls)==1
    assert '数据已就绪' in panel.live_var.get() and '待刷新' not in panel.live_var.get()


def test_closed_session_errors_also_exhaust_the_budget(monkeypatch):
    panel,clock,_,_=reader(monkeypatch,'2026-10-30 14:55',failure=True)
    for second in range(900):clock[0]=second;panel._live_tick()
    assert len(panel._live_poller.calls)==3
    assert '读取失败' in panel.live_var.get() and '自动请求已停止' in panel.live_var.get()


@pytest.mark.parametrize('late',[
    {'error':'old page failure'},{'closeCancelled':True},
    {'ready':True,'error':'old error','closeCancelled':True}])
def test_old_page_status_cannot_affect_new_request(tmp_path,late):
    host=embed.EmbeddedChartHost(Mock(),SimpleNamespace(root=tmp_path),on_error=Mock(),on_ready=Mock())
    host.parent.winfo_exists.return_value=True;host.process=Mock();host.process.poll.return_value=None
    host.token=8;host._started=embed.time.monotonic();host.closing=True
    embed.atomic_json(host.status,dict(scope='page',token=7,**late));host._poll()
    assert host.closing and host._started is not None
    host.on_error.assert_not_called();host.on_ready.assert_not_called()
    embed.atomic_json(host.status,dict(scope='page',token=8,ready=True));host._poll()
    host.on_ready.assert_called_once();assert host._started is None
    embed.atomic_json(host.status,dict(scope='page',token=8,closeCancelled=True));host._poll()
    assert not host.closing


@pytest.mark.parametrize('scope,token',[('page',8),('process',7),('process',None)])
def test_current_page_and_process_fatal_errors_remain_visible(tmp_path,scope,token):
    host=embed.EmbeddedChartHost(Mock(),SimpleNamespace(root=tmp_path),on_error=Mock())
    host.parent.winfo_exists.return_value=True;host.process=Mock();host.process.poll.return_value=None;host.token=8
    embed.atomic_json(host.status,dict(scope=scope,token=token,error='fatal failure'));host._poll()
    host.on_error.assert_called_once_with('fatal failure')


def test_close_guard_captures_request_token_before_async_flush(monkeypatch):
    from gui import chart_shell as shell
    monkeypatch.setattr(shell.threading,'Thread',Mock())
    bridge=SimpleNamespace(active_token=7,close_requested=True,close_token=8)
    guard=shell.ChartCloseGuard(Mock(),bridge)
    assert guard.request() is False and guard.request_token==8
    bridge.active_token=9;bridge.close_token=10
    assert guard.request() is False and guard.request_token==8


@pytest.mark.parametrize('event',['ready','cancelled','startup-fatal','transport-fatal'])
def test_status_producers_distinguish_page_receipts_and_process_failure(tmp_path,monkeypatch,event):
    import sys
    from gui import chart_shell as shell
    class Hook:
        def __init__(self):self.handlers=[]
        def __iadd__(self,callback):self.handlers.append(callback);return self
        def wait(self,_timeout):return True
    session=tmp_path/'session.json'
    embed.atomic_json(session,dict(runtime_root=str(tmp_path/'isolated-runtime'),boot=None,parent_hwnd=1,token=7))
    window=SimpleNamespace(events=SimpleNamespace(closing=Hook(),loaded=Hook(),closed=SimpleNamespace(is_set=lambda:True)),
        native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda:1)),show=Mock(),destroy=Mock())
    def create(*args,**kwargs):window.bridge=kwargs['js_api'];return window
    def start(callback,**kwargs):
        if event=='startup-fatal':raise RuntimeError('startup failed')
        if event=='transport-fatal':callback();return
        if event=='ready':window.events.loaded.handlers[0]();return
        guard=window.events.closing.handlers[0].__self__
        guard.request_token=8
        window.bridge.close_requested=True;window.bridge.close_token=8
        def confirmation(*args):
            guard.request_token=99  # A later request arrives while the dialog is open.
            window.bridge.close_token=99
            return False
        window.create_confirmation_dialog=confirmation
        window.bridge._cancel_close()
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(create_window=create,start=start))
    monkeypatch.setattr(embed,'adopt_parent_dpi',lambda _:None)
    monkeypatch.setattr(embed,'NativeChild',Mock(side_effect=RuntimeError('transport failed')))
    recovery=Mock(wraps=embed.run_embedded_close_recovery)
    monkeypatch.setattr(embed,'run_embedded_close_recovery',recovery)
    if event=='startup-fatal':
        with pytest.raises(RuntimeError,match='startup failed'):shell.run_session(session)
    else:shell.run_session(session)
    assert recovery.call_count==(1 if event=='transport-fatal' else 0)
    status=embed.read_notification(session.with_suffix('.status.json'))
    if event=='cancelled':
        assert status==dict(scope='page',token=8,closeCancelled=True)
        assert window.bridge.close_requested  # Old cancellation cannot clear the newer close.
    elif event=='ready':assert status==dict(scope='page',token=7,ready=True)
    else:assert status['scope']=='process' and status['token']==7 and status['error']
