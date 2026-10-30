"""Installed contracts use only disposable data, no real user libraries."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from workbench import installed_data as data, installed_update as update
from workbench.store import Store
from workbench.process_entry import chart_command


@pytest.fixture
def runtime(tmp_path):
    store=Store(tmp_path/'old-runtime')
    return store.root


def test_binding_keeps_original_bytes_and_backup(runtime,tmp_path):
    before=(runtime/'workbench.sqlite3').read_bytes()
    home=tmp_path/'installed'
    bound=data.bind_existing(runtime,home=home)
    profile=json.loads((home/'data.json').read_text())
    assert bound==runtime
    assert (runtime/'workbench.sqlite3').read_bytes()==before
    assert Path(profile['backup']).read_bytes()==before
    with sqlite3.connect(profile['backup']) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert data.bound_runtime(home=home)==runtime


def test_deleted_bound_data_never_creates_empty_replacement(runtime,tmp_path):
    home=tmp_path/'installed';data.bind_existing(runtime,home=home)
    (runtime/'workbench.sqlite3').unlink()
    with pytest.raises(ValueError,match='没有 Aplus 数据'):
        data.bound_runtime(home=home)
    assert not (runtime/'workbench.sqlite3').exists()


def test_new_binding_refuses_existing_default_data(tmp_path):
    home=tmp_path/'installed';root=tmp_path/'runtime';root.mkdir()
    (root/'important.txt').write_text('retain')
    with pytest.raises(ValueError,match='已有文件'):data.bind_new(home=home)
    assert (root/'important.txt').read_text()=='retain'
    assert not (home/'data.json').exists()


def test_future_schema_is_read_only_rejected(runtime,tmp_path):
    with sqlite3.connect(runtime/'workbench.sqlite3') as db:
        db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    before=(runtime/'workbench.sqlite3').read_bytes()
    with pytest.raises(ValueError,match='版本尚不支持'):
        data.bind_existing(runtime,home=tmp_path/'installed')
    assert (runtime/'workbench.sqlite3').read_bytes()==before


def test_running_jobs_block_binding(runtime,tmp_path):
    with sqlite3.connect(runtime/'workbench.sqlite3') as db:
        db.execute("INSERT INTO jobs(id,kind,status,created,spec) VALUES('test','sync','running','now','{}')")
    with pytest.raises(ValueError,match='运行或排队'):
        data.bind_existing(runtime,home=tmp_path/'installed')


def test_data_inside_new_program_is_not_bound(runtime,tmp_path):
    with pytest.raises(ValueError,match='新版程序目录'):
        data.bind_existing(runtime,home=tmp_path/'installed',install_root=tmp_path)


def test_chart_process_command_uses_frozen_dispatch(monkeypatch):
    monkeypatch.setattr('sys.frozen',True,raising=False)
    assert chart_command(Path('a b.json'))[1:]==['--chart','a b.json']
    monkeypatch.delattr('sys.frozen')
    assert chart_command('x.json')[1:]==['-m','gui.chart_shell','x.json']


def manifest():
    raw=b'installer bytes'
    return dict(protocol=1,channel='stable',version='2026.10.30.2',sequence=202610300002,
        commit='a'*40,database_schema=8,preview=False,
        package=dict(url=update.PACKAGE_BASE+'v2026.10.30.2/AplusSetup.exe',
                     bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))


@pytest.mark.parametrize('field,value',[
    ('protocol',2),('channel','preview'),('sequence',True),('commit','abcd'),
    ('version','../../bad'),('database_schema',9),('preview',True)])
def test_invalid_release_is_rejected(field,value):
    candidate=manifest();candidate[field]=value
    with pytest.raises(ValueError):update.validate_manifest(candidate)


@pytest.mark.parametrize('url',['http://github.com/sysujonathan/Aplus/releases/download/a',
    'https://example.com/update.exe','https://github.com/other/repo/releases/download/a'])
def test_untrusted_package_url_rejected(url):
    candidate=manifest();candidate['package']['url']=url
    with pytest.raises(ValueError):update.validate_manifest(candidate)


def test_download_exact_bytes_and_hash(tmp_path):
    candidate=manifest();path=tmp_path/'setup.exe'
    update.download(candidate,path,opener=lambda *a,**k:io.BytesIO(b'installer bytes'))
    assert path.read_bytes()==b'installer bytes'


@pytest.mark.parametrize('raw',[b'bad bytes',b'installer bytes plus extra'])
def test_failed_download_never_lands_executable(tmp_path,raw):
    path=tmp_path/'setup.exe'
    with pytest.raises(ValueError):
        update.download(manifest(),path,opener=lambda *a,**k:io.BytesIO(raw))
    assert not path.exists() and not path.with_suffix('.part').exists()


@pytest.mark.parametrize('ci',['in_progress','failure','success'])
def test_official_release_needs_exact_main_push_acceptance(ci):
    value=manifest();base=update.PACKAGE_BASE+'v2026.10.30.2/'
    def api(path):
        if path=='/releases/latest':return dict(assets=[dict(name='latest.json',size=500,
            browser_download_url=base+'latest.json'),dict(name='AplusSetup.exe',
            size=value['package']['bytes'],browser_download_url=value['package']['url'])])
        if path=='/commits/main':return dict(sha=value['commit'])
        return dict(workflow_runs=[dict(head_sha=value['commit'],head_branch='main',event='push',
            name='A acceptance',path='.github/workflows/checks.yml',id=1,
            status='in_progress' if ci=='in_progress' else 'completed',conclusion=ci)])
    opener=lambda *a,**k:io.BytesIO(json.dumps(value).encode())
    result=update.check_update(dict(sequence=1),api=api,opener=opener)
    assert (result['state']=='available') is (ci=='success')


@pytest.mark.parametrize('started,alive,expected',[(True,True,'completed'),(False,False,'rolled_back'),(False,True,None)])
def test_delayed_startup_recovery_never_kills_live_process(tmp_path,monkeypatch,started,alive,expected):
    home=tmp_path/'state';monkeypatch.setenv('APLUS_INSTALL_STATE',str(home))
    folder=home/'updates'/'attempt';folder.mkdir(parents=True)
    root=tmp_path/'program';(root/'app/2026.10.30.1').mkdir(parents=True)
    (root/'app/2026.10.30.1/AplusApp.exe').write_bytes(b'old-code')
    (root/'current.txt').write_text('2026.10.30.2')
    data.atomic_json(folder/'request.json',dict(install_root=str(root),target=manifest(),runtime=str(tmp_path/'data')))
    data.atomic_json(root/'update-pending.json',dict(folder=str(folder),previous='2026.10.30.1',pid=123,
        phase='startup_wait',previous_sha256=hashlib.sha256(b'old-code').hexdigest()))
    if started:data.atomic_json(folder/'started.json',dict(started=True,version=manifest()['version'],
        commit=manifest()['commit'],runtime=str(tmp_path/'data')))
    code=update.recover_pending(root,alive=lambda pid:alive,active=lambda path:False)
    if expected:
        assert code==0 and not (root/'update-pending.json').exists()
        assert json.loads((folder/'receipt.json').read_text())['state']==expected
        assert (root/'current.txt').read_text()==('2026.10.30.1' if expected=='rolled_back' else '2026.10.30.2')
    else:
        assert code==2 and (root/'update-pending.json').exists()
        assert (root/'current.txt').read_text()=='2026.10.30.2'


def test_unfinished_database_transaction_blocks_binding(runtime,tmp_path):
    journal=runtime/'workbench.sqlite3-journal';journal.write_bytes(b'not committed')
    with pytest.raises(ValueError,match='未收尾事务'):
        data.bind_existing(runtime,home=tmp_path/'installed')
    assert journal.read_bytes()==b'not committed'


@pytest.mark.parametrize('schema',[1,3])
def test_early_schema_binding_backs_up_before_existing_store_migration(tmp_path,schema):
    root=tmp_path/'legacy';root.mkdir();database=root/'workbench.sqlite3'
    script=(Path(__file__).parent/'fixtures/installed_legacy_v1.sql').read_text()
    with sqlite3.connect(database) as db:
        db.executescript(script)
        db.execute("UPDATE meta SET value=? WHERE key='schema_version'",(str(schema),))
        db.execute("INSERT INTO plans VALUES('p','o','sz.000001','MTR','daily','2026-01-01','DRAFT',10,9,12,100,'retain historical plan','now')")
    before=database.read_bytes();home=tmp_path/'installed'
    assert data.bind_existing(root,home=home)==root
    profile=json.loads((home/'data.json').read_text())
    assert database.read_bytes()==before==Path(profile['backup']).read_bytes()
    store=Store(root)
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']=='8'
    assert store.rows("SELECT notes FROM plans WHERE id='p'")[0]['notes']=='retain historical plan'
    assert Path(profile['backup']).read_bytes()==before


@pytest.mark.parametrize('wrong',['version','commit','runtime','json'])
def test_start_ack_must_match_actual_release_and_data(tmp_path,wrong):
    target=manifest();runtime=tmp_path/'runtime'
    value=dict(started=True,version=target['version'],commit=target['commit'],runtime=str(runtime))
    if wrong!='json':value[wrong]='different'
    path=tmp_path/'started.json';path.write_text('{' if wrong=='json' else json.dumps(value))
    assert update.confirmed_start(tmp_path,target,runtime) is False


def test_unsigned_installer_is_rejected_before_execution(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Windows Authenticode verification')
    path=tmp_path/'unsigned.exe';path.write_bytes(b'MZ unsigned synthetic package')
    with pytest.raises(ValueError,match='可信签名未通过'):update.require_signature(path)


def test_verified_source_loader_executes_verified_bytes_and_rejects_edits(tmp_path):
    from workbench.installed_imports import VerifiedSourceLoader
    path=tmp_path/'strategy.py';raw=b'value = 42\n';path.write_bytes(raw)
    loader=VerifiedSourceLoader('core.example',path,hashlib.sha256(raw).hexdigest())
    namespace={};exec(loader.get_code('core.example'),namespace)
    assert namespace['value']==42
    path.write_bytes(b'value = 99\n')
    with pytest.raises(ImportError,match='冻结源码校验失败'):loader.get_code('core.example')
