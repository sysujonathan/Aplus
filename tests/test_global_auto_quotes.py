import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from gui.chart_panel import ChartPanel


def test_global_off_keeps_one_initial_read_and_manual_retry(monkeypatch):
    import workbench.holding_quotes as quotes
    import workbench.live_minutes as minutes
    import gui.chart_panel as chart
    enabled = [False]
    clock = [0.]
    monkeypatch.setattr(chart.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(quotes, 'session_status', lambda *args:'交易中')
    monkeypatch.setattr(minutes, 'minute_refresh_phase', lambda *args:'open')
    poller = SimpleNamespace(start=Mock(return_value=True), take=lambda:None, delay=15, invalidate=Mock())
    panel = SimpleNamespace(_intraday_on=True, winfo_exists=lambda:True, _code='sz.159570',
        _intraday_code='sz.159570', _intraday_poller=poller, _intraday_loaded=False,
        _intraday_attempted=False, _intraday_due=0, _hourly=None,
        _auto_quotes_enabled=lambda:enabled[0], _intraday_calendar_state=lambda:None,
        after=Mock(return_value=1), _manual_controller=SimpleNamespace(refresh=Mock()))
    panel._intraday_tick = lambda: ChartPanel._intraday_tick(panel)
    for at in (0,400,800):
        clock[0]=at
        ChartPanel._intraday_tick(panel)
    assert poller.start.call_count == 1
    ChartPanel._refresh_manual(panel)
    ChartPanel._intraday_tick(panel)
    assert poller.start.call_count == 2
    enabled[0]=True
    ChartPanel.auto_quotes_changed(panel)
    ChartPanel._intraday_tick(panel)
    assert poller.start.call_count == 3
    enabled[0]=False
    ChartPanel.auto_quotes_changed(panel)
    clock[0]+=400
    ChartPanel._intraday_tick(panel)
    assert poller.start.call_count == 3
    panel._code='sz.159571'
    ChartPanel._intraday_tick(panel)
    assert poller.start.call_count == 4 and poller.start.call_args.args == ('sz.159571',)


def test_global_control_survives_page_switches_and_shares_persisted_setting(tmp_path):
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'tests/global_quotes_probe.py'),str(tmp_path)],
                          cwd=root,capture_output=True,text=True,timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    evidence=json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert all(evidence.values())
