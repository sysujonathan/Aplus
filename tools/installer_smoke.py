"""Explicit opt-in installed GUI probe, only synthetic disposable data."""
import json
import os
from pathlib import Path
import queue
import threading
import time


def run(folder):
    if os.environ.get('APLUS_INSTALL_SMOKE')!='1':
        raise ValueError('安装包冒烟仅允许在明确隔离的测试模式运行。')
    folder=Path(folder).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    os.environ['A_WORKBENCH_HOME']=str(folder/'runtime')
    os.environ['APLUS_INSTALL_STATE']=str(folder/'installed-state')
    import pandas as pd
    from workbench.store import Store
    from workbench.market import save_dataset
    from workbench.strategies import verify_frozen
    from workbench.service import Service
    store=Store(folder/'runtime');service=Service(store)
    from tools.onboarding_probe import run as onboarding
    onboarding_result=onboarding(store.root,folder)
    from gui.main_window import AplusMainWindow
    from gui.chart_embed import EmbeddedChartHost
    frame=pd.DataFrame(dict(date=pd.bdate_range('2026-01-05',periods=160).strftime('%Y-%m-%d'),
        open=10.,high=11.,low=9.,close=10.5,volume=1000))
    save_dataset(store,'sz.000001',frame,'test','qfq')
    app=AplusMainWindow(service=service,store=store,enable_tray=False)
    app.version_controller.destroyed(type('Event',(),{'widget':app})())
    # Never contact a supplier or official releases during this synthetic probe.
    host=EmbeddedChartHost(app,store)
    app.update()
    host.show(dict(frame=frame,code='sz.000001',timeframe='daily',context_kind='candidate',
                   record=dict(source='test',adjustment='qfq')))
    deadline=time.monotonic()+50
    closing=[False]
    def finish():
        if closing[0]:
            if host.closed():
                host.cleanup();app.destroy();return
            if time.monotonic()>deadline:
                raise RuntimeError('测试图表未正常退出，保留诊断现场')
            app.after(100,finish);return
        if host.error:
            (folder/'result.json').write_text(json.dumps(dict(ok=False,error=str(host.error))))
            host.request_close();app.destroy();return
        if host.status.exists():
            status=json.loads(host.status.read_text())
            if status.get('ready'):
                (folder/'result.json').write_text(json.dumps(dict(ok=True,frozen=verify_frozen(),
                    webview_ready=True,onboarding_success=onboarding_result['success'],
                    synthetic=True,window=app.title())))
                host.request_close()
                closing[0]=True;app.after(100,finish);return
        if time.monotonic()>deadline:
            (folder/'result.json').write_text(json.dumps(dict(ok=False,error='WebView readiness timeout')))
            host.request_close();app.destroy();return
        app.after(100,finish)
    app.after(100,finish);app.mainloop();service.pool.shutdown(wait=True)
