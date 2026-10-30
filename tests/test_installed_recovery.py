"""Hard interruption states + the real stable native recovery entry."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from workbench import installed_data as data, installed_update as update
from tests.test_installed_distribution import manifest

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows launcher and named mutexes')


@pytest.fixture(scope='module')
def binaries(tmp_path_factory):
    root=tmp_path_factory.mktemp('native-recovery')
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    launcher=root/'Aplus.exe'
    subprocess.run([str(compiler),'/nologo','/target:winexe','/reference:System.Windows.Forms.dll',
        '/reference:System.Web.Extensions.dll','/out:'+str(launcher),str(ROOT/'packaging/Launcher.cs')],check=True,capture_output=True)
    stub=root/'Stub.cs'
    stub.write_text('''using System;using System.IO;class Stub {
static void Main(){var path=Environment.GetEnvironmentVariable("APLUS_RECOVERY_PROOF");
if(path!=null) File.WriteAllText(path,"old version started");
if(Environment.GetEnvironmentVariable("APLUS_STUB_WAIT")=="1") Console.ReadLine();}}
''')
    application=root/'AplusApp.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/out:'+str(application),str(stub)],check=True,capture_output=True)
    return launcher,application


@pytest.fixture
def interrupted(tmp_path,monkeypatch,binaries):
    home=tmp_path/'state';folder=home/'updates'/'attempt';folder.mkdir(parents=True)
    monkeypatch.setenv('APLUS_INSTALL_STATE',str(home))
    root=tmp_path/'installed';old=root/'app/2026.10.30.1/AplusApp.exe'
    old.parent.mkdir(parents=True);shutil.copy2(binaries[1],old)
    data.atomic_json(old.parent/'_internal'/'installed-release.json',dict(
        version='2026.10.30.1',sequence=202610300001,commit='b'*40,preview=True))
    shutil.copy2(binaries[0],root/'Aplus.exe')
    request=dict(install_root=str(root),target=manifest(),runtime=str(tmp_path/'retained-data'))
    data.atomic_json(folder/'request.json',request)
    (root/'current.txt').write_text('2026.10.30.1')
    record=dict(folder=str(folder),previous='2026.10.30.1',phase='installing',
                previous_sha256=data.file_sha256(old))
    data.atomic_json(root/'update-pending.json',record)
    original=tmp_path/'retained-data';original.mkdir();(original/'important.txt').write_bytes(b'unchanged business fixture')
    return root,folder,record


@pytest.mark.parametrize('phase,pointer',[
    ('installing','2026.10.30.1'),  # worker died before/while Setup ran
    ('installing','2026.10.30.2'),  # Setup switched pointer, worker died
    ('installed','2026.10.30.2'),   # Setup exited, Popen not yet called
    ('launching','damaged-pointer')]) # current EXE/pointer cannot provide recovery
def test_no_pid_interruption_restores_verified_old_version(interrupted,phase,pointer):
    root,folder,record=interrupted
    record['phase']=phase;data.atomic_json(root/'update-pending.json',record)
    (root/'current.txt').write_text(pointer)
    result=subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30)
    assert result.returncode==0
    assert (root/'current.txt').read_text()=='2026.10.30.1'
    assert json.loads((folder/'receipt.json').read_text())['state']=='rolled_back'
    assert not (root/'update-pending.json').exists()
    assert (root.parent/'retained-data/important.txt').read_bytes()==b'unchanged business fixture'


def test_native_launcher_starts_old_app_when_current_payload_missing(interrupted,monkeypatch):
    root,folder,record=interrupted
    (root/'current.txt').write_text('2026.10.30.2')
    proof=root.parent/'old-started.txt';monkeypatch.setenv('APLUS_RECOVERY_PROOF',str(proof))
    assert subprocess.run([str(root/'Aplus.exe')],timeout=30).returncode==0
    deadline=time.monotonic()+5
    while not proof.exists() and time.monotonic()<deadline:time.sleep(.05)
    assert proof.read_text()=='old version started'


@pytest.mark.parametrize('kind',['unknown-phase','unknown-with-dead-pid','changed-old','installer-active'])
def test_uncertain_state_or_active_setup_preserves_everything(interrupted,kind):
    root,folder,record=interrupted
    if kind=='unknown-phase':record.pop('phase')
    if kind=='unknown-with-dead-pid':record.update(phase='future-stage',pid=99999999)
    if kind=='changed-old':(root/'app/2026.10.30.1/AplusApp.exe').write_bytes(b'broken exe')
    data.atomic_json(root/'update-pending.json',record)
    before=(root/'update-pending.json').read_bytes()
    def attempt():
        assert subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30).returncode!=0
    if kind=='installer-active':
        with update.update_mutex('Local\\AplusInstalledSetup'):attempt()
    else:attempt()
    assert (root/'update-pending.json').read_bytes()==before
    assert (root/'current.txt').read_text()=='2026.10.30.1'


def test_unrecorded_live_child_is_not_rolled_back(interrupted,binaries):
    root,folder,record=interrupted
    new=root/'app/2026.10.30.2/AplusApp.exe';new.parent.mkdir();shutil.copy2(binaries[1],new)
    record['phase']='launching';data.atomic_json(root/'update-pending.json',record)
    (root/'current.txt').write_text('2026.10.30.2')
    env=dict(os.environ,APLUS_STUB_WAIT='1')
    child=subprocess.Popen([str(new)],stdin=subprocess.PIPE,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30).returncode!=0
        assert child.poll() is None
        assert (root/'current.txt').read_text()=='2026.10.30.2'
        assert (root/'update-pending.json').exists()
    finally:child.communicate(b'\n',timeout=10)


@pytest.mark.parametrize('phase',['installing','installed','launching','startup_wait'])
def test_worker_crash_after_each_durable_boundary_is_recoverable(interrupted,monkeypatch,phase):
    root,folder,record=interrupted
    request=json.loads((folder/'request.json').read_text());request['parent_pid']=123
    data.atomic_json(folder/'request.json',request)
    (folder/'setup.exe').write_bytes(b'installer bytes')
    class HardStop(BaseException):pass
    real_atomic=update.atomic_json
    def write_then_stop(path,value):
        real_atomic(path,value)
        if Path(path).name=='update-pending.json' and value.get('phase')==phase:raise HardStop()
    def setup(*args,**kwargs):
        (root/'current.txt').write_text(request['target']['version'])
        return SimpleNamespace(returncode=0)
    kernel=SimpleNamespace(OpenProcess=Mock(return_value=1),WaitForSingleObject=Mock(return_value=0),CloseHandle=Mock())
    with monkeypatch.context() as scoped:
        scoped.setattr(update.ctypes,'windll',SimpleNamespace(kernel32=kernel))
        scoped.setattr(update,'update_mutex',lambda *args:nullcontext())
        scoped.setattr(update,'application_mutex',nullcontext)
        scoped.setattr(update,'require_web_closed',lambda:None)
        scoped.setattr(update,'load_manifest',lambda:request['target'])
        scoped.setattr(update,'bound_runtime',lambda:Path(request['runtime']))
        scoped.setattr(update,'bind_existing',lambda *args,**kwargs:None)
        scoped.setattr(update,'atomic_json',write_then_stop)
        scoped.setattr(update.subprocess,'run',setup)
        scoped.setattr(update.subprocess,'Popen',Mock(return_value=Mock(pid=4321)))
        with pytest.raises(HardStop):update.worker(folder/'request.json')
    durable=json.loads((root/'update-pending.json').read_text())
    assert durable['phase']==phase and durable['previous_sha256']==record['previous_sha256']
    assert ('pid' in durable)==(phase=='startup_wait')
    durable['pid']=99999999;data.atomic_json(root/'update-pending.json',durable)
    assert subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30).returncode==0
    assert (root/'current.txt').read_text()=='2026.10.30.1'
    assert (root.parent/'retained-data/important.txt').read_bytes()==b'unchanged business fixture'


@pytest.mark.parametrize('started,alive,expected',[(True,True,'completed'),(False,False,'rolled_back'),(False,True,None)])
def test_native_delayed_ack_and_live_process(interrupted,started,alive,expected):
    root,folder,record=interrupted
    request=json.loads((folder/'request.json').read_text());target=request['target']
    record.update(phase='startup_wait',pid=os.getpid() if alive else 99999999)
    data.atomic_json(root/'update-pending.json',record);(root/'current.txt').write_text(target['version'])
    if started:data.atomic_json(folder/'started.json',dict(started=True,version=target['version'],
        commit=target['commit'],runtime=request['runtime']))
    result=subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30)
    assert (result.returncode==0)==bool(expected)
    if expected:
        assert json.loads((folder/'receipt.json').read_text())['state']==expected
        assert not (root/'update-pending.json').exists()
    else:assert (root/'current.txt').read_text()==target['version']


@pytest.mark.parametrize('change',['protocol','channel','preview','commit','sequence','date','url','sha','bytes','previous'])
def test_shipping_recovery_rejects_invalid_identity_even_with_ack(interrupted,change):
    root,folder,record=interrupted
    request=json.loads((folder/'request.json').read_text());target=request['target']
    if change=='previous':record['previous']='../../outside'
    elif change=='date':target['version']='2026.02.30.1'
    elif change in ('url','sha','bytes'):
        target['package'][{'url':'url','sha':'sha256','bytes':'bytes'}[change]]={'url':'https://other/setup.exe','sha':'x','bytes':0}[change]
    else:target[change]={'protocol':2,'channel':'preview','preview':True,'commit':'x','sequence':1}[change]
    data.atomic_json(folder/'request.json',request);data.atomic_json(root/'update-pending.json',record)
    data.atomic_json(folder/'started.json',dict(started=True,version=target['version'],commit=target['commit'],runtime=request['runtime']))
    before=(root/'update-pending.json').read_bytes()
    assert subprocess.run([str(root/'Aplus.exe'),'--recover-only'],timeout=30).returncode!=0
    assert (root/'update-pending.json').read_bytes()==before
    assert (root/'current.txt').read_text()=='2026.10.30.1'


def test_launcher_inherits_proxy_keys_differing_only_in_case(interrupted):
    root,folder,record=interrupted
    (root/'update-pending.json').unlink()
    proof=root/'started-proof.txt'
    env=dict(os.environ,APLUS_RECOVERY_PROOF=str(proof),HTTPS_PROXY='http://127.0.0.1:9',https_proxy='http://127.0.0.1:10')
    # A real native environment block contains both keys. Never read/print
    # any credentials inherited from the user's real proxy configuration.
    assert subprocess.run([str(root/'Aplus.exe')],env=env,timeout=15).returncode==0
    deadline=time.monotonic()+5
    while not proof.exists() and time.monotonic()<deadline:time.sleep(.05)
    assert proof.read_text()=='old version started'


@pytest.mark.parametrize('previous',['../../outside','2026.02.30.1','2026.10.30.01'])
def test_worker_invalid_previous_never_launches_or_installs(interrupted,monkeypatch,previous):
    root,folder,record=interrupted;(root/'current.txt').write_text(previous)
    request=json.loads((folder/'request.json').read_text());request['parent_pid']=123
    data.atomic_json(folder/'request.json',request)
    kernel=SimpleNamespace(OpenProcess=Mock(return_value=1),WaitForSingleObject=Mock(return_value=0),CloseHandle=Mock())
    monkeypatch.setattr(update.ctypes,'windll',SimpleNamespace(kernel32=kernel))
    monkeypatch.setattr(update,'update_mutex',lambda *a:nullcontext())
    install=Mock();monkeypatch.setattr(update.subprocess,'run',install);monkeypatch.setattr(update.subprocess,'Popen',install)
    with pytest.raises(ValueError):update.worker(folder/'request.json')
    install.assert_not_called()
