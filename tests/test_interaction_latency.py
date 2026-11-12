"""A blocked minute/plan reader must leave native input and tray actions alive."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from gui.chart_panel import ChartPanel
from gui.live_chart_refresh import LiveChartRefresh
from workbench.live_minutes import chart_minute_series, minute_rows


def minutes():
    dates = pd.date_range('2026-11-12 09:35', periods=24, freq='5min').astype(str)
    return pd.DataFrame(dict(date=dates, open=10., high=11., low=9., close=10., volume=100.))


def pump(root, until, timeout=4):
    deadline = time.monotonic() + timeout
    while not until():
        root.update()
        assert time.monotonic() < deadline
        time.sleep(.005)


def test_numeric_display_does_not_copy_minute_transport_attrs():
    frame = minutes()
    expected = chart_minute_series(frame)
    fine = minute_rows(frame)
    class NeverCopy:
        def __deepcopy__(self, memo):
            raise AssertionError('Pandas must not copy transport metadata into each group')
    marker = NeverCopy()
    frame.attrs.update(m1=fine, quote={'price': 10}, diagnostic=marker)
    assert chart_minute_series(frame) == {**expected, 'm1': fine}
    assert frame.attrs['diagnostic'] is marker
    assert frame.attrs['m1'] is fine


@pytest.mark.native_process
def test_slow_minutes_keep_input_and_tray_toggle_responsive(tmp_path, monkeypatch):
    import queue
    import tkinter as tk
    from gui.main_window import AplusMainWindow
    from tests.native_ui import create_window
    from workbench import live_minutes

    root = create_window(tk.Tk)
    entered, release = threading.Event(), threading.Event()
    original = live_minutes.chart_minute_series
    def slow(frame, hourly):
        entered.set()
        assert release.wait(3)
        return original(frame, hourly)
    monkeypatch.setattr(live_minutes, 'chart_minute_series', slow)
    delivered, beats = [], []
    reader = LiveChartRefresh(root, lambda *value: delivered.append(value))
    try:
        entry = tk.Entry(root)
        entry.pack()
        root.update()
        reader.submit('old', minutes(), None)
        assert entered.wait(2)
        root.after(0, lambda: (entry.insert(0, '159518'), beats.append(True)))
        # Exercise production tray dispatch during a deliberately blocked reader.
        root._closing = False
        root._tray_actions = queue.SimpleQueue()
        root._show_from_tray = root.deiconify
        root._hide_to_tray = root.withdraw
        root._poll_tray_actions = Mock()
        root._tray_actions.put('toggle')
        AplusMainWindow._poll_tray_actions(root)
        assert root.state() == 'withdrawn'
        root._tray_actions.put('toggle')
        AplusMainWindow._poll_tray_actions(root)
        assert root.state() == 'normal'
        pump(root, lambda: bool(beats))
        assert entry.get() == '159518' and not delivered
        # Coalesce two more frames. Neither old nor intermediate may be delivered.
        reader.submit('middle', minutes(), None)
        reader.submit('latest', minutes(), None)
        release.set()
        pump(root, lambda: bool(delivered))
        assert [value[0] for value in delivered] == ['latest']
        assert delivered[0][2] is None
        pump(root, lambda: reader.timer is None)
        entered.clear()
        release.clear()
        reader.submit('disabled', minutes(), None)
        assert entered.wait(2)
        panel = SimpleNamespace(_intraday_poller=Mock(), _minute_display=reader)
        ChartPanel.auto_quotes_changed(panel)
        release.set()
        pump(root, lambda: reader.timer is None)
        assert [value[0] for value in delivered] == ['latest']
    finally:
        release.set()
        reader.close()
        root.destroy()


@pytest.mark.parametrize('change', ['symbol', 'token', 'host', 'context', 'shape', 'error'])
def test_late_minutes_cannot_replace_another_chart(change):
    host = SimpleNamespace(token=1, closed=lambda: False, push_live=Mock(return_value=True))
    context = {'context_kind': 'watch'}
    panel = SimpleNamespace(_code='sh.600000', _embedded=host,
                            _workspace_context=context, _intraday_sent=None)
    identity = (panel._code, host, host.token, context)
    if change == 'symbol': panel._code = 'sz.000001'
    if change == 'token': host.token += 1
    if change == 'host': panel._embedded = object()
    if change == 'context': panel._workspace_context = dict(context)
    if change == 'shape': panel._workspace_request = {'shape': {'end': '2026-10-01'}}
    error = ValueError('bad minutes') if change == 'error' else None
    ChartPanel._deliver_intraday(panel, identity, (minutes(), '2026-11-12 11:30', {}, 'key'), error)
    host.push_live.assert_not_called()


@pytest.mark.native_process
def test_slow_plan_lookup_keeps_manual_price_and_cancels_on_close(tmp_path, monkeypatch):
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.trade_management import _PositionDialog
    from tests.native_ui import create_window
    from workbench.store import Store

    root = create_window(lambda: ttk.Window(themename='darkly'))
    store = Store(tmp_path / 'synthetic-ledger')
    dialog = _PositionDialog(root, store, {'sh.600000': '合成标的'})
    entered, release = threading.Event(), threading.Event()
    original = store.rows
    calls = []
    def slow(sql, args=()):
        if 'o.id AS observation_id' in sql:
            calls.append(threading.get_ident())
            entered.set()
            assert release.wait(3)
            return [dict(entry=10, stop=9, target=12, payload='{}',
                         plan_id='synthetic', observation_id='synthetic', strategy='test')]
        return original(sql, args)
    monkeypatch.setattr(store, 'rows', slow)
    try:
        dialog.code.set('600000')
        dialog._autofill_plan(silent=True, resolved='sh.600000')
        assert entered.wait(2)
        dialog.top.after(0, lambda: dialog.entry_price.set('10.75'))
        pump(root, lambda: dialog.entry_price.get() == '10.75')
        assert calls and calls[0] != threading.get_ident()
        release.set()
        pump(root, lambda: dialog._plan_poll is None)
        assert dialog.entry_price.get() == '10.75'
        assert dialog.plan_id is None and dialog.stop.get() == ''
        assert '保留手工' in dialog.hint.get()
        # A later explicit request can apply when the user has not edited it.
        dialog._autofill_plan(silent=True, resolved='sh.600000')
        pump(root, lambda: dialog._plan_poll is None)
        assert dialog.entry_price.get() == '10'
        assert dialog.plan_id == 'synthetic'
        dialog.top.destroy()
        assert dialog._plan_reader.closed and dialog._plan_poll is None
    finally:
        release.set()
        root.destroy()
