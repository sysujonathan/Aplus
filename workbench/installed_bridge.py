"""Schema-eight bootstrap only. Business UI and schema-nine Store run in primary/."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from .installed_data import state_home, bound_runtime, inspect_data, atomic_json
from .release_identity import read_identity, version_parts


def forward(primary,args):
    # Stay alive until the actual child exits. The legacy helper must not see
    # its launched process exit before the real primary desktop can ACK.
    return subprocess.call([str(primary),*args],env=dict(os.environ),cwd=primary.parent)


def fallback(root,runtime):
    from .installed_update import update_mutex
    if inspect_data(runtime)['schema']!=8:return
    with update_mutex():
        if (root/'update-pending.json').exists():return
        previous=(root/'previous.txt').read_text().strip();version_parts(previous)
        old=read_identity(root/'app'/previous/'_internal')
        if old.get('database_schema')!=8 or old.get('component')=='schema_bridge':return
        atomic_json(state_home()/'bridge-last-error.json',dict(previous=previous,runtime=str(runtime)))
        temp=root/'current.txt.new';temp.write_text(previous,encoding='ascii');os.replace(temp,root/'current.txt')
        subprocess.Popen([str(root/'app'/previous/'AplusApp.exe')],cwd=root,
                         env=dict(os.environ,APLUS_INSTALL_ROOT=str(root)))


def main():
    sys.dont_write_bytecode=True
    app=Path(sys.executable).resolve().parent
    if not getattr(sys,'frozen',False):
        app=Path(os.environ['APLUS_BRIDGE_APP']).resolve()  # Isolated development probe only.
    bridge=read_identity(app/'_internal');primary=app/'primary'/'AplusApp.exe'
    root=app.parent.parent
    os.environ['APLUS_INSTALL_ROOT']=str(root)
    if sys.argv[1:2]==['--bridge-self-test']:
        from .installed_bundle import verify_payload
        from .installed_data import file_sha256
        target=dict(bridge,upgrade=dict(protocol=1,from_schema=8,to_schema=9,
                    entry='primary/AplusApp.exe',inventory_sha256=file_sha256(app/'installed-files.json')))
        assert verify_payload(app,target)==primary
        Path(sys.argv[2]).write_text(json.dumps(dict(bridge_schema=8,primary_schema=9,verified=True)),encoding='utf-8')
        return 0
    if sys.argv[1:]:return forward(primary,sys.argv[1:])
    runtime=bound_runtime()
    probe=os.environ.get('APLUS_SCHEMA_BRIDGE_PROBE')
    if probe:
        from tools.schema_bridge_probe import install_authority
        install_authority(probe,root,runtime)
    if runtime is None or inspect_data(runtime)['schema']==9:
        return forward(primary,[])
    if os.environ.get('APLUS_MIGRATION_RECOVERY')=='1':
        raise ValueError('数据恢复状态需要核对，已保留原件和备份，未重复迁移。')
    if inspect_data(runtime)['schema']!=8:
        raise ValueError('没有经过验证的升级路径，原数据已保留。')
    from .version_update import application_mutex
    with application_mutex():
        return handoff_window(app,bridge,primary,root,runtime,probe)


def handoff_window(app,bridge,primary,root,runtime,probe):
    import tkinter as tk
    from tkinter import ttk,messagebox
    window=tk.Tk();window.title('Aplus 正在安全更新');window.geometry('570x190')
    status=tk.StringVar(value='正在接续已下载的新版程序，行情和交易记录保留原位…')
    ttk.Label(window,textvariable=status,wraplength=520).pack(padx=24,pady=25)
    bar=ttk.Progressbar(window,mode='indeterminate');bar.pack(fill='x',padx=24);bar.start(15)
    window.protocol('WM_DELETE_WINDOW',lambda:status.set('正在保护升级，请稍候。完成后会自动打开 Aplus。'))
    results=queue.Queue();phase_one=os.environ.pop('APLUS_INSTALLED_ACK',None);started=[False];waiting=[None]
    def work():
        try:
            from .installed_update import load_manifest
            from .installed_bundle import verify_payload,canonical_manifest
            target=load_manifest()
            bundle=canonical_manifest(target)
            if (bundle is target or any(bundle.get(k)!=bridge.get(k) for k in ('version','sequence','commit','preview'))):
                raise ValueError('正式发布已变化，已保留原数据，请稍后重试更新。')
            verify_payload(app,target)
            folder=state_home()/'updates'/uuid.uuid4().hex;folder.mkdir(parents=True)
            atomic_json(folder/'request.json',dict(target=target,parent_pid=os.getpid(),runtime=str(runtime),
                install_root=str(root),bundle_handoff=True))
            command='--installed-update-probe' if probe else '--installed-update'
            subprocess.Popen([str(primary),command,str(folder/'request.json')],cwd=root,
                             env=dict(os.environ),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            results.put(('waiting',folder))
        except Exception as exc:
            from .installed_update import network_failure_message
            results.put(('error',network_failure_message(exc) or str(exc)))
    def poll():
        if not started[0]:
            try:
                from .installed_update import update_mutex
                with update_mutex():
                    if not (root/'update-pending.json').exists():
                        started[0]=True;threading.Thread(target=work,daemon=True).start()
                        status.set('正在校验新版及升级备份，随后自动打开 Aplus…')
            except ValueError:pass  # The legacy helper is still finishing its genuine startup receipt.
        try:
            kind,value=results.get_nowait()
            if kind=='error':
                messagebox.showerror('本次更新未完成，原数据已保留',value,parent=window)
                try:fallback(root,runtime)
                finally:window.destroy()
                return
            waiting[0]=(value,time.monotonic()+30)
        except queue.Empty:pass
        if waiting[0]:
            folder,deadline=waiting[0]
            if (folder/'ready.json').exists():window.destroy();return
            if time.monotonic()>deadline:
                atomic_json(folder/'cancelled.json',dict(cancelled=True))
                results.put(('error','升级助手未就绪，原数据未修改。'));waiting[0]=None
        window.after(150,poll)
    def ready():
        if not window.winfo_ismapped():
            window.after(50,ready)
            return
        if phase_one:
            ack=Path(phase_one).resolve()
            if ack.name!='started.json' or ack.parent.parent!=state_home()/'updates':
                raise ValueError('升级引导确认位置无效。')
            # This is the real schema-eight bootstrap desktop, not the schema-nine business UI.
            atomic_json(ack,dict(started=True,version=bridge['version'],commit=bridge['commit'],
                runtime=str(runtime),database_schema=8,component='schema_bridge'))
        window.after(150,poll)
    window.after_idle(ready);window.mainloop()
    return 0
