"""PA review regressions; only synthetic images and disposable Store homes."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import threading
import zipfile

from PIL import Image
import pytest

from workbench import pa_growth as pa
from workbench.store import Store


def picture(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (120, 80), color).save(output, 'PNG')
    return dict(name='explicit-test.png', data=output.getvalue())


def saved(store, color='red', **kwargs):
    return store.pa_save_case([picture(color)], dict(data_kind='test'), **kwargs)['case']


def originals(store):
    return list((store.root / 'pa_growth').glob('originals/*/*'))


def test_metadata_catalogue_is_private_and_does_not_join_images(tmp_path, monkeypatch):
    store = Store(tmp_path)
    case = saved(store)
    owner = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")[0]['value']
    store.execute("UPDATE pa_cases SET title='my title',topic_ids='[\"cont-h2\"]' WHERE id=?", (case['id'],))
    store.execute('INSERT INTO pa_cases SELECT ?,?,status,title,memory,keywords,tags,topic_ids,symbol,timeframe,market,data_kind,created,updated,viewed '
                  'FROM pa_cases WHERE id=?', ('foreign-case', owner + '-other', case['id']))
    queries = []
    rows = store.rows
    def capture(sql, args=()):
        queries.append(sql)
        return rows(sql, args)
    monkeypatch.setattr(store, 'rows', capture)
    catalogue = pa.case_metadata(store)
    assert catalogue == [dict(id=case['id'], title='my title', memory='', symbol='', topic_ids=['cont-h2'], data_kind='test')]
    assert not any('pa_assets' in sql or 'pa_case_images' in sql for sql in queries)


def test_pa_outer_failure_cleans_only_new_unreferenced_originals(tmp_path, monkeypatch):
    store = Store(tmp_path)
    kept = saved(store, 'green')
    kept_bytes = store.pa_image(kept['images'][0]['id'])
    write = pa._write_original
    calls = []
    def second_fails(*args):
        result = write(*args)
        calls.append(result)
        if len(calls) == 2:
            raise OSError('second image disk receipt failed')
        return result
    monkeypatch.setattr(pa, '_write_original', second_fails)
    with pytest.raises(OSError):
        with pa.write_scope(store):
            saved(store, 'blue')
            saved(store, 'yellow')
    assert [row['id'] for row in store.pa_search()] == [kept['id']]
    assert len(originals(store)) == 1
    assert store.pa_image(kept['images'][0]['id']) == kept_bytes


@pytest.mark.parametrize('after_commit', [False, True])
def test_commit_failure_rechecks_actual_references_before_cleanup(tmp_path, monkeypatch, after_commit):
    store = Store(tmp_path)
    pa._owner(store)
    connect = store.connect
    failed = False
    @contextlib.contextmanager
    def interrupted_commit():
        nonlocal failed
        outer = getattr(store._atomic_local, 'connection', None) is None
        pa_scope = getattr(store._atomic_local, 'pa_created', None) is not None
        should_fail = outer and pa_scope and not failed
        with connect() as db:
            yield db
            if should_fail and not after_commit:
                failed = True
                raise OSError('commit rejected before durable write')
        if should_fail and after_commit:
            failed = True
            raise OSError('commit receipt failed after durable write')
    monkeypatch.setattr(store, 'connect', interrupted_commit)
    with pytest.raises(OSError):
        saved(store)
    loaded = Store(tmp_path)
    assert len(loaded.pa_search()) == int(after_commit)
    assert len(originals(loaded)) == int(after_commit)
    if after_commit:
        asset = loaded.pa_search()[0]['images'][0]
        assert loaded.pa_image(asset['id']) == picture()['data']


def test_postcommit_case_read_failure_never_removes_committed_original(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(pa, 'read_case', lambda *a, **k: (_ for _ in ()).throw(OSError('read receipt failed')))
    with pytest.raises(OSError):
        saved(store)
    rows = store.rows('SELECT id FROM pa_assets')
    assert len(rows) == 1 and len(originals(store)) == 1
    assert store.pa_image(rows[0]['id']) == picture()['data']


def test_reference_read_failure_retains_uncertain_file(tmp_path, monkeypatch):
    store = Store(tmp_path)
    connect, write = store.connect, pa._write_original
    failed = False
    @contextlib.contextmanager
    def unavailable_reference():
        if failed and getattr(store._atomic_local, 'connection', None) is None:
            raise OSError('cannot verify disk/database reference')
        with connect() as db:
            yield db
    def failed_write(*args):
        nonlocal failed
        write(*args)
        failed = True
        raise OSError('file acknowledgement failed')
    monkeypatch.setattr(store, 'connect', unavailable_reference)
    monkeypatch.setattr(pa, '_write_original', failed_write)
    with pytest.raises(OSError, match='acknowledgement'):
        saved(store)
    assert len(originals(store)) == 1
    assert Store(tmp_path).pa_search() == []


def test_generic_outer_transaction_is_not_mistaken_for_owned_commit(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(OSError):
        with store.atomic_write():
            saved(store)
            raise OSError('generic transaction caller owns rollback')
    assert not store.pa_search()
    assert len(originals(store)) == 1  # Retain when cleanup does not own the final boundary.


def test_concurrent_append_capacity_is_serialized_and_rolls_back_loser(tmp_path, monkeypatch):
    first = Store(tmp_path)
    case = saved(first)
    second = Store(tmp_path)
    monkeypatch.setattr(pa, 'MAX_CASE_IMAGES', 2)
    gate = threading.Barrier(2)
    write = pa._write_original
    def concurrent_write(*args):
        result = write(*args)
        gate.wait(timeout=5)
        return result
    monkeypatch.setattr(pa, '_write_original', concurrent_write)
    def append(store, color):
        try:
            saved(store, color, case_id=case['id'])
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda value: append(*value), [(first, 'blue'), (second, 'green')]))
    assert sorted(outcomes) == [False, True]
    assert len(first.pa_case(case['id'])['images']) == 2
    assert len(originals(first)) == 2


def test_cumulative_byte_cap_preserves_existing_case(tmp_path, monkeypatch):
    store = Store(tmp_path)
    case = saved(store)
    maximum = len(picture()['data']) + len(picture('blue')['data'])
    monkeypatch.setattr(pa, 'MAX_CASE_BYTES', maximum)
    saved(store, 'blue', case_id=case['id'])
    with pytest.raises(ValueError, match='128 MB'):
        saved(store, 'green', case_id=case['id'])
    assert len(store.pa_case(case['id'])['images']) == 2
    assert len(originals(store)) == 2


def test_restore_streams_payloads_and_retains_paths_not_all_raw_images(tmp_path, monkeypatch):
    source = Store(tmp_path / 'source')
    case = saved(source)
    saved(source, 'blue', case_id=case['id'])
    saved(source, 'green', case_id=case['id'])
    archive = tmp_path / 'source.zip'
    source.pa_backup(archive)
    read = zipfile.ZipExtFile.read
    raw_reads = []
    def bounded_read(handle, n=-1):
        if handle.name.startswith('originals/'):
            assert 0 < n <= 1024 * 1024, 'originals must stream, never archive.read whole payload'
            raw_reads.append(n)
        return read(handle, n)
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', bounded_read)
    target = Store(tmp_path / 'target')
    read_backup = pa._read_backup
    def checked_staging(path, staging):
        manifest, assets = read_backup(path, staging)
        assert len(assets) == 3
        assert all(isinstance(entry[0], Path) and entry[0].is_file() for entry in assets.values())
        return manifest, assets
    monkeypatch.setattr(pa, '_read_backup', checked_staging)
    assert target.pa_restore(archive)['images'] == 3
    assert raw_reads and not list(target.root.glob('.pa-restore-*'))
    restored = target.pa_case(target.pa_search()[0]['id'])
    assert [target.pa_image(image['id']) for image in restored['images']] == [picture(color)['data'] for color in ('red', 'blue', 'green')]


def test_legacy_over_capacity_restore_is_complete_but_further_additions_rejected(tmp_path, monkeypatch):
    source = Store(tmp_path / 'source')
    case = saved(source)
    saved(source, 'blue', case_id=case['id'])
    archive = tmp_path / 'source.zip'
    source.pa_backup(archive)
    target = Store(tmp_path / 'target')
    monkeypatch.setattr(pa, 'MAX_CASE_IMAGES', 1)
    assert target.pa_restore(archive) == dict(cases=1, images=2, notes=0, trials=0)
    restored = target.pa_case(target.pa_search()[0]['id'])
    assert [target.pa_image(image['id']) for image in restored['images']] == [picture(color)['data'] for color in ('red', 'blue')]
    with pytest.raises(ValueError, match='100 张'):
        saved(target, 'green', case_id=restored['id'])
    assert len(target.pa_case(restored['id'])['images']) == 2
    assert len(originals(target)) == 2
    assert not list(target.root.glob('.pa-restore-*'))


def test_archive_total_limit_rejects_before_original_or_case_writes(tmp_path, monkeypatch):
    source = Store(tmp_path / 'source')
    saved(source)
    archive = tmp_path / 'source.zip'
    source.pa_backup(archive)
    target = Store(tmp_path / 'target')
    monkeypatch.setattr(pa, 'MAX_BACKUP_BYTES', 1)
    with pytest.raises(ValueError, match='备份解压大小超过限制'):
        target.pa_restore(archive)
    assert not target.pa_search() and not (target.root / 'pa_growth').exists()
    assert not list(target.root.glob('.pa-restore-*'))
