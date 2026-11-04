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
    def __init__(self, cloud, user):
        self.cloud, self.user = cloud, user

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
            return dict(id=file_id, parents=[FOLDER], owners=[dict(permissionId=value['owner'])])

    def read(self, file_id, limit):
        self.info(file_id)
        data = self.cloud.files[file_id]['data']
        assert len(data) <= limit
        return data

    def list_cases(self):
        self.check()
        with self.cloud.lock:
            return [dict(id=k, parents=[FOLDER], owners=[dict(permissionId=v['owner'])])
                    for k, v in self.cloud.files.items() if v['kind'] == 'case-v1']

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
