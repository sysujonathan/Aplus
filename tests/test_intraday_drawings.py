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
@pytest.mark.parametrize('startup_delay', [0, 16], ids=['normal-startup', 'slow-startup-over-old-limit'])
def test_native_minute_editor_save_reopen_and_cross_periods(tmp_path, startup_delay):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'tests/intraday_drawing_probe.py'), str(tmp_path),
                             '--startup-delay', str(startup_delay)],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert evidence.pop('startup_delay_ms') >= startup_delay * 1000
    assert evidence == dict(ok=True, real_webview=True, real_store=True, all_tools=11,
                           cross_period_undo=True, reopened=True, daily_channel_outside_minutes=True,
                           price_basis_notice=True, synthetic=True)


def test_unready_native_page_still_fails_and_preserves_original_traceback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import webview
    from tests.intraday_drawing_probe import run
    window = SimpleNamespace(events=SimpleNamespace(loaded=Mock()), destroy=Mock(), evaluate_js=Mock())
    window.events.loaded.wait.return_value = False
    window.events.loaded.is_set.return_value = False
    monkeypatch.setattr(webview, 'create_window', Mock(return_value=window))
    monkeypatch.setattr(webview, 'start', lambda probe, **kwargs: probe())
    with pytest.raises(AssertionError, match='native WebView page load exceeded'):
        run(tmp_path)
    failure = json.loads((tmp_path/'failure.json').read_text(encoding='utf-8'))
    assert 'Traceback (most recent call last)' in failure['traceback']
    assert 'intraday_drawing_probe.py' in failure['traceback']
    assert 'native WebView page load exceeded' in failure['error']
    assert not (tmp_path/'result.json').exists()
    window.evaluate_js.assert_not_called()  # Diagnostics must not wait on an unready page.
    window.destroy.assert_called_once()
