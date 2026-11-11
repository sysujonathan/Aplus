"""Issue #131: backup rotation converges to the newest verified backup.

Synthetic install state only — a real tiny SQLite file per backup so the
keeper's integrity check runs for real, receipts modelled on
installed_data.bind_existing / installed_migration.prepare.
"""
import json
from pathlib import Path
import sqlite3

import pytest

from workbench import installed_backup_rotation as rotation
from workbench.installed_data import atomic_json, file_sha256


def _database(path, marker='x'):
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    connection.execute('INSERT INTO meta VALUES (?, ?)', ('marker', marker))
    connection.commit()
    connection.close()
    return file_sha256(path)


def _bind_backup(home, runtime, index, *, tamper=False, extra=None):
    stamp = f'202611{index:02d}T000000Z-{index:08x}'
    folder = home / 'backups' / stamp
    folder.mkdir(parents=True)
    digest = _database(folder / 'workbench.sqlite3', f'backup-{index}')
    if tamper:
        with (folder / 'workbench.sqlite3').open('ab') as stream:
            stream.write(b'tampered')
    atomic_json(folder / 'receipt.json', dict(
        info=dict(path=str(runtime), schema=9, counts={}, latest=None, active=0),
        source_sha256=digest, backup_sha256=digest))
    if extra:
        (folder / extra).write_text('user note')
    return folder


@pytest.fixture
def state(tmp_path):
    home = tmp_path / 'installed'
    runtime = tmp_path / 'runtime'
    root = tmp_path / 'program'
    runtime.mkdir(parents=True)
    root.mkdir()
    sentinel = _database(runtime / 'workbench.sqlite3', 'live-runtime')
    return home, runtime, root, sentinel


def _bind(home, runtime, newest):
    atomic_json(home / 'data.json', dict(protocol=1, runtime=str(runtime), existing=True,
                                         backup=str(newest / 'workbench.sqlite3'), schema_before=9))


def _fifteen(state):
    home, runtime, root, sentinel = state
    folders = [_bind_backup(home, runtime, i) for i in range(1, 16)]
    _bind(home, runtime, folders[-1])
    return folders


def test_fifteen_backups_converge_to_newest_verified(state):
    home, runtime, root, sentinel = state
    folders = _fifteen(state)
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    assert result['removed'] == [f.name for f in folders[:-1]]
    assert [p.name for p in (home / 'backups').iterdir()] == [folders[-1].name]
    assert file_sha256(runtime / 'workbench.sqlite3') == sentinel
    again = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert again['removed'] == [] and again['blocked'] is None
    assert json.loads((home / 'backup-rotation.json').read_text())['kept'] == [folders[-1].name]


def test_corrupt_newest_blocks_everything(state):
    home, runtime, root, sentinel = state
    folders = [_bind_backup(home, runtime, i) for i in range(1, 5)]
    folders.append(_bind_backup(home, runtime, 5, tamper=True))
    _bind(home, runtime, folders[-1])
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] and result['removed'] == []
    assert len(list((home / 'backups').iterdir())) == 5


def test_update_pending_blocks_everything(state):
    home, runtime, root, sentinel = state
    _fifteen(state)
    (root / 'update-pending.json').write_text('{}')
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] == 'update pending'
    assert len(list((home / 'backups').iterdir())) == 15


def test_unfinished_recovery_blocks_everything(state):
    home, runtime, root, sentinel = state
    _fifteen(state)
    folder = home / 'backups' / 'recovery-20261101T000000Z-abcdef01'
    folder.mkdir()
    (folder / 'workbench.sqlite3').write_bytes(b'snapshot')
    atomic_json(folder / 'receipt.json', dict(runtime=str(runtime), files={}, state='snapshot_saved'))
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] and result['removed'] == []


