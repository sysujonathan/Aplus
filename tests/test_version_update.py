"""All repositories, runtime folders and update processes here are synthetic."""
import io
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
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
    (root / 'push.cmd').write_text('official launcher\n', encoding='utf-8')
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


def installation_contract(lock, schema=8, supported=(1, 2, 3, 4, 5, 6, 7, 8),
                          migration='store-v8', hashes=None):
    digest = hashlib.sha256(lock).hexdigest()
    return {
        'protocol': 1, 'python': f'{sys.version_info.major}.{sys.version_info.minor}',
        'environment_id': (f'py{sys.version_info.major}{sys.version_info.minor}-{digest[:12]}'
                           + ('-' + hashlib.sha256(hashes).hexdigest()[:12] if hashes is not None else '')),
        'requirements_sha256': digest,
        'requirements_hashes_sha256': hashlib.sha256(hashes).hexdigest() if hashes is not None else None,
        'database': {'schema': schema, 'supported_from': list(supported), 'migration': migration},
    }


def installation_release(repo, *, invalid_hash=False):
    root, _, _ = repo
    git(root, 'switch', 'main')
    first = b'example-package==1.0\n'
    (root/'requirements-lock.txt').write_bytes(first)
    (root/v.RELEASE_INSTALLATION).write_text(json.dumps(installation_contract(first)), encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'installation baseline')
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'installation-release')
    second = b'example-package==2.0\n'
    hashes = b'example-package==2.0 --hash=sha256:' + b'1' * 64 + b'\n'
    (root/'requirements-lock.txt').write_bytes(second)
    (root/'requirements-hashes.txt').write_bytes(hashes)
    contract = installation_contract(second, hashes=hashes)
    if invalid_hash:
        contract['requirements_sha256'] = '0' * 64
    (root/v.RELEASE_INSTALLATION).write_text(json.dumps(contract), encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'dependency release')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main')
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, old, target


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


@pytest.mark.parametrize('status,conclusion,expected', [
    ('queued',None,'running'),('in_progress',None,'running'),
    ('completed','failure','failed'),('completed','cancelled','failed'),('completed','success','passed')])
def test_check_reports_exact_main_ci_state_without_relaxing_permission(repo,status,conclusion,expected):
    root,_,target=repo
    run=dict(id=20,head_sha=target,head_branch='main',event='push',name='A acceptance',
             path='.github/workflows/checks.yml',status=status,conclusion=conclusion)
    result=v.check_version(root,force=True,api=api_for(target,extra=[run]),fetch=Mock())
    assert result['ci_status']==expected and result['ci_passed']==(expected=='passed')
    assert result['can_update']==(expected=='passed')


def test_missing_main_ci_is_not_mistaken_for_running_or_success(repo):
    root,_,target=repo
    api=lambda path: {'commit':{'sha':target}} if path=='/branches/main' else {'workflow_runs':[]}
    result=v.check_version(root,force=True,api=api,fetch=Mock())
    assert result['ci_status']=='missing' and result['state']=='pending' and not result['can_update']


def test_declared_dependency_release_builds_a_parallel_environment_plan(repo):
    root, _, target = installation_release(repo)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update']
    assert plan['installation']['environment_changed'] is True
    assert plan['installation']['database_changed'] is False
    assert plan['installation']['environment_id'].startswith(
        f'py{sys.version_info.major}{sys.version_info.minor}-'
    )


def test_dependency_release_with_unbound_manifest_is_rejected(repo):
    root, old, target = installation_release(repo, invalid_hash=True)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not plan['can_update'] and plan['manual_kind'] == 'installation'
    assert git(root, 'rev-parse', 'HEAD') == old


