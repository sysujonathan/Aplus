"""Review regressions: bounded image reads, old-case resume and native clips."""
import base64
import ast
import ctypes
import io
import os
from pathlib import Path
import sys
from unittest.mock import Mock

from PIL import Image
import pytest

from gui import growth_bridge as bridge_module
from gui.growth_bridge import GrowthBridge
from tools import acceptance_h2_replay
from workbench.store import Store
from workbench.version_update import compatible_store_extension


def png(color='navy'):
    output = io.BytesIO()
    Image.new('RGB', (80, 45), color).save(output, 'PNG')
    return output.getvalue()


def case(store, images=1, **metadata):
    return store.pa_save_case([dict(name='test.png', data=png()) for _ in range(images)],
                              dict(data_kind='test', **metadata), allow_duplicate=True)['case']


def test_case_open_and_metadata_edit_do_not_read_any_original(tmp_path, monkeypatch):
    store = Store(tmp_path)
    saved = case(store, images=8)
    read = Mock(side_effect=AssertionError('opening metadata must not load images'))
    monkeypatch.setattr(store, 'pa_image', read)
    bridge = GrowthBridge(store)
    result = bridge.get_case(saved['id'])
    assert result['ok'] and len(result['case']['images']) == 8
    assert all('data_url' not in row for row in result['case']['images'])
    updated = bridge.update_case(saved['id'], dict(memory='only metadata'))
    assert updated['ok'] and updated['case']['memory'] == 'only metadata'
    assert all('data_url' not in row for row in updated['case']['images'])
    read.assert_not_called()


def test_selected_image_is_exact_and_other_cases_image_is_not_read(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first, second = case(store, images=3), case(store)
    original = store.pa_image
    read = Mock(wraps=original)
    monkeypatch.setattr(store, 'pa_image', read)
    bridge = GrowthBridge(store)
    wanted = first['images'][1]['id']
    result = bridge.get_image(first['id'], wanted)
    assert result['ok'] and base64.b64decode(result['image']['data_url'].split(',', 1)[1]) == png()
    read.assert_called_once_with(wanted)
    read.reset_mock()
    assert not bridge.get_image(first['id'], second['images'][0]['id'])['ok']
    assert not bridge.get_image('not-owned', wanted)['ok']
    assert not bridge.get_image(first['id'], wanted, 'true')['ok']
    read.assert_not_called()
    preview = bridge.get_image(first['id'], wanted, True)
    assert preview['ok'] and preview['image']['data_url'].startswith('data:image/jpeg;base64,')


def test_resume_past_first_hundred_and_one_lightweight_metadata_read(tmp_path, monkeypatch):
    store = Store(tmp_path)
    oldest = case(store, title='old inspected case')
    with store.pa_write():
        for index in range(105):
            case(store, title='new ' + str(index))
    store.pa_case(oldest['id'])
    metadata = Mock(wraps=store.pa_case_metadata)
    search = Mock(wraps=store.pa_search)
    monkeypatch.setattr(store, 'pa_case_metadata', metadata)
    monkeypatch.setattr(store, 'pa_search', search)
    result = GrowthBridge(store).bootstrap()
    assert result['ok'], result
    assert len(result['cases']) == 100 and result['has_more']
    assert oldest['id'] not in {row['id'] for row in result['cases']}
    assert result['recent_case_id'] == result['recent_case']['id'] == oldest['id']
    assert result['recent_case']['title'] == 'old inspected case'
    metadata.assert_called_once_with()
    search.assert_called_once_with(limit=101)


def test_compare_uses_case_callback_instead_of_returning_and_covering_history(tmp_path):
    store = Store(tmp_path)
    saved = case(store)
    bridge = GrowthBridge(store, dict(comparison=True))
    bridge._return_chart, bridge._compare_case = Mock(), Mock()
    result = bridge.compare_case(saved['id'])
    assert result['ok'] and result['case_id'] == saved['id']
    bridge._compare_case.assert_called_once_with(saved['id'])
    bridge._return_chart.assert_not_called()
    assert not GrowthBridge(store).compare_case(saved['id'])['ok']


class NativeFunction:
    def __init__(self, function):
        self.function = function
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.function(*args)


def native_clipboard(monkeypatch, raw, *, size=None, locked=True):
    """Keep a real bounded allocation; mock only the Windows API boundaries."""
    from types import SimpleNamespace
    buffer = ctypes.create_string_buffer(raw)
    user = SimpleNamespace(
        OpenClipboard=NativeFunction(lambda *_: True),
        RegisterClipboardFormatW=NativeFunction(lambda *_: 1),
        GetClipboardData=NativeFunction(lambda *_: 2),
        CloseClipboard=NativeFunction(lambda: True),
    )
    kernel = SimpleNamespace(
        GlobalSize=NativeFunction(lambda *_: len(raw) if size is None else size),
        GlobalLock=NativeFunction(lambda *_: ctypes.addressof(buffer) if locked else 0),
        GlobalUnlock=NativeFunction(lambda *_: True),
    )
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **_: user if name == 'user32' else kernel, raising=False)
    return user, kernel, buffer


