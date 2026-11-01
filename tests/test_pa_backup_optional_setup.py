import io
import json
import zipfile

from PIL import Image
import pytest

from gui.growth_bridge import GrowthBridge
from workbench.store import Store


def sample(store, title):
    raw = io.BytesIO()
    Image.new('RGB', (80, 45), '#516c87').save(raw, format='PNG')
    case = store.pa_save_case([dict(name='synthetic.png', data=raw.getvalue())],
                              dict(title=title, data_kind='test'), allow_duplicate=True)['case']
    return case, raw.getvalue()


def profile_key(store, case_id, issue=False):
    owner = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")[0]['value']
    return f"pa_trial_setup{'_issue' if issue else ''}:{owner}:{case_id}"


def manifest(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read('manifest.json'))


@pytest.mark.parametrize('kind', ['broken_json', 'invalid_cutoff', 'foreign_image'])
def test_bad_setup_roundtrip_keeps_all_originals_research_and_valid_judgment(tmp_path, kind):
    source = Store(tmp_path / 'source')
    bad, raw = sample(source, 'needs-reset')
    good, _ = sample(source, 'valid')
    good_cfg = dict(mode='mask', image_ids=[good['images'][0]['id']], cutoff=.45)
    source.pa_trial_setup(good['id'], good_cfg)
    source.pa_add_note(bad['id'], 'research remains intact')
    source.pa_add_trial(bad['id'], 'wait', details=dict(thought='original judgment'))
    source.pa_add_trial(good['id'], 'pass', details=dict(source='progressive-screenshot', presentation=good_cfg))
    value = '{broken' if kind == 'broken_json' else json.dumps(dict(
        mode='mask', image_ids=[bad['images'][0]['id']] if kind != 'foreign_image' else ['f' * 32], cutoff=0 if kind == 'invalid_cutoff' else .5))
    source.execute('INSERT INTO meta(key,value) VALUES(?,?)', (profile_key(source, bad['id']), value))
    assert GrowthBridge(source).get_case(bad['id'])['ok']
    archive = tmp_path / 'first.zip'
    result = source.pa_backup(archive)
    assert len(result['warnings']) == 1
    entries = {c['title']: c for c in manifest(archive)['cases']}
    assert entries['needs-reset']['trial_setup'] is None
    assert entries['needs-reset']['trial_setup_issues'][0]['raw'] == value
    assert entries['valid']['trial_setup'] == good_cfg
    # Export does not repair, delete or rewrite the source profile.
    assert source.rows('SELECT value FROM meta WHERE key=?', (profile_key(source, bad['id']),))[0]['value'] == value
    target = Store(tmp_path / 'target')
    restored = target.pa_restore(archive)
    assert restored['cases'] == 2 and restored['notes'] == 1 and restored['trials'] == 2
    assert len(restored['warnings']) == 1
    rows = {c['title']: target.pa_case(c['id']) for c in target.pa_search()}
    for c in rows.values():
        assert target.pa_image(c['images'][0]['id']) == raw
    damaged = rows['needs-reset']
    assert damaged['notes'][0]['text'] == 'research remains intact'
    assert damaged['trials'][0]['details'] == dict(thought='original judgment')
    shown = GrowthBridge(target).get_case(damaged['id'])
    assert shown['ok'] and shown['case']['trial_setup_error']
    valid = rows['valid']
    assert target.pa_trial_setup(valid['id'])['image_ids'] == [valid['images'][0]['id']]
    assert valid['trials'][0]['details']['presentation'] == target.pa_trial_setup(valid['id'])
    # Re-export/re-restore retains the raw diagnostic, never reactivates it.
    target.pa_backup(tmp_path / 'second.zip')
    again = Store(tmp_path / 'again')
    assert again.pa_restore(tmp_path / 'second.zip')['cases'] == 2
    issues = [c['trial_setup_issues'] for c in manifest(tmp_path / 'second.zip')['cases'] if c.get('trial_setup_issues')]
    assert issues[0][0]['raw'] == value
    # Explicitly replacing the profile makes practice usable again.
    target.pa_trial_setup(damaged['id'], dict(mode='mask', cutoff=.5, image_ids=[damaged['images'][0]['id']]))
    assert 'trial_setup_error' not in GrowthBridge(target).get_case(damaged['id'])['case']
    target.pa_backup(tmp_path / 'after-reset.zip')
    assert any(c.get('trial_setup_issues') for c in manifest(tmp_path / 'after-reset.zip')['cases'])


def test_valid_settings_are_normalized_and_no_warnings_for_old_backups(tmp_path):
    source = Store(tmp_path / 'source')
    c, _ = sample(source, 'valid')
    value = dict(mode='mask', cutoff=.5, image_ids=[c['images'][0]['id']], ignored='extra')
    source.execute('INSERT INTO meta(key,value) VALUES(?,?)', (profile_key(source, c['id']), json.dumps(value)))
    result = source.pa_backup(tmp_path / 'valid.zip')
    assert 'warnings' not in result
    assert 'ignored' not in manifest(tmp_path / 'valid.zip')['cases'][0]['trial_setup']


@pytest.mark.parametrize('limit', ['MAX_BACKUP_MANIFEST_BYTES', 'MAX_BACKUP_BYTES', 'MAX_BACKUP_RECORDS'])
def test_export_never_claims_success_beyond_existing_restore_limits(tmp_path, monkeypatch, limit):
    from workbench import pa_growth
    source = Store(tmp_path / 'source')
    c, raw = sample(source, 'test')
    source.execute('INSERT INTO meta(key,value) VALUES(?,?)', (profile_key(source, c['id']), '{broken'))
    monkeypatch.setattr(pa_growth, limit, 0)
    with pytest.raises(ValueError, match='未生成备份'):
        source.pa_backup(tmp_path / 'too-large.zip')
    assert not (tmp_path / 'too-large.zip').exists()
    assert source.pa_image(c['images'][0]['id']) == raw


@pytest.mark.parametrize('field,value', [('trial_setup', {'mode': 'mask', 'image_ids': [], 'cutoff': 0}),
                                        ('trial_setup_issues', [{'raw': {'mode': 'mask'}, 'reason': 'invalid'}])])
def test_tampered_archives_still_rejected_before_writes(tmp_path, field, value):
    source = Store(tmp_path / 'source')
    sample(source, 'test')
    source.pa_backup(tmp_path / 'source.zip')
    with zipfile.ZipFile(tmp_path / 'source.zip') as archive:
        items = {n: archive.read(n) for n in archive.namelist()}
    data = json.loads(items['manifest.json'])
    data['cases'][0][field] = value
    items['manifest.json'] = json.dumps(data).encode()
    with zipfile.ZipFile(tmp_path / 'tampered.zip', 'w') as archive:
        for name, content in items.items():
            archive.writestr(name, content)
    target = Store(tmp_path / 'target')
    with pytest.raises(ValueError):
        target.pa_restore(tmp_path / 'tampered.zip')
    assert not target.pa_search()
    assert not list((target.root / 'pa_growth').rglob('*.png'))
