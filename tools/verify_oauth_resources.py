"""Build-time audit of the real payload, not of a developer's login profile."""
import json
from pathlib import Path

from workbench.pa_oauth_config import read_client, resource_path

PRIVATE_HEADERS = (b'-----BEGIN PRIVATE KEY-----', b'-----BEGIN RSA PRIVATE KEY-----',
                   b'-----BEGIN EC PRIVATE KEY-----', b'-----BEGIN OPENSSH PRIVATE KEY-----',
                   b'-----BEGIN ENCRYPTED PRIVATE KEY-----')
USER_FIELDS = {'access_token', 'refresh_token', 'id_token', 'private_key', 'private_key_id', 'client_email', 'client_secret'}


def _private_json(value):
    if isinstance(value, dict):
        return (any(key in USER_FIELDS and item for key, item in value.items())
                or value.get('type') == 'service_account'
                or any(_private_json(item) for item in value.values()))
    return isinstance(value, list) and any(_private_json(item) for item in value)


def audit_payload(root, expected=None):
    root = Path(root)
    config = read_client(resource_path(root / '_internal'))
    if expected is not None and config != expected:
        raise ValueError('安装包 Google 桌面公共客户端与构建资源不一致')
    count = 0
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        count += 1
        if path.suffix.lower() in ('.dpapi', '.palearn', '.sqlite3'):
            raise ValueError('安装包含用户运行记录或登录缓存，构建已停止')
        if path.name == '.env' or path.name.startswith('.env.'):
            raise ValueError('安装包含本机环境配置，构建已停止')
        # Inspect data/text resources only; binary dependencies are not secret
        # stores. Never log matched credential contents or a user's filenames.
        if path.suffix.lower() not in ('.json', '.pem', '.key', '.env', '.txt', '.yaml', '.yml'):
            continue
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('安装资源超过凭证审计上限，请检查构建输入')
        data = path.read_bytes()
        if any(header in data for header in PRIVATE_HEADERS):
            raise ValueError('安装包含私钥，构建已停止')
        if path.suffix.lower() == '.json':
            try:
                value = json.loads(data.decode('utf-8-sig'))
            except (UnicodeError, ValueError):
                raise ValueError('安装包 JSON 资源无法完成凭证审计，构建已停止') from None
            if path != resource_path(root / '_internal') and _private_json(value):
                raise ValueError('安装包含用户令牌或服务账号资料，构建已停止')
    return dict(ok=True, public_desktop_client=True, user_tokens=False, private_keys=False, files_checked=count)
