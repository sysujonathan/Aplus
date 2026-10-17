"""盘前任务工具栏：周期、范围、行情更新、扫描与交易日。"""
from __future__ import annotations

from datetime import date
import json
import logging
import time
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip

from .theme import MUTED
from workbench.sources import (market_source, set_market_source, SOURCES, UPDATE_SOURCES,
                               RETIRED_MESSAGE, update_selection)

_LABEL_FG = MUTED
_ALL = "全部"
_BOARD_SHORT = {
    "沪深主板": "主板",
    "创业板": "创业",
    "科创板": "科创",
    "北交所": "北交",
}
_STRATEGY_FALLBACK = (
    "MTR_MASTER",
    "STRATEGY_3K",
    "STRATEGY_STRUCTURAL_GAP",
    "STRATEGY_GAP_PINBAR",
    "STRATEGY_GAP_H2",
    "STRATEGY_AWIL",
)
_STRATEGY_SHORT = {
    "MTR_MASTER": "MTR",
    "STRATEGY_3K": "3K",
    "STRATEGY_STRUCTURAL_GAP": "GH1",
    "STRATEGY_GAP_PINBAR": "GPb",
    "STRATEGY_GAP_H2": "GH2",
    "STRATEGY_AWIL": "AWIL",
}


def format_board_scope(boards):
    """给紧凑工具栏使用的板块范围文案。"""
    from workbench.market import BOARDS

    selected = [board for board in BOARDS if board in boards]
    if selected == list(BOARDS):
        return "范围：全市场"
    if not selected:
        return "范围：未选择"
    return "范围：" + "+".join(_BOARD_SHORT[board] for board in selected)


def format_strategy_scope(strategies, available):
    """给扫描前置选择器使用的紧凑策略范围文案。"""
    available = list(available)
    selected = [key for key in available if key in strategies]
    if selected == available and available:
        return "策略：全部"
    if not selected:
        return "策略：未选择"
    return f"策略：{len(selected)}/{len(available)}"


