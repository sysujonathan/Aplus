"""First-run Tcl lifecycle is tested in a fresh process, as in the EXE."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows desktop first-run lifecycle')
@pytest.mark.parametrize('mode',['existing','new','failure','cancel'])
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
observed=run(store.root,folder,new=mode=='new',fail=mode=='failure',cancel=mode=='cancel')
if mode in ('existing','new'):
    # Import only after welcome: reproduces real installed entry ordering.
    from gui.main_window import AplusMainWindow
    from workbench.service import Service
    service=Service(store)
    app=AplusMainWindow(service=service,store=store,enable_tray=False)
    app.withdraw();app.update_idletasks()
    assert app.style.master.tk is app.tk
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
        app.destroy();service.pool.shutdown(wait=True)