def test_parallel_environment_failure_keeps_existing_launch_pointer(repo, tmp_path, monkeypatch):
    root, _, _ = repo
    pointer = v.environment_pointer(root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('C:\\old\\python.exe', encoding='utf-8')
    stage = tmp_path/'target-code'; stage.mkdir()
    plan = {'installation': {'environment_changed': True,
                             'environment_id': 'py312-1234567890ab', 'python': '3.12'}}
    def fail(folder, *_args):
        folder.mkdir(parents=True, exist_ok=True)
        raise v.UpdateError('synthetic install failure')
    monkeypatch.setattr(v, '_build_environment', fail)
    with pytest.raises(v.UpdateError, match='synthetic'):
        v.prepare_target_environment(root, stage, plan, sys.executable, smoke=Mock())
    assert pointer.read_text(encoding='utf-8') == 'C:\\old\\python.exe'
    assert not list((pointer.parent/'environments').glob('.staging-*'))
    assert not (pointer.parent/'environments'/'py312-1234567890ab').exists()


def test_parallel_environment_is_created_and_started_at_its_final_path(repo, tmp_path):
    root, _, _ = repo
    stage = tmp_path/'target-code'; stage.mkdir()
    lock = b''
    (stage/'requirements-lock.txt').write_bytes(lock)
    digest = hashlib.sha256(lock).hexdigest()
    environment_id = f'py{sys.version_info.major}{sys.version_info.minor}-{digest[:12]}'
    contract = {
        'environment_changed': True, 'environment_id': environment_id,
        'python': f'{sys.version_info.major}.{sys.version_info.minor}',
        'requirements_sha256': digest, 'requirements_hashes_sha256': None,
    }
    seen = {}
    def smoke(_stage, python):
        seen['python'] = Path(python).resolve()
        assert subprocess.run([python, '-I', '-c', 'import sys;print(sys.executable)'],
                              capture_output=True, text=True).returncode == 0
    python, created = v.prepare_target_environment(
        root, stage, {'installation': contract}, sys.executable, smoke=smoke)
    final = v.metadata_dir(root)/'environments'/environment_id
    assert created == final and Path(python).resolve() == v._environment_python(final).resolve()
    assert seen['python'] == Path(python).resolve()
    assert json.loads(v._environment_marker(final).read_text(encoding='utf-8'))['state'] == 'ready'
    assert not list(final.parent.glob('.staging-*'))


def test_release_manifest_matches_exact_lock_and_current_schema(tmp_path):
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root/v.RELEASE_INSTALLATION).read_text(encoding='utf-8'))
    lock = (root/'requirements-lock.txt').read_bytes()
    assert contract['requirements_sha256'] == hashlib.sha256(lock).hexdigest()
    runtime = tmp_path/'runtime'
    store = Store(runtime)
    with store.connect() as db:
        schema = int(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    assert contract['database']['schema'] == schema


def test_database_migration_is_proved_on_copy_and_can_restore_exact_backup(tmp_path):
    runtime = tmp_path/'runtime'
    runtime.mkdir()
    database = runtime/'workbench.sqlite3'
    db = sqlite3.connect(database)
    try:
        db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO meta VALUES('schema_version','8')")
        db.execute('CREATE TABLE observations(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute("INSERT INTO observations VALUES('kept','signal')")
        db.commit()
    finally:
        db.close()
    stage = tmp_path/'stage'
    (stage/'workbench').mkdir(parents=True)
    (stage/'workbench'/'__init__.py').write_text('', encoding='utf-8')
    (stage/'workbench'/'store.py').write_text(
        "import sqlite3\nfrom pathlib import Path\n"
        "class Store:\n"
        " def __init__(self,root):\n"
        "  p=Path(root)/'workbench.sqlite3'\n"
        "  with sqlite3.connect(p) as db:\n"
        "   db.execute('ALTER TABLE observations ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0')\n"
        "   db.execute(\"UPDATE meta SET value='9' WHERE key='schema_version'\")\n",
        encoding='utf-8')
    folder = tmp_path/'transaction'
    folder.mkdir()
    contract = {'database_changed': True, 'source_schema': 8, 'target_schema': 9}
    transaction = v.prepare_database_migration(stage, sys.executable, runtime, folder, contract)
    assert transaction['changed'] and v._database_state(database)['schema'] == 8
    assert v._database_state(folder/'trial-runtime'/'workbench.sqlite3')['schema'] == 9
    v.migrate_formal_database(stage, sys.executable, runtime, transaction)
    assert v._database_state(database)['schema'] == 9
    v.restore_database(transaction)
    assert v._database_state(database)['schema'] == 8
    assert v._digest(database) == transaction['backup_sha256']
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT payload FROM observations WHERE id=?', ('kept',)).fetchone()[0] == 'signal'


def test_database_state_rejects_broken_foreign_keys_and_dataset_files(tmp_path):
    runtime = tmp_path/'runtime'; runtime.mkdir()
    database = runtime/'workbench.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "INSERT INTO meta VALUES('schema_version','8');"
            "CREATE TABLE parent(id TEXT PRIMARY KEY);"
            "CREATE TABLE child(id TEXT PRIMARY KEY,parent_id TEXT REFERENCES parent(id));"
            "INSERT INTO child VALUES('broken','missing');"
        )
    with pytest.raises(v.UpdateError, match='外键'):
        v._database_state(database, runtime)

    database = runtime/'datasets.sqlite3'
    market = runtime/'market.csv'; market.write_bytes(b'prices')
    digest = hashlib.sha256(market.read_bytes()).hexdigest()
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "INSERT INTO meta VALUES('schema_version','8');"
            "CREATE TABLE datasets(id TEXT PRIMARY KEY,path TEXT NOT NULL,sha256 TEXT NOT NULL);"
        )
        db.execute('INSERT INTO datasets VALUES(?,?,?)', ('daily', 'market.csv', digest))
    assert v._database_state(database, runtime)['external']['count'] == 1
    market.write_bytes(b'changed')
    with pytest.raises(v.UpdateError, match='行情文件'):
        v._database_state(database, runtime)


