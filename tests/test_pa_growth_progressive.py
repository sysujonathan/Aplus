import io

from PIL import Image
import pytest

from gui.growth_bridge import GrowthBridge
from workbench.store import Store


def case(store, count=3):
    data = io.BytesIO()
    Image.new('RGB', (80, 45), '#c54e81').save(data, format='PNG')
    return store.pa_save_case([{'name': f'test-{i}.png', 'data': data.getvalue()} for i in range(count)],
                              {'data_kind': 'test'}, allow_duplicate=True)['case']


@pytest.mark.parametrize('mode', ['mask', 'sequence'])
def test_settings_survive_new_store_and_backup_with_remapped_images(tmp_path, mode):
    store = Store(tmp_path / 'source')
    c = case(store)
    ids = [i['id'] for i in c['images']]
    setting = dict(mode=mode, image_ids=ids if mode == 'sequence' else ids[:1])
    if mode == 'mask':
        setting['cutoff'] = .37
    assert store.pa_trial_setup(c['id']) is None
    assert store.pa_trial_setup(c['id'], setting) == setting
    assert Store(store.root).pa_trial_setup(c['id']) == setting
    store.pa_add_trial(c['id'], 'wait', details=dict(source='progressive-screenshot', presentation=setting, image_id=ids[0]))
    before = [store.pa_image(i) for i in ids]
    store.pa_backup(tmp_path / 'cases.zip')
    target = Store(tmp_path / 'target')
    target.pa_restore(tmp_path / 'cases.zip')
    restored = target.pa_search()[0]
    new = target.pa_case(restored['id'])
    cfg = target.pa_trial_setup(new['id'])
    assert cfg['mode'] == mode
    assert cfg['image_ids'] == [i['id'] for i in new['images']][:len(setting['image_ids'])]
    assert not set(cfg['image_ids']) & set(ids)
    assert new['trials'][0]['details']['presentation'] == cfg
    assert new['trials'][0]['details']['image_id'] == new['images'][0]['id']
    assert [target.pa_image(i['id']) for i in new['images']] == before
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'] == '9'


@pytest.mark.parametrize('invalid', [
    {'mode': 'mask', 'cutoff': float('nan')},
    {'mode': 'mask', 'cutoff': True},
    {'mode': 'mask', 'cutoff': 1},
    {'mode': 'mask', 'cutoff': 0},
    {'mode': 'sequence'},
])
def test_invalid_setup_rejected_without_overwriting_valid_setting(tmp_path, invalid):
    store = Store(tmp_path)
    c = case(store)
    ids = [c['images'][0]['id']]
    valid = dict(mode='mask', cutoff=.5, image_ids=ids)
    store.pa_trial_setup(c['id'], valid)
    with pytest.raises(ValueError):
        store.pa_trial_setup(c['id'], dict(invalid, image_ids=ids))
    assert store.pa_trial_setup(c['id']) == valid


def test_setup_rejects_foreign_case_image_and_private_owner(tmp_path):
    store = Store(tmp_path)
    a, b = case(store), case(store)
    bridge = GrowthBridge(store)
    assert not bridge.set_trial_setup(a['id'], dict(mode='mask', cutoff=.5, image_ids=[b['images'][0]['id']]))['ok']
    store.execute("UPDATE pa_cases SET owner='another-member' WHERE id=?", (b['id'],))
    assert not bridge.set_trial_setup(b['id'], dict(mode='mask', cutoff=.5, image_ids=[b['images'][0]['id']]))['ok']
    atlas = bridge.bootstrap()['atlas_cases']
    assert {c['id'] for c in atlas} == {a['id']}


def test_progressive_judgment_records_exact_setup_and_rejects_stale_configuration(tmp_path):
    store = Store(tmp_path)
    c = case(store)
    cfg = dict(mode='mask', cutoff=.45, image_ids=[c['images'][0]['id']])
    bridge = GrowthBridge(store)
    value = dict(judgment='wait', details=dict(source='progressive-screenshot', presentation=cfg))
    assert not bridge.save_trial(c['id'], value)['ok']
    assert bridge.set_trial_setup(c['id'], cfg)['ok']
    assert bridge.save_trial(c['id'], value)['trial']['details']['presentation'] == cfg
    store.pa_trial_setup(c['id'], dict(cfg, cutoff=.6))
    assert not bridge.save_trial(c['id'], value)['ok']
    assert len(store.pa_case(c['id'])['trials']) == 1


def test_broken_optional_setup_does_not_block_original_case(tmp_path):
    store = Store(tmp_path)
    c = case(store)
    owner = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")[0]['value']
    store.execute('INSERT INTO meta(key,value) VALUES(?,?)', (f"pa_trial_setup:{owner}:{c['id']}", '{broken'))
    result = GrowthBridge(store).get_case(c['id'])
    assert result['ok'] and result['case']['images']
    assert result['case']['trial_setup'] is None and result['case']['trial_setup_error']
    edited = GrowthBridge(store).update_case(c['id'], dict(memory='仍能编辑原案例'))
    assert edited['ok'] and edited['case']['memory'] == '仍能编辑原案例'
