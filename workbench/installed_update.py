"""Installed release checks and out-of-process installer updates; no local Git."""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import uuid

from .installed_data import state_home, atomic_json, bound_runtime, bind_existing, file_sha256
from .version_update import github_json, acceptance, application_mutex, require_web_closed

REPO='sysujonathan/Aplus'
PACKAGE_BASE='https://github.com/'+REPO+'/releases/download/'


def current_release():
    root=Path(__file__).resolve().parents[1]
    return json.loads((root/'installed-release.json').read_text(encoding='utf-8'))


def validate_manifest(value):
    if value.get('protocol')!=1 or value.get('channel')!='stable':
        raise ValueError('更新信息格式不受支持。')
    if value.get('preview') is not False:
        raise ValueError('预览安装包不能作为自动更新的正式发布。')
    if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d+',value.get('version','')):
        raise ValueError('更新版本无效。')
    if type(value.get('sequence')) is not int or value['sequence']<1:
        raise ValueError('更新发布序号无效。')
    if not re.fullmatch('[0-9a-f]{40}',value.get('commit','')):
        raise ValueError('无法确认正式版提交。')
    package=value.get('package',{})
    url=package.get('url','')
    if not url.startswith(PACKAGE_BASE) or urlparse(url).query or urlparse(url).fragment:
        raise ValueError('更新包不是官方发布地址。')
    if not re.fullmatch('[0-9a-f]{64}',package.get('sha256','')):
        raise ValueError('更新包校验信息缺失。')
    if type(package.get('bytes')) is not int or not 0<package['bytes']<=2_000_000_000:
        raise ValueError('更新包大小无效。')
    if value.get('database_schema')!=8:
        raise ValueError('此更新需要单独的数据兼容升级，当前版本继续可用。')
    return value


def load_manifest(api=github_json, opener=urlopen):
    release=api('/releases/latest')
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('正式发布尚未准备好。')
    assets=release.get('assets',[])
    metadata=next((a for a in assets if a.get('name')=='latest.json'),None)
    if not metadata or metadata.get('size',0)>32768:
        raise ValueError('正式发布尚未准备好。')
    url=metadata['browser_download_url']
    if not url.startswith(PACKAGE_BASE):
        raise ValueError('正式发布来源无效。')
    with opener(Request(url,headers={'User-Agent':'Aplus-installed-update'}),timeout=15) as response:
        value=validate_manifest(json.loads(response.read(32769)))
    package=next((a for a in assets if a.get('browser_download_url')==value['package']['url']),None)
    if not package or package.get('size')!=value['package']['bytes']:
        raise ValueError('新版程序包尚未完整发布。')
    target=value['commit']
    main=api('/commits/main')['sha']
    if main!=target:
        comparison=api('/compare/'+target+'...'+main)
        if comparison.get('status') not in ('ahead','identical'):
            raise ValueError('更新提交不属于官方主线。')
    if not acceptance(target,api):
        raise ValueError('正式版本验收尚未完成，请稍后检查。')
    return value


def check_update(current=None, *, api=github_json, opener=urlopen):
    current=current or current_release()
    try:
        target=load_manifest(api,opener)
        if target['sequence']<=current['sequence']:
            return dict(state='current',message='当前已是最新版本。')
        return dict(state='available',target=target,message='发现新版，更新后自动重启。')
    except Exception as exc:
        return dict(state='unavailable',message='暂时无法检查更新，当前版本可继续使用。',detail=str(exc))


def download(target, destination, *, opener=urlopen):
    target=validate_manifest(target)
    package=target['package'];destination=Path(destination)
    temp=destination.with_suffix('.part'); digest=hashlib.sha256();size=0
    try:
        with opener(Request(package['url'],headers={'User-Agent':'Aplus-installed-update'}),timeout=30) as response:
            with temp.open('xb') as output:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>package['bytes']:raise ValueError('更新包大小不符，已停止。')
                    digest.update(chunk);output.write(chunk)
        if size!=package['bytes'] or digest.hexdigest()!=package['sha256']:
            raise ValueError('更新包校验未通过，当前版本未改动。')
        os.replace(temp,destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def require_signature(path):
    """OS trust validation without prompts or changing Windows security policy."""
    script="""$signature=Get-AuthenticodeSignature -LiteralPath $env:APLUS_VERIFY_INSTALLER
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) { exit 1 }
"""
    env={key:value for key,value in os.environ.items() if key.casefold()!='psmodulepath'}
    env['APLUS_VERIFY_INSTALLER']=str(Path(path).resolve())
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=env,capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode!=0:
        raise ValueError('新版安装包的可信签名未通过，当前版本与数据保持不变。')


def process_alive(pid):
    kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x100000,False,int(pid))
    if not handle:
        if kernel.GetLastError()==87:return False
        raise ctypes.WinError()
    try:return kernel.WaitForSingleObject(ctypes.c_void_p(handle),0)==0x102
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))