def test_new_business_writes_after_open_are_never_overwritten(tmp_path):
    runtime = tmp_path/'runtime'; runtime.mkdir()
    database = runtime/'workbench.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "INSERT INTO meta VALUES('schema_version','8');"
            "CREATE TABLE executions(id TEXT PRIMARY KEY,amount REAL NOT NULL);"
            "INSERT INTO executions VALUES('old',100.0);"
        )
    backup = tmp_path/'before.sqlite3'
    v._sqlite_snapshot(database, backup)
    before_sha = v._digest(backup)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    migrated_sha = v._digest(database)
    transaction = {
        'changed': True, 'database': str(database), 'backup': str(backup),
        'backup_sha256': before_sha, 'schema_before': 8,
        'post_migration_sha256': migrated_sha,
    }
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO executions VALUES('new-after-open',250.0)")
    with pytest.raises(v.UpdateError, match='新增交易记录'):
        v.restore_database(transaction, expected_current={migrated_sha})
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT amount FROM executions WHERE id='new-after-open'").fetchone()[0] == 250.0


def test_migration_rejects_changed_protected_ledger_facts():
    fact = {'columns': ['id', 'amount'], 'count': 1, 'sha256': 'a'*64}
    before = {'objects': ['table:executions'], 'facts': {'executions': fact},
              'external': {'count': 0, 'sha256': 'b'*64}}
    after = {'schema': 9, 'objects': ['table:executions'],
             'facts': {'executions': {**fact, 'sha256': 'c'*64}},
             'external': before['external']}
    with pytest.raises(v.UpdateError, match='受保护'):
        v._validate_migration(before, after, 9)


def test_launchers_prefer_committed_environment_pointer_without_configuring_users():
    root = Path(__file__).resolve().parents[1]
    for name in ('启动A.vbs', '启动A.cmd'):
        text = (root/name).read_text(encoding='utf-8')
        assert '.git\\aplus-update\\current-python.txt' in text
        assert '.git\\aplus-update\\active-request.txt' in text
        assert '--recover' in text


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


