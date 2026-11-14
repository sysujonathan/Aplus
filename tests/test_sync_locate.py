from pathlib import Path

import pytest

from workbench import sync_client
from workbench.store import Store

RELATIVE = Path('02-交易原件') / 'Aplus桌面同步源'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('APLUS_SYNC_DIR', raising=False)
    return Store(tmp_path / 'desktop')


def source(path):
    path.mkdir(parents=True)
    (path / 'manifest.json').write_text('{}', encoding='utf-8')
    return path


def remembered(store):
    return store.rows("SELECT value FROM meta WHERE key='sync_dir'")


def remember(store, path):
    store.execute("INSERT INTO meta(key,value) VALUES('sync_dir',?)", (str(path),))


@pytest.mark.parametrize('layer', ['', '我的云端硬盘', 'My Drive'])
def test_discover_and_remember(store, tmp_path, layer):
    expected = source(tmp_path / layer / RELATIVE)
    assert sync_client.locate_sync_source(store, [tmp_path]) == expected
    assert remembered(store) == [{'value': str(expected.resolve())}]
    assert sync_client.locate_sync_source(store, []) == expected


def test_priority(store, tmp_path, monkeypatch):
    discovered = source(tmp_path / RELATIVE)
    saved = source(tmp_path / 'saved')
    override = source(tmp_path / 'override' / '2026-11-13')
    remember(store, saved)
    monkeypatch.setenv('APLUS_SYNC_DIR', str(override.parent))
    assert sync_client.locate_sync_source(store, [tmp_path]) == override
    assert remembered(store) == [{'value': str(saved)}]
    monkeypatch.delenv('APLUS_SYNC_DIR')
    assert sync_client.locate_sync_source(store, [tmp_path]) == saved
    assert discovered != saved


@pytest.mark.parametrize('override', ['', 'missing'])
def test_explicit_invalid_override_does_not_fall_back(store, tmp_path, monkeypatch, override):
    remember(store, source(tmp_path / 'saved'))
    source(tmp_path / RELATIVE)
    monkeypatch.setenv('APLUS_SYNC_DIR', override)
    assert sync_client.locate_sync_source(store, [tmp_path]) is None


def test_stale_memory_falls_back(store, tmp_path):
    remember(store, tmp_path / 'gone')
    expected = source(tmp_path / RELATIVE)
    assert sync_client.locate_sync_source(store, [tmp_path]) == expected
    assert remembered(store) == [{'value': str(expected.resolve())}]


@pytest.mark.parametrize('kind', ['empty', 'missing-manifest', 'child-only'])
def test_reject_wrong_candidate(store, tmp_path, kind):
    candidate = tmp_path / RELATIVE
    candidate.mkdir(parents=True)
    if kind == 'missing-manifest':
        (candidate / 'market.csv').write_text('incomplete', encoding='utf-8')
    elif kind == 'child-only':
        source(candidate / 'dated')
    assert sync_client.locate_sync_source(store, [tmp_path]) is None
    assert remembered(store) == []


def test_skip_empty_candidate(store, tmp_path):
    (tmp_path / RELATIVE).mkdir(parents=True)
    expected = source(tmp_path / 'My Drive' / RELATIVE)
    assert sync_client.locate_sync_source(store, [tmp_path]) == expected


def test_non_windows(store, monkeypatch):
    monkeypatch.setattr(sync_client.sys, 'platform', 'linux')
    assert sync_client.locate_sync_source(store) is None


def test_windows_existing_drives_only(monkeypatch):
    monkeypatch.setattr(sync_client.sys, 'platform', 'win32')
    probes = []

    class Drive:
        def __init__(self, name):
            self.name = name
            probes.append(name)

        def is_dir(self):
            if self.name.startswith('D:'):
                raise OSError('unavailable drive')
            return self.name[0] in 'CG'

    monkeypatch.setattr(sync_client, 'Path', Drive)
    assert [root.name for root in sync_client._sync_search_roots()] == ['C:\\', 'G:\\']
    assert len(probes) == 26


def test_corrupt_manifest_rejected_after_discovery(store, tmp_path):
    expected = source(tmp_path / RELATIVE)
    (expected / 'manifest.json').write_text('{broken', encoding='utf-8')
    located = sync_client.locate_sync_source(store, [tmp_path])
    before = {table: store.rows(f'SELECT * FROM {table}')
              for table in ('meta', 'datasets', 'observations')}
    assert sync_client.apply_sync_source(store, located)['status'] == 'rejected'
    assert {table: store.rows(f'SELECT * FROM {table}') for table in before} == before


@pytest.fixture
def toolbar(store):
    import threading
    import tkinter as tk
    from types import SimpleNamespace
    from unittest.mock import Mock

    # Tcl timers exercise the real Tk event queue without requiring a display.
    root = tk.Tcl()
    main_thread = threading.get_ident()

    def after(*args):
        assert threading.get_ident() == main_thread
        return root.after(*args)

    bar = SimpleNamespace(_job_id=None, _desktop_sync_running=False,
                          store=store, _header_data_var=Mock(),
                          btn_desktop_sync=Mock(), _on_job_finished=Mock(), after=after)
    yield bar, root
    for timer in root.tk.call('after', 'info'):
        root.after_cancel(timer)


