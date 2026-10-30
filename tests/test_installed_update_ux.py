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
    c.button=Mock();c.results=queue.Queue();c.latest=dict(state='available',target=manifest())
    c.busy=False;c.queued=False;c.reserved=False;c.waiting=None;c.downloaded=None;c.last_check=time.monotonic()
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
    def download(target,path):path.write_bytes(b'installer bytes')
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
