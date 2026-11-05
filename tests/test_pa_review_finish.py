"""Focused PR105 closing regressions: isolated records, fake Drive and consent."""
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import ProxyHandler, build_opener

from PIL import Image
import pytest

from gui.growth_bridge import GrowthBridge
from workbench import pa_auth
from workbench.pa_auth import GoogleSession, SessionChanged
from workbench.pa_shared import Workspace
from workbench.store import Store
from tests.test_pa_shared import Cloud, Drive, make_case
from tests.test_pa_permission_boundaries import prepared
from tests.test_pa_session_boundaries import credentials


def learning_bridge(tmp_path):
    store = Store(tmp_path / 'runtime')
    case, _ = make_case(store)
    setup = store.pa_trial_setup(case['id'])
    store.pa_add_trial(case['id'], 'wait', details={'presentation': setup})
    session = GoogleSession(store.root)
    session.path = tmp_path / 'session.dpapi'
    class AccountDrive(Drive):
        def identity(self, online=False):
            value = session.load()
            if not value:
                raise ValueError('请先连接 Google Drive')
            return value['user']
    drive = AccountDrive(Cloud(), 'unused')
    drive.session = session
    bridge = GrowthBridge(store)
    bridge._drive = drive
    return bridge, session, case, setup


def test_growth_counts_follow_displayed_account_and_disconnect_restores_local_history(tmp_path):
    bridge, session, case, setup = learning_bridge(tmp_path)
    assert bridge.bootstrap()['stats']['notes'] == 1
    session.save(credentials('a', time.time() + 3600))
    connected = bridge.bootstrap()
    assert connected['stats']['notes'] == connected['stats']['trials'] == 0
    assert connected['notes'] == connected['trials'] == []
    assert bridge.add_note(case['id'], 'account-a-note')['ok']
    assert bridge.save_trial(case['id'], {'judgment': 'wait', 'depth': 'quick',
                                         'details': {'presentation': setup}})['ok']
    account = bridge.growth()
    assert account['stats']['notes'] == len(account['notes']) == 1
    assert account['stats']['trials'] == len(account['trials']) == 1
    assert bridge.disconnect_google()['ok']
    assert session.load() is None
    local = bridge.bootstrap()
    assert local['stats']['notes'] == len(local['notes']) == 1
    assert local['stats']['trials'] == len(local['trials']) == 1
    assert local['notes'][0]['text'] == '不许上传的私密笔记'
    session.save(credentials('a', time.time() + 3600))
    assert bridge.bootstrap()['notes'][0]['text'] == 'account-a-note'


def test_growth_does_not_merge_previous_accounts_learning_into_new_account(tmp_path):
    bridge, session, case, _ = learning_bridge(tmp_path)
    session.save(credentials('a', time.time() + 3600))
    bridge.bootstrap()
    assert bridge.add_note(case['id'], 'account-a-only')['ok']
    session.save(credentials('b', time.time() + 3600))
    data = bridge.bootstrap()
    assert data['stats']['notes'] == 0 and data['records'] == []
    assert 'account-a-only' not in str(data)
    assert len(bridge._store.pa_growth()['notes']) == 1


def test_nonfirst_thumbnail_uses_its_own_original_while_first_uses_cover(tmp_path):
    store, drive = Store(tmp_path), Drive(Cloud(), 'a')
    images = []
    for color in ('navy', 'red'):
        output = io.BytesIO()
        Image.new('RGB', (60, 40), color).save(output, 'PNG')
        images.append({'name': color + '.png', 'data': output.getvalue()})
    case = store.pa_save_case(images, {'data_kind': 'test', 'topic_ids': ['brooks-high-2']})['case']
    w = Workspace(store, drive)
    public_id = w.publish(case['id'])['case_id']
    w.refresh()
    public = w.case(public_id)
    first, second = public['images']
    cover = drive.read(public['cover']['id'], 1024 * 1024)
    assert w.image(public_id, second['id'], thumbnail=True) == images[1]['data']
    assert w.image(public_id, first['id'], thumbnail=True) == cover
    assert w.image(public_id, first['id']) == images[0]['data']


@pytest.mark.parametrize('writable', [True, False])
def test_single_image_404_keeps_directory_and_other_cached_images(tmp_path, monkeypatch, writable):
    store, drive, w, _, _, public, image = prepared(tmp_path)
    cover = w.image(public, image, thumbnail=True)
    epoch = w.status()['access_epoch']
    monkeypatch.setattr(drive, 'read', lambda *a: (_ for _ in ()).throw(pa_auth.DriveError(404)))
    monkeypatch.setattr(drive, 'check_folder', lambda: writable)  # Read-only is still accessible.
    bridge = GrowthBridge(store)
    bridge._drive = drive
    bridge.bootstrap()
    failed = bridge.get_image(public, image)
    assert not failed['ok'] and '图片' in failed['error']
    assert failed.get('error_kind') != 'security'
    other = Workspace(Store(store.root), drive)
    assert other.status()['cached'] and other.status()['access_epoch'] == epoch
    assert len(other.catalogue()) == 1
    assert other.image(public, image, thumbnail=True, cache_only=True) == cover


@pytest.mark.parametrize('intervening', ['renew', 'switch', 'logout'])
def test_interactive_login_accepts_only_background_renewal_of_same_generation(tmp_path, monkeypatch, intervening):
    client = tmp_path / 'client.json'
    client.write_text(json.dumps({'installed': {'client_id': 'test.apps.googleusercontent.com',
                                               'client_secret': 'public-test-value'}}), encoding='utf-8')
    session, other = GoogleSession(tmp_path), GoogleSession(tmp_path)
    session.path = other.path = tmp_path / 'session.dpapi'
    session.save(credentials('a'))
    threads = []
    def browser(url):
        args = parse_qs(urlparse(url).query)
        def callback():
            opener = build_opener(ProxyHandler({}))
            with opener.open(args['redirect_uri'][0] + '?' + urlencode({
                    'state': args['state'][0], 'code': 'one-use-test'}), timeout=5) as response:
                assert response.status == 200
        thread = threading.Thread(target=callback)
        threads.append(thread)
        thread.start()
        return True
    def request(url, **kwargs):
        if url.endswith('/token'):
            grant = parse_qs(kwargs['data'].decode())['grant_type'][0]
            if grant == 'refresh_token':
                return b'{"access_token":"renewed-old","expires_in":3600}', {}
            if intervening == 'renew':
                assert other.access_token() == 'renewed-old'
            elif intervening == 'switch':
                other.save(credentials('b', time.time() + 3600))
            else:
                other.logout()
            return json.dumps({'access_token': 'new-access', 'refresh_token': 'new-refresh',
                               'expires_in': 3600, 'scope': pa_auth.SCOPE}).encode(), {}
        return b'{"user":{"permissionId":"new-user","displayName":"New"}}', {}
    monkeypatch.setattr(pa_auth.webbrowser, 'open', browser)
    monkeypatch.setattr(pa_auth, 'request', request)
    if intervening == 'renew':
        assert session.login(client, consent_timeout=5)['id'] == 'new-user'
        assert other.load()['user']['id'] == 'new-user'
    else:
        with pytest.raises(SessionChanged):
            session.login(client, consent_timeout=5)
        assert other.load() is None if intervening == 'logout' else other.load()['user']['id'] == 'b'
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