def managed_release(repo, value='value = 2\n'):
    root, old, target = repo
    git(root, 'switch', '-c', 'codex/local-release-20261011', old)
    (root / 'feature.py').write_text(value, encoding='utf-8')
    git(root, 'add', 'feature.py')
    git(root, 'commit', '-m', 'Aplus managed local combination')
    local = git(root, 'rev-parse', 'HEAD')
    (root / 'push.cmd').write_text('private launcher kept locally\n', encoding='utf-8')
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, local, target


def test_managed_release_and_preserved_launcher_update_with_one_button(repo, tmp_path):
    root, local, target = managed_release(repo)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    assert plan['state'] == 'available' and plan['can_update']
    assert plan['migration'] == 'managed_release'
    assert not plan['local']['dirty'] and 'push.cmd' in plan['local']['preserved']
    backup = tmp_path / 'backup.zip'
    v.apply_update(root, plan, backup, sys.executable, api=api_for(target), smoke=Mock())
    assert git(root, 'branch', '--show-current') == 'main'
    assert git(root, 'rev-parse', 'HEAD') == target
    assert (root / 'push.cmd').read_text(encoding='utf-8') == 'private launcher kept locally\n'
    assert not v.local_version(root)['dirty']
    with zipfile.ZipFile(backup) as archive:
        assert archive.read('__aplus_local__/push.cmd') == (root / 'push.cmd').read_bytes()


def test_managed_release_with_unabsorbed_runtime_code_still_requires_maintainer(repo):
    root, _, target = managed_release(repo, 'local-only behavior = 99\n')
    result = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    assert result['state'] == 'diverged' and not result['can_update']


def test_preserved_launcher_change_after_confirmation_is_rejected(repo, tmp_path):
    root, local, target = managed_release(repo)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    (root / 'push.cmd').write_text('changed after confirmation\n', encoding='utf-8')
    with pytest.raises(v.UpdateError, match='重新检查'):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable,
                       api=api_for(target), smoke=Mock())
    assert git(root, 'rev-parse', 'HEAD') == local


def test_target_cannot_remove_preserved_launcher(repo):
    root, _, _ = managed_release(repo)
    (root / 'push.cmd').write_text(git(root, 'show', 'HEAD:push.cmd') + '\n', encoding='utf-8')
    git(root, 'switch', 'server')
    git(root, 'rm', 'push.cmd')
    git(root, 'commit', '-m', 'remove official launcher')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'codex/local-release-20261011')
    (root / 'push.cmd').write_text('private launcher kept locally\n', encoding='utf-8')
    git(root, 'update-ref', 'refs/aplus-update/main', target)

    result = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)

    assert not result['can_update']
    assert '受保护' in result['message']
    assert (root / 'push.cmd').read_text(encoding='utf-8') == 'private launcher kept locally\n'


def test_managed_release_failed_postflight_restores_branch_and_launcher(repo, tmp_path):
    root, local, target = managed_release(repo)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=lambda sha: None)
    smoke = Mock(side_effect=[None, RuntimeError('postflight failed')])
    with pytest.raises(v.UpdateError, match='回到旧代码'):
        v.apply_update(root, plan, tmp_path/'backup.zip', sys.executable,
                       api=api_for(target), smoke=smoke)
    assert git(root, 'branch', '--show-current') == 'codex/local-release-20261011'
    assert git(root, 'rev-parse', 'HEAD') == local
    assert (root / 'push.cmd').read_text(encoding='utf-8') == 'private launcher kept locally\n'


def test_atomic_launcher_restore_handles_windows_read_only_destination(tmp_path):
    launcher = tmp_path / 'push.cmd'
    launcher.write_bytes(b'old launcher\r\n')
    os.chmod(launcher, launcher.stat().st_mode & ~0o222)

    v._replace_file(launcher, b'restored launcher\r\n')

    assert launcher.read_bytes() == b'restored launcher\r\n'


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
    assert git(root, 'branch', '--show-current') == 'main'
    assert plan_for(repo)['can_update']


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


