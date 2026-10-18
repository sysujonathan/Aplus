"""TradingView-like analysis workspace on top of Aplus' verified chart data.

The bitmap is still produced by the shared, strategy-aware renderer.  This
module owns only trader-authored drawings, indicators, replay controls and
local closed-bar alerts.  It never writes observations, plans or positions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import math
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import pandas as pd
from PIL import ImageDraw, ImageTk

from workbench.chart_workspace import (
    History, POINT_COUNTS, TOOLS, aggregate_minutes, create_alert, evaluate_alert, identity,
    make_drawing, minute_frame, risk_readout, session_counts, validate_document,
)
from .chart_renderer import render_chart
from .replay_navigation import candle_readout
from .theme import ACCENT, APP_BG, BORDER, CHART_BG, CONTROL_BG, MUTED, PANEL_BG, TEXT


TOOL_GLYPHS = dict(fib='≋', long='L↑', ray='↦', trend='╱', short='S↓', rectangle='□',
                   up='↑', down='↓', arrow='➚', path='⌁', channel='∥')
DRAWING_LABELS = {**dict(TOOLS), 'text': '文字', 'ruler': '价格范围'}
TF_LABELS = [('5m', '5分'), ('1h', '1小时'), ('4h', '4小时'),
             ('daily', '日'), ('weekly', '周'), ('monthly', '月')]
CONDITION_LABELS = {'above': '高于', 'below': '低于', 'cross_up': '向上穿越',
                    'cross_down': '向下穿越', 'cross': '任意穿越'}


def _ruler_readout(obj):
    start, end = (float(point[1]) for point in obj['points'])
    delta = end - start
    return f'{delta:+.3f} ({delta / start * 100:+.2f}%)'


def _path_ends(obj):
    web = obj.get('web') or {}
    return (web.get('startStyle') == 'arrow',
            web.get('endStyle', 'arrow' if web.get('path_version') else 'none') == 'arrow')


def _arrow_marker_points(a, b):
    length = math.hypot(b[0]-a[0], b[1]-a[1])
    if length < .01:
        return [a, b]
    ux, uy = (b[0]-a[0])/length, (b[1]-a[1])/length
    head = min(48, length*.4)
    wing, shaft = head*.625, head*.625*.45
    base = (b[0]-ux*head, b[1]-uy*head)
    def side(offset):
        return (base[0]-uy*offset, base[1]+ux*offset)
    return [a, side(shaft), side(wing), b, side(-wing), side(-shaft)]


def resample_daily(frame, timeframe):
    """Create display-only weekly/monthly OHLC from real daily bars."""
    if timeframe == 'daily':
        return frame.copy()
    rule = {'weekly': 'W-FRI', 'monthly': 'ME'}[timeframe]
    value = frame.copy()
    value.index = pd.to_datetime(value.date)
    out = value.resample(rule).agg(open=('open', 'first'), high=('high', 'max'),
                                   low=('low', 'min'), close=('close', 'last'),
                                   volume=('volume', 'sum')).dropna().reset_index()
    out.date = out.date.dt.strftime('%Y-%m-%d')
    return out


class ChartWorkspaceDialog:
    """A resizable, persistent analysis layer for one symbol."""

    def __init__(self, parent, store, *, frame, record, code, name='', timeframe='daily',
                 payload=None, metadata=None, strategy=None, strategy_type='',
                 instrument_type=None, holding_levels=(), replay_events=(), title='', display_timeframe=None):
        self.store, self.code, self.name = store, str(code), str(name or '')
        self.record = dict(record or {})
        self.source = str(self.record.get('source') or 'unknown')
        self.adjustment = str(self.record.get('adjustment') or 'unknown')
        self.identity = identity(self.code, self.source, self.adjustment)
        self.base_frame = frame.reset_index(drop=True).copy()
        self.base_frame.date = self.base_frame.date.astype(str)
        self.frame = self.base_frame
        self.base_timeframe = timeframe
        self.timeframe = timeframe if timeframe in dict(TF_LABELS) else 'daily'
        self.payload, self.metadata = dict(payload or {}), dict(metadata or {})
        self.strategy, self.strategy_type = strategy, strategy_type
        self.instrument_type = instrument_type
        self.holding_levels, self.replay_events = tuple(holding_levels), tuple(replay_events)
        self.show_strategy = True
        self.hide_drawings = self.hide_indicators = False
        self.view_bars, self.view_offset, self.price_scale = min(120, len(frame)), 0, 1.0
        self.replay_index = None
        self.replay_playing = False
        self.replay_speed = 1.0
        self.tool = None
        self.pending_points = []
        self.selected_id = None
        self._preview = None
        self._drag = None
        self._after = self._alert_after = None
        self._render_token = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='aplus-chart')
        self._results = queue.Queue()
        self._image = self._photo = None
        self._info = {}
        self._closed = False
        root = parent
        while getattr(root, 'master', None) is not None:
            root = root.master
        self.app_root = root
        active = getattr(root, '_active_chart_workspaces', None)
        if active is None:
            active = root._active_chart_workspaces = set()
        active.add(self.identity)
        saved = store.chart_document(self.identity)
        self.history = History(saved)
        self.revision = saved['revision']

        self.top = tk.Toplevel(parent)
        self.top.title(title or f'A++ 图表工作区 · {self.name} {self.code}')
        self.top.configure(bg=APP_BG)
        self.top.geometry(f'{min(1500, self.top.winfo_screenwidth()-50)}x'
                          f'{min(940, self.top.winfo_screenheight()-70)}')
        self.top.minsize(980, 620)
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        self.top.bind('<Destroy>', self._destroyed, add='+')
        self.top.bind('<Control-z>', lambda _e: self.undo())
        self.top.bind('<Control-y>', lambda _e: self.redo())
        self.top.bind('<Escape>', self._escape)
        self._build()
        self._load_saved_intraday()
        display = (saved['settings'].get('web_state') or {}).get('timeframe') or display_timeframe
        if display in ('daily', 'weekly', 'monthly') and display != self.timeframe:
            self.change_timeframe(display)
        else:
            self.schedule_render()
        self._alert_after = self.top.after(3_000, self._scheduled_alert_check)

    @property
    def value(self):
        return self._preview if self._preview is not None else self.history.value

    def _build(self):
        topbar = tk.Frame(self.top, bg=CONTROL_BG, height=42)
        topbar.pack(fill=tk.X)
        topbar.pack_propagate(False)
        tk.Label(topbar, text=f'{self.code}  {self.name}', bg=CONTROL_BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 11, 'bold'), padx=12).pack(side=tk.LEFT, fill=tk.Y)
        self.tf_buttons = {}
        for key, label in TF_LABELS:
            button = tk.Button(topbar, text=label, command=lambda v=key: self.change_timeframe(v),
                               bg=CONTROL_BG, fg=TEXT, activebackground=ACCENT,
                               activeforeground='white', relief=tk.FLAT, padx=8)
            button.pack(side=tk.LEFT, fill=tk.Y)
            self.tf_buttons[key] = button
        self.replay_button = self._top_button(topbar, '⏪ 回放', self.toggle_replay)
        snap = tk.Menubutton(topbar, text='📷 快照', bg=CONTROL_BG, fg=TEXT,
                             activebackground=ACCENT, relief=tk.FLAT, padx=10)
        snap.pack(side=tk.RIGHT, fill=tk.Y)
        snap.menu = tk.Menu(snap, tearoff=False)
        snap['menu'] = snap.menu
        snap.menu.add_command(label='保存干净图表 PNG…', command=self.save_snapshot)
        snap.menu.add_command(label='复制图像到剪贴板', command=self.copy_snapshot)
        alarm = tk.Menubutton(topbar, text='闹钟＋', bg=CONTROL_BG, fg=TEXT,
                              activebackground=ACCENT, relief=tk.FLAT, padx=10)
        alarm.pack(side=tk.RIGHT, fill=tk.Y)
        alarm.menu = tk.Menu(alarm, tearoff=False); alarm['menu'] = alarm.menu
        alarm.menu.add_command(label='创建收盘 K 线提醒…', command=self.alert_dialog)
        alarm.menu.add_command(label='立即检查已载入 K 线', command=self.check_alerts)
        self._top_button(topbar, '指标', self.indicator_dialog, side=tk.RIGHT)
        self._top_button(topbar, '5分数据', self.import_intraday, side=tk.RIGHT)
        self._top_button(topbar, '↶', self.undo, side=tk.RIGHT)
        self._top_button(topbar, '↷', self.redo, side=tk.RIGHT)

        self.replaybar = tk.Frame(self.top, bg=PANEL_BG, height=0)
        self.replay_text = tk.StringVar(value='')
        self._top_button(self.replaybar, '退出回放', self.exit_replay)
        self._top_button(self.replaybar, '◀', lambda: self.replay_step(-1))
        self.play_button = self._top_button(self.replaybar, '▶', self.replay_play)
        self._top_button(self.replaybar, '▶|', lambda: self.replay_step(1))
        self.speed_var = tk.StringVar(value='1x')
        ttk.Combobox(self.replaybar, textvariable=self.speed_var, state='readonly', width=5,
                     values=('.2x', '.5x', '1x', '2x', '5x')).pack(side=tk.LEFT, padx=6)
        tk.Label(self.replaybar, textvariable=self.replay_text, bg=PANEL_BG, fg=TEXT).pack(side=tk.LEFT)

        body = tk.Frame(self.top, bg=APP_BG)
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg=CONTROL_BG, width=48)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        tk.Button(left, text='✚', command=lambda: self.select_tool(None), bg=CONTROL_BG,
                  fg=TEXT, relief=tk.FLAT).pack(fill=tk.X, pady=(5, 2))
        for key, label in TOOLS:
            b = tk.Button(left, text=TOOL_GLYPHS[key], command=lambda v=key: self.select_tool(v),
                          bg=CONTROL_BG, fg=TEXT, activebackground=ACCENT,
                          activeforeground='white', relief=tk.FLAT, height=1)
            b.pack(fill=tk.X, pady=1)
            self._tooltip(b, label)
        tk.Frame(left, bg=BORDER, height=1).pack(fill=tk.X, pady=4)
        eye = tk.Menubutton(left, text='◉', bg=CONTROL_BG, fg=TEXT, relief=tk.FLAT)
        eye.pack(fill=tk.X, pady=2)
        eye.menu = tk.Menu(eye, tearoff=False); eye['menu'] = eye.menu
        eye.menu.add_command(label='隐藏／显示所有图形', command=lambda: self.toggle_hide('drawings'))
        eye.menu.add_command(label='隐藏／显示所有指标', command=lambda: self.toggle_hide('indicators'))
        eye.menu.add_command(label='隐藏／显示策略证据', command=self.toggle_strategy)
        eye.menu.add_command(label='隐藏／显示全部', command=lambda: self.toggle_hide('all'))
        trash = tk.Menubutton(left, text='♲', bg=CONTROL_BG, fg=TEXT, relief=tk.FLAT)
        trash.pack(side=tk.BOTTOM, fill=tk.X, pady=6)
        trash.menu = tk.Menu(trash, tearoff=False); trash['menu'] = trash.menu
        trash.menu.add_command(label='移除所有图形', command=lambda: self.remove_scope('drawings'))
        trash.menu.add_command(label='移除所有指标', command=lambda: self.remove_scope('indicators'))
        trash.menu.add_command(label='移除图形与指标', command=lambda: self.remove_scope('all'))

        center = tk.Frame(body, bg=CHART_BG)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fav = tk.Frame(center, bg=CONTROL_BG)
        fav.place(relx=.5, y=8, anchor=tk.N, height=38)
        tk.Label(fav, text='⠿', bg=CONTROL_BG, fg=MUTED, padx=7).pack(side=tk.LEFT)
        for key, label in TOOLS:
            b = tk.Button(fav, text=TOOL_GLYPHS[key], command=lambda v=key: self.select_tool(v),
                          bg=CONTROL_BG, fg=TEXT, activebackground=ACCENT, relief=tk.FLAT, width=3)
            b.pack(side=tk.LEFT); self._tooltip(b, label)
        self.canvas = tk.Canvas(center, bg=CHART_BG, highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill=tk.BOTH, expand=True)
        fav.lift()
        self.canvas.bind('<Configure>', self.schedule_render)
        self.canvas.bind('<Button-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        self.canvas.bind('<Double-1>', self._double)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Button-3>', self._pan_start)
        self.canvas.bind('<B3-Motion>', self._pan_motion)

        right = tk.Frame(body, bg=PANEL_BG, width=270)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        right.pack_propagate(False)
        tk.Label(right, text='对象与提醒', bg=PANEL_BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 10, 'bold'), padx=10, pady=8).pack(fill=tk.X)
        self.tree = ttk.Treeview(right, columns=('state',), show='tree headings', height=18)
        self.tree.heading('#0', text='对象'); self.tree.heading('state', text='状态')
        self.tree.column('#0', width=165); self.tree.column('state', width=72, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6)
        self.tree.bind('<<TreeviewSelect>>', self._tree_select)
        self.tree.bind('<Double-1>', lambda _e: self.edit_selected())
        actions = tk.Frame(right, bg=PANEL_BG)
        actions.pack(fill=tk.X, padx=6, pady=6)
        for label, command in [('编辑', self.edit_selected), ('显隐', self.toggle_selected),
                               ('删除', self.delete_selected)]:
            tk.Button(actions, text=label, command=command, bg=CONTROL_BG, fg=TEXT,
                      relief=tk.FLAT).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.status = tk.StringVar(value='图形是分析草稿；不会写入交易计划或持仓')
        tk.Label(self.top, textvariable=self.status, bg=CONTROL_BG, fg=MUTED,
                 anchor=tk.W, padx=9).pack(fill=tk.X)
        self.refresh_tree()

    def _top_button(self, parent, text, command, *, side=tk.LEFT):
        button = tk.Button(parent, text=text, command=command, bg=CONTROL_BG, fg=TEXT,
                           activebackground=ACCENT, activeforeground='white', relief=tk.FLAT, padx=9)
        button.pack(side=side, fill=tk.Y)
        return button

    @staticmethod
    def _tooltip(widget, text):
        widget.bind('<Enter>', lambda _e: widget.configure(text=text[:4]))
        glyph = widget.cget('text')
        widget.bind('<Leave>', lambda _e: widget.configure(text=glyph))

    def schedule_render(self, _event=None):
        if self._closed:
            return
        if self._after:
            self.top.after_cancel(self._after)
        self._after = self.top.after(100, self._start_render)

    def _render_frame(self):
        end = self.replay_index + 1 if self.replay_index is not None else len(self.frame)
        return self.frame.iloc[:end].copy()

    def _start_render(self):
        self._after = None
        size = (self.canvas.winfo_width(), self.canvas.winfo_height())
        if min(size) < 120:
            return
        self._render_token += 1
        token = self._render_token
        frame, settings = self._render_frame(), deepcopy(self.history.value['settings'])
        if self.hide_indicators:
            settings['ema'] = False
        payload = self.payload if self.show_strategy and self.replay_index is None else {}
        strategy = self.strategy if self.show_strategy and self.replay_index is None else None
        events = self.replay_events if self.show_strategy and self.replay_index is None else ()
        levels = self.holding_levels if self.show_strategy and self.replay_index is None else ()
        args = dict(frame=frame, payload=payload, title='', meta=self.metadata,
                    strategy=strategy, strategy_type=self.strategy_type,
                    replay_events=events, view_bars=self.view_bars,
                    size=size, price_scale=self.price_scale, view_offset=self.view_offset,
                    code=self.code, instrument_type=self.instrument_type,
                    holding_levels=levels, display_options=settings)
        def work():
            try: self._results.put((token, render_chart(**args), None))
            except Exception as exc: self._results.put((token, None, str(exc)))
        self._executor.submit(work)
        self.top.after(40, self._poll_render)

    def _poll_render(self):
        if self._closed:
            return
        try:
            token, image, error = self._results.get_nowait()
        except queue.Empty:
            self.top.after(40, self._poll_render); return
        if token != self._render_token:
            self.top.after(1, self._poll_render); return
        if error:
            self.canvas.delete('all'); self.canvas.create_text(30, 70, text='图表绘制失败：'+error,
                                                               fill=TEXT, anchor=tk.NW)
            return
        self._image, self._info = image, image.info.get('replay_view', {})
        self._photo = ImageTk.PhotoImage(image, master=self.top)
        self.canvas.delete('all'); self.canvas.create_image(0, 0, image=self._photo, anchor=tk.NW, tags='base')
        self.draw_overlay(); self._update_tf_buttons()

    def _update_tf_buttons(self):
        for key, button in self.tf_buttons.items():
            button.configure(bg=ACCENT if key == self.timeframe else CONTROL_BG)

    def _visible_drawings(self):
        values = self.value['drawings']
        if self.replay_index is None:
            return values
        cutoff = pd.Timestamp(self.frame.date.iloc[self.replay_index])
        # A drawing made after the replayed bar is hidden in blind replay.
        return [obj for obj in values if pd.Timestamp(obj.get('created_at', '2100-01-01')).tz_localize(None) <= cutoff]

    def _xy(self, point):
        candles = self._info.get('candles', [])
        xs = self._info.get('candle_x', [])
        bounds = self._info.get('price_bounds')
        price_y = self._info.get('price_y')
        if not candles or not xs or not bounds or not price_y:
            return None
        target = pd.Timestamp(point[0])
        stamps = [pd.Timestamp(row['date']) for row in candles]
        index = min(range(len(stamps)), key=lambda i: abs(stamps[i]-target))
        low, high = map(float, bounds); top, bottom = map(float, price_y)
        y = bottom-(float(point[1])-low)/(high-low or 1)*(bottom-top)
        return float(xs[index]), y

    def _point_at(self, x, y):
        candles = self._info.get('candles', [])
        xs = self._info.get('candle_x', [])
        bounds = self._info.get('price_bounds'); price_y = self._info.get('price_y')
        if not candles or not bounds:
            return None
        index = min(range(len(xs)), key=lambda i: abs(xs[i]-x))
        low, high = map(float, bounds); top, bottom = map(float, price_y)
        price = high-(y-top)/(bottom-top or 1)*(high-low)
        settings = self.history.value['settings']
        if settings.get('magnet'):
            values = [float(candles[index][key]) for key in ('open', 'high', 'low', 'close')]
            snapped = min(values, key=lambda p: abs(p-price))
            pixel = abs(snapped-price)/(high-low or 1)*(bottom-top)
            if pixel <= 12:
                price = snapped
        return [str(candles[index]['date']), max(.000001, float(price))]

    def draw_overlay(self):
        self.canvas.delete('overlay')
        settings = self.history.value['settings']
        if not self.hide_indicators:
            self._draw_indicator_labels(settings)
        if not self.hide_drawings:
            for obj in self._visible_drawings():
                if obj.get('visible', True):
                    self._draw_object(obj, selected=obj['id'] == self.selected_id)
            if self.pending_points:
                self._draw_pending()
        self.refresh_tree()

    def _draw_indicator_labels(self, settings):
        if settings.get('indicator_labels'):
            y = 58
            if settings.get('ema'):
                ema_values = self._info.get('ema_values', [])
                last = next((v for v in reversed(ema_values) if not pd.isna(v)), None)
                text = f"EMA {settings['ema_length']} {settings['ema_source']}"
                if last is not None: text += f'  {last:.3f}'
                self.canvas.create_text(94, y, text=text, fill=settings['ema_color'], anchor=tk.NW,
                                        font=('Microsoft YaHei UI', 10, 'bold'), tags='overlay')
                y += 23
            if settings.get('bar_count'):
                state = '' if self.timeframe in ('5m', '1h', '4h') else '（切到真实分钟图显示）'
                self.canvas.create_text(94, y, text=f"Bar Count Normal {settings['count_interval']} {state}",
                                        fill=settings['count_color'], anchor=tk.NW,
                                        font=('Microsoft YaHei UI', 10), tags='overlay')
        if settings.get('bar_count') and self.timeframe in ('5m', '1h', '4h'):
            candles, xs = self._info.get('candles', []), self._info.get('candle_x', [])
            if candles:
                bars = pd.DataFrame(candles)
                for count, x, row in zip(session_counts(bars), xs, candles):
                    if count % int(settings['count_interval']) == 0:
                        point = self._xy([row['date'], row['high']])
                        if point:
                            self.canvas.create_text(x, point[1]-10, text=str(count),
                                fill=settings['count_color'], font=('Segoe UI', settings['count_size']),
                                tags='overlay')

    def _draw_object(self, obj, *, selected=False):
        points = [self._xy(point) for point in obj['points']]
        if any(point is None for point in points):
            return
        tag = ('overlay', 'object', 'obj:'+obj['id'])
        color, width = obj.get('color', '#26b9c5'), int(obj.get('width', 2))
        kind = obj['kind']; x_right = self._info.get('price_x', (0, self.canvas.winfo_width()))[1]
        if kind == 'fib':
            (x1, y1), (x2, y2) = points
            levels = obj.get('levels', [0, .5, 1])
            for level in levels:
                y = y1+(y2-y1)*float(level)
                self.canvas.create_line(min(x1,x2), y, x_right, y, fill=color, width=width, tags=tag)
                price = obj['points'][0][1]+(obj['points'][1][1]-obj['points'][0][1])*float(level)
                self.canvas.create_text(min(x1,x2)+4, y-2, text=f'{level:g} ({price:.3f})',
                                        fill=color, anchor=tk.SW, tags=tag)
        elif kind == 'ray':
            x, y = points[0]; self.canvas.create_line(x, y, x_right, y, fill=color, width=width, tags=tag)
        elif kind == 'arrow' and (obj.get('web') or {}).get('arrow_marker_version'):
            polygon = _arrow_marker_points(*points[:2])
            self.canvas.create_polygon(*[n for point in polygon for n in point], fill=color, outline=color, tags=tag)
        elif kind in ('trend', 'arrow'):
            self.canvas.create_line(*points[0], *points[1], fill=color, width=width,
                                    arrow=tk.LAST if kind == 'arrow' else tk.NONE, tags=tag)
        elif kind == 'ruler':
            (x1, y1), (x2, y2) = points
            self.canvas.create_rectangle(min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2),
                                         outline=color, width=width, tags=tag)
            self.canvas.create_line((x1+x2)/2, y1, (x1+x2)/2, y2,
                                    fill=color, width=width, arrow=tk.BOTH, tags=tag)
            self.canvas.create_text((x1+x2)/2, min(y1,y2)-5, text=_ruler_readout(obj),
                                    fill=color, anchor=tk.S, tags=tag)
        elif kind == 'text':
            self.canvas.create_text(points[0][0]+4, points[0][1]+4,
                                    text=obj.get('text') or '文字', fill=color, anchor=tk.NW,
                                    font=('Microsoft YaHei UI', 11), tags=tag)
        elif kind == 'rectangle':
            self.canvas.create_rectangle(*points[0], *points[1], outline=color, width=width,
                                         fill='#331244', stipple='gray50', tags=tag)
        elif kind in ('up', 'down'):
            x, y = points[0]
            self.canvas.create_text(x, y, text='▲' if kind == 'up' else '▼', fill=color,
                                    font=('Segoe UI Symbol', 18, 'bold'), tags=tag)
        elif kind == 'path':
            flat = [n for point in points for n in point]
            start, end = _path_ends(obj)
            self.canvas.create_line(*flat, fill=color, width=width, smooth=False,
                                    arrow=tk.BOTH if start and end else tk.FIRST if start else tk.LAST if end else tk.NONE,
                                    tags=tag)
        elif kind == 'channel':
            a, b, c = points; dx, dy = c[0]-a[0], c[1]-a[1]
            self.canvas.create_line(*a, *b, fill=color, width=width, tags=tag)
            self.canvas.create_line(c[0], c[1], b[0]+dx, b[1]+dy, fill=color, width=width, tags=tag)
            self.canvas.create_line(a[0]+dx/2, a[1]+dy/2, b[0]+dx/2, b[1]+dy/2,
                                    fill=color, width=1, dash=(4,4), tags=tag)
        elif kind in ('long', 'short'):
            entry, stop, target, edge = points; x1, x2 = entry[0], edge[0]
            green, red = '#1f9d7a', '#e34b56'
            self.canvas.create_rectangle(x1, target[1], x2, entry[1], fill=green,
                                         outline=green, stipple='gray50', tags=tag)
            self.canvas.create_rectangle(x1, entry[1], x2, stop[1], fill=red,
                                         outline=red, stipple='gray50', tags=tag)
            text, _, _ = risk_readout(obj)
            self.canvas.create_text(x1+5, entry[1]-5, text=text, fill='white', anchor=tk.SW,
                                    font=('Microsoft YaHei UI', 9, 'bold'), tags=tag)
        if kind != 'text' and obj.get('text'):
            self.canvas.create_text(points[0][0]+4, points[0][1]+4, text=obj['text'], fill=color,
                                    anchor=tk.NW, tags=tag)
        if selected:
            handles = [(str(index), point) for index, point in enumerate(points)]
            if kind == 'rectangle':
                a,b=points
                handles=[('rect:0',(a[0],a[1])),('rect:1',(b[0],b[1])),
                    ('rect:0x1y',(a[0],b[1])),('rect:1x0y',(b[0],a[1])),
                    ('rect:0x', (a[0],(a[1]+b[1])/2)),('rect:1x',(b[0],(a[1]+b[1])/2)),
                    ('rect:0y',((a[0]+b[0])/2,a[1])),('rect:1y',((a[0]+b[0])/2,b[1]))]
            for handle, (x, y) in handles:
                self.canvas.create_rectangle(x-5, y-5, x+5, y+5, fill=CHART_BG, outline=ACCENT,
                                             width=2, tags=('overlay', 'handle', f'handle:{handle}'))

    def _draw_pending(self):
        points = [self._xy(point) for point in self.pending_points]
        points = [point for point in points if point]
        if len(points) > 1:
            self.canvas.create_line(*[n for point in points for n in point], fill='#26b9c5',
                                    width=2, dash=(4, 3), tags='overlay')
        for x, y in points:
            self.canvas.create_oval(x-4, y-4, x+4, y+4, outline=ACCENT, width=2, tags='overlay')

    def select_tool(self, kind):
        self.tool, self.pending_points, self.selected_id = kind, [], None
        self.status.set(('正在使用：'+dict(TOOLS)[kind]+'；单击放置锚点'
                         + ('，双击结束' if kind == 'path' else '')) if kind else
                        '选择模式：拖动对象或锚点；滚轮缩放')
        self.draw_overlay()

    def _hit(self, x, y):
        items = self.canvas.find_overlapping(x-5, y-5, x+5, y+5)
        for item in reversed(items):
            tags = self.canvas.gettags(item)
            handle = next((t for t in tags if t.startswith('handle:')), None)
            obj = next((t for t in tags if t.startswith('obj:')), None)
            if handle and self.selected_id:
                value = handle.split(':',1)[1]
                return self.selected_id, int(value) if value.isdigit() else value
            if obj:
                return obj.split(':', 1)[1], None
        return None, None

    def _press(self, event):
        point = self._point_at(event.x, event.y)
        if point is None:
            return
        if self.tool:
            self.pending_points.append(point)
            expected = 999 if self.tool == 'path' else POINT_COUNTS[self.tool]
            if len(self.pending_points) >= expected:
                self._finish_drawing()
            else:
                self.draw_overlay()
            return
        object_id, handle = self._hit(event.x, event.y)
        if not object_id:
            self.selected_id = None; self.draw_overlay(); return
        self.selected_id = object_id
        obj = next(obj for obj in self.history.value['drawings'] if obj['id'] == object_id)
        if obj.get('locked'):
            self.status.set('对象已锁定；可在右侧双击解除锁定')
            self.draw_overlay(); return
        self._drag = dict(id=object_id, handle=handle, start=point,
                          original=deepcopy(self.history.value))
        self.draw_overlay()

    def _motion(self, event):
        if not self._drag:
            return
        current = self._point_at(event.x, event.y)
        if current is None:
            return
        doc = deepcopy(self._drag['original'])
        obj = next(obj for obj in doc['drawings'] if obj['id'] == self._drag['id'])
        start = self._drag['start']; handle = self._drag['handle']
        visible_dates = [str(row['date']) for row in self._info.get('candles', [])]
        def shift_day(day):
            if not visible_dates: return day
            a = min(range(len(visible_dates)), key=lambda i: abs(pd.Timestamp(visible_dates[i])-pd.Timestamp(start[0])))
            b = min(range(len(visible_dates)), key=lambda i: abs(pd.Timestamp(visible_dates[i])-pd.Timestamp(current[0])))
            old = min(range(len(visible_dates)), key=lambda i: abs(pd.Timestamp(visible_dates[i])-pd.Timestamp(day)))
            return visible_dates[max(0, min(len(visible_dates)-1, old+b-a))]
        delta = current[1]-start[1]
        if handle is None:
            for point in obj['points']:
                point[0], point[1] = shift_day(point[0]), max(.000001, point[1]+delta)
        elif obj['kind'] == 'rectangle' and isinstance(handle, str) and handle.startswith('rect:'):
            axes = handle.split(':',1)[1]
            if '0x' in axes: obj['points'][0][0] = current[0]
            if '1x' in axes: obj['points'][1][0] = current[0]
            if '0y' in axes: obj['points'][0][1] = current[1]
            if '1y' in axes: obj['points'][1][1] = current[1]
        elif obj['kind'] in ('long', 'short') and handle == 0:
            for index in range(3):
                obj['points'][index][1] = max(.000001, obj['points'][index][1]+delta)
            obj['points'][0][0] = current[0]
        elif obj['kind'] in ('long', 'short') and handle == 3:
            obj['points'][3][0] = current[0]
        else:
            obj['points'][handle] = current
        self._preview = doc
        self.draw_overlay()

    def _release(self, _event):
        if self._drag and self._preview is not None:
            try:
                if self.history.commit(self._preview): self._save()
            except ValueError as exc:
                messagebox.showerror('对象未修改', str(exc), parent=self.top)
        self._preview = None; self._drag = None
        self.draw_overlay()

    def _double(self, _event):
        if self.tool == 'path' and len(self.pending_points) >= 2:
            if len(self.pending_points) >= 2 and self.pending_points[-1] == self.pending_points[-2]:
                self.pending_points.pop()
            self._finish_drawing()
            return 'break'

    def _finish_drawing(self):
        if not self.pending_points:
            return
        candles = self._info.get('candles', [])
        right = candles[min(len(candles)-1, max(0, len(candles)*3//4))]['date'] if candles else self.pending_points[0][0]
        obj = make_drawing(self.tool, self.pending_points, created_at=datetime.now(timezone.utc).isoformat(),
                           settings=self.history.value['settings'], right_date=right)
        doc = deepcopy(self.history.value); doc['drawings'].append(obj)
        try:
            self.history.commit(doc); self._save(); self.selected_id = obj['id']
            self.status.set(dict(TOOLS)[self.tool]+'已保存；Ctrl+Z 可撤销')
        except ValueError as exc:
            messagebox.showerror('无法保存图形', str(exc), parent=self.top)
        self.pending_points = []; self.tool = None; self.draw_overlay()

    def _wheel(self, event):
        if event.state & 0x0001:
            self.price_scale = max(.35, min(5., self.price_scale*(.9 if event.delta>0 else 1.1)))
            self.schedule_render(); return
        total = len(self._render_frame())
        direction = -1 if event.delta > 0 else 1
        self.view_bars = max(12, min(total, int(self.view_bars*(1+direction*.15))))
        self.view_offset = min(self.view_offset, max(0, total-self.view_bars))
        self.schedule_render()

    def _hover(self, event):
        if self._drag or self.tool:
            return
        text = candle_readout(self._info, event.x, event.y)
        if text: self.status.set(text+' · 滚轮缩放 · Shift+滚轮缩放价格轴 · 右键拖动平移')

    def _pan_start(self, event):
        self._pan_origin = (event.x, self.view_offset)

    def _pan_motion(self, event):
        if not hasattr(self, '_pan_origin'): return
        spacing = max(4., (self._info.get('price_x',(0,self.canvas.winfo_width()))[1]
                           -self._info.get('price_x',(0,self.canvas.winfo_width()))[0]) / max(1,self.view_bars))
        delta = round((event.x-self._pan_origin[0])/spacing)
        total = len(self._render_frame())
        self.view_offset = max(0,min(max(0,total-self.view_bars),self._pan_origin[1]+delta))
        self.schedule_render()

    def undo(self):
        if self.history.undo():
            self.selected_id = None; self._save(); self.schedule_render()

    def redo(self):
        if self.history.redo():
            self.selected_id = None; self._save(); self.schedule_render()

    def toggle_hide(self, scope):
        if scope in ('drawings', 'all'): self.hide_drawings = not self.hide_drawings
        if scope in ('indicators', 'all'): self.hide_indicators = not self.hide_indicators
        if scope == 'all':
            self.show_strategy = False if self.hide_drawings or self.hide_indicators else self.timeframe == self.base_timeframe
        self.schedule_render() if scope in ('indicators','all') else self.draw_overlay()

    def toggle_strategy(self):
        if self.timeframe != self.base_timeframe:
            messagebox.showinfo('策略证据周期固定', '策略证据只在原扫描周期显示。', parent=self.top); return
        self.show_strategy = not self.show_strategy
        self.schedule_render()

    def remove_scope(self, scope):
        doc = deepcopy(self.history.value)
        if scope in ('drawings', 'all'): doc['drawings'] = []
        if scope in ('indicators', 'all'):
            doc['settings']['ema'] = doc['settings']['bar_count'] = False
        if doc == self.history.value:
            return
        label = {'drawings':'全部图形', 'indicators':'全部指标', 'all':'图形与指标'}[scope]
        if messagebox.askyesno('移除对象', f'移除{label}？可用 Ctrl+Z 恢复。', parent=self.top):
            self.history.commit(doc); self._save(); self.selected_id = None; self.schedule_render()

    def change_timeframe(self, timeframe):
        if timeframe in ('5m', '1h', '4h'):
            try:
                frame, _ = self.store.load_chart_series(self.identity, '5m')
            except Exception as exc:
                messagebox.showerror('分钟行情不可用', str(exc), parent=self.top); return
            if frame is None:
                if not messagebox.askyesno('需要真实分钟行情',
                    'A++ 不会用日线伪造分钟 K 线。现在导入真实 5 分钟 CSV？\n\n'
                    '支持列：时间、开盘、最高、最低、收盘、成交量。', parent=self.top):
                    return
                frame = self.import_intraday(activate=False)
                if frame is None: return
            try:
                self.frame = frame if timeframe == '5m' else aggregate_minutes(frame, int(timeframe[:-1])*60)
            except ValueError as exc:
                messagebox.showerror('周期不可用', str(exc), parent=self.top); return
        else:
            if self.base_timeframe != 'daily' and timeframe != self.base_timeframe:
                messagebox.showinfo('缺少真实日线底层',
                    '当前入口没有逐日底层，不能从较大周期反推更细 K 线。请从主界面日线图打开工作区。', parent=self.top)
                return
            self.frame = resample_daily(self.base_frame, timeframe)
        self.timeframe = timeframe
        self.view_bars, self.view_offset, self.price_scale = min(120, len(self.frame)), 0, 1.0
        self.replay_index = None; self.show_strategy = timeframe == self.base_timeframe
        self.schedule_render()

    def import_intraday(self, *, activate=True):
        path = filedialog.askopenfilename(parent=self.top, title='导入／更新真实 5 分钟行情',
                                          filetypes=[('CSV 行情', '*.csv')])
        if not path: return None
        try:
            frame = minute_frame(path, 5)
            record = self.store.save_chart_series(self.identity, '5m', frame)
            self.status.set(f"真实 5 分钟行情已保存：{record['start']} ～ {record['end']} · {record['rows']} 根")
            if activate:
                self.timeframe='5m'; self.frame=frame; self.view_bars=min(120,len(frame)); self.view_offset=0
                self.replay_index=None; self.show_strategy=False; self.schedule_render()
            return frame
        except Exception as exc:
            messagebox.showerror('分钟行情未导入', str(exc), parent=self.top); return None

    def _load_saved_intraday(self):
        # Read-only warm-up verifies a previously imported artifact early. It
        # does not change the opening timeframe or silently substitute data.
        try: self.store.load_chart_series(self.identity, '5m')
        except Exception as exc: self.status.set(f'已保存的分钟行情不可用：{exc}')

    def toggle_replay(self):
        if self.replay_index is not None:
            self.exit_replay(); return
        if len(self.frame) < 12:
            messagebox.showinfo('无法回放', '至少需要 12 根真实 K 线。', parent=self.top); return
        day = simpledialog.askstring('生成回放', '从哪根 K 线开始？\n输入日期或日期时间；留空从中间开始。',
                                     parent=self.top)
        if day is None: return
        if day.strip():
            try:
                target = pd.Timestamp(day.strip())
                self.replay_index = min(range(len(self.frame)),
                    key=lambda i: abs(pd.Timestamp(self.frame.date.iloc[i])-target))
            except Exception:
                messagebox.showerror('日期无效', '请输入可识别的日期或日期时间。', parent=self.top); return
        else:
            self.replay_index = max(10, len(self.frame)//2)
        self.show_strategy = False
        self.replaybar.pack(fill=tk.X, after=self.top.winfo_children()[0])
        self._update_replay_text(); self.schedule_render()

    def replay_step(self, amount):
        if self.replay_index is None: return
        self.replay_index = max(1, min(len(self.frame)-1, self.replay_index+int(amount)))
        self.view_offset = 0; self._update_replay_text(); self.schedule_render()

    def replay_play(self):
        if self.replay_index is None: return
        self.replay_playing = not self.replay_playing
        self.play_button.configure(text='Ⅱ' if self.replay_playing else '▶')
        if self.replay_playing: self._replay_tick()

    def _replay_tick(self):
        if not self.replay_playing or self.replay_index is None: return
        if self.replay_index >= len(self.frame)-1:
            self.replay_playing = False; self.play_button.configure(text='▶'); return
        self.replay_step(1)
        try: speed = float(self.speed_var.get().rstrip('x'))
        except ValueError: speed = 1
        self.top.after(max(80, int(700/speed)), self._replay_tick)

    def _update_replay_text(self):
        if self.replay_index is not None:
            self.replay_text.set(f"盲回放 · {self.frame.date.iloc[self.replay_index]} · "
                                 f"{self.replay_index+1}/{len(self.frame)} · 不触发提醒")

    def exit_replay(self):
        self.replay_playing = False; self.replay_index = None
        self.show_strategy = self.timeframe == self.base_timeframe
        self.replaybar.pack_forget(); self.schedule_render()

    def indicator_dialog(self):
        win = tk.Toplevel(self.top); win.title('指标与图表设置'); win.configure(bg=APP_BG)
        win.transient(self.top); win.grab_set(); win.resizable(False, False)
        settings = deepcopy(self.history.value['settings'])
        ema = tk.BooleanVar(value=settings['ema']); count = tk.BooleanVar(value=settings['bar_count'])
        volume = tk.BooleanVar(value=settings['volume']); magnet = tk.BooleanVar(value=settings['magnet'])
        length = tk.StringVar(value=str(settings['ema_length'])); source = tk.StringVar(value=settings['ema_source'])
        offset = tk.StringVar(value=str(settings['ema_offset'])); interval = tk.StringVar(value=str(settings['count_interval']))
        size = tk.StringVar(value=str(settings['count_size'])); risk = tk.StringVar(value=str(settings['risk_budget']))
        fields = ttk.Frame(win, padding=14); fields.pack(fill=tk.BOTH, expand=True)
        ttk.Checkbutton(fields, text='EMA', variable=ema).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(fields, text='长度').grid(row=0,column=1); ttk.Entry(fields,textvariable=length,width=7).grid(row=0,column=2)
        ttk.Label(fields, text='来源').grid(row=0,column=3); ttk.Combobox(fields,textvariable=source,
            values=('open','high','low','close','volume','hl2','hlc3','ohlc4'),state='readonly',width=7).grid(row=0,column=4)
        ttk.Label(fields, text='偏移').grid(row=0,column=5); ttk.Entry(fields,textvariable=offset,width=7).grid(row=0,column=6)
        ttk.Button(fields, text='颜色', command=lambda: self._pick_color(settings, 'ema_color')).grid(row=0,column=7,padx=6)
        ttk.Checkbutton(fields, text='Bar Count', variable=count).grid(row=1,column=0,sticky=tk.W,pady=8)
        ttk.Label(fields, text='每隔').grid(row=1,column=1); ttk.Entry(fields,textvariable=interval,width=7).grid(row=1,column=2)
        ttk.Label(fields, text='字号').grid(row=1,column=3); ttk.Entry(fields,textvariable=size,width=7).grid(row=1,column=4)
        ttk.Button(fields, text='颜色', command=lambda: self._pick_color(settings, 'count_color')).grid(row=1,column=7,padx=6)
        ttk.Checkbutton(fields, text='显示成交量', variable=volume).grid(row=2,column=0,columnspan=2,sticky=tk.W)
        ttk.Checkbutton(fields, text='弱磁吸 OHLC', variable=magnet).grid(row=2,column=2,columnspan=3,sticky=tk.W)
        ttk.Label(fields, text='多空测算风险预算（分析假设）').grid(row=3,column=0,columnspan=4,sticky=tk.W,pady=(10,0))
        ttk.Entry(fields,textvariable=risk,width=12).grid(row=3,column=4,sticky=tk.W,pady=(10,0))
        ttk.Label(fields, text='Bar Count 只在真实 5分／1小时／4小时 K 线上绘制；午休不重置，次交易日从 1 开始。',
                  foreground=MUTED).grid(row=4,column=0,columnspan=8,sticky=tk.W,pady=10)
        def save():
            try:
                doc = deepcopy(self.history.value)
                settings.update(ema=ema.get(), bar_count=count.get(), volume=volume.get(), magnet=magnet.get(),
                    ema_length=int(length.get()), ema_source=source.get(), ema_offset=int(offset.get()),
                    count_interval=int(interval.get()), count_size=int(size.get()), risk_budget=float(risk.get()))
                doc['settings'] = settings; validate_document(doc)
                self.history.commit(doc); self._save(); win.destroy(); self.schedule_render()
            except Exception as exc: messagebox.showerror('参数未保存', str(exc), parent=win)
        ttk.Button(fields,text='取消',command=win.destroy).grid(row=5,column=6,pady=(10,0))
        ttk.Button(fields,text='应用',command=save).grid(row=5,column=7,pady=(10,0))

    @staticmethod
    def _pick_color(settings, key):
        color = colorchooser.askcolor(settings[key])[1]
        if color: settings[key] = color

    def alert_dialog(self, alert=None):
        if self.replay_index is not None:
            messagebox.showinfo('回放不触发提醒', '请退出回放后创建真实行情提醒。', parent=self.top); return
        win = tk.Toplevel(self.top); win.title('收盘 K 线提醒'); win.transient(self.top); win.grab_set()
        box = ttk.Frame(win,padding=14); box.pack(fill=tk.BOTH,expand=True)
        alert = deepcopy(alert or {})
        name = tk.StringVar(value=alert.get('name', f'{self.code} 提醒'))
        target_labels = {'price':'固定价格','ema':'EMA'}
        target_values = {value:key for key,value in target_labels.items()}
        condition_values = {value:key for key,value in CONDITION_LABELS.items()}
        frequency_labels = {'once':'仅一次','bar_close':'每根 K 线收盘'}
        frequency_values = {value:key for key,value in frequency_labels.items()}
        target = tk.StringVar(value=target_labels[alert.get('target','price')])
        condition = tk.StringVar(value=CONDITION_LABELS[alert.get('condition','cross')])
        frequency = tk.StringVar(value=frequency_labels[alert.get('frequency','bar_close')])
        last = float(self._render_frame().close.iloc[-1])
        value = tk.StringVar(value=str(alert.get('value', round(last, 3))))
        ema_len = tk.StringVar(value=str(alert.get('ema_length', self.history.value['settings']['ema_length'])))
        enabled = tk.BooleanVar(value=alert.get('enabled',True))
        expiry = tk.StringVar(value=alert.get('expires',''))
        rows = [('名称',name),('固定价格',value),('EMA 长度',ema_len),('到期时间（可空）',expiry)]
        for row,(label,var) in enumerate(rows):
            ttk.Label(box,text=label).grid(row=row,column=0,sticky=tk.W,pady=4)
            ttk.Entry(box,textvariable=var,width=30).grid(row=row,column=1,sticky=tk.EW,pady=4)
        ttk.Label(box,text='目标').grid(row=4,column=0,sticky=tk.W)
        ttk.Combobox(box,textvariable=target,state='readonly',values=tuple(target_values)).grid(row=4,column=1,sticky=tk.EW)
        ttk.Label(box,text='条件').grid(row=5,column=0,sticky=tk.W,pady=4)
        ttk.Combobox(box,textvariable=condition,state='readonly',values=tuple(condition_values)).grid(row=5,column=1,sticky=tk.EW,pady=4)
        ttk.Label(box,text='频率').grid(row=6,column=0,sticky=tk.W,pady=4)
        ttk.Combobox(box,textvariable=frequency,state='readonly',values=tuple(frequency_values)).grid(row=6,column=1,sticky=tk.EW,pady=4)
        ttk.Checkbutton(box,text='启用',variable=enabled).grid(row=7,column=1,sticky=tk.W)
        ttk.Label(box,text=f'固定口径：{self.source} · {self.adjustment} · {dict(TF_LABELS)[self.timeframe]}\n'
                  '只检查之后出现的已收盘 K 线；回放和历史数据不会补触发。\n'
                  '触发方式：A++ 本机弹窗，不发送外部消息。',foreground=MUTED,justify=tk.LEFT).grid(
                      row=8,column=0,columnspan=2,sticky=tk.W,pady=10)
        def save():
            try:
                if alert:
                    item = deepcopy(alert); item.update(name=name.get().strip(),target=target_values[target.get()],
                        condition=condition_values[condition.get()],value=float(value.get()),ema_length=int(ema_len.get()),
                        frequency=frequency_values[frequency.get()], expires=expiry.get().strip(),
                        enabled=enabled.get(),status='等待下一根已收盘 K 线')
                else:
                    from .chart_alerts import load_alert_frame
                    moment = datetime.now(timezone.utc).isoformat()
                    current = load_alert_frame(self.store, self.identity, dict(code=self.code,
                        source=self.source, adjustment=self.adjustment, timeframe=self.timeframe), moment)
                    if current is None or current.empty:
                        raise ValueError('没有同来源、同复权的已完成正式行情，请更新行情后创建提醒')
                    item = create_alert(name=name.get(), code=self.code, source=self.source,
                        adjustment=self.adjustment, timeframe=self.timeframe, target=target_values[target.get()],
                        condition=condition_values[condition.get()], value=float(value.get()), frame=current,
                        ema_length=int(ema_len.get()), frequency=frequency_values[frequency.get()],
                        expires=expiry.get().strip(), active_after=moment)
                doc = deepcopy(self.history.value)
                doc['alerts'] = [item if old['id']==item['id'] else old for old in doc['alerts']]
                if not alert: doc['alerts'].append(item)
                doc = validate_document(doc); self.history.value = doc
                self._save(); win.destroy(); self.refresh_tree()
            except Exception as exc: messagebox.showerror('提醒未保存',str(exc),parent=win)
        ttk.Button(box,text='取消',command=win.destroy).grid(row=9,column=0,sticky=tk.E,padx=5)
        ttk.Button(box,text='保存提醒',command=save).grid(row=9,column=1,sticky=tk.E)

    def _scheduled_alert_check(self):
        self._alert_after = None
        try:
            self.check_alerts(silent=True)
            self._last_alert_error = None
        except Exception as exc:
            error = str(exc)
            self.status.set('本轮提醒检查暂停：' + error)
            if error != getattr(self, '_last_alert_error', None):
                self._last_alert_error = error
                messagebox.showwarning('A++ 提醒检查暂停', error + '\n\n当前图形仍保留，下轮重新核对已保存行情。', parent=self.top)
        finally:
            if not self._closed:
                self._alert_after = self.top.after(60_000, self._scheduled_alert_check)

    def check_alerts(self, *, silent=False):
        if self.replay_index is not None: return
        doc = deepcopy(self.history.value); events = []
        now = datetime.now(timezone.utc).isoformat()
        from .chart_alerts import load_alert_frame
        for index, alert in enumerate(doc['alerts']):
            if (alert['code'],alert['source'],alert['adjustment']) != (
                    self.code,self.source,self.adjustment):
                continue
            frame = load_alert_frame(self.store, self.identity, alert, now)
            if frame is None or frame.empty:
                continue
            doc['alerts'][index], event = evaluate_alert(alert, frame, now=now)
            if event: events.append(event)
        if doc['alerts'] != self.history.value['alerts']:
            previous = self.history.value
            self.history.value = validate_document(doc)
            if not self._save():
                self.history.value = previous
                return  # Another editor still owns the revision; do not notify uncommitted events.
            self.refresh_tree()
        if events:
            messagebox.showwarning('A++ 行情提醒', '\n'.join(
                f"{event['text']} · {event['bar']} · 收盘 {event['price']:.3f}" for event in events), parent=self.top)
            self.top.bell()
        elif not silent:
            messagebox.showinfo('提醒检查', '已检查当前已载入的最新收盘 K 线，没有新触发。', parent=self.top)

    def refresh_tree(self):
        if not hasattr(self,'tree'): return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        labels = DRAWING_LABELS
        for obj in self.history.value['drawings']:
            state = ('隐藏' if not obj.get('visible',True) else '锁定' if obj.get('locked') else '显示')
            self.tree.insert('',tk.END,iid='d:'+obj['id'],text=labels.get(obj['kind'], obj['kind']),values=(state,))
        for alert in self.history.value['alerts']:
            state = '启用' if alert.get('enabled') else '停用'
            self.tree.insert('',tk.END,iid='a:'+alert['id'],text='闹钟 · '+alert['name'],values=(state,))
        for iid in selected:
            if self.tree.exists(iid): self.tree.selection_add(iid)

    def _tree_select(self, _event=None):
        selection = self.tree.selection()
        if selection and selection[0].startswith('d:'):
            self.selected_id = selection[0][2:]; self.draw_overlay()

    def _selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else ('d:'+self.selected_id if self.selected_id else None)

    def toggle_selected(self):
        selected = self._selected()
        if not selected: return
        doc = deepcopy(self.history.value)
        bucket = doc['drawings'] if selected.startswith('d:') else doc['alerts']
        item = next((x for x in bucket if x['id']==selected[2:]),None)
        if not item: return
        key = 'visible' if selected.startswith('d:') else 'enabled'; item[key] = not item.get(key,True)
        if selected.startswith('d:'): self.history.commit(doc); self._save(); self.draw_overlay()
        else: self.history.value=validate_document(doc); self._save(); self.refresh_tree()

    def delete_selected(self):
        selected = self._selected()
        if not selected: return
        if not messagebox.askyesno('删除对象','删除所选对象？图形可用 Ctrl+Z 恢复；提醒删除不参与撤销。',parent=self.top): return
        doc = deepcopy(self.history.value); key = 'drawings' if selected.startswith('d:') else 'alerts'
        doc[key] = [item for item in doc[key] if item['id'] != selected[2:]]
        if key == 'drawings': self.history.commit(doc); self.selected_id=None
        else: self.history.value=validate_document(doc)
        self._save(); self.draw_overlay()

    def edit_selected(self):
        selected = self._selected()
        if not selected: return
        if selected.startswith('a:'):
            alert = next(a for a in self.history.value['alerts'] if a['id']==selected[2:])
            self.alert_dialog(alert); return
        obj = next(o for o in self.history.value['drawings'] if o['id']==selected[2:])
        text = simpledialog.askstring('编辑图形', '备注文字：', initialvalue=obj.get('text',''), parent=self.top)
        if text is None: return
        color = colorchooser.askcolor(obj.get('color','#26b9c5'), parent=self.top)[1]
        if color is None: return
        width = simpledialog.askinteger('编辑图形','线宽（1～8）：',initialvalue=obj.get('width',2),
                                        minvalue=1,maxvalue=8,parent=self.top)
        if width is None: return
        locked = messagebox.askyesnocancel('对象锁定',
            '是否锁定该对象？\n\n“是”锁定；“否”保持可拖动；“取消”放弃本次编辑。', parent=self.top)
        if locked is None: return
        doc=deepcopy(self.history.value); changed=next(o for o in doc['drawings'] if o['id']==obj['id'])
        changed.update(text=text,color=color,width=width,locked=locked)
        self.history.commit(doc); self._save(); self.draw_overlay()

    def _snapshot(self):
        if self._image is None:
            raise ValueError('图表尚未完成绘制')
        image = self._image.convert('RGB').copy(); draw = ImageDraw.Draw(image, 'RGBA')
        settings = self.history.value['settings']
        if not self.hide_indicators and settings.get('bar_count') and self.timeframe in ('5m','1h','4h'):
            candles, xs = self._info.get('candles',[]), self._info.get('candle_x',[])
            for count,x,row in zip(session_counts(pd.DataFrame(candles)),xs,candles):
                if count % int(settings['count_interval']) == 0:
                    point=self._xy([row['date'],row['high']])
                    if point: draw.text((x,point[1]-18),str(count),fill=settings['count_color'])
        if self.hide_drawings: return image
        for obj in self._visible_drawings():
            if not obj.get('visible',True): continue
            pts=[self._xy(p) for p in obj['points']]
            if any(p is None for p in pts): continue
            color=obj.get('color','#26b9c5'); width=int(obj.get('width',2)); kind=obj['kind']
            right=self._info.get('price_x',(0,image.width))[1]
            if kind=='fib':
                for level in obj.get('levels',[0,.5,1]):
                    y=pts[0][1]+(pts[1][1]-pts[0][1])*float(level)
                    draw.line((min(pts[0][0],pts[1][0]),y,right,y),fill=color,width=width)
            elif kind=='ray': draw.line((*pts[0],right,pts[0][1]),fill=color,width=width)
            elif kind=='arrow' and (obj.get('web') or {}).get('arrow_marker_version'):
                draw.polygon(_arrow_marker_points(*pts[:2]), fill=color)
            elif kind in ('trend','arrow'): draw.line((*pts[0],*pts[1]),fill=color,width=width)
            elif kind=='ruler':
                (x1,y1),(x2,y2)=pts
                draw.rectangle((min(x1,x2),min(y1,y2),max(x1,x2),max(y1,y2)),outline=color,width=width)
                draw.line(((x1+x2)/2,y1,(x1+x2)/2,y2),fill=color,width=width)
                draw.text(((x1+x2)/2,min(y1,y2)-14),_ruler_readout(obj),fill=color,anchor='mm')
            elif kind=='text': draw.text((pts[0][0]+4,pts[0][1]+4),obj.get('text') or '文字',fill=color)
            elif kind=='rectangle': draw.rectangle((*pts[0],*pts[1]),outline=color,width=width,fill='#33124466')
            elif kind in ('up','down'): draw.text(pts[0],'▲' if kind=='up' else '▼',fill=color,anchor='mm')
            elif kind=='path':
                draw.line([tuple(p) for p in pts],fill=color,width=width,joint='curve')
                start, end = _path_ends(obj)
                for enabled, tip, previous in ((start,pts[0],pts[1]),(end,pts[-1],pts[-2])):
                    if enabled:
                        angle=math.atan2(tip[1]-previous[1],tip[0]-previous[0])
                        for offset in (-.45,.45):
                            wing=(tip[0]-12*math.cos(angle+offset),tip[1]-12*math.sin(angle+offset))
                            draw.line((tuple(tip),wing),fill=color,width=width)
            elif kind=='channel':
                a,b,c=pts; dx,dy=c[0]-a[0],c[1]-a[1]
                draw.line((a,b),fill=color,width=width); draw.line((c,(b[0]+dx,b[1]+dy)),fill=color,width=width)
                draw.line(((a[0]+dx/2,a[1]+dy/2),(b[0]+dx/2,b[1]+dy/2)),fill=color,width=1)
            elif kind in ('long','short'):
                entry,stop,target,edge=pts; x1,x2=entry[0],edge[0]
                draw.rectangle((x1,target[1],x2,entry[1]),fill='#1f9d7a66',outline='#1f9d7a')
                draw.rectangle((x1,entry[1],x2,stop[1]),fill='#e34b5666',outline='#e34b56')
                draw.text((x1+5,entry[1]-16),risk_readout(obj)[0],fill='white')
            if kind!='text' and obj.get('text'): draw.text((pts[0][0]+4,pts[0][1]+4),obj['text'],fill=color)
        return image

    def save_snapshot(self):
        path=filedialog.asksaveasfilename(parent=self.top,title='保存干净图表快照',defaultextension='.png',
            initialfile=f"{self.code.replace('.','_')}-{self.timeframe}.png",filetypes=[('PNG 图像','*.png')])
        if not path:return
        temporary = None
        try:
            target=Path(path); temporary=target.with_name(target.name+'.aplus-tmp')
            self._snapshot().save(temporary,'PNG'); os.replace(temporary,target)
            self.status.set('快照已保存：'+path)
        except Exception as exc:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass
            messagebox.showerror('快照未保存',str(exc),parent=self.top)

    def copy_snapshot(self):
        try:
            if os.name!='nt': raise ValueError('当前系统暂不支持直接复制图像，请保存 PNG')
            image=self._snapshot(); stream=BytesIO(); image.save(stream,'BMP'); data=stream.getvalue()[14:]
            import ctypes
            GMEM_MOVEABLE, CF_DIB = 0x0002, 8
            kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
            handle=kernel.GlobalAlloc(GMEM_MOVEABLE,len(data)); pointer=kernel.GlobalLock(handle)
            ctypes.memmove(pointer,data,len(data)); kernel.GlobalUnlock(handle)
            if not user.OpenClipboard(None): raise OSError('无法打开剪贴板')
            try:
                user.EmptyClipboard(); user.SetClipboardData(CF_DIB,handle); handle=None
            finally: user.CloseClipboard()
            if handle: kernel.GlobalFree(handle)
            self.status.set('干净图表已复制到剪贴板')
        except Exception as exc: messagebox.showerror('复制失败',str(exc),parent=self.top)

    def _save(self):
        try:
            doc=deepcopy(self.history.value); doc['revision']=self.revision
            revision=self.store.save_chart_document(self.identity,doc,self.revision)
            self.revision=revision; self.history.value['revision']=revision
            for stack in (self.history.undo_stack,self.history.redo_stack):
                for item in stack:item['revision']=revision
            return True
        except Exception as exc:
            self.status.set('尚未保存：'+str(exc))
            messagebox.showerror('图表草稿尚未保存',str(exc)+'\n\n当前窗口仍保留修改。',parent=self.top)
            return False

    def _escape(self, _event=None):
        if self.pending_points:
            self.pending_points=[]; self.tool=None; self.draw_overlay(); return 'break'
        if self.replay_index is not None:
            self.exit_replay(); return 'break'
        self.selected_id=None; self.draw_overlay(); return 'break'

    def _destroyed(self, event):
        if event.widget is self.top and not self._closed:
            self._cleanup()

    def _cleanup(self):
        self._closed=True; self.replay_playing=False
        active = getattr(self.app_root, '_active_chart_workspaces', set())
        active.discard(self.identity)
        if self._after:
            try:self.top.after_cancel(self._after)
            except tk.TclError:pass
        if self._alert_after:
            try:self.top.after_cancel(self._alert_after)
            except tk.TclError:pass
        self._executor.shutdown(wait=False,cancel_futures=True)

    def close(self):
        if not self._closed:
            self._cleanup()
        self.top.destroy()


def open_chart_workspace(parent, store, **context):
    """Open the production WebView; keep the Tk canvas as a safe local fallback."""
    if os.environ.get('APLUS_TK_CHART_FALLBACK') != '1':
        try:
            from .chart_shell import launch_chart_workspace
            process = launch_chart_workspace(store, **context)
            if process is not None:
                return process
        except Exception as exc:
            messagebox.showwarning(
                '高保真图表暂不可用',
                '本机 WebView 未能启动，已切换到兼容图表；行情和图形记录不受影响。\n\n' + str(exc),
                parent=parent,
            )
    fallback = dict(context)
    fallback.pop('context_kind', None)
    return ChartWorkspaceDialog(parent, store, **fallback)