def drain(toolbar):
    import time
    bar, root = toolbar
    deadline = time.monotonic() + 4
    while bar._desktop_sync_running and time.monotonic() < deadline:
        root.eval('update')
        time.sleep(.005)
    assert not bar._desktop_sync_running
    bar.btn_desktop_sync.state.assert_called_with(['!disabled'])


def test_toolbar_uses_locator_and_preserves_missing_message(toolbar, monkeypatch):
    from unittest.mock import Mock
    from gui.toolbar import ToolBar

    bar, _ = toolbar
    locator = Mock(return_value=None)
    monkeypatch.setattr(sync_client, 'locate_sync_source', locator)
    ToolBar._on_desktop_sync(bar)
    drain(toolbar)
    locator.assert_called_once_with(bar.store)
    bar._header_data_var.set.assert_called_once_with('未配置同步来源')
    bar._on_job_finished.assert_not_called()
    locator.reset_mock()
    bar._job_id = 'busy'
    ToolBar._on_desktop_sync(bar)
    locator.assert_not_called()
    bar._header_data_var.set.assert_called_with('正在忙，稍后再试')


@pytest.mark.parametrize('slow', ['database', 'drive'])
def test_toolbar_locator_io_keeps_tk_responsive(toolbar, tmp_path, monkeypatch, slow):
    import sqlite3
    import threading
    import time
    from gui.toolbar import ToolBar

    bar, root = toolbar
    expected = source(tmp_path / RELATIVE)
    entered = threading.Event()
    released = threading.Event()
    worker_threads = []
    main_thread = threading.get_ident()
    original_rows, original_execute = bar.store.rows, bar.store.execute

    def rows(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        entered.set()
        return original_rows(*args, **kwargs)

    def execute(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(bar.store, 'rows', rows)
    monkeypatch.setattr(bar.store, 'execute', execute)

    def roots():
        worker_threads.append(threading.get_ident())
        if slow == 'drive':
            entered.set()
            time.sleep(.7)
            released.set()
        return [tmp_path]

    monkeypatch.setattr(sync_client, '_sync_search_roots', roots)
    applied = []

    def apply(store, path):
        worker_threads.append(threading.get_ident())
        applied.append(path)
        return {'status': 'applied', 'message': '同步完成'}

    monkeypatch.setattr(sync_client, 'apply_sync_source', apply)
    locker = None
    if slow == 'database':
        locked = threading.Event()

        def lock():
            with sqlite3.connect(bar.store.path) as db:
                db.execute('BEGIN EXCLUSIVE')
                locked.set()
                time.sleep(.7)
                db.rollback()
            released.set()

        locker = threading.Thread(target=lock)
        locker.start()
        assert locked.wait(1)

    started = time.monotonic()
    ToolBar._on_desktop_sync(bar)
    elapsed = time.monotonic() - started
    assert elapsed < .2, elapsed
    assert bar._desktop_sync_running
    assert entered.wait(.2)
    # Repeated clicks must not launch another locator while this one is blocked.
    ToolBar._on_desktop_sync(bar)
    heartbeats = []
    root.after(20, lambda: heartbeats.append((time.monotonic(), released.is_set())))
    drain(toolbar)
    if locker:
        locker.join(1)
    assert heartbeats and heartbeats[0][1] is False
    assert heartbeats[0][0] - started < .3
    assert released.is_set()
    assert applied == [expected.resolve()]
    assert all(thread != main_thread for thread in worker_threads)
    assert original_rows("SELECT value FROM meta WHERE key='sync_dir'") == [{'value': str(expected.resolve())}]
    bar._on_job_finished.assert_called_once_with('同步', 'completed', None)
    bar._header_data_var.set.assert_called_with('同步完成')


@pytest.mark.parametrize('stage', ['locate', 'remember', 'apply', 'failure-message', 'thread-start'])
def test_toolbar_sync_failures_restore_button(toolbar, monkeypatch, stage):
    from unittest.mock import Mock
    from gui.toolbar import ToolBar
    import gui.toolbar as module

    bar, _ = toolbar
    if stage == 'thread-start':
        monkeypatch.setattr(module.threading, 'Thread', Mock(side_effect=RuntimeError('start')))
    elif stage in ('locate', 'failure-message'):
        monkeypatch.setattr(sync_client, 'locate_sync_source', Mock(side_effect=OSError('drive')))
    elif stage == 'remember':
        monkeypatch.setattr(bar.store, 'execute', Mock(side_effect=OSError('write')))
        # Exercise the real remembered-path persistence.
        saved = source(bar.store.root / 'saved')
        original_rows = bar.store.rows
        monkeypatch.setattr(bar.store, 'rows', lambda sql: [{'value': str(saved)}]
                            if "key='sync_dir'" in sql else original_rows(sql))
    else:
        monkeypatch.setattr(sync_client, 'locate_sync_source', Mock(return_value=bar.store.root))
        monkeypatch.setattr(sync_client, 'apply_sync_source', Mock(side_effect=OSError('apply')))
    if stage == 'failure-message':
        monkeypatch.setattr(bar.store, 'rows', Mock(side_effect=OSError('database')))
    ToolBar._on_desktop_sync(bar)
    drain(toolbar)
    bar._header_data_var.set.assert_called_with('同步失败，仍为 原有 数据')
    bar._on_job_finished.assert_called_once_with('同步', 'failed', None)
