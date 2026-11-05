"""Protocol integration with isolated stores; not a live Google account test."""
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import threading
import uuid

from PIL import Image
import pytest

from workbench.store import Store
from workbench.pa_shared import FOLDER, DriveError, Workspace, GoogleDrive, validate_manifest
from workbench.pa_auth import protect
from gui.growth_bridge import GrowthBridge


class Cloud:
    def __init__(self):
        self.files = {}
        self.lock = threading.Lock()
        self.offline = False
        self.interrupt = None
        self.writes = 0


class Drive:
    def __init__(self, cloud, user, folder=FOLDER):
        self.cloud, self.user = cloud, user
        self.folder = folder

    def check(self):
        if self.cloud.offline:
            raise ConnectionError('断网，未确认同步')

    def identity(self, online=False):
        if online:
            self.check()
        return dict(id=self.user, name=self.user)

    def check_folder(self):
        self.check()
        return True

    def allocate(self, count):
        return [uuid.uuid4().hex for _ in range(count)]

    def info(self, file_id):
        self.check()
        with self.cloud.lock:
            if file_id not in self.cloud.files:
                raise DriveError(404)
            value = self.cloud.files[file_id]
            return dict(id=file_id, parents=[self.folder], owners=[dict(permissionId=value['owner'])])

    def read(self, file_id, limit):
        self.info(file_id)
        data = self.cloud.files[file_id]['data']
        assert len(data) <= limit
        return data

    def list_cases(self):
        return self.list_records('case-v1')

    def list_records(self, kind):
        self.check()
        with self.cloud.lock:
            return [dict(id=k, parents=[self.folder], owners=[dict(permissionId=v['owner'])])
                    for k, v in self.cloud.files.items() if v['kind'] == kind]

    def put_once(self, file_id, name, data, mime, kind):
        self.check()
        with self.cloud.lock:
            if file_id in self.cloud.files:
                if self.cloud.files[file_id]['data'] != data:
                    raise ValueError('冲突')
                return
            if self.cloud.interrupt == kind:
                self.cloud.interrupt = None
                raise ConnectionError('上传中断')
            self.cloud.files[file_id] = dict(data=data, kind=kind, owner=self.user)
            self.cloud.writes += 1


def make_case(store, color='navy', terms=None):
    data = io.BytesIO()
    Image.new('RGB', (60, 40), color).save(data, 'PNG')
    result = store.pa_save_case([dict(name='测试.png', data=data.getvalue())],
        dict(memory='ETHUSD 锦和商业 microDB', data_kind='test', topic_ids=terms or ['brooks-high-2', 'brooks-pullback']),
        allow_duplicate=True)['case']
    store.pa_add_note(result['id'], '不许上传的私密笔记')
    store.pa_trial_setup(result['id'], dict(mode='mask', image_ids=[result['images'][0]['id']], cutoff=.5))
    return result, data.getvalue()


