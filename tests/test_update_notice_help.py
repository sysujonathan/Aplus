"""Optional release prompts and offline help use only isolated code/data."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import version_update as ui


@pytest.fixture
def controller(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    c = object.__new__(ui.VersionController)
    c.root = tmp_path
    c.waiting = c.notice = None
    c.busy = False
    c.pending_notice = dict(state='available', target='a' * 40, can_update=True)
    c.notified_targets = set()
    c.window = Mock()
    c.window.state.return_value = 'normal'
    c.window.service.cancel_flags = {}
    c.window.service._code_update_reserved = False
    c.window.service.store.rows.return_value = []
    c.show_notice = Mock()
    monkeypatch.setattr(ui, 'open_forms', lambda w: False)
    return c


def test_prompt_once_per_target_including_restart(controller):
    c = controller
    release = dict(c.pending_notice)
    c.maybe_show_notice()
    c.show_notice.assert_called_once_with(release)
    assert json.loads((c.root / '.git/aplus-update/notice.json').read_text())['target'] == release['target']
    c.pending_notice = release
    c.notified_targets.clear()  # simulate fresh session
    c.maybe_show_notice()
    assert c.show_notice.call_count == 1
    c.pending_notice = dict(release, target='b' * 40)
    c.maybe_show_notice()
    assert c.show_notice.call_count == 2
    c.window.service.reserve_code_update.assert_not_called()
    c.window.withdraw.assert_not_called()


@pytest.mark.parametrize('blocker', ['queued', 'finishing', 'reserved', 'hidden', 'edit', 'checking', 'helper', 'notice', 'offline'])
def test_prompt_waits_without_consuming_notice(controller, monkeypatch, blocker):
    c = controller
    if blocker == 'queued':
        c.window.service.store.rows.return_value = [{'id': 'queued'}]
    elif blocker == 'finishing':
        c.window.service.cancel_flags = {'job': object()}
    elif blocker == 'reserved':
        c.window.service._code_update_reserved = True
    elif blocker == 'hidden':
        c.window.state.return_value = 'withdrawn'
    elif blocker == 'edit':
        monkeypatch.setattr(ui, 'open_forms', lambda w: True)
    elif blocker == 'checking':
        c.busy = True
    elif blocker == 'helper':
        c.waiting = (c.root, 10)
    elif blocker == 'notice':
        c.notice = object()
    else:
        c.window.service = None
    c.maybe_show_notice()
    c.show_notice.assert_not_called()
    assert c.pending_notice is not None
    assert not (c.root / '.git/aplus-update/notice.json').exists()


def test_idle_after_scan_shows_deferred_prompt(controller):
    c = controller
    c.window.service.cancel_flags = {'job': object()}
    c.maybe_show_notice()
    c.window.service.cancel_flags.clear()
    c.maybe_show_notice()
    c.show_notice.assert_called_once()


def test_metadata_failure_does_not_break_prompt_or_repeat_in_session(controller, monkeypatch):
    c = controller
    release = dict(c.pending_notice)
    monkeypatch.setattr(ui, 'write_json', Mock(side_effect=OSError('read-only')))
    c.maybe_show_notice()
    c.pending_notice = release
    c.maybe_show_notice()
    c.show_notice.assert_called_once()


def test_manual_check_closes_own_notice_before_details(controller):
    c = controller
    dialog = c.notice = Mock()
    c.start_check = Mock()
    c.manual_check()
    dialog.destroy.assert_called_once()
    assert c.notice is None
    c.start_check.assert_called_once_with(manual=True)


def test_cached_release_is_freshly_confirmed_without_another_user_click():
    c = object.__new__(ui.VersionController)
    c.window = Mock(_closing=False)
    c.window.after.return_value = 'next-poll'
    c.results = queue.SimpleQueue()
    c.results.put(dict(state='available', target='a' * 40, can_update=False,
                       block_reason='cached'))
    c.busy = True
    c.latest = None
    c.pending_notice = dict(state='available')
    c.waiting = None
    c.button = Mock()
    c.start_check = Mock()
    c.maybe_show_notice = Mock()
    c.poll()
    c.start_check.assert_called_once_with(manual=True, show_result=False)
    assert c.pending_notice is None
    c.button.configure.assert_called_with(text='正在确认新版…', bootstyle='warning')
    c.maybe_show_notice.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native controls')
def test_native_prompt_and_help_buttons_are_optional_and_leave_chart_height(tmp_path):
    script = r'''
import sys
from pathlib import Path
import tkinter as tk
from unittest.mock import Mock
from workbench.store import Store
from workbench.service import Service
from gui.main_window import AplusMainWindow
from gui.usage_help import show_usage
from gui import version_update as ui
s=Store(Path(sys.argv[1])/'isolated'); service=Service(s)
w=AplusMainWindow(service=service,store=s)
# This test drives synthetic release notices. Keep the unrelated delayed online
# checker out of it, even when loading the offline documents takes over 4 seconds.
w.after_cancel(w.version_controller.startup_check_id)
w.version_controller.startup_check_id=None
w.update()
c=w.version_controller; c.root=Path(sys.argv[1]); (c.root/'.git').mkdir()
height=w.chart.winfo_height()
assert w.usage_button.master is w.section_nav
w.usage_button.invoke(); w.update()
help_window=w._usage_window
assert help_window.winfo_exists()
assert show_usage(w) is help_window
tabs=help_window.winfo_children()[0]
assert [tabs.tab(t,'text') for t in tabs.tabs()]==['快速上手','详细说明','软件更新','更新日志']
assert '更新行情 ≠ 更新软件' in tabs.winfo_children()[0].winfo_children()[0].get('1.0','end')
release=dict(state='available',target='a'*40,can_update=True,local=dict(head='b'*40))
c.running=dict(head='b'*40,version='2026.10.10.29',channel='main')  # isolated running build
c.pending_notice=release
c.maybe_show_notice(); assert c.notice is None  # help is an open form
help_window.destroy()
c.maybe_show_notice(); w.update()
assert c.notice.winfo_exists() and c.notice.grab_current() is None
assert w.chart.winfo_height()==height
def find_button(parent, label):
    for child in parent.winfo_children():
        if child.winfo_class()=='TButton' and child.cget('text')==label: return child
        found=find_button(child,label)
        if found is not None: return found
    return None
find_button(c.notice,'稍后').invoke(); assert c.notice is None
c.pending_notice=release; c.maybe_show_notice(); assert c.notice is None
release=dict(release,target='c'*40)
c.pending_notice=release; c.maybe_show_notice()
c.begin_update=Mock()
find_button(c.notice,'立即更新并重启').invoke()
assert c.notice is None; c.begin_update.assert_called_once_with(release)
release=dict(release,target='d'*40,can_update=False,message='开发分支需人工核对')
c.pending_notice=release; c.maybe_show_notice()
blocked=find_button(c.notice,'暂不能自动更新')
assert str(blocked.cget('state')) == 'disabled'
blocked.invoke(); assert c.notice.winfo_exists() and c.begin_update.call_count==1
find_button(c.notice,'稍后').invoke()
assert w.chart.winfo_height()==height
assert not s.rows('SELECT id FROM jobs')
w.destroy(); service.pool.shutdown(wait=True)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_help_docs_has_clear_message_and_no_blank_window(tmp_path, monkeypatch):
    from gui import usage_help as help_ui
    monkeypatch.setattr(help_ui.messagebox, 'showinfo', Mock())
    window = SimpleNamespace()
    assert help_ui.show_usage(window, root=tmp_path) is None
    assert not hasattr(window, '_usage_window')
    assert '说明文件缺失' in help_ui.messagebox.showinfo.call_args.args[1]
