"""Two real Setup versions and real desktop ACK; only CI authority is synthetic."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('old',type=Path);parser.add_argument('new',type=Path)
    parser.add_argument('--fail-start',action='store_true')
    parser.add_argument('--expected-launcher',type=Path)
    args=parser.parse_args();old,new=args.old.resolve(),args.new.resolve()
    folder=ROOT/'.test-installer'/('update-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    from workbench.store import Store
    from workbench import installed_data as data, installed_update as update
    runtime=folder/'retained-runtime';store=Store(runtime)
    store.execute("INSERT INTO watchlist(code,observation_id,notes,active,created,updated) VALUES('sz.000001','','synthetic-update',1,'now','now')")
    before=store.path.read_bytes();home=folder/'state';data.bind_existing(runtime,home=home)
    root=folder/'installed-program'
    common=['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS','/TASKS=']
    subprocess.run([str(old),*common,'/DIR='+str(root)],check=True,timeout=180)
    old_launcher=(root/'Aplus.exe').read_bytes()
    if args.expected_launcher:
        # A live stable entry must prevent replacement, without forced exits
        # or scheduling a replacement at Windows reboot.
        with update.update_mutex('Local\\AplusInstalledLauncher'):
            blocked=subprocess.run([str(new),*common,'/DIR='+str(root)],timeout=120)
            assert blocked.returncode!=0
        assert (root/'Aplus.exe').read_bytes()==old_launcher
        assert (root/'current.txt').read_text().strip()=='2026.10.30.1'
    metadata=new.with_suffix('.json')
    if not metadata.exists():metadata=new.parent/'latest.json'
    target=json.loads(metadata.read_text())
    # The candidate cannot yet have official main CI; only publication authority
    # is substituted here. Installer bytes, data backup, GUI and ACK stay real.
    target['preview']=False
    target['package'].update(sha256=hashlib.sha256(new.read_bytes()).hexdigest(),bytes=new.stat().st_size)
    request_folder=home/'updates'/'controlled';request_folder.mkdir(parents=True)
    shutil.copy2(new,request_folder/'setup.exe')
    owned=[]
    real_popen=subprocess.Popen
    def controlled_popen(command,**kwargs):
        if args.fail_start and len(command)==1 and Path(command[0])==root/'app'/target['version']/'AplusApp.exe':
            raise OSError('Explicit isolated probe: new GUI process creation failed')
        child=real_popen(command,**kwargs)
        owned.append(child);return child
    env=dict(APLUS_INSTALL_STATE=str(home),APLUS_INSTALL_ROOT=str(root),
             APLUS_INSTALL_SMOKE='1',APLUS_INSTALL_TEST_EXIT='1',A_WORKBENCH_HOME=str(runtime))
    errors=[]
    try:
        with patch.dict(os.environ,env):
            parent=real_popen([sys.executable,'-I','-c','input()'],stdin=subprocess.PIPE)
            owned.append(parent)
            request=request_folder/'request.json'
            data.atomic_json(request,dict(parent_pid=parent.pid,install_root=str(root),runtime=str(runtime),target=target))
            def work():
                try:update.worker(request)
                except Exception as exc:errors.append(repr(exc))
            with patch.object(update,'load_manifest',lambda:target),patch.object(update.subprocess,'Popen',controlled_popen):
                thread=threading.Thread(target=work);thread.start()
                deadline=time.monotonic()+15
                while not (request_folder/'ready.json').exists() and time.monotonic()<deadline:time.sleep(.05)
                assert (request_folder/'ready.json').exists()
                parent.communicate(b'\n',timeout=10)
                thread.join(timeout=240)
                assert not thread.is_alive() and not errors,errors
            receipt=json.loads((request_folder/'receipt.json').read_text())
            assert receipt['state']==('rolled_back' if args.fail_start else 'completed'),receipt
            if not args.fail_start:
                assert json.loads((request_folder/'started.json').read_text())['started'] is True
            assert (root/'current.txt').read_text().strip()==('2026.10.30.1' if args.fail_start else target['version'])
            assert not (root/'update-pending.json').exists()
            if args.expected_launcher:
                assert (root/'Aplus.exe').read_bytes()==args.expected_launcher.read_bytes()
                assert (root/'Aplus.exe').read_bytes()!=old_launcher
            for child in owned:child.wait(timeout=25)
            # Normal backend startup may write metadata. Business records and
            # the pre-start backup are the relevant invariants after restart.
            assert Store(runtime).rows("SELECT notes FROM watchlist WHERE code='sz.000001'")[0]['notes']=='synthetic-update'
            profiles=json.loads((home/'data.json').read_text())
            assert Path(profiles['backup']).read_bytes()==before
        subprocess.run([str(root/'unins000.exe'),*common],check=True,timeout=120)
        result=dict(ok=True,real_installer=True,real_desktop_ack=not args.fail_start,rollback=args.fail_start,
                    launcher_replaced=bool(args.expected_launcher),busy_launcher_blocked=bool(args.expected_launcher),business_data_retained=True,
                    backup_unchanged=True,source_authority='synthetic official-release authority; real byte/order checks, no signature bypass',folder=str(folder))
        (folder/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    finally:
        for child in owned:
            if child.poll() is None:
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True)


if __name__=='__main__':main()
