"""Explicit stable publication, only signed artifacts from exact accepted main.

No release is published by building a local preview. Run after code signing has
been configured; gh uses the release maintainer's existing authorization.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('setup',type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--signtool',required=True,type=Path)
    args=parser.parse_args()
    from workbench.installed_update import validate_manifest
    from workbench.version_update import github_json, acceptance
    value=validate_manifest(json.loads(args.manifest.read_text(encoding='utf-8')))
    if github_json('/commits/main')['sha']!=value['commit'] or not acceptance(value['commit']):
        raise SystemExit('Only exact accepted current main may become latest')
    package=value['package']
    if args.setup.name!=package['url'].rsplit('/',1)[-1]:
        raise SystemExit('Installer name does not match release')
    if args.setup.stat().st_size!=package['bytes'] or hashlib.sha256(args.setup.read_bytes()).hexdigest()!=package['sha256']:
        raise SystemExit('Installer bytes do not match the signed release manifest')
    subprocess.run([str(args.signtool),'verify','/pa','/all',str(args.setup.resolve())],check=True)
    tag='v'+value['version']
    # gh refuses duplicate tags/releases. Never rewrite a published package.
    subprocess.run(['gh','release','create',tag,str(args.setup.resolve()),str(args.manifest.resolve()),
                    '--repo','sysujonathan/Aplus','--target',value['commit'],'--draft',
                    '--title','Aplus '+value['version'],'--notes','Aplus Windows 安装版。软件内可一键更新。'],check=True)
    # Recheck at the actual publication point; an older slow build must never
    # overwrite latest after main has moved.
    if github_json('/commits/main')['sha']!=value['commit'] or not acceptance(value['commit']):
        raise SystemExit('Main changed; draft retained and not published')
    subprocess.run(['gh','release','edit',tag,'--repo','sysujonathan/Aplus','--draft=false','--latest'],check=True)


if __name__=='__main__':main()