@pytest.mark.parametrize('kind', ['sync', 'scan', 'premarket'])
def test_only_successful_scan_submission_triggers_hint_after_task_starts(kind):
    from gui.toolbar import ToolBar
    toolbar = Mock()
    order = []
    toolbar.service.submit.side_effect = lambda *args: order.append('submit') or 'job'
    toolbar._on_scan_started = lambda: order.append('version')
    ToolBar._submit_job(toolbar, kind, '扫描策略' if kind == 'scan' else '更新行情', {})
    assert order == (['submit', 'version'] if kind in ('scan','premarket') else ['submit'])


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


def synthetic_worker_request(repo, tmp_path):
    root, old, target = repo
    plan = plan_for(repo)
    folder = v.metadata_dir(root) / 'synthetic-worker'
    folder.mkdir(parents=True)
    lock = folder.parent/'update.lock'
    lock.touch()
    request_path = folder/'request.json'
    v.write_json(request_path, dict(root=str(root), plan=plan, runtime=str(tmp_path/'private-runtime'),
                                    python=sys.executable, parent_pid=12345))
    v._persist_transaction(request_path, 'prepared', protocol=1, old=old, old_branch='main',
                           target=target, old_python=sys.executable, old_pointer=None,
                           target_python=None, code_changed=False, database_changed=False,
                           database={'changed': False}, recovery={'outcome': 'pending'})
    v.active_transaction_path(root).write_text(str(request_path.resolve()), encoding='utf-8')
    return root, old, target, folder, lock, request_path


def interrupted_transaction(repo, tmp_path, phase='code_installed'):
    root, old, target = repo
    plan = plan_for(repo)
    git(root, 'merge', '--ff-only', target)
    folder = v.metadata_dir(root)/('interrupted-' + phase)
    folder.mkdir(parents=True)
    request_path = folder/'request.json'
    runtime = tmp_path/'private-runtime'; runtime.mkdir(exist_ok=True)
    v.write_json(request_path, {
        'root': str(root), 'plan': plan, 'runtime': str(runtime),
        'python': sys.executable, 'parent_pid': 1,
    })
    v._persist_transaction(
        request_path, phase, protocol=v.INSTALLATION_PROTOCOL,
        old=old, old_branch='main', target=target,
        old_python=sys.executable, target_python=sys.executable,
        old_pointer=None, code_changed=True, database_changed=False,
        database={'changed': False}, recovery={'outcome': 'pending'},
    )
    v.active_transaction_path(root).write_text(str(request_path.resolve()), encoding='utf-8')
    (v.metadata_dir(root)/'update.lock').touch()
    return root, old, target, folder, request_path


def allow_isolated_recovery(monkeypatch, *, alive=False):
    monkeypatch.setattr(v, '_process_alive', lambda _pid: alive)
    monkeypatch.setattr(v, 'application_mutex', lambda: contextlib.nullcontext())
    monkeypatch.setattr(v, 'require_web_closed', Mock())


def test_hard_interruption_is_recognized_and_rolled_back_on_next_start(repo, tmp_path, monkeypatch):
    root, old, _, folder, request_path = interrupted_transaction(repo, tmp_path)
    allow_isolated_recovery(monkeypatch)
    outcome = v.recover_transaction(request_path)
    assert outcome['transaction']['phase'] == 'rolled_back'
    assert git(root, 'rev-parse', 'HEAD') == old
    assert not v.active_transaction_path(root).exists()
    assert not (v.metadata_dir(root)/'update.lock').exists()
    assert json.loads((folder/'receipt.json').read_text(encoding='utf-8'))['state'] == 'rolled_back'


