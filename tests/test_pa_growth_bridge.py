"""Exercise the desktop API with isolated data, without a live business DB."""
import base64
import io

from PIL import Image

from gui.growth_bridge import GrowthBridge
from workbench.store import Store


def png(color):
    output = io.BytesIO()
    Image.new('RGB', (800, 450), color).save(output, 'PNG')
    return output.getvalue()


def stage(bridge, data, name='test.png'):
    value = bridge.stage_images([dict(name=name, data_url='data:image/png;base64,' +
                                  base64.b64encode(data).decode())])
    assert value['ok'], value
    return value['pending'][0]['token']


def test_batch_persist_search_originals_notes_and_reopen(tmp_path):
    store = Store(tmp_path)
    bridge = GrowthBridge(store)
    assert bridge.bootstrap()['ok']
    red, blue = png('red'), png('blue')
    tokens = [stage(bridge, red), stage(bridge, blue)]
    result = bridge.save_capture(tokens, dict(memory='ETHUSD microDB 锦和商业', data_kind='test'))
    assert result['ok'] and len(result['cases']) == 2
    reopened = GrowthBridge(Store(tmp_path))
    cases = reopened.search_cases('microd')['cases']
    assert len(cases) == 2 and all(row['thumbnail'].startswith('data:image/jpeg') for row in cases)
    detail = reopened.get_case(cases[0]['id'])['case']
    assert base64.b64decode(detail['images'][0]['data_url'].split(',', 1)[1]) in (red, blue)
    assert reopened.add_note(detail['id'], 'ETH 反思')['ok']
    assert reopened.bootstrap()['recent_case_id'] == detail['id']
    assert reopened.growth()['stats']['notes'] == 1


def test_duplicate_requires_explicit_confirmation_and_preserves_pending(tmp_path):
    bridge = GrowthBridge(Store(tmp_path))
    data = png('green')
    first = bridge.save_capture([stage(bridge, data)], {})
    token = stage(bridge, data)
    refused = bridge.save_capture([token], {})
    assert refused['duplicates'] and not refused['cases']
    assert token in bridge._pending
    allowed = bridge.save_capture([token], {}, {'allow_duplicates': True})
    assert allowed['ok'] and allowed['cases'][0]['id'] != first['cases'][0]['id']


def test_failed_commit_retains_pending_and_no_searchable_case(tmp_path, monkeypatch):
    store = Store(tmp_path)
    bridge = GrowthBridge(store)
    token = stage(bridge, png('black'))
    monkeypatch.setattr(store, 'pa_save_case', lambda *a, **k: (_ for _ in ()).throw(OSError('磁盘写入失败')))
    result = bridge.save_capture([token])
    assert result == {'ok': False, 'error': '磁盘写入失败'}
    assert token in bridge._pending and store.pa_search() == []


def test_invalid_payload_close_and_private_scope_fail_explicitly(tmp_path):
    bridge = GrowthBridge(Store(tmp_path))
    assert not bridge.stage_images([dict(data_url='file:///private.png')])['ok']
    assert not bridge.search_cases('', {'owner': 'another'})['ok']
    assert not bridge.get_case('not-owned')['ok']
    assert not bridge.add_topic_note('nonexistent', 'text')['ok']
    bridge._closing = True
    assert not bridge.save_capture([])['ok']


def test_second_batch_write_failure_rolls_back_all_cases_and_keeps_pending(tmp_path, monkeypatch):
    store = Store(tmp_path)
    bridge = GrowthBridge(store)
    tokens = [stage(bridge, png('red')), stage(bridge, png('blue'))]
    save = store.pa_save_case
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            # Prove the first image was registered inside the outer transaction
            # before the next disk failure asks that transaction to roll back.
            assert len(store.pa_search()) == 1
            raise OSError('第二张原图保存失败')
        return save(*args, **kwargs)
    monkeypatch.setattr(store, 'pa_save_case', fail_second)
    result = bridge.save_capture(tokens, dict(data_kind='test'))
    assert result == {'ok': False, 'error': '第二张原图保存失败'}
    assert all(token in bridge._pending for token in tokens)
    reopened = Store(store.root)
    assert reopened.pa_search() == []
    assert reopened.pa_growth()['stats']['images'] == 0
    assert reopened.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == 0
    assert reopened.rows('SELECT COUNT(*) AS n FROM plans')[0]['n'] == 0