def test_completed_recovery_snapshot_is_retained_not_deleted(state):
    home, runtime, root, sentinel = state
    folders = _fifteen(state)
    folder = home / 'backups' / 'recovery-20261101T000000Z-abcdef01'
    folder.mkdir()
    (folder / 'workbench.sqlite3').write_bytes(b'snapshot')
    atomic_json(folder / 'receipt.json', dict(runtime=str(runtime), files={}, state='recovered'))
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    assert folder.exists() and result['skipped'][folder.name] == 'recovery snapshot retained'


def test_binding_pointer_backup_kept_even_when_not_newest(state):
    home, runtime, root, sentinel = state
    folders = [_bind_backup(home, runtime, i) for i in range(1, 6)]
    _bind(home, runtime, folders[2])
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    remaining = {p.name for p in (home / 'backups').iterdir()}
    assert remaining == {folders[2].name, folders[4].name}


def test_unknown_foreign_and_augmented_folders_are_kept(state, tmp_path):
    home, runtime, root, sentinel = state
    folders = [_bind_backup(home, runtime, i) for i in range(1, 6)]
    _bind(home, runtime, folders[-1])
    mine = home / 'backups' / 'my-own-backup'
    mine.mkdir()
    (mine / 'workbench.sqlite3').write_bytes(b'mine')
    other_runtime = tmp_path / 'other-runtime'
    other_runtime.mkdir()
    foreign = _bind_backup(home, other_runtime, 9)
    augmented = folders[1]
    (augmented / 'notes.txt').write_text('user note')
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    assert mine.exists() and foreign.exists() and augmented.exists()
    assert folders[-1].exists()


def test_symlinked_backup_is_kept(state, tmp_path):
    home, runtime, root, sentinel = state
    folders = [_bind_backup(home, runtime, i) for i in range(1, 5)]
    _bind(home, runtime, folders[-1])
    target = tmp_path / 'elsewhere.sqlite3'
    target.write_bytes(b'elsewhere')
    link_folder = folders[1]
    (link_folder / 'workbench.sqlite3').unlink()
    (link_folder / 'workbench.sqlite3').symlink_to(target)
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    assert link_folder.exists()


def _migration_attempt(home, name, state_value, *, bad_shadow=False):
    attempt = home / 'updates' / name
    migration = attempt / 'migration' / 'shadow'
    migration.mkdir(parents=True)
    original = attempt / 'migration' / 'original.sqlite3'
    shadow = migration / 'workbench.sqlite3'
    original_digest = _database(original, 'original-' + name)
    shadow_digest = _database(shadow, 'shadow-' + name)
    atomic_json(attempt / 'migration' / 'receipt.json', dict(
        protocol=1, from_schema=8, to_schema=9, state='prepared',
        original_sha256=original_digest,
        migrated_sha256=('0' * 64 if bad_shadow else shadow_digest)))
    atomic_json(attempt / 'receipt.json', dict(state=state_value, version='2026.11.11.1'))
    return attempt


def test_completed_migration_copies_swept_receipts_kept(state):
    home, runtime, root, sentinel = state
    _fifteen(state)
    done1 = _migration_attempt(home, 'attempt-a', 'completed')
    done2 = _migration_attempt(home, 'attempt-b', 'completed')
    rolled = _migration_attempt(home, 'attempt-c', 'rolled_back')
    damaged = _migration_attempt(home, 'attempt-d', 'completed', bad_shadow=True)
    result = rotation.rotate_backups(home, install_root=root, runtime=runtime)
    assert result['blocked'] is None
    for attempt in (done1, done2):
        assert not (attempt / 'migration' / 'original.sqlite3').exists()
        assert not (attempt / 'migration' / 'shadow').exists()
        assert (attempt / 'migration' / 'receipt.json').exists()
        assert (attempt / 'receipt.json').exists()
    assert (rolled / 'migration' / 'original.sqlite3').exists()
    assert (damaged / 'migration' / 'original.sqlite3').exists()
    assert 'updates/attempt-d/migration' in result['skipped']