def test_interruption_after_formal_migration_restores_code_and_database(repo, tmp_path, monkeypatch):
    root, old, _, _, request_path = interrupted_transaction(repo, tmp_path, 'database_migrated')
    runtime = Path(json.loads(request_path.read_text(encoding='utf-8'))['runtime'])
    database = runtime/'workbench.sqlite3'
    with contextlib.closing(sqlite3.connect(database)) as db:
        with db:
            db.executescript(
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                "INSERT INTO meta VALUES('schema_version','8');"
                "CREATE TABLE observations(id TEXT PRIMARY KEY,payload TEXT NOT NULL);"
                "INSERT INTO observations VALUES('kept','signal');"
            )
    backup = request_path.parent/'workbench-before.sqlite3'
    v._sqlite_snapshot(database, backup)
    backup_sha = v._digest(backup)
    with contextlib.closing(sqlite3.connect(database)) as db:
        with db:
            db.execute('ALTER TABLE observations ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0')
            db.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    post_sha = v._digest(database)
    v._persist_transaction(
        request_path, 'database_migrated', database_changed=True,
        database={'changed': True, 'database': str(database), 'backup': str(backup),
                  'backup_sha256': backup_sha, 'pre_migration_sha256': backup_sha,
                  'post_migration_sha256': post_sha, 'schema_before': 8, 'schema_after': 9},
    )
    allow_isolated_recovery(monkeypatch)
    assert v.recover_transaction(request_path)['transaction']['phase'] == 'rolled_back'
    assert git(root, 'rev-parse', 'HEAD') == old
    assert v._database_state(database, runtime)['schema'] == 8
    with contextlib.closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT payload FROM observations WHERE id='kept'").fetchone()[0] == 'signal'


def test_alive_unconfirmed_startup_blocks_second_instance_then_recovers(repo, tmp_path, monkeypatch):
    root, old, _, _, request_path = interrupted_transaction(repo, tmp_path, 'startup_wait')
    v._persist_transaction(request_path, startup_pid=777)
    allow_isolated_recovery(monkeypatch, alive=True)
    assert v.recover_transaction(request_path)['action'] == 'block'
    assert v._load_transaction(request_path)['phase'] == 'startup_unconfirmed'
    assert git(root, 'rev-parse', 'HEAD') != old
    monkeypatch.setattr(v, '_process_alive', lambda _pid: False)
    assert v.recover_transaction(request_path)['transaction']['phase'] == 'rolled_back'
    assert git(root, 'rev-parse', 'HEAD') == old


def test_pointer_commit_failure_keeps_transaction_and_is_retryable(repo, tmp_path, monkeypatch):
    root, _, _, folder, request_path = interrupted_transaction(repo, tmp_path, 'startup_wait')
    v.write_json(folder/'started.json', {'started': True})
    original = v.write_environment_pointer
    monkeypatch.setattr(v, 'write_environment_pointer', Mock(side_effect=OSError('pointer denied')))
    assert v.recover_for_startup(request_path) == v.RECOVERY_BLOCKED
    assert v._load_transaction(request_path)['phase'] == 'recovery_failed'
    assert v.active_transaction_path(root).exists()
    monkeypatch.setattr(v, 'write_environment_pointer', original)
    assert v.recover_for_startup(request_path) == 0
    assert v._load_transaction(request_path)['phase'] == 'completed'
    assert Path(v.environment_pointer(root).read_text(encoding='utf-8')).resolve() == Path(sys.executable).resolve()


@pytest.mark.parametrize('failed_part', ['database', 'code'])
def test_partial_rollback_failure_preserves_precise_recovery_state(repo, tmp_path, monkeypatch, failed_part):
    _, _, _, _, request_path = interrupted_transaction(repo, tmp_path)
    transaction = v._load_transaction(request_path)
    transaction.update(database_changed=True, database={
        'changed': True, 'post_migration_sha256': 'a'*64,
    })
    v._persist_transaction(request_path, database_changed=True, database=transaction['database'])
    monkeypatch.setattr(v, '_reconcile_interrupted_changes', lambda *_args: transaction)
    if failed_part == 'database':
        monkeypatch.setattr(v, 'rollback', Mock())
        monkeypatch.setattr(v, 'restore_database', Mock(side_effect=v.UpdateError('db failed')))
    else:
        monkeypatch.setattr(v, 'rollback', Mock(side_effect=v.UpdateError('code failed')))
        monkeypatch.setattr(v, 'restore_database', Mock())
    with pytest.raises(v.UpdateError, match='恢复失败'):
        v._rollback_transaction(request_path)
    state = v._load_transaction(request_path)
    assert state['phase'] == 'recovery_failed'
    assert state['code_changed'] is (failed_part == 'code')
    assert state['database_changed'] is (failed_part == 'database')
    assert v.active_transaction_path(json.loads(request_path.read_text(encoding='utf-8'))['root']).exists()


