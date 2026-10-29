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


def windows_short_path(path):
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    assert 0 < length < len(buffer), ctypes.WinError()
    return Path(buffer.value)


def launcher_identity(command, expected):
    """Fail closed for desktop commands that cannot prove file identity."""
    if len(command) < 2:
        return None
    actual, expected = Path(command[1]), Path(expected)
    proof = dict(actual=str(actual), expected=str(expected),
                 actual_resolved=str(actual.resolve()), expected_resolved=str(expected.resolve()),
                 old_string_equal=str(actual) == str(expected))
    try:
        proof['samefile'] = actual.samefile(expected)
    except OSError as error:
        proof.update(samefile=False, identity_error=str(error))
    if proof['samefile']:
        return proof
    if 'launch_dashboard.py' in {actual.name.casefold(), actual.resolve().name.casefold()}:
        raise AssertionError('Uncontrolled desktop refused: ' + json.dumps(proof))
    return None


def controlled_launcher(root, script, stage, real_popen, owned, children, launches):
    def popen(command, **kwargs):
        identity = launcher_identity(command, root/'launch_dashboard.py')
        if identity is None:
            return real_popen(command, **kwargs)
        import uuid
        mutex = 'Local\\AplusBridge'+stage+'-'+uuid.uuid4().hex
        isolated = [command[0], '-I', '-c', script, str(root), mutex]
        folder = Path(kwargs['env']['APLUS_UPDATE_ACK']).parent
        with (folder/'desktop-output.log').open('wb') as log:
            child = real_popen(isolated, stdout=log, stderr=log, **kwargs)
        owned.append(child)
        children.append(child)
        proof = dict(stage=stage, isolated_branch=True, pid=child.pid, mutex=mutex,
                     requested_command=list(map(str, command)), isolated_command=list(map(str, isolated)),
                     identity=identity)
        launches.append(proof)
        v.write_json(folder/'controlled-launch.json', proof)
        print(json.dumps(proof), flush=True)
        return child
    return popen


@pytest.mark.skipif(os.name != 'nt', reason='Windows real 8.3 file identities')
@pytest.mark.parametrize('stage', ['Initial', 'Next'])
@pytest.mark.parametrize('spelling', ['long', 'short-file', 'case'])
def test_launcher_interception_uses_windows_file_identity(tmp_path, stage, spelling):
    folder = tmp_path/'Aplus launcher identity'; folder.mkdir()
    launcher = folder/'launch_dashboard.py'; launcher.write_text('# identity only')
    short_root = windows_short_path(folder)
    assert str(short_root) != str(folder), '8.3 alias required for the regression'
    command_path = {'long': str(launcher.resolve()), 'short-file': str(windows_short_path(launcher)),
                    'case': str(launcher.resolve()).swapcase()}[spelling]
    command = [sys.executable, command_path]
    expected = short_root/'launch_dashboard.py'
    assert str(command[1]) != str(expected)  # The previous interception missed.
    assert Path(command[1]).samefile(expected)
    child = Mock(pid=1234); real_popen = Mock(return_value=child)
    owned, children, launches = [], [], []
    intercepted = controlled_launcher(short_root, '# isolated test desktop', stage,
                                     real_popen, owned, children, launches)
    assert intercepted(command, env={'APLUS_UPDATE_ACK': str(tmp_path/'started.json')}) is child
    assert owned == children == [child] and len(launches) == 1
    assert launches[0]['identity']['samefile'] is True
    assert launches[0]['isolated_branch'] is True
    assert real_popen.call_args.args[0][1:3] == ['-I', '-c']


@pytest.mark.parametrize('stage', ['Initial', 'Next'])
@pytest.mark.parametrize('kind', ['other-file', 'missing'])
def test_launcher_interception_refuses_unknown_desktop(tmp_path, stage, kind):
    root = tmp_path/'expected'; root.mkdir()
    (root/'launch_dashboard.py').write_text('# expected')
    other = tmp_path/'other'; other.mkdir()
    actual = other/'launch_dashboard.py'
    if kind == 'other-file':
        actual.write_text('# not the test installation')
    real_popen = Mock()
    intercepted = controlled_launcher(root, '# controlled', stage, real_popen, [], [], [])
    with pytest.raises(AssertionError, match='Uncontrolled desktop refused'):
        intercepted([sys.executable, str(actual)])
    real_popen.assert_not_called()
    # Legitimate non-desktop commands keep their normal purpose.
    intercepted([sys.executable, '-I', '-m', 'pip', 'check'])
    real_popen.assert_called_once_with([sys.executable, '-I', '-m', 'pip', 'check'])


