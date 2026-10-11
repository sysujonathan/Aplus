"""Synthetic Git repos and isolated tray callbacks, never formal trading data."""
import os
import queue
import subprocess
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.main_window import AplusMainWindow
from gui.version_update import VersionController
from tests.test_version_update import api_for, git, repo
from workbench import version_update as v


def rename_target(repo, name):
    root, old, _ = repo
    git(root, 'switch', '-c', 'rename-server', old)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    git(root, 'mv', 'feature.py', name)
    git(root, 'commit', '-m', 'synthetic exact rename')
    target = git(root, 'rev-parse', 'HEAD')
    assert v.run_git(root, 'diff', '--name-status', old, target).startswith('R100\t')
    git(root, 'switch', 'main')
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, old, target


@pytest.mark.parametrize('name', ['ignored.txt', 'nested/保留 文件.log'])
@pytest.mark.parametrize('phase', ['before_check', 'after_check', 'during_probe'])
def test_renamed_target_never_overwrites_ignored_local_file(repo, tmp_path, name, phase):
    root, old, target = rename_target(repo, name)
    sentinel = root / name
    payload = b'preserved local file, not release code'
    old_code = (root / 'feature.py').read_bytes()

    def preserve():
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(payload)
        assert not v.local_version(root)['dirty']  # Git intentionally ignores it.

    if phase == 'before_check':
        preserve()
    plan = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    backup = tmp_path / 'backup.zip'
    if phase == 'before_check':
        assert not plan['can_update'] and '本地保留文件重名' in plan['message']
    else:
        assert plan['can_update']
        if phase == 'after_check':
            preserve()
        smoke = Mock(side_effect=lambda *args: preserve() if phase == 'during_probe' else None)
        with pytest.raises(v.UpdateError, match='本地保留文件重名'):
            v.apply_update(root, plan, backup, sys.executable, api=api_for(target), smoke=smoke)
        assert smoke.call_count == (1 if phase == 'during_probe' else 0)
    assert sentinel.read_bytes() == payload
    assert git(root, 'rev-parse', 'HEAD') == old
    assert (root / 'feature.py').read_bytes() == old_code
    if phase != 'during_probe':
        assert not backup.exists()


@pytest.mark.parametrize('name', ['ignored.txt', 'nested/保留 文件.log'])
def test_unobstructed_rename_still_updates_normally(repo, tmp_path, name):
    root, old, target = rename_target(repo, name)
    content = (root / 'feature.py').read_bytes()
    plan = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    assert plan['can_update']
    smoke = Mock()
    v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable, api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == target
    assert (root / name).read_bytes() == content and not (root / 'feature.py').exists()
    assert smoke.call_count == 2


def tray_window():
    window = SimpleNamespace(_closing=False, _update_pending=True,
        _tray_actions=queue.SimpleQueue(), _tray_icon=Mock(), toolbar=SimpleNamespace(_job_id=None),
        service=Mock(), after=Mock(), destroy=Mock(), deiconify=Mock(),
        state=Mock(), lift=Mock(), focus_force=Mock())
    for name in ('_poll_tray_actions', '_show_from_tray', '_exit_from_tray'):
        setattr(window, name, MethodType(getattr(AplusMainWindow, name), window))
    return window


def test_pending_quit_keeps_tray_poll_alive_and_accepts_show_then_exit_after_recovery():
    w = tray_window()
    w._tray_actions.put('quit')
    w._tray_actions.put('show')
    w._poll_tray_actions()
    assert not w._closing
    w.destroy.assert_not_called()
    w.deiconify.assert_not_called()
    w._tray_icon.stop.assert_not_called()
    w.after.assert_called_once_with(200, w._poll_tray_actions)
    w._update_pending = False  # Same reset as failed update preparation.
    w._tray_actions.put('show')
    w._poll_tray_actions()
    w.deiconify.assert_called_once()
    w.after.reset_mock()
    w._tray_actions.put('quit')
    w._poll_tray_actions()
    assert w._closing
    w.destroy.assert_called_once()
    w._tray_icon.stop.assert_called_once()
    w.after.assert_not_called()  # Only a real close ends the timer loop.