@pytest.mark.skipif(os.name != 'nt', reason='native Windows clipboard path')
@pytest.mark.parametrize('raw', [b'', png()[:35], b'not a PNG'])
def test_invalid_native_png_falls_back_to_lossless_bitmap(tmp_path, monkeypatch, raw):
    user, kernel, allocation = native_clipboard(monkeypatch, raw)
    fallback = Image.new('RGB', (80, 45), 'red')
    grab = Mock(return_value=fallback)
    monkeypatch.setattr(bridge_module.ImageGrab, 'grabclipboard', grab)
    result = bridge_module.clipboard_images()
    assert len(result) == 1
    with Image.open(io.BytesIO(result[0]['data'])) as loaded:
        assert loaded.format == 'PNG' and loaded.getpixel((0, 0)) == (255, 0, 0)
    grab.assert_called_once_with()
    assert len(user.CloseClipboard.calls) == 1
    assert len(kernel.GlobalUnlock.calls) == (1 if raw else 0)


@pytest.mark.skipif(os.name != 'nt', reason='native Windows clipboard path')
def test_valid_aligned_native_png_keeps_original_bytes_without_dib_read(monkeypatch):
    raw = png() + b'\x00' * 16
    user, kernel, allocation = native_clipboard(monkeypatch, raw)
    grab = Mock(side_effect=AssertionError('a valid native PNG must stay original'))
    monkeypatch.setattr(bridge_module.ImageGrab, 'grabclipboard', grab)
    assert bridge_module.clipboard_images()[0]['data'] == raw
    assert len(user.CloseClipboard.calls) == len(kernel.GlobalUnlock.calls) == 1
    grab.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='native Windows clipboard path')
def test_native_clipboard_limit_is_checked_before_reading_memory(monkeypatch):
    user, kernel, allocation = native_clipboard(monkeypatch, b'x', size=bridge_module.MAX_IMAGE + 1)
    grab = Mock(side_effect=AssertionError('oversize must not bypass the quota'))
    monkeypatch.setattr(bridge_module.ImageGrab, 'grabclipboard', grab)
    with pytest.raises(ValueError, match='32MB'):
        bridge_module.clipboard_images()
    assert not kernel.GlobalLock.calls and len(user.CloseClipboard.calls) == 1
    grab.assert_not_called()


@pytest.mark.parametrize('schema,phrase', [('9', '没有真实行情快照'), ('10', '版本 4～9')])
def test_h2_acceptance_accepts_schema_nine_but_rejects_unknown_without_source_writes(tmp_path, monkeypatch, schema, phrase):
    source = Store(tmp_path / 'source')
    source.execute("UPDATE meta SET value=? WHERE key='schema_version'", (schema,))
    before = source.path.read_bytes()
    destination = tmp_path / 'evidence'
    monkeypatch.setattr(acceptance_h2_replay, 'verify_frozen', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['acceptance_h2_replay.py', '--source-home', str(source.root),
        '--home', str(destination), '--start', '2026-01-01', '--end', '2026-01-02', '--codes', 'sh.600000'])
    with pytest.raises(ValueError, match=phrase):
        acceptance_h2_replay.main()
    assert source.path.read_bytes() == before
    assert not destination.exists()


def test_pa_helpers_keep_same_schema_source_updates_compatible():
    source = (Path(__file__).resolve().parents[1] / 'workbench' / 'store.py').read_text(encoding='utf-8')
    before = ast.parse(source)
    store_class = next(node for node in before.body if isinstance(node, ast.ClassDef) and node.name == 'Store')
    store_class.body = [node for node in store_class.body
                        if not isinstance(node, ast.FunctionDef) or node.name not in ('pa_write', 'pa_case_metadata', 'pa_trial_setup')]
    assert compatible_store_extension(ast.unparse(before), source)