def ensure_candidate_history(root, source_head, base):
    # Connecting both endpoints matters: fetching the old object alone cannot
    # prove ancestry when the candidate itself is a shallow boundary.
    if git(root, 'rev-parse', '--is-shallow-repository') == 'true':
        git(root, 'fetch', '--no-tags', '--unshallow', 'origin', source_head)
    historical = subprocess.run(['git','-C',str(root),'cat-file','-e',base+'^{commit}'],
                                capture_output=True,timeout=30)
    if historical.returncode:
        git(root,'fetch','--no-tags','origin',base)


def test_shallow_candidate_history_connects_old_and_new_commits(repo, tmp_path):
    source, old, target = repo
    root = tmp_path/'shallow'
    subprocess.run(['git','clone','--depth=1','--branch','server',source.as_uri(),str(root)],
                   capture_output=True,check=True,timeout=60)
    assert git(root,'rev-parse','--is-shallow-repository') == 'true'
    ensure_candidate_history(root,target,old)
    assert git(root,'rev-parse','--is-shallow-repository') == 'false'
    git(root,'merge-base','--is-ancestor',old,target)


@pytest.fixture
def short_bridge_root(request):
    # Windows without LongPathsEnabled cannot install ONNX under pytest's
    # deeply nested generated test name. Match the short daily-install layout.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='Aplus update ') as folder:
        processes = []
        request.node.bridge_processes = processes
        try:
            short = windows_short_path(folder) if os.name == 'nt' else Path(folder)
            assert str(short) != str(Path(folder)), 'A real 8.3 alias must exercise both launches'
            yield short
        finally:
            # Only processes created explicitly by this disposable test. A failed
            # startup assertion must not leave DLLs locked during fixture cleanup.
            stop_bridge_processes(processes)
    assert not Path(folder).exists()
    print('bridge temporary environment removed after all owned processes exited', flush=True)


def stop_bridge_processes(processes):
    for process in reversed(processes):
        if process.poll() is None:
            if os.name == 'nt':
                # venv python.exe can be a redirector with a child using the
                # DLLs. End only this test-owned process tree, before the parent.
                stopped = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                         capture_output=True, timeout=15)
                assert stopped.returncode == 0 or process.poll() is not None, stopped.stderr
            else:
                process.terminate()
        process.wait(timeout=15)


def test_bridge_failure_cleanup_releases_child_files(tmp_path):
    import time
    proof = tmp_path/'child.log'
    child = subprocess.Popen([sys.executable, '-I', '-c',
        "import sys,time; f=open(sys.argv[1],'w'); f.write('ready'); f.flush(); time.sleep(60)", str(proof)])
    try:
        deadline = time.monotonic()+15
        while not proof.exists() and time.monotonic()<deadline:
            time.sleep(.05)
        assert proof.is_file()
    finally:
        stop_bridge_processes([child])
    assert child.poll() is not None
    proof.unlink()  # Fails on Windows if the interpreter child still holds it.


def test_unconfirmed_bridge_reports_receipt_and_desktop_stack(tmp_path):
    (tmp_path/'receipt.json').write_text(json.dumps({'state':'startup_unconfirmed'}))
    (tmp_path/'desktop-stacks.log').write_text('blocked in native window')
    with pytest.raises(AssertionError, match='blocked in native window'):
        assert_bridge_completed(tmp_path)


def assert_bridge_completed(folder):
    receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    diagnostics = {path.name: path.read_text(encoding='utf-8', errors='replace')[-16000:]
                   for path in folder.glob('desktop-*.log')}
    if receipt['state'] != 'completed':
        # Pytest abbreviates dictionary reprs. Keep the actual stack available
        # in captured stdout even with the normal CI verbosity.
        print(json.dumps({'receipt': receipt, 'desktop': diagnostics}, ensure_ascii=True, indent=2))
    assert receipt['state'] == 'completed', {'receipt': receipt, 'desktop': diagnostics}


