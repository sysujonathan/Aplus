"""Real old Setup/EXE -> bootstrap -> primary GUI, on synthetic retained data.

Preview mode substitutes only release authority and uses the byte-identical
archived legacy helper. --official runs the shipped old EXE helper and live
release discovery/download, with no authority substitution or preview hook.
"""
from contextlib import closing,contextmanager
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import types
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


@contextmanager
def registration(root):
    import winreg
    key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7B56A3BD-706C-4F60-AC18-0453099FA226}_is1'
    saved=[]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
            for index in range(winreg.QueryInfoKey(handle)[1]):saved.append(winreg.EnumValue(handle,index))
    except FileNotFoundError:pass
    try:yield
    finally:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
                location=winreg.QueryValueEx(handle,'InstallLocation')[0]
                if Path(location).resolve()!=root.resolve():
                    raise ValueError('Concurrent unrelated installation; registration retained')
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
        except FileNotFoundError:pass
        if saved:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,key) as handle:
                for name,value,kind in saved:winreg.SetValueEx(handle,name,0,kind,value)


def wait_for(predicate,seconds=120):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.2)
    raise TimeoutError('Real schema bridge probe timed out')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('old',type=Path);parser.add_argument('new',type=Path)
    parser.add_argument('--manifest',required=True,type=Path);parser.add_argument('--official',action='store_true')
    parser.add_argument('--fail-start',action='store_true');parser.add_argument('--recover-written',action='store_true')
    args=parser.parse_args();raw=json.loads(args.manifest.read_text(encoding='utf-8'))
    if args.official and args.fail_start:raise ValueError('Fault injection is preview-only')
    from workbench import installed_data as data, installed_bundle as bundle, installed_migration as migration
    from workbench.version_update import application_mutex,require_web_closed
    with application_mutex():require_web_closed()  # Stop before installing if a genuine user app is active.
    folder=ROOT/'.test-installer'/('bridge-probe-'+uuid.uuid4().hex)
    runtime=folder/'runtime';runtime.mkdir(parents=True);root=folder/'program';home=folder/'state'
    with closing(sqlite3.connect(runtime/'workbench.sqlite3')) as db:
        db.executescript((ROOT/'tests/fixtures/installed_schema8.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT/'tests/fixtures/installed_schema8_business.sql').read_text(encoding='utf-8'))
        db.execute("INSERT INTO watchlist VALUES('sz.000001','','synthetic bridge retained',1,'now','now')");db.commit()
    (runtime/'market').mkdir();(runtime/'market/original.csv').write_bytes(b'retained original daily cache\n')
    before=(runtime/'workbench.sqlite3').read_bytes();facts=migration.business_fingerprint(runtime/'workbench.sqlite3')
    data.bind_existing(runtime,home=home)
    initial_backup=Path(json.loads((home/'data.json').read_text(encoding='utf-8'))['backup'])
    archive=(ROOT/'tests/fixtures/installed_update_schema8.py.txt').read_bytes()
    assert hashlib.sha256(archive).hexdigest()=='4f82809873435a1b786827b71c78fea21b12124ca72b90510b66dadd4655794d'
    legacy=types.ModuleType('workbench._shipped_schema8_probe');legacy.__file__=str(ROOT/'workbench/installed_update.py')
    exec(compile(archive,legacy.__file__,'exec'),legacy.__dict__)
    target=copy.deepcopy(raw)
    if args.official:
        discovered=legacy.check_update(dict(sequence=1,database_schema=8))
        assert discovered['state']=='available' and discovered['target']==raw,discovered
    else:
        assert raw['preview'] is True
        target['preview']=False  # Explicit synthetic authority for the old helper only; never published.
    assert args.new.stat().st_size==target['package']['bytes']
    assert data.file_sha256(args.new)==target['package']['sha256']
    common=['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS','/TASKS=']
    env=dict(APLUS_INSTALL_STATE=str(home),APLUS_INSTALL_ROOT=str(root),
             APLUS_INSTALL_SMOKE='1',APLUS_INSTALL_TEST_EXIT='1',LOCALAPPDATA=str(folder/'local'),
             A_WORKBENCH_HOME=str(runtime))
    if not args.official:
        probe=folder/'probe.json'
        data.atomic_json(probe,dict(synthetic_schema_bridge=True,install_root=str(root),runtime=str(runtime),
            target=bundle.primary_target(raw),fault='launch_failed' if args.fail_start else None))
        env['APLUS_SCHEMA_BRIDGE_PROBE']=str(probe)
    owned=[];errors=[];real_popen=subprocess.Popen
    def start(command,**kwargs):
        child=real_popen(command,**kwargs);owned.append(child);return child
    with registration(root),patch.dict(os.environ,env):
        try:
            subprocess.run([str(args.old.resolve()),*common,'/DIR='+str(root)],check=True,timeout=180)
            previous=(root/'current.txt').read_text().strip();old=root/'app'/previous/'AplusApp.exe'
            assert data.file_sha256(old.parent/'_internal/workbench/installed_update.py')==hashlib.sha256(archive).hexdigest()
            initial=home/'updates/old-start';initial.mkdir(parents=True)
            attempt=home/'updates/legacy-update';attempt.mkdir()
            if args.official:legacy.download(target,attempt/'setup.exe')
            else:shutil.copy2(args.new,attempt/'setup.exe')
            parent=start([str(old)],env=dict(os.environ,APLUS_INSTALLED_ACK=str(initial/'started.json')),cwd=root)
            wait_for(lambda:(initial/'started.json').exists(),45)
            request=attempt/'request.json'
            data.atomic_json(request,dict(target=target,parent_pid=parent.pid,runtime=str(runtime),install_root=str(root)))
            if args.official:
                helper=start([str(old),'--installed-update',str(request)],cwd=root)
            else:
                def work():
                    try:legacy.worker(request)
                    except BaseException as exc:errors.append(repr(exc))
                with patch.object(legacy,'load_manifest',lambda:target),patch.object(legacy.subprocess,'Popen',start):
                    thread=threading.Thread(target=work);thread.start();thread.join(timeout=240)
                    assert not thread.is_alive() and not errors,errors
            parent.wait(timeout=30)
            def completed():
                for candidate in (home/'updates').glob('*/request.json'):
                    value=json.loads(candidate.read_text(encoding='utf-8'))
                    if not value.get('bundle_handoff'):continue
                    receipt=candidate.with_name('receipt.json')
                    if receipt.exists():
                        status=json.loads(receipt.read_text(encoding='utf-8'))
                        if status.get('state')=='rolled_back' and args.fail_start:return candidate.parent
                        if status.get('state') in ('rolled_back','blocked'):raise AssertionError(status)
                        if status.get('state')=='completed':return candidate.parent
                return None
            handoff=wait_for(completed,180)
            if args.fail_start:
                proof=json.loads((handoff/'fault-proof.json').read_text(encoding='utf-8'))
                assert proof['restored_original_before_restart'] and proof['schema']==8
                wait_for(lambda:Path(proof['ack']).exists(),45)
                assert json.loads(Path(proof['ack']).read_text(encoding='utf-8'))['version']==previous
            else:
                ack=json.loads((handoff/'started.json').read_text(encoding='utf-8'))
                assert ack['database_schema']==9 and ack['version']==raw['version']
            assert data.inspect_data(runtime)['schema']==(8 if args.fail_start else 9)
            after=migration.business_fingerprint(runtime/'workbench.sqlite3')
            # Normal GUI startup may update meta preferences, but old business tables stay identical.
            assert all(after[k]==v for k,v in facts.items() if k!='meta')
            retained_original=(handoff/'migration/original.sqlite3').read_bytes()
            assert initial_backup.read_bytes()==before
            # Old GUI startup may update preferences. Compare the exact bytes
            # saved after that real GUI exits, before either helper migrates.
            originals=list((home/'backups').glob('*/workbench.sqlite3'))
            assert sum(p.read_bytes()==retained_original for p in originals)>=2
            assert (runtime/'market/original.csv').read_bytes()==b'retained original daily cache\n'
            assert (root/'current.txt').read_text().strip()==(previous if args.fail_start else raw['version'])
            for child in owned:child.wait(timeout=45)
            time.sleep(6)  # Primary's test-only timed, normal tray exit releases native gates.
            if args.recover_written and not args.fail_start:
                recovery=home/'updates'/('recovery-'+uuid.uuid4().hex);recovery.mkdir()
                shutil.copytree(handoff/'migration',recovery/'migration')
                plan=json.loads((handoff/'migration/receipt.json').read_text(encoding='utf-8'))
                plan.update(backup=str(recovery/'migration/original.sqlite3'),shadow=str(recovery/'migration/shadow/workbench.sqlite3'))
                with closing(sqlite3.connect(runtime/'workbench.sqlite3')) as db:
                    db.execute("UPDATE watchlist SET notes='synthetic new business retained'");db.commit()
                written=migration.business_fingerprint(runtime/'workbench.sqlite3')
                recovered_target=bundle.primary_target(target)
                data.atomic_json(recovery/'request.json',dict(target=recovered_target,runtime=str(runtime),install_root=str(root)))
                data.atomic_json(root/'update-pending.json',dict(previous=previous,folder=str(recovery),phase='startup_wait',
                    previous_sha256=data.file_sha256(old),migration=plan))
                retry=start([str(root/'Aplus.exe')],cwd=root)
                wait_for(lambda:(recovery/'started.json').exists(),60)
                wait_for(lambda:not (root/'update-pending.json').exists(),30)
                assert json.loads((recovery/'receipt.json').read_text(encoding='utf-8'))['recovered'] is True
                after_recovery=migration.business_fingerprint(runtime/'workbench.sqlite3')
                assert data.inspect_data(runtime)['schema']==9
                assert all(after_recovery[k]==v for k,v in written.items() if k!='meta')
                retry.wait(timeout=30);time.sleep(6)
            subprocess.run([str(root/'unins000.exe'),*common],check=True,timeout=120)
            report=dict(ok=True,synthetic_data=True,old_exe_unmodified=True,old_gui_started=True,
                real_setup=True,real_primary_gui_ack=not args.fail_start,one_user_update=True,second_download=False,
                schema8_to9=not args.fail_start,old_business_retained=True,backup_original_bytes=True,cache_retained=True,
                real_rollback_gui=args.fail_start,real_native_forward_recovery=args.recover_written and not args.fail_start,
                official_authority=args.official,preview_authority_substitution=not args.official,folder=str(folder))
            data.atomic_json(folder/'result.json',report);print(json.dumps(report))
        finally:
            # These are only processes we created and recorded; never touch another Aplus.
            for child in owned:
                if child.poll() is None:child.terminate();child.wait(timeout=15)
            assert root.resolve().is_relative_to((ROOT/'.test-installer').resolve())
            script="""$prefix=[IO.Path]::GetFullPath($env:APLUS_PROBE_ROOT).TrimEnd('\\')+'\\'
Get-CimInstance Win32_Process -Filter \"Name='AplusApp.exe'\" | Where-Object {
 $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath)).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                env=dict(os.environ,APLUS_PROBE_ROOT=str(root)),capture_output=True,timeout=20)


if __name__=='__main__':main()
