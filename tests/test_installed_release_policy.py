"""Official-release trust replaces the certificate gate without bypassing CI."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from workbench import installed_update as update, release_identity as identity, releases
from tests.test_installed_distribution import manifest


def official(value=None,*,later_main=False):
    value=value or manifest();tag='v'+value['version']
    release=dict(draft=False,prerelease=False,tag_name=tag,assets=[
        dict(name='latest.json',size=500,browser_download_url=update.PACKAGE_BASE+tag+'/latest.json'),
        dict(name=Path(value['package']['url']).name,size=value['package']['bytes'],browser_download_url=value['package']['url'])])
    ci=dict(head_sha=value['commit'],head_branch='main',event='push',name='A acceptance',
            path='.github/workflows/checks.yml',id=1,status='completed',conclusion='success')
    calls=[]
    def api(path):
        calls.append(path)
        if path=='/releases/latest':return release
        if path=='/commits/'+tag:return dict(sha=value['commit'])
        if path=='/commits/main':return dict(sha='c'*40 if later_main else value['commit'])
        if path.startswith('/compare/'):return dict(status='ahead')
        return dict(workflow_runs=[ci])
    return release,ci,api,lambda *a,**k:io.BytesIO(json.dumps(value).encode()),calls


@pytest.mark.parametrize('later_main',[False,True])
def test_unsigned_official_release_accepted_even_when_main_advances(tmp_path,monkeypatch,later_main):
    value=manifest();release,ci,api,opener,calls=official(value,later_main=later_main)
    assert update.load_manifest(api=api,opener=opener)==value
    path=tmp_path/'unsigned.exe';path.write_bytes(b'installer bytes')
    monkeypatch.setattr(update,'load_manifest',lambda:value)
    old=dict(version='2026.10.30.1',sequence=202610300001,commit='b'*40,preview=True,database_schema=9)
    update.verify_candidate(value,path,old)
    assert '/commits/v2026.10.30.2' in calls


@pytest.mark.parametrize('mutate',[
    lambda r,c:r.update(draft=True),lambda r,c:r.update(prerelease=True),
    lambda r,c:r.update(tag_name='v2026.10.30.99'),
    lambda r,c:r['assets'].pop(),
    lambda r,c:c.update(event='pull_request'),lambda r,c:c.update(head_sha='b'*40),
    lambda r,c:c.update(head_branch='feature'),lambda r,c:c.update(name='Other workflow'),
    lambda r,c:c.update(path='.github/workflows/untrusted.yml'),
    lambda r,c:c.update(conclusion='failure'),lambda r,c:c.update(status='in_progress')])
def test_wrong_or_unfinished_authority_never_authorizes_release(mutate):
    release,ci,api,opener,_=official();mutate(release,ci)
    with pytest.raises(ValueError):update.load_manifest(api=api,opener=opener)


@pytest.mark.parametrize('case',['tag_sha','not_main','metadata_url','asset_size'])
def test_release_binding_is_checked(case):
    release,ci,api,opener,_=official(later_main=True)
    if case=='metadata_url':release['assets'][0]['browser_download_url']=update.PACKAGE_BASE+'other/latest.json'
    if case=='asset_size':release['assets'][1]['size']+=1
    def corrupt(path):
        if case=='tag_sha' and path.startswith('/commits/v'):return dict(sha='d'*40)
        if case=='not_main' and path.startswith('/compare/'):return dict(status='diverged')
        return api(path)
    with pytest.raises(ValueError):update.load_manifest(api=corrupt,opener=opener)


@pytest.mark.parametrize('change',['byte','truncate','extra','downgrade','changed_release'])
def test_helper_rechecks_bytes_order_and_current_official_release(tmp_path,monkeypatch,change):
    value=manifest();path=tmp_path/'setup.exe';path.write_bytes(b'installer bytes')
    old=dict(version='2026.10.30.1',sequence=202610300001,commit='b'*40,preview=True,database_schema=9)
    official_value=copy.deepcopy(value)
    if change=='byte':path.write_bytes(b'Installer bytes')
    elif change=='truncate':path.write_bytes(b'installer')
    elif change=='extra':path.write_bytes(b'installer bytes extra')
    elif change=='downgrade':old.update(version='2026.10.30.3',sequence=202610300003)
    else:official_value['commit']='c'*40
    monkeypatch.setattr(update,'load_manifest',lambda:official_value)
    with pytest.raises(ValueError):update.verify_candidate(value,path,old)


@pytest.mark.parametrize('version',['2026.13.01.1','2026.02.30.1','2026.10.30.0',
                                    '2026.10.30.65536','2026.10.30.01','2026.1.1.1'])
def test_bad_calendar_or_windows_version_rejected(version):
    with pytest.raises(ValueError):identity.version_parts(version)


def test_all_display_paths_prefer_actual_installed_identity(tmp_path):
    value=dict(version='2026.10.30.6',sequence=202610300006,commit='a'*40,preview=True)
    (tmp_path/'installed-release.json').write_text(json.dumps(value))
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'release_catalog.json').write_bytes((root/'release_catalog.json').read_bytes())
    visible=releases.identity(tmp_path,dict(head='b'*40,branch='main'))
    assert visible['version']==value['version'] and visible['head']==value['commit']
    assert '2026.10.17.53' not in releases.version_label(visible)


def test_new_source_commit_does_not_inherit_old_catalog_build_label():
    root=Path(__file__).resolve().parents[1]
    assert releases.identity(root,dict(head='a'*40,branch='main'))['version'] is None
    assert releases.identity(root,target='a'*40,git_reader=lambda *a:(root/'release_catalog.json').read_text())['version'] is None


def test_manual_check_messages_include_real_running_and_target_versions():
    value=manifest();_,_,api,opener,_=official(value)
    old=dict(version='2026.10.30.1',sequence=202610300001,commit='b'*40,preview=True,database_schema=9)
    text=update.check_update(old,api=api,opener=opener)['message']
    assert 'v2026.10.30.1' in text and 'v2026.10.30.2' in text


def test_version_resources_use_one_identity_without_optional_build_dependencies(tmp_path):
    from tools.build_installer import write_version_resources
    value=dict(version='2026.10.30.6')
    resource,assembly=write_version_resources(value,tmp_path)
    assert 'filevers=(2026, 10, 30, 6)' in resource.read_text()
    assert 'prodvers=(2026, 10, 30, 6)' in resource.read_text()
    assert 'AssemblyFileVersion("2026.10.30.6")' in assembly.read_text()


@pytest.mark.parametrize('failure',[None,'ci','main','bytes','preview','main_changed'])
def test_publisher_requires_accepted_main_and_bytes_but_not_certificate(tmp_path,monkeypatch,failure):
    from tools import publish_installer as publish
    from workbench import version_update
    value=manifest();setup=tmp_path/Path(value['package']['url']).name
    setup.write_bytes(b'installer bytes')
    if failure=='preview':value['preview']=True
    if failure=='bytes':setup.write_bytes(b'wrong installer')
    metadata=tmp_path/'latest.json';metadata.write_text(json.dumps(value))
    checked=[]
    def main_api(path):
        checked.append(path)
        changed=failure=='main' or (failure=='main_changed' and len(checked)>1)
        return dict(sha='b'*40 if changed else value['commit'])
    monkeypatch.setattr(version_update,'github_json',main_api)
    monkeypatch.setattr(version_update,'acceptance',lambda sha:failure!='ci')
    invoked=Mock();monkeypatch.setattr(publish.subprocess,'run',invoked)
    monkeypatch.setattr('sys.argv',['publish',str(setup),'--manifest',str(metadata)])
    if failure:
        with pytest.raises((SystemExit,ValueError)):publish.main()
        assert invoked.call_count==(1 if failure=='main_changed' else 0)
    else:
        publish.main()
        assert invoked.call_count==2
        create,edit=[c.args[0] for c in invoked.call_args_list]
        assert create[:3]==['gh','release','create'] and '--draft' in create
        assert edit[:3]==['gh','release','edit'] and '--latest' in edit


@pytest.mark.parametrize('mutate',[
    lambda v:v.update(sequence=v['sequence']+1),
    lambda v:v['package'].update(url=v['package']['url'].replace('sysujonathan','other')),
    lambda v:v['package'].update(url=v['package']['url'].replace('/v2026.10.30.2/','/v2026.10.30.3/'))])
def test_manifest_version_sequence_and_package_location_cannot_disagree(mutate):
    value=manifest();mutate(value)
    with pytest.raises(ValueError):update.validate_manifest(value)


@pytest.mark.skipif(sys.platform!='win32',reason='Actual Windows PowerShell workflow allocator')
@pytest.mark.parametrize('tags,main_ok,expected',[
    ([],True,'2026.10.30.7'),(['v2026.10.30.12'],True,'2026.10.30.13'),
    (['v2026.10.31.1'],True,None),([],False,None)])
def test_actual_workflow_allocator_respects_shipped_preview_and_releases(tmp_path,tags,main_ok,expected):
    root=Path(__file__).resolve().parents[1]
    workflow=(root/'.github/workflows/installed-release.yml').read_text()
    import yaml
    jobs=yaml.load(workflow,Loader=yaml.BaseLoader)['jobs']
    block=next(s['run'] for s in jobs['identity']['steps'] if s.get('id')=='identity')
    block=block.replace('[DateTime]::UtcNow',"[DateTime]::Parse('2026-10-30T14:00:00Z').ToUniversalTime()")
    (tmp_path/'packaging').mkdir()
    (tmp_path/'packaging/release.json').write_text(json.dumps(dict(sequence=202610300006)))
    output=tmp_path/'env.txt'
    history=json.dumps([dict(tag_name=t) for t in tags])
    script=tmp_path/'allocate.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n$global:LASTEXITCODE=0\nfunction git { 'accepted-head' }\n"+
        "function gh { if ($args -contains '--jq') { '"+('accepted-head' if main_ok else 'different-main')+
        "' } else { '"+history+"' } }\n"+block,encoding='utf-8-sig')
    env=dict(os.environ,GITHUB_ENV=str(output),GITHUB_OUTPUT=str(tmp_path/'outputs.txt'),GITHUB_REPOSITORY='sysujonathan/Aplus')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],
                          cwd=tmp_path,env=env,capture_output=True,timeout=30)
    if expected:
        assert result.returncode==0,result.stderr.decode(errors='replace')
        text=output.read_text(encoding='utf-16')
        assert 'APLUS_VERSION='+expected in text
        assert 'APLUS_SEQUENCE='+str(identity.release_sequence(expected)) in text
    else:
        assert result.returncode!=0
        assert not output.exists()


def test_broken_current_metadata_returns_visible_check_result(monkeypatch):
    def broken():raise ValueError('broken installed metadata')
    monkeypatch.setattr(update,'current_release',broken)
    result=update.check_update()
    assert result['state']=='unavailable' and 'broken installed metadata' in result['detail']


def test_low_disk_space_stops_before_network(tmp_path,monkeypatch):
    monkeypatch.setattr(update.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    network=Mock();path=tmp_path/'setup.exe'
    with pytest.raises(ValueError,match='空间不足'):update.download(manifest(),path,opener=network)
    network.assert_not_called();assert not path.exists()


def test_old_partial_file_cannot_block_new_verified_download(tmp_path):
    import io
    partial=tmp_path/'setup.part';partial.write_bytes(b'old interrupted bytes')
    path=tmp_path/'setup.exe'
    update.download(manifest(),path,opener=lambda *a,**k:io.BytesIO(b'installer bytes'))
    assert path.read_bytes()==b'installer bytes' and partial.read_bytes()==b'old interrupted bytes'
    assert not list(tmp_path.glob('setup.exe.*.part'))
