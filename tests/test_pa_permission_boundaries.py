"""Confirmed denial invalidates every window; transport failures keep offline reads."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from gui.growth_bridge import GrowthBridge
from workbench.pa_auth import DriveError
from workbench.pa_shared import Workspace
from workbench.store import Store
from tests.test_pa_shared import Cloud, Drive, make_case


def prepared(tmp_path):
    store, cloud = Store(tmp_path), Cloud()
    drive = Drive(cloud, 'a')
    w = Workspace(store, drive)
    local, original = make_case(store)
    public = w.publish(local['id'])['case_id']
    w.refresh()
    image = w.case(public)['images'][0]['id']
    return store, drive, w, local, original, public, image


@pytest.mark.parametrize('status', [401, 403, 404])
@pytest.mark.parametrize('operation', ['original', 'thumbnail', 'publish', 'refresh'])
def test_confirmed_denial_revokes_all_windows_but_preserves_private_originals(tmp_path, monkeypatch, status, operation):
    store, drive, w, local, original, public, image = prepared(tmp_path)
    other = Workspace(Store(store.root), drive)
    # Cache the opposite representation, so denial must hide already cached data too.
    w.image(public, image, thumbnail=operation != 'thumbnail')
    bridges = [GrowthBridge(store), GrowthBridge(Store(store.root))]
    for bridge in bridges:
        bridge._drive = drive
        assert bridge.bootstrap()['ok']
    def denied(*a, **k):
        raise DriveError(status)
    if operation == 'publish':
        monkeypatch.setattr(drive, 'check_folder', denied)
        result = bridges[0].publish_case(local['id'])
    elif operation == 'refresh':
        monkeypatch.setattr(drive, 'list_cases', denied)
        result = bridges[0].refresh_shared()
    else:
        monkeypatch.setattr(drive, 'read', denied)
        if status == 404:
            monkeypatch.setattr(drive, 'check_folder', denied)
        result = bridges[0].get_image(public, image, operation == 'thumbnail')
    assert not result['ok'] and result['error_kind'] == 'security'
    assert not w.status()['cached'] and not other.status()['cached']
    assert w.status()['reconnect_required'] is (status == 401)
    assert other.status()['reconnect_required'] is (status == 401)
    assert w.catalogue() == other.catalogue() == []
    with pytest.raises(ValueError):
        other.image(public, image, thumbnail=operation != 'thumbnail', cache_only=True)
    assert not bridges[1].get_case(public)['ok']
    assert bridges[1].bootstrap()['public_cases'] == []
    assert store.pa_image(local['images'][0]['id']) == original


def test_offline_original_failure_does_not_revoke_valid_cover_or_other_window(tmp_path):
    store, drive, w, local, original, public, image = prepared(tmp_path)
    cover = w.image(public, image, True)
    bridge = GrowthBridge(store)
    bridge._drive = drive
    assert bridge.bootstrap()['ok']
    drive.cloud.offline = True
    denied = bridge.get_image(public, image)
    assert not denied['ok'] and denied['error_kind'] == 'offline'
    other = Workspace(Store(store.root), drive)
    assert other.status()['cached'] and len(other.catalogue()) == 1
    assert not other.status()['reconnect_required']
    assert other.image(public, image, True, True) == cover


def test_inflight_refresh_cannot_restore_grant_after_image_denial(tmp_path, monkeypatch):
    store, drive, w, _, _, public, image = prepared(tmp_path)
    entered, release = threading.Event(), threading.Event()
    listing = drive.list_cases
    def delayed():
        rows = listing()
        entered.set()
        assert release.wait(5)
        return rows
    monkeypatch.setattr(drive, 'list_cases', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(Workspace(Store(store.root), drive).refresh)
        assert entered.wait(5)
        def deny(*args):
            raise DriveError(403)
        monkeypatch.setattr(drive, 'read', deny)
        with pytest.raises(DriveError):
            w.image(public, image)
        release.set()
        with pytest.raises(ValueError):
            pending.result(timeout=5)
    assert not w.status()['cached']
