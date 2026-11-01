"""First-run Tcl lifecycle is tested in a fresh process, as in the EXE."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def assert_desktop_metrics(app):
    import ctypes
    from tkinter import font
    user=ctypes.windll.user32
    user.GetDpiForWindow.argtypes=[ctypes.c_void_p]
    dpi=user.GetDpiForWindow(app.winfo_id())
    assert dpi and abs(app.winfo_fpixels('1i')-dpi)<2
    listing=font.Font(root=app,font=app.style.lookup('Treeview','font'))
    assert app.style.lookup('Treeview','rowheight')>=listing.metrics('linespace')+4


@pytest.mark.skipif(os.name!='nt',reason='Windows DPI initialization order')
def test_setup_establishes_dpi_before_first_tcl_interpreter(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from workbench import installed_runtime as runtime
    calls=[]
    monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=SimpleNamespace(
        SetProcessDPIAware=lambda:calls.append('dpi'))))
    sentinel=object()
    from workbench import branding
    monkeypatch.setattr(branding,'apply_window_icon',lambda window:calls.append('icon') if window is sentinel else None)
    def window():calls.append('tk');return sentinel
    monkeypatch.setattr(runtime.tk,'Tk',window)
    assert runtime.setup_window() is sentinel
    assert calls==['dpi','tk','icon']


@pytest.mark.skipif(os.name!='nt',reason='Windows desktop first-run lifecycle')
@pytest.mark.parametrize('mode',['existing','new','failure','cancel','restart'])
def test_welcome_then_real_desktop(tmp_path,mode):
    script=r'''
import os,sys
from pathlib import Path
folder=Path(sys.argv[1]);mode=sys.argv[2]
os.environ['APLUS_INSTALL_STATE']=str(folder/'profile')
os.environ['A_WORKBENCH_HOME']=str(folder/'original')
from workbench.store import Store
store=Store(folder/'original')
from tools.onboarding_probe import run
if mode!='restart':
    observed=run(store.root,folder,new=mode=='new',fail=mode=='failure',cancel=mode=='cancel')
if mode in ('existing','new','restart'):
    # Import only after welcome: reproduces real installed entry ordering.
    from gui.main_window import AplusMainWindow
    from workbench.service import Service
    service=Service(store)
    app=AplusMainWindow(service=service,store=store,enable_tray=False)
    app.withdraw();app.update_idletasks()
    assert app.style.master.tk is app.tk
    from tests.test_installed_onboarding import assert_desktop_metrics
    assert_desktop_metrics(app)
    app.destroy();service.pool.shutdown(wait=True)
print('first_run_ok',mode)
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),mode],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'first_run_ok '+mode in result.stdout


def test_installed_startup_error_is_reported_after_binding(tmp_path,monkeypatch):
    from workbench import installed_runtime as runtime, strategies
    import launch_dashboard
    monkeypatch.setattr(strategies,'verify_frozen',lambda:True)
    monkeypatch.setattr(runtime,'bound_runtime',lambda:tmp_path)
    monkeypatch.setattr(launch_dashboard,'build_backend',lambda:(None,None))
    monkeypatch.setenv('A_WORKBENCH_HOME',str(tmp_path))
    def fail():
        raise SystemExit(1)
    monkeypatch.setattr(launch_dashboard,'main',fail)
    with pytest.raises(RuntimeError,match='主界面未能打开'):
        runtime.launch()


@pytest.mark.skipif(os.name!='nt',reason='Native Tk recovery confirmation')
@pytest.mark.native_process
@pytest.mark.parametrize('mode',['success','cancel','failure'])
def test_recovery_confirmation_and_background_result(tmp_path,monkeypatch,mode):
    from workbench import installed_runtime as runtime
    from unittest.mock import Mock
    monkeypatch.setattr(runtime.messagebox,'askyesno',lambda *a,**k:mode!='cancel')
    error=Mock();monkeypatch.setattr(runtime.messagebox,'showerror',error)
    def recover():
        if mode=='cancel':pytest.fail('no recovery without consent')
        if mode=='failure':raise ValueError('snapshot retained')
        return tmp_path
    monkeypatch.setattr(runtime,'recover_bound_runtime',recover)
    assert runtime.recover_data()==(tmp_path if mode=='success' else None)
    assert error.call_count==(1 if mode=='failure' else 0)
    if mode=='success':
        from workbench.store import Store
        from workbench.service import Service
        from gui.main_window import AplusMainWindow
        store=Store(tmp_path/'synthetic');service=Service(store)
        app=AplusMainWindow(service=service,store=store,enable_tray=False)
        app.withdraw();app.update_idletasks();assert app.style.master.tk is app.tk
        assert_desktop_metrics(app)
        app.destroy();service.pool.shutdown(wait=True)
