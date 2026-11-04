"""Public Google Desktop client resource; never a user credential store."""
import json
import os
from pathlib import Path
import re
import sys

RESOURCE = Path('resources/google-desktop-oauth.json')
PUBLIC_FIELDS = {'client_id', 'client_secret', 'project_id'}
EXPORT_FIELDS = {'auth_uri', 'token_uri', 'auth_provider_x509_cert_url', 'redirect_uris'}


def resource_path(root=None):
    return (Path(root) if root is not None else Path(__file__).resolve().parents[1]) / RESOURCE


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Google 登录配置存在重复字段')
        result[key] = value
    return result


def read_client(path, *, desktop_export=False):
    try:
        path = Path(path)
        if path.stat().st_size > 16 * 1024:
            raise ValueError('Google 登录配置超过资源上限')
        value = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_unique)
        if not isinstance(value, dict) or set(value) != {'installed'}:
            raise ValueError('Google 登录配置必须为桌面公共客户端')
        config = value['installed']
        allowed = PUBLIC_FIELDS | (EXPORT_FIELDS if desktop_export else set())
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError('Google 登录配置含非公共客户端字段，拒绝加载')
        if (not isinstance(config.get('client_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}\.apps\.googleusercontent\.com', config['client_id'])
                or not isinstance(config.get('client_secret'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{8,180}', config['client_secret'])):
            raise ValueError('Google 桌面客户端配置无效')
        if 'project_id' in config and (not isinstance(config['project_id'], str)
                or not re.fullmatch(r'[a-z0-9-]{1,100}', config['project_id'])):
            raise ValueError('Google 客户端项目标识无效')
        # Google calls this public Desktop-client value client_secret. It is
        # shipped as protocol configuration, not treated as a confidential key.
        return {key: config[key] for key in PUBLIC_FIELDS if key in config}
    except FileNotFoundError:
        raise ValueError('安装资源缺少 Google 登录配置，请重新安装或联系维护者') from None
    except (json.JSONDecodeError, UnicodeError, OSError):
        raise ValueError('安装资源中的 Google 登录配置无法读取，请重新安装或联系维护者') from None


def load_client():
    # Installed builds never consult a developer path or display a file picker.
    override = None if getattr(sys, 'frozen', False) else os.environ.get('APLUS_GOOGLE_DESKTOP_CLIENT')
    return read_client(override or resource_path(), desktop_export=bool(override))