def confirmed_start(folder, target, runtime):
    path=Path(folder)/'started.json'
    if not path.exists():return False
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    return (value.get('started') is True and value.get('version')==target['version']
            and value.get('commit')==target['commit']
            and Path(value.get('runtime','')).resolve()==Path(runtime).resolve())


def installed_process_active(executable):
    # A worker can die after Popen but before recording its child PID. Check
    # image identity as well, excluding this recovery helper itself.
    script='''$ErrorActionPreference='Stop'
$items=Get-CimInstance Win32_Process -Filter "Name='AplusApp.exe'"
foreach($item in $items) {
 if ($item.ProcessId -eq [int]$env:APLUS_RECOVERY_PID) { continue }
 if (-not $item.ExecutablePath) { throw 'Cannot establish application process identity' }
 if ([IO.Path]::GetFullPath($item.ExecutablePath) -eq $env:APLUS_RECOVERY_EXE) { exit 10 }
}
exit 0'''
    env={key:value for key,value in os.environ.items() if key.casefold()!='psmodulepath'}
    env.update(APLUS_RECOVERY_PID=str(os.getpid()),APLUS_RECOVERY_EXE=str(Path(executable).resolve()))
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=env,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode not in (0,10):raise ValueError('无法确认新版进程状态，已保留恢复现场。')
    return result.returncode==10


def recover_pending(root, *, alive=process_alive, active=installed_process_active):
    # Hold both gates through the decision/write, closing the race with a
    # second updater or an independently continuing Inno Setup process.
    with update_mutex(), update_mutex('Local\\AplusInstalledSetup'):
        return _recover_pending(root,alive=alive,active=active)


def _recover_pending(root, *, alive, active):
    """Finish a delayed real ACK, or restore code only after the new process exits."""
    root=Path(root).resolve();pending=root/'update-pending.json'
    if not pending.exists():return 0
    value=json.loads(pending.read_text(encoding='utf-8'))
    folder=Path(value['folder']).resolve()
    if folder.parent!=state_home()/'updates':raise ValueError('更新恢复位置无效。')
    request=json.loads((folder/'request.json').read_text(encoding='utf-8'))
    if Path(request['install_root']).resolve()!=root:raise ValueError('更新恢复安装身份不一致。')
    validate_manifest(request['target'])
    if confirmed_start(folder,request['target'],request['runtime']):
        atomic_json(folder/'receipt.json',dict(state='completed',version=request['target']['version']))
        pending.unlink();return 0
    pid=value.get('pid')
    if pid and alive(pid):return 2
    previous=value['previous']
    if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d+',previous):
        raise ValueError('旧版本指针无效，原数据已保留。')
    if active(root/'app'/request['target']['version']/'AplusApp.exe'):return 2
    if value.get('phase') not in ('installing','installed','launching','startup_wait'):
        return 2  # Legacy/unknown records do not prove a safe recovery point.
    executable=root/'app'/previous/'AplusApp.exe'
    if not executable.is_file():raise ValueError('旧版本文件缺失。')
    expected=value.get('previous_sha256')
    if not expected or file_sha256(executable)!=expected:
        raise ValueError('旧版本身份未能确认，已保留恢复现场。')
    # Recovery only changes this installation's pointer, never its database.
    # The two transaction gates + exact new-image check protect the operation
    # without coupling it to another source/development desktop's global lock.
    temp=root/'current.txt.new';temp.write_text(previous,encoding='ascii');os.replace(temp,root/'current.txt')
    atomic_json(folder/'receipt.json',dict(state='rolled_back',previous=previous))
    pending.unlink()
    return 0