def test_cleanup_keeps_current_two_recoverable_versions_and_all_failures(repo):
    root, _, _ = repo
    base = v.metadata_dir(root); base.mkdir(parents=True, exist_ok=True)
    environments = base/'environments'; environments.mkdir()
    env_paths = {}
    for name in ('current', 'previous-1', 'previous-2', 'expired', 'failed', 'orphan'):
        folder = environments/name; folder.mkdir()
        python = v._environment_python(folder); python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        v.write_json(v._environment_marker(folder), {'state': 'ready', 'environment_id': name})
        env_paths[name] = python.resolve()
    v.write_environment_pointer(root, env_paths['current'])

    folders = {}
    for index, name in enumerate(('expired', 'previous-2', 'previous-1', 'current')):
        folder = base/name; folder.mkdir(); folders[name] = folder
        v.write_json(folder/v.TRANSACTION_FILE, {
            'phase': 'completed', 'updated_at': f'2026-10-{20 + index:02d}T00:00:00+00:00',
            'old_python': str(env_paths[name]), 'target_python': str(env_paths[name]),
        })
    failed = base/'failed-transaction'; failed.mkdir()
    v.write_json(failed/v.TRANSACTION_FILE, {
        'phase': 'recovery_failed', 'updated_at': '2026-10-25T00:00:00+00:00',
        'old_python': str(env_paths['failed']), 'target_python': str(env_paths['failed']),
    })
    v.cleanup_committed_transactions(root, folders['current'])
    assert not folders['expired'].exists()
    assert all(folders[name].exists() for name in ('current', 'previous-1', 'previous-2'))
    assert failed.exists()
    assert all(env_paths[name].parent.parent.exists()
               for name in ('current', 'previous-1', 'previous-2', 'failed'))
    assert not env_paths['expired'].parent.parent.exists()
    assert not env_paths['orphan'].parent.parent.exists()


def worker_kernel(order):
    return SimpleNamespace(OpenProcess=Mock(return_value=111), CloseHandle=Mock(),
                           CreateMutexW=Mock(return_value=222), GetLastError=Mock(return_value=0),
                           WaitForSingleObject=Mock(side_effect=lambda *args: order.append('wait') or 0))


@pytest.mark.skipif(os.name != 'nt', reason='Windows update handshake')
def test_worker_commits_only_after_actual_startup_ack(repo, tmp_path, monkeypatch):
    root, old, target, folder, lock, request_path = synthetic_worker_request(repo, tmp_path)
    order = []
    message = Mock()
    k = worker_kernel(order)
    monkeypatch.setattr(v.ctypes, 'windll', SimpleNamespace(kernel32=k, user32=SimpleNamespace(MessageBoxW=message)))
    def apply(*args):
        order.append('apply')
        return {'python': sys.executable, 'database': {'changed': False}}
    monkeypatch.setattr(v, 'apply_update', Mock(side_effect=apply))
    monkeypatch.setattr(v, 'require_web_closed', Mock())
    def launch(*args, **kwargs):
        order.append('launch')
        ack = kwargs['env'].get('APLUS_UPDATE_ACK')
        if ack:
            v.write_json(ack, {'started': True})
        return SimpleNamespace(pid=777, poll=lambda: None)
    launched = Mock(side_effect=launch)
    monkeypatch.setattr(v.subprocess, 'Popen', launched)
    def commit(path):
        tx = v._persist_transaction(path, 'completed', pointer_committed=True)
        v._clear_active_transaction(root, path)
        return {'action': 'block', 'transaction': tx}
    monkeypatch.setattr(v, 'recover_transaction', Mock(side_effect=commit))
    v.worker(request_path)
    receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    assert order == ['wait', 'apply', 'launch']
    assert receipt['state'] == 'completed'
    assert not lock.exists()
    assert message.call_args.args[2:] == ('Aplus更新完成', 0x40)