@pytest.mark.parametrize('failed_receipt', [False, True])
def test_handshake_failure_preserves_actual_tray_callbacks(tmp_path, monkeypatch, failed_receipt):
    from gui import version_update as ui
    w = tray_window()
    w._tray_actions.put('quit')
    w._poll_tray_actions()
    c = object.__new__(VersionController)
    c.window = w
    c.results = queue.SimpleQueue()
    c.pending_notice = None
    c.waiting = (tmp_path, 0)
    c.reserved = True
    if failed_receipt:
        v.write_json(tmp_path/'ready.json', {'ready': True})
        v.write_json(tmp_path/'receipt.json', {'state': 'failed'})
    monkeypatch.setattr(ui.messagebox, 'showerror', Mock())
    c.poll()
    assert not w._update_pending and not w._closing
    assert (tmp_path/'cancelled.json').exists()
    w.service.release_code_update.assert_called_once()
    assert any(call.args == (200, w._poll_tray_actions) for call in w.after.call_args_list)
    w.deiconify.reset_mock()
    w._tray_actions.put('show')
    w._poll_tray_actions()
    w.deiconify.assert_called_once()
    w._tray_actions.put('quit')
    w._poll_tray_actions()
    assert w._closing
    w.destroy.assert_called_once()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native tray timer regression')
@pytest.mark.parametrize('failed_receipt', [False, True])
def test_native_timer_recovers_after_update_failure(tmp_path, failed_receipt):
    script = r'''
import sys, queue
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
import ttkbootstrap as ttk
from gui.main_window import AplusMainWindow
from gui.version_update import VersionController
from workbench.version_update import write_json
w=ttk.Window(themename='darkly'); w.withdraw()
w._closing=False; w._update_pending=True; w._tray_actions=queue.SimpleQueue()
w._tray_icon=Mock(); w.toolbar=SimpleNamespace(_job_id=None)
w.service=Mock(); w.store=SimpleNamespace(root=Path(sys.argv[1]))
for name in ('_poll_tray_actions','_show_from_tray','_exit_from_tray'):
    setattr(w,name,MethodType(getattr(AplusMainWindow,name),w))
c=VersionController(w,w,root=Path(sys.argv[1]))
w.after_cancel(c.poll_id)  # Schedule this controller once, not a second poll loop.
c.waiting=(Path(sys.argv[1]),0); c.reserved=True
if sys.argv[2]=='True':
    write_json(c.waiting[0]/'ready.json',{'ready':True})
    write_json(c.waiting[0]/'receipt.json',{'state':'failed'})
checks=[]; failures=[]
def checked(label,callback):
    try: callback(); checks.append(label)
    except Exception as exc: failures.append((label,repr(exc))); w.destroy()
def pending():
    assert not w._closing and w._update_pending
    timers=w.tk.call('after','info')
    assert any('_poll_tray_actions' in str(w.tk.call('after','info',timer)) for timer in timers)
    w.after(10,lambda:checked('recover',recover))
def recover():
    c.poll()
    assert not w._update_pending and not w._closing
    w.service.release_code_update.assert_called_once()
    w.withdraw(); w._tray_actions.put('show')
    w.after(260,lambda:checked('shown',shown))
def shown():
    assert w.state()=='normal'
    w._tray_actions.put('quit')
w._tray_actions.put('quit'); w.after(0,w._poll_tray_actions)
w.after(20,lambda:checked('pending',pending))
w.after(6000,lambda:checked('deadline',lambda: (_ for _ in ()).throw(AssertionError('tray quit failed'))))
with patch('gui.version_update.messagebox.showerror'):
    w.mainloop()
assert not failures, failures
assert checks==['pending','recover','shown'] and w._closing, checks
w._tray_icon.stop.assert_called_once()
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(failed_receipt)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
