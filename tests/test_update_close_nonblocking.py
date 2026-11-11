"""Version updates share the tray ACK gate and retain their reservation until ACK."""
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import main_window, version_update, installed_update
from tests.test_exit_state_machine import Host, _tick, window


@pytest.fixture(params=['source', 'installed'])
def update(request, window, tmp_path, monkeypatch):
    for name in ('_close_for_version_update', '_finish_version_update'):
        setattr(window, name, getattr(main_window.AplusMainWindow, name).__get__(window))
    window.service = Mock()
    window.deiconify = Mock()
    window._update_pending = True
    window._flush_embedded_charts = Mock(side_effect=AssertionError('blocking flush called'))
    window.update = Mock(side_effect=AssertionError('nested Tk update called'))
    window._tray_icon = Mock()
    # Keep tray polling out of the manual ACK queue, but exercise tray stop.
    window._poll_tray_actions = Mock()
    if request.param == 'source':
        module = version_update
        c = object.__new__(module.VersionController)
        c.results = queue.SimpleQueue()
        c.maybe_show_notice = Mock()
    else:
        module = installed_update
        c = object.__new__(module.InstalledVersionController)
        c.results = queue.Queue()
        c._operation = 0
        c.download_bar = Mock()
        c.button = Mock()
        c.queued = False
        c.last_check = window.clock.now
    c.window = window
    c.waiting = (tmp_path, window.clock.now + 15)
    c.reserved = True
    c.busy = False
    (tmp_path / 'ready.json').write_text('{}')
    monkeypatch.setattr(module.messagebox, 'showerror', Mock())
    monkeypatch.setattr(module.messagebox, 'showinfo', Mock())
    monkeypatch.setattr(main_window.messagebox, 'showwarning', Mock())
    return c, window, tmp_path


def begin(update, hosts):
    c, w, folder = update
    w.chart = SimpleNamespace(embedded_hosts=lambda: list(hosts))
    c.poll()
    w._flush_embedded_charts.assert_not_called()
    w.update.assert_not_called()
    return c, w, folder


def test_update_ack_success_is_nonblocking_and_holds_mutex(update):
    hosts = [Host(), Host()]
    c, w, folder = begin(update, hosts)
    assert w._closing and w._update_pending and c.reserved
    w.service.pool.shutdown.assert_not_called()
    w.destroy.assert_not_called()
    hosts[0]._closed = True
    _tick(w)
    w.destroy.assert_not_called()
    hosts[1]._closed = True
    _tick(w)
    assert all(h.cleaned == 1 and h.requested == 1 for h in hosts)
    w.service.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    w._tray_icon.stop.assert_called_once()
    w.destroy.assert_called_once()
    w.service.release_code_update.assert_not_called()
    assert not (folder / 'cancelled.json').exists()


@pytest.mark.parametrize('failure', ['timeout', 'save_failure'])
def test_update_ack_failure_revokes_permission_and_restores_session(update, failure):
    host = Host(closing=failure != 'save_failure')
    c, w, folder = begin(update, [host])
    w.clock.now += 16 if failure == 'timeout' else .5
    _tick(w)
    assert (folder / 'cancelled.json').exists()
    assert not w._closing and not w._update_pending and not c.reserved
    assert c.waiting is None
    w.service.release_code_update.assert_called_once()
    w.service.pool.shutdown.assert_not_called()
    w.destroy.assert_not_called()
    w._tray_icon.stop.assert_not_called()
    w._resume_embedded_charts.assert_called_once()
    w.deiconify.assert_called_once()
    assert host.cleaned == 0
    assert w._exit_completion is None and w._exit_failure is None
    assert c.poll in w.scheduled
    assert main_window.messagebox.showwarning.call_count == (failure == 'timeout')


def test_update_without_charts_finishes_immediately(update):
    c, w, folder = begin(update, [])
    w.destroy.assert_called_once()
    w.service.release_code_update.assert_not_called()


def test_update_close_request_error_rolls_back_once(update):
    host = Host()
    host.request_close = Mock(side_effect=OSError('chart transport unavailable'))
    c, w, folder = begin(update, [host])
    assert (folder / 'cancelled.json').exists()
    assert not c.reserved and not w._update_pending and not w._closing
    w.service.release_code_update.assert_called_once()
    w.destroy.assert_not_called()


def test_update_blocks_reentrant_tray_and_update_close(update):
    host = Host()
    c, w, folder = begin(update, [host])
    w._exit_from_tray()
    w._close_for_version_update(on_failure=Mock())
    assert host.requested == 1
    host._closed = True
    _tick(w)
    w.destroy.assert_called_once()
    w.service.release_code_update.assert_not_called()


def test_async_completion_error_still_releases_update_reservation(update):
    host = Host()
    c, w, folder = begin(update, [host])
    w.service.pool.shutdown.side_effect = RuntimeError('pool shutdown failed')
    host._closed = True
    _tick(w)
    assert (folder / 'cancelled.json').exists()
    assert not c.reserved and not w._update_pending and not w._closing
    w.service.release_code_update.assert_called_once()
    w.destroy.assert_not_called()
    assert c.poll in w.scheduled
