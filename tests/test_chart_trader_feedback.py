"""Trader feedback: real renderer, temporary interactions, and native resizing."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows WebView2')
def test_feedback_workflow_in_real_webview(tmp_path):
    probe = Path(__file__).with_name('chart_feedback_probe.py')
    result = subprocess.run([sys.executable, str(probe), str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=75)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))['ok']


@pytest.mark.native_process
@pytest.mark.parametrize('desktop_limit', [None, (1028, 740)])
def test_main_window_can_shrink_horizontally_without_fixed_aspect(tmp_path, desktop_limit):
    from gui.main_window import AplusMainWindow
    from workbench.service import Service
    from workbench.store import Store

    store = Store(tmp_path / 'isolated-desktop')
    window = AplusMainWindow(service=Service(store), store=store)
    try:
        window.after_cancel(window.version_controller.startup_check_id)
        window.version_controller.startup_check_id = None
        window.state('normal')
        if desktop_limit:
            window.maxsize(*desktop_limit)
        max_width, max_height = window.maxsize()
        widths = (min(1200, max_width), min(820, max_width))
        height = min(600, max_height)
        assert widths[0] > widths[1], 'test desktop must allow two distinct widths'
        for width in widths:
            window.geometry(f'{width}x{height}')
            window.update()
            assert abs(window.winfo_width() - width) <= 2
            assert window.winfo_height() == height
            assert window.chart.winfo_width() > 200
            assert window.chart.winfo_height() > 100
    finally:
        window.destroy()
