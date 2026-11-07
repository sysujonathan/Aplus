"""Candidate history is bounded by its original, immutable snapshot."""
from pathlib import Path
import sys

import pandas as pd
import pytest

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui.chart_bridge import workspace_boot
from gui.chart_embed import ChartSessionBridge
from workbench.market import save_dataset
from workbench.store import Store


def chart_fixture(root):
    store = Store(root / 'runtime')
    dates = pd.bdate_range(end='2026-11-06', periods=2450).strftime('%Y-%m-%d')
    close = pd.Series(range(len(dates))).map(lambda index: 10 + (index % 47) * .01)
    frame = pd.DataFrame(dict(date=dates, open=close-.02, high=close+.07,
                              low=close-.09, close=close, volume=1000))
    code = 'sz.000001'
    record = dict(code=code, timeframe='daily', source='test', adjustment='前复权', start=dates[0])
    record['id'] = save_dataset(store, code, frame, record['source'], record['adjustment'])
    context = dict(code=code, timeframe='daily', context_kind='candidate', record=record,
                   frame=frame.iloc[-312:-12])
    boot = workspace_boot(store, context)
    boot.update(embedded=True, sessionToken=1)
    bridge = ChartSessionBridge(store, boot, lambda _boot: None)
    bridge.active_token = 1
    return dates, context, bridge


def test_candidate_pages_keep_snapshot_cutoff_and_drawing_save(tmp_path):
    dates, context, bridge = chart_fixture(tmp_path)
    cursor = dates[-312]
    assert len(bridge.bootstrap()['bars']) == 300
    assert bridge.read_older_daily(cursor, 1)[0]['date'] == dates[-712]
    assert bridge.read_older_daily(cursor, 1)[-1]['date'] == dates[-313]
    assert len(bridge.current.rows) == 700
    assert bridge.current.rows[-1]['date'] == dates[-13]
    assert bridge.read_older_daily(dates[-712], 1)[0]['date'] == dates[-1112]
    assert len(bridge.current.rows) == 1100
    drawing = dict(id='history', type='trend', a=dict(index=0, value=10),
                   b=dict(index=1, value=11))
    bridge.save_state(dict(timeframe='daily', objects=[drawing]), session_token=1)
    document = bridge.store.chart_document(bridge.current.identity)
    assert document['drawings'][0]['points'] == [[dates[-1112], 10], [dates[-1111], 11]]
    with pytest.raises(ValueError, match='没有可读取'):
        bridge.read_older_daily(dates[-13], 1)
    bridge.pending = dict(token=2, boot=workspace_boot(bridge.store, context))
    bridge.activate_context()
    with pytest.raises(ValueError, match='已切换'):
        bridge.read_older_daily(cursor, 1)
    assert len(bridge.current.rows) == 300
    assert workspace_boot(bridge.store, dict(context, context_kind='watch'))['olderDaily'] is None


def test_changed_snapshot_does_not_extend_chart(tmp_path):
    _, _, bridge = chart_fixture(tmp_path)
    artifact, = (tmp_path / 'runtime' / 'market').glob('*.csv')
    artifact.write_bytes(artifact.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='校验不一致'):
        bridge.read_older_daily(bridge.current.rows[0]['date'], 1)
    assert len(bridge.current.rows) == 300


def test_native_candidate_pan_preserves_anchors_and_loads_to_first_bar(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows WebView2')
    result = subprocess.run([sys.executable, str(Path(__file__)), str(tmp_path)],
                            capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'pages=6' in result.stdout


def native_probe(root):
    import statistics
    import time
    import traceback
    import webview
    from gui.chart_shell import _materialize_page

    dates, _, bridge = chart_fixture(root)
    page, _ = _materialize_page(root / 'candidate.json', bridge.bootstrap())
    window = webview.create_window('Aplus isolated history paging', page.as_uri(), js_api=bridge,
                                   width=1250, height=850, hidden=True)
    failures = []

    def wait(expression):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if window.evaluate_js(expression):
                return
            time.sleep(.02)
        raise AssertionError(expression)

    def probe():
        try:
            assert window.events.loaded.wait(15)
            wait("typeof panChart === 'function' && !!window.pywebview?.api?.read_older_daily")
            assert window.evaluate_js("bars.length === 300 && visibleWindow().begin > 36")
            window.evaluate_js("""
                priceZoom=1.6; priceCenterOffset=.12;
                window.savedAnchor=bars[140].date;
                userObjects=[{id:'test-trend',type:'trend',a:{date:savedAnchor,index:140,value:10.2},
                  b:{date:bars[150].date,index:150,value:10.4},createdAtDate:savedAnchor,
                  createdAtIndex:140,visible:true,color:'#2962ff',width:1}];
                rememberObjects();
                window.recentEma=ema.at(-1);
                window.startWeekly=chartBoot.series.weekly.length;
            """)
            times = []
            for size in (700, 1100, 1500, 1900, 2300, 2438):
                window.evaluate_js("""
                    window.pageStarted=performance.now();
                    panChart(panLimits().max);
                    window.viewportAnchor=visibleWindow().data[0].date;
                """)
                wait(f'bars.length === {size} && !readingOlderDaily')
                times.append(window.evaluate_js('performance.now() - pageStarted'))
                assert window.evaluate_js("""
                    visibleWindow().data[0].date===viewportAnchor &&
                    priceZoom===1.6 && priceCenterOffset===.12 &&
                    bars[userObjects[0].a.index].date===savedAnchor &&
                    bars[undoStack.at(-1)[0].a.index].date===savedAnchor &&
                    Math.abs(ema.at(-1)-recentEma)<1e-8 &&
                    chartBoot.series.weekly.length===startWeekly
                """)
            assert window.evaluate_js(f"bars[0].date==='{dates[0]}' && bars.at(-1).date==='{dates[-13]}'")
            assert window.evaluate_js("switchProductionTimeframe('weekly',false) && switchProductionTimeframe('daily',false) && bars.length===2438")
            print(f'pages={len(times)} median_ms={statistics.median(times):.1f} '
                  f'max_ms={max(times):.1f} samples_ms={[round(value, 1) for value in times]}')
        except Exception:
            failures.append(traceback.format_exc())
        finally:
            window.destroy()

    webview.start(probe, gui='edgechromium', private_mode=True, storage_path=str(root / 'webview'))
    assert not failures, ''.join(failures)


if __name__ == '__main__':
    native_probe(Path(sys.argv[1]))
