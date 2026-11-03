"""Real WebView2, renderer, JS editor and Store. Synthetic isolated data only."""
import base64
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder, manual=False):
    import pandas as pd
    import webview
    from gui.chart_bridge import ChartBridge, workspace_boot, refresh_workspace_boot
    from gui.chart_shell import _materialize_page
    from workbench.store import Store
    from workbench.live_minutes import coarse_series

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder/'runtime')
    days = pd.bdate_range('2026-10-12', '2026-10-30').strftime('%Y-%m-%d').tolist()
    daily = pd.DataFrame([dict(date=day, open=10, high=11, low=9, close=10, volume=1000) for day in days])
    minutes = []
    for day in days[-5:]:
        for n in range(5, 241, 5):
            t = 570+n if n <= 120 else 780+n-120
            close = 10 + (n % 35)*.004
            minutes.append(dict(date=f'{day} {t//60:02d}:{t%60:02d}:00', open=close-.02,
                                high=close+.08, low=close-.06, close=close, volume=100))
    context = dict(frame=daily, minute_series=pd.DataFrame(minutes), code='sz.159570', timeframe='daily',
                   context_kind='watch', record=dict(source='test', adjustment='不复权'))
    context['hour_series'] = coarse_series(None, context['minute_series'], 60)
    context['halfday_series'] = coarse_series(None, context['minute_series'], 240)
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    session = folder/'session.json'
    page, _ = _materialize_page(session, boot)
    window = webview.create_window('Aplus isolated minute drawing acceptance', page.as_uri(), js_api=bridge,
                                   width=1280, height=900, hidden=not manual)
    failures = []

    def wait(expression):
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            if window.evaluate_js(expression):
                return
            time.sleep(.05)
        raise AssertionError(expression)

    def check(expression):
        assert window.evaluate_js(expression), expression

    def saved():
        window.evaluate_js('window.probeSaved=false;savePrototypeState(true).then(()=>window.probeSaved=!bridgeSaveFailed);')
        wait('window.probeSaved')

    def snapshot(name):
        data = window.evaluate_js("""(()=>{const out=document.createElement('canvas');out.width=canvas.width;out.height=canvas.height;
            const ctx=out.getContext('2d');for(const id of ['chartBaseCanvas','chartCanvas','chartForegroundCanvas'])
            ctx.drawImage(document.getElementById(id),0,0);return out.toDataURL('image/png').split(',')[1];})()""")
        (folder/name).write_bytes(base64.b64decode(data))

    def probe():
        try:
            assert window.events.loaded.wait(15)
            wait("typeof placeDrawing==='function' && Boolean(canvas._view) && Boolean(window.pywebview?.api?.save_state)")
            check("switchProductionTimeframe('m5',false)")
            window.evaluate_js('magnetEnabled=false;')
            kinds = ['fib', 'long', 'ray', 'trend', 'short', 'rectangle', 'up', 'down', 'arrow', 'path', 'channel']
            for kind in kinds:
                window.evaluate_js(f"startTool('{kind}');placeDrawing({{index:210,value:10,x:300,y:220}});")
                if kind == 'path':
                    window.evaluate_js('placeDrawing({index:216,value:10.1,x:360,y:200});finishPath();')
                elif kind not in ('long', 'short', 'ray', 'up', 'down'):
                    window.evaluate_js('placeDrawing({index:222,value:10.2,x:450,y:200});')
                    if kind == 'channel':
                        window.evaluate_js('placeDrawing({index:216,value:10.3,x:360,y:180});')
                check(f"userObjects.at(-1).type==='{kind}' && drawingVisibleInWorkspace(userObjects.at(-1))")
            saved()
            assert len(store.chart_document(boot['identity'])['drawings']) == 11
            original = window.evaluate_js("JSON.stringify(userObjects.map(o=>({id:o.id,a:[o.a.date,o.a.value],b:[o.b.date,o.b.value],c:o.c&&[o.c.date,o.c.value],points:o.points?.map(p=>[p.date,p.value]),stop:o.stop})))")
            window.evaluate_js('window.probeCoordinates='+json.dumps(original)+';')
            for unit in ('daily', 'weekly', 'monthly', 'm60', 'm240', 'm5'):
                check(f"switchProductionTimeframe('{unit}')")
                saved()
                check("JSON.stringify(userObjects.map(o=>({id:o.id,a:[o.a.date,o.a.value],b:[o.b.date,o.b.value],c:o.c&&[o.c.date,o.c.value],points:o.points?.map(p=>[p.date,p.value]),stop:o.stop})))===window.probeCoordinates")
            # Actual undo/redo handlers across axes retain all timestamps.
            window.evaluate_js("document.querySelector('#undoAction').click();")
            check('userObjects.length===10')
            window.evaluate_js("document.querySelector('#redoAction').click();")
            check('userObjects.length===11')
            saved()
            # Source/adjustment changes never silently reprice support levels.
            check("switchProductionTimeframe('daily',false)")
            window.evaluate_js("chartBoot.adjustment='前复权';renderWorkspace();")
            check("!document.querySelector('#drawingBasisNote').hidden")
            window.evaluate_js("chartBoot.adjustment='不复权';renderWorkspace();")
            check("document.querySelector('#drawingBasisNote').hidden")
            # Recreate the real page from the saved document, then switch again.
            refreshed = refresh_workspace_boot(store, boot)
            assert len(refreshed['drawings']) == 11
            page, _ = _materialize_page(folder/'reopened.json', refreshed)
            source = page.read_text(encoding='utf-8')
            page.write_text(source.replace('<head>', '<head><script>window.probeErrors=[];window.onerror=(...args)=>window.probeErrors.push(String(args[0])+":"+args[2]);</script>'), encoding='utf-8')
            window.evaluate_js('window.probeOldPage=true;')
            window.load_url(page.as_uri())
            wait("!window.probeOldPage && typeof placeDrawing==='function' && userObjects.length===11 && Boolean(canvas._view)")
            check("switchProductionTimeframe('m5',false)")
            check("userObjects[0].a.date==='2026-10-30 11:05:00' && drawingVisibleInWorkspace(userObjects[0])")
            # Inspect uncluttered channel geometry, including old daily anchors.
            window.evaluate_js("userObjects.forEach(o=>o.visible=o.type==='channel');renderWorkspace();")
            snapshot('minute-channel.png')
            check("switchProductionTimeframe('daily',false)")
            window.evaluate_js("startTool('channel');placeDrawing({index:0,value:9.8,x:250,y:400});placeDrawing({index:4,value:9.9,x:450,y:350});placeDrawing({index:2,value:10.1,x:350,y:300});userObjects.at(-1).extendRight=true;renderWorkspace();")
            saved()
            snapshot('daily-channel.png')
            check("switchProductionTimeframe('m5',false)")
            check('userObjects.at(-1).a.index<userObjects.at(-1).b.index && userObjects.at(-1).b.index<0')
            snapshot('daily-channel-on-minutes.png')
            if manual:
                (folder/'ready.json').write_text(json.dumps(dict(ready=True, synthetic=True)), encoding='utf-8')
                deadline = time.monotonic()+240
                while time.monotonic()<deadline and not (folder/'finish').exists():
                    time.sleep(.25)
            (folder/'result.json').write_text(json.dumps(dict(ok=True, real_webview=True, real_store=True,
                all_tools=11, cross_period_undo=True, reopened=True, daily_channel_outside_minutes=True,
                price_basis_notice=True, synthetic=True)), encoding='utf-8')
        except Exception as exc:
            try:
                (folder/'failure.json').write_text(json.dumps(window.evaluate_js("({errors:window.probeErrors, objects:typeof userObjects==='undefined'?'undefined':userObjects.length, boot:window.APLUS_CHART_BOOT?.drawings?.length, text:document.body.innerText.slice(-600)})")), encoding='utf-8')
            except Exception:
                pass
            failures.append(repr(exc))
        finally:
            window.destroy()
    webview.start(probe, gui='edgechromium', private_mode=True, storage_path=str(folder/'webview'))
    assert not failures, failures


if __name__ == '__main__':
    run(sys.argv[1], '--manual' in sys.argv)
