"""Real WebView2 feedback regression, with a new isolated Store and synthetic bars."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder):
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
            results['workflow'] = window.evaluate_js(r"""(() => {
              const check=(v,message)=>{if(!v)throw Error(message);};
              window.feedbackErrors=[];addEventListener('error',e=>feedbackErrors.push(e.message));
              const p=(index,value)=>({index,value,x:canvas._view.xGlobal(index),y:canvas._view.y(value)});
              magnetEnabled=false;undoStack=[];indicatorUndoStack=[];
              startTool('channel');placeDrawing(p(110,20));
              check(draftAnchor&&!draftPoints.length&&!userObjects.length,'channel first point');
              placeDrawing(p(140,22));
              check(draftPoints.length===2&&!userObjects.length,'channel waits for width point');
              placeDrawing(p(135,20));
              check(userObjects.length===1&&!activeDrawingTool,'third point commits once');
              const channel=userObjects[0];
              check(hitRegions.filter(r=>r.id===channel.id&&r.handle).length===6,'six channel handles');
              const initial=structuredClone(channel),g=channelGeometry(channel);
              const mid=(g.a.index+g.b.index)/2;
              const widthPoint=p(mid,g.at(mid)+g.delta+.5),snappedWidth=snapDrawingPoint(widthPoint);
              moveObjectFromOriginal(channel,initial,{},widthPoint,'channel-mid-1');
              check(Math.abs(channelGeometry(channel).delta-(snappedWidth.value-g.at(mid)))<1e-8,'width handle');
              const beforeCorner=structuredClone(channel),oldDelta=channelGeometry(channel).delta;
              moveObjectFromOriginal(channel,beforeCorner,{},p(115,21),'channel-a');
              check(Math.abs(channelGeometry(channel).delta-oldDelta)<1e-8,'parallel corner adjustment');
              channel.text='沿线文字验收';
              channel.textVertical='top';channel.textAlign='left';renderWorkspace('overlay');
              check(document.querySelectorAll('.chart-watermark').length===0,'no watermark');
              const r=canvas.getBoundingClientRect();
              // Synthetic DOM pointers have no OS capture. Only that platform
              // precondition is replaced; production gesture handlers run intact.
              canvas.setPointerCapture=()=>{};canvas.releasePointerCapture=()=>{};
              const fire=(type,x,y,button=0)=>canvas.dispatchEvent(new PointerEvent(type,
                {bubbles:true,clientX:r.left+x,clientY:r.top+y,button,pointerId:1}));
              const menu=()=>canvas.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2}));
              for(const phase of [0,1,2]){
                const count=userObjects.length,hist=undoStack.length;
                startTool('channel');
                if(phase>0)placeDrawing(p(90,21));
                if(phase>1)placeDrawing(p(120,22));
                fire('pointerdown',150,200,2);fire('pointerup',150,200,2);menu();
                check(!activeDrawingTool&&!draftAnchor&&!draftPoints.length,'right cancel phase '+phase);
                check(cursorMode==='arrow'&&userObjects.length===count&&undoStack.length===hist,'cancel is not a drawing/edit');
              }
              const count=userObjects.length,hist=undoStack.length;
              startTool('ruler');
              fire('pointerdown',200,250);fire('pointermove',360,340);fire('pointerup',360,340);
              check(rulerMeasurement&&!activeDrawingTool,'ruler finishes as temporary measurement');
              check(!document.querySelector('#selectedObject').classList.contains('visible'),'ruler has no settings toolbar');
              check(userObjects.length===count&&undoStack.length===hist,'ruler never enters history or objects');
              menu();check(!rulerMeasurement,'right button clears measurement');
              startTool('ruler');fire('pointerdown',210,250);fire('pointermove',350,340);fire('pointerup',350,340);
              document.querySelector('#favoriteTools').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}));
              check(!rulerMeasurement,'outside click clears measurement');
              for(const mode of ['dot','arrow','highlight','eraser']){
                startTool('cursor-'+mode);check(cursorMode===mode&&canvas.dataset.cursor===mode,'cursor '+mode);
              }
              startTool('cross',document.querySelector('[data-tool="cross"]'));
              check(cursorMode==='cross','crosshair mode');
              selectedId=channel.id;openObjectSettings();
              const modal=document.querySelector('#objectModal');
              modal.style.position='fixed';modal.style.left='5000px';modal.style.top='5000px';fitWorkspaceModal(modal);
              const bounds=modal.getBoundingClientRect();
              check(bounds.right<=innerWidth&&bounds.bottom<=innerHeight,'modal remains reachable');
              closeObjectSettings();
              startTool('trend');placeDrawing(p(80,21));placeDrawing(p(100,20));
              openObjectSettings();
              check(document.querySelector('.line-setting-row [data-setting="startStyle"]'),'inline start control');
              check(document.querySelector('.line-setting-row [data-setting="endStyle"]'),'inline end control');
              closeObjectSettings();
              return {channelStages:3,channelHandles:6,rightCancelPhases:3,temporaryRuler:true,
                cursorModes:5,endpointLayout:true,modalBounds:true,objects:userObjects.length};
            })()""")
            window.evaluate_js('window.feedbackSaved=false;savePrototypeState().then(()=>{window.feedbackSaved=!bridgeSaveFailed;});')
            deadline = time.monotonic() + 10
            while not window.evaluate_js('window.feedbackSaved'):
                assert time.monotonic() < deadline, window.evaluate_js("document.querySelector('.save-state').textContent")
                time.sleep(.05)
            saved = store.chart_document(boot['identity'])
            assert len(saved['drawings']) == 2
            assert all(d['kind'] != 'ruler' for d in saved['drawings'])
            reopened = workspace_boot(store, context)
            assert reopened['drawings'][0]['text'] == '沿线文字验收'
            results['reopened'] = True
            for width in (520, 840):
                previous = window.evaluate_js('innerWidth')
                window.resize(width, 680)
                deadline = time.monotonic() + 5
                while window.evaluate_js('innerWidth') == previous:
                    assert time.monotonic() < deadline, (
                        'native window did not resize', width, previous,
                        window.evaluate_js('({width:innerWidth,height:innerHeight,ratio:devicePixelRatio})'),
                        window.width, window.height)
                    time.sleep(.05)
                results[f'width-{width}'] = window.evaluate_js("""(() => {
                  renderWorkspace();const r=wrap.getBoundingClientRect();
                  if(Math.abs(canvas.width-r.width*devicePixelRatio)>2)throw Error('canvas width');
                  if(Math.abs(canvas.height-r.height*devicePixelRatio)>2)throw Error('canvas height');
                  if(!canvas._view||canvas._view.plotHeight<=0)throw Error('missing plot');
                  return {width:r.width,height:r.height,backing:canvas.width};
                })()""")
            assert window.evaluate_js('feedbackErrors') == []
            assert store.rows('SELECT * FROM observations') == []
            assert store.rows('SELECT * FROM plans') == []
            results['ok'] = True
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
    run(sys.argv[1])
