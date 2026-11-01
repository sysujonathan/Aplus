"""Explicit stable publication from exact accepted main and verified bytes.

No release is published by building a local preview. Windows signing is optional;
gh uses the release maintainer's authorization.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def gh_json(path, *, missing_ok=False):
    result=subprocess.run(['gh','api','repos/sysujonathan/Aplus'+path],capture_output=True,text=True,encoding='utf-8')
    if result.returncode:
        if missing_ok and '(HTTP 404)' in result.stderr:return None
        raise RuntimeError('GitHub release lookup failed: '+result.stderr.strip())
    return json.loads(result.stdout)


def release_for_tag(tag, *, required=False):
    """The by-tag endpoint exposes published releases, not new drafts."""
    value=gh_json('/releases/tags/'+tag,missing_ok=True)
    if value is not None:return value
    matches=[]
    for page in range(1,101):
        values=gh_json('/releases?per_page=100&page='+str(page))
        if not isinstance(values,list):raise ValueError('Release listing is invalid')
        matches.extend(item for item in values if isinstance(item,dict) and item.get('tag_name')==tag)
        if len(values)<100:break
    else:raise ValueError('Release listing exceeds safe lookup bounds')
    if len(matches)>1:raise ValueError('Multiple drafts claim the same release tag; retained for review')
    if not matches:
        if required:raise ValueError('Uploaded release is not visible; draft retained')
        return None
    identifier=matches[0].get('id')
    if type(identifier) is not int or identifier<1:raise ValueError('Release ID is invalid')
    value=gh_json('/releases/'+str(identifier))
    if value.get('id')!=identifier or value.get('tag_name')!=tag:
        raise ValueError('Release identity changed during lookup')
    return value


def verify_uploaded(tag,value,release,*,allow_missing=False):
    """Draft retries may reuse only exactly the same target and uploaded bytes."""
    if (release.get('tag_name')!=tag or release.get('target_commitish')!=value['commit']
            or release.get('prerelease') is not False or type(release.get('draft')) is not bool):
        raise ValueError('Existing release does not belong to this exact official package')
    names={Path(value['package']['url']).name:value['package']['bytes'],'latest.json':None}
    present=[]
    for name,size in names.items():
        matches=[item for item in release.get('assets',[]) if item.get('name')==name]
        if not matches and allow_missing:continue
        if len(matches)!=1 or (size is not None and matches[0].get('size')!=size):
            raise ValueError('Existing release assets are incomplete or have different sizes')
        present.append(name)
    if not present:return
    with tempfile.TemporaryDirectory(prefix='aplus-release-verify-') as temp:
        patterns=[arg for name in present for arg in ('--pattern',name)]
        subprocess.run(['gh','release','download',tag,'--repo','sysujonathan/Aplus',*patterns,'--dir',temp],check=True)
        root=Path(temp)
        if 'latest.json' in present and json.loads((root/'latest.json').read_text(encoding='utf-8'))!=value:
            raise ValueError('Uploaded release manifest differs; never overwrite it')
        from workbench.installed_update import verify_package
        if Path(value['package']['url']).name in present:
            verify_package(value,root/Path(value['package']['url']).name)


def ensure_forward_release(value):
    from tools.release_preflight import release_api
    from workbench.installed_update import load_manifest
    try:release_api('/releases/latest')
    except HTTPError as exc:
        if exc.code==404:return
        raise
    latest=load_manifest(api=release_api,select_payload=False)
    if latest['sequence']>value['sequence'] or (latest['sequence']==value['sequence'] and latest!=value):
        raise ValueError('Refusing to replace a newer or different stable release')


def verify_online(value):
    from tools.release_preflight import release_api
    from workbench.installed_update import load_manifest
    for delay in (0,2,4):
        if delay:time.sleep(delay)
        try:
            if load_manifest(api=release_api,select_payload=False)==value:return
        except (OSError,ValueError):pass
    raise ValueError('Release was published, but online update discovery is not yet confirmed; safely rerun publication')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('setup',type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--signtool',type=Path,help='Optional additional Windows signature verification')
    parser.add_argument('--expected-commit',help='Bind workflow artifacts to their exact build job commit')
    parser.add_argument('--resume',action='store_true',help='Verify and resume an identical draft, never overwrite assets')
    parser.add_argument('--verify-online',action='store_true')
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    from workbench.installed_update import validate_manifest
    from workbench.version_update import github_json, acceptance
    value=validate_manifest(json.loads(args.manifest.read_text(encoding='utf-8')))
    if args.expected_commit and value['commit']!=args.expected_commit:
        raise SystemExit('Downloaded artifact does not match its build job commit')
    if github_json('/commits/main')['sha']!=value['commit'] or not acceptance(value['commit']):
        raise SystemExit('Only exact accepted current main may become latest')
    package=value['package']
    if args.setup.name!=package['url'].rsplit('/',1)[-1]:
        raise SystemExit('Installer name does not match release')
    if args.setup.stat().st_size!=package['bytes'] or hashlib.sha256(args.setup.read_bytes()).hexdigest()!=package['sha256']:
        raise SystemExit('Installer bytes do not match the official release manifest')
    if args.signtool:
        subprocess.run([str(args.signtool),'verify','/pa','/all',str(args.setup.resolve())],check=True)
    tag='v'+value['version']
    existing=None
    if args.resume:
        ensure_forward_release(value)
        # A missing commit-ish returns 422, not "tag absent". The exact Git
        # reference endpoint returns 404; resolve existing annotated tags too.
        ref=gh_json('/git/ref/tags/'+tag,missing_ok=True)
        if ref is not None and gh_json('/commits/'+tag).get('sha')!=value['commit']:
            raise ValueError('Existing tag belongs to a different commit')
        existing=release_for_tag(tag)
    # gh refuses duplicate tags/releases. Never rewrite a published package.
    if existing is None:
        subprocess.run(['gh','release','create',tag,str(args.setup.resolve()),str(args.manifest.resolve()),
                        '--repo','sysujonathan/Aplus','--target',value['commit'],'--draft',
                        '--title','Aplus '+value['version'],'--notes','Aplus Windows 安装版。软件内可一键更新。'],check=True)
    if args.resume:
        existing=release_for_tag(tag,required=True)
        if existing.get('draft') is True:
            uploaded={item.get('name') for item in existing.get('assets',[])}
            missing=[path for path in (args.setup,args.manifest) if path.name not in uploaded]
            if missing:
                verify_uploaded(tag,value,existing,allow_missing=True)
                subprocess.run(['gh','release','upload',tag,*map(lambda p:str(p.resolve()),missing),
                                '--repo','sysujonathan/Aplus'],check=True)
                existing=release_for_tag(tag,required=True)
        verify_uploaded(tag,value,existing)
    # Recheck at the actual publication point; an older slow build must never
    # overwrite latest after main has moved.
    if github_json('/commits/main')['sha']!=value['commit'] or not acceptance(value['commit']):
        raise SystemExit('Main changed; draft retained and not published')
    if args.resume:ensure_forward_release(value)
    if not existing or existing.get('draft') is True:
        subprocess.run(['gh','release','edit',tag,'--repo','sysujonathan/Aplus','--draft=false','--latest'],check=True)
    if args.verify_online:verify_online(value)
    receipt=dict(version=value['version'],commit=value['commit'],sha256=package['sha256'],
                 bytes=package['bytes'],url='https://github.com/sysujonathan/Aplus/releases/tag/'+tag,
                 online_verified=args.verify_online)
    if args.receipt:args.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
