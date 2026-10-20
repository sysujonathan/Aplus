"""Application-level monitor for persisted, local closed-bar chart alerts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from tkinter import messagebox

import pandas as pd

from workbench.chart_workspace import (aggregate_minutes, bar_close_time, bar_completed,
                                       evaluate_alert, validate_document)


def load_alert_frame(store, chart_identity, alert, now):
    """Read verified local source data, never a historical display snapshot."""
    timeframe = alert['timeframe']
    if timeframe in ('5m', '1h', '4h'):
        frame, _ = store.load_chart_series(chart_identity, '5m')
        if frame is None:
            return None
        frame = frame if timeframe == '5m' else aggregate_minutes(frame, int(timeframe[:-1])*60)
    else:
        from .data import latest_market_dataset
        from workbench.market import load_dataset
        from workbench.watch_history import cached_history
        record = latest_market_dataset(store, alert['code'], 'daily', alert['source'])
        official = (load_dataset(store, record['id'])
                    if record and str(record.get('adjustment')) == alert['adjustment'] else None)
        try:
            cached = cached_history(store, alert['code'], alert['source'], alert['adjustment'])
        except (OSError, ValueError, KeyError, TypeError):
            # A disposable watch-chart cache must never suppress a verified
            # official snapshot or abort every other alert in this poll.
            cached = None
        candidates = [item for item in (official, cached) if item is not None]
        if not candidates:
            return None
        # Keep each immutable series intact: never splice scan and chart-cache prices.
        frame, _ = max(candidates, key=lambda item: (item[1]['end'], item[1].get('checked_through', '')))
        frame = frame.loc[[bar_completed(day, 'daily', now) for day in frame.date]].reset_index(drop=True)
        if timeframe in ('weekly', 'monthly') and not frame.empty:
            from .chart_workspace import resample_daily
            rule = {'weekly': 'W-FRI', 'monthly': 'M'}[timeframe]
            coverage = frame.groupby(pd.to_datetime(frame.date).dt.to_period(rule)).date.max()
            result = resample_daily(frame, timeframe)
            # A Friday/month-end label does not prove its daily constituents exist.
            frame = result.loc[[
                str(coverage.loc[pd.Timestamp(day).to_period(rule)])
                >= bar_close_time(day, timeframe).date().isoformat()
                for day in result.date
            ]].reset_index(drop=True)
        elif timeframe not in ('daily', 'weekly', 'monthly'):
            return None
    return frame.loc[[bar_completed(day, timeframe, now) for day in frame.date]].reset_index(drop=True)


class ChartAlertMonitor:
    """Checks local immutable snapshots while A++ is open; never sends messages."""
    def __init__(self, root, store, interval_ms=60_000, *, clock=None):
        self.root, self.store, self.interval_ms = root, store, int(interval_ms)
        self.after_id = None
        self.last_error = None
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def start(self):
        if self.store is not None and self.after_id is None:
            self.after_id = self.root.after(3_000, self.poll)

    def poll(self):
        self.after_id = None
        events = []
        try:
            for row in self.store.rows("SELECT key,value FROM meta WHERE key LIKE 'chart_workspace:%'"):
                key, doc = row['key'], validate_document(json.loads(row['value']))
                if key.split(':',1)[1] in getattr(self.root, '_active_chart_workspaces', set()):
                    continue
                revised = deepcopy(doc)
                frames = {}
                for index, alert in enumerate(doc['alerts']):
                    if not alert.get('enabled', True):
                        continue
                    signature = (alert['code'], alert['source'], alert['adjustment'], alert['timeframe'])
                    if signature not in frames:
                        frames[signature] = self._frame(key.split(':',1)[1], alert)
                    frame = frames[signature]
                    if frame is None or len(frame) < 2:
                        continue
                    revised['alerts'][index], event = evaluate_alert(
                        alert, frame, now=self.clock())
                    if event: events.append(event)
                if revised['alerts'] != doc['alerts']:
                    try: self.store.save_chart_document(key.split(':',1)[1], revised, doc['revision'])
                    except ValueError: pass  # an open workspace owns the newer revision
            self.last_error = None
        except Exception as exc:
            error = str(exc)
            if error != self.last_error:
                self.last_error = error
                messagebox.showwarning('A++ 提醒检查暂停',
                    '本轮提醒没有完成：'+error+'\n\n行情和交易记录未修改；请打开对应图表核对数据。',
                    parent=self.root)
        if events:
            messagebox.showwarning('A++ 行情提醒', '\n'.join(
                f"{item['text']} · {item['bar']} · 收盘 {item['price']:.3f}" for item in events),
                parent=self.root)
            self.root.bell()
        if self.root.winfo_exists():
            self.after_id = self.root.after(self.interval_ms, self.poll)

    def _frame(self, chart_identity, alert):
        return load_alert_frame(self.store, chart_identity, alert, self.clock())
