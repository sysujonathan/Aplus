"""Synthetic PA screenshots and isolated stores; never touches a user's data."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sqlite3
import zipfile

from PIL import Image
import pytest

from workbench.store import Store, digest
from workbench import pa_growth as pa


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'isolated-pa-runtime')


def picture(color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (640, 360), color).save(buffer, 'PNG')
    return {'data': buffer.getvalue(), 'name': 'TradingView screenshot.png'}


def save(store, **metadata):
    return store.pa_save_case([picture()], metadata)['case']


def legacy_store(tmp_path):
    """Build an old Store schema using the current fixture, never real data."""
    store = Store(tmp_path / 'legacy-isolated')
    with store.connect() as db:
        for table in ('pa_case_images', 'pa_assets', 'pa_cases', 'pa_notes', 'pa_trials', 'pa_topic_notes'):
            db.execute('DROP TABLE ' + table)
        db.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
        db.execute("INSERT INTO plans VALUES('p','o','ETHUSD','unchanged','daily','2026-01-01','观察',1,0.5,2,10,'keep me','2026-01-01')")
        db.execute("INSERT INTO meta VALUES('chart_workspace:kept','original drawings')")
    return store


def test_v8_migration_takes_recoverable_backup_before_additive_schema(tmp_path):
    old = legacy_store(tmp_path)
    reopened = Store(old.root)
    assert reopened.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'] == '9'
    assert reopened.rows('SELECT notes FROM plans')[0]['notes'] == 'keep me'
    assert reopened.rows("SELECT value FROM meta WHERE key='chart_workspace:kept'")[0]['value'] == 'original drawings'
    copies = list((old.root / 'backups').glob('pre-pa-v8-*.sqlite3'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '8'
        assert db.execute('SELECT notes FROM plans').fetchone()[0] == 'keep me'
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='pa_cases'").fetchone()
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    Store(old.root)
    assert len(list((old.root / 'backups').glob('pre-pa-v8-*'))) == 1


def test_backup_failure_and_future_version_refuse_writes(tmp_path, monkeypatch):
    old = legacy_store(tmp_path)
    original = old.path.read_bytes()
    monkeypatch.setattr('workbench.store.os.fsync', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        Store(old.root)
    assert old.path.read_bytes() == original
    monkeypatch.undo()
    with old.connect() as db:
        db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    future = old.path.read_bytes()
    with pytest.raises(ValueError, match='版本'):
        Store(old.root)
    assert old.path.read_bytes() == future


def test_original_bytes_private_case_and_notes_survive_restart(store):
    image = picture()
    case = store.pa_save_case([image], {'symbol': 'ETHUSD', 'memory': 'microDB 锦和商业', 'tags': ['H2']})['case']
    assert case['status'] == 'private_draft'
    assert case['title'] == ''
    note = store.pa_add_note(case['id'], '等待二次尝试，不根据结果改写事前判断')
    store.pa_case(case['id'])
    reopened = Store(store.root)
    loaded = reopened.pa_case(case['id'], touch=False)
    assert loaded['memory'] == 'microDB 锦和商业'
    assert loaded['notes'] == [note]
    assert note['author'] == '我' and note['created']
    assert reopened.pa_image(loaded['images'][0]['id']) == image['data']
    assert loaded['images'][0]['sha256'] == digest(image['data'])
    assert reopened.pa_growth()['recent'][0]['id'] == case['id']
    assert reopened.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == 0
    assert reopened.rows('SELECT COUNT(*) AS n FROM plans')[0]['n'] == 0


def test_substring_search_and_deliberate_match_reasons(store):
    case = save(store, symbol='ETHUSD', memory='锦和商业', keywords=['microDB'], title='双底研究',
                market='Crypto', timeframe='4H', tags=['H2'], topic_ids=['cont-h2'])
    for query, reason in [('ethus', '品种'), ('锦和', '记忆描述'), ('MICROdb', '个人关键词'),
                          ('双底', '标题'), ('crypt', '市场'), ('4h', '周期'), ('h2', '已确认 PA 标签')]:
        found = store.pa_search(query)
        assert len(found) == 1
        assert reason in found[0]['match_reasons']
        assert found[0]['asset_id'] == case['images'][0]['id']
    store.pa_add_note(case['id'], 'lower-high 需要更多证据')
    assert store.pa_search('LOWER-H')[0]['match_reasons'] == ['研究笔记']
    assert store.pa_search('missing') == []
    assert store.pa_search(filters={'topic_id': 'cont-h2'})[0]['id'] == case['id']
    assert store.pa_search(filters={'market': 'crypto', 'tag': 'h2'})
    assert store.pa_search(filters={'timeframe': 'daily'}) == []


def test_duplicate_warning_never_merges_and_attach_is_explicit(store):
    first = save(store, symbol='ETHUSD')
    warning = store.pa_save_case([picture()], {'symbol': 'ETHUSD'})
    assert not warning['saved'] and warning['duplicates'][0]['id'] == first['id']
    assert len(store.pa_search()) == 1
    assert store.pa_find_duplicates(picture()['data'])[0]['id'] == first['id']
    second = store.pa_save_case([picture()], {'symbol': 'ETHUSD'}, allow_duplicate=True)['case']
    assert second['id'] != first['id']
    assert len(store.pa_search()) == 2
    assert store.pa_image(second['images'][0]['id']) == picture()['data']
    attached = store.pa_save_case([picture('blue')], case_id=first['id'])['case']
    assert len(attached['images']) == 2
    assert len(store.pa_case(second['id'])['images']) == 1
    batch = store.pa_save_case([picture('green'), picture('green')])
    assert batch['repeated_batch'] and not batch['saved']


def test_local_owner_scopes_every_read_and_mutation(store):
    mine = save(store)
    original = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")[0]['value']
    store.execute("UPDATE meta SET value='another-local-profile' WHERE key='pa_local_owner'")
    assert store.pa_search() == []
    assert store.pa_find_duplicates(picture()['data']) == []
    assert store.pa_growth()['stats'] == {'cases': 0, 'images': 0, 'notes': 0, 'trials': 0, 'topics': 0}
    for operation in (lambda: store.pa_case(mine['id']), lambda: store.pa_image(mine['images'][0]['id']),
                      lambda: store.pa_update_case(mine['id'], {'memory': 'overwrite'}),
                      lambda: store.pa_add_note(mine['id'], 'foreign'),
                      lambda: store.pa_add_trial(mine['id'], 'foreign'),
                      lambda: store.pa_save_case([picture('green')], case_id=mine['id'])):
        with pytest.raises(ValueError, match='无权访问'):
            operation()
    with pytest.raises(ValueError, match='不支持'):
        store.pa_save_case([picture()], {'owner': original})
    with pytest.raises(ValueError, match='未知筛选'):
        store.pa_search(filters={'owner': original})


def test_image_corruption_and_path_tampering_are_not_silently_accepted(store, tmp_path):
    case = save(store)
    asset_id = case['images'][0]['id']
    row = store.rows('SELECT path FROM pa_assets WHERE id=?', (asset_id,))[0]
    path = store.root / row['path']
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='校验失败'):
        store.pa_image(asset_id)
    secret = tmp_path / 'unrelated.txt'
    secret.write_text('not a PA asset', encoding='utf-8')
    store.execute('UPDATE pa_assets SET path=? WHERE id=?', (str(secret), asset_id))
    with pytest.raises(ValueError, match='路径无效'):
        store.pa_image(asset_id)
    for data in (b'not an image', b''):
        with pytest.raises(ValueError):
            store.pa_save_case([{'data': data, 'name': 'fake.png'}])


def test_failed_file_write_and_failed_commit_do_not_publish_cases(store, monkeypatch):
    def full(*_):
        raise OSError('disk full')
    monkeypatch.setattr(pa.os, 'fsync', full)
    with pytest.raises(OSError, match='disk full'):
        save(store)
    assert store.pa_search() == []
    monkeypatch.undo()
    original_connect = store.connect
    @contextmanager
    def failed_commit():
        with original_connect() as db:
            yield db
            if db.in_transaction and db.execute('SELECT COUNT(*) FROM pa_cases').fetchone()[0]:
                raise OSError('commit rejected')
    monkeypatch.setattr(store, 'connect', failed_commit)
    with pytest.raises(OSError, match='commit rejected'):
        save(store)
    monkeypatch.undo()
    assert store.pa_search() == []


def test_batch_store_transaction_rolls_back_all_case_records(store):
    with pytest.raises(OSError, match='batch failure'):
        with store.atomic_write():
            store.pa_save_case([picture()])
            store.pa_save_case([picture('blue')])
            raise OSError('batch failure')
    assert store.pa_search() == []
    with store.atomic_write():
        first = store.pa_save_case([picture()])['case']
        store.pa_save_case([picture('blue')], case_id=first['id'])
    assert len(store.pa_case(first['id'])['images']) == 2


def test_growth_records_only_actual_research_no_scores_or_mastery(store):
    assert store.pa_growth()['first_use']
    case = save(store, topic_ids=['cont-h2'], data_kind='test')
    store.pa_add_note(case['id'], '第二次尝试的结构仍需验证')
    store.pa_add_trial(case['id'], '等待', 'quick', {'phase': 'before', 'price_marks': []})
    store.pa_topic_note('cont-h2', '此前只看信号 K，现在先写明背景')
    result = Store(store.root).pa_growth()
    assert not result['first_use']
    assert result['stats'] == {'cases': 1, 'images': 1, 'notes': 1, 'trials': 1, 'topics': 1}
    assert result['trials'][0]['data_kind'] == 'test'
    assert result['team_available'] is False
    assert not any(key in str(result) for key in ('win_rate', 'mastery', 'profit'))
    with pytest.raises(ValueError, match='不生成自动成绩'):
        store.pa_add_trial(case['id'], '买入', details={'correct': True})
    with pytest.raises(ValueError, match='未知知识主题'):
        store.pa_topic_note('made-up', 'not valid')


def test_backup_roundtrip_keeps_originals_and_records_without_replacing(tmp_path, store):
    case = save(store, symbol='ETHUSD', keywords=['microDB'], topic_ids=['cont-h2'])
    store.pa_save_case([picture('blue')], case_id=case['id'])
    store.pa_add_note(case['id'], '原始研究记录')
    store.pa_add_trial(case['id'], '等待证据', 'structure', {'phase': 'before'})
    store.pa_topic_note('cont-h2', '第二次尝试')
    path = tmp_path / 'case-backup.zip'
    assert store.pa_backup(path)['images'] == 2
    with pytest.raises(ValueError, match='已存在'):
        store.pa_backup(path)
    target = Store(tmp_path / 'restore-isolated')
    existing = target.pa_save_case([picture('yellow')], {'title': 'untouched'})['case']
    restored = target.pa_restore(path)
    assert restored == {'cases': 1, 'images': 2, 'notes': 1, 'trials': 1}
    loaded = target.pa_case(target.pa_search('microDB')[0]['id'])
    assert loaded['id'] != case['id']
    assert target.pa_image(loaded['images'][0]['id']) == picture()['data']
    assert target.pa_image(loaded['images'][1]['id']) == picture('blue')['data']
    assert loaded['notes'][0]['text'] == '原始研究记录'
    assert target.pa_case(existing['id'])['title'] == 'untouched'
    assert target.pa_growth()['stats']['topics'] == 1


@pytest.mark.parametrize('damage', ['hash', 'traversal', 'missing', 'foreign-link', 'extra', 'future'])
def test_corrupt_backups_rejected_before_any_cases_or_originals_written(tmp_path, store, damage):
    save(store)
    source = tmp_path / 'valid.zip'
    store.pa_backup(source)
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files['manifest.json'])
    if damage == 'hash':
        manifest['assets'][0]['sha256'] = '0' * 64
    elif damage == 'traversal':
        files['../escape.png'] = b'evil'
    elif damage == 'missing':
        files.pop(next(name for name in files if name.startswith('originals/')))
    elif damage == 'foreign-link':
        manifest['links'][0]['case_id'] = 'not-in-backup'
    elif damage == 'extra':
        files['unexpected.txt'] = b'unlisted'
    else:
        manifest['version'] = 999
    files['manifest.json'] = json.dumps(manifest).encode()
    broken = tmp_path / 'broken.zip'
    with zipfile.ZipFile(broken, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    target = Store(tmp_path / 'untouched-isolated')
    with pytest.raises(ValueError):
        target.pa_restore(broken)
    assert target.pa_search() == []
    assert not (target.root / 'pa_growth').exists()