def test_two_users_five_cases_graph_private_records_restart(tmp_path):
    cloud = Cloud()
    a, b = Store(tmp_path / 'a'), Store(tmp_path / 'b')
    da, db = Drive(cloud, 'user-a'), Drive(cloud, 'user-b')
    wa, wb = Workspace(a, da), Workspace(b, db)
    inputs = [make_case(a if i < 3 else b, ['navy', 'red', 'green', 'pink', 'gray'][i]) for i in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        published = list(pool.map(lambda pair: (wa if pair[0] < 3 else wb).publish(pair[1][0]['id']), enumerate(inputs)))
    assert len(cloud.files) == 15  # each: original, cover, atomic manifest
    assert wa.refresh()['count'] == wb.refresh()['count'] == 5
    assert wa.catalogue() == wb.catalogue()
    for operation in (wa, wb):
        assert len(operation.search('H2')['cases']) == 5
        assert len(operation.search('High 2')['cases']) == 5
        assert len(operation.search('锦和')['cases']) == 5
    assert len(wa.search(filters={'contributor': 'me'})['cases']) == 3
    assert len(wb.search(filters={'contributor': 'me'})['cases']) == 2
    from workbench.pa_terms import graph
    g = graph(wa.catalogue())
    assert len(g['nodes']) == 2 and g['edges'][0]['count'] == 5
    case_id = published[0]['case_id']
    c = wa.case(case_id)
    assert '不许上传' not in json.dumps(cloud.files, ensure_ascii=False, default=str)
    assert wa.image(case_id, c['images'][0]['id']) == inputs[0][1]
    wa.record('notes', case_id, 'A 的个人研究')
    wa.record('trials', case_id, dict(judgment='wait', depth='quick', details={'presentation': c['trial_setup']}))
    wa.record('studied', case_id, None)
    assert not wb.case(case_id)['notes'] and not wb.personal()['trials']
    cloud.offline = True
    restarted = Workspace(Store(tmp_path / 'a'), da)
    assert len(restarted.catalogue()) == 5
    assert restarted.image(case_id, c['images'][0]['id']) == inputs[0][1]
    assert restarted.personal()['notes'][0]['text'] == 'A 的个人研究'
    assert 'A 的个人研究' not in str(a.rows("SELECT value FROM meta WHERE key LIKE 'pa_personal:%'"))
    with pytest.raises(ConnectionError):
        restarted.refresh()
    assert len(restarted.catalogue()) == 5


@pytest.mark.parametrize('failure', ['image-v1', 'cover-v1', 'case-v1'])
def test_interrupted_publish_has_no_partial_case_and_retry_is_idempotent(tmp_path, failure):
    cloud = Cloud()
    store = Store(tmp_path)
    workspace = Workspace(store, Drive(cloud, 'a'))
    c, _ = make_case(store)
    cloud.interrupt = failure
    with pytest.raises(ConnectionError):
        workspace.publish(c['id'])
    assert workspace.refresh()['count'] == 0
    first = workspace.publish(c['id'])
    files = copy.deepcopy(cloud.files)
    second = Workspace(Store(tmp_path), workspace.drive).publish(c['id'])
    assert first['case_id'] == second['case_id']
    assert cloud.files == files
    assert workspace.refresh()['count'] == 1


def test_same_case_simultaneous_publish_has_one_commit(tmp_path):
    cloud = Cloud()
    store = Store(tmp_path)
    c, _ = make_case(store)
    drive = Drive(cloud, 'a')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: Workspace(Store(tmp_path), drive).publish(c['id']), range(2)))
    assert results[0]['case_id'] == results[1]['case_id']
    assert len(cloud.files) == 3


def test_corruption_permissions_and_changed_snapshot_do_not_overwrite(tmp_path):
    cloud = Cloud()
    store = Store(tmp_path)
    w = Workspace(store, Drive(cloud, 'a'))
    c, original = make_case(store)
    published = w.publish(c['id'])
    w.refresh()
    public = w.case(published['case_id'])
    original_files = copy.deepcopy(cloud.files)
    store.pa_update_case(c['id'], {'memory': '已经编辑'})
    with pytest.raises(ValueError, match='快照'):
        w.publish(c['id'])
    assert original_files == cloud.files
    file_id = public['images'][0]['id']
    cloud.files[file_id]['data'] = b'corrupt'
    with pytest.raises(ValueError, match='校验'):
        w.image(public['id'], file_id)
    assert store.pa_image(c['images'][0]['id']) == original
    cloud.files[file_id]['data'] = original
    assert w.image(public['id'], file_id) == original
    manifest_id = public['id'].split(':')[1]
    cloud.files[manifest_id]['owner'] = 'forged-user'
    with pytest.raises(ValueError, match='身份'):
        w.refresh()
    assert len(w.catalogue()) == 1  # last validated snapshot survives bad network content


def test_unpublished_drafts_never_enter_public_and_owner_cannot_be_supplied(tmp_path):
    store = Store(tmp_path)
    w = Workspace(store, Drive(Cloud(), 'a'))
    make_case(store)
    assert w.refresh()['count'] == 0
    bridge = GrowthBridge(store)
    bridge._drive = w.drive
    assert not bridge.search_cases('', {'scope': 'public', 'owner': 'other'})['ok']
    assert not bridge.get_case('shared:unknown')['ok']
    assert bridge.bootstrap()['graph'] == {'nodes': [], 'edges': []}


def test_dpapi_roundtrip_and_no_plaintext():
    raw = b'private PA progress 123'
    encrypted = protect(raw)
    assert raw not in encrypted
    assert protect(encrypted, True) == raw
    with pytest.raises(ValueError):
        protect(encrypted[:-10], True)


