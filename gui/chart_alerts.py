"""Application-level monitor for persisted, local closed-bar chart alerts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from tkinter import messagebox

import pandas as pd

from workbench.chart_workspace import aggregate_minutes, evaluate_alert, validate_document


class ChartAlertMonitor:
    """Checks local immutable snapshots while A++ is open; never sends messages."""
    def __init__(self, root, store, interval_ms=60_000):
        self.root, self.store, self.interval_ms = root, store, int(interval_ms)
        self.after_id = None
        self.last_error = None

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
                        alert, frame, now=datetime.now(timezone.utc).isoformat())
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
        timeframe = alert['timeframe']
        if timeframe in ('5m','1h','4h'):
            frame, _ = self.store.load_chart_series(chart_identity, '5m')
            if frame is None: return None
            return frame if timeframe == '5m' else aggregate_minutes(frame, int(timeframe[:-1])*60)
        from .data import latest_market_dataset
        from workbench.market import completed_date, load_dataset, weekly_bars
        record = latest_market_dataset(self.store, alert['code'], 'daily', alert['source'])
        if not record or str(record.get('adjustment')) != alert['adjustment']:
            return None
        frame, _ = load_dataset(self.store, record['id'])
        frame = frame.loc[frame.date.le(completed_date())].reset_index(drop=True)
        if timeframe == 'daily': return frame
        if timeframe == 'weekly': return weekly_bars(frame, completed_date())
        if timeframe == 'monthly':
            from .chart_workspace import resample_daily
            result = resample_daily(frame, 'monthly')
            current = pd.Timestamp(completed_date()).to_period('M')
            return result.loc[pd.to_datetime(result.date).dt.to_period('M').lt(current)].reset_index(drop=True)
        return None
