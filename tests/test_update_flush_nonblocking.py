"""Exercise both update entry points on a real Tk event loop with synthetic ACKs."""
import _tkinter
import queue
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import installed_update, version_update
from gui.main_window import AplusMainWindow
from tests.test_exit_state_machine import Host


@pytest.mark.parametrize('entry', ['source', 'installed'])
@pytest.mark.parametrize('outcome', ['ack', 'timeout', 'refused'])
def test_update_waits_for_chart_ack_without_blocking_tk(entry, outcome, tmp_path, monkeypatch):
    try:
        window = tk.Tk()
    except tk.TclError:
        # Keep the real Tcl event dispatcher and timers on headless runners.
        window = tk.Tcl()
        window.winfo_children = lambda: []
        window.winfo_exists = lambda: True
        window.withdraw = window.deiconify = window.geometry = Mock()
        window.destroy = lambda: None
        done = []
        window.quit = lambda: done.append(True)

        def run():
            while not done:
                window.tk.dooneevent(_tkinter.ALL_EVENTS)

        window.mainloop = run
    try:
        exercise_update(window, entry, outcome, tmp_path, monkeypatch)
    finally:
        window.destroy()


def exercise_update(window, entry, outcome, tmp_path, monkeypatch):
    service = Mock()
    window.service = service
    window.store = SimpleNamespace(root=tmp_path)
    window._closing = window._update_pending = False
    window._resume_embedded_charts = Mock()
    first, last = Host(), Host(closing=outcome != 'refused')
    window.chart = SimpleNamespace(embedded_hosts=lambda: [first, last])
    for name in ('_flush_embedded_charts', '_begin_update_flush', '_end_update_flush',
                 '_begin_exit_flush', '_poll_exit_flush'):
        method = getattr(AplusMainWindow, name, None)
        if method is not None:
            setattr(window, name, method.__get__(window))
    warnings, notices = Mock(), Mock()
    monkeypatch.setattr(version_update.messagebox, 'showwarning', warnings)
    monkeypatch.setattr(version_update.messagebox, 'showinfo', notices)
    monkeypatch.setattr(version_update, 'require_web_closed', Mock())
    spawn = Mock()
    monkeypatch.setattr(installed_update.subprocess, 'Popen', spawn)
    monkeypatch.setenv('APLUS_INSTALL_ROOT', str(tmp_path))

    request = tmp_path / 'request.json'
    prepare = Mock(side_effect=lambda *_: (request.touch(), tmp_path)[1])
    monkeypatch.setattr(version_update, 'prepare_worker', prepare)
    result = {'sequence': 1}
    if entry == 'source':
        controller = object.__new__(version_update.VersionController)
        controller.root = tmp_path
        controller.reserved = False
        start = lambda: controller.begin_update(result)
    else:
        controller = object.__new__(installed_update.InstalledVersionController)
        controller.results = queue.Queue()
        controller.results.put(('download', (tmp_path, result), False, 1))
        controller._operation = 1
        controller.busy = controller.reserved = True
        controller.queued = False
        controller.last_check = time.monotonic()
        controller.button = Mock()
        controller.download_bar = Mock()
        start = controller.poll
    controller.window = window
    controller.waiting = None

    returned, interactions, errors = [], [], []
    real_clock = time.monotonic
    elapsed = [0]
    monkeypatch.setattr(time, 'monotonic', lambda: real_clock() + elapsed[0])
    window.report_callback_exception = lambda _kind, exc, _tb: (errors.append(exc), window.quit())

    def interact():
        interactions.append((bool(returned), request.exists(), service.reserve_code_update.call_count))
        window.geometry(f'320x200+{100 + len(interactions)}+100')

    invoke = interact
    if window._tkloaded:
        button = tk.Button(window, text='Other action', command=interact)
        button.pack()
        invoke = button.invoke

    def begin():
        start()
        returned.append(real_clock() - started)

    def finish_ack():
        last._closed = True

    window.after(0, begin)
    window.after(100, invoke)
    if outcome == 'ack':
        window.after(500, lambda: setattr(first, '_closed', True))
        for delay in (1000, 2000, 3000, 4000, 4900):
            window.after(delay, invoke)
        window.after(5000, finish_ack)
        window.after(5300, window.quit)
    else:
        # Advance past the production deadline without spending 15 seconds per case.
        if outcome == 'timeout':
            window.after(200, lambda: elapsed.__setitem__(0, 15))
        window.after(650, window.quit)
    started = real_clock()
    window.mainloop()

    print(f'{entry}/{outcome}: entry callback returned in {returned[0] * 1000:.2f} ms')
    assert not errors
    assert interactions == [(True, False, 0)] * (6 if outcome == 'ack' else 1)
    assert returned[0] < (5 if outcome == 'ack' else .65)
    assert first.requested == last.requested == 1
    if outcome == 'ack':
        assert first.cleaned == last.cleaned == 1
        assert request.exists() and controller.waiting and window._update_pending
        assert controller.reserved
        if entry == 'source':
            service.reserve_code_update.assert_called_once()
            prepare.assert_called_once()
        else:
            spawn.assert_called_once()
        window._resume_embedded_charts.assert_not_called()
        notices.assert_not_called()
    else:
        assert not request.exists() and not controller.waiting and not window._update_pending
        prepare.assert_not_called()
        spawn.assert_not_called()
        assert not controller.reserved
        window._resume_embedded_charts.assert_called_once()
        assert warnings.call_count == (outcome == 'timeout')
        if entry == 'source':
            service.reserve_code_update.assert_not_called()
            service.release_code_update.assert_not_called()
        else:
            service.release_code_update.assert_called_once()
            assert not controller.busy and controller.latest['state'] == 'available'
            assert notices.call_args.args[1] == '图表仍在保存，请稍后再更新。'
