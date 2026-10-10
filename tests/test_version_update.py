"""All repositories, runtime folders and update processes here are synthetic."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from workbench import version_update as v
from workbench.service import Service
from workbench.store import Store


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'code'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Isolated test')
    git(root, 'config', 'core.autocrlf', 'false')
    git(root, 'remote', 'add', 'origin', v.REMOTE)
    (root / '.gitignore').write_text('runtime/\n.venv/\n*.log\nignored.txt\n', encoding='utf-8')
    (root / 'feature.py').write_text('value = 1\n', encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'old code')
    old = git(root, 'rev-parse', 'HEAD')
    (root / 'feature.py').write_text('value = 2\n', encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Aplus new code')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'branch', '-m', 'server')
    git(root, 'switch', '-c', 'main', old)
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, old, target


def api_for(target, conclusion='success', extra=()):
    def api(path):
        if path == '/branches/main':
            return {'commit': {'sha': target}}
        return {'workflow_runs': [dict(id=10, head_sha=target, head_branch='main', event='push',
                                      name='A acceptance', path='.github/workflows/checks.yml',
                                      status='completed', conclusion=conclusion), *extra]}
    return api


def plan_for(repo):
    root, old, target = repo
    return v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)


def test_check_is_readonly_for_code_and_runtime(repo):
    root, old, target = repo
    runtime = root / 'runtime'
    runtime.mkdir()
    (runtime / 'personal.db').write_bytes(b'private data sentinel')
    plan = plan_for(repo)
    assert plan['state'] == 'available' and plan['can_update']
    assert 'Aplus new code' in plan['summary']
    assert git(root, 'rev-parse', 'HEAD') == old
    assert (root / 'feature.py').read_text() == 'value = 1\n'
    assert (runtime / 'personal.db').read_bytes() == b'private data sentinel'


def test_auto_checks_once_per_shanghai_day_and_manual_bypasses(repo, monkeypatch):
    root, _, target = repo
    api = Mock(side_effect=api_for(target))
    monkeypatch.setattr(v, 'shanghai_day', lambda: '2026-10-10')
    first = v.check_version(root, api=api, fetch=lambda sha: None)
    assert first['can_update']
    for _ in range(4):
        assert not v.check_version(root, api=api)['can_update']
    assert api.call_count == 2
    v.check_version(root, force=True, api=api, fetch=lambda sha: None)
    assert api.call_count == 4
    monkeypatch.setattr(v, 'shanghai_day', lambda: '2026-10-11')
    v.check_version(root, api=api, fetch=lambda sha: None)
    assert api.call_count == 6


def test_offline_failures_do_not_retry_every_scan(repo):
    root, old, _ = repo
    api = Mock(side_effect=TimeoutError())
    first = v.check_version(root, api=api)
    second = v.check_version(root, api=api)
    assert first['state'] == second['state'] == 'unavailable'
    assert '不影响行情' in second['message']
    assert api.call_count == 1 and git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', None])
def test_main_ci_must_pass_for_exact_head(repo, conclusion):
    root, _, target = repo
    result = v.check_version(root, force=True, api=api_for(target, conclusion), fetch=Mock())
    assert result['state'] == 'pending' and not result['can_update']


@pytest.mark.parametrize('override', [dict(head_sha='a'*40), dict(head_branch='feature'),
                                      dict(event='pull_request'), dict(name='Unrelated'),
                                      dict(path='.github/workflows/other.yml'), dict(status='in_progress')])
def test_other_ci_cannot_certify_main(override):
    target = 'b'*40
    run = dict(id=99, head_sha=target, head_branch='main', event='push', name='A acceptance',
               path='.github/workflows/checks.yml', status='completed', conclusion='success')
    run.update(override)
    assert not v.acceptance(target, lambda path: {'workflow_runs': [run]})


def test_latest_failed_rerun_overrides_earlier_success(repo):
    _, _, target = repo
    newer = dict(id=11, head_sha=target, head_branch='main', event='push', name='A acceptance',
                 path='.github/workflows/checks.yml', status='completed', conclusion='failure')
    assert not v.acceptance(target, api_for(target, extra=[newer]))


@pytest.mark.parametrize('mutation', ['tracked', 'untracked', 'branch', 'ahead', 'diverged', 'origin'])
def test_unsafe_local_versions_are_never_overwritten(repo, mutation):
    root, old, target = repo
    if mutation == 'tracked':
        (root / 'feature.py').write_text('my modification\n')
    elif mutation == 'untracked':
        (root / 'personal.txt').write_text('my addition')
    elif mutation == 'branch':
        git(root, 'switch', '-c', 'my-dev-branch')
    elif mutation in {'ahead', 'diverged'}:
        if mutation == 'ahead':
            git(root, 'merge', '--ff-only', target)
        (root / 'local.py').write_text('unmerged = True\n')
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'local unmerged fix')
    else:
        git(root, 'remote', 'set-url', 'origin', 'https://example.invalid/private.git')
    before = git(root, 'rev-parse', 'HEAD')
    before_file = (root / 'feature.py').read_bytes()
    if mutation == 'origin':
        with pytest.raises(v.UpdateError, match='约定'):
            plan_for(repo)
    else:
        result = plan_for(repo)
        assert not result['can_update']
        if mutation == 'ahead':
            assert result['state'] == 'ahead'
        if mutation == 'diverged':
            assert result['state'] == 'diverged'
    assert git(root, 'rev-parse', 'HEAD') == before
    assert (root / 'feature.py').read_bytes() == before_file


def test_cached_safe_result_cannot_authorize_after_local_edit(repo):
    root, _, _ = repo
    assert plan_for(repo)['can_update']
    (root / 'feature.py').write_text('local_edit = True\n')
    cached = v.check_version(root, api=Mock(side_effect=AssertionError('no network')))
    assert cached['local']['dirty'] and not cached['can_update']


@pytest.mark.parametrize('path', ['workbench/store.py', 'requirements-lock.txt', 'frozen_manifest.json',
                                'core/new.py', 'runtime/private.txt', '.env', 'ignored.txt'])
def test_sensitive_changes_require_manual_update(repo, path):
    root, old, target = repo
    git(root, 'switch', 'server')
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('new')
    git(root, 'add', '-f', path)
    git(root, 'commit', '-m', 'restricted target')
    new = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main')
    if path == 'ignored.txt':
        (root / path).write_text('preserved local ignored data')
    git(root, 'update-ref', 'refs/aplus-update/main', new)
    result = v.check_version(root, force=True, api=api_for(new), fetch=lambda sha: None)
    assert not result['can_update']
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('name', ['../outside.py', '/absolute.py', 'runtime/private.py', 'C:/escape.py',
                                'nested\\escape.py'])
def test_unsafe_zip_paths_are_rejected(tmp_path, name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(name.replace('\\', '/'), 'bad')
    packed = data.getvalue()
    if '\\' in name:
        packed = packed.replace(name.replace('\\', '/').encode(), name.encode())
    stage = tmp_path / 'stage'
    with pytest.raises(v.UpdateError, match='路径不安全'):
        v.extract_archive(packed, stage)
    assert not stage.exists()


def test_successful_ff_update_preserves_ignored_runtime_and_creates_code_backup(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    (root / 'runtime').mkdir()
    sentinel = root / 'runtime' / 'personal.db'
    sentinel.write_bytes(b'never roll back data')
    smoke = Mock()
    backup = tmp_path / 'old-code.zip'
    original_blob = v.run_git(root, 'show', old + ':feature.py', binary=True)
    v.apply_update(root, plan, backup, sys.executable, api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == target
    assert git(root, 'branch', '--show-current') == 'main'
    assert sentinel.read_bytes() == b'never roll back data'
    assert smoke.call_count == 2
    with zipfile.ZipFile(backup) as archive:
        assert archive.read('feature.py') == original_blob
        assert not any(name.startswith('runtime/') for name in archive.namelist())


def test_failed_preflight_leaves_code_and_data_untouched(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    with pytest.raises(v.UpdateError):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable, api=api_for(target),
                       smoke=Mock(side_effect=v.UpdateError('preflight failed')))
    assert git(root, 'rev-parse', 'HEAD') == old


def test_failed_postflight_restores_old_code_not_data(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    smoke = Mock(side_effect=[None, RuntimeError('postflight failed')])
    with pytest.raises(v.UpdateError, match='回到旧代码'):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable, api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == old
    assert git(root, 'branch', '--show-current') == ''


@pytest.mark.parametrize('changed', ['local', 'main', 'ci'])
def test_changes_after_confirmation_are_rejected(repo, tmp_path, changed):
    root, old, target = repo
    plan = plan_for(repo)
    api = api_for(target)
    if changed == 'local':
        (root / 'feature.py').write_text('user edit')
    elif changed == 'main':
        api = api_for('c'*40)
    else:
        api = api_for(target, conclusion='failure')
    with pytest.raises(v.UpdateError):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable, api=api, smoke=Mock())
    assert git(root, 'rev-parse', 'HEAD') == old


def test_local_edit_during_probe_is_not_lost(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    def smoke(*args):
        (root / 'feature.py').write_text('edited while probe runs')
    with pytest.raises(v.UpdateError, match='已经变化'):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable, api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == old
    assert (root / 'feature.py').read_text() == 'edited while probe runs'


def test_service_reservation_blocks_all_jobs_without_database_mutation(tmp_path):
    store = Store(tmp_path/'isolated')
    service = Service(store)
    service.reserve_code_update()
    try:
        with pytest.raises(ValueError, match='准备版本更新'):
            service.submit('universe', {})
        with pytest.raises(ValueError):
            service.reserve_code_update()
        assert store.rows('SELECT * FROM jobs') == []
    finally:
        service.release_code_update()
        service.pool.shutdown(wait=True)
    assert not service._code_update_reserved


def test_update_waits_until_worker_finishes_final_writes(tmp_path):
    store = Store(tmp_path/'isolated')
    service = Service(store)
    service.cancel_flags['finishing'] = threading.Event()
    try:
        with pytest.raises(ValueError, match='尚未结束'):
            service.reserve_code_update()
        assert not service._code_update_reserved
    finally:
        service.cancel_flags.clear()
        service.pool.shutdown(wait=True)


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_update_rejects_durable_active_jobs(tmp_path, status):
    store = Store(tmp_path/'isolated')
    service = Service(store)
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('job', 'sync', status, 'now', '{}'))
    try:
        with pytest.raises(ValueError, match='尚未结束'):
            service.reserve_code_update()
        assert store.rows('SELECT status FROM jobs')[0]['status'] == status
    finally:
        service.pool.shutdown(wait=True)


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
@pytest.mark.parametrize('allowed', [False, True])
def test_oneclick_partial_data_uses_explicit_scan_readiness(source, allowed):
    from gui.toolbar import ToolBar
    toolbar = Mock()
    toolbar._job_kind = '更新行情'
    toolbar._job_started_at = None
    toolbar._auto_started_at = None
    toolbar._auto_scan_after_sync = True
    toolbar._auto_chain_cancelled = False
    toolbar._on_job_finished = None
    report = dict(source=source, scan_readiness=dict(scan_allowed=allowed))
    ToolBar._finish_job(toolbar, 'partial', result_json=json.dumps(report))
    assert toolbar.after_idle.call_count == int(allowed)


@pytest.mark.parametrize('kind', ['sync', 'scan'])
def test_only_successful_scan_submission_triggers_hint_after_task_starts(kind):
    from gui.toolbar import ToolBar
    toolbar = Mock()
    order = []
    toolbar.service.submit.side_effect = lambda *args: order.append('submit') or 'job'
    toolbar._on_scan_started = lambda: order.append('version')
    ToolBar._submit_job(toolbar, kind, '扫描策略' if kind == 'scan' else '更新行情', {})
    assert order == (['submit', 'version'] if kind == 'scan' else ['submit'])


def test_failed_scan_submission_does_not_check_version():
    from gui.toolbar import ToolBar
    toolbar = Mock()
    toolbar.service.submit.side_effect = ValueError('busy')
    toolbar._on_scan_started = Mock()
    ToolBar._submit_job(toolbar, 'scan', '扫描策略', {})
    toolbar._on_scan_started.assert_not_called()


def test_hint_exception_cannot_fail_or_cancel_submitted_scan():
    from gui.toolbar import ToolBar
    toolbar = Mock()
    toolbar.service.submit.return_value = 'accepted'
    toolbar._on_scan_started = Mock(side_effect=RuntimeError('checker unavailable'))
    ToolBar._submit_job(toolbar, 'scan', '扫描策略', {})
    assert toolbar._job_id == 'accepted'
    toolbar.after.assert_called_once()
    toolbar.service.cancel.assert_not_called()


def test_prepared_helper_pins_runtime_and_leaves_git_version_untouched(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    launched = Mock()
    runtime = tmp_path / 'preserved-personal-runtime'
    folder = v.prepare_worker(root, plan, runtime, python=sys.executable, spawn=launched)
    request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
    assert request['runtime'] == str(runtime.resolve())
    assert request['plan']['target'] == target
    assert (folder/'worker.py').read_bytes() == Path(v.__file__).read_bytes()
    assert git(root, 'rev-parse', 'HEAD') == old
    assert not runtime.exists()
    with pytest.raises(v.UpdateError, match='保护锁'):
        v.prepare_worker(root, plan, runtime)


@pytest.mark.skipif(os.name != 'nt', reason='Windows update handshake')
@pytest.mark.parametrize('scenario', ['success', 'parent_running', 'cancelled', 'preflight_failed',
                                     'other_instance', 'startup_failed', 'startup_unconfirmed'])
def test_worker_waits_for_old_process_and_confirms_actual_startup(repo, tmp_path, monkeypatch, scenario):
    root, old, target = repo
    plan = plan_for(repo)
    folder = v.metadata_dir(root) / 'synthetic-worker'
    folder.mkdir(parents=True)
    lock = folder.parent/'update.lock'
    lock.touch()
    request_path = folder/'request.json'
    v.write_json(request_path, dict(root=str(root), plan=plan, runtime=str(tmp_path/'private-runtime'),
                                    python=sys.executable, parent_pid=12345))
    if scenario == 'cancelled':
        v.write_json(folder/'cancelled.json', {'cancelled': True})
    order = []
    k = SimpleNamespace(OpenProcess=Mock(return_value=111), CloseHandle=Mock(),
                        CreateMutexW=Mock(return_value=222),
                        GetLastError=Mock(return_value=183 if scenario == 'other_instance' else 0),
                        WaitForSingleObject=Mock(side_effect=lambda *args: order.append('wait') or
                                                (258 if scenario == 'parent_running' else 0)))
    monkeypatch.setattr(v.ctypes, 'windll', SimpleNamespace(kernel32=k, user32=SimpleNamespace(MessageBoxW=Mock())))
    def apply(*args):
        order.append('apply')
        if scenario == 'preflight_failed':
            raise v.UpdateError('preflight failed; old code retained')
    applied = Mock(side_effect=apply)
    monkeypatch.setattr(v, 'apply_update', applied)
    monkeypatch.setattr(v, 'require_web_closed', Mock())
    rolled_back = Mock()
    monkeypatch.setattr(v, 'rollback', rolled_back)
    monkeypatch.setattr(v, 'run_git', lambda *args, **kwargs: old)
    monkeypatch.setattr(v, 'local_version', lambda *args: dict(head=old, dirty=False, branch='main'))
    def launch(*args, **kwargs):
        order.append('launch')
        ack = kwargs['env'].get('APLUS_UPDATE_ACK')
        if scenario == 'success' and ack:
            v.write_json(ack, {'started': True})
        return SimpleNamespace(poll=lambda: 1 if scenario == 'startup_failed' and ack else None)
    launched = Mock(side_effect=launch)
    monkeypatch.setattr(v.subprocess, 'Popen', launched)
    if scenario == 'startup_unconfirmed':
        stamps = iter([0, 100])
        monkeypatch.setattr(v.time, 'monotonic', lambda: next(stamps))
    v.worker(request_path)
    receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    assert order[0] == 'wait'
    assert not lock.exists()
    if scenario == 'success':
        assert order == ['wait', 'apply', 'launch']
        assert receipt['state'] == 'completed'
        assert launched.call_args.kwargs['env']['A_WORKBENCH_HOME'] == str(tmp_path/'private-runtime')
    elif scenario in {'parent_running', 'cancelled', 'other_instance'}:
        applied.assert_not_called()
        assert receipt['state'] == 'failed'
        rolled_back.assert_not_called()
    elif scenario == 'startup_failed':
        rolled_back.assert_called_once_with(root, target, old)
        assert launched.call_count == 2 and receipt['state'] == 'failed'
    elif scenario == 'startup_unconfirmed':
        assert receipt['state'] == 'unconfirmed'
        rolled_back.assert_not_called()
        assert launched.call_count == 1
    else:
        assert receipt['state'] == 'failed' and launched.call_count == 1


def test_ui_refuses_update_when_edit_form_is_open(tmp_path, monkeypatch):
    from gui import version_update as ui
    c = object.__new__(ui.VersionController)
    c.window = Mock()
    c.reserved = False
    monkeypatch.setattr(ui, 'open_forms', lambda w: True)
    monkeypatch.setattr(ui.messagebox, 'showinfo', Mock())
    c.begin_update({'can_update': True})
    c.window.service.reserve_code_update.assert_not_called()
    c.window.withdraw.assert_not_called()


def test_ui_preparation_failure_releases_reservation_and_restores_window(tmp_path, monkeypatch):
    from gui import version_update as ui
    c = object.__new__(ui.VersionController)
    c.window = Mock()
    c.root = tmp_path
    c.reserved = False
    monkeypatch.setattr(ui, 'open_forms', lambda w: False)
    monkeypatch.setattr(ui, 'require_web_closed', Mock())
    monkeypatch.setattr(ui, 'prepare_worker', Mock(side_effect=v.UpdateError('launch failed')))
    monkeypatch.setattr(ui.messagebox, 'showinfo', Mock())
    c.begin_update({'can_update': True})
    c.window.service.reserve_code_update.assert_called_once()
    c.window.service.release_code_update.assert_called_once()
    c.window.deiconify.assert_called_once()
    assert c.window._update_pending is False


@pytest.mark.parametrize('failed_receipt', [False, True])
def test_ui_handshake_timeout_revokes_permission_before_restoring_window(tmp_path, monkeypatch, failed_receipt):
    from gui import version_update as ui
    c = object.__new__(ui.VersionController)
    c.window = Mock(_closing=False)
    c.results = __import__('queue').SimpleQueue()
    c.waiting = (tmp_path, 0)
    c.reserved = True
    if failed_receipt:
        v.write_json(tmp_path/'ready.json', {'ready': True})
        v.write_json(tmp_path/'receipt.json', {'state': 'failed'})
    monkeypatch.setattr(ui.messagebox, 'showerror', Mock())
    c.poll()
    assert (tmp_path/'cancelled.json').exists()
    c.window._close_for_version_update.assert_not_called()
    c.window.service.release_code_update.assert_called_once()


def test_native_probe_uses_new_isolated_runtime_not_formal_environment(tmp_path, monkeypatch):
    captured = {}
    def fake_run(args, **kwargs):
        captured.update(kwargs)
        assert args[1] == '-I'
        assert kwargs['env']['A_WORKBENCH_HOME'] != str(tmp_path/'formal')
        assert 'APLUS_UPDATE_ACK' not in kwargs['env']
        return SimpleNamespace(returncode=0)
    monkeypatch.setenv('A_WORKBENCH_HOME', str(tmp_path/'formal'))
    monkeypatch.setenv('APLUS_UPDATE_ACK', str(tmp_path/'bad-ack'))
    monkeypatch.setattr(v.subprocess, 'run', fake_run)
    v.probe(tmp_path, sys.executable)
    assert not (tmp_path/'formal').exists()


def test_listening_web_port_prevents_code_update():
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(v.UpdateError, match='Web工作台端口'):
            v.require_web_closed(listener.getsockname()[1])


def test_uncertain_web_probe_does_not_allow_update(monkeypatch):
    monkeypatch.setattr(v.socket, 'create_connection', Mock(side_effect=TimeoutError()))
    with pytest.raises(v.UpdateError, match='无法确认'):
        v.require_web_closed()


def test_closed_web_port_allows_update(monkeypatch):
    monkeypatch.setattr(v.socket, 'create_connection', Mock(side_effect=ConnectionRefusedError()))
    v.require_web_closed()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native desktop')
def test_native_button_and_background_check_do_not_change_chart_height(tmp_path):
    script = r'''
import sys, threading, time
from pathlib import Path
from workbench.store import Store
from workbench.service import Service
from gui.main_window import AplusMainWindow
from gui.version_update import open_forms
import tkinter as tk
s=Store(Path(sys.argv[1])/'isolated')
service=Service(s)
w=AplusMainWindow(service=service,store=s)
w.update()
c=w.version_controller
assert c.button.master is w.section_nav
assert w.toolbar._on_scan_started == c.scan_hint
height=w.chart.winfo_height()
started=threading.Event(); done=threading.Event()
def checker(*args,**kwargs):
    started.set(); done.wait(3)
    return dict(state='available', can_update=False, message='synthetic')
c.checker=checker
before=time.monotonic(); c.scan_hint()
assert time.monotonic()-before < .5
assert started.wait(2)
for _ in range(3): w.update(); time.sleep(.04)
assert w.chart.winfo_height()==height
done.set()
deadline=time.monotonic()+3
while c.busy and time.monotonic()<deadline: w.update(); time.sleep(.02)
assert not c.busy and c.button.cget('text')=='有新版 · 查看'
assert w.chart.winfo_height()==height
assert not open_forms(w)
dialog=tk.Toplevel(w); assert open_forms(w); dialog.destroy()
w.destroy(); service.pool.shutdown(wait=True)
'''
    p = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                       cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45)
    assert p.returncode == 0, p.stdout + p.stderr
