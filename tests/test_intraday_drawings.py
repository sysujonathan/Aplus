"""Cross-period drawing geometry and the real Store boundary, synthetic data only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from gui.chart_bridge import ChartBridge, workspace_boot
from tests.test_tv_workspace_prototype import run_node
from workbench.store import Store


def test_minute_tools_and_switches_preserve_one_saved_document(tmp_path):
    result = run_node('chart_intraday_drawings.cjs')
    assert result['ok'] and len(result['kinds']) == 11
    daily = pd.DataFrame([dict(date=day, open=10, high=11, low=9, close=10, volume=100)
                          for day in result['dates']])
    context = dict(frame=daily, minute_series=pd.DataFrame(result['minutes']), code='sz.159570',
                   timeframe='daily', context_kind='watch', record=dict(source='test', adjustment='不复权'))
    store = Store(tmp_path/'runtime')
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    expected = None
    # Include minute-created saves and every subsequent display period.
    for state in result['states']:
        bridge.save_state(state)
        saved = store.chart_document(boot['identity'])
        if len(state['objects']) != 12:
            continue
        signature = json.dumps([(obj['id'], obj['points'], obj['created_at']) for obj in saved['drawings']])
        if expected is None:
            expected = signature
        assert signature == expected
    reopened = workspace_boot(store, context)
    assert len(reopened['drawings']) == 12
    assert reopened['drawings'][1]['a']['date'] == '2026-10-30 11:05:00'
    assert reopened['drawings'][1]['priceBasis'] == '不复权'
    other = workspace_boot(store, dict(context, code='sz.159571'))
    assert other['drawings'] == []


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows WebView2 acceptance')
def test_native_minute_editor_save_reopen_and_cross_periods(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'tests/intraday_drawing_probe.py'), str(tmp_path)],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert evidence == dict(ok=True, real_webview=True, real_store=True, all_tools=11,
                           cross_period_undo=True, reopened=True, daily_channel_outside_minutes=True,
                           price_basis_notice=True, synthetic=True)