def test_cache_rebuild_revoke_and_private_note_search(tmp_path):
    cloud = Cloud()
    store = Store(tmp_path)
    w = Workspace(store, Drive(cloud, 'a'))
    c, original = make_case(store)
    public_id = w.publish(c['id'])['case_id']
    w.refresh()
    public = w.case(public_id)
    image_id = public['images'][0]['id']
    assert w.image(public_id, image_id) == original
    cover = w.image(public_id, image_id, True)
    path = store.root / 'pa_growth/shared_cache' / FOLDER / image_id
    path.write_bytes(b'invalid cache')
    cloud.offline = True
    assert w.image(public_id, image_id, True, True) == cover
    with pytest.raises(ConnectionError):
        w.image(public_id, image_id)
    assert path.read_bytes() == b'invalid cache'
    cloud.offline = False
    assert w.image(public_id, image_id) == original
    assert path.read_bytes() == original
    w.record('notes', public_id, '专属笔记关键词')
    assert w.search('专属笔记关键词')['cases'][0]['match_reasons'] == ['个人研究']
    w.drive = Drive(cloud, 'b')
    w.refresh()
    assert not w.search('专属笔记关键词')['cases']
    def denied(online=False):
        if online:
            raise DriveError(401)
        return {'id': 'b', 'name': 'B'}
    w.drive.identity = denied
    with pytest.raises(DriveError):
        w.refresh()
    assert not w.catalogue()
    assert store.pa_case(c['id'])['images']


def test_learning_backup_is_encrypted_identity_checked_and_merges_without_loss(tmp_path):
    cloud = Cloud()
    store = Store(tmp_path / 'a')
    w = Workspace(store, Drive(cloud, 'a'))
    c, _ = make_case(store)
    public = w.publish(c['id'])['case_id']
    w.refresh()
    note = w.record('notes', public, '只属于 A 的学习备份')
    path = tmp_path / 'learning.palearn'
    assert w.export_learning(path)['saved']
    assert '只属于 A'.encode() not in path.read_bytes()
    other = Workspace(Store(tmp_path / 'b'), Drive(cloud, 'b'))
    with pytest.raises(ValueError, match='不属于'):
        other.restore_learning(path)
    assert not other.personal()['notes']
    restored = Workspace(Store(tmp_path / 'new-a'), Drive(cloud, 'a'))
    assert restored.restore_learning(path)['restored'] == 1
    assert restored.personal()['notes'] == [note]
    assert restored.restore_learning(path)['restored'] == 0
    corrupt = tmp_path / 'bad.palearn'
    corrupt.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(ValueError):
        restored.restore_learning(corrupt)
    assert restored.personal()['notes'] == [note]
    restored.store.execute('UPDATE meta SET value=? WHERE key=?', ('"broken ciphertext"', 'pa_personal:a'))
    restored.refresh()
    assert restored.case(public)['personal_error']
    assert restored.search('ETHUSD')['cases']
    before = restored.store.rows("SELECT value FROM meta WHERE key='pa_personal:a'")[0]['value']
    with pytest.raises(ValueError):
        restored.record('notes', public, '不能覆盖损坏记录')
    assert restored.store.rows("SELECT value FROM meta WHERE key='pa_personal:a'")[0]['value'] == before
    assert restored.restore_learning(path)['isolated_unreadable']
    assert restored.personal()['notes'] == [note]
    assert restored.store.rows("SELECT value FROM meta WHERE key LIKE 'pa_personal_issue:a:%'")[0]['value'] == before


def test_incremental_discovery_and_rebuilding_invalid_catalogue(tmp_path):
    import hashlib
    class RevisionDrive(Drive):
        reads = 0
        def info(self, file_id):
            info = super().info(file_id)
            return dict(info, md5Checksum=hashlib.md5(self.cloud.files[file_id]['data']).hexdigest())
        def list_cases(self):
            return [self.info(row['id']) for row in super().list_cases()]
        def read(self, *args):
            self.reads += 1
            return super().read(*args)
    cloud = Cloud()
    store = Store(tmp_path)
    drive = RevisionDrive(cloud, 'a')
    w = Workspace(store, drive)
    case, _ = make_case(store)
    public_id = w.publish(case['id'])['case_id']
    w.refresh()
    drive.reads = 0
    w.refresh()
    assert drive.reads == 0, 'unchanged immutable manifests do not redownload'
    store.execute('UPDATE meta SET value=? WHERE key=?', ('{broken cache', w.space_key('catalogue')))
    restarted = Workspace(Store(tmp_path), drive)
    assert restarted.catalogue() == []
    assert restarted.status()['cache_error']
    assert restarted.refresh()['count'] == 1
    assert restarted.case(public_id)['id'] == public_id
    assert not restarted.status()['cache_error']
    file_id = public_id.split(':')[1]
    cloud.files[file_id]['data'] += b' '
    with pytest.raises(ValueError, match='被修改'):
        restarted.refresh()
    assert restarted.catalogue()[0]['id'] == public_id


