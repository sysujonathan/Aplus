"""Prepare only the public Desktop client for an admitted release build."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workbench.pa_oauth_config import read_client, resource_path


def prepare(raw, root=None):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('正式构建缺少 APLUS_GOOGLE_DESKTOP_OAUTH_JSON，已停止生成安装包')
    data = raw.encode('utf-8')
    if len(data) > 16 * 1024:
        raise ValueError('Google Desktop 构建配置超过资源上限')
    target = resource_path(ROOT if root is None else root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        config = read_client(temporary, desktop_export=True)
        temporary.write_text(json.dumps({'installed': config}, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        read_client(temporary)  # The final resource is canonical, without export metadata.
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main():
    # Never echo configuration or leave it in the compiler's environment.
    prepare(os.environ.pop('APLUS_GOOGLE_DESKTOP_OAUTH_JSON', None))
    print('Google Desktop public resource validated; no user credentials included')


if __name__ == '__main__':
    main()