def assert_controlled_desktop(folder, children, launches, owned):
    assert len(children) == len(launches) == 1, launches
    child, launch = children[0], launches[0]
    assert child in owned and child.pid == launch['pid']
    assert launch['isolated_branch'] and launch['identity']['samefile']
    assert not launch['identity']['old_string_equal'], launch
    assert json.loads((folder/'started.json').read_text()) == {'started': True}
    assert_bridge_completed(folder)
    assert child.wait(timeout=15) == 0
    print(json.dumps(dict(stage=launch['stage'], pid=child.pid, ack=True, completed=True,
                          exit_code=child.returncode)), flush=True)


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
def test_real_a192_transition_and_followup_update(tmp_path, monkeypatch, short_bridge_root, request):
    """Real historical tree + candidate tree; CI authority is synthetic until merge.

    Creates the complete hashed venv once. Never references Aplus_daily/runtime.
    """
    owned_processes = request.node.bridge_processes
    root = short_bridge_root/'daily'
    subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(ROOT), str(root)],
                   capture_output=True, check=True, timeout=90)
    git(root, 'config', 'user.name', 'Isolated bridge test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'core.autocrlf', 'false')
    git(root, 'remote', 'set-url', 'origin', v.REMOTE)
    base = 'a192515369da647c586a2d7609071cda5bd00d75'
    source_head = git(ROOT, 'rev-parse', 'HEAD')
    # Actions checkout may be shallow and have no origin/main tracking ref.
    ensure_candidate_history(root,source_head,base)
    git(root, 'switch', '-c', 'candidate', source_head)
    for name in ('workbench/version_update.py', 'release_installation.json', 'requirements-hashes.txt',
                 'tools/maintenance_update.py', 'tools/legacy_update_bootstrap.py', '安全过渡更新.vbs', '启动A.vbs'):
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, root/name)
    # In CI these files are already committed. The candidate still needs a
    # distinct identity for the two-release simulation, even with identical bytes.
    git(root, 'add', '.'); git(root, 'commit', '--allow-empty', '-m', 'candidate bridge release')
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
import sys,os,faulthandler
from pathlib import Path
diagnostics=Path(os.environ['APLUS_UPDATE_ACK']).with_name('desktop-stacks.log')
stack_log=diagnostics.open('w',encoding='utf-8')
faulthandler.enable(stack_log)
faulthandler.dump_traceback_later(20,repeat=True,file=stack_log)
def progress(value):
 print(value,flush=True)
progress('importing launcher')
sys.path.insert(0,sys.argv[1])
import launch_dashboard as launch
launch.LOG_PATH=str(diagnostics.with_name('desktop-errors.log'))
original=launch.acquire_single_instance
launch.acquire_single_instance=lambda: original(False,sys.argv[2])
progress('importing window')
from gui import main_window
RealWindow=main_window.AplusMainWindow
class TestWindow(RealWindow):
 def __init__(self,*a,**k):
  progress('constructing window')
  k['enable_tray']=False
  super().__init__(*a,**k)
  progress('window constructed')
  def exit_after_commit():
   transaction=diagnostics.with_name('transaction.json')
   phase=__import__('json').loads(transaction.read_text(encoding='utf-8'))['phase']
   if phase=='completed':
    progress('transaction committed; closing test desktop')
    os._exit(0)
   self.after(100,exit_after_commit)
  # A fixed 1.8s lifetime can end the desktop during slow environment/pointer
  # validation. Stay alive until the actual worker has committed, never create
  # a fake startup ack. The fixture owns failure cleanup.
  self.after(100,exit_after_commit)
main_window.AplusMainWindow=TestWindow
progress('starting desktop')
launch.main()
'''
    import threading
    from contextlib import nullcontext
    from tools import maintenance_update as maintenance
    real_popen = subprocess.Popen
    old = real_popen([sys.executable, '-I', '-c', 'input()'], stdin=subprocess.PIPE)
    owned_processes.append(old)
    workers, children, data_checks, first_launches = [], [], [], []
    initial_popen = controlled_launcher(root, script, 'Initial', real_popen,
                                       owned_processes, children, first_launches)
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
        assert_controlled_desktop(first, children, first_launches, owned_processes)
        assert data_checks == [True, True]
        assert (first/'original-workbench.sqlite3').read_bytes() == before
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
    owned_processes.append(old_process)
    real_popen = subprocess.Popen
    children, next_launches = [], []
    popen = controlled_launcher(root, script, 'Next', real_popen,
                               owned_processes, children, next_launches)
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
    assert_controlled_desktop(receipt_folder, children, next_launches, owned_processes)
    assert git(root, 'rev-parse', 'HEAD') == next_target
    assert v._database_state(store.path)['facts'] == second_facts
    assert Path(v.environment_pointer(root).read_text(encoding='utf-8')).samefile(python)
    assert not v.active_transaction_path(root).exists()
    assert all(process.poll() is not None for process in owned_processes)
