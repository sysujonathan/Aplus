"""Bounded Drive requests and desktop consent without real external credentials."""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest

from workbench import pa_auth, pa_shared
from workbench.pa_auth import GoogleSession, DriveError
from workbench.pa_shared import GoogleDrive, FOLDER


def test_desktop_login_pins_google_pkce_state_and_keeps_secrets_encrypted(tmp_path, monkeypatch):
    client = tmp_path / 'desktop.json'
    client.write_text(json.dumps({'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'only-test',
                                              'auth_uri': 'https://evil.invalid', 'token_uri': 'https://evil.invalid'}}))
    captured = {}
    def browser(url):
        assert url.startswith('https://accounts.google.com/o/oauth2/v2/auth?')
        params = parse_qs(urlparse(url).query)
        captured.update(params)
        import threading
        def reply():
            callback = params['redirect_uri'][0]
            try:
                urlopen(callback + '?state=wrong&code=bad', timeout=5)
            except HTTPError as exc:
                assert exc.code == 400
            with urlopen(callback + '?state=' + params['state'][0] + '&code=one-use-test-code', timeout=5) as response:
                assert response.status == 200
        threading.Thread(target=reply).start()
        return True
    def remote(url, **kwargs):
        assert url in ('https://oauth2.googleapis.com/token', 'https://www.googleapis.com/drive/v3/about?fields=user')
        if url.endswith('/token'):
            data = parse_qs(kwargs['data'].decode())
            assert data['code'] == ['one-use-test-code']
            expected = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'][0].encode()).digest()).decode().rstrip('=')
            assert captured['code_challenge'] == [expected]
            assert captured['code_challenge_method'] == ['S256']
            return json.dumps(dict(access_token='test-access', refresh_token='test-refresh', expires_in=3600,
                                   scope=pa_auth.SCOPE)).encode(), {}
        return b'{"user":{"permissionId":"stable-drive-user","displayName":"Test"}}', {}
    monkeypatch.setattr(pa_auth.webbrowser, 'open', browser)
    monkeypatch.setattr(pa_auth, 'request', remote)
    session = GoogleSession()
    session.path = tmp_path / 'credential.dpapi'
    assert session.login(client)['id'] == 'stable-drive-user'
    assert b'test-refresh' not in session.path.read_bytes()
    restored = GoogleSession()
    restored.path = session.path
    assert restored.access_token() == 'test-access'
    restored.logout()
    assert not session.path.exists()


class Session:
    def access_token(self):
        return 'isolated-test-token'


def test_independent_runtime_profiles_do_not_share_active_google_session(tmp_path):
    first = GoogleSession(tmp_path / 'a')
    second = GoogleSession(tmp_path / 'b')
    assert first.path != second.path
    assert first.path == GoogleSession(tmp_path / 'a').path
    assert first.path.name == second.path.name == 'session.dpapi'


def test_revoked_refresh_requires_reconnection_without_rewriting_credentials(tmp_path, monkeypatch):
    session = GoogleSession(tmp_path)
    session.path = tmp_path / 'credential.dpapi'
    session.save(dict(expires=0, client_id='fake', client_secret='fake', refresh_token='fake'))
    monkeypatch.setattr(pa_auth, 'request', lambda *a, **k: (_ for _ in ()).throw(DriveError(400)))
    with pytest.raises(DriveError, match='重新连接') as error:
        session.access_token()
    assert error.value.status == 401
    assert session.value['expires'] == 0


def test_drive_idempotent_upload_never_patches_existing_or_follows_external_location(monkeypatch):
    drive = GoogleDrive(Session())
    calls = []
    def api(url, **kwargs):
        calls.append((url, kwargs.get('method')))
        if '/upload/' in url:
            return b'', {'Location': 'https://evil.invalid/upload'}
        raise AssertionError(url)
    monkeypatch.setattr(pa_shared, 'request', api)
    monkeypatch.setattr(drive, 'read', lambda *args: (_ for _ in ()).throw(DriveError(404)))
    with pytest.raises(ValueError, match='上传地址'):
        drive.put_once('reserved', 'sample', b'bytes', 'image/png', 'image-v1')
    assert len(calls) == 1
    assert calls[0][0].startswith('https://www.googleapis.com/upload/drive/')
    monkeypatch.setattr(drive, 'read', lambda *args: b'bytes')
    drive.put_once('reserved', 'sample', b'bytes', 'image/png', 'image-v1')
    assert len(calls) == 1
    with pytest.raises(ValueError, match='冲突'):
        drive.put_once('reserved', 'sample', b'other', 'image/png', 'image-v1')


def test_read_checks_folder_before_fetch_and_listing_paginates(monkeypatch):
    drive = GoogleDrive(Session())
    called = []
    monkeypatch.setattr(drive, 'info', lambda _: dict(parents=['wrong'], size='2'))
    monkeypatch.setattr(drive, 'call', lambda *a, **k: called.append(a))
    with pytest.raises(ValueError, match='指定'):
        drive.read('id', 10)
    assert called == []
    def call(path, **kwargs):
        called.append(path)
        args = parse_qs(urlparse(path).query)
        assert FOLDER in args['q'][0] and 'trashed=false' in args['q'][0]
        if 'pageToken' not in args:
            return b'{"files":[{"id":"a"}],"nextPageToken":"second"}', {}
        assert args['pageToken'] == ['second']
        return b'{"files":[{"id":"b"}]}', {}
    monkeypatch.setattr(drive, 'call', call)
    assert drive.list_cases() == [{'id': 'a'}, {'id': 'b'}]


def test_bearer_redirects_disabled_and_error_never_exposes_token():
    assert pa_auth.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid') is None
    assert 'test-token' not in str(DriveError(403))
