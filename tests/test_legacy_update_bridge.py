"""Only disposable repositories, runtimes and subprocesses are used here."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

from workbench import version_update as v
from tools import legacy_update_bootstrap as boot
from tests.test_version_update import repo, git, api_for, plan_for

ROOT = Path(__file__).resolve().parents[1]
CERT = 'tests/fixtures/tls/emsign-ecc-root-g3.pem'


@pytest.fixture
def short_bridge_root():
    # Windows without LongPathsEnabled cannot install ONNX under pytest's
    # deeply nested generated test name. Match the short daily-install layout.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='au-') as folder:
        yield Path(folder)


@pytest.mark.parametrize('kind', ['approved', 'changed', 'private-key', 'other-pem', 'user-cert', 'database'])
def test_certificate_exception_is_exact_path_and_bytes(repo, kind):
    root, old, _ = repo
    git(root, 'switch', '-c', 'certificate-release')
    name = {'other-pem': 'tests/fixtures/tls/other.pem', 'user-cert': 'user.pem',
            'database': 'user.sqlite3'}.get(kind, CERT)
    path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((ROOT/CERT).read_bytes() if kind not in {'changed', 'private-key', 'database'}
                     else b'-----BEGIN PRIVATE KEY-----\nnot a public certificate\n')
    git(root, 'add', '-f', name); git(root, 'commit', '-m', 'candidate fixture')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', target)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] is (kind == 'approved')
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', None])
def test_bootstrap_requires_exact_successful_main(conclusion):
    with pytest.raises(RuntimeError, match='验收'):
        boot.accepted_target(api_for('a'*40, conclusion))


def test_hash_lock_has_every_pinned_requirement_and_manifest_digest():
    lock = [l for l in (ROOT/'requirements-lock.txt').read_text().splitlines() if '==' in l and not l.startswith('#')]
    hashed = [l.split()[0] for l in (ROOT/'requirements-hashes.txt').read_text().splitlines() if '==' in l]
    assert hashed == lock
    manifest = json.loads((ROOT/v.RELEASE_INSTALLATION).read_text())
    assert hashlib.sha256((ROOT/'requirements-hashes.txt').read_bytes()).hexdigest() == manifest['requirements_hashes_sha256']


def test_maintenance_bridge_pins_exact_bootstrap_target(tmp_path, monkeypatch):
    from tools import maintenance_update as m
    from workbench.store import Store
    from contextlib import nullcontext
    store = Store(tmp_path/'runtime')
    monkeypatch.setattr(m, 'application_mutex', nullcontext)
    monkeypatch.setattr(m, 'require_web_closed', Mock())
    monkeypatch.setattr(m, 'check_version', Mock(return_value={'can_update': True, 'target': 'b'*40}))
    worker = Mock(); monkeypatch.setattr(m, 'prepare_worker', worker)
    before = store.path.read_bytes()
    with pytest.raises(v.UpdateError, match='发生变化'):
        m.prepare(tmp_path/'code', store.root, sys.executable, expected_target='a'*40)
    worker.assert_not_called()
    assert store.path.read_bytes() == before


def test_bootstrap_gets_official_source_and_passes_original_data_home(repo, tmp_path, monkeypatch):
    import io
    import zipfile
    root, _, target = repo
    runtime = tmp_path/'original-runtime'; runtime.mkdir()
    (runtime/'workbench.sqlite3').write_bytes(b'never open or rewrite in bootstrap')
    monkeypatch.setenv('A_WORKBENCH_HOME', str(runtime))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('tools/legacy_update_bootstrap.py', '# published marker')
        z.writestr('tools/maintenance_update.py', '# approved helper')
    def fake_git(_root, *args, **kwargs):
        if args[:3] == ('remote', 'get-url', 'origin'): return v.REMOTE
        if args[0] == 'rev-parse': return target
        if args[0] == 'archive': return archive.getvalue()
        return ''
    monkeypatch.setattr(boot, 'git', fake_git)
    runner = Mock(return_value=type('Result', (), {'returncode': 0, 'stdout': json.dumps({'prepared':'receipt'}), 'stderr':''})())
    monkeypatch.setattr(boot.subprocess, 'run', runner)
    assert boot.transition(root, api_for(target)) == 'receipt'
    command = runner.call_args.args[0]
    assert command[command.index('--runtime')+1] == str(runtime.resolve())
    assert command[command.index('--expected-target')+1] == target
    assert Path(command[2]).name == 'maintenance_update.py'
    assert Path(command[2]).parent.parent != root
    assert (runtime/'workbench.sqlite3').read_bytes() == b'never open or rewrite in bootstrap'


@pytest.mark.skipif(os.name != 'nt', reason='Windows double-click launcher')
def test_double_click_transition_launcher_routes_selected_install(tmp_path):
    root = tmp_path/'selected'; root.mkdir()
    py = root/'.venv'/'Scripts'/'python.exe'; py.parent.mkdir(parents=True)
    # Use a symlink-free wrapper pointing to the test interpreter via the same
    # environment pointer logic, without creating another environment.
    py.touch()
    pointer = root/'.git'/'aplus-update'/'current-python.txt'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(sys.executable, encoding='utf-8')
    package = tmp_path/'bundle'; (package/'tools').mkdir(parents=True)
    proof = package/'proof.json'
    (package/'tools'/'legacy_update_bootstrap.py').write_text(
        'import json,sys,pathlib\npathlib.Path(__file__).parent.parent.joinpath("proof.json").write_text(json.dumps(sys.argv))\n')
    source = (ROOT/'安全过渡更新.vbs').read_text(encoding='utf-8')
    source = '\n'.join(line for line in source.splitlines() if not line.startswith('MsgBox '))
    source = '\n'.join('Set folder = shell.NameSpace("'+str(root)+'")'
                       if line.startswith('Set folder = shell.BrowseForFolder') else line
                       for line in source.splitlines())
    entry = package/'transition.vbs'
    entry.write_text(source, encoding='utf-16')
    result = subprocess.run(['cscript.exe','//Nologo',str(entry)],capture_output=True,timeout=20)
    assert result.returncode == 0, result.stderr
    args = json.loads(proof.read_text())
    assert args[-2:] == ['--root', str(root)]


@pytest.mark.skipif(os.name != 'nt', reason='Windows VBS and native desktop update workflow')
def test_real_a192_transition_and_followup_update(tmp_path, monkeypatch, short_bridge_root):
    """Real historical tree + candidate tree; CI authority is synthetic until merge.

    Creates the complete hashed venv once. Never references Aplus_daily/runtime.
    """
    root = short_bridge_root/'daily'
    subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(ROOT), str(root)],
                   capture_output=True, check=True, timeout=90)
    git(root, 'config', 'user.name', 'Isolated bridge test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'core.autocrlf', 'false')
    git(root, 'remote', 'set-url', 'origin', v.REMOTE)
    base = 'a192515369da647c586a2d7609071cda5bd00d75'
    # Actions checkout may be shallow and have no origin/main tracking ref.
    # Fetch only the known public historical commit if it is not available.
    historical = subprocess.run(['git','-C',str(root),'cat-file','-e',base+'^{commit}'],
                                capture_output=True,timeout=30)
    if historical.returncode:
        git(root,'fetch','--no-tags','origin',base)
    git(root, 'switch', '-c', 'candidate', git(ROOT, 'rev-parse', 'HEAD'))
    for name in ('workbench/version_update.py', 'release_installation.json', 'requirements-hashes.txt',
                 'tools/maintenance_update.py', 'tools/legacy_update_bootstrap.py', '安全过渡更新.vbs', '启动A.vbs'):
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, root/name)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'candidate bridge release')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'main', base)
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    from workbench.store import Store
    runtime = tmp_path/'existing-runtime'
    store = Store(runtime)
    before = store.path.read_bytes()
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'], plan
    assert plan['installation']['environment_changed']
    # Actual target GUI/backend startup in an isolated named mutex. The real
    # user's desktop may be open; this test never touches its mutex or process.
    script = '''
import sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import launch_dashboard as launch
original=launch.acquire_single_instance
launch.acquire_single_instance=lambda: original(False,sys.argv[2])
from gui import main_window
RealWindow=main_window.AplusMainWindow
class TestWindow(RealWindow):
 def __init__(self,*a,**k):
  k['enable_tray']=False
  super().__init__(*a,**k)
  self.after(1800,lambda: os._exit(0))
main_window.AplusMainWindow=TestWindow
launch.main()
'''
    import uuid
    import threading
    from contextlib import nullcontext
    from tools import maintenance_update as maintenance
    real_popen = subprocess.Popen
    old = real_popen([sys.executable, '-I', '-c', 'input()'], stdin=subprocess.PIPE)
    workers, children, data_checks = [], [], []
    def initial_popen(command, **kwargs):
        if len(command)>1 and str(command[1]) == str(root/'launch_dashboard.py'):
            command = [command[0], '-I', '-c', script, str(root),
                       'Local\\AplusBridgeInitial-'+uuid.uuid4().hex]
            child = real_popen(command, **kwargs); children.append(child); return child
        return real_popen(command, **kwargs)
    def prepared(r,p,h,**kwargs):
        folder = v.prepare_worker(r,p,h,spawn=Mock(),**kwargs)
        request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
        request['parent_pid'] = old.pid
        v.write_json(folder/'request.json',request)
        thread = threading.Thread(target=v.worker,args=(folder/'request.json',),daemon=True)
        workers.append(thread); thread.start()
        return folder
    real_verify_data = v.verify_maintenance_data
    def verified_data(request):
        real_verify_data(request)
        data_checks.append(store.path.read_bytes() == before)
    with monkeypatch.context() as isolated:
        isolated.setattr(v.subprocess,'Popen',initial_popen)
        isolated.setattr(v,'github_json',api_for(target))
        isolated.setattr(v,'application_mutex',nullcontext)
        isolated.setattr(v,'require_web_closed',Mock())
        isolated.setattr(v,'verify_maintenance_data',verified_data)
        isolated.setattr(v.ctypes.windll.user32,'MessageBoxW',Mock())
        isolated.setattr(maintenance,'application_mutex',nullcontext)
        isolated.setattr(maintenance,'require_web_closed',Mock())
        isolated.setattr(maintenance,'check_version',lambda *a,**k: plan)
        isolated.setattr(maintenance,'prepare_worker',prepared)
        first = maintenance.prepare(root,runtime,sys.executable,expected_target=target)
        old.communicate(b'\n',timeout=15)
        workers[0].join(timeout=2100)
        assert not workers[0].is_alive()
        assert json.loads((first/'receipt.json').read_text(encoding='utf-8'))['state'] == 'completed'
        assert (first/'started.json').is_file()
        assert data_checks == [True, True]
        assert (first/'original-workbench.sqlite3').read_bytes() == before
        for child in children: child.wait(timeout=15)
    python = Path(v.environment_pointer(root).read_text(encoding='utf-8'))
    assert python.is_file()
    assert (root/CERT).read_bytes() == (ROOT/CERT).read_bytes()
    env = dict(os.environ,A_WORKBENCH_HOME=str(runtime))
    # Validate VBS selection/routing using a disposable launcher payload. It
    # records interpreter + runtime without launching a second real desktop.
    (root/'launch_dashboard.py').write_text(
        "import json,os,sys,pathlib\npathlib.Path(__file__).with_name('vbs-proof.json').write_text("
        "json.dumps([sys.executable,os.environ.get('A_WORKBENCH_HOME')]))\n")
    launched = subprocess.run(['cscript.exe', '//Nologo', str(root/'启动A.vbs')], cwd=root, env=env,
                             capture_output=True, timeout=15)
    assert launched.returncode == 0
    import time
    proof = root/'vbs-proof.json'
    deadline = time.monotonic()+15
    while not proof.exists() and time.monotonic()<deadline:
        time.sleep(.1)
    assert proof.is_file()
    actual_python, _runtime = json.loads(proof.read_text())
    assert Path(actual_python).parent == python.parent
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            registered = winreg.QueryValueEx(key, 'A_WORKBENCH_HOME')[0]
    except FileNotFoundError:
        registered = str(runtime)
    assert Path(_runtime).resolve() == Path(registered or runtime).resolve()
    # Undo only this test-created payload via exact target bytes; no reset.
    (root/'launch_dashboard.py').write_bytes(v.run_git(root, 'show', target+':launch_dashboard.py', binary=True))
    proof.unlink()
    git(root, 'switch', '-c', 'next-release')
    (root/'bridge-followup.txt').write_text('ordinary release\n')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'next ordinary update')
    next_target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', next_target)
    followup = v.check_version(root, force=True, api=api_for(next_target), fetch=Mock())
    assert followup['can_update'], followup
    assert not followup['installation']['environment_changed']
    # Invoke the button controller, then its actual prepared worker transaction.
    # Only the shell and official CI authority are synthetic; code replacement,
    # dependency checks, startup ack and pointer commit use production functions.
    from gui import version_update as ui
    from types import SimpleNamespace
    from contextlib import nullcontext
    import threading
    old_process = subprocess.Popen([python, '-I', '-c', 'input()'], stdin=subprocess.PIPE)
    real_popen = subprocess.Popen
    children = []
    def popen(command, **kwargs):
        if len(command)>1 and str(command[1]) == str(root/'launch_dashboard.py'):
            command = [command[0], '-I', '-c', script, str(root),
                       'Local\\AplusBridgeNext-'+uuid.uuid4().hex]
            child = real_popen(command, **kwargs); children.append(child); return child
        return real_popen(command, **kwargs)
    monkeypatch.setattr(v.subprocess, 'Popen', popen)
    monkeypatch.setattr(v, 'github_json', api_for(next_target))
    monkeypatch.setattr(v, 'application_mutex', nullcontext)
    monkeypatch.setattr(v, 'require_web_closed', Mock())
    monkeypatch.setattr(v.ctypes.windll.user32, 'MessageBoxW', Mock())
    monkeypatch.setattr(ui, 'open_forms', lambda _: False)
    monkeypatch.setattr(ui, 'require_web_closed', Mock())
    monkeypatch.setattr(ui, 'prepare_worker', lambda r,p,h:
                        v.prepare_worker(r,p,h,python=python,spawn=Mock()))
    controller = object.__new__(ui.VersionController)
    controller.root = root; controller.reserved = False
    controller.window = SimpleNamespace(service=Mock(),store=store,withdraw=Mock(),
                                        _flush_embedded_charts=lambda: True)
    second_facts = v._database_state(store.path)['facts']
    controller.begin_update(followup)
    assert controller.reserved and controller.window._update_pending
    receipt_folder = controller.waiting[0]
    request_path = receipt_folder/'request.json'
    request = json.loads(request_path.read_text(encoding='utf-8'))
    assert Path(request['runtime']) == runtime
    request['parent_pid'] = old_process.pid; v.write_json(request_path,request)
    def exit_old():
        deadline=time.monotonic()+15
        while not (receipt_folder/'ready.json').exists() and time.monotonic()<deadline:
            time.sleep(.05)
        old_process.communicate(b'\n',timeout=10)
    thread=threading.Thread(target=exit_old,daemon=True); thread.start()
    v.worker(request_path)
    thread.join(timeout=20)
    assert json.loads((receipt_folder/'receipt.json').read_text(encoding='utf-8'))['state'] == 'completed'
    for child in children: child.wait(timeout=15)
    assert git(root, 'rev-parse', 'HEAD') == next_target
    assert v._database_state(store.path)['facts'] == second_facts
    assert not v.active_transaction_path(root).exists()
