"""One-time bridge for old Git installations. Standard library only.

Only accepted official main is executed. This file never writes business data.
The existing maintenance worker owns backups, installation, recovery and restart.
"""
import argparse
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

REPOSITORY = 'sysujonathan/Aplus'
API = 'https://api.github.com/repos/' + REPOSITORY


def git(root, *args, binary=False):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=180)
    if p.returncode:
        raise RuntimeError('无法核对官方代码或网络连接；原安装未被覆盖。')
    return p.stdout if binary else p.stdout.decode('utf-8').strip()


def github(path):
    request = Request(API + path, headers={'Accept': 'application/vnd.github+json',
                                         'User-Agent': 'Aplus-safe-transition'})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def accepted_target(api=github):
    sha = api('/branches/main')['commit']['sha']
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise RuntimeError('正式版提交身份无效，已停止。')
    runs = api('/actions/runs?branch=main&event=push&per_page=100')['workflow_runs']
    matching = [r for r in runs if r.get('head_sha') == sha and r.get('head_branch') == 'main'
                and r.get('event') == 'push' and r.get('name') == 'A acceptance'
                and r.get('path') == '.github/workflows/checks.yml']
    latest = max(matching, key=lambda r: (r['id'], r.get('run_attempt', 1)), default={})
    if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
        raise RuntimeError('正式版尚未通过验收，请等发布完成后再运行；旧版可继续使用。')
    return sha


def unpack(data, destination):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                    or ':' in entry.filename or not path.parts
                    or path.parts[0].lower() in {'.git', 'runtime', 'data', '.venv'}
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeError('正式版归档路径未通过保护检查。')
        archive.extractall(destination)


def original_runtime(root, source):
    # Import definitions only, never construct Store. Its existing resolver is
    # authoritative, with the legacy fallback bound to the OLD installation.
    sys.path.insert(0, str(source))
    try:
        from workbench.store import resolve_runtime_root
        configured = os.environ.get('A_WORKBENCH_HOME')
        preferred = resolve_runtime_root()
        legacy = Path(root)/'runtime'
        if not configured and not (preferred/'workbench.sqlite3').is_file() and (legacy/'workbench.sqlite3').is_file():
            preferred = legacy.resolve()
        if not (preferred/'workbench.sqlite3').is_file():
            raise RuntimeError('找不到原数据仓，已停止；不会创建新账户或新数据库。')
        return preferred
    finally:
        sys.path.pop(0)


def transition(root, api=github):
    root = Path(root).resolve()
    if not (root/'.git').is_dir():
        raise RuntimeError('请选择原来的 Aplus 安装目录。')
    remote = git(root, 'remote', 'get-url', 'origin')
    if remote not in {'https://github.com/sysujonathan/Aplus.git',
                      'https://github.com/sysujonathan/Aplus', 'git@github.com:sysujonathan/Aplus.git'}:
        raise RuntimeError('原安装不是约定的官方仓库，已停止。')
    target = accepted_target(api)
    git(root, 'fetch', 'origin', 'refs/heads/main:refs/aplus-update/bootstrap')
    if git(root, 'rev-parse', 'refs/aplus-update/bootstrap') != target:
        raise RuntimeError('正式版在准备期间发生变化，请稍后重新运行。')
    with tempfile.TemporaryDirectory(prefix='aplus-transition-') as temporary:
        source = Path(temporary)
        unpack(git(root, 'archive', '--format=zip', target, binary=True), source)
        if not (source/'tools'/'legacy_update_bootstrap.py').is_file():
            raise RuntimeError('安全过渡版本尚未发布到 main，请等维护者完成发布。')
        runtime = original_runtime(root, source)
        # Do not use the old environment's updater. Execute official target
        # source with the old interpreter; the new worker verifies its packages.
        result = subprocess.run([sys.executable, '-I', str(source/'tools'/'maintenance_update.py'),
                                 '--root', str(root), '--runtime', str(runtime),
                                 '--python', sys.executable, '--expected-target', target], cwd=source,
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or '安全更新准备未完成，旧安装和数据已保留。')
        return json.loads(result.stdout)['prepared']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    from tkinter import Tk, messagebox
    window = Tk(); window.withdraw()
    try:
        transition(args.root)
        messagebox.showinfo('Aplus 安全过渡', '已交给安全更新助手。更新完成后会自动打开软件；请勿重复启动。')
    except Exception as exc:
        messagebox.showerror('Aplus 安全过渡未完成', str(exc))
        return 1
    finally:
        window.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
