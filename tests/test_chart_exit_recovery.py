"""After transport failure, exiting still uses the original save/close guard."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from gui import chart_embed as embed,chart_shell as shell
from tests.test_chart_embedded_workflow import setup


@pytest.mark.parametrize('edited',[False,True])
@pytest.mark.parametrize('cancel_first',[False,True])
def test_recovery_closes_only_after_page_ack_and_preserves_real_edits(tmp_path,monkeypatch,edited,cancel_first):
    store,_,boot=setup(tmp_path/'runtime')
    bridge=embed.ChartSessionBridge(store,boot,Mock());bridge.active_token=7
    session=tmp_path/'slot.json'
    embed.atomic_json(session.with_suffix('.control.json'),dict(token=8,close=True,id='first'))
    closed=threading.Event();scheduled=[];flushed=[];ticks=[]
    class Thread:
        def __init__(self,*,target,**kwargs):self.target=target
        def start(self):scheduled.append(self.target)
    monkeypatch.setattr(shell.threading,'Thread',Thread)
    window=SimpleNamespace(events=SimpleNamespace(closed=closed),create_confirmation_dialog=Mock(return_value=False))
    def destroy():
        if guard.request():closed.set()
    window.destroy=destroy
    guard=shell.ChartCloseGuard(window,bridge)
    def evaluate(script):
        assert 'aplusFlushAndClose' in script
        flushed.append(True)
        if cancel_first and len(flushed)==1:
            bridge.cancel_close();return True
        if edited:
            bridge.save_state(dict(objects=[dict(id='kept',type='trend',a=dict(index=0,value=9),b=dict(index=2,value=11))]),0,7)
        bridge.finish_close();return True
    window.evaluate_js=evaluate
    def tick(_seconds):
        ticks.append(True);assert len(ticks)<=5,'recovery did not close'
        if scheduled:
            assert not closed.is_set()
            assert store.chart_document(boot['identity'])['revision']==0
            scheduled.pop(0)()
        elif cancel_first and not closed.is_set():
            assert len(flushed)==1  # An old request must not reopen the confirmation repeatedly.
            embed.atomic_json(session.with_suffix('.control.json'),dict(token=8,close=True,id='retry'))
    monkeypatch.setattr(embed.time,'sleep',tick)
    embed.run_embedded_close_recovery(window,bridge,session)
    assert closed.is_set() and bridge.close_token==8
    document=store.chart_document(boot['identity'])
    assert document['revision']==(1 if edited else 0)
    assert [item['id'] for item in document['drawings']]==(['kept'] if edited else [])
    assert window.create_confirmation_dialog.call_count==(1 if cancel_first else 0)


def test_retry_close_resends_request_without_changing_its_token(tmp_path):
    host=embed.EmbeddedChartHost(Mock(),SimpleNamespace(root=tmp_path))
    host.process=Mock();host.process.poll.return_value=None;host.token=7
    host.request_close();first=embed.read_notification(host.control)
    host.request_close();second=embed.read_notification(host.control)
    assert first['close'] and second['close'] and first['token']==second['token']==8
    assert first['id']!=second['id']


def test_close_recovery_ignores_selections_and_retries_a_locked_control(tmp_path,monkeypatch):
    session=tmp_path/'slot.json';control=session.with_suffix('.control.json')
    embed.atomic_json(control,dict(token=9,boot={'symbol':'sz.000002'}))
    bridge=SimpleNamespace(pending=None,activate_context=Mock());closed=threading.Event()
    window=SimpleNamespace(events=SimpleNamespace(closed=closed),destroy=Mock(side_effect=closed.set))
    real_read=embed.read_notification;calls=[]
    def read(path):
        calls.append(True)
        return None if len(calls)==1 else real_read(path)
    def tick(_seconds):
        if len(calls)==2:embed.atomic_json(control,dict(token=10,close=True,id='exit'))
        assert len(calls)<5
    monkeypatch.setattr(embed,'read_notification',read);monkeypatch.setattr(embed.time,'sleep',tick)
    embed.run_embedded_close_recovery(window,bridge,session)
    assert bridge.close_token==10
    window.destroy.assert_called_once();bridge.activate_context.assert_not_called()


def test_tray_exit_finishes_without_false_unsaved_warning_after_transport_fault(tmp_path,monkeypatch):
    from gui import main_window
    closed=threading.Event()
    bridge=SimpleNamespace(active_token=7,close_token=None,close_requested=False,pending=None)
    window=SimpleNamespace(events=SimpleNamespace(closed=closed))
    def destroy():
        if guard.request():closed.set()
    window.destroy=destroy
    guard=shell.ChartCloseGuard(window,bridge)
    def evaluate(script):
        assert 'aplusFlushAndClose' in script
        bridge._close_window()  # No edits: page acknowledges immediately, without a Store write.
        return True
    window.evaluate_js=evaluate
    host=embed.EmbeddedChartHost(Mock(),SimpleNamespace(root=tmp_path))
    host.process=SimpleNamespace(poll=lambda:0 if closed.is_set() else None)
    host.token=7
    parent=SimpleNamespace(chart=SimpleNamespace(embedded_hosts=lambda:[host]),_chart_exit_wait=False,
        update=lambda:embed.run_embedded_close_recovery(window,bridge,host.session),_resume_embedded_charts=Mock())
    warning=Mock();monkeypatch.setattr(main_window.messagebox,'showwarning',warning)
    assert main_window.AplusMainWindow._flush_embedded_charts(parent) is True
    assert closed.is_set() and not parent._chart_exit_wait
    warning.assert_not_called();parent._resume_embedded_charts.assert_not_called()
