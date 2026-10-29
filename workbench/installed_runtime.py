"""Installed desktop bootstrap: explicit data ownership before Store startup."""
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from .installed_data import (state_home, bound_runtime, discover_hints, candidate_data,
                             bind_existing, bind_new)


def choose_data(install_root):
    import ttkbootstrap as ttk
    window = ttk.Window(themename='darkly')
    window.title('欢迎使用 Aplus')
    window.geometry('720x450')
    window.minsize(640, 400)
    body = ttk.Frame(window, padding=24); body.pack(fill='both', expand=True)
    ttk.Label(body, text='接续您的 Aplus 数据', font=('Microsoft YaHei',18,'bold')).pack(anchor='w')
    status = tk.StringVar(value='正在查找已有数据…')
    ttk.Label(body,textvariable=status,wraplength=660).pack(anchor='w',pady=16)
    selected = tk.StringVar()
    choice = ttk.Combobox(body,textvariable=selected,state='readonly',width=80)
    choice.pack(fill='x')
    summary = tk.StringVar(value='已有行情、关注和交易记录会保留在原位置。接续前自动备份数据库。')
    ttk.Label(body,textvariable=summary,wraplength=660).pack(anchor='w',pady=18)
    results, candidates, outcome = queue.Queue(), [], []
    working = [True]
    def describe(_event=None):
        index = choice.current()
        if index >= 0:
            item=candidates[index]; count=item['counts']
            summary.set(f"最近行情：{item['latest'] or '暂无'}　关注：{count['watchlist']}　"
                        f"账户：{count['accounts']}　持仓记录：{count['positions']}\n"
                        '接续前自动备份；原行情文件保留原位。')
    choice.bind('<<ComboboxSelected>>',describe)
    def set_candidates(items):
        candidates[:]=items
        choice.configure(values=[item['path'] for item in items])
        # Multiple libraries need explicit user selection, never pick by date.
        if len(items)==1:
            choice.current(0); describe()
        else:
            selected.set('')
        status.set('发现已有数据，请确认后接续。' if items else
                   '未自动找到已有数据。用过 Aplus 请先选择原程序或数据文件夹。')
    def browse():
        if working[0]: return
        folder=filedialog.askdirectory(parent=window,title='选择原 Aplus 程序或数据文件夹')
        if folder:
            try:
                item=candidate_data(folder)
                set_candidates([item])
            except Exception as exc:
                messagebox.showwarning('未找到可接续的数据',str(exc),parent=window)
    def start(existing):
        if working[0]: return
        index=choice.current()
        if existing and index<0:
            messagebox.showinfo('请选择数据', '请先选择您要接续的数据位置。',parent=window); return
        if not existing and not messagebox.askyesno('首次使用 Aplus',
            '确认没有需要保留的旧数据，并创建新的数据仓？\n如果以前用过 Aplus，请取消并选择原数据。',parent=window):
            return
        working[0]=True; status.set('正在保护并接续数据，请稍候…' if existing else '正在创建新数据位置…')
        def work():
            try:
                from .version_update import application_mutex, require_web_closed
                with application_mutex():
                    require_web_closed()
                    result=(bind_existing(candidates[index]['path'],install_root=install_root)
                            if existing else bind_new())
                results.put(('bound', result))
            except Exception as exc:
                results.put(('error', str(exc)))
        threading.Thread(target=work,daemon=True).start()
    actions=ttk.Frame(body);actions.pack(fill='x',side='bottom')
    ttk.Button(actions,text='选择其他位置',command=browse).pack(side='left')
    ttk.Button(actions,text='首次使用',command=lambda:start(False),bootstyle='secondary').pack(side='left',padx=12)
    ttk.Button(actions,text='保护并接续数据',command=lambda:start(True),bootstyle='success').pack(side='right',ipady=6)
    def discover():
        try: results.put(('found',discover_hints()))
        except Exception: results.put(('found',[]))
    def poll():
        try:
            kind,value=results.get_nowait(); working[0]=False
            if kind=='found': set_candidates(value)
            elif kind=='bound': outcome.append(value);window.destroy();return
            else:
                status.set('数据尚未接续，原件已保留。')
                messagebox.showerror('暂不能接续',value,parent=window)
        except queue.Empty: pass
        window.after(100,poll)
    def close():
        if working[0]:
            messagebox.showinfo('请稍候','正在检查或备份数据，完成后可关闭。',parent=window)
        else: window.destroy()
    window.protocol('WM_DELETE_WINDOW',close)
    threading.Thread(target=discover,daemon=True).start();window.after(100,poll)
    window.mainloop()
    return outcome[0] if outcome else None


def launch():
    from .strategies import verify_frozen
    verify_frozen()
    install_root=Path(os.environ.get('APLUS_INSTALL_ROOT') or Path(__import__('sys').executable).parent.parent.parent)
    try:
        runtime=bound_runtime()
    except Exception as exc:
        root=tk.Tk();root.withdraw()
        retry=messagebox.askyesno('请确认原数据位置',str(exc)+'\n\n是否重新选择原数据位置？',parent=root)
        root.destroy()
        if not retry:return
        runtime=None
    if runtime is None:
        runtime=choose_data(install_root)
    if runtime is None: return
    os.environ['A_WORKBENCH_HOME']=str(runtime)
    os.environ['APLUS_INSTALLED']='1'
    import launch_dashboard
    # Installed users must never get a debug shell after backend failure.
    original=launch_dashboard.build_backend
    def backend():
        service,store=original()
        if service is None or store is None:
            raise RuntimeError('数据或后端启动未通过，已停止打开；原数据未被空库替代。')
        return service,store
    launch_dashboard.build_backend=backend
    ack=os.environ.pop('APLUS_INSTALLED_ACK',None)
    if ack or (os.environ.get('APLUS_INSTALL_SMOKE')=='1' and os.environ.get('APLUS_INSTALL_TEST_EXIT')):
        from .installed_data import atomic_json
        path=Path(ack).resolve() if ack else None
        if path and (path.name!='started.json' or path.parent.parent!=state_home()/'updates'):
            raise ValueError('安装版启动确认位置无效。')
        from gui import main_window
        original_window=main_window.AplusMainWindow
        class ConfirmedWindow(original_window):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs)
                if path and self.service is not None and self.store is not None:
                    from .installed_update import current_release
                    release=current_release()
                    self.after_idle(lambda:atomic_json(path,dict(started=True,
                        version=release['version'],commit=release['commit'],runtime=str(self.store.root))))
                if os.environ.get('APLUS_INSTALL_SMOKE')=='1' and os.environ.get('APLUS_INSTALL_TEST_EXIT'):
                    self.after(5000,self._exit_from_tray)
        main_window.AplusMainWindow=ConfirmedWindow
    launch_dashboard.main()