@pytest.mark.skipif(os.name != 'nt', reason='Windows update handshake')
def test_worker_popen_failure_uses_unified_persisted_rollback(repo, tmp_path, monkeypatch):
    root, old, target, folder, lock, request_path = synthetic_worker_request(repo, tmp_path)
    order = []
    k = worker_kernel(order)
    monkeypatch.setattr(v.ctypes, 'windll', SimpleNamespace(
        kernel32=k, user32=SimpleNamespace(MessageBoxW=Mock())))
    monkeypatch.setattr(v, 'require_web_closed', Mock())
    monkeypatch.setattr(v, 'apply_update', Mock(return_value={
        'python': sys.executable, 'database': {'changed': False}}))
    def recover(path):
        tx = v._persist_transaction(path, 'rolled_back', code_changed=False,
                                    database_changed=False, recovery={'outcome': 'rolled_back'})
        v._clear_active_transaction(root, path)
        return {'action': 'proceed', 'transaction': tx}
    monkeypatch.setattr(v, 'recover_transaction', Mock(side_effect=recover))
    monkeypatch.setattr(v.subprocess, 'Popen', Mock(side_effect=[OSError('create failed'),
                                                               SimpleNamespace(pid=2)]))
    v.worker(request_path)
    receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'rolled_back' and not lock.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows update handshake')
def test_worker_timeout_persists_unfinished_transaction_and_keeps_gate(repo, tmp_path, monkeypatch):
    root, old, target, folder, lock, request_path = synthetic_worker_request(repo, tmp_path)
    order = []
    k = worker_kernel(order)
    monkeypatch.setattr(v.ctypes, 'windll', SimpleNamespace(
        kernel32=k, user32=SimpleNamespace(MessageBoxW=Mock())))
    monkeypatch.setattr(v, 'require_web_closed', Mock())
    monkeypatch.setattr(v, 'apply_update', Mock(return_value={
        'python': sys.executable, 'database': {'changed': False}}))
    monkeypatch.setattr(v.subprocess, 'Popen', Mock(return_value=SimpleNamespace(pid=777, poll=lambda: None)))
    stamps = iter([0, 100])
    monkeypatch.setattr(v, 'supervise_delayed_start', Mock(return_value=None))
    monkeypatch.setattr(v.time, 'monotonic', lambda: next(stamps))
    recover = Mock()
    monkeypatch.setattr(v, 'recover_transaction', recover)
    v.worker(request_path)
    receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'startup_unconfirmed'
    assert v._load_transaction(request_path)['phase'] == 'startup_unconfirmed'
    assert lock.exists() and v.active_transaction_path(root).exists()
    recover.assert_not_called()


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


def test_real_closed_loopback_port_is_not_mistaken_for_uncertain_service():
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    # On this Windows host refusal arrives in ~2 seconds, not 0.3 seconds.
    # A real listening port and uncertain timeout remain rejected by other tests.
    v.require_web_closed(port)


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
c=w.version_controller
# Cancel the real startup check before processing GUI events: first paint can
# take over four seconds on a loaded desktop, firing a real network check.
w.after_cancel(c.startup_check_id)
c.startup_check_id=None
w.update()
assert c.button.master is w.section_nav
assert w.toolbar._on_scan_started == c.scan_hint
height=w.chart.winfo_height()
started=threading.Event(); done=threading.Event()
def checker(*args,**kwargs):
    started.set(); done.wait(3)
    return dict(state='available', can_update=False, message='synthetic')
c.checker=checker
before=time.monotonic(); c.startup_check()
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
