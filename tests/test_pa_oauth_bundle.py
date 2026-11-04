"""Public Desktop resource and real build admission, never live credentials."""
import json
import sys
from unittest.mock import Mock

import pytest

from workbench import pa_oauth_config as config
from workbench.pa_auth import GoogleSession
from tools import build_installer
from tools.verify_oauth_resources import audit_payload
from gui.growth_bridge import GrowthBridge
from workbench.store import Store
from tests.test_pa_shared import Cloud, Drive


def public(path, client='test'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'installed': {'client_id': client + '.apps.googleusercontent.com',
                                            'client_secret': 'public-test-value'}}), encoding='utf-8')
    return path


def test_frozen_resource_ignores_developer_override_and_source_can_use_one(tmp_path, monkeypatch):
    bundled = public(tmp_path / 'resources/client.json')
    override = public(tmp_path / 'developer.json', 'developer')
    monkeypatch.setattr(config, 'resource_path', lambda: bundled)
    monkeypatch.setenv('APLUS_GOOGLE_DESKTOP_CLIENT', str(override))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert config.load_client()['client_id'].startswith('test.')
    monkeypatch.setattr(sys, 'frozen', False)
    assert config.load_client()['client_id'].startswith('developer.')


@pytest.mark.parametrize('payload', [
    {'web': {}}, {'type': 'service_account', 'private_key': 'never-ship'},
    {'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'public-test-value', 'refresh_token': 'never-ship'}},
    {'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'public-test-value'}, 'user': {'email': 'private.invalid'}},
    {'installed': {'client_id': 'bad host', 'client_secret': 'public-test-value'}},
])
def test_resource_rejects_user_credentials_service_keys_and_unknown_data(tmp_path, payload):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        config.read_client(path)


@pytest.mark.parametrize('raw', [None, b'{broken', b'\xff\xff'])
def test_missing_or_corrupt_frozen_config_never_opens_browser_or_file_picker(tmp_path, monkeypatch, raw):
    path = tmp_path / 'config.json'
    if raw is not None:
        path.write_bytes(raw)
    monkeypatch.setattr(config, 'resource_path', lambda: path)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    browser = Mock(side_effect=AssertionError('Must fail before consent'))
    monkeypatch.setattr('workbench.pa_auth.webbrowser.open', browser)
    session = GoogleSession(tmp_path)
    session.path = tmp_path / 'session.dpapi'
    with pytest.raises(ValueError, match='Google 登录配置'):
        session.login()
    browser.assert_not_called()
    with pytest.raises(ValueError, match='仅使用内置'):
        session.login(tmp_path / 'manual.json')


def test_connect_uses_embedded_client_without_file_dialog_and_returns_to_window(tmp_path):
    bridge = GrowthBridge(Store(tmp_path))
    drive = Drive(Cloud(), 'a')
    drive.session = Mock()
    drive.session.login.return_value = drive.identity()
    bridge._drive = drive
    bridge._window = Mock()
    bridge._window.create_file_dialog.side_effect = AssertionError('No OAuth file picker')
    result = bridge.connect_google()
    assert result['ok'] and result['user']['id'] == 'a'
    drive.session.login.assert_called_once_with()
    bridge._window.show.assert_called_once_with()
    bridge._window.create_file_dialog.assert_not_called()


def test_failed_consent_returns_to_pa_with_retry_error_and_keeps_current_login(tmp_path):
    bridge = GrowthBridge(Store(tmp_path))
    drive = Drive(Cloud(), 'a')
    drive.session = Mock()
    drive.session.login.side_effect = ValueError('Google 授权已超时，请在成长空间重新点击连接')
    bridge._drive = drive
    bridge._window = Mock()
    result = bridge.connect_google()
    assert not result['ok'] and '重新点击连接' in result['error']
    bridge._window.show.assert_called_once_with()
    drive.session.logout.assert_not_called()
    bridge._window.create_file_dialog.assert_not_called()


def test_build_fails_before_any_compiler_when_public_resource_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(build_installer, 'ROOT', tmp_path)
    compiler = Mock(side_effect=AssertionError('Compiler must not run'))
    monkeypatch.setattr(build_installer, 'run', compiler)
    with pytest.raises(ValueError, match='缺少 Google 登录配置'):
        build_installer.main(['--iscc', str(tmp_path / 'iscc.exe')])
    compiler.assert_not_called()
    assert not (tmp_path / 'build').exists()


@pytest.mark.parametrize('name,data', [
    ('session.dpapi', b'encrypted-user-cache'),
    ('tokens.json', b'{"refresh_token":"do-not-ship"}'),
    ('service.json', b'{"type":"service_account","private_key":"do-not-ship"}'),
    ('private.pem', b'-----BEGIN PRIVATE KEY-----\nnever-ship'),
    ('.env', b'GOOGLE_TOKEN=never-ship'),
])
def test_real_payload_audit_accepts_only_public_resource_and_rejects_user_material(tmp_path, name, data):
    public(config.resource_path(tmp_path / '_internal'))
    assert audit_payload(tmp_path)['ok']
    (tmp_path / name).write_bytes(data)
    with pytest.raises(ValueError):
        audit_payload(tmp_path)


def test_spec_and_installed_self_test_require_the_public_resource():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'packaging/Aplus.spec').read_text(encoding='utf-8')
    assert 'read_client(resource_path(root))' in spec and "'resources'" in spec
    assert config.read_client(config.resource_path())
    assert 'google_oauth_config=True' in (root / 'tools/installed_entry.py').read_text(encoding='utf-8')
