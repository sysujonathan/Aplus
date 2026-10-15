"""Static contract checks for the reviewed TradingView-style prototype."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / 'docs' / 'prototypes' / 'tv-workspace'


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
    assert "ctx.strokeStyle = '#2962ff'" in script
    assert "ctx.fillStyle = '#fff'" in script
    assert '--blue: #2962ff' in style
    assert '5分钟行情接入后可用' in (PROTOTYPE / 'index.html').read_text(encoding='utf-8')
    assert "ctx.setLineDash([6, 4])" in interactions
    assert "ctx.strokeStyle = '#2962ff'" in interactions


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
    assert 'drawingPalette' in script and 'data-color=' in script
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

