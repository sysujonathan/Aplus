"""Issue #132: tray exit is an after()-driven state machine, not a busy wait.

Drives AplusMainWindow's exit methods unbound on a fake window, the same
style as test_chart_exit_recovery. A manual scheduler stands in for
Tk's after(); a fake clock stands in for time.monotonic.
"""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import main_window


class Host:
    def __init__(self, *, closing=True):
        self._closed = False
        self.closing = False
        self._closing_on_request = closing
        self.requested = 0
        self.cleaned = 0

    def closed(self):
        return self._closed

    def request_close(self):
        self.requested += 1
        if self._closing_on_request:
            self.closing = True

    def cleanup(self):
        self.cleaned += 1


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def window(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, 'monotonic', clock)
    scheduled = []
    parent = SimpleNamespace(
        _closing=False, _update_pending=False, _exit_poll_id=None, _exit_hosts=None,
        chart=None, _pages={}, _case_comparisons=[],
        toolbar=SimpleNamespace(_job_id=None), service=None, _tray_icon=None,
        scheduled=scheduled, clock=clock)
    parent.after = lambda delay, fn: (scheduled.append(fn), len(scheduled))[1]
    parent.winfo_exists = lambda: True
    parent.destroy = Mock()
    parent._resume_embedded_charts = Mock()
    parent.after_cancel = Mock()
    for name in ('_exit_from_tray', '_begin_exit_flush', '_poll_exit_flush',
                 '_abort_exit', '_finish_exit'):
        setattr(parent, name,
                getattr(main_window.AplusMainWindow, name).__get__(parent, SimpleNamespace))
    return parent


def _tick(parent):
    parent.scheduled.pop(0)()


def _begin(parent, hosts):
    parent.chart = SimpleNamespace(embedded_hosts=lambda: list(hosts))
    main_window.AplusMainWindow._exit_from_tray(parent)
    assert parent._closing is True


def test_exit_without_charts_finishes_immediately(window):
    _begin(window, [])
    window.destroy.assert_called_once()


def test_exit_waits_for_ack_via_after_ticks(window):
    host = Host()
    _begin(window, [host])
    assert host.requested == 1
    window.destroy.assert_not_called()
    _tick(window)  # still closing, not closed
    window.destroy.assert_not_called()
    host._closed = True
    _tick(window)
    assert host.cleaned == 1
    window.destroy.assert_called_once()


def test_refused_close_restores_session_without_warning(window, monkeypatch):
    warning = Mock()
    monkeypatch.setattr(main_window.messagebox, 'showwarning', warning)
    host = Host(closing=False)
    _begin(window, [host])
    _tick(window)  # within the grace period: keep waiting
    assert window._closing is True
    window.clock.now += 0.5
    _tick(window)  # refused beyond grace: abort and restore
    assert window._closing is False
    window._resume_embedded_charts.assert_called_once()
    window.destroy.assert_not_called()
    warning.assert_not_called()


def test_timeout_warns_and_restores_session(window, monkeypatch):
    warning = Mock()
    monkeypatch.setattr(main_window.messagebox, 'showwarning', warning)
    host = Host()  # closing starts but the ack never lands
    _begin(window, [host])
    window.clock.now += 16
    _tick(window)
    warning.assert_called_once()
    assert window._closing is False
    window._resume_embedded_charts.assert_called_once()
    window.destroy.assert_not_called()
