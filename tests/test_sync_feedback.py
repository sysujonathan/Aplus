"""Actionable failures must preserve existing desktop data."""
import os

import pytest

from tests.test_desktop_sync import sample, edit, state
from workbench import sync_client
from workbench.store import Store


@pytest.mark.parametrize('layer', ['', 'My Drive', '我的云端硬盘'])
def test_verified_remote_parent_is_discovered_without_recursive_search(tmp_path, monkeypatch, layer):
    monkeypatch.delenv('APLUS_SYNC_DIR', raising=False)
    store = Store(tmp_path / 'desktop')
    folder = tmp_path / layer / 'CyberMe原件备份/02-交易原件/Aplus桌面同步源'
    folder.mkdir(parents=True)
    assert '缺少 manifest.json' in sync_client.missing_sync_message(store, [tmp_path])
    (folder / 'manifest.json').write_text('{}', encoding='utf-8')
    assert sync_client.locate_sync_source(store, [tmp_path]) == folder.resolve()
    assert store.rows("SELECT value FROM meta WHERE key='sync_dir'") == [{'value': str(folder.resolve())}]


def test_missing_folder_empty_folder_and_override_have_different_next_steps(tmp_path, monkeypatch):
    monkeypatch.delenv('APLUS_SYNC_DIR', raising=False)
    store = Store(tmp_path / 'desktop')
    assert '未找到同步源文件夹' in sync_client.missing_sync_message(store, [tmp_path])
    folder = tmp_path / 'My Drive/02-交易原件/Aplus桌面同步源'
    folder.mkdir(parents=True)
    assert '缺少 manifest.json' in sync_client.missing_sync_message(store, [tmp_path])
    monkeypatch.setenv('APLUS_SYNC_DIR', '')
    assert '清除该设置' in sync_client.missing_sync_message(store, [tmp_path])


@pytest.mark.parametrize('fault,expected', [
    ('missing', '可离线使用'), ('version', '更新桌面程序'),
    ('corrupt', '内容校验失败'), ('expired', '已过期 8 天'),
    ('future', '日期在未来'),
])
def test_rejection_explains_next_step_without_writes(sample, monkeypatch, fault, expected):
    _, desktop, out = sample
    monkeypatch.setattr(sync_client, 'sync_age_days', lambda _: 1)
    if fault == 'missing':
        (out / 'manifest.json').unlink()
    elif fault == 'version':
        edit(out, lambda m: m.update(engine='wrong'))
    elif fault == 'corrupt':
        (out / 'manifest.json').write_text('{bad', encoding='utf-8')
    else:
        monkeypatch.setattr(sync_client, 'sync_age_days', lambda _: 8 if fault == 'expired' else -1)
    before = state(desktop)
    result = sync_client.apply_sync_source(desktop, out)
    assert result['status'] == 'rejected'
    assert expected in result['message']
    assert '仍为 原有 数据' in result['message']
    assert state(desktop) == before


def test_seven_day_boundary_and_expired_repeat(sample, monkeypatch):
    _, desktop, out = sample
    monkeypatch.setattr(sync_client, 'sync_age_days', lambda _: 7)
    assert sync_client.apply_sync_source(desktop, out)['status'] == 'applied'
    before = state(desktop)
    monkeypatch.setattr(sync_client, 'sync_age_days', lambda _: 8)
    assert '已过期 8 天' in sync_client.apply_sync_source(desktop, out)['message']
    assert state(desktop) == before


@pytest.mark.parametrize('blocked_step', ['查找同步源', '读取并校验旧格式行情'])
def test_desktop_sync_reports_timeout_while_drive_io_is_blocked(monkeypatch, sample, blocked_step):
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gui import toolbar

    _, store, source = sample
    entered = threading.Event()
    unblock = threading.Event()
    released = threading.Event()
    callbacks = []
    moment = [0]

    def wait_for_io():
        entered.set()
        assert unblock.wait(5)

    def locate(_store):
        if blocked_step == '查找同步源':
            wait_for_io()
        return source

    read = Path.read_bytes

    def read_bytes(path):
        if path.parent == source / 'market':
            wait_for_io()
        return read(path)

    monkeypatch.setattr(sync_client, 'locate_sync_source', locate)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(toolbar, 'time', SimpleNamespace(monotonic=lambda: moment[0]))
    view = SimpleNamespace(
        _job_id=None, _desktop_sync_running=False, btn_desktop_sync=Mock(),
        _header_data_var=Mock(), after=lambda _delay, callback: callbacks.append(callback),
        store=store, service=SimpleNamespace(reserve_idle=Mock(), release_idle=released.set),
    )
    toolbar.ToolBar._on_desktop_sync(view)
    try:
        assert entered.wait(2)
        moment[0] = 301
        callbacks.pop(0)()
        message = view._header_data_var.set.call_args.args[0]
        assert blocked_step in message and '超时' in message
        assert '重开' not in message
        assert not released.is_set()
    finally:
        unblock.set()
        assert released.wait(2)
    assert not store.rows("SELECT value FROM meta WHERE key='sync_asof'")


