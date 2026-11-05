"""Only new shared collaboration boundaries; synthetic Drive and isolated Stores."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from tests.test_pa_shared import Cloud, Drive, make_case
from workbench.store import Store
from workbench.pa_shared import Workspace, encoded, DriveError, compose_case, resolve_events
from workbench.pa_terms import graph
from gui.growth_bridge import GrowthBridge


class TeamDrive(Drive):
    def check_folder(self):
        result = super().check_folder()
        self.folder_owners = ['owner']
        return result

    def put_once(self, file_id, name, data, mime, kind):
        barrier = getattr(self.cloud, 'barrier', None)
        if kind == 'collaboration-v1' and barrier:
            barrier.wait(timeout=5)
        super().put_once(file_id, name, data, mime, kind)
        if kind == 'collaboration-v1' and barrier:
            barrier.wait(timeout=5)


@pytest.fixture
def team(tmp_path):
    cloud = Cloud()
    a = Workspace(Store(tmp_path / 'a'), TeamDrive(cloud, 'owner'))
    b = Workspace(Store(tmp_path / 'b'), TeamDrive(cloud, 'member'))
    private, image = make_case(a.store)
    case_id = a.publish(private['id'])['case_id']
    a.refresh()
    b.refresh()
    return cloud, a, b, case_id, private, image


def fields(title):
    return dict(title=title, memory='新的案例描述', keywords=['collaborative'], term_ids=['brooks-pullback'])


def revise(workspace, case_id, title, version=None):
    return workspace.collaborate(case_id, 'revision', version or workspace.case(case_id)['revision_version'], fields(title))


def test_member_revision_preserves_originals_history_and_rejects_stale(team):
    cloud, a, b, case_id, _, image = team
    originals = copy.deepcopy(cloud.files)
    old = b.case(case_id)['revision_version']
    bridge = GrowthBridge(b.store)
    bridge._drive = b.drive
    response = bridge.revise_public_case(case_id, old, fields('Member revision'))
    assert response['ok']  # Actual guarded API; edits another contributor.
    a.refresh()
    c = a.case(case_id)
    assert c['title'] == 'Member revision' and c['contributor']['id'] == 'owner'
    assert c['history'][0]['author']['id'] == 'member'
    assert c['history'][0]['values'] == fields('Member revision')
    assert a.image(case_id, c['images'][0]['id']) == image
    assert all(cloud.files[k] == v for k, v in originals.items())
    assert [n['id'] for n in graph(a.catalogue())['nodes']] == ['brooks-pullback']
    writes = cloud.writes
    with pytest.raises(ValueError, match='重新加载'):
        revise(a, case_id, 'stale', old)
    assert cloud.writes == writes
    with pytest.raises(ValueError, match='只能修改'):
        a.collaborate(case_id, 'revision', c['revision_version'], dict(fields('bad'), images=[]))
    assert cloud.writes == writes


def test_simultaneous_revisions_are_visible_conflicts_and_reload_can_resolve(team):
    cloud, a, b, case_id, _, _ = team
    cloud.barrier = threading.Barrier(2)
    version = a.case(case_id)['revision_version']
    def write(pair):
        workspace, title = pair
        with pytest.raises(ValueError, match='并发修改'):
            revise(workspace, case_id, title, version)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, [(a, 'A'), (b, 'B')]))
    cloud.barrier = None
    a.refresh()
    c = a.case(case_id)
    assert c['revision_conflict'] and len(c['history']) == 2
    assert c['title'] == c['original_metadata']['title']  # No arbitrary winner.
    revise(a, case_id, 'Reviewed A and B')
    b.refresh()
    c = b.case(case_id)
    assert c['title'] == 'Reviewed A and B' and not c['revision_conflict']
    assert len(c['history']) == 3 and len(c['history'][-1]['parents']) == 2


def test_public_comments_survive_revisions_without_exposing_personal_notes(team):
    cloud, a, b, case_id, _, _ = team
    a.record('notes', case_id, 'PRIVATE_ACCOUNT_NOTE')
    b.collaborate(case_id, 'comment', 'comment-request', '<script>plain text</script>')
    revise(b, case_id, 'Updated')
    a.refresh()
    c = a.case(case_id)
    assert c['comments'][0]['text'] == '<script>plain text</script>'
    assert c['comments'][0]['author']['id'] == 'member' and c['comments'][0]['created']
    assert c['notes'][0]['text'] == 'PRIVATE_ACCOUNT_NOTE'
    assert b.case(case_id)['notes'] == []
    serialized = repr(cloud.files)
    assert 'PRIVATE_ACCOUNT_NOTE' not in serialized and '不许上传的私密笔记' not in serialized
    with pytest.raises(ValueError, match='纯文本留言'):
        b.collaborate(case_id, 'comment', 'comment-request', '  ')
    # A stale list entry is not evidence that the whole folder was revoked.
    event_id = c['comments'][0]['id']
    read = a.drive.read
    def missing_record(file_id, limit):
        if file_id == event_id:
            raise DriveError(404)
        return read(file_id, limit)
    a.drive.read = missing_record
    with pytest.raises(ValueError, match='保留有效缓存'):
        a.refresh()
    assert a.status()['cached'] and a.case(case_id)['comments'] == c['comments']


def test_only_owner_hides_and_restores_without_deleting_assets_or_comments(team):
    cloud, a, b, case_id, _, image = team
    b.collaborate(case_id, 'comment', 'comment-request', 'Keep this comment')
    originals = copy.deepcopy(cloud.files)
    version = a.case(case_id)['visibility_version']
    with pytest.raises(ValueError, match='所有者'):
        b.collaborate(case_id, 'visibility', version, True)
    a.collaborate(case_id, 'visibility', version, True)
    b.refresh()
    assert not a.search()['cases'] and not b.search()['cases']
    assert not graph(a.catalogue())['nodes']
    assert a.store.pa_random_trial('public', drive=a.drive)['eligible'] == 0
    assert a.search(filters={'hidden': 'yes'})['cases'][0]['id'] == case_id
    with pytest.raises(ValueError, match='所有者'):
        b.search(filters={'hidden': 'yes'})
    with pytest.raises(ValueError, match='不存在'):
        b.case(case_id)
    hidden = a.case(case_id)
    assert hidden['hidden'] and hidden['trial_setup'] is None
    assert a.image(case_id, hidden['images'][0]['id']) == image
    a.collaborate(case_id, 'visibility', hidden['visibility_version'], False)
    b.refresh()
    restored = b.case(case_id)
    assert not restored['hidden'] and restored['comments'][0]['text'] == 'Keep this comment'
    assert b.store.pa_random_trial('public', drive=b.drive)['eligible'] == 1
    assert all(cloud.files[k] == v for k, v in originals.items())
    # A member cannot impersonate the owner by writing their ID into JSON.
    poison = dict(version=1, type='visibility', case_id=case_id, author=dict(id='owner', name='Owner'),
                  created='2026-11-05', parents=[], hidden=True)
    cloud.files['forged'] = dict(kind='collaboration-v1', owner='member', data=encoded(poison))
    with pytest.raises(ValueError, match='身份'):
        b.refresh()
    assert b.case(case_id)['hidden'] is False  # Valid cache is retained.


def test_collaboration_ui_render():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    result = subprocess.run([node, str(Path(__file__).with_name('pa_collaboration_ui.cjs'))],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_criss_cross_resolutions_keep_only_undisputed_metadata():
    original = dict(id='shared:case', title='Original', memory='Undisputed', keywords=['original'],
                    term_ids=['brooks-high-2'], updated='2026-11-05')
    def event(name, parents):
        return dict(id=name, type='revision', case_id=original['id'], created=name, parents=parents,
                    values=dict(title=name, memory=name, keywords=[name], term_ids=['brooks-pullback']))
    events = [event('A', []), event('B', []), event('C', ['A', 'B']), event('D', ['A', 'B'])]
    assert resolve_events(events) == (['C', 'D'], None)
    result = compose_case(original, events)
    assert result['revision_conflict'] and len(result['history']) == 4
    assert all(result[k] == original[k] for k in ('title', 'memory', 'keywords', 'term_ids'))
    # A single explicit resolution is authoritative; no arbitrary fallback.
    assert compose_case(original, events[:-1])['title'] == 'C'
    assert compose_case(original, events + [event('E', ['C', 'D'])])['title'] == 'E'
    # If there is a known shared version, retain it rather than the original.
    with_prior = [event('P', [])] + [dict(e, parents=['P']) if e['id'] in ('A', 'B') else e for e in events]
    assert resolve_events(with_prior) == (['C', 'D'], 'P')


def test_comment_upload_ack_loss_reuses_file_and_allows_new_identical_comment(team):
    cloud, a, b, case_id, _, _ = team
    bridge = GrowthBridge(a.store)
    bridge._drive = a.drive
    put_once = a.drive.put_once
    lost = False
    def upload_then_lose_ack(*args):
        nonlocal lost
        put_once(*args)
        if args[-1] == 'collaboration-v1' and not lost:
            lost = True
            raise ConnectionError('simulated lost acknowledgement after upload')
    a.drive.put_once = upload_then_lose_ack
    first = bridge.comment_public_case(case_id, 'Same public text', 'pending-1')
    assert not first['ok'] and lost
    committed = {k: v.copy() for k, v in cloud.files.items() if v['kind'] == 'collaboration-v1'}
    assert len(committed) == 1
    # Recreate the bridge/Store to prove the reservation is durable.
    retry = GrowthBridge(Store(a.store.root))
    retry._drive = a.drive
    assert retry.comment_public_case(case_id, 'Same public text', 'pending-1')['ok']
    assert retry.comment_public_case(case_id, 'Same public text', 'pending-1')['ok']
    assert {k: v for k, v in cloud.files.items() if v['kind'] == 'collaboration-v1'} == committed
    assert retry.comment_public_case(case_id, 'Same public text', 'new-intent-2')['ok']
    b.refresh()
    assert [c['text'] for c in b.case(case_id)['comments']] == ['Same public text'] * 2
    assert not b.case(case_id)['notes'] and '不许上传的私密笔记' not in repr(cloud.files)
    node = shutil.which('node')
    assert node, 'Node required for the actual comment retry handler'
    result = subprocess.run([node, str(Path(__file__).with_name('pa_collaboration_ui.cjs')), '--comment-retry'],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unchanged_collaboration_cache_revalidates_identity_and_downloads_only_when_needed(team):
    cloud, a, b, case_id, _, _ = team
    b.collaborate(case_id, 'comment', 'cache-comment', 'Cache me')
    a.collaborate(case_id, 'visibility', a.case(case_id)['visibility_version'], False)
    list_records, read = a.drive.list_records, a.drive.read
    mode = None
    downloads = []
    def rows(kind):
        result = list_records(kind)
        for row in result:
            row.update(trashed=False, md5Checksum=hashlib.md5(cloud.files[row['id']]['data']).hexdigest())
            if kind == 'collaboration-v1' and cloud.files[row['id']]['owner'] == 'member':
                if mode == 'missing-info':
                    del row['trashed']
                elif mode == 'wrong-owner':
                    row['owners'] = [dict(permissionId='impostor')]
                elif mode == 'wrong-folder':
                    row['parents'] = ['other-folder']
                elif mode == 'changed-md5':
                    row['md5Checksum'] = 'changed'
        return result
    def track(file_id, limit):
        if cloud.files[file_id]['kind'] == 'collaboration-v1':
            downloads.append(file_id)
        return read(file_id, limit)
    a.drive.list_records, a.drive.read = rows, track
    a.refresh()  # Warm MD5 cache.
    downloads.clear()
    a.refresh()
    assert not downloads
    mode = 'missing-info'
    a.refresh()
    assert len(downloads) == 1  # Full validation remains the conservative fallback.
    downloads.clear()
    for mode in ('wrong-owner', 'wrong-folder', 'changed-md5'):
        with pytest.raises(ValueError):
            a.refresh()
        assert a.status()['cached'] and not downloads
    mode = None
    check_folder = a.drive.check_folder
    def no_longer_owner():
        result = check_folder()
        a.drive.folder_owners = []
        return result
    a.drive.check_folder = no_longer_owner
    with pytest.raises(ValueError, match='所有者身份'):
        a.refresh()
    assert not downloads  # Unchanged bytes do not bypass live admin validation.


def test_comment_reply_keeps_newly_typed_draft():
    node = shutil.which('node')
    assert node, 'Node required for the production delayed-comment handler'
    result = subprocess.run([node, str(Path(__file__).with_name('pa_collaboration_ui.cjs')), '--comment-draft'],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
