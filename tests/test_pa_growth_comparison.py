"""The PA navigation/comparison contract against real native child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Tk + Edge WebView2 comparison')
def test_real_chart_survives_growth_navigation_and_comparison_close(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # The chart IPC writer appends its own UUID. A deep pytest node directory
    # can exceed Windows MAX_PATH even though the normal runtime is short.
    short_home = root / '.test-pa-growth'
    short_home.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pc-', dir=short_home) as isolated:
        isolated = Path(isolated).resolve()
        assert isolated.is_relative_to(short_home.resolve())
        folder = isolated / 'pa-comparison-probe'
        process_temp = isolated / 'temp'
        process_temp.mkdir()
        environment = dict(os.environ, TEMP=str(process_temp), TMP=str(process_temp), PYTHONUTF8='1',
                           A_WORKBENCH_HOME=str(folder / 'runtime'),
                           APLUS_ACCELERATION_PROFILE=str(isolated / 'acceleration.json'))
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('pa_growth_comparison_probe.py')), str(folder)],
                                cwd=root, env=environment, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=90)
        if (folder / 'result.json').exists():
            report = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            (tmp_path / 'comparison-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        assert result.returncode == 0, result.stdout + result.stderr
    assert report['ok'] and report['synthetic'] and report['real_tk']
    assert report['real_chart_webview'] and report['real_growth_webview']
    assert set(report['checks']) == {
        'canonical_drawing_view_and_period_loaded_in_real_chart',
        'growth_navigation_returns_same_chart_process_and_context',
        'actual_comparison_opens_beside_original_chart',
        'comparison_close_preserves_saved_drawings_view_and_period',
        'same_embedded_process_token_identity_without_reload',
        'no_live_price_fetch',
        'ordinary_close_guard_acknowledged_all_children',
    }
    assert len({report['chart_pid'], report['growth_pid'], report['comparison_pid']}) == 3