def test_manifest_cannot_inject_preview_bytes_or_misstate_trial_dimensions(tmp_path):
    cloud = Cloud()
    store = Store(tmp_path)
    w = Workspace(store, Drive(cloud, 'a'))
    c, _ = make_case(store)
    public_id = w.publish(c['id'])['case_id']
    manifest = cloud.files[public_id.split(':')[1]]
    value = json.loads(manifest['data'])
    value['images'][0]['data_url'] = 'data:image/png;base64,ZmFrZQ=='
    value['images'][0]['width'] = 6000
    manifest['data'] = json.dumps(value).encode()
    w.refresh()
    public = w.case(public_id)
    assert 'data_url' not in public['images'][0]
    with pytest.raises(ValueError, match='尺寸或格式'):
        w.image(public_id, public['images'][0]['id'])
    setup = public['trial_setup']
    with pytest.raises(ValueError, match='未知字段'):
        w.record('trials', public_id, dict(id='forged', judgment='wait', depth='quick', details={'presentation': setup}))
    assert not w.personal()['trials']


def test_cold_shared_thumbnails_do_not_block_search_or_private_save(tmp_path):
    store = Store(tmp_path)
    cloud = Cloud()
    bridge = GrowthBridge(store)
    bridge._drive = Drive(cloud, 'a')
    c, _ = make_case(store)
    public_id = bridge.publish_case(c['id'])['case_id']
    bridge.refresh_shared()
    public = bridge._public('case', public_id)
    entered, release = threading.Event(), threading.Event()
    read = bridge._drive.read
    def slow(*args):
        entered.set()
        assert release.wait(10)
        return read(*args)
    bridge._drive.read = slow
    # Metadata search neither downloads thumbnails nor waits for their I/O.
    assert bridge.search_cases('ETH', {'scope': 'public'})['cases'][0]['thumbnail_pending']
    with ThreadPoolExecutor(2) as pool:
        image = pool.submit(bridge.get_image, public_id, public['images'][0]['id'], True)
        assert entered.wait(3)
        try:
            staging = pool.submit(bridge._stage, [dict(name='another.png', data=make_case(Store(tmp_path / 'source'), 'pink')[1])])
            pending = staging.result(timeout=3)['pending'][0]['token']
            saved = pool.submit(bridge.save_capture, [pending], {}, {})
            assert saved.result(timeout=3)['ok']
        finally:
            release.set()
        assert image.result(timeout=3)['ok']


def test_folder_change_never_reuses_old_grants_cache_or_upload_reservations(tmp_path, monkeypatch):
    from workbench import pa_shared
    store = Store(tmp_path)
    old_cloud, new_cloud = Cloud(), Cloud()
    old = Workspace(store, Drive(old_cloud, 'a', folder='old-authority'))
    monkeypatch.setattr(pa_shared, 'FOLDER', 'old-authority')
    c, original = make_case(store)
    first = old.publish(c['id'])['case_id']
    old.refresh()
    public = old.case(first)
    old.image(first, public['images'][0]['id'])
    assert old.catalogue()  # Warm the same Store instance's in-memory cache.
    old_files = copy.deepcopy(old_cloud.files)
    old_meta = store.rows("SELECT key,value FROM meta WHERE key LIKE 'pa_shared_%' OR key LIKE 'pa_publish:%'")

    monkeypatch.setattr(pa_shared, 'FOLDER', FOLDER)
    current = Workspace(store, Drive(new_cloud, 'a'))
    assert not current.status()['cached']
    assert not current.catalogue()
    with pytest.raises(ValueError):
        current.image(first, public['images'][0]['id'])
    assert current.refresh()['count'] == 0
    second = current.publish(c['id'])['case_id']
    assert first != second
    assert current.refresh()['count'] == 1
    assert current.catalogue()[0]['id'] == second
    assert old_cloud.files == old_files
    assert store.pa_image(c['images'][0]['id']) == original
    for row in old_meta:
        assert store.rows('SELECT value FROM meta WHERE key=?', (row['key'],))[0]['value'] == row['value']


def test_unscoped_legacy_cache_and_grant_are_preserved_but_not_activated(tmp_path):
    store = Store(tmp_path)
    w = Workspace(store, Drive(Cloud(), 'a'))
    w.set('pa_shared_access:a', {'granted': True, 'writable': True})
    w.set('pa_shared_catalogue', [{'id': 'shared:old-file'}])
    w.set('pa_shared_catalogue_stamp', 'legacy-stamp')
    store._pa_shared_catalogue_cache = ('legacy-stamp', [{'id': 'shared:old-file'}])
    assert not w.status()['cached']
    assert w.catalogue() == []
    assert w.refresh()['count'] == 0
    assert w.get('pa_shared_catalogue') == [{'id': 'shared:old-file'}]
    assert w.get('pa_shared_access:a')['granted']
