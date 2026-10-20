"""Static contract checks for the reviewed TradingView-style prototype."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import json

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / 'docs' / 'prototypes' / 'tv-workspace'


def test_production_tool_geometry_and_blind_rendering_in_javascript():
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript 行为回归需要 Node.js')
    result = subprocess.run([node, str(ROOT / 'tests' / 'chart_tools_regression.cjs')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['ok']


def test_production_cross_timeframe_behavior_in_javascript(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript 行为回归需要 Node.js（CI Windows 镜像提供）')
    result = subprocess.run([node, str(ROOT / 'tests' / 'chart_timeframe_regression.cjs')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert output['ok']
    # Feed the actual JS snapshots into the actual Python/Store boundary.
    import pandas as pd
    from gui.chart_bridge import ChartBridge, workspace_boot
    from workbench.store import Store

    frame = pd.DataFrame([dict(date=date, open=10, high=11, low=9, close=10, volume=100)
                          for date in output['dates']])
    store = Store(tmp_path / 'js-store')
    context = dict(frame=frame, record={'source': 's'}, code='sz.000001', timeframe='daily')
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    expected = None
    for state in output['states']:
        bridge.save_state(state)
        stored = store.chart_document(boot['identity'])
        coordinates = json.dumps([(obj['points'], obj['created_at']) for obj in stored['drawings']])
        if expected is None:
            expected = coordinates
        assert coordinates == expected
    reopened = workspace_boot(store, context)
    assert reopened['drawings'][0]['a']['date'] == '2026-01-06'
    assert reopened['drawings'][0]['a']['value'] == 10.123456789


class _PrototypeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.remote_assets = []
        self.minute_buttons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get('id'):
            self.ids.add(values['id'])
        url = values.get('src') or values.get('href')
        if url and re.match(r'https?://', url):
            self.remote_assets.append(url)
        if tag == 'button' and 'disabled' in values.get('class', '').split():
            self.minute_buttons.append(values.get('data-unavailable'))


def test_prototype_is_offline_and_contains_reviewed_states():
    parser = _PrototypeParser()
    parser.feed((PROTOTYPE / 'index.html').read_text(encoding='utf-8'))

    assert not parser.remote_assets
    assert {
        'chartCanvas', 'rightPanel', 'alertModal', 'objectModal',
        'replayBar', 'replayStartModal', 'replayExitModal',
        'chartNavControls', 'snapshotModal', 'removeModal',
    } <= parser.ids
    assert parser.minute_buttons == ['当前未接入分钟行情'] * 3


def test_prototype_embeds_public_daily_bars_and_gap_visual_contract():
    script = (PROTOTYPE / 'prototype.js').read_text(encoding='utf-8')
    interactions = (PROTOTYPE / 'prototype-interactions.js').read_text(encoding='utf-8')
    style = (PROTOTYPE / 'prototype.css').read_text(encoding='utf-8')
    history = (PROTOTYPE / 'historical-bars-v2.js').read_text(encoding='utf-8')

    assert '2026-06-22' in script and '2026-09-30' in script
    assert script.count('"date":"2026-') >= 70
    assert '2025-10-20' in history and '2026-06-18' in history
    assert "ctx.setLineDash([6, 4])" in script
    assert "ctx.strokeStyle = '#cbd5e1'" in script
    assert "callout(ctx, 'Gap'," in script
    assert '--blue: #2962ff' in style
    assert '5分钟行情接入后可用' in (PROTOTYPE / 'index.html').read_text(encoding='utf-8')
    assert "ctx.setLineDash([6, 4])" in interactions
    assert "ctx.strokeStyle = '#cbd5e1'" in interactions
    assert "callout(ctx, 'Gap'," in interactions


def test_prototype_contains_tradingview_interaction_contract():
    html = (PROTOTYPE / 'index.html').read_text(encoding='utf-8')
    script = (PROTOTYPE / 'prototype-interactions.js').read_text(encoding='utf-8')
    style = (PROTOTYPE / 'prototype.css').read_text(encoding='utf-8')

    assert 'id="zoomOut"' in html
    assert 'id="favoriteDragHandle"' in html
    assert 'href="#i-polyline"' in html
    assert 'data-tab="coordinates"' in html
    assert 'data-tab="visibility"' in html
    assert 'event.deltaY' in script and 'anchorIndex' in script
    assert "mode: 'price-scale'" in script
    assert "priceZoom = clamp" in script
    assert "localStorage.setItem(tvStateKey" in script
    assert "hitObject(point.x, point.y)" in script
    assert "openObjectSettings()" in script
    assert "mode: 'zoom-box'" in script
    assert "mode: region.handle ? 'object-handle' : 'object-move'" in script
    assert 'snapDrawingPoint' in script and 'row.open, row.high, row.low, row.close' in script
    assert 'exitDrawingMode()' in script
    assert '选择完整色板' in script
    assert 'chartPaletteColors' in (PROTOTYPE / 'chart-palette.js').read_text(encoding='utf-8')
    assert 'data-stroke-preview="middle"' in script
    assert 'data-stroke-preview="background"' in script
    assert 'data-setting="backgroundAlpha"' not in script
    assert 'data-setting="middleWidth"' not in script
    assert 'middleAlpha' in script
    assert 'aria-label="通道背景颜色和不透明度"' in (PROTOTYPE / 'drawing-settings.js').read_text(encoding='utf-8')
    assert 'value="enter_channel"' in html and 'value="rise_pct"' in html
    assert 'id="alertChannelFields"' in html and 'id="alertMovementFields"' in html
    assert 'syncAlertConditionFields' in script and 'upper, lower, lookback' in script
    assert "object.type === 'up' || object.type === 'down'" in script
    assert 'ClipboardItem' in script and 'navigator.clipboard.write' in script
    assert 'DEFAULT_VISIBLE_BARS = 204' in script
    assert 'RIGHT_MARGIN_BARS = 12' in script
    assert "gesture.startOffset + Math.round(dx / candle)" in script
    assert 'resetChartView' in script and 'navReset' in html
    assert 'replaySpeeds = [10, 7, 5, 3, 1, .5, .3, .2, .1]' in script
    assert 'replayStartDate' in html and 'replaySpeedMenu' in html
    assert 'drawReplayBoundary' in script and 'createdAtIndex' in script
    assert 'fibLevelDefaults' in script and '[4.764, false' in script
    assert 'data-fib-level-color' in script and 'data-fib-setting="fibExtend"' in script
    assert 'data-coordinate-field="index"' in script
    assert 'fibVisibilityDefaults' in script and 'data-fib-visibility-bound' in script
    assert 'saveFibTemplate' in script and 'loadFibTemplate' in script
    assert '>可见范围</button>' in html
    assert '.fib-level-grid' in style and '.fib-visibility-row' in style
    assert '.favorite-tools .drag-handle' in style
    assert '.chart-nav-controls.visible' in style
    assert 'cursor: ns-resize' in style


def test_prototype_is_also_bound_to_the_production_store_bridge():
    html = (PROTOTYPE / 'index.html').read_text(encoding='utf-8')
    script = (PROTOTYPE / 'prototype-interactions.js').read_text(encoding='utf-8')
    shell = (ROOT / 'gui' / 'chart_shell.py').read_text(encoding='utf-8')
    bridge = (ROOT / 'gui' / 'chart_bridge.py').read_text(encoding='utf-8')

    assert 'window.APLUS_CHART_BOOT' in script
    assert 'window.pywebview.api.save_state' in script
    assert 'create_price_alert' in script
    assert 'id="alertPrice"' in html and 'id="alertName"' in html
    assert 'webview.create_window' in shell and 'gui="edgechromium"' in shell
    assert 'save_chart_document' in bridge