def format_elapsed(seconds):
    """把运行秒数转换成适合状态栏的紧凑中文。"""
    seconds = max(0, int(round(float(seconds or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{seconds:02d}秒"
    if minutes:
        return f"{minutes}分{seconds:02d}秒"
    return f"{seconds}秒"


def format_task_timings(sync_elapsed=None, scan_elapsed=None, running_kind="", running_elapsed=None):
    """分别保留最近一次行情与扫描用时，运行时只更新对应一项。"""
    def item(label, value, running):
        if running:
            return f"{label}用时 进行中 {format_elapsed(running_elapsed)}"
        if isinstance(value, (int, float)):
            return f"{label}用时 {format_elapsed(value)}"
        return f"{label}用时 —"

    return " · ".join((
        item("行情", sync_elapsed, running_kind == "更新行情"),
        item("扫描", scan_elapsed, running_kind == "扫描策略"),
    ))


def format_scope_readiness(boards, audit):
    """说明当前所选板块有多少只真正可进入策略扫描。"""
    scope = format_board_scope(boards).removeprefix("范围：")
    if not boards:
        return "范围未选择"
    expected = int(audit.get("expected") or 0)
    if expected <= 0:
        return f"{scope}：范围待核验"
    ready = int(audit.get("ready") or 0)
    suspended = int(audit.get("suspended") or 0)
    gaps = len(audit.get("gaps") or [])
    return f"{scope}：可扫描 {ready}/应有 {expected} · 停牌 {suspended} · 缺口 {gaps}"


def format_header_data_status(market_date, signal_date, boards, audit):
    """右上角常驻摘要；详细覆盖仍保留在任务结果和状态信息中。"""
    market = str(market_date)[:10] if market_date else "无"
    signal = str(signal_date)[:10] if signal_date else "无"
    pending = bool(market_date and (not signal_date or market > signal))
    market_mark = "✓" if market_date else "—"
    scan_mark = "⚠待扫描" if pending else ("✓" if signal_date else "—")
    scope = format_board_scope(boards).removeprefix("范围：")
    expected = int((audit or {}).get("expected") or 0)
    ready = int((audit or {}).get("ready") or 0)
    coverage = f"{scope} {ready}/{expected}" if expected > 0 else f"{scope} 待核验"
    if (audit or {}).get('source') in SOURCES and (audit or {}).get('gaps'):
        if signal_date and not pending:
            scan_mark = '⚠部分'
        pending = sum(g.get('category') == 'quality_pending' for g in audit['gaps'])
        # The count already expresses usable/total. Details belong in the repair
        # entry; repeating exclusions here makes the persistent header wrap.
        if pending:
            coverage += f" · 待核验 {pending}"
    return f"行情 {market} {market_mark} · 信号 {signal} {scan_mark} · {coverage}"


def format_data_chain_status(market_date, signal_date, readiness=None):
    """把行情层和扫描层分开说清楚，避免把信号日误认成行情日。"""
    market = str(market_date)[:10] if market_date else "无"
    signal = str(signal_date)[:10] if signal_date else "无"
    pending = bool(market_date and (not signal_date or market > signal))
    market_mark = "✓" if market_date else "—"
    scan_mark = "⚠ 待扫描" if pending else ("✓" if signal_date else "—")
    text = f"行情 {market} {market_mark} · 信号 {signal} {scan_mark}"
    if readiness:
        text += " · " + readiness
    return text


def _two_years_ago():
    """当前日线扫描默认准备最近两年的覆盖。"""
    from datetime import date

    from workbench.market import completed_date

    d = date.fromisoformat(completed_date())
    try:
        return d.replace(year=d.year - 2).isoformat()
    except ValueError:  # 2/29 闰年边界
        return d.replace(year=d.year - 2, day=28).isoformat()


def sync_start_date(store, full_history=False, timeframe='daily'):
    """All sources prepare the same current day/week input, regardless of coverage."""
    if full_history:
        return "2016-01-01"
    # Daily needs <=300 bars; weekly needs <=300 completed weeks. Existing
    # long histories remain available, but cannot trigger an earlier bootstrap.
    start = _two_years_ago()
    return f'{date.fromisoformat(start).year-5}-01-01' if timeframe == 'weekly' else start


class ToolBar(ttk.Frame):
    def __init__(self, parent, service=None, store=None, tf_var=None,
                 on_timeframe_change=None, on_date_change=None, on_job_finished=None,
                 on_chart_page=None, on_chart_layout=None):
        super().__init__(parent)
        self.service = service
        self.store = store
        self._tf_var = tf_var or tk.StringVar(value="daily")
        self._on_tf = on_timeframe_change
        self._on_date = on_date_change
        self._on_job_finished = on_job_finished  # 任务结束回调：(任务名, 终态, 回执)，主窗口借此刷新列表
        self._on_chart_page = on_chart_page
        self._on_chart_layout = on_chart_layout
        self._job_id = None      # 当前提交的任务（submit 返回值），None=无任务
        self._job_kind = ""      # 任务显示名："下载行情" / "扫描策略"
        self._job_started_at = None
        self._auto_scan_after_sync = False  # 一键盘前：行情成功后自动衔接扫描
        self._auto_chain_cancelled = False  # 用户终止后禁止已完成行情继续衔接扫描
        self._auto_started_at = None
        self._auto_sync_elapsed = None
        self._sync_elapsed = None
        self._scan_elapsed = None
        # The first screen must not wait for thousands of status receipts.
        # Display summaries never replace the Service's scan-time audit.
        self._async_status_mode = True
        self._source_pending = False
        self._source_reader = self._status_reader = None
        self._job_reader = self._job_poll = None
        self._job_read_pending = False
        self._job_snapshot = None
        self._job_read_error = False
        self._status_context = self._status_snapshot = None
        self._source_poll = self._status_poll = None
        self._premarket_progress = {}
        self._mkt_var = tk.StringVar(value="行情：连接中…")  # 行情健康状态（常驻，只读）
        self._header_data_var = tk.StringVar(value="行情 连接中… · 信号 连接中…")
        self._timing_var = tk.StringVar(value=format_task_timings())
        self.full_history_var = tk.BooleanVar(value=False)    # 更早研究历史须显式选择
        try:
            from workbench.scope import selected_boards

            saved_boards = selected_boards(store) if store is not None else []
        except Exception:
            saved_boards = []
        from workbench.market import BOARDS

        if not saved_boards:
            saved_boards = list(BOARDS[:3])
        self.board_vars = {
            board: tk.BooleanVar(value=board in saved_boards) for board in BOARDS
        }
        try:
            from core.strategy_registry import StrategyRegistry

            available_strategies = list(StrategyRegistry.list_strategies())
        except Exception:
            available_strategies = list(_STRATEGY_FALLBACK)
        self._available_strategies = available_strategies
        self.strategy_vars = {
            key: tk.BooleanVar(value=True) for key in self._available_strategies
        }

        self.configure(padding=(10, 8))
        self._all_years = []
        self._all_months = []
        self._all_days = []
        self._months_by_year = {}
        self._days_by_ym = {}

        self.header = header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky=tk.W)
        self.actions = actions = ttk.Frame(self)
        actions.grid(row=0, column=1, sticky=tk.W)
        self.filters = filters = ttk.Frame(self)
        filters.grid(row=0, column=2, sticky=tk.W)
        self.columnconfigure(3, weight=1)
        # 日线 / 周线（只由用户手动切换）
        tf_f = ttk.Frame(header)
        tf_f.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(
            tf_f, text="日线", value="daily", variable=self._tf_var, command=self._fire_tf, bootstyle="toolbutton"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            tf_f, text="周线", value="weekly", variable=self._tf_var, command=self._fire_tf, bootstyle="toolbutton"
        ).pack(side=tk.LEFT)

        # 高频默认路径是一键盘前；两个分步任务继续保留，便于核验和迭代。
        auto_host = ttk.Frame(actions)
        self.btn_auto = ttk.Button(
            auto_host,
            text="一键盘前",
            command=self._on_auto,
            bootstyle="success",
            cursor="hand2",
        )
        # Reserve exactly the former button's requested width, including at DPI
        # scaling. The smaller label makes room for the menu inside that width.
        auto_width, auto_height = self.btn_auto.winfo_reqwidth(), self.btn_auto.winfo_reqheight()
        auto_host.configure(width=auto_width, height=auto_height)
        auto_host.pack_propagate(False)
        auto_host.pack(side=tk.LEFT, padx=(0, 8))
        self._auto_host = auto_host
        self._auto_original_width = auto_width
        self.btn_auto.configure(text='一键', padding=(2, 0))
        # Menubutton already draws its native arrow; adding a glyph repeats it
        # and steals space from the action label at larger display scaling.
        self.btn_auto_menu = ttk.Menubutton(auto_host, text='', padding=(2, 0), bootstyle='success')
        self.btn_auto_menu.pack(side=tk.RIGHT, fill=tk.Y)
        self.btn_auto.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._auto_tips = [
            ToolTip(self.btn_auto, text='一键完成盘前：复用已齐行情，自动补拉普通缺口，再扫描；中断后再点可续做。'),
            ToolTip(self.btn_auto_menu, text='选择一键盘前使用的数据源。'),
        ]
        self._auto_sources_saved = store.premarket_sources() if store else None
        initial = update_selection(self._auto_sources_saved, market_source(store) if store else 'baostock')
        self.auto_source_vars = {s: tk.BooleanVar(value=s in initial) for s in UPDATE_SOURCES}
        auto_menu = tk.Menu(self.btn_auto_menu, tearoff=0)
        auto_menu.add_command(label='一键盘前使用的数据源', state=tk.DISABLED)
        for source, variable in self.auto_source_vars.items():
            auto_menu.add_checkbutton(label=SOURCES[source], variable=variable,
                                      command=self._save_auto_sources)
        self.btn_auto_menu.configure(menu=auto_menu)
        ttk.Separator(actions, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 8), pady=3
        )
        ttk.Label(
            actions, text="① 行情", font=("Microsoft YaHei", 9, "bold"), foreground=_LABEL_FG
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.scope_button = ttk.Menubutton(
            actions,
            text=format_board_scope(self._selected_boards()),
            bootstyle="secondary-outline",
        )
        scope_menu = tk.Menu(self.scope_button, tearoff=0)
        for board, variable in self.board_vars.items():
            scope_menu.add_checkbutton(
                label=board,
                variable=variable,
                command=self._on_board_change,
            )
        self.scope_button.configure(menu=scope_menu)
        self.scope_button.pack(side=tk.LEFT, padx=(0, 5))
        self.source_var=tk.StringVar(value=SOURCES[market_source(store)] if store else 'BaoStock')
        self.source_combo=ttk.Combobox(actions,textvariable=self.source_var,values=list(SOURCES.values()),
                                       width=9,state='readonly')
        self.source_combo.pack(side=tk.LEFT,padx=(0,5))
        self.source_combo.bind('<<ComboboxSelected>>',self._source_changed)
        self.btn_sync = ttk.Button(
            actions, text="更新行情", command=self._on_sync, bootstyle="primary", cursor="hand2"
        )
        self.btn_sync.pack(side=tk.LEFT, padx=5)
        self.btn_integrity = ttk.Button(actions, text='完整性 / 补拉', width=12, command=self._show_tickflow_integrity,
                                        bootstyle='warning-outline', cursor='hand2')
        # Shared integrity entry; only UI preferences vary by source.
        ttk.Separator(actions, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(8, 8), pady=3
        )
        ttk.Label(
            actions, text="② 扫描", font=("Microsoft YaHei", 9, "bold"), foreground=_LABEL_FG
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.strategy_scope_button = ttk.Menubutton(
            actions,
            text=format_strategy_scope(
                self._selected_strategies(), self._available_strategies
            ),
            bootstyle="secondary-outline",
        )
        strategy_menu = tk.Menu(self.strategy_scope_button, tearoff=0)
        strategy_menu.add_command(
            label="全部策略", command=lambda: self._set_all_strategies(True)
        )
        strategy_menu.add_command(
            label="清空选择", command=lambda: self._set_all_strategies(False)
        )
        strategy_menu.add_separator()
        for key, variable in self.strategy_vars.items():
            short = _STRATEGY_SHORT.get(key, key)
            strategy_menu.add_checkbutton(
                label=f"{short}  ·  {key}",
                variable=variable,
                command=self._on_strategy_change,
            )
        self.strategy_scope_button.configure(menu=strategy_menu)
        self.strategy_scope_button.pack(side=tk.LEFT, padx=(0, 5))
        self.btn_scan = ttk.Button(
            actions, text="策略扫描", command=self._on_scan, bootstyle="primary", cursor="hand2"
        )
        self.btn_scan.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(actions, text="终止", command=self._on_stop, bootstyle="danger")
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        self.btn_stop.configure(state=tk.DISABLED)
        sync_menu = tk.Menu(self, tearoff=0)
        sync_menu.add_checkbutton(label="完整历史（2016 年起）", variable=self.full_history_var)
        self.btn_sync.bind("<Button-3>", lambda e: sync_menu.tk_popup(e.x_root, e.y_root))
        self.ai_var = tk.BooleanVar(value=False)
        self.chk_ai = ttk.Checkbutton(actions, text="AI 复核", variable=self.ai_var,
                                     bootstyle="round-toggle", command=self._explain_ai)
        self.chk_ai.pack(side=tk.LEFT, padx=(12, 0))

        # 日期栏同时包含行情日和扫描日；启动仍定位最近一次有效扫描结果。
        self.date_label = ttk.Label(filters, text="交易日", font=("Microsoft YaHei", 10), foreground=_LABEL_FG)
        self.date_label.pack(side=tk.LEFT, padx=(12, 4))
        self.year_var = tk.StringVar(value=_ALL)
        self.month_var = tk.StringVar(value=_ALL)
        self.day_var = tk.StringVar(value=_ALL)
        combo_kw = {"state": "readonly", "font": ("Consolas", 11)}
        self.year_combo = ttk.Combobox(
            filters, textvariable=self.year_var, width=5, **combo_kw
        )
        self.year_combo.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(filters, text="-", foreground=_LABEL_FG).pack(side=tk.LEFT)
        self.month_combo = ttk.Combobox(
            filters, textvariable=self.month_var, width=3, **combo_kw
        )
        self.month_combo.pack(side=tk.LEFT, padx=(2, 2))
        ttk.Label(filters, text="-", foreground=_LABEL_FG).pack(side=tk.LEFT)
        self.day_combo = ttk.Combobox(
            filters, textvariable=self.day_var, width=3, **combo_kw
        )
        self.day_combo.pack(side=tk.LEFT, padx=(2, 0))
        self.year_combo.bind("<<ComboboxSelected>>", self._on_year_change)
        self.month_combo.bind("<<ComboboxSelected>>", self._on_month_change)
        self.day_combo.bind("<<ComboboxSelected>>", self._on_day_change)

        # TradingView 式多图控制：布局选择 + 整组左右翻页，放在信号日同一行。
        ttk.Separator(filters, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(12, 8), pady=2
        )
        self.layout_var = tk.StringVar(value="1×1")
        self.layout_combo = ttk.Combobox(
            filters,
            textvariable=self.layout_var,
            values=("1×1", "2×2", "2×3", "3×3"),
            state="readonly",
            width=3,
            font=("Consolas", 9),
        )
        self.layout_combo.pack(side=tk.LEFT, padx=(0, 5))
        self.layout_combo.bind("<<ComboboxSelected>>", self._fire_layout)
        self.btn_chart_prev = ttk.Button(
            filters,
            text="‹",
            width=2,
            command=lambda: self._fire_chart_page(-1),
            bootstyle="primary",
            padding=(2, 0),
            cursor="hand2",
        )
        self.btn_chart_prev.pack(side=tk.LEFT, padx=(0, 2), pady=3)
        self.btn_chart_next = ttk.Button(
            filters,
            text="›",
            width=2,
            command=lambda: self._fire_chart_page(1),
            bootstyle="primary",
            padding=(2, 0),
            cursor="hand2",
        )
        self.btn_chart_next.pack(side=tk.LEFT, padx=(0, 2), pady=3)

        # 依赖 store 的真实信号日填充下拉
        self._load_date_options()
        self._select_latest_date()
        # 常驻行情健康状态（只读，不依赖任务）
        self._initial_status = self.after_idle(self._load_market_status)

        self._status = tk.StringVar(value=self._mkt_var.get())
        # 右上角始终保留数据版本和分项用时；底部只承担当前进度、结果和临时提示。
        self.status_panel = ttk.Frame(self)
        self.status_panel.grid(row=0, column=3, sticky=tk.E, padx=(12, 6))
        self.status_label = ttk.Label(
            self.status_panel,
            width=1,
            font=("Microsoft YaHei UI", 9),
            foreground=_LABEL_FG,
            anchor=tk.E,
        )
        self.status_label.configure(justify=tk.RIGHT)
        self.status_label.pack(anchor=tk.E, fill=tk.X)
        self.timing_label = ttk.Label(
            self.status_panel,
            width=1,
            font=("Consolas", 9),
            foreground=_LABEL_FG,
            anchor=tk.E,
        )
        self.timing_label.configure(justify=tk.RIGHT)
        self.timing_label.pack(anchor=tk.E, fill=tk.X)
        self._status_tips = [ToolTip(label, text='', wraplength=900) for label in
                             (self.status_label, self.timing_label)]
        self._layout_traces = [
            (value, value.trace_add("write", self._queue_responsive))
            for value in (self._header_data_var, self._timing_var)
        ]
        for frame in (self.header, self.actions, self.filters):
            frame.bind("<Configure>", self._queue_responsive)
        self.bind("<Configure>", self._responsive)
        self.bind("<Destroy>", self._cancel_responsive)
        self._queue_responsive()

    def _queue_responsive(self, *_):
        # Text/source/repair-button changes can grow without resizing the window.
        if self.winfo_exists():
            self._responsive()

    def _cancel_responsive(self, event):
        if event.widget is not self:
            return
        if getattr(self, '_initial_status', None):
            self.after_cancel(self._initial_status)
            self._initial_status = None
        for value, trace in self._layout_traces:
            value.trace_remove("write", trace)
        self._layout_traces = []
        for tip in self._status_tips:
            tip.leave()
        for reader in (self._source_reader, self._status_reader, self._job_reader):
            if reader:
                reader.close()
        for poll in (self._source_poll, self._status_poll, self._job_poll):
            if poll:
                self.after_cancel(poll)

    def _status_required_width(self):
        # A fixed, font-aware minimum for sharing the controls row, not the
        # worst-case full receipt width. Longer text is fitted to the remaining
        # space without adding a blank row or changing the chart's height.
        return int(self.tk.call("font", "measure", self.status_label.cget("font"),
                                "-displayof", self._w,
                                '行情 2099-12-31 · 信号 2099-12-31')) + 8

    def _fit_status(self, budget):
        for label, value, tip in zip((self.status_label, self.timing_label),
                                     (self._header_data_var, self._timing_var), self._status_tips):
            raw = value.get()
            text = raw.replace('\n', ' · ')
            measure = lambda s: int(self.tk.call('font', 'measure', label.cget('font'), '-displayof', self._w, s))
            # Share the available right-hand space; keep the source and final
            # counts visible. Hover always shows the complete, unchanged receipt.
            if measure(text) > budget:
                low, high = 0, len(text)
                while low < high:
                    n = (low + high + 1) // 2
                    candidate = text[:(n+1)//2] + '…' + (text[-(n//2):] if n//2 else '')
                    if measure(candidate) <= budget:
                        low = n
                    else:
                        high = n - 1
                text = text[:(low+1)//2] + '…' + (text[-(low//2):] if low//2 else '')
            label.configure(text=text, wraplength=0)
            tip.text = raw

    def _responsive(self, event=None):
        if event is not None and event.widget is not self:
            return
        width = event.width if event is not None else self.winfo_width()
        header_width = self.header.winfo_reqwidth()
        actions_width = self.actions.winfo_reqwidth()
        filters_width = self.filters.winfo_reqwidth()
        needed = header_width + actions_width + filters_width + 40
        status_width = self._status_required_width()
        # Prefer the controls row even if the full receipt needs middle elision.
        # Fall back only when a useful minimum status panel cannot fit beside it.
        budget = max(1, width - 40)
        if width >= needed + status_width:
            self.actions.grid(row=0, column=1, columnspan=1, sticky=tk.W, pady=0)
            self.filters.grid(row=0, column=2, columnspan=1, sticky=tk.W, pady=0)
            self.status_panel.grid(row=0, column=3, columnspan=1, sticky=tk.EW, pady=0)
            budget = max(1, width - needed - 18)
        elif width >= needed:
            self.actions.grid(row=0, column=1, columnspan=1, sticky=tk.W, pady=0)
            self.filters.grid(row=0, column=2, columnspan=1, sticky=tk.W, pady=0)
            self.status_panel.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        elif width >= actions_width + filters_width + 40:
            self.actions.grid(row=1, column=0, columnspan=1, sticky=tk.W, pady=(6, 0))
            self.filters.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(6, 0))
            self.status_panel.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        else:
            self.actions.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
            self.filters.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))
            self.status_panel.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        self._fit_status(budget)

    def _explain_ai(self):
        from tkinter import messagebox
        self.ai_var.set(False)
        messagebox.showinfo("AI 复核", "当前 Aplus 后端尚未接入 AI 复核；本轮仅恢复界面，扫描仍使用原策略。", parent=self)

    def selected_date(self):
        return tuple(v.get() if v.get() != _ALL else None
                     for v in (self.year_var, self.month_var, self.day_var))

    def _integrity_asof(self):
        """Review the selected full day; broad filters use daily readiness."""
        from workbench.market import completed_date
        parts = self.selected_date()
        return date(*(int(v) for v in parts)).isoformat() if all(parts) else completed_date()

    def _selected_boards(self):
        """按固定市场顺序返回当前勾选范围。"""
        from workbench.market import BOARDS

        return [board for board in BOARDS if self.board_vars[board].get()]

    def _selected_strategies(self):
        """按注册顺序返回本轮启用策略。"""
        return [
            key for key in self._available_strategies
            if self.strategy_vars[key].get()
        ]

    def _set_all_strategies(self, selected):
        for variable in self.strategy_vars.values():
            variable.set(bool(selected))
        self._on_strategy_change()

    def _on_strategy_change(self):
        strategies = self._selected_strategies()
        self.strategy_scope_button.configure(
            text=format_strategy_scope(strategies, self._available_strategies)
        )
        if strategies:
            self.set_status(f"本轮扫描 {len(strategies)} 个策略")
        else:
            self.set_status("请至少选择一个扫描策略")

    def _on_board_change(self):
        boards = self._selected_boards()
        self.scope_button.configure(text=format_board_scope(boards))
        if not boards:
            self.set_status("请至少选择一个行情板块")
            return
        if self.store is not None:
            try:
                from workbench.scope import save_boards

                save_boards(self.store, boards)
            except Exception as exc:
                self.set_status(f"行情范围保存失败：{exc}")
                return
        self._load_market_status()
        self.set_status("行情与扫描范围：" + "、".join(boards))

    def _select_latest_date(self):
        if not self.store:
            return
        from .data import latest_candidate_date

        day = latest_candidate_date(self.store, self._tf_var.get())
        if not day:
            for value in (self.year_var, self.month_var, self.day_var):
                value.set(_ALL)
        if day:
            self._select_date(day)

    def _select_date(self, day):
        """Select one real scan day without firing a second user-change event."""
        parsed = self._parse_asof(day)
        if parsed is None:
            return False
        y, m, d = parsed
        if y not in self._all_years or m not in self._months_by_year.get(y, []):
            return False
        if d not in self._days_by_ym.get((y, m), []):
            return False
        self.year_var.set(y)
        self._refresh_months()
        self.month_var.set(m)
        self._refresh_days()
        self.day_var.set(d)
        return True

    # ---- 周期切换 ----
    def _fire_tf(self):
        if self._source_pending:
            return
        self._load_date_options()
        self._select_latest_date()
        self._load_market_status()
        if self._on_tf:
            self._on_tf(self._tf_var.get())

    def set_status(self, text):
        self._status.set(text)

    def set_chart_page_status(self, start, end, total):
        if start <= 1:
            self.btn_chart_prev.configure(state=tk.DISABLED)
        else:
            self.btn_chart_prev.configure(state=tk.NORMAL)
        if not total or end >= total:
            self.btn_chart_next.configure(state=tk.DISABLED)
        else:
            self.btn_chart_next.configure(state=tk.NORMAL)

    def _fire_chart_page(self, delta):
        if self._on_chart_page:
            self._on_chart_page(delta)

    def set_chart_source(self, label):
        # 来源由右侧对应列表的选中状态表达，顶部不再重复占位显示。
        return None

    def _fire_layout(self, _event=None):
        label = self.layout_var.get().strip().lower().replace("✖", "×").replace("x", "×")
        count = {"1×1": 1, "2×2": 4, "2×3": 6, "3×3": 9}.get(label, 4)
        if self._on_chart_layout:
            self._on_chart_layout(count)

    # ---- 交易日三联 Combobox 联动 ----
    def _load_date_options(self):
        self._all_years = []
        self._all_months = []
        self._all_days = []
        self._months_by_year = {}
        self._days_by_ym = {}
        if self.store is None:
            self.year_combo["values"] = [_ALL]
            self.month_combo["values"] = [_ALL]
            self.day_combo["values"] = [_ALL]
            return
        rows = []
        try:
            from .data import candidate_dates

            rows = [{"asof": value} for value in candidate_dates(
                self.store, self._tf_var.get()
            )]
        except Exception:
            rows = []
        for r in rows:
            ymd = self._parse_asof(r["asof"])
            if ymd is None:
                continue
            y, m, d = ymd
            if y not in self._all_years:
                self._all_years.append(y)
            if m not in self._all_months:
                self._all_months.append(m)
            if d not in self._all_days:
                self._all_days.append(d)
            self._months_by_year.setdefault(y, [])
            if m not in self._months_by_year[y]:
                self._months_by_year[y].append(m)
            self._days_by_ym.setdefault((y, m), [])
            if d not in self._days_by_ym[(y, m)]:
                self._days_by_ym[(y, m)].append(d)
        self._all_years.sort()
        self._all_months.sort()
        self._all_days.sort()
        for y in self._months_by_year:
            self._months_by_year[y].sort()
        for k in self._days_by_ym:
            self._days_by_ym[k].sort()
        self.year_combo["values"] = [_ALL] + self._all_years
        if self.year_var.get() not in self.year_combo["values"]:
            self.year_var.set(_ALL)
        # 任务完成后会在保留当前选择的情况下重载日期。这里必须重新按
        # 当前年月联动，不能把其他月份出现过的日号塞进当前月份。
        self._refresh_months()
        self._refresh_days()

    @staticmethod
    def _parse_asof(asof):
        if not asof:
            return None
        date_part = str(asof).split(" ")[0][:10]
        try:
            parsed = date.fromisoformat(date_part)
        except ValueError:
            return None
        return f"{parsed.year:04d}", f"{parsed.month:02d}", f"{parsed.day:02d}"

    def _refresh_months(self):
        y = self.year_var.get()
        if y == _ALL:
            months = self._all_months
        else:
            months = self._months_by_year.get(y, [])
        self.month_combo["values"] = [_ALL] + months
        if self.month_var.get() not in self.month_combo["values"]:
            self.month_var.set(_ALL)

    def _refresh_days(self):
        y = self.year_var.get()
        m = self.month_var.get()
        days = sorted({
            day
            for (year, month), values in self._days_by_ym.items()
            if (y == _ALL or year == y) and (m == _ALL or month == m)
            for day in values
        })
        self.day_combo["values"] = [_ALL] + days
        if self.day_var.get() not in self.day_combo["values"]:
            self.day_var.set(_ALL)

    def _on_year_change(self, _e=None):
        self._refresh_months()
        self.month_var.set(_ALL)
        self._refresh_days()
        self.day_var.set(_ALL)
        self._fire_date()

    def _on_month_change(self, _e=None):
        self._refresh_days()
        self.day_var.set(_ALL)
        self._fire_date()

    def _on_day_change(self, _e=None):
        self._fire_date()

    def _fire_date(self):
        if self._source_pending:
            return
        y = self.year_var.get()
        m = self.month_var.get()
        d = self.day_var.get()
        y = None if y == _ALL else y
        m = None if m == _ALL else m
        d = None if d == _ALL else d
        if self._on_date:
            self._on_date(y, m, d)
        self._load_market_status()

    # ---- 行情健康状态（常驻，只读现有表）----
    def _load_market_status(self, force=False):
        if self.store is None:
            self._mkt_var.set("行情：未连接")
            self._header_data_var.set("行情 无 — · 信号 无 — · 范围待核验")
            return self._mkt_var.get()
        if not self._job_id:
            self.btn_sync.state(['disabled'] if self._display_source() not in UPDATE_SOURCES else ['!disabled'])
        from .data import latest_market_date, latest_scan_date

        if self._async_status_mode or self._job_kind == '一键盘前':
            return self._load_status_async(force)

        market = latest_market_date(self.store, self._tf_var.get())
        signal = latest_scan_date(self.store, self._tf_var.get())
        boards = self._selected_boards()
        audit = {}
        try:
            from workbench.readiness import audit_scope

            audit = audit_scope(self.store, boards, self._integrity_asof(), timeframe=self._tf_var.get(),history_cache_only=True)
        except Exception:
            audit = {}
        return self._render_market_status(market, signal, boards, audit)

    def _render_market_status(self, market, signal, boards, audit, pending=None):
        readiness = format_scope_readiness(boards, audit)
        text = format_data_chain_status(market, signal, readiness)
        self._tickflow_audit = audit
        if hasattr(self,'btn_integrity'):
            if self._display_source() in SOURCES:
                from workbench.gap_review import pending_count
                count=(pending if pending is not None else pending_count(self.store,audit,self._tf_var.get())) if audit.get('scope_valid') else 0
                label=(f'缺口处理 {count}' if count else '缺口分类') if audit.get('scope_valid') else '范围待核验'
                self.btn_integrity.configure(text=label)
                self.btn_integrity.pack(side=tk.LEFT,padx=3,after=self.btn_sync)
            else:
                self.btn_integrity.pack_forget()
        self._mkt_var.set(text)
        self._header_data_var.set(
            SOURCES[self._display_source()]+' · '+format_header_data_status(market, signal, boards, audit)
        )
        return text

    def _display_source(self):
        # The display selection is already in Tk; progress painting
        # must not make another blocking query on the busy business database.
        return next((key for key,label in SOURCES.items() if label == self.source_var.get()), 'baostock')

    def _status_key(self):
        return (self._display_source(), tuple(self._selected_boards()), self._integrity_asof(), self._tf_var.get())

    def _load_status_async(self, force=False):
        from .background_status import LatestRefresh, load_status
        context = self._status_key()
        if force:
            self._status_snapshot = None
        if self._status_snapshot and self._status_snapshot[0] == context:
            item = self._status_snapshot[1]
            return self._render_market_status(item['market'], item['signal'], context[1], item['audit'], item['pending'])
        if self._status_reader is None:
            root = self.store.root
            self._status_reader = LatestRefresh(lambda value, check: load_status(root, value, check))
        if self._status_context != context or force:
            self._status_context = context
            self._status_reader.submit(context)
        self._tickflow_audit = {}
        text = SOURCES[context[0]]+' · 正在后台核对完整性；可继续查看已保存结果'
        self._mkt_var.set(text)
        self._header_data_var.set(text)
        self.btn_integrity.configure(text='完整性刷新中')
        if self._status_poll is None:
            self._status_poll = self.after(50, self._poll_status)
        return text

    def _poll_status(self):
        self._status_poll = None
        reply = self._status_reader.poll()
        if reply:
            _, context, item, error = reply
            self._status_context = None
            if not self._source_pending and context == self._status_key():
                if error:
                    self._header_data_var.set(SOURCES[context[0]]+' · 完整性暂未读出，可稍后重试')
                    self.btn_integrity.configure(text='完整性待刷新')
                else:
                    self._status_snapshot = (context, item)
                    self._render_market_status(item['market'], item['signal'], context[1], item['audit'], item['pending'])
            return
        self._status_poll = self.after(50, self._poll_status)

    def _refresh_timing_status(self, running_elapsed=None):
        """刷新右上角计时，不让底部临时消息覆盖已经完成的分项用时。"""
        sync_elapsed = getattr(self, "_sync_elapsed", None)
        scan_elapsed = getattr(self, "_scan_elapsed", None)
        if getattr(self,'_job_kind','')=='更新行情' and isinstance(running_elapsed,(int,float)):
            base=getattr(self,'_job_sync_base',0)
            if isinstance(base,(int,float)):
                running_elapsed += base
        self._timing_var.set(format_task_timings(
            sync_elapsed if isinstance(sync_elapsed, (int, float)) else None,
            scan_elapsed if isinstance(scan_elapsed, (int, float)) else None,
            getattr(self, "_job_kind", ""),
            running_elapsed,
        ))

    def data_chain_summary(self):
        if self.store is None:
            return "行情未连接"
        from .data import latest_market_date, latest_scan_date

        return format_data_chain_status(
            latest_market_date(self.store, self._tf_var.get()),
            latest_scan_date(self.store, self._tf_var.get()),
        )

    # ---- 动作（提交 service 任务 + 状态栏实时反馈）----
    def _show_tickflow_integrity(self):
        if self.store is None or market_source(self.store) not in SOURCES:
            return
        source=market_source(self.store)
        if self._source_pending:
            self.set_status('正在切换来源，请稍后打开缺口分类')
            return
        from workbench.gap_review import review_receipt
        from workbench.readiness import expected_day
        from workbench.market import completed_date
        try:
            day=expected_day(self.store,self._integrity_asof(),source)
            report=review_receipt(self.store,source,self._selected_boards(),day,self._tf_var.get())
        except ValueError as exc:
            self.set_status(str(exc))
            return
        from .tickflow_integrity import show_integrity
        def repair_selected(codes,timeframe,include_history):
            if market_source(self.store)!=source:
                raise ValueError('数据源已切换，请重新打开当前来源的缺口分类')
            if day != expected_day(self.store,completed_date(),source):
                raise ValueError('当前查看的是历史日期；请切回最近交易日后补拉，避免按旧回执更新新行情')
            return self._repair_tickflow(codes,timeframe,include_history)
        def verify_selected(codes, timeframe, retry):
            if self._job_id or market_source(self.store) != source:
                return False
            self._auto_scan_after_sync = False
            self._submit_job('verify_status','核验缺日',dict(source=source,boards=self._selected_boards(),
                codes=codes,timeframe=timeframe,asof=day,retry=retry))
            return bool(self._job_id)
        show_integrity(self,report,repair_selected,store=self.store,on_changed=self._gap_decisions_changed,
                       on_verify=verify_selected, repair_blocked=self._repair_block_reason)

    def _gap_decisions_changed(self):
        self._load_market_status(force=True)
        if self._async_status_mode:
            self.set_status('缺口处理已保存，正在后台刷新数量；不改变扫描资格')
            return
        from workbench.gap_review import pending_count
        audit=self._tickflow_audit
        count=pending_count(self.store,audit,self._tf_var.get())
        ignored=len(audit.get('gaps',[]))-count
        self.set_status(f"缺口处理已保存：待处理 {count} 只 · 已忽略 {ignored} 只 · 可扫描 {audit.get('ready',0)} 只；仍只扫描数据合格股票")

    def _repair_tickflow(self,codes,timeframe=None,include_history=False):
        if market_source(self.store) not in UPDATE_SOURCES:
            self.set_status(RETIRED_MESSAGE)
            return False
        if self._job_id or not codes or market_source(self.store) not in SOURCES:
            self.set_status('补拉未开始：请等待当前任务结束，并保持所选数据源')
            return False
        self._auto_scan_after_sync=False
        source=market_source(self.store)
        tf=timeframe or self._tf_var.get()
        self._submit_job('sync','更新行情',dict(source=source,boards=self._selected_boards(),
            start=sync_start_date(self.store,include_history,tf),end=None,force=False,repair_codes=codes,
            scan_timeframe=tf,repair_scope='history' if include_history else 'scan'))
        return bool(self._job_id)

    def _repair_block_reason(self, action='repair'):
        if action == 'repair' and market_source(self.store) not in UPDATE_SOURCES:
            return RETIRED_MESSAGE
        if self._source_pending:
            return '正在切换来源，切换完成后可查看补拉条件。'
        if self._job_id:
            if self._job_kind == '一键盘前':
                ended = ('完成','部分完成','未完成','失败','已停止')
                waiting = '、'.join(SOURCES[s]+' '+item.get('phase','进行中')
                    for s,item in self._premarket_progress.items() if item.get('phase') not in ended)
                return (f'本轮仍有 {waiting or "行情或扫描任务"}；'
                        '三源任务全部结束后才能补拉。可先查看详情，不需要重做已完成结果。')
            return f'{self._job_kind or "当前任务"}尚未结束；结束后才能补拉或核验缺日。'
        from workbench.provider_guard import ProviderGuard
        source = 'baostock' if action == 'verify' else market_source(self.store)
        try:
            remaining = ProviderGuard(self.store, source).state().get('until', 0)-time.time()
        except Exception:
            return '暂时无法确认访问状态，请稍后重试；未发出补拉请求。'
        if remaining > 0:
            return f'{SOURCES[source]}正在访问冷却，约 {int(remaining)+1} 秒后可重试；不要连续请求。'
        return ''

    def _source_changed(self,event=None, background=True):
        if self.store is None:
            return
        source=next(k for k,v in SOURCES.items() if v==self.source_var.get())
        self._async_status_mode = True
        if background:
            from .background_status import LatestRefresh
            if self._source_reader is None:
                store = self.store
                def save(value, check):
                    check()
                    set_market_source(store, value)
                    return value
                self._source_reader = LatestRefresh(save)
            self._source_pending = True
            self._source_reader.submit(source)
            self.set_status(f'正在切换到 {SOURCES[source]}；当前图仍为原来源，后台任务继续')
            self._header_data_var.set(f'正在切换到 {SOURCES[source]} · 当前图仍为原来源')
            if self._source_poll is None:
                self._source_poll = self.after(50, self._poll_source)
            return
        try:
            set_market_source(self.store,source)
            self._apply_source(source)
        except Exception as exc:
            self.source_var.set(SOURCES[market_source(self.store)])
            self.set_status(str(exc))

    def _poll_source(self):
        self._source_poll = None
        reply = self._source_reader.poll()
        if reply is None:
            self._source_poll = self.after(50, self._poll_source)
            return
        _, source, _, error = reply
        self._source_pending = False
        if error:
            self.source_var.set(SOURCES[market_source(self.store)])
            self.set_status(str(error))
            return
        self._apply_source(source)

    def _apply_source(self, source):
        try:
            if not getattr(self, '_auto_sources_saved', None) and hasattr(self, 'auto_source_vars'):
                for key, variable in self.auto_source_vars.items(): variable.set(key in update_selection(None, source))
            self._sync_elapsed = self._scan_elapsed = None
            self._refresh_timing_status()
            # Date choices are source-owned too; changing only the selected
            # variables leaves the Combobox inventories on the previous source.
            self._load_date_options()
            self._select_latest_date()
            self._fire_date()
            risk='；历史只读，已停用更新／补拉；一键只使用 BaoStock／TickFlow' if source=='tencent' else ''
            if not self._job_id:
                self.btn_sync.state(['disabled'] if source not in UPDATE_SOURCES else ['!disabled'])
            self.set_status(f'日 K 来源已切换为 {SOURCES[source]}；独立缓存，旧快照与旧回测保留'+risk)
        except Exception as exc:
            self.source_var.set(SOURCES[market_source(self.store)])
            self.set_status(str(exc))

    def _on_auto(self):
        """先更新行情，成功后自动用同一范围执行策略扫描。"""
        if self._source_pending:
            self.set_status('正在切换来源，请切换完成后再开始一键盘前')
            return
        if self._job_id:
            self.set_status(f"已有{self._job_kind}运行中，请等待或终止后再试")
            return
        if not self._selected_strategies():
            self.set_status("一键盘前未开始：请至少选择一个扫描策略")
            return
        sources = [s for s, variable in self.auto_source_vars.items() if variable.get()] if hasattr(self, 'auto_source_vars') else [market_source(self.store)]
        if not sources:
            self.set_status('一键盘前未开始：请至少选择一个数据源')
            return
        boards = self._selected_boards()
        if not boards or self.service is None:
            self.set_status('请先连接后端并选择行情板块')
            return
        from workbench.scope import save_boards
        save_boards(self.store, boards)
        if market_source(self.store) not in sources:
            self.source_var.set(SOURCES[sources[0]])
            self._source_changed(background=False)
        self._auto_chain_cancelled = False
        self._auto_scan_after_sync = False
        self._sync_elapsed = None
        self._scan_elapsed = None
        self._submit_job('premarket', '一键盘前', dict(sources=sources, boards=boards,
            strategies=self._selected_strategies(), timeframe=self._tf_var.get(),
            start=sync_start_date(self.store, self.full_history_var.get(), self._tf_var.get()),
            full_history=self.full_history_var.get()))

    def _save_auto_sources(self):
        sources = [s for s, variable in self.auto_source_vars.items() if variable.get()]
        if not sources:
            for s in update_selection(self._auto_sources_saved, market_source(self.store)):
                self.auto_source_vars[s].set(True)
            self.set_status('至少保留一个数据源')
            return
        if self.store:
            self.store.save_premarket_sources(sources)
        self._auto_sources_saved = sources
        self.set_status('一键盘前来源：' + '、'.join(SOURCES[s] for s in sources))

    def _on_sync(self, chained=False):
        if self.store and market_source(self.store) not in UPDATE_SOURCES:
            self.set_status(RETIRED_MESSAGE)
            return
        if self._source_pending:
            self.set_status('正在切换来源，请切换完成后再更新行情')
            return
        if not chained:
            self._auto_scan_after_sync = False
        if self.service is None:
            self.set_status("后端未连接：更新行情需接 service")
            return
        boards = self._selected_boards()
        if not boards:
            self.set_status("更新未开始：请至少选择一个行情板块")
            return
        try:
            from workbench.scope import save_boards

            save_boards(self.store, boards)
        except Exception as exc:
            self.set_status(f"行情范围保存失败：{exc}")
            return
        # 三源先准备当前周期所需区间；更早研究历史是显式操作。
        start = sync_start_date(self.store, self.full_history_var.get(),self._tf_var.get())
        spec = {
            "source":market_source(self.store),
            "boards": boards,
            "start": start,
            "end": None,
            "force": False,
            "scan_timeframe":self._tf_var.get(),
            "resume": True,
            "full_history": self.full_history_var.get(),
        }
        self._submit_job("sync", "更新行情", spec)

    def _on_scan(self):
        if self._source_pending:
            self.set_status('正在切换来源，请切换完成后再扫描')
            return
        self._auto_scan_after_sync = False
        if self.service is None:
            self.set_status("后端未连接：扫描需接 service")
            return
        boards = self._selected_boards()
        if not boards:
            self.set_status("扫描未开始：请至少选择一个行情板块")
            return
        try:
            from workbench.market import completed_date
            from workbench.scope import save_boards, scan_datasets

            save_boards(self.store, boards)
            ids = [r["id"] for r in scan_datasets(self.store, market_source(self.store))]
            if not ids:
                self.set_status("所选板块没有可用行情：请先更新行情，再扫描")
                return
            strategies = self._selected_strategies()
            if not strategies:
                self.set_status("扫描未开始：请至少选择一个策略")
                return
            spec = {
                "source": market_source(self.store),
                "boards": boards,
                "datasets": ids,
                "strategies": strategies,
                "timeframes": [self._tf_var.get()],
                "asof": completed_date(),
            }
        except Exception as exc:
            self.set_status(f"扫描准备失败：{exc}")
            return
        self._submit_job("scan", "扫描策略", spec)

    def _submit_job(self, kind, label, spec):
        if getattr(self, '_source_pending', False) is True:
            self.set_status('正在切换数据源，请切换完成后再提交任务')
            return
        if kind == 'premarket':
            self._async_status_mode = True
            self._premarket_progress = {}
        """提交任务并启动轮询。service 的互斥拒绝（已有任务）在这里转成人话。"""
        try:
            job = self.service.submit(kind, spec)
        except ValueError as exc:
            self.set_status(f"{label}未开始：{exc}")
            return
        except Exception as exc:
            self.set_status(f"{label}提交失败：{exc}")
            return
        self._job_id, self._job_kind = job, label
        self._job_snapshot = None
        self._job_read_pending = self._job_read_error = False
        self._job_started_at = time.monotonic()
        if label == "更新行情":
            from workbench.market import completed_date
            self._job_sync_context=(spec.get('source','baostock'),tuple(spec.get('boards',[])),
                                    spec.get('end') or completed_date())
            totals=getattr(self,'_sync_totals',None)
            self._sync_totals=totals if isinstance(totals,dict) else {}
            self._job_sync_base=(self._sync_totals.get(self._job_sync_context,0)
                                 if spec.get('repair_codes') else 0)
            self._sync_elapsed = self._job_sync_base or None
        elif label == "扫描策略":
            self._scan_elapsed = None
        self._refresh_timing_status(running_elapsed=0)
        self.btn_stop.pack(side=tk.LEFT, padx=5, after=self.btn_scan)
        self.btn_stop.configure(state=tk.NORMAL)
        for button in (
            self.btn_auto,
            self.btn_sync,
            self.btn_scan,
            self.scope_button,
            self.strategy_scope_button,
        ):
            button.state(["disabled"])
        if hasattr(self,'source_combo'):
            self.source_combo.state(['!disabled','readonly'] if kind == 'premarket' else ['disabled'])
        if hasattr(self,'btn_auto_menu'):
            self.btn_auto_menu.state(['disabled'])
        self.set_status(f"⏳ {label}已提交（任务 {job[:8]}），排队中…")
        self._job_poll = self.after(800, self._poll_job)
        # Successful submission first: version networking never delays or
        # invalidates a market/strategy task. One-click premarket uses this too.
        hint = getattr(self, '_on_scan_started', None)
        if kind in ('scan','premarket') and callable(hint):
            try:
                hint()
            except Exception:
                pass

    def _poll_job(self):
        """Tk only paints snapshots; bounded reads run off-thread and recover."""
        self._job_poll = None
        if not self._job_id or self.store is None:
            return
        job = self._job_id
        try:
            from .background_status import LatestRefresh, load_job
            if self._job_reader is None:
                root = self.store.root
                self._job_reader = LatestRefresh(lambda value, check: load_job(root, value, check))
            reply = self._job_reader.poll()
            if reply and reply[1] == job:
                self._job_read_pending = False
                _, _, item, error = reply
                self._job_read_error = error is not None
                if error:
                    logging.getLogger(__name__).warning('任务进度读取失败，将重试：%s', error)
                elif item is None:
                    # Only a successful empty query proves that it is absent.
                    self._finish_job('failed', '任务记录不存在；请检查任务回执')
                    return
                else:
                    self._job_snapshot = (job, item, time.monotonic())
            if not self._job_read_pending:
                self._job_reader.submit(job)
                self._job_read_pending = True
            snapshot = self._job_snapshot
            if snapshot and snapshot[0] == job:
                self._render_job_snapshot(snapshot[1])
            else:
                started = self._job_started_at
                elapsed = max(0, time.monotonic()-started) if isinstance(started,(int,float)) else None
                self._refresh_timing_status(running_elapsed=elapsed)
                hint = '进度暂未读出，正在重试；不必重复点击' if self._job_read_error else '正在后台读取任务进度'
                self.set_status(f'⏳ {self._job_kind}：{hint} · 已用时 {format_elapsed(elapsed)}')
        except Exception:
            # A display/read problem is not a business failure. Keep the stop
            # control and task identity, log it, and never lose the next poll.
            logging.getLogger(__name__).exception('任务进度显示异常')
            self.set_status('进度显示暂未恢复，正在重试；请勿重复提交任务' if self._job_id
                            else '任务已结束；结果显示暂未恢复，请查看任务回执')
        finally:
            # _finish_job may start a new chained job with its own timer.
            if self._job_id == job:
                self._job_poll = self.after(800, self._poll_job)

    def _render_job_snapshot(self, j):
        if self._job_kind == '一键盘前':
            previous = self._premarket_progress
            result = json.loads(j.get('result') or '{}')
            progress = result.get('sources', {})
            if not isinstance(progress,dict):
                raise ValueError('任务来源进度格式异常')
            source = self._display_source()
            phase = progress.get(source, {}).get('phase')
            if phase in ('完成','部分完成','未完成','失败','已停止') and phase != previous.get(source, {}).get('phase'):
                self._load_market_status(force=True)
            self._premarket_progress = progress
        status = j["status"]
        if status in ("queued", "running"):
            prog, total = j["progress"] or 0, j["total"] or 0
            msg = (j["message"] or "").strip()[:180]
            if self._job_read_error:
                msg += ' · 进度暂未读出，正在重试（以上为上次进度）'
            elif time.monotonic()-self._job_snapshot[2] > 3:
                msg += ' · 正在后台读取新进度（以上为上次进度）'
            started = getattr(self, "_job_started_at", None)
            elapsed_seconds = (
                max(0, time.monotonic() - started)
                if isinstance(started, (int, float)) else None
            )
            self._refresh_timing_status(running_elapsed=elapsed_seconds)
            elapsed = (f" · 已用时 {format_elapsed(elapsed_seconds)}"
                       if elapsed_seconds is not None else "")
            if total:
                self.set_status(f"⏳ {self._job_kind} {prog}/{total}（{prog * 100 // total}%）：{msg}{elapsed}")
            else:
                fallback = '排队中…' if status == 'queued' else '运行中，准备核验…'
                self.set_status(f"⏳ {self._job_kind}：{msg or fallback}{elapsed}")
            return
        self._finish_job(status, j["message"] or "", j["result"] or "")

    def _finish_job(self, status, message="", result_json=""):
        """终态：解析回执出简报、恢复按钮、通知主窗口刷新。"""
        import json

        kind = self._job_kind or "任务"
        finished_at = time.monotonic()
        started = getattr(self, "_job_started_at", None)
        elapsed = max(0, finished_at - started) if isinstance(started, (int, float)) else None
        if kind == "更新行情":
            base=getattr(self,'_job_sync_base',0)
            base=base if isinstance(base,(int,float)) else 0
            self._sync_elapsed = base+elapsed if elapsed is not None else None
            totals=getattr(self,'_sync_totals',None)
            context=getattr(self,'_job_sync_context',None)
            if isinstance(totals,dict) and isinstance(context,tuple):
                totals[context]=self._sync_elapsed or 0
        elif kind == "扫描策略":
            self._scan_elapsed = elapsed
        chain_scan = False
        self._job_id = None
        self._job_kind = ""
        self._job_started_at = None
        self.btn_stop.configure(state=tk.DISABLED)
        for btn in (
            self.btn_auto,
            self.btn_sync,
            self.btn_scan,
            self.scope_button,
            self.strategy_scope_button,
        ):
            btn.state(["!disabled"])
        if hasattr(self,'source_combo'):
            self.source_combo.state(['!disabled','readonly'])
        if hasattr(self,'btn_auto_menu'):
            self.btn_auto_menu.state(['!disabled'])
        try:
            r = json.loads(result_json) if result_json else {}
        except Exception:
            r = {}
        chain_scan = (
            self._auto_scan_after_sync and not self._auto_chain_cancelled and kind == '更新行情'
            and (status == 'completed' or (status == 'partial' and r.get('source') in SOURCES
                 and r.get('scan_readiness', {}).get('scan_allowed')))
        )
        if kind == '更新行情' and not chain_scan and status != 'completed':
            self._auto_scan_after_sync = False
        if kind == '一键盘前' and r.get('sources'):
            text = ' · '.join(f'{SOURCES[s]} {item.get("phase","未完成")}：'
                f'行情{format_elapsed(item.get("sync_seconds"))}／扫描{format_elapsed(item.get("scan_seconds"))}'
                for s,item in r['sources'].items())
            for s,item in r['sources'].items():
                sync_result = item.get('sync_result', {})
                resume = sync_result.get('resume', {})
                scan_result = item.get('scan_result', {})
                if resume:
                    text += f"；{SOURCES[s]}行情复用 {resume.get('reused',0)}"
                    if resume.get('repair_attempted'):
                        text += f"／补拉 {resume.get('repair_count',len(resume.get('repair_codes',[])))} 只"
                    elif resume.get('waiting_for_cooldown'):
                        text += '／其余待冷却结束'
                if 'reused' in scan_result:
                    text += f"，判断复用 {scan_result['reused']}／新计算 {scan_result.get('calculated',0)}"
                if scan_result.get('not_scanned_count'):
                    text += f"；{SOURCES[s]}行情未齐未扫描 {scan_result['not_scanned_count']} 只（不代表无信号）"
                batch = sync_result.get('daily_batch', {})
                if batch.get('fallback'):
                    text += f"；{SOURCES[s]}逐股回退 {batch['fallback']} 只"
            current = r['sources'].get(market_source(self.store), {})
            self._sync_elapsed = current.get('sync_seconds')
            self._scan_elapsed = current.get('scan_seconds')
        elif kind == '核验缺日' and status in ('completed','partial'):
            c = r.get('coverage', {})
            marker = '✓' if status == 'completed' else '⚠'
            text = (f"{marker} 缺日核验：确认停牌 {len(r.get('confirmed_halt', []))} 天 · "
                    f"确认有交易 {len(r.get('traded', []))} 天 · 待定 {len(r.get('unknown', []))} 天；"
                    f"可扫描 {c.get('ready',0)}/{c.get('expected',0)} 只，不下载价格")
            if r.get('errors') or r.get('remaining'):
                text += '；部分未核验，请查看回执'
        elif status == "completed" and kind == "扫描策略":
            text = (f"✓ 扫描完成：命中 {r.get('signals', 0)} 个信号 · "
                    f"检查 {r.get('success', 0)} 项 · 判断复用 {r.get('reused', 0)}／新计算 {r.get('calculated',0)}")
        elif status == "completed":
            text = (f"✓ 行情更新完成：新增 {r.get('downloaded', 0)} · 补齐 {r.get('updated', 0)} · "
                    f"复用 {r.get('skipped', 0)} · 重刷 {r.get('refreshed', 0)} · "
                    f"停牌 {r.get('suspended', 0)} · 失败 {len(r.get('errors', []))}")
        elif status == "partial":
            head = (f"命中 {r.get('signals', 0)} 个信号" if kind == "扫描策略"
                    else f"已保存 {r.get('success', 0)} · 未下载 {r.get('remaining', 0)} · 数据未齐 {len(r.get('errors', []))}")
            if kind == '更新行情' and r.get('partial_saved'):
                head += f" · 最新尾部保留 {r['partial_saved']} 只"
            text = f"⚠ {kind}部分完成：{head} —— {str(r.get('stop_reason', ''))[:44]}"
            if kind == '扫描策略' and r.get('coverage', {}).get('source') in SOURCES:
                c = r['coverage']
                text = (f"⚠ 扫描部分完成：可扫描 {c['ready']}/{c['expected']} 只 · "
                        f"数据未齐 {len(c['gaps'])} 只暂不扫描 · 命中 {r.get('signals', 0)} 个信号（详情见回执）")
        elif status == "cancelled":
            text = f"⏹ {kind}已停止（已保存的记录保留）：{message[:48]}"
            if kind == '更新行情' and r.get('partial_saved'):
                text += f"；最新尾部保留 {r['partial_saved']} 只"
        elif status == "failed":
            text = f"✗ {kind}失败：{message[:64]}"
        else:
            text = f"{kind}结束（{status}）：{message[:56]}"
        auto_started = getattr(self, "_auto_started_at", None)
        if kind == "更新行情" and chain_scan:
            self._auto_sync_elapsed = elapsed
        elif kind == "扫描策略" and isinstance(auto_started, (int, float)):
            total_elapsed = max(0, finished_at - auto_started)
            sync_elapsed = getattr(self, "_auto_sync_elapsed", None)
            if isinstance(sync_elapsed, (int, float)) and elapsed is not None:
                text += (f" · 行情 {format_elapsed(sync_elapsed)} · 扫描 {format_elapsed(elapsed)}"
                         f" · 总计 {format_elapsed(total_elapsed)}")
            elif elapsed is not None:
                text += f" · 用时 {format_elapsed(elapsed)}"
            self._auto_started_at = None
            self._auto_sync_elapsed = None
        elif elapsed is not None:
            text += f" · 用时 {format_elapsed(elapsed)}"
        batch = r.get('daily_batch')
        if kind == '更新行情' and r.get('resume'):
            resume = r['resume']
            text += f"；已齐行情复用 {resume.get('reused',0)} 只"
            if resume.get('repair_attempted'):
                text += f"，自动补拉 {len(resume.get('repair_codes',[]))} 只（是否补齐见完整性）"
            if resume.get('unchanged'):
                text += f"；{len(resume['unchanged'])} 只补拉无改善，保留待查，不重复请求"
        if kind == '扫描策略' and status == 'partial':
            text += f"；判断复用 {r.get('reused',0)}／新计算 {r.get('calculated',0)}"
        if kind == '更新行情' and batch:
            text += f"；整日保存 {batch.get('saved',0)} 只，逐股回退 {batch.get('fallback',0)} 只"
            reason = r.get('batch_fallback_reason') or batch.get('reason')
            if reason: text += f'：{reason}'
        if kind == "更新行情" and status != "completed" and not chain_scan:
            self._auto_started_at = None
            self._auto_sync_elapsed = None
        self._refresh_timing_status()
        self._load_market_status(force=True)
        if status == "completed" and kind in {"更新行情", "扫描策略"}:
            text += "；" + self.data_chain_summary()
        self.set_status(text)
        if kind == '更新行情' and r.get('integrity'):
            i=r['integrity']
            from workbench.gap_review import pending_count
            count=pending_count(self.store,i['scan'],self._tf_var.get())
            self.set_status(text+f"；可扫描 {i['scan']['ready']}/{i['scan']['expected']} · 待处理 {count} 只，点击「缺口分类」")
        if self._on_job_finished:
            try:
                self._on_job_finished(kind, status, r)
            except Exception:
                pass
        if chain_scan:
            self.after_idle(self._start_auto_scan)

    def _start_auto_scan(self):
        """在行情任务完成并刷新界面后，衔接一键盘前的扫描阶段。"""
        if self._auto_chain_cancelled:
            self._auto_scan_after_sync = False
            self._auto_started_at = None
            self._auto_sync_elapsed = None
            self.set_status("⏹ 一键盘前已终止，未继续策略扫描")
            return
        self._auto_scan_after_sync = False
        self.set_status("✓ 行情更新完成，正在自动准备策略扫描…")
        self._on_scan()
        if self._job_id is None:
            self._auto_started_at = None
            self._auto_sync_elapsed = None

    def _on_stop(self):
        # 先切断一键盘前的后续动作，再请求停止当前任务。即使行情已经
        # 完成、扫描正等待 after_idle 衔接，也不能越过用户的终止指令。
        self._auto_chain_cancelled = True
        self._auto_scan_after_sync = False
        if self.service is None:
            self.set_status("后端未连接")
            return
        if not self._job_id:
            self.set_status("当前没有运行中的任务")
            return
        try:
            self.service.cancel(self._job_id)
            self.set_status(f"⏹ 已请求停止{self._job_kind}（当前股票/请求结束后生效）")
        except Exception as exc:
            self.set_status(f"停止失败：{exc}")
