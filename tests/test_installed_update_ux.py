"""An edit pause resumes verified bytes; failed discovery never advertises stale availability."""
import copy
import queue
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import installed_update as gui
from tests.test_installed_distribution import manifest


@pytest.fixture
def controller(tmp_path,monkeypatch):
    c=object.__new__(gui.InstalledVersionController)
    c.window=SimpleNamespace(service=Mock(),store=SimpleNamespace(root=tmp_path/'runtime'),
        after=Mock(),deiconify=Mock(),_closing=False,_update_pending=False)
    c.button=Mock();c.download_bar=Mock();c.results=queue.Queue();c.latest=dict(state='available',target=manifest())
    c.busy=False;c.queued=False;c.reserved=False;c.waiting=None;c.downloaded=None;c.last_check=time.monotonic();c._operation=0
    c.idle=lambda:True
    class Thread:
        def __init__(self,*,target,daemon):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(gui.threading,'Thread',Thread)
    monkeypatch.setattr(gui,'state_home',lambda:tmp_path/'state')
    monkeypatch.setattr(gui.update,'load_manifest',lambda:copy.deepcopy(manifest()))
    monkeypatch.setattr(gui.update,'current_release',lambda:dict(sequence=202610300001))
    monkeypatch.setattr(gui.messagebox,'showinfo',Mock())
    monkeypatch.setattr(gui.subprocess,'Popen',Mock())
    monkeypatch.setenv('APLUS_INSTALL_ROOT',str(tmp_path/'program'))
    def download(target,path,**kwargs):path.write_bytes(b'installer bytes')
    network=Mock(side_effect=download);monkeypatch.setattr(gui.update,'download',network)
    return c,network


@pytest.mark.parametrize('blocked',['form','chart'])
def test_paused_update_reuses_download_after_another_official_check(controller,monkeypatch,blocked):
    c,network=controller;pause=[True]
    monkeypatch.setattr(gui,'open_forms',lambda w:pause[0] if blocked=='form' else False)
    c.window._flush_embedded_charts=lambda:not pause[0]
    official=Mock(return_value=manifest());monkeypatch.setattr(gui.update,'load_manifest',official)
    c.begin();c.poll()
    assert c.downloaded and not c.busy and c.latest['state']=='available'
    pause[0]=False;c.manual_check();c.poll();c.poll()
    assert network.call_count==1 and official.call_count==2
    assert c.waiting and c.window._update_pending


def test_network_failure_clears_stale_available_button(controller,monkeypatch):
    c,network=controller
    def failed():raise TimeoutError('network')
    monkeypatch.setattr(gui.update,'load_manifest',failed)
    c.begin();c.poll()
    assert c.latest is None and not c.busy
    assert c.button.configure.call_args.kwargs['text']=='检查更新'


def test_corrupt_cached_package_is_downloaded_again(controller,monkeypatch,tmp_path):
    c,network=controller;folder=tmp_path/'cache';folder.mkdir();(folder/'setup.exe').write_bytes(b'corrupt')
    c.downloaded=(folder,manifest());monkeypatch.setattr(gui,'open_forms',lambda w:True)
    c.begin();c.poll()
    assert network.call_count==1 and (folder/'setup.exe').read_bytes()==b'installer bytes'


def test_retry_keeps_cancelled_transaction_separate_without_redownload(controller,monkeypatch,tmp_path):
    c,network=controller;folder=tmp_path/'old-attempt';folder.mkdir()
    (folder/'setup.exe').write_bytes(b'installer bytes')
    (folder/'request.json').write_text('{}');(folder/'cancelled.json').write_text('keep cancellation')
    c.downloaded=(folder,manifest());monkeypatch.setattr(gui,'open_forms',lambda w:False)
    c.begin();c.poll()
    assert c.waiting and c.waiting[0]!=folder and network.call_count==0
    assert not (c.waiting[0]/'cancelled.json').exists()
    assert (folder/'cancelled.json').read_text()=='keep cancellation'


def test_completed_download_supersedes_all_queued_percentages_in_one_poll(controller,monkeypatch):
    c,network=controller
    def downloading(target,path,progress):
        progress(0,100);progress(50,100);progress(100,100)
        path.write_bytes(b'installer bytes')
    network.side_effect=downloading
    monkeypatch.setattr(gui,'open_forms',lambda w:True)
    c.begin()
    c.button.configure.reset_mock()
    c.poll()
    assert c.downloaded and c.download_bar.pack_forget.called
    assert c.latest['state']=='available' and not c.busy
    assert not any('下载更新 ' in call.kwargs.get('text','') for call in c.button.configure.call_args_list)


def test_backlogged_progress_paints_only_latest_value(controller):
    c,_=controller;c.busy=True
    for percent in (0,15,35,70):c.results.put(('progress',(percent,100),False,c._operation))
    c.poll()
    assert c.results.empty()
    c.button.configure.assert_called_once_with(text='下载更新 70%')


def test_checksum_error_supersedes_queued_progress_and_late_notifications(controller):
    c,_=controller;c.busy=True;c.reserved=True;old=c._operation
    c.results.put(('progress',(50,100),False,old))
    c.results.put(('error','校验失败',False,old))
    c.poll()
    assert c.latest is None and not c.busy and not c.reserved
    c.window.service.release_code_update.assert_called_once()
    c.button.configure.assert_called_once_with(text='检查更新',bootstyle='secondary')
    c.results.put(('progress',(75,100),False,old));c.poll()
    assert c.button.configure.call_count==1


def test_retry_ignores_previous_attempt_download_and_error(controller,monkeypatch,tmp_path):
    c,_=controller;old=c._operation;c.fail('first attempt failed')
    c._operation+=1;c.busy=True
    for kind,value in [('progress',(90,100)),('error','old error'),('download',(tmp_path,manifest()))]:
        c.results.put((kind,value,False,old))
    c.results.put(('progress',(10,100),False,c._operation))
    c.button.configure.reset_mock();c.poll()
    c.button.configure.assert_called_once_with(text='下载更新 10%')
    assert c.waiting is None and c.downloaded is None
    gui.subprocess.Popen.assert_not_called()
