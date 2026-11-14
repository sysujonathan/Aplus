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


def test_toolbar_uses_locator_and_preserves_missing_message(store, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from gui.toolbar import ToolBar

    toolbar = SimpleNamespace(_job_id=None, _desktop_sync_running=False,
                              store=store, _header_data_var=Mock())
    locator = Mock(return_value=None)
    monkeypatch.setattr(sync_client, 'locate_sync_source', locator)
    ToolBar._on_desktop_sync(toolbar)
    locator.assert_called_once_with(store)
    toolbar._header_data_var.set.assert_called_once_with('未配置同步来源')
    locator.reset_mock()
    toolbar._job_id = 'busy'
    ToolBar._on_desktop_sync(toolbar)
    locator.assert_not_called()
    toolbar._header_data_var.set.assert_called_with('正在忙，稍后再试')
