"""Selections survive IPC sharing faults and session boundaries; no real data."""
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import chart_embed as embed


def test_browser_view_preferences_do_not_require_analysis_save():
    from tests.test_tv_workspace_prototype import run_node
    assert run_node('chart_switch_regression.cjs')['ok']


@pytest.mark.parametrize('channel', ['control', 'navigation', 'live'])
@pytest.mark.parametrize('fault', [PermissionError, FileNotFoundError, json.JSONDecodeError])
def test_transient_notification_failure_keeps_switch_and_close_reader_alive(tmp_path, monkeypatch, channel, fault):
    session = tmp_path/'slot.json'
    paths = {name:session.with_suffix('.'+name+'.json') for name in ('control','navigation','live')}
    embed.atomic_json(paths['navigation'], dict(id='nav',token=3,date=None))
    embed.atomic_json(paths['live'], dict(id='old',token=1,series={}))
    bridge = SimpleNamespace(pending=None,active_token=1,failed_token=None,busy=False,close_requested=False)
    window = SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=lambda:True)),destroy=Mock())
    switched, commands, deliveries = [], [], []
    def evaluate(script):
        commands.append(script)
        if 'aplusApplyLiveBars' in script:deliveries.append((bridge.active_token,script))
        if 'aplusSwitchContext' in script:
            bridge.active_token=bridge.pending['token'];bridge.busy=False
            switched.append(bridge.active_token)
        return True
    window.evaluate_js=evaluate
    tick=0
    def fit():
        nonlocal tick
        tick+=1
        if tick in (1,2):
            embed.atomic_json(paths['control'],dict(token=tick+1,boot={'code':str(tick)}))
            os.utime(paths['control'],ns=(tick*1000000000,tick*1000000000))
        if tick==3:embed.atomic_json(paths['live'],dict(id='new',token=3,series={}))
        if tick==4:embed.atomic_json(paths['control'],dict(token=4,close=True))
        return tick<=4
    real_read=Path.read_text;failed=[]
    def read(path,*args,**kwargs):
        if path==paths[channel] and not failed:
            failed.append(True)
            if fault is json.JSONDecodeError:raise fault('changing notification','',0)
            raise fault('temporary sharing conflict')
        return real_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    monkeypatch.setattr(embed.time,'sleep',lambda _:None)
    embed.run_embedded_transport(window,bridge,session,0,SimpleNamespace(fit=fit))
    assert failed and switched[-1]==3 and bridge.close_requested
    window.destroy.assert_called_once()
    assert all(token==1 for token,call in deliveries if '"id": "old"' in call)
    assert any('"id": "new"' in call for call in commands)


def test_live_payload_retries_until_frontend_acknowledges(tmp_path, monkeypatch):
    session=tmp_path/'slot.json'
    embed.atomic_json(session.with_suffix('.live.json'),dict(id='same',token=2,series={}))
    bridge=SimpleNamespace(pending=None,active_token=2,failed_token=None,busy=False)
    evaluate=Mock(side_effect=[False,True])
    window=SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=lambda:True)),evaluate_js=evaluate)
    native=SimpleNamespace(fit=Mock(side_effect=[True,True,True,False]))
    monkeypatch.setattr(embed.time,'sleep',lambda _:None)
    embed.run_embedded_transport(window,bridge,session,0,native)
    assert evaluate.call_count==2


def test_rapid_selections_flush_old_drawings_and_activate_only_latest_request(tmp_path,monkeypatch):
    from tests.test_chart_embedded_workflow import setup
    from gui.chart_bridge import workspace_boot
    store,frame,boot=setup(tmp_path)
    pages=[];bridge=embed.ChartSessionBridge(store,boot,pages.append);bridge.active_token=1
    def incoming(code):
        return workspace_boot(store,dict(frame=frame,record={'source':'test','adjustment':'qfq'},code=code,timeframe='daily'))
    second,third=incoming('sz.000002'),incoming('sz.000003')
    session=tmp_path/'rapid.json';tick=0
    def fit():
        nonlocal tick
        tick+=1
        if tick in (1,2):
            embed.atomic_json(session.with_suffix('.control.json'),dict(token=tick+1,boot=second if tick==1 else third))
            os.utime(session.with_suffix('.control.json'),ns=(tick*1000000000,tick*1000000000))
        if tick==3:
            bridge.save_state(dict(objects=[dict(id='kept',type='trend',a=dict(index=0,value=9),b=dict(index=2,value=11))]),0,1)
            bridge.activate_context();bridge.busy=False
        return tick<=3
    window=SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=lambda:True)),evaluate_js=Mock(return_value=True))
    monkeypatch.setattr(embed.time,'sleep',lambda _:None)
    embed.run_embedded_transport(window,bridge,session,0,SimpleNamespace(fit=fit))
    assert bridge.active_token==3 and pages[-1]['symbol']=='sz.000003'
    assert len(pages)==1 and store.chart_document(boot['identity'])['drawings'][0]['id']=='kept'
    assert store.chart_document(third['identity'])['drawings']==[]


