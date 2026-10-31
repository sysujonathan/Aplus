"""Real WebView + production IPC, synthetic runtime only (standalone child)."""
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def run(folder):
    import pandas as pd
    import webview
    from gui.chart_bridge import workspace_boot
    from gui.chart_embed import ChartSessionBridge, atomic_json, run_embedded_transport
    from gui.chart_shell import _materialize_page
    from workbench.store import Store
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    store=Store(folder/'runtime')
    frame=pd.DataFrame(dict(date=pd.bdate_range('2026-09-01',periods=42).strftime('%Y-%m-%d'),
                            open=1.2,high=1.3,low=1.1,close=1.25,volume=100))
    context=dict(frame=frame,code='sz.159570',timeframe='daily',context_kind='watch',
                 record=dict(source='baostock',adjustment='不复权'))
    boot=workspace_boot(store,context);boot['sessionToken']=1
    session=folder/'session.json';page,_=_materialize_page(session,boot)
    bridge=ChartSessionBridge(store,boot,lambda b:_materialize_page(session,b));bridge.active_token=1
    window=webview.create_window('Aplus isolated watch refresh probe',page.as_uri(),js_api=bridge,hidden=True)
    done=threading.Event();failures=[]
    def check(expression):
        value=window.evaluate_js(expression)
        if not value:raise AssertionError(expression)
        return value
    def wait(expression):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if window.evaluate_js(expression):return
            time.sleep(.05)
        raise AssertionError('WebView wait: '+expression)
    def probe():
        transport=None
        try:
            assert window.events.loaded.wait(15)
            wait("typeof window.aplusApplyHistory === 'function'")
            evaluations=[]
            original_eval=window.evaluate_js
            def evaluate(script):
                if 'window.aplusApplyHistory(' in script:evaluations.append(script)
                return original_eval(script)
            window.evaluate_js=evaluate
            def transport_run():
                try:run_embedded_transport(window,bridge,session,0,native=SimpleNamespace(fit=lambda:not done.is_set()))
                except Exception as exc:failures.append(repr(exc));done.set()
            transport=threading.Thread(target=transport_run);transport.start()
            minutes=[dict(date=f'2026-10-30 10:{n:02d}',open=1.2,high=1.3,low=1.1,close=1.25,volume=100)
                     for n in range(0,60,5)]
            atomic_json(session.with_suffix('.live.json'),dict(id='minutes',token=1,series={'m5':minutes},
                        live={'source':'腾讯分钟行情','asof':minutes[-1]['date']}))
            wait("chartBoot.series.m5?.length === 12")
            check("switchProductionTimeframe('m5',false)")
            window.evaluate_js('viewCount=6;viewOffset=3;priceZoom=1.6;')
            fresh=frame.copy();fresh.loc[len(fresh)]=['2026-10-30',1.2,1.3,1.1,1.25,100]
            new=workspace_boot(store,dict(context,frame=fresh))
            atomic_json(session.with_suffix('.history.json'),dict(id='daily-later',token=1,boot=new))
            wait("chartBoot.latest.date === '2026-10-30'")
            check("activeTimeframeUnit==='m5' && bars.length===12 && viewOffset===3 && priceZoom===1.6")
            check("chartBoot.live.source==='腾讯分钟行情'")
            check("switchProductionTimeframe('daily',false) && bars.at(-1).date==='2026-10-30'")
            check("switchProductionTimeframe('m5',false) && bars.length===12")
            assert bridge.current.rows[-1]['date']=='2026-10-30'
            # A genuine JS refusal gets one terminal receipt, never a 100ms loop.
            bad=json.loads(json.dumps(new));bad['series']['daily'][-1]['low']=999
            atomic_json(session.with_suffix('.history.json'),dict(id='bad-history',token=1,boot=bad))
            receipt=session.with_suffix('.history-status.json')
            def wait_receipt(identity):
                from gui.chart_embed import read_notification
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    value=read_notification(receipt)
                    if value and value.get('id')==identity:return value
                    time.sleep(.05)
                raise AssertionError('history receipt missing: '+identity)
            assert wait_receipt('bad-history')['accepted'] is False
            count=len(evaluations);time.sleep(.35)
            assert len(evaluations)==count
            check("activeTimeframeUnit==='m5' && bars.length===12 && chartBoot.latest.date==='2026-10-30'")
            window.evaluate_js('window.savedHistoryHandler=window.aplusApplyHistory;window.aplusApplyHistory=undefined;')
            atomic_json(session.with_suffix('.history.json'),dict(id='missing-handler',token=1,boot=new))
            assert wait_receipt('missing-handler')['accepted'] is False
            count=len(evaluations);time.sleep(.35)
            assert len(evaluations)==count and not window.events.closed.is_set()
            assert not failures,failures
            (folder/'result.json').write_text(json.dumps(dict(ok=True,real_webview=True,
                real_history_ipc=True,minute_period_preserved=True,view_preserved=True,
                daily_updated=True,rejected_once=True,missing_handler_reported=True,synthetic=True)),encoding='utf-8')
        except Exception as exc:
            failures.append(repr(exc))
        finally:
            done.set()
            if transport:transport.join(5)
            window.destroy()
    webview.start(probe,gui='edgechromium',private_mode=True,storage_path=str(folder/'webview'))
    assert not failures,failures


if __name__=='__main__':run(sys.argv[1])
