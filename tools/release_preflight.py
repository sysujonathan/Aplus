"""Read-only release admission: trusted main push acceptance, one release per head."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.installed_update import REPO, load_manifest
from workbench.version_update import API, acceptance


def release_api(path):
    headers={'Accept':'application/vnd.github+json','User-Agent':'Aplus-release'}
    token=os.environ.get('GH_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    with urlopen(Request(API+path,headers=headers),timeout=15) as response:
        return json.load(response)


def preflight(event_name,event,ref,repository,head,*,api=release_api,opener=urlopen):
    if not re.fullmatch('[0-9a-f]{40}',head):raise ValueError('Build commit must be a full SHA')
    if repository!=REPO or ref!='refs/heads/main':
        raise ValueError('Only the official main workflow may release')
    if event_name=='workflow_run':
        run=event.get('workflow_run') or {}
        if not (run.get('event')=='push' and run.get('head_branch')=='main'
                and run.get('head_sha')==head and run.get('conclusion')=='success'
                and run.get('name')=='A acceptance' and run.get('path')=='.github/workflows/checks.yml'
                and (run.get('head_repository') or {}).get('full_name')==REPO):
            raise ValueError('Upstream event is not this exact official main push acceptance')
    elif event_name!='workflow_dispatch':
        raise ValueError('Release event is not supported')
    if api('/commits/main')['sha']!=head or not acceptance(head,api):
        raise ValueError('Exact current main has not passed its latest push acceptance')
    try:
        api('/releases/latest')
    except HTTPError as exc:
        if exc.code!=404:raise
        latest=None
    else:
        latest=load_manifest(api,opener,select_payload=False)
    return dict(build=not latest or latest['commit']!=head,commit=head)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    result=preflight(os.environ['GITHUB_EVENT_NAME'],event,os.environ['GITHUB_REF'],
                     os.environ['GITHUB_REPOSITORY'],head)
    with args.output.open('a',encoding='utf-8',newline='\n') as stream:
        stream.write('build='+str(result['build']).lower()+'\ncommit='+head+'\n')
    print('Accepted main needs a release' if result['build'] else 'This exact main is already released; no duplicate build')


if __name__=='__main__':main()
