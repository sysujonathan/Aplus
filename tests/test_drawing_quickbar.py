"""Edited quick styles cross the actual JS -> ChartBridge -> Store -> reopen boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest

from gui.chart_bridge import ChartBridge, workspace_boot
from gui.chart_shell import _materialize_page
from workbench.store import Store


def test_quick_styles_reopen_through_the_production_store(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the real quick-style assignments')
    snapshot = tmp_path / 'quick-styles.json'
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/chart_tools_regression.cjs')],
        env={**os.environ, 'APLUS_QUICKBAR_SNAPSHOT': str(snapshot)},
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    objects = json.loads(snapshot.read_text(encoding='utf-8'))
    frame = pd.DataFrame(dict(date=pd.bdate_range('2026-09-29', periods=55).strftime('%Y-%m-%d'),
        open=30, high=65, low=10, close=40, volume=100))
    store = Store(tmp_path / 'isolated-store')
    context = dict(code='sz.000001', timeframe='daily', frame=frame,
        record={'source': 'test', 'adjustment': 'qfq'})
    boot = workspace_boot(store, context)
    ChartBridge(store, boot).save_state(dict(objects=objects, timeframe='daily', indicators={}, toolbar={}, view={}))
    reopened = workspace_boot(store, context)
    assert len(reopened['drawings']) == len(objects)
    assert len({item['type'] for item in reopened['drawings']}) == 11
    for saved, original in zip(reopened['drawings'], objects):
        for key in ('color', 'width', 'fibLevels', 'fibTrendWidth', 'fibHorizontalWidth',
                    'fibTrendAlpha', 'strokeAlpha', 'channelLevels', 'backgroundAlpha', 'textAlpha', 'alpha',
                    'targetColor', 'stopColor', 'targetAlpha', 'stopAlpha'):
            if key in original:
                assert saved[key] == original[key], (original['id'], key)
        assert saved['a']['date'] == original['a']['date']
        assert saved['a']['value'] == original['a']['value']
        assert saved['b']['date'] == original['b']['date']
        assert saved['b']['value'] == original['b']['value']
    assert store.rows('SELECT * FROM observations') == []
    assert store.rows('SELECT * FROM plans') == []


def test_production_page_materializes_the_same_quickbar_module(tmp_path):
    page, target = _materialize_page(tmp_path / 'chart.json', {'production': True})
    assert (target / 'drawing-quickbar.js').is_file()
    html = page.read_text(encoding='utf-8')
    assert 'drawing-quickbar.js?build=' in html
    assert html.index('drawing-quickbar.js') < html.index('prototype-interactions.js')
