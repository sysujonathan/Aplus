"""Rotate installed data backups: keep only the newest verified backup.

Bind and update flows snapshot the whole database on every run, so without
rotation the install state grows by one full copy per event forever
(Issue #131). This module converges ``backups/`` to the newest backup that
is provably Aplus-owned, receipt-carrying, hash-matching and bound to the
currently bound runtime, and retires the big original/shadow copies of
completed update migrations while keeping their receipts.

Conservative by construction:
- any global doubt (an update is pending, the binding is unreadable or
  points at another runtime, an unfinished recovery exists, or the newest
  backup does not fully validate) aborts the rotation: nothing is deleted;
- anything not provably ours (unknown folder names, extra files, linked
  paths, missing/invalid receipts, hash mismatches, other runtimes) is
  kept folder-by-folder;
- the backup ``data.json`` currently points at is always kept;
- deletion is file-by-file with parent checks, never recursive force.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from .installed_data import atomic_json, file_sha256, state_home

_BIND_NAME = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{8}$')
_RECOVERY_NAME = re.compile(r'^recovery-(\d{8}T\d{6}Z-[0-9a-f]{8})$')
_BIND_FILES = {'workbench.sqlite3', 'receipt.json'}


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _folder_bytes(folder):
    total = 0
    for path in folder.rglob('*'):
        try:
            if path.is_file() and not _linked(path):
                total += path.stat().st_size
        except OSError:
            pass
    return total


def _remove_folder_files(folder, keep_names=()):
    """Unlink every file except keep_names, deepest dirs last.

    Parent-of-record is re-verified before each unlink; anything that has
    moved or linked aborts this folder (already-unlinked files stay gone —
    callers only pass folders holding validated redundant copies).
    """
    folder = Path(folder)
    files = []
    directories = []
    for directory, children, names in os.walk(folder, followlinks=False):
        base = Path(directory)
        if _linked(base):
            raise ValueError('linked directory')
        for name in children:
            if _linked(base / name):
                raise ValueError('linked item')
            directories.append(base / name)
        for name in names:
            if _linked(base / name):
                raise ValueError('linked item')
            files.append(base / name)
    for path in files:
        if path.name in keep_names and path.parent == folder:
            continue
        if path.resolve().parent != folder and folder not in path.resolve().parents:
            raise ValueError('payload moved')
        path.unlink()
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        directory.rmdir()
    if not keep_names:
        folder.rmdir()


def _classify_backup(folder, runtime):
    """Return the stamp for a provably Aplus-owned bind-backup folder.

    Raises ValueError with a keep-reason for anything else. Hash validation
    of the database file happens in _validate_backup; this step checks
    shape, receipt identity and runtime ownership only. Recovery snapshots
    are deliberately not rotation candidates (see rotate_backups).
    """
    name = folder.name
    if _BIND_NAME.match(name):
        receipt = _read_json(folder / 'receipt.json')
        if not isinstance(receipt, dict):
            raise ValueError('receipt missing')
        info = receipt.get('info') or {}
        if Path(str(info.get('path', ''))).resolve() != runtime:
            raise ValueError('other runtime')
        if (not re.fullmatch('[0-9a-f]{64}', str(receipt.get('backup_sha256', '')))
                or receipt.get('backup_sha256') != receipt.get('source_sha256')):
            raise ValueError('receipt hashes incomplete')
        names = {p.name for p in folder.iterdir()}
        if names != _BIND_FILES:
            raise ValueError('unknown contents')
        return name.split('-')[0], 'bind'
    raise ValueError('unknown folder')


def _validate_backup(folder, *, full):
    database = folder / 'workbench.sqlite3'
    if _linked(folder) or _linked(database):
        raise ValueError('linked backup')
    receipt = _read_json(folder / 'receipt.json')
    if file_sha256(database) != receipt['backup_sha256']:
        raise ValueError('backup hash mismatch')
    if full:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('integrity check failed')


def _sweep_migrations(home, result):
    updates = home / 'updates'
    if not updates.is_dir() or _linked(updates):
        return
    eligible = []
    for attempt in sorted(updates.iterdir()):
        migration = attempt / 'migration'
        if not attempt.is_dir() or _linked(attempt) or not migration.is_dir():
            continue
        label = 'updates/' + attempt.name + '/migration'
        receipt = _read_json(attempt / 'receipt.json')
        if not isinstance(receipt, dict) or receipt.get('state') != 'completed':
            result['skipped'][label] = 'update not completed'
            continue
        plan = _read_json(migration / 'receipt.json')
        original = migration / 'original.sqlite3'
        shadow = migration / 'shadow' / 'workbench.sqlite3'
        if not isinstance(plan, dict) or not original.is_file() or not shadow.is_file():
            result['skipped'][label] = 'migration copies absent'
            continue
        try:
            if _linked(migration) or _linked(original) or _linked(shadow):
                raise ValueError('linked migration copy')
            if file_sha256(original) != plan.get('original_sha256'):
                raise ValueError('original hash mismatch')
            if file_sha256(shadow) != plan.get('migrated_sha256'):
                raise ValueError('shadow hash mismatch')
        except (OSError, ValueError) as exc:
            result['skipped'][label] = str(exc)
            continue
        try:
            stamp = (attempt / 'receipt.json').stat().st_mtime
        except OSError:
            stamp = 0.0
        eligible.append((stamp, attempt.name, label, migration, original, shadow))
    if not eligible:
        return
    # The most recent completed attempt's migration copies are that update's
    # rollback original; the handoff contract keeps them. Only older attempts
    # are swept, so accumulation still converges one update later.
    eligible.sort()
    *older, newest = eligible
    result['skipped'][newest[2]] = 'latest completed migration retained as rollback copy'
    for _stamp, _name, label, migration, original, shadow in older:
        try:
            freed = original.stat().st_size + shadow.stat().st_size
            original.unlink()
            shadow.unlink()
            (migration / 'shadow').rmdir()
            result['removed'].append(label)
            result['freed'] = result.get('freed', 0) + freed
        except OSError as exc:
            result['skipped'][label] = str(exc)


def rotate_backups(home=None, *, install_root=None, runtime=None):
    """Converge installed backups; see module docstring for the contract.

    Returns a result dict; ``blocked`` names the global reason when nothing
    was deleted. Never raises for filesystem/state problems.
    """
    home = Path(home or state_home())
    result = dict(kept=[], removed=[], skipped={}, blocked=None, freed=0)
    binding = _read_json(home / 'data.json')
    if not isinstance(binding, dict) or not binding.get('runtime'):
        result['blocked'] = 'binding unreadable'
        return result
    bound = Path(str(binding['runtime'])).resolve()
    if runtime is not None and Path(runtime).resolve() != bound:
        result['blocked'] = 'runtime mismatch'
        return result
    runtime = bound
    root = Path(install_root) if install_root else (
        Path(os.environ['APLUS_INSTALL_ROOT']) if os.environ.get('APLUS_INSTALL_ROOT') else None)
    if root is not None and (Path(root) / 'update-pending.json').exists():
        result['blocked'] = 'update pending'
        return result
    backups = home / 'backups'
    if not backups.is_dir():
        result['blocked'] = 'no backups directory'
        return result
    pointer = None
    if binding.get('backup'):
        pointer = Path(str(binding['backup'])).parent.resolve()

    candidates = []
    for folder in sorted(p for p in backups.iterdir() if p.is_dir()):
        try:
            if _RECOVERY_NAME.match(folder.name):
                # Recovery snapshots are forensic evidence, never rotation
                # candidates. One still in flight (or failed unexplained)
                # means recovery state is unresolved: delete nothing.
                receipt = _read_json(folder / 'receipt.json')
                state = receipt.get('state') if isinstance(receipt, dict) else None
                if state in ('snapshot_saved', 'recovery_failed'):
                    result['blocked'] = 'unfinished recovery: ' + folder.name
                    return result
                result['skipped'][folder.name] = 'recovery snapshot retained'
                continue
            stamp = _classify_backup(folder, runtime)
            candidates.append((stamp, folder))
        except ValueError as exc:
            result['skipped'][folder.name] = str(exc)
        except OSError as exc:
            result['skipped'][folder.name] = str(exc)
    if candidates:
        candidates.sort(key=lambda item: item[0])
        keeper = candidates[-1][1]
        try:
            _validate_backup(keeper, full=True)
        except (OSError, ValueError, sqlite3.Error) as exc:
            result['blocked'] = 'newest backup invalid: ' + str(exc)
            return result
        for stamp, folder in candidates:
            if folder == keeper or (pointer is not None and folder.resolve() == pointer):
                result['kept'].append(folder.name)
                continue
            try:
                _validate_backup(folder, full=False)
                freed = _folder_bytes(folder)
                _remove_folder_files(folder)
                result['removed'].append(folder.name)
                result['freed'] += freed
            except (OSError, ValueError, sqlite3.Error) as exc:
                result['skipped'][folder.name] = str(exc)
    _sweep_migrations(home, result)
    result['free_after'] = shutil.disk_usage(home).free
    try:
        atomic_json(home / 'backup-rotation.json', result)
    except OSError:
        pass
    return result