@contextmanager
def update_mutex(name='Local\\AplusInstalledUpdate'):
    kernel=ctypes.windll.kernel32;kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise ctypes.WinError()
    try:
        if kernel.GetLastError()==183:raise ValueError('已有更新正在进行。')
        yield
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))


def worker(request_path):
    request_path=Path(request_path).resolve();folder=request_path.parent
    request=json.loads(request_path.read_text(encoding='utf-8'))
    if folder.parent!=state_home()/'updates' or request_path.name!='request.json':
        raise ValueError('更新请求位置无效。')
    root=Path(request['install_root']).resolve()
    target=validate_manifest(request['target'])
    setup=folder/'setup.exe'
    def receipt(state,**extra):atomic_json(folder/'receipt.json',dict(state=state,**extra))
    with update_mutex():
        kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=ctypes.c_void_p
        parent=kernel.OpenProcess(0x100000,False,int(request['parent_pid']))
        if not parent:raise ValueError('无法确认旧程序进程，未更新。')
        try:
            atomic_json(folder/'ready.json',{'ready':True})
            if kernel.WaitForSingleObject(ctypes.c_void_p(parent),60000)!=0:
                raise ValueError('旧程序未退出，当前版本保持不变。')
        finally:kernel.CloseHandle(ctypes.c_void_p(parent))
        previous=(root/'current.txt').read_text().strip()
        previous_exe=root/'app'/previous/'AplusApp.exe'
        process=None
        try:
            if (folder/'cancelled.json').exists():raise ValueError('本次更新已取消。')
            confirmed=load_manifest()
            if confirmed!=target:raise ValueError('正式发布已变化，请重新检查。')
            if file_sha256(setup)!=target['package']['sha256']:
                raise ValueError('下载包发生变化，未安装。')
            require_signature(setup)
            with application_mutex():
                require_web_closed()
                runtime=bound_runtime()
                if str(runtime)!=request['runtime']:raise ValueError('数据绑定已变化，未更新。')
                bind_existing(runtime,install_root=root)
            receipt('installing',previous=previous,target=target['version'])
            pending=dict(previous=previous,folder=str(folder),phase='installing',
                         previous_sha256=file_sha256(previous_exe))
            atomic_json(root/'update-pending.json',pending)
            code=subprocess.run([str(setup),'/CURRENTUSER','/SILENT','/SUPPRESSMSGBOXES','/NORESTART',
                                 '/DIR='+str(root)],timeout=900).returncode
            if code!=0:raise ValueError('新版安装未完成，正在恢复旧版本。')
            if (root/'current.txt').read_text().strip()!=target['version']:
                raise ValueError('安装版本与请求不一致。')
            pending.update(phase='installed')
            atomic_json(root/'update-pending.json',pending)
            env=dict(os.environ,APLUS_INSTALL_ROOT=str(root),APLUS_INSTALLED_ACK=str(folder/'started.json'))
            pending.update(phase='launching')
            atomic_json(root/'update-pending.json',pending)
            process=subprocess.Popen([str(root/'app'/target['version']/'AplusApp.exe')],env=env,cwd=root)
            pending.update(phase='startup_wait',pid=process.pid)
            atomic_json(root/'update-pending.json',pending)
            deadline=time.monotonic()+60
            while time.monotonic()<deadline and process.poll() is None:
                if confirmed_start(folder,target,request['runtime']):
                    receipt('completed',version=target['version'])
                    (root/'update-pending.json').unlink(missing_ok=True)
                    return
                time.sleep(.2)
            if process.poll() is None:
                receipt('startup_unconfirmed',message='新版仍在启动确认，请保留当前状态。');return
            raise ValueError('新版未能启动，正在恢复旧版本。')
        except Exception as exc:
            if process is not None and process.poll() is None:
                receipt('startup_unconfirmed',message=str(exc),pid=process.pid)
                return  # Never switch code underneath a potentially live new app.
            temp=root/'current.txt.new';temp.write_text(previous,encoding='ascii');os.replace(temp,root/'current.txt')
            receipt('rolled_back',message=str(exc),previous=previous)
            (root/'update-pending.json').unlink(missing_ok=True)
            subprocess.Popen([str(previous_exe)],cwd=root,
                env=dict(os.environ,APLUS_INSTALL_ROOT=str(root)))
