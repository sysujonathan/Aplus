"""Candidate history is bounded by its original, immutable snapshot."""
from pathlib import Path
import sys

import pandas as pd
import pytest

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui.chart_bridge import _bars, _resample, workspace_boot
from gui.chart_embed import ChartSessionBridge
from workbench.market import load_dataset, save_dataset
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
    context = dict(code=code, timeframe='daily', context_kind='candidate', record=record, embedded=True,
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
    assert bridge.read_older_history('daily', cursor, 1)['rows'][0]['date'] == dates[-712]
    assert bridge.read_older_history('daily', cursor, 1)['rows'][-1]['date'] == dates[-313]
    assert len(bridge.current.rows) == 700
    assert bridge.current.rows[-1]['date'] == dates[-13]
    assert bridge.read_older_history('daily', dates[-712], 1)['rows'][0]['date'] == dates[-1112]
    assert len(bridge.current.rows) == 1100
    drawing = dict(id='history', type='trend', a=dict(index=0, value=10),
                   b=dict(index=1, value=11))
    bridge.save_state(dict(timeframe='daily', objects=[drawing]), session_token=1)
    document = bridge.store.chart_document(bridge.current.identity)
    assert document['drawings'][0]['points'] == [[dates[-1112], 10], [dates[-1111], 11]]
    with pytest.raises(ValueError, match='没有可读取'):
        bridge.read_older_history('daily', dates[-13], 1)
    bridge.pending = dict(token=2, boot=workspace_boot(bridge.store, context))
    bridge.activate_context()
    with pytest.raises(ValueError, match='已切换'):
        bridge.read_older_history('daily', cursor, 1)
    assert len(bridge.current.rows) == 300
    assert workspace_boot(bridge.store, dict(context, context_kind='watch'))['snapshotHistory'] is None
    detached = workspace_boot(bridge.store, dict(context, embedded=False))
    assert detached['snapshotHistory'] is None
    assert len(detached['series']['weekly']) == len(bridge.current.boot['series']['weekly']) + 1


def test_changed_snapshot_does_not_extend_chart(tmp_path):
    _, _, bridge = chart_fixture(tmp_path)
    artifact, = (tmp_path / 'runtime' / 'market').glob('*.csv')
    artifact.write_bytes(artifact.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='校验不一致'):
        bridge.read_older_history('daily', bridge.current.rows[0]['date'], 1)
    assert len(bridge.current.rows) == 300


@pytest.mark.parametrize(('unit', 'rule'), [('weekly', 'W-FRI'), ('monthly', 'ME')])
def test_calendar_pages_restore_whole_buckets_without_daily_paging(tmp_path, unit, rule):
    dates, context, bridge = chart_fixture(tmp_path)
    frame, _ = load_dataset(bridge.store, context['record']['id'])
    expected = _bars(_resample(frame[frame.date <= dates[-13]], rule))
    rows = bridge.current.boot['series'][unit]
    while True:
        page = bridge.read_older_history(unit, rows[0]['date'], 1)
        if not page['rows']:
            break
        assert len(page['rows']) <= 400
        assert set(page['closeDates']) == {row['date'] for row in page['rows']}
        rows = bridge.current.boot['series'][unit]
    assert [row['date'] for row in rows] == [row['date'] for row in expected]
    for actual, reference in zip(rows, expected):
        assert actual['open'] == pytest.approx(reference['open'])
        assert actual['close'] == pytest.approx(reference['close'])
        assert actual['volume'] == reference['volume']
    assert len(bridge.current.rows) == 300


def test_native_candidate_pan_preserves_anchors_and_loads_to_first_bar(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows WebView2')
    result = subprocess.run([sys.executable, str(Path(__file__)), str(tmp_path)],
                            capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'periods=8' in result.stdout


def native_probe(root):
    import json
    import statistics
    import time
    import traceback
    import webview
    from gui.chart_shell import _materialize_page
    from workbench.live_minutes import session_closes

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
            wait("typeof panChart === 'function' && !!window.pywebview?.api?.read_older_history")
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
                wait(f'bars.length === {size} && !readingOlderHistory')
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
            for unit in ('weekly', 'monthly'):
                assert window.evaluate_js(f"switchProductionTimeframe('{unit}',false)")
                before = window.evaluate_js('bars.length')
                window.evaluate_js('panChart(panLimits().max || 1)')
                wait(f'bars.length > {before} && !readingOlderHistory')
                assert window.evaluate_js(f"activeTimeframeUnit==='{unit}'")
            assert window.evaluate_js("switchProductionTimeframe('daily',false) && bars.length===2438")
            assert window.evaluate_js("""
                (() => {
                    const previous = chartBoot.series.daily.at(-1);
                    const day = chartBoot.axisDates.daily[chartBoot.axisDates.daily.indexOf(previous.date) + 1];
                    const oldestWeek = chartBoot.series.weekly[0].date;
                    const oldestMonth = chartBoot.series.monthly[0].date;
                    return applyDailyReference({...previous, code:chartBoot.symbol, date:day,
                        previous_close:previous.close, price:previous.close, quote_time:day+'T14:00:00+08:00'})
                        && chartBoot.series.weekly[0].date===oldestWeek
                        && chartBoot.series.monthly[0].date===oldestMonth;
                })()
            """)

            def minute_fixture(interval, days, count):
                stamps = [f'{day} {minute // 60:02d}:{minute % 60:02d}'
                          for day in days
                          for minute in sorted(session_closes(interval))]
                return [
                    dict(date=stamp, open=10, high=11, low=9, close=10, volume=100)
                    for stamp in stamps[-count:]
                ]
            series = {f'm{interval}': minute_fixture(interval, dates[-161:], 640)
                      for interval in (1, 5, 15, 30, 60)}
            packet = dict(series=series, live=dict(source='腾讯分钟行情', adjustment='不复权'))
            assert window.evaluate_js(f"window.aplusApplyLiveBars({json.dumps(packet)})")
            for unit, rows in series.items():
                assert window.evaluate_js(f"switchProductionTimeframe('{unit}',false) && bars.length===300")
                window.evaluate_js('viewOffset=panLimits().max; window.minuteAnchor=visibleWindow().data[0].date; panChart(1)')
                assert window.evaluate_js(
                    f"activeTimeframeUnit==='{unit}' && bars.length===640 && bars[0].date==='{rows[0]['date']}' "
                    "&& visibleWindow().data[0].date===minuteAnchor")
            assert window.evaluate_js("switchProductionTimeframe('m5',false)")
            series['m5'] = series['m5'][1:] + [{**series['m5'][-1], 'date': '2026-11-09 09:35'}]
            assert window.evaluate_js(f"window.aplusApplyLiveBars({json.dumps(packet)}) && bars.length===641")
            short_history = minute_fixture(15, dates[-240:-161], 320)
            series['m15'] = short_history[:212]
            assert window.evaluate_js(f"window.aplusApplyLiveBars({json.dumps(packet)})")
            series['m15'] = short_history[108:]
            assert window.evaluate_js(f"window.aplusApplyLiveBars({json.dumps(packet)})")
            assert window.evaluate_js("switchProductionTimeframe('m15',false) && bars.length===300")
            window.evaluate_js('panChart(panLimits().max)')
            assert window.evaluate_js('bars.length===320')
            print(f'periods=8 daily_pages={len(times)} median_ms={statistics.median(times):.1f} '
                  f'max_ms={max(times):.1f} samples_ms={[round(value, 1) for value in times]}')
        except Exception:
            failures.append(traceback.format_exc())
        finally:
            window.destroy()

    webview.start(probe, gui='edgechromium', private_mode=True, storage_path=str(root / 'webview'))
    assert not failures, ''.join(failures)


if __name__ == '__main__':
    native_probe(Path(sys.argv[1]))
