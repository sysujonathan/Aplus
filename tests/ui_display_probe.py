"""Actual WebView display verification; synthetic runtime, optional screenshots."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder):
    import pandas as pd
    import webview
    from gui.chart_bridge import workspace_boot
    from gui.chart_shell import _materialize_page
    from workbench.store import Store

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder / 'synthetic-runtime')
    dates = pd.bdate_range('2026-01-05', '2026-11-04').strftime('%Y-%m-%d')
    closes = [17.4 - (len(dates)-1-i)*.0084 for i in range(len(dates))]
    frame = pd.DataFrame(dict(date=dates, open=[v-.02 for v in closes],
        high=[v+.05 for v in closes], low=[v-.06 for v in closes], close=closes, volume=1000))
    boot = workspace_boot(store, dict(frame=frame, code='sh.600000', timeframe='daily',
        context_kind='watch', record=dict(source='tickflow', adjustment='前复权')))
    boot.update(embedded=True, name='合成标的·界面验收',
                dailyReference=dict(quote_time='2026-11-04T16:14:51+08:00'))
    page, _ = _materialize_page(folder / 'session.json', boot)
    screenshots = '--screenshots' in sys.argv
    window = webview.create_window('Aplus synthetic display verification', page.as_uri(),
        hidden=True, width=1400, height=850, x=30, y=30)
    failures = []
    result = {}

    def probe():
        try:
            assert window.events.loaded.wait(20)
            deadline = time.monotonic() + 15
            while not window.evaluate_js("typeof window.aplusApplyHistory==='function'"):
                if time.monotonic() > deadline:
                    raise AssertionError('production chart did not initialize')
                time.sleep(.05)
            window.evaluate_js("window.displayErrors=[];addEventListener('error',e=>displayErrors.push(e.message));")
            window.evaluate_js('priceZoom=.5;renderWorkspace();')
            result['wide'] = check_readout()
            if screenshots:
                window.evaluate_js("updateReadout(bars.find(row=>row.date==='2026-06-15'));")
                capture('chart-wide.png')
            window.resize(600, 850)
            deadline = time.monotonic() + 5
            while window.evaluate_js('innerWidth') > 650:
                assert time.monotonic() < deadline
                time.sleep(.05)
            result['narrow'] = check_readout()
            if screenshots:
                capture('chart-narrow.png')
            assert not window.evaluate_js('displayErrors')
            result['ok'] = True
        except BaseException as exc:
            failures.append(repr(exc))
        finally:
            (folder / 'result.json').write_text(json.dumps(dict(result, failures=failures),
                ensure_ascii=False, indent=2), encoding='utf-8')
            window.destroy()

    def check_readout():
        value = window.evaluate_js("""(() => {
          updateReadout(bars.find(row=>row.date==='2026-06-15'));
          const selected=document.querySelector('#readoutDate').textContent;
          const selectedClose=document.querySelector('#closeValue').textContent;
          updateReadout(bars.at(-1));
          const latest=document.querySelector('#readoutDate').textContent;
          const title=document.querySelector('.instrument-title').getBoundingClientRect();
          const ohlc=document.querySelector('.ohlc').getBoundingClientRect();
          const header=document.querySelector('.instrument-row').getBoundingClientRect();
          window.savedPriceTag=priceTag;window.displayTags=[];
          priceTag=(ctx,text,y,color,...rest)=>{displayTags.push({text,y,color});savedPriceTag(ctx,text,y,color,...rest);};
          renderWorkspace();priceTag=savedPriceTag;
          return {selected,selectedClose,latest,titleBottom:title.bottom,ohlcTop:ohlc.top,
            ohlcBottom:ohlc.bottom,headerBottom:header.bottom,tags:displayTags,
            source:document.querySelector('.instrument-title b').textContent};
        })()""")
        assert value['selected'] == '选中K线 2026-06-15'
        assert value['latest'] == '末根K线 2026-11-04'
        assert '最新参考报价时间 2026-11-04' in value['source']
        assert value['ohlcTop'] >= value['titleBottom']
        assert value['ohlcBottom'] <= value['headerBottom']
        assert len(value['tags']) == 2
        assert abs(value['tags'][0]['y'] - value['tags'][1]['y']) >= 22
        return value

    def capture(name):
        # Capture only this WebView's renderer, independent of desktop focus.
        from System import Action
        from System.IO import MemoryStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        stream = MemoryStream()
        tasks = []
        control = window.native.webview
        control.Invoke(Action(lambda: tasks.append(control.CoreWebView2.CapturePreviewAsync(
            CoreWebView2CapturePreviewImageFormat.Png, stream))))
        assert tasks[0].Wait(10000)
        (folder / name).write_bytes(bytes(stream.ToArray()))
        stream.Dispose()

    webview.start(probe, gui='edgechromium', debug=False, private_mode=True,
                  storage_path=str(folder / 'webview'))
    if failures:
        raise AssertionError('; '.join(failures))


if __name__ == '__main__':
    run(sys.argv[1])
