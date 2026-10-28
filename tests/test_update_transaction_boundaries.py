"""Release metadata and recovery faults use synthetic repositories/databases only."""
import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from workbench import version_update as v
from tests.test_version_update import (
    repo, git, api_for, plan_for, installation_contract,
    interrupted_transaction, allow_isolated_recovery,
)


def metadata_release(repo, change):
    root, _, _ = repo
    lock = b''
    (root/'requirements-lock.txt').write_bytes(lock)
    source = installation_contract(lock)
    v.write_json(root/v.RELEASE_INSTALLATION, source)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'installation baseline')
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'metadata-release')
    target_contract = json.loads(json.dumps(source))
    change(target_contract)
    v.write_json(root/v.RELEASE_INSTALLATION, target_contract)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'metadata change')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main')
    git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, old, target


def test_manifest_only_python_change_cannot_use_missing_runtime(repo):
    def change(c):
        c['python'] = '3.99'
        c['environment_id'] = 'py399-' + c['requirements_sha256'][:12]
    root, old, target = metadata_release(repo, change)
    result = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not result['can_update'] and result['manual_kind'] == 'python'
    assert git(root, 'rev-parse', 'HEAD') == old


def test_forged_environment_identity_is_rejected(repo):
    root, _, target = metadata_release(repo, lambda c: c.update(environment_id='py312-1234567890ab'))
    result = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not result['can_update'] and result['manual_kind'] == 'installation'


def test_unchanged_environment_is_not_recreated(repo):
    root, _, target = metadata_release(repo, lambda c: c['database'].update(migration='store-v8-reviewed'))
    result = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert result['can_update'] and not result['installation']['environment_changed']


def test_manifest_dependency_identity_change_is_an_environment_change(monkeypatch):
    source = installation_contract(b'old==1\n')
    target = installation_contract(b'new==2\n', hashes=b'trusted-hash-lock')
    monkeypatch.setattr(v, 'release_installation', lambda root, revision, **kw:
                        source if revision == 'old' else target)
    assert v.installation_plan('.', 'old', 'new', set())['environment_changed']


def test_missing_schema_migration_path_is_rejected(repo):
    def change(c):
        c['database'].update(schema=9, supported_from=[9])
    root, _, target = metadata_release(repo, change)
    result = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not result['can_update'] and result['manual_kind'] == 'data'


@pytest.mark.parametrize('schema,allowed', [(9, True), (7, False)])
def test_manifest_only_schema_change_requires_migration_or_blocks_downgrade(repo, schema, allowed):
    root, _, target = metadata_release(repo, lambda c: c['database'].update(schema=schema))
    result = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert result['can_update'] is allowed
    if allowed:
        assert result['installation']['database_changed']
    else:
        assert result['manual_kind'] == 'data'


@pytest.mark.parametrize('branch', ['main', 'codex/local-release-20261028'])
def test_rollback_preserves_branch_and_next_update_is_available(repo, tmp_path, branch):
    root, old, target = repo
    if branch != 'main':
        git(root, 'switch', '-c', branch)
    plan = plan_for(repo)
    assert plan['can_update']
    v.apply_update(root, plan, tmp_path/'before.zip', sys.executable,
                   api=api_for(target), smoke=Mock())
    v.rollback(root, target, old, branch)
    assert git(root, 'branch', '--show-current') == branch
    assert git(root, 'rev-parse', 'HEAD') == old
    again = plan_for(repo)
    assert again['can_update']
    v.apply_update(root, again, tmp_path/'retry.zip', sys.executable,
                   api=api_for(target), smoke=Mock())
    assert git(root, 'rev-parse', 'HEAD') == target


def make_database(runtime, schema=8):
    runtime.mkdir(exist_ok=True)
    database = runtime/'workbench.sqlite3'
    with contextlib.closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO meta VALUES(?,?)', ('schema_version', str(schema)))
        db.execute('CREATE TABLE executions(id TEXT PRIMARY KEY,amount INTEGER)')
        db.execute("INSERT INTO executions VALUES('old',100)")
    return database


