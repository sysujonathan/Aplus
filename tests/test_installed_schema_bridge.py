"""Actual legacy client admission, exact schema-eight facts and native recovery."""
from contextlib import closing
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import types
from unittest.mock import Mock

import pytest

from workbench import installed_update as update, installed_data as data, installed_migration as migration
from workbench import installed_bundle as bundle
from workbench.installed_cleanup import write_inventory
from tests.test_installed_distribution import manifest
from tests.test_auto_installed_release import authority, HEAD
from tests.test_installed_recovery import binaries

ROOT=Path(__file__).resolve().parents[1]


def schema8(root):
    root.mkdir(parents=True,exist_ok=True);database=root/'workbench.sqlite3'
    with closing(sqlite3.connect(database)) as db:
        db.executescript((ROOT/'tests/fixtures/installed_schema8.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT/'tests/fixtures/installed_schema8_business.sql').read_text(encoding='utf-8'))
        db.execute("INSERT INTO watchlist VALUES('sz.000001','','retained watch',1,'now','now')")
        db.execute("INSERT INTO meta VALUES('custom-user-setting','retained value')")
        db.commit()
    (root/'market').mkdir();(root/'market'/'immutable.csv').write_bytes(b'original daily cache\n')
    return database


def release():
    value=manifest();value.update(database_schema=8,component='schema_bridge',payload_schema=9,commit=HEAD)
    value['upgrade']=dict(protocol=1,from_schema=8,to_schema=9,entry=bundle.PRIMARY_ENTRY,inventory_sha256='e'*64)
    return value


def payload(root,value):
    for name,schema in (('',8),('primary',9)):
        app=root/name;app.mkdir(parents=True,exist_ok=True);(app/'AplusApp.exe').write_bytes(('program '+str(schema)).encode())
        identity={k:v for k,v in value.items() if k not in ('package','upgrade')}
        identity['database_schema']=schema
        if schema==9:identity.pop('component');identity.pop('payload_schema')
        data.atomic_json(app/'_internal'/'installed-release.json',identity)
    write_inventory(root)
    value['upgrade']['inventory_sha256']=data.file_sha256(root/'installed-files.json')


def test_unmodified_shipped_schema8_client_accepts_genuine_bootstrap_and_new_client_selects_primary():
    raw=(ROOT/'tests/fixtures/installed_update_schema8.py.txt').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='4f82809873435a1b786827b71c78fea21b12124ca72b90510b66dadd4655794d'
    # This fixture is the unchanged update module from official v2026.10.31.2.
    old=types.ModuleType('workbench._shipped_schema8');old.__file__=str(ROOT/'workbench/installed_update.py')
    exec(compile(raw,old.__file__,'exec'),old.__dict__)
    value=release();api,opener=authority(value)
    current=dict(sequence=1,database_schema=8)
    legacy=old.check_update(current,api=api,opener=opener)
    assert legacy['state']=='available' and legacy['target']['database_schema']==8
    modern=update.check_update(current,api=api,opener=opener)
    assert modern['state']=='available' and modern['target']['database_schema']==9
    assert update.load_manifest(api,opener,select_payload=False)==value
    assert bundle.canonical_manifest(modern['target'])==value
    assert legacy['target']['upgrade']['to_schema']==9


def test_plain_cross_schema_release_is_still_refused():
    value=manifest()
    with pytest.raises(ValueError,match='单独的数据兼容'):
        update.require_same_schema(value,dict(database_schema=8))


@pytest.mark.parametrize('field,value',[('from_schema',7),('to_schema',10),('to_schema',True),
    ('entry','../AplusApp.exe'),('inventory_sha256','bad'),('protocol',True)])
def test_only_exact_known_bridge_contract_is_accepted(field,value):
    target=release();target['upgrade'][field]=value
    with pytest.raises(ValueError):update.validate_manifest(target)


def test_payload_and_both_real_component_identities_are_byte_bound(tmp_path):
    value=release();app=tmp_path/'app';payload(app,value);target=bundle.primary_target(value)
    assert update.validate_manifest(target)==target
    assert bundle.verify_payload(app,target)==app/'primary/AplusApp.exe'
    (app/'primary/AplusApp.exe').write_bytes(b'tampered')
    with pytest.raises(ValueError):bundle.verify_payload(app,target)


def test_shadow_upgrade_preserves_every_old_table_and_cache_and_can_restore_exact_bytes(tmp_path):
    root=tmp_path/'runtime';database=schema8(root);before=database.read_bytes()
    folder=tmp_path/'update';folder.mkdir();facts=migration.business_fingerprint(database)
    plan=migration.prepare(root,folder)
    assert database.read_bytes()==before==Path(plan['backup']).read_bytes()
    migration.commit(plan,root,folder)
    assert data.inspect_data(root)['schema']==9
    current=migration.business_fingerprint(database)
    assert all(current[k]==v for k,v in facts.items())
    assert (root/'market/immutable.csv').read_bytes()==b'original daily cache\n'
    assert migration.restore_if_unchanged(plan,root,folder)
    assert database.read_bytes()==before


def test_new_business_write_never_gets_replaced_by_old_backup(tmp_path):
    root=tmp_path/'runtime';database=schema8(root);folder=tmp_path/'update';folder.mkdir()
    plan=migration.prepare(root,folder);migration.commit(plan,root,folder)
    with closing(sqlite3.connect(database)) as db:
        db.execute("UPDATE watchlist SET notes='new real edit'");db.commit()
    before=database.read_bytes()
    assert not migration.restore_if_unchanged(plan,root,folder)
    assert database.read_bytes()==before and data.inspect_data(root)['schema']==9


def test_bad_migration_cannot_touch_original_data(tmp_path,monkeypatch):
    from workbench import store
    root=tmp_path/'runtime';database=schema8(root);before=database.read_bytes();folder=tmp_path/'update';folder.mkdir()
    original=store.Store
    def broken(path):
        result=original(path);result.execute("UPDATE watchlist SET notes='incorrect rewrite'");return result
    monkeypatch.setattr(store,'Store',broken)
    with pytest.raises(ValueError,match='改变了已有记录'):migration.prepare(root,folder)
    assert database.read_bytes()==before


@pytest.mark.parametrize('problem',['active','journal','space'])
def test_unsafe_source_or_insufficient_space_never_migrates(tmp_path,monkeypatch,problem):
    root=tmp_path/'runtime';database=schema8(root);folder=tmp_path/'update';folder.mkdir()
    if problem=='active':
        with closing(sqlite3.connect(database)) as db:
            db.execute("INSERT INTO jobs(id,kind,status,created,spec) VALUES('busy','scan','running','now','{}')");db.commit()
    if problem=='journal':database.with_name(database.name+'-journal').write_bytes(b'pending')
    if problem=='space':monkeypatch.setattr(migration.shutil,'disk_usage',lambda *_:types.SimpleNamespace(free=0))
    before=database.read_bytes()
    with pytest.raises(ValueError):migration.prepare(root,folder)
    assert database.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Native Windows recovery')
@pytest.mark.parametrize('phase',['prepared','committed','written'])
def test_real_native_recovery_restores_only_untouched_migration(tmp_path,monkeypatch,binaries,phase):
    home=tmp_path/'state';folder=home/'updates/attempt';folder.mkdir(parents=True)
    monkeypatch.setenv('APLUS_INSTALL_STATE',str(home))
    root=tmp_path/'installed';previous='2026.10.30.1';value=release();target=bundle.primary_target(value)
    old=root/'app'/previous/'AplusApp.exe';old.parent.mkdir(parents=True);shutil.copy2(binaries[1],old)
    shutil.copy2(binaries[0],root/'Aplus.exe');(root/'current.txt').write_text(target['version'])
    runtime=tmp_path/'runtime';database=schema8(runtime);before=database.read_bytes()
    data.atomic_json(home/'data.json',dict(runtime=str(runtime),existing=True))
    plan=migration.prepare(runtime,folder)
    if phase!='prepared':migration.commit(plan,runtime,folder)
    if phase=='written':
        with closing(sqlite3.connect(database)) as db:
            db.execute("UPDATE watchlist SET notes='retain new write'");db.commit()
    retained=database.read_bytes()
    data.atomic_json(folder/'request.json',dict(install_root=str(root),runtime=str(runtime),target=target))
    data.atomic_json(root/'update-pending.json',dict(folder=str(folder),previous=previous,phase='migrating',
        previous_sha256=data.file_sha256(old),migration=plan))
    result=subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30,capture_output=True)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')
    if phase=='written':
        assert database.read_bytes()==retained and (root/'current.txt').read_text()==target['version']
        assert (root/'update-pending.json').exists()
        assert json.loads((folder/'receipt.json').read_text(encoding='utf-8'))['state']=='startup_unconfirmed'
    else:
        assert database.read_bytes()==before and (root/'current.txt').read_text()==previous
        assert not (root/'update-pending.json').exists()


@pytest.mark.skipif(os.name!='nt',reason='Native Windows recovery')
def test_native_guard_rejects_active_desktop_without_touching_pending(tmp_path,monkeypatch,binaries):
    root=tmp_path/'app';root.mkdir();shutil.copy2(binaries[0],root/'Aplus.exe')
    pending=root/'update-pending.json';pending.write_bytes(b'{"retain":"pending"}')
    name='Local\\AplusNativeRecovery-'+hashlib.sha256(str(binaries[0].parent).encode()).hexdigest()[:20]
    with update.update_mutex(name):
        result=subprocess.run([str(root/'Aplus.exe'),'--recover-only'],capture_output=True,timeout=30)
    assert result.returncode==1 and pending.read_bytes()==b'{"retain":"pending"}'


@pytest.mark.parametrize('outcome',['success','launch_failed','new_write_then_failed','release_changed'])
def test_handoff_worker_uses_installed_payload_once_and_preserves_business_data(tmp_path,monkeypatch,outcome):
    home=tmp_path/'state';folder=home/'updates/attempt';folder.mkdir(parents=True)
    monkeypatch.setenv('APLUS_INSTALL_STATE',str(home))
    root=tmp_path/'installed';value=release();target=bundle.primary_target(value)
    app=root/'app'/value['version'];payload(app,value);target=bundle.primary_target(value)
    previous='2026.10.30.1';old=root/'app'/previous;old.mkdir(parents=True);(old/'AplusApp.exe').write_bytes(b'old executable')
    data.atomic_json(old/'_internal/installed-release.json',dict(version=previous,sequence=202610300001,
        commit='b'*40,preview=False,database_schema=8))
    (root/'current.txt').write_text(value['version']);(root/'previous.txt').write_text(previous)
    runtime=tmp_path/'runtime';database=schema8(runtime);before=database.read_bytes()
    data.atomic_json(home/'data.json',dict(runtime=str(runtime),existing=True))
    data.atomic_json(folder/'request.json',dict(target=target,parent_pid=123,runtime=str(runtime),
        install_root=str(root),bundle_handoff=True))
    monkeypatch.setattr(update,'update_mutex',lambda *args:nullcontext())
    monkeypatch.setattr(update,'application_mutex',nullcontext)
    monkeypatch.setattr(update,'require_web_closed',lambda:None)
    monkeypatch.setattr(update,'load_manifest',lambda:dict(target,commit='d'*40) if outcome=='release_changed' else target)
    kernel=types.SimpleNamespace(OpenProcess=Mock(return_value=1),WaitForSingleObject=Mock(return_value=0),CloseHandle=Mock())
    monkeypatch.setattr(update.ctypes,'windll',types.SimpleNamespace(kernel32=kernel))
    calls=[]
    def start(command,**kwargs):
        calls.append(command)
        if Path(command[0])==app/'AplusApp.exe':
            assert data.inspect_data(runtime)['schema']==9
            if outcome=='launch_failed':raise OSError('injected primary launch failure')
            if outcome=='new_write_then_failed':
                with closing(sqlite3.connect(database)) as db:
                    db.execute("UPDATE watchlist SET notes='new business write'");db.commit()
                return types.SimpleNamespace(pid=4321,poll=lambda:1)
            data.atomic_json(folder/'started.json',dict(started=True,version=target['version'],commit=HEAD,
                runtime=str(runtime),database_schema=9))
        return types.SimpleNamespace(pid=4321,poll=lambda:None)
    monkeypatch.setattr(update.subprocess,'Popen',start)
    monkeypatch.setattr(update.subprocess,'run',lambda *a,**k:pytest.fail('handoff must reuse installed payload, not run Setup again'))
    update.worker(folder/'request.json')
    result=json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    if outcome=='success':
        assert result['state']=='completed' and data.inspect_data(runtime)['schema']==9
        assert (folder/'migration/original.sqlite3').read_bytes()==before
        assert len(calls)==1
    elif outcome=='new_write_then_failed':
        assert result['state']=='startup_unconfirmed' and data.inspect_data(runtime)['schema']==9
        assert (root/'update-pending.json').exists() and len(calls)==1
    else:
        assert result['state']=='rolled_back' and database.read_bytes()==before
        assert (root/'current.txt').read_text()==previous
        assert Path(calls[-1][0])==old/'AplusApp.exe'


@pytest.mark.parametrize('problem',['official','runtime','program','state','unmarked','target_official'])
def test_preview_authority_probe_cannot_apply_to_official_program_or_other_paths(tmp_path,monkeypatch,problem):
    from tools.schema_bridge_probe import install_authority
    from workbench import release_identity
    folder=tmp_path/'.test-installer'/'bridge-probe-synthetic';folder.mkdir(parents=True)
    home=folder/'state';home.mkdir();monkeypatch.setenv('APLUS_INSTALL_STATE',str(home))
    target=bundle.primary_target(release());target['preview']=True;target['bridge_manifest']['preview']=True
    value=dict(synthetic_schema_bridge=True,install_root=str(folder/'program'),runtime=str(folder/'runtime'),target=target)
    if problem=='runtime':value['runtime']=str(tmp_path/'other')
    if problem=='program':value['install_root']=str(tmp_path/'other')
    if problem=='state':monkeypatch.setenv('APLUS_INSTALL_STATE',str(tmp_path/'other'))
    if problem=='unmarked':value['synthetic_schema_bridge']=False
    if problem=='target_official':target['preview']=False
    path=folder/'probe.json';data.atomic_json(path,value)
    monkeypatch.setattr(release_identity,'read_identity',lambda:dict(preview=problem!='official'))
    original=update.load_manifest
    with pytest.raises(ValueError):install_authority(path)
    assert update.load_manifest is original
