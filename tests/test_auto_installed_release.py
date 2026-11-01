"""Exercise release admission, real publisher decisions and the workflow wiring."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest
import yaml

from tools import release_preflight as gate, publish_installer as publisher
from workbench import installed_update as update, version_update
from tests.test_installed_distribution import manifest

ROOT=Path(__file__).resolve().parents[1]
HEAD='c'*40


def upstream():
    return dict(workflow_run=dict(event='push',head_branch='main',head_sha=HEAD,conclusion='success',
        name='A acceptance',path='.github/workflows/checks.yml',head_repository=dict(full_name=gate.REPO)))


def authority(latest=None):
    def api(path):
        if path=='/commits/main':return dict(sha=HEAD)
        if path=='/releases/latest':
            if latest is None:raise HTTPError('https://api.github.com',404,'Not Found',{},None)
            tag='v'+latest['version']
            return dict(draft=False,prerelease=False,tag_name=tag,assets=[
                dict(name='latest.json',size=500,browser_download_url=update.PACKAGE_BASE+tag+'/latest.json'),
                dict(name=Path(latest['package']['url']).name,size=latest['package']['bytes'],
                     browser_download_url=latest['package']['url'])])
        if path.startswith('/actions/runs?'):
            sha=path.split('head_sha=',1)[1].split('&',1)[0]
            return dict(workflow_runs=[dict(id=2,run_attempt=1,status='completed',conclusion='success',
                name='A acceptance',path='.github/workflows/checks.yml',event='push',head_branch='main',head_sha=sha)])
        if path.startswith('/commits/v'):return dict(sha=latest['commit'])
        if path.startswith('/compare/'):return dict(status='ahead')
        raise AssertionError(path)
    return api,lambda *a,**k:io.BytesIO(json.dumps(latest).encode())


@pytest.mark.parametrize('latest_commit',[None,'a'*40,HEAD])
def test_exact_accepted_main_builds_once(latest_commit):
    latest=manifest() if latest_commit else None
    if latest:latest['commit']=latest_commit
    api,opener=authority(latest)
    result=gate.preflight('workflow_run',upstream(),'refs/heads/main',gate.REPO,HEAD,api=api,opener=opener)
    assert result==dict(commit=HEAD,build=latest_commit!=HEAD)


@pytest.mark.parametrize('field,value',[('event','pull_request'),('head_branch','feature'),('head_sha','a'*40),
    ('conclusion','failure'),('name','Other workflow'),('path','.github/workflows/other.yml'),
    ('head_repository',dict(full_name='attacker/Aplus'))])
def test_untrusted_upstream_never_reaches_release_apis(field,value):
    event=upstream();event['workflow_run'][field]=value;api=Mock()
    with pytest.raises(ValueError):gate.preflight('workflow_run',event,'refs/heads/main',gate.REPO,HEAD,api=api)
    api.assert_not_called()


@pytest.mark.parametrize('event,ref,repo',[('push','refs/heads/main',gate.REPO),
    ('workflow_dispatch','refs/heads/feature',gate.REPO),('workflow_run','refs/heads/main','fork/Aplus')])
def test_other_events_branches_and_repositories_are_rejected(event,ref,repo):
    with pytest.raises(ValueError):gate.preflight(event,upstream(),ref,repo,HEAD,api=Mock())


@pytest.mark.parametrize('change',['main_moved','pr_check','new_attempt_running','new_attempt_failed','wrong_path'])
def test_latest_exact_main_push_acceptance_cannot_be_replaced_by_other_green(change):
    good,_=authority()
    def api(path):
        result=good(path)
        if path=='/commits/main' and change=='main_moved':return dict(sha='a'*40)
        if path.startswith('/actions/runs?'):
            run=result['workflow_runs'][0]
            if change=='pr_check':run['event']='pull_request'
            if change=='wrong_path':run['path']='other.yml'
            if change.startswith('new_attempt'):
                newer=dict(run,run_attempt=2,status='in_progress' if change.endswith('running') else 'completed',conclusion='failure')
                result['workflow_runs'].append(newer)
        return result
    with pytest.raises(ValueError):gate.preflight('workflow_run',upstream(),'refs/heads/main',gate.REPO,HEAD,api=api)


def test_dispatch_still_requires_current_main_acceptance_and_network_errors_fail_closed():
    api,opener=authority()
    assert gate.preflight('workflow_dispatch',{},'refs/heads/main',gate.REPO,HEAD,api=api,opener=opener)['build']
    def denied(path):
        if path=='/releases/latest':raise HTTPError('https://api.github.com',403,'Forbidden',{},None)
        return api(path)
    with pytest.raises(HTTPError):gate.preflight('workflow_dispatch',{},'refs/heads/main',gate.REPO,HEAD,api=denied)


def test_preflight_cli_writes_actual_gate_outputs(tmp_path,monkeypatch):
    api,opener=authority();real=gate.preflight
    monkeypatch.setattr(gate,'preflight',lambda *args:real(*args,api=api,opener=opener))
    monkeypatch.setattr(gate.subprocess,'check_output',lambda *a,**k:HEAD)
    event=tmp_path/'event.json';event.write_text(json.dumps(upstream()))
    for key,value in dict(GITHUB_EVENT_PATH=str(event),GITHUB_EVENT_NAME='workflow_run',
        GITHUB_REF='refs/heads/main',GITHUB_REPOSITORY=gate.REPO).items():monkeypatch.setenv(key,value)
    output=tmp_path/'outputs'
    monkeypatch.setattr(sys,'argv',['preflight','--output',str(output)])
    gate.main()
    assert output.read_text()=='build=true\ncommit='+HEAD+'\n'


def test_workflow_keeps_build_read_only_and_publishes_only_its_successful_artifact():
    workflow=yaml.load((ROOT/'.github/workflows/installed-release.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
    assert workflow['permissions']=={'contents':'read','actions':'read'}
    assert workflow['on']['workflow_run']==dict(workflows=['A acceptance'],branches=['main'],types=['completed'])
    assert workflow['concurrency']['cancel-in-progress']=='false'
    build=workflow['jobs']['build'];publish=workflow['jobs']['publish']
    assert 'permissions' not in build and publish['permissions']=={'contents':'write','actions':'read'}
    assert publish['needs']=='build' and "needs.build.outputs.ready == 'true'" in publish['if']
    assert 'refs/heads/main' in build['if'] and "workflow_run.event == 'push'" in build['if']
    steps=build['steps'];names=[step.get('name','') for step in steps]
    assert names.index('Build exact accepted main installer')<names.index('Installed GUI and WebView smoke')
    for step in steps:
        if step.get('uses','').startswith('actions/upload-artifact'):
            assert step['if']=="steps.gate.outputs.build == 'true'"
            assert step['with']['if-no-files-found']=='error'
    checkout=publish['steps'][0]
    assert checkout['with']=={'ref':'${{ needs.build.outputs.commit }}','persist-credentials':'false'}
    download=next(s for s in publish['steps'] if s.get('uses','').startswith('actions/download-artifact'))
    assert download['with']['artifact-ids']=='${{ needs.build.outputs.artifact }}'
    command=next(s['run'] for s in publish['steps'] if s.get('name')=='Publish verified stable release')
    assert '--expected-commit' in command and '--resume' in command and '--verify-online' in command
    assert 'continue-on-error' not in json.dumps(publish)


@pytest.mark.parametrize('event_name,run_event,ref,repo,head_repo,allowed',[
    ('workflow_run','push','refs/heads/main',gate.REPO,gate.REPO,True),
    ('workflow_run','pull_request','refs/heads/main',gate.REPO,gate.REPO,False),
    ('workflow_run','push','refs/heads/main',gate.REPO,'fork/Aplus',False),
    ('workflow_run','push','refs/heads/main','fork/Aplus','fork/Aplus',False),
    ('workflow_dispatch','push','refs/heads/feature',gate.REPO,gate.REPO,False),
    ('workflow_dispatch','push','refs/heads/main',gate.REPO,gate.REPO,True)])
def test_actual_workflow_admission_expression(event_name,run_event,ref,repo,head_repo,allowed):
    workflow=yaml.load((ROOT/'.github/workflows/installed-release.yml').read_text(),Loader=yaml.BaseLoader)
    expression=workflow['jobs']['build']['if'].replace('&&',' and ').replace('||',' or ')
    github=SimpleNamespace(repository=repo,ref=ref,event_name=event_name,event=SimpleNamespace(
        workflow_run=SimpleNamespace(event=run_event,conclusion='success',head_branch='main',
                                     head_repository=SimpleNamespace(full_name=head_repo))))
    assert bool(eval(expression,{'__builtins__':{}},{'github':github})) is allowed


@pytest.fixture
def release_world(tmp_path,monkeypatch):
    value=manifest();value['commit']=HEAD
    setup=tmp_path/Path(value['package']['url']).name;setup.write_bytes(b'installer bytes')
    metadata=tmp_path/'latest.json';metadata.write_text(json.dumps(value))
    state=SimpleNamespace(release=None,files={},latest=None,calls=[],fail_upload=False)
    def api(path):
        if path=='/releases/latest':
            if state.latest is None:raise HTTPError('https://api.github.com',404,'Not Found',{},None)
            return {}
        raise AssertionError(path)
    monkeypatch.setattr(gate,'release_api',api)
    monkeypatch.setattr(update,'load_manifest',lambda **kwargs:copy.deepcopy(state.latest))
    monkeypatch.setattr(version_update,'github_json',lambda path:dict(sha=HEAD))
    monkeypatch.setattr(version_update,'acceptance',lambda sha:True)
    monkeypatch.setattr(publisher.time,'sleep',Mock())
    def assets():
        state.release['assets']=[dict(name=name,size=len(raw)) for name,raw in state.files.items()]
    def run(command,**kwargs):
        state.calls.append(command)
        if command[:2]==['gh','api']:
            if '/git/ref/tags/' in command[2]:
                data=dict(ref='refs/tags/v'+value['version'],object=dict(type='commit',sha=HEAD)) if state.release and not state.release['draft'] else None
            elif '/commits/' in command[2]:
                if not state.release or state.release['draft']:
                    return subprocess.CompletedProcess(command,1,stdout='',
                        stderr='gh: No commit found for SHA: v'+value['version']+' (HTTP 422)')
                data=dict(sha=HEAD)
            elif '/releases/tags/' in command[2]:
                # GitHub's tag endpoint does not expose unpublished drafts.
                data=state.release if state.release and not state.release['draft'] else None
            elif '/releases?' in command[2]:data=[dict(state.release,id=123)] if state.release else []
            else:data=dict(state.release,id=123) if state.release else None
            if data is None:return subprocess.CompletedProcess(command,1,stdout='',stderr='gh: Not Found (HTTP 404)')
            return subprocess.CompletedProcess(command,0,stdout=json.dumps(data),stderr='')
        action=command[2]
        if action=='create':
            state.release=dict(tag_name=command[3],target_commitish=HEAD,draft=True,prerelease=False)
            state.files[setup.name]=setup.read_bytes();assets()
            if state.fail_upload:
                state.fail_upload=False
                raise subprocess.CalledProcessError(1,command)
            state.files[metadata.name]=metadata.read_bytes();assets()
        elif action=='upload':
            assert '--clobber' not in command
            for name in command[4:command.index('--repo')]:
                path=Path(name);assert path.name not in state.files
                state.files[path.name]=path.read_bytes()
            assets()
        elif action=='download':
            folder=Path(command[command.index('--dir')+1])
            for name,raw in state.files.items():(folder/name).write_bytes(raw)
        elif action=='edit':state.release['draft']=False;state.latest=copy.deepcopy(value)
        else:raise AssertionError(command)
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(publisher.subprocess,'run',run)
    monkeypatch.setattr(sys,'argv',['publish',str(setup),'--manifest',str(metadata),
        '--expected-commit',HEAD,'--resume','--verify-online','--receipt',str(tmp_path/'published.json')])
    return value,setup,metadata,state,assets


def test_auto_publication_verifies_uploaded_bytes_then_public_update_discovery(release_world):
    value,setup,metadata,state,_=release_world
    publisher.main()
    assert state.latest==value and state.release['draft'] is False
    assert json.loads((setup.parent/'published.json').read_text())['online_verified'] is True
    assert [call[2] for call in state.calls if call[:2]==['gh','release']]==['create','download','edit']


def test_partial_upload_resumes_without_overwrite_and_published_retry_is_idempotent(release_world):
    value,setup,metadata,state,_=release_world;state.fail_upload=True
    with pytest.raises(subprocess.CalledProcessError):publisher.main()
    assert state.release['draft'] is True and 'latest.json' not in state.files
    publisher.main()
    assert state.release['draft'] is False and state.latest==value
    edits=sum(call[:3]==['gh','release','edit'] for call in state.calls)
    publisher.main()
    assert sum(call[:3]==['gh','release','create'] for call in state.calls)==1
    assert sum(call[:3]==['gh','release','edit'] for call in state.calls)==edits==1


@pytest.mark.parametrize('damage',['commit','prerelease','bytes','manifest','missing_public_asset'])
def test_existing_different_release_or_corrupt_upload_never_published(release_world,damage):
    value,setup,metadata,state,assets=release_world
    state.release=dict(tag_name='v'+value['version'],target_commitish=HEAD,draft=True,prerelease=False)
    state.files={setup.name:setup.read_bytes(),metadata.name:metadata.read_bytes()}
    if damage=='commit':state.release['target_commitish']='d'*40
    elif damage=='prerelease':state.release['prerelease']=True
    elif damage=='bytes':state.files[setup.name]=b'Installer bytes'  # same size, wrong hash
    elif damage=='manifest':state.files[metadata.name]=b'{}'
    else:state.release['draft']=False;del state.files[metadata.name]
    assets()
    with pytest.raises(ValueError):publisher.main()
    assert not any(call[:3]==['gh','release','edit'] for call in state.calls)


def test_workflow_artifact_commit_mismatch_stops_before_github_mutation(release_world,monkeypatch):
    value,setup,metadata,state,_=release_world
    value['commit']='d'*40;metadata.write_text(json.dumps(value))
    with pytest.raises(SystemExit,match='artifact'):publisher.main()
    assert not state.calls


def test_newer_stable_cannot_be_replaced_even_by_same_main(release_world):
    value,setup,metadata,state,_=release_world
    state.latest=dict(value,sequence=value['sequence']+1)
    with pytest.raises(ValueError,match='newer'):publisher.main()
    assert not any(call[:3]==['gh','release','create'] for call in state.calls)


@pytest.mark.parametrize('failure',['main_moved','ci_failed'])
def test_upload_cannot_be_promoted_after_main_or_latest_acceptance_changes(release_world,monkeypatch,failure):
    _,_,_,state,_=release_world
    checks=[]
    def api(path):
        checks.append(path)
        return dict(sha='d'*40 if failure=='main_moved' and len(checks)>1 else HEAD)
    monkeypatch.setattr(version_update,'github_json',api)
    monkeypatch.setattr(version_update,'acceptance',lambda sha:failure!='ci_failed' or len(checks)==1)
    with pytest.raises(SystemExit,match='Main changed'):publisher.main()
    assert state.release['draft'] is True and state.latest is None
    assert not any(call[:3]==['gh','release','edit'] for call in state.calls)


def test_foreign_existing_tag_cannot_be_reused(release_world,monkeypatch):
    _,_,_,state,_=release_world
    original=publisher.subprocess.run
    def run(command,**kwargs):
        if command[:2]==['gh','api'] and '/git/ref/tags/' in command[2]:
            return subprocess.CompletedProcess(command,0,stdout=json.dumps(dict(object=dict(type='commit',sha='d'*40))))
        if command[:2]==['gh','api'] and '/commits/' in command[2]:
            return subprocess.CompletedProcess(command,0,stdout=json.dumps(dict(sha='d'*40)))
        return original(command,**kwargs)
    monkeypatch.setattr(publisher.subprocess,'run',run)
    with pytest.raises(ValueError,match='tag belongs'):publisher.main()
    assert state.release is None


def test_partial_draft_with_corrupt_existing_asset_is_not_repaired_or_published(release_world):
    value,setup,_,state,assets=release_world
    state.release=dict(tag_name='v'+value['version'],target_commitish=HEAD,draft=True,prerelease=False)
    state.files={setup.name:b'Installer bytes'};assets()
    with pytest.raises(ValueError):publisher.main()
    assert not any(call[:3] in (['gh','release','upload'],['gh','release','edit']) for call in state.calls)


def test_publication_confirmation_failure_can_retry_without_republishing(release_world,monkeypatch):
    value,setup,_,state,_=release_world;visible=[False]
    monkeypatch.setattr(update,'load_manifest',lambda **kw:copy.deepcopy(state.latest if visible[0] else
        dict(value,sequence=value['sequence']-1)))
    with pytest.raises(ValueError,match='online update discovery'):publisher.main()
    assert state.release['draft'] is False and not (setup.parent/'published.json').exists()
    visible[0]=True;publisher.main()
    assert json.loads((setup.parent/'published.json').read_text())['online_verified']
    assert sum(call[:3]==['gh','release','edit'] for call in state.calls)==1


def test_new_tag_uses_exact_reference_lookup_without_resolving_missing_commit(release_world):
    value,_,_,state,_=release_world
    publisher.main()
    paths=[call[2] for call in state.calls if call[:2]==['gh','api']]
    assert 'repos/'+gate.REPO+'/git/ref/tags/v'+value['version'] in paths
    assert not any('/commits/' in path for path in paths)
    assert state.release['draft'] is False


@pytest.mark.parametrize('kind',['commit','tag'])
@pytest.mark.parametrize('target',[HEAD,'d'*40])
def test_existing_tag_resolves_commit_even_for_annotated_tag_objects(release_world,monkeypatch,kind,target):
    _,_,_,state,_=release_world;original=publisher.subprocess.run;lookups=[]
    def run(command,**kwargs):
        if command[:2]==['gh','api']:
            lookups.append(command[2])
            if '/git/ref/tags/' in command[2]:
                return subprocess.CompletedProcess(command,0,stdout=json.dumps(dict(
                    object=dict(type=kind,sha=target if kind=='commit' else 'e'*40))))
            if '/commits/' in command[2]:
                return subprocess.CompletedProcess(command,0,stdout=json.dumps(dict(sha=target)))
        return original(command,**kwargs)
    monkeypatch.setattr(publisher.subprocess,'run',run)
    if target==HEAD:
        publisher.main()
        assert state.release['draft'] is False
    else:
        with pytest.raises(ValueError,match='different commit'):publisher.main()
        assert state.release is None
    assert any('/commits/' in path for path in lookups)


@pytest.mark.parametrize('stage,status',[
    ('reference',403),('reference',422),('reference',429),('reference',500),
    ('commit',403),('commit',404),('commit',422),('commit',500)])
def test_tag_lookup_other_errors_fail_closed_before_release_writes(release_world,monkeypatch,stage,status):
    _,_,_,state,_=release_world;original=publisher.subprocess.run
    def run(command,**kwargs):
        if command[:2]==['gh','api']:
            if '/git/ref/tags/' in command[2]:
                if stage=='reference':
                    return subprocess.CompletedProcess(command,1,stdout='',stderr=f'gh: lookup failed (HTTP {status})')
                return subprocess.CompletedProcess(command,0,stdout=json.dumps(dict(object=dict(type='tag',sha='e'*40))))
            if '/commits/' in command[2]:
                return subprocess.CompletedProcess(command,1,stdout='',stderr=f'gh: lookup failed (HTTP {status})')
        return original(command,**kwargs)
    monkeypatch.setattr(publisher.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='lookup failed'):publisher.main()
    assert state.release is None and not any(call[:2]==['gh','release'] for call in state.calls)


def test_gh_json_decodes_real_utf8_output_independent_of_windows_codepage(monkeypatch):
    value=dict(sha=HEAD,message='主线验收与策略更新 ✓')
    raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
    original=subprocess.run
    monkeypatch.setattr(subprocess,'_text_encoding',lambda:'cp936')
    def run(command,**kwargs):
        return original([sys.executable,'-c',
            'import sys; sys.stdout.buffer.write(bytes.fromhex('+repr(raw.hex())+'))'],**kwargs)
    monkeypatch.setattr(publisher.subprocess,'run',run)
    assert publisher.gh_json('/commits/v2026.10.31.2')==value


def test_draft_lookup_pages_then_reads_exact_id_without_using_placeholder_tag(monkeypatch):
    tag='v2026.11.01.1';calls=[]
    draft=dict(id=123,tag_name=tag,draft=True,target_commitish=HEAD)
    def api(path,**kwargs):
        calls.append(path)
        if path=='/releases/tags/'+tag:return None
        if path=='/releases?per_page=100&page=1':return [dict(id=i+1,tag_name='other-'+str(i)) for i in range(100)]
        if path=='/releases?per_page=100&page=2':return [draft]
        if path=='/releases/123':return draft
        raise AssertionError(path)
    monkeypatch.setattr(publisher,'gh_json',api)
    assert publisher.release_for_tag(tag,required=True)==draft
    assert calls[-1]=='/releases/123' and not any('untagged-' in path for path in calls)


@pytest.mark.parametrize('problem',['ambiguous','bad_id','changed_id','changed_tag','invalid_listing','denied','missing'])
def test_draft_lookup_unknown_or_ambiguous_identity_fails_closed(monkeypatch,problem):
    tag='v2026.11.01.1';draft=dict(id=123,tag_name=tag,draft=True)
    def api(path,**kwargs):
        if path.startswith('/releases/tags/'):return None
        if path.startswith('/releases?'):
            if problem=='denied':raise RuntimeError('GitHub lookup failed (HTTP 403)')
            if problem=='invalid_listing':return {}
            if problem=='missing':return []
            if problem=='bad_id':return [dict(draft,id=True)]
            return [draft,dict(draft,id=124)] if problem=='ambiguous' else [draft]
        if path=='/releases/123':
            return dict(draft,id=124) if problem=='changed_id' else dict(draft,tag_name='another-tag')
        raise AssertionError(path)
    monkeypatch.setattr(publisher,'gh_json',api)
    with pytest.raises((ValueError,RuntimeError)):publisher.release_for_tag(tag,required=True)