@pytest.mark.parametrize('failure', ['unsupported-volume', 'unimplemented'])
def test_legacy_import_without_hardlinks(sample, monkeypatch, failure):
    import errno
    import json
    from pathlib import Path

    from tests.test_desktop_sync import ASOF
    from workbench.sync_source import DIRECTORY

    attempts = []

    def no_hardlink(*_args):
        attempts.append(1)
        if failure == 'unimplemented':
            raise NotImplementedError
        raise OSError(errno.ENOTSUP, 'hard links unavailable')

    _, store, source = sample
    monkeypatch.setattr(Path, 'hardlink_to', no_hardlink)
    assert sync_client.apply_sync_source(store, source)['status'] == 'applied'
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    assert store.rows('SELECT * FROM datasets ORDER BY id') == sorted(
        manifest['datasets'], key=lambda row: row['id'])
    assert len(store.rows('SELECT id FROM observations')) == len(manifest['observations'])
    assert store.rows("SELECT value FROM meta WHERE key='sync_asof'") == [{'value': ASOF}]
    for relative in [DIRECTORY, manifest['calendar']['path'],
                     *[row['path'] for row in manifest['datasets']]]:
        assert (store.root / relative).read_bytes() == (source / relative).read_bytes()
    for row in manifest['datasets']:
        with pytest.raises(ValueError, match='拒绝覆盖'):
            store.write_immutable_sync_file(row['path'], b'conflicting bytes')
        assert (store.root / row['path']).read_bytes() == (source / row['path']).read_bytes()
    assert not list(store.root.rglob('.a-*.tmp'))
    assert len(attempts) == 1


def _exit_during_legacy_write(root, source, entry):
    """Drive the real exit callbacks with a small event loop in a disposable process."""
    import heapq
    import itertools
    import queue
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gui.main_window import AplusMainWindow
    from gui.toolbar import ToolBar
    from workbench.sync_source import DIRECTORY

    store = Store(root)
    entered = threading.Event()
    unblock = threading.Event()
    scheduled = []
    sequence = itertools.count()
    closed = False
    messages = []

    def after(delay, callback):
        callback_id = next(sequence)
        heapq.heappush(scheduled, (time.monotonic() + delay / 1000, callback_id, callback))
        return callback_id

    def destroy():
        nonlocal closed
        closed = True

    write = store.write_artifact

    def pause_after_directory(relative, content):
        result = write(relative, content)
        if relative == DIRECTORY and not entered.is_set():
            entered.set()
            assert unblock.wait(10)
        return result

    store.write_artifact = pause_after_directory
    sync_client.locate_sync_source = lambda _store: source
    sync_client.sync_age_days = lambda _day: 1
    service = SimpleNamespace(reserve_idle=Mock(), release_idle=Mock())
    view = SimpleNamespace(
        _job_id=None, _desktop_sync_running=False, btn_desktop_sync=Mock(),
        _header_data_var=SimpleNamespace(set=messages.append), _on_job_finished=None,
        after=after, store=store, service=service,
    )
    window = SimpleNamespace(
        toolbar=view, service=service, _closing=False, _tray_actions=queue.Queue(),
        _tray_icon=Mock() if entry == 'tray' else None, after=after, destroy=destroy,
        winfo_exists=lambda: not closed, deiconify=Mock(),
        lift=Mock(), title=messages.append,
    )
    for name in ('_poll_tray_actions', '_exit_from_tray', '_begin_exit_flush', '_finish_exit'):
        setattr(window, name, getattr(AplusMainWindow, name).__get__(window))

    ToolBar._on_desktop_sync(view)
    assert entered.wait(5)
    if entry == 'tray':
        window._tray_actions.put('quit')
        window._poll_tray_actions()
    else:
        # WM_DELETE_WINDOW uses this callback when the tray is unavailable.
        window._exit_from_tray()
    after(100, unblock.set)
    while not closed:
        deadline, _, callback = heapq.heappop(scheduled)
        time.sleep(max(0, deadline - time.monotonic()))
        callback()
    print('\n'.join(messages), flush=True)


@pytest.mark.parametrize('entry', ['tray', 'window'])
def test_exit_during_legacy_write_leaves_reopened_store_consistent(sample, entry):
    import subprocess
    import sys

    from tests.test_desktop_sync import PREVIOUS
    from workbench.sync_source import DIRECTORY

    _, store, source = sample
    calendar = 'sources/tickflow/trading_calendar.json'
    old_directory = b'code,code_name\nsh.600000,old\n'
    old_calendar = ('{"trading_days":["' + PREVIOUS + '"]}').encode()
    store.write_artifact(DIRECTORY, old_directory)
    store.write_artifact(calendar, old_calendar)
    store.execute("INSERT INTO meta VALUES('sync_asof', ?)", (PREVIOUS,))
    before = state(store)
    probe = subprocess.run(
        [sys.executable, '-c',
         'import sys; from tests.test_sync_feedback import _exit_during_legacy_write; '
         '_exit_during_legacy_write(*sys.argv[1:])',
         str(store.root), str(source), entry],
        capture_output=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'},
        timeout=20,
    )
    assert probe.returncode == 0, probe.stderr
    reopened = Store(store.root)
    assert (reopened.root / DIRECTORY).read_bytes() == old_directory
    assert (reopened.root / calendar).read_bytes() == old_calendar
    assert state(reopened) == before
    assert '等待' in probe.stdout and '回滚' in probe.stdout
