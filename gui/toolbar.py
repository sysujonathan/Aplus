"""盘前任务工具栏：周期、范围、行情更新、扫描与交易日。"""
from __future__ import annotations

from datetime import date
import tkinter as tk
import ttkbootstrap as ttk

from .theme import ACCENT, APP_BG, CONTROL_BG, MUTED, TEXT

_LABEL_FG = MUTED
_ALL = "全部"
_BOARD_SHORT = {
    "沪深主板": "主板",
    "创业板": "创业",
    "科创板": "科创",
    "北交所": "北交",
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


def format_data_chain_status(market_date, signal_date, coverage=None, total=None):
    """把行情层和扫描层分开说清楚，避免把信号日误认成行情日。"""
    market = str(market_date)[:10] if market_date else "无"
    signal = str(signal_date)[:10] if signal_date else "无"
    pending = bool(market_date and (not signal_date or market > signal))
    market_mark = "✓" if market_date else "—"
    scan_mark = "⚠ 待扫描" if pending else ("✓" if signal_date else "—")
    text = f"行情最新 {market} {market_mark} · 信号最新 {signal} {scan_mark}"
    if coverage is not None:
        text += f" · 覆盖 {coverage}/{total if total else '?'}"
    return text


def _two_years_ago():
    """已有本地历史时，日常更新只要求最近两年的覆盖。"""
    from datetime import date

    from workbench.market import completed_date

    d = date.fromisoformat(completed_date())
    try:
        return d.replace(year=d.year - 2).isoformat()
    except ValueError:  # 2/29 闰年边界
        return d.replace(year=d.year - 2, day=28).isoformat()


def sync_start_date(store, full_history=False):
    """First use builds full history; later runs reuse coverage incrementally."""
    if full_history:
        return "2016-01-01"
    try:
        state = store.rows(
            "SELECT COUNT(*) AS count, MAX(start) AS latest_start FROM sync_coverage"
        )[0]
        covered = state["count"]
        latest_start = state["latest_start"]
    except (IndexError, KeyError, TypeError):
        covered, latest_start = 0, None
    full_history_ready = covered and latest_start and latest_start <= "2016-01-01"
    return _two_years_ago() if full_history_ready else "2016-01-01"


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
        self._on_job_finished = on_job_finished  # 任务结束回调：(任务名, 终态)，主窗口借此刷新列表
        self._on_chart_page = on_chart_page
        self._on_chart_layout = on_chart_layout
        self._job_id = None      # 当前提交的任务（submit 返回值），None=无任务
        self._job_kind = ""      # 任务显示名："下载行情" / "扫描策略"
        self._mkt_var = tk.StringVar(value="行情：连接中…")  # 行情健康状态（常驻，只读）
        self._universe_total = None                           # 市场总量（首次渲染时缓存）
        self.full_history_var = tk.BooleanVar(value=False)    # 首次自动完整历史；也可手动要求重新核对
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

        # 日常顺序固定为：选择范围 → 更新行情 → 策略扫描。
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
        self.btn_sync = ttk.Button(
            actions, text="更新行情", command=self._on_sync, bootstyle="primary"
        )
        self.btn_sync.pack(side=tk.LEFT, padx=5)
        self.btn_scan = ttk.Button(
            actions, text="策略扫描", command=self._on_scan, bootstyle="secondary-outline"
        )
        self.btn_scan.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(actions, text="终止", command=self._on_stop, bootstyle="danger")
        sync_menu = tk.Menu(self, tearoff=0)
        sync_menu.add_checkbutton(label="完整历史（2016 年起）", variable=self.full_history_var)
        self.btn_sync.bind("<Button-3>", lambda e: sync_menu.tk_popup(e.x_root, e.y_root))
        self.ai_var = tk.BooleanVar(value=False)
        self.chk_ai = ttk.Checkbutton(actions, text="AI 复核", variable=self.ai_var,
                                     bootstyle="round-toggle", command=self._explain_ai)
        self.chk_ai.pack(side=tk.LEFT, padx=(16, 0))

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
        self.layout_var = tk.StringVar(value="2×2")
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
        self.btn_chart_prev = tk.Button(
            filters,
            text="‹",
            width=2,
            command=lambda: self._fire_chart_page(-1),
            bg=CONTROL_BG,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground=TEXT,
            borderwidth=0,
            cursor="hand2",
        )
        self.btn_chart_prev.pack(side=tk.LEFT, ipady=4)
        self.chart_page_var = tk.StringVar(value="0 / 0")
        self._chart_source_label = "策略"
        tk.Label(
            filters,
            textvariable=self.chart_page_var,
            width=14,
            anchor=tk.CENTER,
            font=("Consolas", 9),
            fg=_LABEL_FG,
            bg=APP_BG,
        ).pack(side=tk.LEFT, padx=3)
        self.btn_chart_next = tk.Button(
            filters,
            text="›",
            width=2,
            command=lambda: self._fire_chart_page(1),
            bg=CONTROL_BG,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground=TEXT,
            borderwidth=0,
            cursor="hand2",
        )
        self.btn_chart_next.pack(side=tk.LEFT, ipady=4)

        # 依赖 store 的真实信号日填充下拉
        self._load_date_options()
        # 常驻行情健康状态（只读，不依赖任务）
        self._load_market_status()

        self._status = tk.StringVar(value=self._mkt_var.get())
        # 顶部始终表示数据链事实；任务进度和结果另在底部状态栏显示。
        self.status_label = ttk.Label(self, textvariable=self._mkt_var, font=("Consolas", 10), foreground=_LABEL_FG)
        self.status_label.grid(row=0, column=3, sticky=tk.E, padx=6)
        self.bind("<Configure>", self._responsive)
        self._select_latest_date()

    def _responsive(self, event):
        if event.widget is not self:
            return
        header_width = self.header.winfo_reqwidth()
        actions_width = self.actions.winfo_reqwidth()
        filters_width = self.filters.winfo_reqwidth()
        needed = header_width + actions_width + filters_width + 40
        if event.width >= needed:
            self.actions.grid(row=0, column=1, columnspan=1, sticky=tk.W, pady=0)
            self.filters.grid(row=0, column=2, columnspan=1, sticky=tk.W, pady=0)
            if event.width > needed + self.status_label.winfo_reqwidth():
                self.status_label.grid(row=0, column=3, sticky=tk.E)
            else:
                self.status_label.grid_remove()
        elif event.width >= actions_width + filters_width + 40:
            self.actions.grid(row=1, column=0, columnspan=1, sticky=tk.W, pady=(6, 0))
            self.filters.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(6, 0))
            self.status_label.grid_remove()
        else:
            self.actions.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
            self.filters.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))
            self.status_label.grid_remove()

    def _explain_ai(self):
        from tkinter import messagebox
        self.ai_var.set(False)
        messagebox.showinfo("AI 复核", "当前 Aplus 后端尚未接入 AI 复核；本轮仅恢复界面，扫描仍使用原策略。", parent=self)

    def selected_date(self):
        return tuple(v.get() if v.get() != _ALL else None
                     for v in (self.year_var, self.month_var, self.day_var))

    def _selected_boards(self):
        """按固定市场顺序返回当前勾选范围。"""
        from workbench.market import BOARDS

        return [board for board in BOARDS if self.board_vars[board].get()]

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
            y, m, d = str(day)[:10].split("-")
            self.year_var.set(y)
            self._refresh_months()
            self.month_var.set(m)
            self._refresh_days()
            self.day_var.set(d)

    # ---- 周期切换 ----
    def _fire_tf(self):
        self._load_date_options()
        self._select_latest_date()
        if self._on_tf:
            self._on_tf(self._tf_var.get())

    def set_status(self, text):
        self._status.set(text)

    def set_chart_page_status(self, start, end, total):
        if not total:
            page = "0 / 0"
        elif start == end:
            page = f"{start} / {total}"
        else:
            page = f"{start}–{end} / {total}"
        self.chart_page_var.set(f"{self._chart_source_label} {page}")
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
        self._chart_source_label = "关注" if label == "关注" else "策略"

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
        y = self.year_var.get()
        m = self.month_var.get()
        d = self.day_var.get()
        y = None if y == _ALL else y
        m = None if m == _ALL else m
        d = None if d == _ALL else d
        if self._on_date:
            self._on_date(y, m, d)

    # ---- 行情健康状态（常驻，只读现有表）----
    def _load_market_status(self):
        if self.store is None:
            self._mkt_var.set("行情：未连接")
            return self._mkt_var.get()
        try:
            cov = self.store.rows("SELECT COUNT(*) c FROM sync_coverage")[0]["c"]
        except Exception:
            cov = 0
        if self._universe_total is None:
            try:
                p = self.store.root / "universe.csv"
                if p.exists():
                    with open(p, "r", encoding="utf-8") as fh:
                        self._universe_total = max(0, sum(1 for _ in fh) - 1)
            except Exception:
                self._universe_total = 0
        total = self._universe_total if self._universe_total else "?"
        from .data import latest_market_date, latest_scan_date

        market = latest_market_date(self.store, self._tf_var.get())
        signal = latest_scan_date(self.store, self._tf_var.get())
        text = format_data_chain_status(market, signal, cov, total)
        self._mkt_var.set(text)
        return text

    def data_chain_summary(self):
        if self.store is None:
            return "行情未连接"
        from .data import latest_market_date, latest_scan_date

        return format_data_chain_status(
            latest_market_date(self.store, self._tf_var.get()),
            latest_scan_date(self.store, self._tf_var.get()),
        )

    # ---- 动作（提交 service 任务 + 状态栏实时反馈）----
    def _on_sync(self):
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
        # 空仓首次更新自动建立完整历史；已有覆盖后仍由同步器只补缺口。
        start = sync_start_date(self.store, self.full_history_var.get())
        spec = {
            "boards": boards,
            "start": start,
            "end": None,
            "force": False,
        }
        self._submit_job("sync", "更新行情", spec)

    def _on_scan(self):
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
            ids = [r["id"] for r in scan_datasets(self.store, "baostock")]
            if not ids:
                self.set_status("所选板块没有可用行情：请先更新行情，再扫描")
                return
            # 全部已启用策略（GUI 无勾选界面，扫描即全量）
            try:
                from core.strategy_registry import StrategyRegistry

                strategies = list(StrategyRegistry.list_strategies())
            except Exception:
                strategies = ["MTR_MASTER", "STRATEGY_GAP_H2", "STRATEGY_AWIL"]
            spec = {
                "source": "baostock",
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
        self.btn_stop.pack(side=tk.LEFT, padx=5, after=self.btn_scan)
        for button in (self.btn_sync, self.btn_scan, self.scope_button):
            button.state(["disabled"])
        self.set_status(f"⏳ {label}已提交（任务 {job[:8]}），排队中…")
        self.after(800, self._poll_job)

    def _poll_job(self):
        """每 800ms 查一次 jobs 表，把进度滚到状态栏；终态时出简报并恢复按钮。"""
        if not self._job_id or self.store is None:
            return
        try:
            rows = self.store.rows(
                "SELECT status, progress, total, message, result FROM jobs WHERE id=?",
                (self._job_id,),
            )
        except Exception:
            rows = []
        if not rows:
            self._finish_job("failed", "任务记录丢失")
            return
        j = rows[0]
        status = j["status"]
        if status in ("queued", "running"):
            prog, total = j["progress"] or 0, j["total"] or 0
            msg = (j["message"] or "").strip()
            if total:
                self.set_status(f"⏳ {self._job_kind} {prog}/{total}（{prog * 100 // total}%）：{msg[:56]}")
            else:
                self.set_status(f"⏳ {self._job_kind}：{msg[:64] or '排队中…'}")
            self.after(800, self._poll_job)
            return
        self._finish_job(status, j["message"] or "", j["result"] or "")

    def _finish_job(self, status, message="", result_json=""):
        """终态：解析回执出简报、恢复按钮、通知主窗口刷新。"""
        import json

        kind = self._job_kind or "任务"
        self._job_id = None
        self._job_kind = ""
        self.btn_stop.pack_forget()
        for btn in (self.btn_sync, self.btn_scan, self.scope_button):
            btn.state(["!disabled"])
        try:
            r = json.loads(result_json) if result_json else {}
        except Exception:
            r = {}
        if status == "completed" and kind == "扫描策略":
            text = (f"✓ 扫描完成：命中 {r.get('signals', 0)} 个信号 · "
                    f"检查 {r.get('success', 0)} 项 · 复用 {r.get('reused', 0)}")
        elif status == "completed":
            text = (f"✓ 行情更新完成：补齐 {r.get('updated', 0)} · 复用已存 {r.get('refreshed', 0)} · "
                    f"停牌 {r.get('suspended', 0)} · 失败 {len(r.get('errors', []))}")
        elif status == "partial":
            head = (f"命中 {r.get('signals', 0)} 个信号" if kind == "扫描策略"
                    else f"补齐 {r.get('updated', 0)} · 失败 {len(r.get('errors', []))} 项")
            text = f"⚠ {kind}部分完成：{head} —— {str(r.get('stop_reason', ''))[:44]}"
        elif status == "cancelled":
            text = f"⏹ {kind}已停止（已保存的记录保留）：{message[:48]}"
        elif status == "failed":
            text = f"✗ {kind}失败：{message[:64]}"
        else:
            text = f"{kind}结束（{status}）：{message[:56]}"
        self._load_market_status()
        if status == "completed" and kind in {"更新行情", "扫描策略"}:
            text += "；" + self.data_chain_summary()
        self.set_status(text)
        if self._on_job_finished:
            try:
                self._on_job_finished(kind, status)
            except Exception:
                pass

    def _on_stop(self):
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