@pytest.mark.parametrize('channel',['focus','actions','status'])
def test_parent_reader_does_not_close_page_for_temporary_file_lock(tmp_path,monkeypatch,channel):
    host=embed.EmbeddedChartHost(Mock(),SimpleNamespace(root=tmp_path),on_error=Mock(),on_ready=Mock())
    host.parent.winfo_exists.return_value=True
    host.process=Mock();host.process.poll.return_value=None
    host.token=2;host._started=embed.time.monotonic()
    embed.atomic_json(host.status,dict(ready=True,token=1))
    real_read=Path.read_text
    def read(path,*args,**kwargs):
        if path==getattr(host,channel):raise PermissionError('reader held briefly')
        return real_read(path,*args,**kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(Path,'read_text',read)
        host._poll()
    host.on_error.assert_not_called();host.on_ready.assert_not_called()
    assert host._started is not None  # An old page's ACK cannot end the new timeout.
    embed.atomic_json(host.status,dict(ready=True,token=2));host._poll()
    host.on_ready.assert_called_once();assert host._started is None


@pytest.mark.skipif(os.name!='nt',reason='Windows real sharing violation')
def test_actual_exclusive_live_file_does_not_prevent_next_selection(tmp_path,monkeypatch):
    import ctypes
    from ctypes import wintypes
    session=tmp_path/'slot.json';live=session.with_suffix('.live.json')
    embed.atomic_json(live,dict(id='old',token=1))
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(live),0x80000000,0,None,3,0,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    bridge=SimpleNamespace(pending=None,active_token=1,failed_token=None,busy=False)
    def evaluate(_script):
        bridge.active_token=bridge.pending['token'];bridge.busy=False;return True
    window=SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=lambda:True)),evaluate_js=evaluate)
    tick=0
    def fit():
        nonlocal tick,handle
        tick+=1
        if tick==2:
            kernel.CloseHandle(handle);handle=None
            embed.atomic_json(session.with_suffix('.control.json'),dict(token=2,boot={}))
        return tick<=2
    monkeypatch.setattr(embed.time,'sleep',lambda _:None)
    try:embed.run_embedded_transport(window,bridge,session,0,SimpleNamespace(fit=fit))
    finally:
        if handle:kernel.CloseHandle(handle)
    assert bridge.active_token==2


@pytest.mark.parametrize('clock,expected',[
    ('2026-10-30T09:00',False),('2026-10-30T10:00',True),
    ('2026-10-30T11:45',False),('2026-10-30T14:00',True),
    ('2026-10-30T16:00',False),('2026-10-31T10:00',False)])
def test_symbol_first_read_is_independent_of_market_session(tmp_path,monkeypatch,clock,expected):
    from gui.chart_panel import ChartPanel
    from workbench import holding_quotes
    from workbench.live_minutes import CHINA
    monkeypatch.setattr(holding_quotes,'china_now',lambda:datetime.fromisoformat(clock).replace(tzinfo=CHINA))
    poller=SimpleNamespace(invalidate=Mock(),take=Mock(return_value=None),start=Mock(return_value=True),
                           busy=True,delay=8)
    panel=SimpleNamespace(_intraday_on=True,winfo_exists=lambda:True,_intraday_poller=poller,
        _code='sz.000002',_intraday_code='sz.000001',_intraday_loaded=True,_intraday_due=0,
        _hourly=None,_intraday_calendar_state=lambda:None,after=Mock(),_intraday_tick=Mock(),CLOSED_RETRY_SECONDS=60)
    ChartPanel._intraday_tick(panel)
    poller.invalidate.assert_called_once();poller.start.assert_called_once_with('sz.000002')
    # First history is loaded even when shut; subsequent refreshes follow session.
    panel._intraday_loaded=True;panel._intraday_due=0
    ChartPanel._intraday_tick(panel)
    assert poller.start.call_count==(2 if expected else 1)


def test_session_transitions_pause_and_resume_polling_without_blocking_selection(monkeypatch):
    from gui.chart_panel import ChartPanel
    from workbench import holding_quotes
    from workbench.live_minutes import CHINA
    poller=SimpleNamespace(take=lambda:None,start=Mock(return_value=True),delay=8)
    panel=SimpleNamespace(_intraday_on=True,winfo_exists=lambda:True,_intraday_poller=poller,
        _code='sz.000002',_intraday_code='sz.000002',_intraday_loaded=True,_intraday_due=0,
        _hourly=None,_intraday_calendar_state=lambda:None,after=Mock(),_intraday_tick=Mock(),CLOSED_RETRY_SECONDS=300)
    for clock,count in [('09:00',0),('09:31',1),('11:45',2),('13:01',3),('15:30',4)]:
        monkeypatch.setattr(holding_quotes,'china_now',lambda c=clock:datetime.fromisoformat('2026-10-30T'+c).replace(tzinfo=CHINA))
        panel._intraday_due=0;ChartPanel._intraday_tick(panel)
        assert poller.start.call_count==count
