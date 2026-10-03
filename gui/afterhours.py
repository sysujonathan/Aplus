"""Compact trader-oriented GAP H2 research, isolated from the premarket desk."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
import tkinter as tk
from tkinter import messagebox

import ttkbootstrap as ttk
from PIL import Image, ImageTk

from workbench.backtest import Assumptions
from workbench.h2_replay import H2Settings, LABELS, MODEL
from workbench.h2_replay_service import available_snapshots, load_receipt
from workbench.market import BOARDS, completed_date
from .data import code_names
from .h2_replay_chart import render_replay
from .theme import ACCENT, ACCENT_HOVER, BORDER, CHART_BG, CONTROL_BG, DOWN, MUTED, PANEL_BG, REPEAT, SELECTION, TEXT, UP
from .toolbar import format_board_scope


def panel(parent, background=PANEL_BG):
    frame = tk.Frame(parent, highlightthickness=1)
    frame.configure(bg=background, highlightbackground=BORDER)
    return frame


def surface(parent, **kwargs):
    frame = tk.Frame(parent, **kwargs)
    if 'bg' in kwargs:
        frame.configure(bg=kwargs['bg'])
    return frame


def label(parent, text=None, *, variable=None, size=13, bold=False, color=TEXT, **kwargs):
    kwargs.setdefault('anchor', tk.W)
    widget = tk.Label(parent, text=text, textvariable=variable, **kwargs)
    # Bootstrap's Tk constructor reapplies theme colors; explicitly set ours
    # afterwards. Negative font sizes are pixels, stable at Windows high DPI.
    widget.configure(bg=parent.cget('background'), fg=color,
                     font=('Microsoft YaHei UI', -size, 'bold' if bold else 'normal'))
    return widget


def button(parent, *, primary=False, **kwargs):
    return ttk.Button(parent, style='Research.Primary.TButton' if primary else 'Research.TButton', **kwargs)


def number(value, suffix='', percent=False):
    if value is None:
        return '—'
    return f'{value * (100 if percent else 1):.2f}{suffix}'


def list_prices(record):
    """Only a live pending opportunity gets current pending prices."""
    if record['filled']:
        return record['entry'], record['stop'], record['initial_risk_pct']
    if record['status'] == 'pending':
        plan = record['latest_plan']
        return plan.get('entry'), plan.get('stop'), plan.get('initial_risk_pct')
    return None, None, None


class AfterhoursPage(ttk.Frame):
    def __init__(self, parent, service=None, store=None):
        super().__init__(parent, padding=(16, 12))
        self.service, self.store = service, store
        self.settings, self.costs = H2Settings(), Assumptions()
        self.records, self.report = [], {}
        self.job = None
        self.receipts = {}
        self._loaded = False
        self._group = '全部'
        self._trade = None
        self._event_index = 0
        self._image = self._photo = None
        self._chart_cache = {}
        self._fit_after = None
        self._detail_visible = False
        self._names = {}
        self._compact_side = None
        self._edge_tab = 'profit'
        self._ui_scale = 1.0
        self.scope = tk.StringVar(value='market')
        self.board_vars = {board: tk.BooleanVar(value=True) for board in BOARDS}
        self.start = tk.StringVar(value=(date.fromisoformat(completed_date()) - timedelta(days=365)).isoformat())
        self.end = tk.StringVar(value=completed_date())
        self.status = tk.StringVar(value='选择区间与市场，开始本次研究。')
        self.filter = tk.StringVar(value='全部')
        self.history = tk.StringVar()
        self.posthoc = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build()
        self._font_widgets = []
        def collect_fonts(widget):
            if 'font' in widget.keys():
                parts = self.tk.splitlist(str(widget.cget('font')))
                if len(parts) >= 2 and str(parts[1]).lstrip('-').isdigit() and int(parts[1]) < 0:
                    self._font_widgets.append((widget, parts))
            for child in widget.winfo_children():
                collect_fonts(child)
        collect_fonts(self)
        self.bind('<Configure>', self._resize_density, add='+')
        self._poll_id = self.after(350, self._poll)
        self.bind('<Destroy>', self._destroyed, add='+')

    def _build(self):
        style = ttk.Style()
        style.configure('Research.Treeview', rowheight=30, font=('Microsoft YaHei UI', -13),
                        background=PANEL_BG, fieldbackground=PANEL_BG, foreground=TEXT, borderwidth=0)
        style.configure('Research.Treeview.Heading', font=('Microsoft YaHei UI', -13, 'bold'),
                        background=CONTROL_BG, foreground=MUTED, relief='flat')
        style.map('Research.Treeview', background=[('selected', SELECTION)],
                  foreground=[('selected', TEXT)])
        for name, background, edge in [('Research.TButton', CONTROL_BG, BORDER),
                                       ('Research.Primary.TButton', ACCENT, ACCENT)]:
            style.configure(name, background=background, bordercolor=edge, foreground=TEXT,
                            font=('Microsoft YaHei UI', -13), padding=(12, 7), relief='flat')
            style.map(name, foreground=[('disabled', MUTED), ('active', TEXT)],
                      background=[('disabled', PANEL_BG), ('active', SELECTION)],
                      bordercolor=[('focus', ACCENT_HOVER), ('active', ACCENT_HOVER)])
        style.configure('Research.TMenubutton', background=CONTROL_BG, foreground=TEXT,
                        bordercolor=BORDER, arrowcolor=TEXT, font=('Microsoft YaHei UI', -13), padding=(12, 7))
        style.map('Research.TMenubutton', background=[('active', SELECTION)], foreground=[('active', TEXT)],
                  bordercolor=[('focus', ACCENT_HOVER)])
        style.configure('Research.TCheckbutton', font=('Microsoft YaHei UI', -13))
        for name in ('Research.Vertical.TScrollbar', 'Research.Horizontal.TScrollbar'):
            style.configure(name, background=BORDER, troughcolor=PANEL_BG, bordercolor=PANEL_BG,
                            arrowcolor=MUTED, arrowsize=12, relief='flat')

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 12))
        ttk.Label(header, text='盘后回测', font=('Microsoft YaHei UI', -24, 'bold')).pack(side=tk.LEFT)
        ttk.Label(header, text='历史执行回放', foreground=MUTED, font=('Microsoft YaHei UI', -13)).pack(side=tk.LEFT, padx=12)
        button(header, text='数据与成交口径', command=self._receipt_dialog).pack(side=tk.RIGHT)
        self.history_box = ttk.Combobox(header, font=('Microsoft YaHei UI', -13), textvariable=self.history, state='readonly', width=31)
        self.history_box.pack(side=tk.RIGHT, padx=(8, 12))
        self.history_box.bind('<<ComboboxSelected>>', self._history_selected)
        ttk.Label(header, text='历史结果', foreground=MUTED, font=('Microsoft YaHei UI', -13)).pack(side=tk.RIGHT)

        self.conditions = panel(self)
        self.conditions.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))
        controls = surface(self.conditions, bg=PANEL_BG)
        controls.pack(fill=tk.X, padx=14, pady=12)
        label(controls, '策略', color=MUTED).pack(side=tk.LEFT, padx=(0, 8))
        strategy = ttk.Combobox(controls, font=('Microsoft YaHei UI', -13), values=['GAP H2 · 日线'], width=14, state='readonly')
        strategy.current(0)
        strategy.pack(side=tk.LEFT, padx=(0, 16))
        self.scope_button = ttk.Menubutton(controls, text='范围：全市场', style='Research.TMenubutton')
        self.scope_button.pack(side=tk.LEFT, padx=(0, 16))
        self.scope_menu = tk.Menu(self.scope_button, tearoff=0, background=PANEL_BG, foreground=TEXT,
                                  activebackground=SELECTION, activeforeground=TEXT)
        for board, variable in self.board_vars.items():
            self.scope_menu.add_checkbutton(label=board, variable=variable, command=self._board_changed)
        self.scope_menu.add_separator()
        self.scope_menu.add_radiobutton(label='关注名单', variable=self.scope, value='watch',
                                        command=self._scope_changed)
        self.scope_menu.configure(background=PANEL_BG, foreground=TEXT, activebackground=SELECTION,
                                  activeforeground=TEXT, font=('Microsoft YaHei UI', -13))
        self.scope_button.configure(menu=self.scope_menu)
        self._scope_changed()
        label(controls, '区间', color=MUTED).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Entry(controls, font=('Microsoft YaHei UI', -13), textvariable=self.start, width=11).pack(side=tk.LEFT)
        label(controls, '—', color=MUTED).pack(side=tk.LEFT, padx=6)
        ttk.Entry(controls, font=('Microsoft YaHei UI', -13), textvariable=self.end, width=11).pack(side=tk.LEFT, padx=(0, 12))
        button(controls, text='研究条件', command=self._settings_dialog).pack(side=tk.LEFT)
        self.run_button = button(controls, text='开始回测', command=self._run, primary=True, padding=(16, 6))
        self.run_button.pack(side=tk.RIGHT)
        self.stop_button = button(controls, text='停止', command=self._stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.RIGHT, padx=(10, 8))

        self.research_status = ttk.Frame(self)
        self.research_status.grid(row=2, column=0, sticky=tk.EW, pady=(0, 12))
        self.research_status.columnconfigure(1, weight=1)
        self.state = tk.StringVar(value='准备就绪')
        self.state_badge = tk.Label(self.research_status, textvariable=self.state, bg=CONTROL_BG,
                                   fg=ACCENT_HOVER, font=('Microsoft YaHei UI', -13, 'bold'), padx=10, pady=4)
        self.state_badge.grid(row=0, column=0, sticky=tk.NW, padx=(0, 10))
        self.status_label = ttk.Label(self.research_status, textvariable=self.status, foreground=MUTED, font=('Microsoft YaHei UI', -12))
        self.status_label.grid(row=0, column=1, sticky=tk.EW)
        self.research_status.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(300, e.width - 110)))
        self.progress = ttk.Progressbar(self.research_status, maximum=100, mode='determinate', length=100)
        self.progress.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0))
        self.progress.configure(style='Research.Horizontal.TProgressbar')
        style.configure('Research.Horizontal.TProgressbar', thickness=3, background=ACCENT,
                        troughcolor=CONTROL_BG, borderwidth=0)

        self.host = ttk.Frame(self)
        self.host.grid(row=3, column=0, sticky=tk.NSEW)
        self.host.rowconfigure(0, weight=1)
        self.host.columnconfigure(0, weight=1)
        self.home = ttk.Frame(self.host)
        self.home.grid(row=0, column=0, sticky=tk.NSEW)
        self.home.columnconfigure(0, weight=1)
        self.home.rowconfigure(1, weight=1)

        stats = ttk.Frame(self.home)
        stats.grid(row=0, column=0, sticky=tk.EW, pady=(0, 12))
        self.stats, self.stat_labels = [], []
        metrics = [('机会数', '区间内首次确认'), ('触发率', '触发 / 机会'),
                   ('胜率', '已结束交易'), ('平均净 R', '已结束 · 扣费后'),
                   ('初始风险', '已成交 · 中位数')]
        for i, (caption, note) in enumerate(metrics):
            stats.columnconfigure(i, weight=1, uniform='metric')
            card = panel(stats)
            card.grid(row=0, column=i, sticky=tk.EW, padx=(0, 10 if i < 4 else 0))
            surface(card, bg=ACCENT if i == 0 else BORDER, height=2).pack(fill=tk.X)
            inside = surface(card, bg=PANEL_BG)
            inside.pack(fill=tk.BOTH, padx=14, pady=(10, 12))
            label(inside, caption, color=MUTED).pack(anchor=tk.W)
            var = tk.StringVar(value='—')
            value = label(inside, variable=var, size=30, bold=True)
            value.pack(anchor=tk.W, pady=(2, 2))
            label(inside, note, size=11, color=MUTED).pack(anchor=tk.W)
            self.stats.append(var)
            self.stat_labels.append(value)

        body = ttk.Frame(self.home)
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=76, uniform='work')
        body.columnconfigure(1, weight=24, uniform='work')
        main = panel(body)
        main.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        filterbar = surface(main, bg=PANEL_BG)
        filterbar.grid(row=0, column=0, sticky=tk.EW, padx=14, pady=(12, 8))
        self.list_title = tk.StringVar(value='机会清单')
        label(filterbar, variable=self.list_title, size=15, bold=True).pack(side=tk.LEFT)
        self.open_button = button(filterbar, text='逐笔复盘 →', command=self._open_selected,
                                      state=tk.DISABLED)
        self.open_button.pack(side=tk.RIGHT)
        self.filter_box = ttk.Combobox(filterbar, font=('Microsoft YaHei UI', -13), textvariable=self.filter,
                                      values=['全部', *LABELS.values()], width=17, state='readonly')
        self.filter_box.pack(side=tk.RIGHT, padx=10)
        self.filter_box.bind('<<ComboboxSelected>>', lambda e: self._fill_rows())
        label(filterbar, '结局', color=MUTED).pack(side=tk.RIGHT)
        self.completeness = tk.StringVar(value='选择研究区间，开始回测。')
        label(main, variable=self.completeness, color=MUTED, size=11).grid(
            row=1, column=0, sticky=tk.EW, padx=14, pady=(0, 12))
        cols = ('date', 'code', 'name', 'entry', 'stop', 'risk', 'result', 'r')
        table = surface(main, bg=PANEL_BG)
        table.grid(row=2, column=0, sticky=tk.NSEW)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=cols, show='headings', selectmode='browse', style='Research.Treeview')
        for col, title, width in zip(cols, ['H2 日期', '代码', '名称', '成交 / 待挂价', 'SL1', '初始风险', '结局', '净 R'],
                                     [100, 96, 98, 110, 76, 86, 122, 68]):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=width,
                             anchor=tk.W if col in ('date', 'code', 'name', 'result') else tk.E)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview, style='Research.Vertical.TScrollbar')
        vertical.grid(row=0, column=1, sticky=tk.NS)
        self.tree.configure(yscrollcommand=vertical.set)
        horizontal = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=self.tree.xview, style='Research.Horizontal.TScrollbar')
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        self.tree.configure(xscrollcommand=horizontal.set)
        table.configure(width=1)
        table.grid_propagate(False)
        self.tree.tag_configure('profit', foreground=UP)
        self.tree.tag_configure('loss', foreground=DOWN)
        self.tree.bind('<Double-1>', lambda e: self._open_selected())
        self.tree.bind('<Return>', lambda e: self._open_selected())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.open_button.configure(
            state=tk.NORMAL if self.tree.selection() else tk.DISABLED))
        self.empty = surface(table, bg=PANEL_BG)
        self.empty_title = label(self.empty, '开始一次策略研究', size=18, bold=True)
        self.empty_title.pack(pady=(0, 8))
        self.empty_note = label(self.empty, '选择区间和市场，运行后在这里逐笔复盘。', color=MUTED)
        self.empty_note.pack()
        self.empty.place(relx=.5, rely=.45, anchor=tk.CENTER)
        label(main, '双击 / Enter 复盘    ·    ↑ ↓ 切换机会    ·    Esc 返回结果', color=MUTED, size=11).grid(
            row=3, column=0, sticky=tk.W, padx=14, pady=10)

        self.side = side = ttk.Frame(body)
        side.grid(row=0, column=1, sticky=tk.NSEW)
        side.columnconfigure(0, weight=1)
        self.boundary_trees = {}
        self.boundary_cards = {}
        self.edge_tabs = ttk.Frame(side)
        self.edge_buttons = {}
        for group, caption in [('profit', 'Top 盈利'), ('loss', 'Top 亏损')]:
            self.edge_buttons[group] = button(self.edge_tabs, text=caption,
                                              command=lambda g=group: self._select_edge_tab(g))
            self.edge_buttons[group].pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        for i, (group, caption, color) in enumerate([('profit', 'Top 盈利', UP), ('loss', 'Top 亏损', DOWN)]):
            side.rowconfigure(i, weight=4, uniform='edges')
            card = panel(side)
            self.boundary_cards[group] = card
            card.grid(row=i, column=0, sticky=tk.NSEW, pady=(0, 12))
            card.rowconfigure(1, weight=1)
            card.columnconfigure(0, weight=1)
            head = surface(card, bg=PANEL_BG)
            head.grid(row=0, column=0, sticky=tk.EW, padx=12, pady=10)
            label(head, caption, size=15, bold=True, color=color).pack(side=tk.LEFT)
            label(head, '净 R · 最多 5 笔', size=11, color=MUTED).pack(side=tk.RIGHT)
            tree = ttk.Treeview(card, columns=('code', 'r'), show='headings', height=3,
                               selectmode='browse', style='Research.Treeview')
            tree.heading('code', text='代码 / H2 日期')
            tree.heading('r', text='净 R')
            tree.column('code', width=150, minwidth=110)
            tree.column('r', width=65, minwidth=55, anchor=tk.E)
            tree.grid(row=1, column=0, sticky=tk.NSEW)
            scroll = ttk.Scrollbar(card, command=tree.yview, style='Research.Vertical.TScrollbar')
            scroll.grid(row=1, column=1, sticky=tk.NS)
            tree.configure(yscrollcommand=scroll.set)
            tree.tag_configure(group, foreground=color)
            tree.bind('<Double-1>', lambda e, g=group: self._open_boundary(g))
            tree.bind('<Return>', lambda e, g=group: self._open_boundary(g))
            self.boundary_trees[group] = tree
        side.rowconfigure(2, weight=3)
        distribution_card = panel(side)
        distribution_card.grid(row=2, column=0, sticky=tk.NSEW)
        distribution_card.columnconfigure(0, weight=1)
        distribution_card.rowconfigure(1, weight=1)
        label(distribution_card, '净 R 分布', size=15, bold=True).grid(row=0, column=0, sticky=tk.W, padx=12, pady=10)
        self.distribution = tk.StringVar(value='没有已结束交易')
        self.histogram = tk.Canvas(distribution_card, bg=PANEL_BG, height=90, highlightthickness=0)
        self.histogram.configure(bg=PANEL_BG)
        self.histogram.grid(row=1, column=0, sticky=tk.NSEW, padx=8)
        self.histogram.bind('<Configure>', lambda e: self._histogram())
        label(distribution_card, '仅已结束交易 · 未结束不计入', color=MUTED, size=11).grid(
            row=2, column=0, sticky=tk.W, padx=12, pady=(4, 10))
        side.bind('<Configure>', self._resize_side)

        self.detail = ttk.Frame(self.host)
        self.detail.grid(row=0, column=0, sticky=tk.NSEW)
        self.detail.rowconfigure(1, weight=1)
        self.detail.columnconfigure(0, weight=1)
        nav = ttk.Frame(self.detail)
        nav.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        button(nav, text='← 结果', command=self._back, ).pack(side=tk.LEFT)
        self.detail_title = tk.StringVar()
        ttk.Label(nav, textvariable=self.detail_title, font=('Microsoft YaHei UI', -16, 'bold')).pack(side=tk.LEFT, padx=12)
        ttk.Checkbutton(nav, text='完整走势（事后）', variable=self.posthoc, command=self._draw, style='Research.TCheckbutton').pack(side=tk.RIGHT)
        button(nav, text='下一笔 ↓', command=lambda: self._step_trade(1),
                   ).pack(side=tk.RIGHT, padx=(6, 16))
        button(nav, text='上一笔 ↑', command=lambda: self._step_trade(-1),
                   ).pack(side=tk.RIGHT)
        replay = ttk.Frame(self.detail)
        replay.grid(row=1, column=0, sticky=tk.NSEW)
        replay.rowconfigure(0, weight=1)
        replay.columnconfigure(0, weight=1)
        replay.columnconfigure(1, weight=0)
        chart_host = panel(replay, background=CHART_BG)
        chart_host.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12))
        chart_host.grid_propagate(False)
        chart_host.columnconfigure(0, weight=1)
        chart_host.rowconfigure(0, weight=1)
        self.chart = tk.Label(chart_host, bg=CHART_BG, fg=TEXT, text='选择一笔机会查看 K 线',
                              borderwidth=0, highlightthickness=0)
        self.chart.grid(row=0, column=0, sticky=tk.NSEW)
        self.chart.bind('<Configure>', self._schedule_fit)
        self.timeline_host = timeline_host = panel(replay)
        timeline_host.grid(row=0, column=1, sticky=tk.NSEW)
        timeline_host.configure(width=300)
        timeline_host.grid_propagate(False)
        timeline_host.rowconfigure(2, weight=1)
        timeline_host.columnconfigure(0, weight=1)
        label(timeline_host, '逐日复盘', size=17, bold=True).grid(row=0, column=0, sticky=tk.W, padx=14, pady=(14, 6))
        self.replay_mode = tk.StringVar(value='当时视图')
        self.mode_label = label(timeline_host, variable=self.replay_mode, size=13, color=ACCENT_HOVER)
        self.mode_label.grid(row=1, column=0, sticky=tk.W, padx=14, pady=(0, 12))
        self.timeline = ttk.Treeview(timeline_host, columns=('date', 'kind'), show='headings',
                                    selectmode='browse', style='Research.Treeview')
        self.timeline.heading('date', text='日期')
        self.timeline.heading('kind', text='事件')
        self.timeline.column('date', width=100, minwidth=96)
        self.timeline.column('kind', width=132, minwidth=100)
        self.timeline.grid(row=2, column=0, sticky=tk.NSEW)
        scroll = ttk.Scrollbar(timeline_host, command=self.timeline.yview, style='Research.Vertical.TScrollbar')
        scroll.grid(row=2, column=1, sticky=tk.NS)
        self.timeline.configure(yscrollcommand=scroll.set)
        self.timeline.tag_configure('key', foreground=ACCENT_HOVER)
        self.timeline.bind('<<TreeviewSelect>>', self._event_selected)
        event_card = surface(timeline_host, bg=CONTROL_BG, padx=12, pady=12)
        event_card.grid(row=3, column=0, columnspan=2, sticky=tk.EW, padx=12, pady=12)
        self.event_text = tk.StringVar()
        label(event_card, '当前事件', size=11, color=MUTED).pack(anchor=tk.W, pady=(0, 6))
        self.event_label = label(event_card, variable=self.event_text, wraplength=248, justify=tk.LEFT)
        self.event_label.pack(fill=tk.X)
        label(timeline_host, '← → 切换事件    ·    Esc 返回', color=MUTED, size=11).grid(
            row=4, column=0, sticky=tk.W, padx=14, pady=(0, 12))
        for widget in (self.detail, self.timeline, self.chart):
            for key, command in [('<Up>', lambda e: self._step_trade(-1)),
                                 ('<Down>', lambda e: self._step_trade(1)),
                                 ('<Left>', lambda e: self._step_event(-1)),
                                 ('<Right>', lambda e: self._step_event(1)),
                                 ('<Escape>', lambda e: self._back())]:
                widget.bind(key, command)
        self.home.tkraise()

    def refresh(self):
        if self.store is None:
            return
        self._names = code_names(self.store)
        self._refresh_history()
        if not self._loaded and self.receipts:
            self.history.set(next(iter(self.receipts)))
            self._history_selected()
        self._loaded = True

    def _selected_boards(self):
        return [board for board in BOARDS if self.board_vars[board].get()]

    def _resize_density(self, event):
        if event.widget is not self:
            return
        scale = min(1.6, max(1., round(event.width / 1600, 1)))
        if scale == self._ui_scale:
            return
        self._ui_scale = scale
        for widget, parts in self._font_widgets:
            widget.configure(font=(parts[0], round(int(parts[1]) * scale), *parts[2:]))
        style = ttk.Style()
        for name in ('Research.TButton', 'Research.Primary.TButton', 'Research.TMenubutton', 'Research.TCheckbutton'):
            style.configure(name, font=('Microsoft YaHei UI', -round(13 * scale)))
        style.configure('Research.Treeview', font=('Microsoft YaHei UI', -round(13 * scale)), rowheight=round(30 * scale))
        style.configure('Research.Treeview.Heading', font=('Microsoft YaHei UI', -round(13 * scale), 'bold'))
        self.timeline_host.configure(width=round(300 * scale))
        self.timeline.column('date', width=round(100 * scale), minwidth=round(96 * scale))
        self.timeline.column('kind', width=round(132 * scale), minwidth=round(100 * scale))
        self.event_label.configure(wraplength=round(248 * scale))
        self._compact_side = None
        self._resize_side()
        self._histogram()

    def _resize_side(self, event=None):
        height = event.height if event else self.side.winfo_height()
        compact = height < 480 * self._ui_scale
        if compact == self._compact_side:
            return
        self._compact_side = compact
        if compact:
            self.side.rowconfigure(0, weight=0, uniform='')
            self.side.rowconfigure(1, weight=1, uniform='')
            self.side.rowconfigure(2, weight=0, minsize=round(136 * self._ui_scale))
            self.edge_tabs.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
            for card in self.boundary_cards.values():
                card.grid(row=1, column=0)
            self._select_edge_tab(self._edge_tab)
        else:
            self.edge_tabs.grid_remove()
            self.side.rowconfigure(0, weight=4, uniform='edges')
            self.side.rowconfigure(1, weight=4, uniform='edges')
            self.side.rowconfigure(2, weight=3, minsize=round(136 * self._ui_scale))
            for i, card in enumerate(self.boundary_cards.values()):
                card.grid(row=i, column=0)

    def _select_edge_tab(self, group):
        self._edge_tab = group
        if self._compact_side:
            self.boundary_cards[group].tkraise()
        for key, widget in self.edge_buttons.items():
            widget.configure(style='Research.Primary.TButton' if key == group else 'Research.TButton')

    def _board_changed(self):
        self.scope.set('market')
        self._scope_changed()

    def _scope_changed(self):
        self.scope_button.configure(text='关注名单' if self.scope.get() == 'watch'
                                    else format_board_scope(self._selected_boards()))

    def _refresh_history(self):
        rows = self.store.rows("SELECT id,status,created,result FROM jobs WHERE kind='backtest' "
                               "AND status NOT IN ('queued','running') ORDER BY created DESC,rowid DESC")
        self.receipts = {}
        for row in rows:
            report = json.loads(row['result'])
            if report.get('execution_model') == MODEL:
                state = {'completed': '完成', 'partial': '部分完成', 'cancelled': '已停止'}.get(row['status'], '未完成')
                label = f'{row["created"][:16]} · {state} · {row["id"][:5]}'
                self.receipts[label] = row['id']
        self.history_box.configure(values=list(self.receipts))

    def _history_selected(self, _event=None):
        if self.job or self.history.get() not in self.receipts:
            return
        try:
            row, report, records = load_receipt(self.store, self.receipts[self.history.get()])
            self._show_report(row, report, records)
        except Exception as exc:
            self.status.set(f'回执读取失败：{exc}')

    def _run(self):
        if self.service is None or self.store is None:
            return
        try:
            start, end = self.start.get(), self.end.get()
            date.fromisoformat(start)
            date.fromisoformat(end)
            if start > end or end > completed_date():
                raise ValueError('请使用有效的已完成行情日期范围')
            scope = self.scope.get()
            boards = self._selected_boards() if scope == 'market' else None
            snapshots, missing = available_snapshots(self.store, scope, boards)
            if missing:
                raise ValueError(f'关注名单中 {len(missing)} 个标的未存日线，请先更新行情')
            if not snapshots:
                raise ValueError('没有可回放的真实行情；请先更新行情')
            self.job = self.service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily',
                execution_model=MODEL, datasets=[r['id'] for r in snapshots], start=start, end=end,
                scope=scope, boards=boards, h2_settings=asdict(self.settings), assumptions=asdict(self.costs)))
            self._back()
            self.report, self.records, self._trade = {}, [], None
            self._chart_cache.clear()
            self.history.set('')
            self.filter.set('全部')
            self.filter_box.configure(values=['全部', *LABELS.values()])
            self._fill_rows()
            for var in self.stats:
                var.set('—')
            for widget in self.stat_labels:
                widget.configure(fg=TEXT)
            for tree in self.boundary_trees.values():
                tree.delete(*tree.get_children())
            self.completeness.set('正在回放；完成或停止后才显示本次结果。')
            self.distribution.set('正在回放')
            self._histogram()
            self.run_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.history_box.configure(state=tk.DISABLED)
            self.state.set('正在回放')
            self.state_badge.configure(fg=ACCENT_HOVER)
            self.progress['value'] = 0
            self.status.set(f'准备回放 {len(snapshots)} 个标的')
        except Exception as exc:
            messagebox.showerror('不能开始回测', str(exc), parent=self)

    def _stop(self):
        if self.job:
            self.service.cancel(self.job)
            self.state.set('正在停止')
            self.status.set('正在停止；保留已完整回放的标的，结果会标记为部分。')

    def _poll(self):
        if self.job:
            rows = self.store.rows('SELECT * FROM jobs WHERE id=?', (self.job,))
            if rows:
                row = rows[0]
                self.status.set(row['message'])
                self.progress['value'] = row['progress'] / max(row['total'], 1) * 100
                if row['status'] not in ('running', 'queued'):
                    finished = self.job
                    self.job = None
                    self.run_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.history_box.configure(state='readonly')
                    self._refresh_history()
                    label = next((k for k, v in self.receipts.items() if v == finished), None)
                    if label:
                        self.history.set(label)
                        self._history_selected()
        self._poll_id = self.after(350, self._poll)

    def _show_report(self, row, report, records):
        self.report, self.records = report, records
        self.start.set(report['start'])
        self.end.set(report['end'])
        if report.get('h2_settings'):
            self.settings = H2Settings(**report['h2_settings'])
        if report.get('assumptions'):
            self.costs = Assumptions(**report['assumptions'])
        if report.get('scope') in ('market', 'watch'):
            self.scope.set(report['scope'])
        if report.get('boards'):
            for board, variable in self.board_vars.items():
                variable.set(board in report['boards'])
        self._scope_changed()
        self._chart_cache.clear()
        self._image = self._photo = None
        self.filter.set('全部')
        self.home.tkraise()
        self.conditions.grid()
        self.research_status.grid()
        self._detail_visible = False
        status = {'completed': '完成', 'partial': '部分完成', 'cancelled': '已停止'}.get(row['status'], row['status'])
        self.state.set(status)
        self.state_badge.configure(fg=ACCENT_HOVER if row['status'] == 'completed' else REPEAT)
        self.progress['value'] = report['processed_datasets'] / max(report['requested_datasets'], 1) * 100
        self.status.set(f'{status} · {report["start"]} 至 {report["end"]} · '
                        f'已回放 {report["processed_datasets"]}/{report["requested_datasets"]} 个标的 · '
                        f'覆盖提示 {len(report["coverage_warnings"])} · 失败 {len(report["errors"])}'
                        + (' · 导入研究，来源未认证' if not report['real_data'] else ''))
        performance = report.get('performance')
        if performance:
            self.status.set(self.status.get() + f' · 耗时 {performance["elapsed_seconds"]:.1f} 秒'
                            + f' · 复用 {performance["reused"]} 个标的')
        values = [str(report['opportunities']), number(report['trigger_rate'], '%', True),
                  number(report['win_rate'], '%', True), number(report['mean_r'], 'R'),
                  number(report['median_risk_pct'], '%')]
        for var, value in zip(self.stats, values):
            var.set(value)
        mean_r = report.get('mean_r')
        self.stat_labels[3].configure(fg=UP if mean_r is not None and mean_r > 0 else
                                     DOWN if mean_r is not None and mean_r < 0 else TEXT)
        self.completeness.set(f'触发 {report["triggered"]} · 模拟成交 {report["filled"]} · '
                              f'触发未成交 {report["unfilled"]} · 已结束 {report["closed_trades"]} · '
                              f'持仓未结束 {report["counts"]["open"]} · 条件过滤 {report["counts"]["filtered"]}')
        counts = report['counts']
        self.filter_box.configure(values=['全部', *[f'{label} ({counts[key]})' for key, label in LABELS.items()]])
        self._fill_rows()
        lookup = {r['id']: r for r in records}
        for group, tree in self.boundary_trees.items():
            tree.delete(*tree.get_children())
            for identity in report['boundaries'][group]:
                r = lookup[identity]
                tree.insert('', tk.END, iid=identity, tags=(group,),
                            values=(f'{r["code"]} / {r["setup_date"][5:]}', number(r['r_multiple'], 'R')))
        labels = ['< -2R', '-2 至 -1R', '-1 至 0R', '0 至 1R', '1 至 2R', '≥ 2R']
        self.distribution.set('\n'.join(f'{label:12}  {count} 笔' for label, count in zip(labels, report['distribution'])))
        self._histogram()

    def _histogram(self):
        self.histogram.delete('all')
        values = self.report.get('distribution', [])
        if not values or not sum(values):
            self.histogram.create_text(10, 38, text='没有已结束交易', fill=MUTED, anchor=tk.W,
                                       font=('Microsoft YaHei UI', -round(12 * self._ui_scale)))
            return
        width, height = max(180, self.histogram.winfo_width()), max(70, self.histogram.winfo_height())
        maximum = max(values)
        step = width / len(values)
        for i, value in enumerate(values):
            left, right = i * step + 5, (i + 1) * step - 5
            bottom = height - 24
            top = bottom - (height - 50) * value / maximum
            self.histogram.create_rectangle(left, top, right, bottom, fill=DOWN if i < 3 else UP, outline='')
            self.histogram.create_text((left + right) / 2, top - 8, text=str(value), fill=TEXT,
                                       font=('Consolas', -round(12 * self._ui_scale)))
            self.histogram.create_text((left + right) / 2, height - 10, text=['<-2', '-2~-1', '-1~0', '0~1', '1~2', '≥2'][i],
                                       fill=MUTED, font=('Consolas', -round(10 * self._ui_scale)))

    def _fill_rows(self):
        old = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        selected = self.filter.get().split(' (')[0]
        for r in self.records:
            if selected != '全部' and LABELS[r['status']] != selected:
                continue
            entry, stop, risk = list_prices(r)
            outcome = LABELS[r['status']]
            if r['status'] == 'closed':
                reason = r.get('reason', '')
                outcome = 'MM 止盈' if '止盈' in reason else '期限退出' if '期限' in reason else '止损'
            net_r = r.get('r_multiple') or 0
            tags = ('profit',) if net_r > 0 else ('loss',) if net_r < 0 else ()
            self.tree.insert('', tk.END, iid=r['id'], tags=tags, values=(r['setup_date'], r['code'], self._names.get(r['code'], ''),
                             number(entry), number(stop), number(risk, '%'), outcome, number(r.get('r_multiple'), 'R')))
        if old and self.tree.exists(old[0]):
            self.tree.selection_set(old[0])
        count = len(self.tree.get_children())
        self.list_title.set(f'机会清单 · {count} 笔' if self.report else '机会清单')
        self.open_button.configure(state=tk.NORMAL if self.tree.selection() else tk.DISABLED)
        if count:
            self.empty.place_forget()
        else:
            self.empty_title.configure(text='正在回放行情' if self.job else '当前筛选没有机会' if self.records
                                       else '本次没有符合条件的机会' if self.report else '开始一次策略研究')
            self.empty_note.configure(text='完成或停止后显示本次结果。' if self.job else
                                      '切换结局筛选，查看其他机会。' if self.records else
                                      '可调整区间或研究条件后再次运行。' if self.report else
                                      '选择区间和市场，运行后在这里逐笔复盘。')
            self.empty.place(relx=.5, rely=.45, anchor=tk.CENTER)

    def _open_selected(self):
        selected = self.tree.selection()
        if selected:
            self._group = '全部'
            self._open(selected[0])
        return 'break'

    def _open_boundary(self, group):
        selected = self.boundary_trees[group].selection()
        if selected:
            self._select_edge_tab(group)
            self._group = group
            self._open(selected[0])
        return 'break'

    def _open(self, identity):
        self._trade = next(r for r in self.records if r['id'] == identity)
        self._event_index = 0
        self.posthoc.set(False)
        self.detail.tkraise()
        self._detail_visible = True
        self.conditions.grid_remove()
        self.research_status.grid_remove()
        self.detail.focus_set()
        self.detail_title.set(f'{self._trade["code"]} · H2 {self._trade["setup_date"]} · {LABELS[self._trade["status"]]}')
        if self.report.get('complete') is False:
            self.detail_title.set(self.detail_title.get() + ' · 部分结果')
        self.timeline.delete(*self.timeline.get_children())
        titles = {'setup': 'H2 成立', 'plan': '更新次日计划', 'trigger': '价格触发', 'fill': '模拟成交',
                  'holding': '持仓', 'exit': '模拟退出', 't1': 'T+1 限制', 'blocked': '成交受限'}
        for i, event in enumerate(self._trade['events']):
            self.timeline.insert('', tk.END, iid=str(i), tags=('key',) if event['kind'] in ('setup', 'fill', 'exit') else (),
                                 values=(event['date'], titles.get(event['kind'], LABELS.get(event['kind'], event['kind']))))
        self.timeline.selection_set('0')
        self._draw()
        self.after_idle(self._fit)

    def _back(self):
        self.home.tkraise()
        self._detail_visible = False
        self.conditions.grid()
        self.research_status.grid()
        if self._trade:
            identity = self._trade['id']
            tree = self.tree if self._group == '全部' else self.boundary_trees[self._group]
            if tree.exists(identity):
                tree.selection_set(identity)
                tree.see(identity)
                tree.focus(identity)
                tree.focus_set()

    def _step_trade(self, delta):
        ids = list(self.tree.get_children()) if self._group == '全部' else self.report['boundaries'][self._group]
        if self._trade and self._trade['id'] in ids:
            i = ids.index(self._trade['id']) + delta
            if 0 <= i < len(ids):
                self._open(ids[i])
        return 'break'

    def _step_event(self, delta):
        if self._trade:
            i = self._event_index + delta
            if 0 <= i < len(self._trade['events']):
                self.timeline.selection_set(str(i))
                self.timeline.see(str(i))
        return 'break'

    def _event_selected(self, _event=None):
        selected = self.timeline.selection()
        if selected:
            self._event_index = int(selected[0])
            self._draw()

    def _draw(self):
        if not self._trade:
            return
        event = self._trade['events'][self._event_index]
        self.event_text.set(event['date'] + '\n' + event['text'] + '\n\n' +
                            ('事后走势：图中含此事件之后的数据' if self.posthoc.get() else '图中仅有此日及之前的数据'))
        self.replay_mode.set(('事后完整走势' if self.posthoc.get() else '当时视图') + ' · ' + event['date'])
        self.mode_label.configure(fg=REPEAT if self.posthoc.get() else ACCENT_HOVER)
        self._fit()

    def _schedule_fit(self, _event=None):
        if self._fit_after:
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(100, self._fit)

    def _fit(self):
        self._fit_after = None
        if not self._detail_visible or not self._trade:
            return
        width, height = self.chart.winfo_width(), self.chart.winfo_height()
        if width < 100 or height < 100:
            return
        key = (self._trade['id'], self._event_index, self.posthoc.get(), width, height)
        try:
            if key not in self._chart_cache:
                self._chart_cache[key] = render_replay(self.store, self._trade, self._event_index,
                                                     self.posthoc.get(), pixel_size=(width, height))
                if len(self._chart_cache) > 8:
                    self._chart_cache.pop(next(iter(self._chart_cache)))
            self._image = self._chart_cache[key]
            # Native rendering can round a pixel; match only that final boundary.
            image = self._image if self._image.size == (width, height) else self._image.resize(
                (width, height), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(image)
            self.chart.configure(image=self._photo, text='')
        except Exception as exc:
            self._image = self._photo = None
            self.chart.configure(image='', text=f'K 线读取失败：{exc}')

    def _settings_dialog(self):
        fields = [('lookback', '突破观察窗口（根）'), ('min_pullback', '最短回调（根）'),
                  ('max_pullback', '最长回调（根）'), ('wait_bars', 'H2 后等待期限（根）'),
                  ('max_risk_pct', '初始风险上限（%，0 不筛选）'), ('min_mm_r', '最低 MM 收益（R，0 不筛选）')]
        self._parameter_dialog('GAP H2 · 本次研究条件', self.settings, fields, H2Settings, 'settings',
                               '只作用于下次回测；不改变盘前正式策略。\n风险与 MM 是额外筛选条件，在 H2 成立日执行。')

    def _parameter_dialog(self, title, obj, fields, cls, attribute, note):
        dialog = ttk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, padding=20)
        content.pack(fill=tk.BOTH, expand=True)
        variables = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(content, text=label).grid(row=i, column=0, sticky=tk.W, pady=5)
            var = tk.StringVar(value=str(getattr(obj, key)))
            ttk.Entry(content, textvariable=var, width=14).grid(row=i, column=1, padx=12)
            variables[key] = var
        ttk.Label(content, text=note, foreground=MUTED, wraplength=460).grid(row=len(fields), column=0, columnspan=2, pady=15)
        def save():
            try:
                values = asdict(obj)
                for key, var in variables.items():
                    values[key] = int(var.get()) if type(getattr(obj, key)) is int else float(var.get())
                changed = cls(**values)
                changed.validate()
                setattr(self, attribute, changed)
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror('设置需要调整', str(exc), parent=dialog)
        ttk.Button(content, text='应用到下次回测', command=save).grid(row=len(fields) + 1, column=1)
        ttk.Button(content, text='恢复默认', command=lambda: [var.set(str(getattr(cls(), key))) for key, var in variables.items()],
                   bootstyle='secondary').grid(row=len(fields) + 1, column=0, sticky=tk.W)

    def _receipt_dialog(self):
        dialog = ttk.Toplevel(self)
        dialog.title('成交假设与回测回执')
        dialog.transient(self.winfo_toplevel())
        box = ttk.Frame(dialog, padding=18)
        box.pack(fill=tk.BOTH, expand=True)
        receipt = self.report
        settings = receipt.get('h2_settings', asdict(self.settings))
        costs = receipt.get('assumptions', asdict(self.costs))
        text = ('此结果的研究条件（未运行时显示下次条件）：\n'
                + ('市场板块：' + '、'.join(receipt['boards']) + '\n' if receipt.get('boards') else '')
                + f'突破观察 {settings["lookback"]} 根；回调 {settings["min_pullback"]}～{settings["max_pullback"]} 根；'
                + f'H2 后等待 {settings["wait_bars"]} 根\n'
                + f'风险上限 {settings["max_risk_pct"]}%；最低 MM {settings["min_mm_r"]}R（0 表示不筛选）\n\n'
                + f'成交假设：最长持有 {costs["holding_bars"]} 个交易日；单边佣金 {costs["commission_bps"]} 基点；'
                + f'卖出税费 {costs["sell_tax_bps"]} 基点；单边滑点 {costs["slippage_bps"]} 基点\n'
                + '1 基点 = 0.01%；假设值可按自己的交易成本调整。\n\n'
                + '\n'.join(receipt.get('limitations', ['仅使用已保存真实日线；保守成交，A 股 T+1。'])) + '\n\n'
                + '覆盖与失败：\n' + '\n'.join(f'{r.get("code", r.get("dataset", ""))}：{r.get("reason", r.get("error", ""))}'
                    for r in receipt.get('coverage_warnings', []) + receipt.get('errors', []))
                + '\n\n所用行情：\n' + '\n'.join(f'{r["code"]} · {r["start"]} 至 {r["end"]} · '
                    f'{r["source"]} · {r["adjustment"]}' for r in receipt.get('datasets', []))
                + '\n\n报告位置：' + receipt.get('report_path', '未运行')
                + '\n回放版本校验：' + receipt.get('engine_version', '未运行'))
        widget = tk.Text(box, width=95, height=30, wrap='word')
        widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(box, command=widget.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        widget.configure(yscrollcommand=scroll.set)
        widget.insert('1.0', text)
        widget.configure(state=tk.DISABLED)
        ttk.Button(dialog, text='调整下次成交假设', command=lambda: self._parameter_dialog(
            '下次回测 · 成交假设', self.costs,
            [('holding_bars', '最长持有（交易日）'), ('commission_bps', '单边佣金（基点）'),
             ('sell_tax_bps', '卖出税费（基点）'), ('slippage_bps', '单边滑点（基点）')],
            Assumptions, 'costs', '1 基点 = 0.01%；这是研究假设，不代表实际收费或成交。'),
            bootstyle='secondary').pack(pady=10)

    def _destroyed(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)
            if self._fit_after:
                self.after_cancel(self._fit_after)