@pytest.mark.parametrize('actual', [7, 10])
def test_actual_schema_mismatch_stops_even_without_static_store_change(tmp_path, actual):
    runtime = tmp_path/'runtime'; make_database(runtime, actual)
    with pytest.raises(v.UpdateError, match='版本与迁移计划'):
        v.prepare_database_migration(tmp_path, sys.executable, runtime, tmp_path,
                                     {'database_changed': False, 'source_schema': 8, 'target_schema': 8})


def test_schema_declaration_without_migrator_fails_on_copy(tmp_path):
    runtime = tmp_path/'runtime'; database = make_database(runtime)
    stage = tmp_path/'stage'; (stage/'workbench').mkdir(parents=True)
    (stage/'workbench'/'__init__.py').touch()
    (stage/'workbench'/'store.py').write_text('class Store:\n def __init__(self,root): pass\n')
    folder = tmp_path/'transaction'; folder.mkdir()
    with pytest.raises(v.UpdateError, match='迁移后的版本'):
        v.prepare_database_migration(stage, sys.executable, runtime, folder,
                                     {'database_changed': True, 'source_schema': 8, 'target_schema': 9})
    assert v._database_state(database)['schema'] == 8


def test_wal_write_with_unchanged_main_file_blocks_restore(tmp_path):
    runtime = tmp_path/'runtime'; database = make_database(runtime)
    backup = tmp_path/'backup.sqlite3'; v._sqlite_snapshot(database, backup)
    with contextlib.closing(sqlite3.connect(database)) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        db.execute('PRAGMA wal_autocheckpoint=0')
        baseline = v._database_state(database)
        raw_before = v._digest(database)
        db.execute("INSERT INTO executions VALUES('new',250)"); db.commit()
        assert v._digest(database) == raw_before
        transaction = dict(changed=True, database=str(database), backup=str(backup),
                           backup_sha256=v._digest(backup), schema_before=8,
                           post_logical_sha256=baseline['logical_sha256'])
        with pytest.raises(v.UpdateError, match='新增交易记录'):
            v.restore_database(transaction, expected_current={raw_before})
        assert db.execute("SELECT amount FROM executions WHERE id='new'").fetchone()[0] == 250


def test_checkpoint_without_new_logical_writes_allows_restore(tmp_path):
    runtime = tmp_path/'runtime'; database = make_database(runtime)
    backup = tmp_path/'backup.sqlite3'; v._sqlite_snapshot(database, backup)
    with contextlib.closing(sqlite3.connect(database)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute("UPDATE meta SET value='9' WHERE key='schema_version'"); db.commit()
        before_checkpoint = v._database_state(database)
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    transaction = dict(changed=True, database=str(database), backup=str(backup),
                       backup_sha256=v._digest(backup), schema_before=8,
                       post_logical_sha256=before_checkpoint['logical_sha256'])
    v.restore_database(transaction, expected_current={before_checkpoint['sha256']})
    assert v._database_state(database)['schema'] == 8


def test_late_startup_ack_is_committed_by_supervisor_without_second_launch(repo, tmp_path, monkeypatch):
    root, _, _, folder, request = interrupted_transaction(repo, tmp_path, 'startup_unconfirmed')
    allow_isolated_recovery(monkeypatch, alive=True)
    launch = Mock(wraps=v.subprocess.Popen); monkeypatch.setattr(v.subprocess, 'Popen', launch)
    monkeypatch.setattr(v.time, 'sleep', lambda _: v.write_json(folder/'started.json', {'started': True}))
    outcome = v.supervise_delayed_start(request, Mock(poll=lambda: None), timeout=5)
    assert outcome['transaction']['phase'] == 'completed'
    assert not v.active_transaction_path(root).exists()
    assert all('launch_dashboard.py' not in str(call.args) for call in launch.call_args_list)
