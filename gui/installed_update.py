"""Minimal installed UX: check quietly, one click, wait for work, restart."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
from tkinter import messagebox
import ttkbootstrap as ttk

from workbench import installed_update as update
from workbench.installed_data import state_home, atomic_json
from .version_update import open_forms


class InstalledVersionController:
    def __init__(self,window,parent):
        self.window=window;self.results=queue.Queue();self.latest=None
        self.busy=False;self.queued=False;self.reserved=False;self.waiting=None
        self.button=ttk.Button(parent,text='检查更新',width=14,command=self.manual_check,bootstyle='secondary')
        self.button.pack(side='right',padx=(8,0),ipady=2)
        self.poll_id=window.after(200,self.poll)
        self.check_id=window.after(4000,self.scan_hint)
        self.last_check=0
        window.bind('<Destroy>',self.destroyed,add='+')

    def scan_hint(self):
        if time.monotonic()-self.last_check>6*3600:self.check(False)

    def manual_check(self):
        if self.busy or self.queued or self.waiting:return
        if self.latest and self.latest['state']=='available':
            self.queued=True;self.button.configure(text='等待空闲后更新');return
        self.check(True)

    def check(self,manual):
        if self.busy:return
        self.busy=True;self.last_check=time.monotonic()
        self.button.configure(text='检查更新…')
        def work():self.results.put(('check',update.check_update(),manual))
        threading.Thread(target=work,daemon=True).start()

    def idle(self):
        service=self.window.service
        return service and not open_forms(self.window) and not service.cancel_flags and not service._code_update_reserved and not service.store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')")

    def begin(self):
        self.queued=False
        try:
            service=self.window.service
            service.reserve_code_update();self.reserved=True
            self.busy=True;self.button.configure(text='下载更新…',bootstyle='warning')
            def work():
                try:
                    target=update.load_manifest()
                    if target['sequence']<=update.current_release()['sequence']:
                        raise ValueError('当前已是最新版本。')
                    folder=state_home()/'updates'/uuid.uuid4().hex;folder.mkdir(parents=True)
                    update.download(target,folder/'setup.exe')
                    update.verify_package(target,folder/'setup.exe')
                    self.results.put(('download',(folder,target),False))
                except Exception as exc:self.results.put(('error',str(exc),False))
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:self.fail(str(exc))

    def fail(self,message):
        self.busy=False;self.waiting=None;self.window._update_pending=False
        if self.reserved:
            self.window.service.release_code_update();self.reserved=False
        self.button.configure(text='一键更新',bootstyle='success')
        self.window.deiconify()
        messagebox.showinfo('当前版本继续使用',message,parent=self.window)

    def poll(self):
        if getattr(self.window,'_closing',False):return
        try:
            kind,result,manual=self.results.get_nowait()
            if kind=='check':
                self.busy=False;self.latest=result
                available=result['state']=='available'
                self.button.configure(text='一键更新' if available else '检查更新',
                                      bootstyle='success' if available else 'secondary')
                if manual:messagebox.showinfo('软件更新',result['message'],parent=self.window)
            elif kind=='error':self.fail(result)
            elif kind=='download':
                folder,target=result
                if open_forms(self.window):
                    raise ValueError('更新包已下载。请先保存并关闭编辑窗口，再点击更新。')
                flush=getattr(self.window,'_flush_embedded_charts',lambda:True)
                if not flush():raise ValueError('图表仍在保存，请稍后再更新。')
                request=dict(target=target,parent_pid=os.getpid(),runtime=str(self.window.store.root),
                    install_root=os.environ['APLUS_INSTALL_ROOT'])
                atomic_json(folder/'request.json',request)
                subprocess.Popen([sys.executable,'--installed-update',str(folder/'request.json')],
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.window._update_pending=True
                self.waiting=(folder,time.monotonic()+15)
                self.button.configure(text='准备重启…')
        except queue.Empty:pass
        except Exception as exc:self.fail(str(exc))
        if self.waiting:
            folder,deadline=self.waiting
            if (folder/'ready.json').exists():
                try:self.window._close_for_version_update();return
                except Exception as exc:
                    atomic_json(folder/'cancelled.json',{'cancelled':True});self.fail(str(exc))
            elif time.monotonic()>deadline:
                atomic_json(folder/'cancelled.json',{'cancelled':True});self.fail('更新助手未能就绪，请稍后重试。')
        if self.queued and not self.busy and self.idle():self.begin()
        if not self.busy and not self.queued and time.monotonic()-self.last_check>6*3600:self.check(False)
        self.poll_id=self.window.after(200,self.poll)

    def destroyed(self,event):
        if event.widget is self.window:
            for token in (self.poll_id,self.check_id):
                try:self.window.after_cancel(token)
                except Exception:pass
