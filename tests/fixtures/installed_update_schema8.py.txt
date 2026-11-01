"""Installed release checks and out-of-process installer updates; no local Git."""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import time
from urllib.request import Request, urlopen
import uuid

from .installed_data import state_home, atomic_json, bound_runtime, bind_existing, file_sha256
from .version_update import github_json, acceptance, application_mutex, require_web_closed
from .release_identity import read_identity, validate_identity, version_label, version_parts

REPO='sysujonathan/Aplus'
PACKAGE_BASE='https://github.com/'+REPO+'/releases/download/'


def current_release():
    return read_identity()


def validate_manifest(value):
    if type(value.get('protocol')) is not int or value['protocol']!=1 or value.get('channel')!='stable':
        raise ValueError('更新信息格式不受支持。')
    if value.get('preview') is not False:
        raise ValueError('预览安装包不能作为自动更新的正式发布。')
    validate_identity(value)
    if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d+',value.get('version','')):
        raise ValueError('更新版本无效。')
    if type(value.get('sequence')) is not int or value['sequence']<1:
        raise ValueError('更新发布序号无效。')
    if not re.fullmatch('[0-9a-f]{40}',value.get('commit','')):
        raise ValueError('无法确认正式版提交。')
    package=value.get('package',{})
    url=package.get('url','')
    expected=PACKAGE_BASE+'v'+value['version']+'/AplusSetup-'+value['version']+'-x64.exe'
    if url!=expected:
        raise ValueError('更新包不是官方发布地址。')
    if not re.fullmatch('[0-9a-f]{64}',package.get('sha256','')):
        raise ValueError('更新包校验信息缺失。')
    if type(package.get('bytes')) is not int or not 0<package['bytes']<=2_000_000_000:
        raise ValueError('更新包大小无效。')
    if type(value.get('database_schema')) is not int or value['database_schema']!=8:
        raise ValueError('此更新需要单独的数据兼容升级，当前版本继续可用。')
    return value


def load_manifest(api=github_json, opener=urlopen):
    release=api('/releases/latest')
    if release.get('draft') is not False or release.get('prerelease') is not False:
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
    tag='v'+value['version']
    if release.get('tag_name')!=tag or url!=PACKAGE_BASE+tag+'/latest.json':
        raise ValueError('正式发布标签与更新清单不一致。')
    if api('/commits/'+tag)['sha']!=value['commit']:
        raise ValueError('正式发布标签与提交不一致。')
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
    prefix=''
    try:
        current=current or current_release()
        prefix=('当前版本：'+version_label(current)+'。\n') if current.get('version') else ''
        target=load_manifest(api,opener)
        if target['sequence']<=current['sequence']:
            return dict(state='current',message=prefix+'当前已是最新版本。')
        return dict(state='available',target=target,message=prefix+'发现新版 '+version_label(target)+'，更新后自动重启。')
    except Exception as exc:
        return dict(state='unavailable',message=prefix+'暂时无法检查更新，当前版本可继续使用。',detail=str(exc))


def verify_package(target,path):
    """Trust official GitHub/main acceptance, not Windows certificate possession."""
    validate_manifest(target)
    path=Path(path)
    if path.stat().st_size!=target['package']['bytes'] or file_sha256(path)!=target['package']['sha256']:
        raise ValueError('安装包大小或 SHA256 不一致，未安装。')


def verify_candidate(target,path,current):
    validate_identity(current)
    if load_manifest()!=target:
        raise ValueError('正式发布已变化，请重新检查。')
    if target['sequence']<=current['sequence']:
        raise ValueError('更新版本没有前进，拒绝降级或重复安装。')
    verify_package(target,path)


def download(target, destination, *, opener=urlopen, progress=None):
    target=validate_manifest(target)
    package=target['package'];destination=Path(destination)
    if shutil.disk_usage(destination.parent).free < package['bytes']+64*1024*1024:
        raise ValueError('下载更新的磁盘空间不足，当前版本未改动。')
    temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part');digest=hashlib.sha256();size=0
    try:
        if progress:progress(0,package['bytes'])
        with opener(Request(package['url'],headers={'User-Agent':'Aplus-installed-update'}),timeout=30) as response:
            with temp.open('xb') as output:
                while True:
                    chunk=response.read(128*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>package['bytes']:raise ValueError('更新包大小不符，已停止。')
                    digest.update(chunk);output.write(chunk)
                    if progress:progress(size,package['bytes'])
        if size!=package['bytes'] or digest.hexdigest()!=package['sha256']:
            raise ValueError('更新包校验未通过，当前版本未改动。')
        os.replace(temp,destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def confirmed_start(folder, target, runtime):
    path=Path(folder)/'started.json'
    if not path.exists():return False
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    return (value.get('started') is True and value.get('version')==target['version']
            and value.get('commit')==target['commit']
            and Path(value.get('runtime','')).resolve()==Path(runtime).resolve())


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
        version_parts(previous)  # Reject traversal before building any executable path.
        previous_exe=root/'app'/previous/'AplusApp.exe'
        process=None
        try:
            if (folder/'cancelled.json').exists():raise ValueError('本次更新已取消。')
            current=read_identity(root/'app'/previous/'_internal')
            verify_candidate(target,setup,current)
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
                    from .installed_cleanup import finish_update
                    finish_update(root,request['runtime'],folder,target)
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
