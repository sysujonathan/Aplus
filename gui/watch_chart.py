"""Latest-selection-wins K charts. No Tk calls or strategy scans in workers."""
from contextlib import nullcontext
import tkinter as tk
import threading
import time

from .background_status import LatestRefresh
from .chart_renderer import render_chart
from workbench.watch_history import chart_day, local_history, fetch_history


class _Cancellation:
    def __init__(self, check):
        self.check = check
        self.cancelled = threading.Event()

    def set(self):
        self.cancelled.set()

    def is_set(self):
        if self.cancelled.is_set():
            return True
        try:
            self.check()
            return False
        except InterruptedError:
            return True

    def wait(self, timeout=None):
        """Event protocol for the original provider's cancellable request gap."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self.is_set()
            self.cancelled.wait(.05 if remaining is None else min(.05, remaining))
        return True


def load_chart(store, service, code, source, timeframe, phase, check):
    """Worker values contain no Tk owners, including references via closures."""
    check()
    if phase == 'fetch':
        cancelled = _Cancellation(check)
        guard = service.chart_access(cancelled) if service else nullcontext()
        with guard:
            history = fetch_history(store, code, source, cancelled)
    else:
        history = local_history(store, code, source)
    check()
    if history is None:
        return None
    frame, record = history
    from workbench.strategies import prepare
    bars = prepare(frame, timeframe, str(frame.date.iloc[-1]))
    if bars.empty:
        raise ValueError('该周期尚无已完成K线，可先查看日线')
    bars['ema20'] = bars.close.ewm(span=20, adjust=False).mean()
    # Funds use a 0.001 tick; the frame carries the confirmed instrument so the
    # renderer never has to guess it from price digits.
    instrument_type = frame.attrs.get('instrument_type')
    image = render_chart(bars, {}, '', {}, code=code, instrument_type=instrument_type)
    check()
    return dict(frame=frame, record=record, image=image, instrument_type=instrument_type,
                refresh=(phase == 'local' and record['end'] < chart_day()
                         and record.get('checked_through', '') < chart_day()))


class WatchChartController:
    def __init__(self, panel, item):
        self.panel, self.item = panel, item
        store = panel.store
        timeframe = item.timeframe or panel._tf or 'daily'
        service = getattr(panel.winfo_toplevel(), 'service', None)
        source = item.source
        code = item.code
        self.timeframe = timeframe
        self.reader = LatestRefresh(lambda phase, check: load_chart(store, service, code, source, timeframe, phase, check))
        self.poll_id = None
        self.closed = False
        self.shown = False
        self.reader.submit('local')
        self.poll_id = panel.after(30, self._poll)

    def refresh(self):
        if not self.closed:
            self.reader.submit('fetch')
            self.panel.readout.set('正在后台更新这一只标的的K图…')
            if self.poll_id is None:
                self.poll_id = self.panel.after(30, self._poll)

    def _poll(self):
        self.poll_id = None
        if self.closed:
            return
        reply = self.reader.poll()
        if reply is None:
            self.poll_id = self.panel.after(30, self._poll)
            return
        _, phase, data, error = reply
        panel = self.panel
        if error:
            if panel._image is None:
                panel.chart_label.configure(image='', text=f'K图暂未取得：{error}\n可稍后点击“更新K线”。')
            else:
                panel.readout.set(f'保留已有K图；本次更新未完成：{error}')
            return
        if data:
            record = data['record']
            panel._image = data['image']
            period = '周K' if self.timeframe == 'weekly' else '日K'
            panel._title_var.set(f'{self.item.code} {self.item.name} · 手动关注 · 历史{period}'
                                 f" · {record['source']} / {record['adjustment']} · 截至 {record['end']}")
            panel._workspace_context = dict(frame=data['frame'], record=record, code=self.item.code,
                                           name=self.item.name, timeframe='daily', payload={},
                                           display_timeframe=self.timeframe,
                                           metadata={}, strategy_type='', context_kind='watch',
                                           instrument_type=data.get('instrument_type'))
            panel._workspace_request = {'manual': True}
            panel._analysis_btn.configure(state=tk.NORMAL)
            if getattr(panel, '_interactive', False):
                panel._show_interactive(refresh_history=self.shown)
                self.shown = True
            else:
                panel._fit_image()
        if data is None or data['refresh']:
            self.refresh()

    def close(self):
        self.closed = True
        self.reader.close()
        if self.poll_id is not None:
            self.panel.after_cancel(self.poll_id)
            self.poll_id = None
