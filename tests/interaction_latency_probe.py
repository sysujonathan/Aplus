"""Manual latency measurements, synthetic Store only; no provider or real ledger.

Run python tests/interaction_latency_probe.py <isolated-folder> [--hold].
Timing is evidence, not a machine-dependent pytest pass threshold.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder, hold=False):
    import math
    import pandas as pd
    import webview
    from gui.chart_bridge import ChartBridge, workspace_boot
    from gui.chart_shell import _materialize_page
    from workbench.store import Store

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder / 'isolated-store')
    dates = pd.bdate_range('2026-01-05', periods=180).strftime('%Y-%m-%d')
    values = [20 + math.sin(i / 10) + i / 100 for i in range(len(dates))]
    frame = pd.DataFrame(dict(date=dates, open=values, close=values,
        high=[v + .3 for v in values], low=[v - .3 for v in values], volume=1000))
    context = dict(code='sh.600000', name='合成标的·使用反馈验收', timeframe='daily',
        frame=frame, record=dict(source='synthetic-feedback', adjustment='qfq'))
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    page, _ = _materialize_page(folder / 'chart.json', boot)
    window = webview.create_window('Aplus isolated feedback verification', page.as_uri(),
        js_api=bridge, hidden=False, width=1100, height=760, min_size=(200, 200))
    results, failures = {}, []

    def probe():
        try:
            assert window.events.loaded.wait(20)
            deadline = time.monotonic() + 15
            while not window.evaluate_js("typeof setChartCursor==='function'&&!!canvas._view&&typeof window.pywebview?.api?.save_state==='function'"):
                assert time.monotonic() < deadline, 'chart did not initialize'
                time.sleep(.05)
            results['latency'] = window.evaluate_js(r"""(() => {
              const timing=(name,run)=>{const start=performance.now();run();return {[name]:performance.now()-start};};
              const rows=Array.from({length:640},(_,i)=>({date:`2026-10-${String(1+Math.floor(i/48)).padStart(2,'0')} ${String(9+Math.floor((i%48)*5/60)).padStart(2,'0')}:${String(i%12*5).padStart(2,'0')}`,open:20,high:21,low:19,close:20,volume:100}));
              chartBoot.series.m5=rows;
              const out={...timing('to_m5_ms',()=>switchProductionTimeframe('m5'))};
              activeDrawingTool=null;magnetEnabled=false;userObjects=[];selectedId=null;
              const capture=canvas.setPointerCapture,release=canvas.releasePointerCapture;
              canvas.setPointerCapture=()=>{};canvas.releasePointerCapture=()=>{};
              const r=canvas.getBoundingClientRect(),v=canvas._view;
              const x=v.left+v.plotWidth*.6,y=v.top+v.plotHeight*.6;
              const fire=(type,dx=0)=>canvas.dispatchEvent(new PointerEvent(type,{bubbles:true,clientX:r.left+x+dx,clientY:r.top+y,button:0,pointerId:1}));
              const before=viewOffset;fire('pointerdown');fire('pointermove',100);fire('pointerup',100);
              out.first_drag_moved=viewOffset!==before;out.gesture=gesture;
              startTool('fib');placeDrawing({index:580,value:19});placeDrawing({index:620,value:21});
              Object.assign(out,timing('fib_settings_ms',()=>openObjectSettings()));closeObjectSettings();
              Object.assign(out,timing('to_daily_ms',()=>switchProductionTimeframe('daily')));
              Object.assign(out,timing('to_m5_with_drawing_ms',()=>switchProductionTimeframe('m5')));
              canvas.setPointerCapture=capture;canvas.releasePointerCapture=release;
              if(!out.first_drag_moved)throw Error('first synthetic drag did not pan');
              return out;
            })()""")
            if hold:
                window.evaluate_js("switchProductionTimeframe('m5');userObjects=[];selectedId=null;viewCount=80;viewOffset=0;renderWorkspace();")
                (folder/'ready').write_text('synthetic only',encoding='utf-8')
                time.sleep(45)
                results['desktop_drag'] = window.evaluate_js('({offset:viewOffset,gesture:gesture?.mode||null})')
            results['ok']=True
        except BaseException as exc:
            import traceback
            failures.append(traceback.format_exc())
        finally:
            (folder / 'result.json').write_text(json.dumps(dict(results, failures=failures),
                ensure_ascii=False, indent=2), encoding='utf-8')
            window.destroy()

    webview.start(probe, gui='edgechromium', debug=False, private_mode=True,
        storage_path=str(folder / 'webview'))
    if failures:
        raise AssertionError('; '.join(failures))


if __name__ == '__main__':
    run(sys.argv[1], hold='--hold' in sys.argv[2:])
