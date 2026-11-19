"""Actionable failures must preserve existing desktop data."""
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
        assert not released.is_set()
    finally:
        unblock.set()
        assert released.wait(2)
    assert not store.rows("SELECT value FROM meta WHERE key='sync_asof'")
