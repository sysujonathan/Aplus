"""顶部工具栏（旧 A 风格还原 + 接 service）。

旧 A 顶栏参数：标题 Microsoft YaHei 16 bold #f5f5f7；日线/周线切换；搜索框；
下载行情 / 扫描 / 停止；AI 复核；信号日；右侧状态。按钮在后端连接时提交 service 任务，
未连接时明确提示（调试壳）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_LABEL_FG = "#a1a1a6"
_TITLE_FG = "#f5f5f7"
_PLACEHOLDER_FG = "#8e8e93"
_ALL = "全部"


class ToolBar(ttk.Frame):
    def __init__(self, parent, service=None, store=None, tf_var=None,
                 on_timeframe_change=None, on_date_change=None):
        super().__init__(parent)
        self.service = service
        self.store = store
        self._tf_var = tf_var or tk.StringVar(value="daily")
        self._on_tf = on_timeframe_change
        self._on_date = on_date_change

        self.configure(padding=(18, 12))
        self._all_years = []
        self._all_months = []
        self._all_days = []
        self._months_by_year = {}
        self._days_by_ym = {}

        ttk.Label(
            self,
            text="Aplus 交易工作台",
            font=("Microsoft YaHei", 16, "bold"),
            foreground=_TITLE_FG,
        ).pack(side=tk.LEFT, padx=(4, 20))

        # 日线 / 周线（只由用户手动切换）
        tf_f = ttk.Frame(self)
        tf_f.pack(side=tk.LEFT, padx=(0, 18))
        ttk.Radiobutton(
            tf_f, text="日线", value="daily", variable=self._tf_var, command=self._fire_tf
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            tf_f, text="周线", value="weekly", variable=self._tf_var, command=self._fire_tf
        ).pack(side=tk.LEFT)

        # 搜索框（回车 -> TradingView）
        search_f = ttk.Frame(self)
        search_f.pack(side=tk.LEFT, padx=(0, 20))
        self.ent_code = ttk.Entry(search_f, width=14, font=("Consolas", 12), style="Search.TEntry")
        self.ent_code.pack(side=tk.LEFT, ipady=3)
        self.ent_code.insert(0, "输入代码送 TV")
        self.ent_code.config(foreground="#5b6470")
        self.ent_code.bind("<FocusIn>", self._search_focus)
        self.ent_code.bind("<FocusOut>", self._search_blur)
        self.ent_code.bind("<Return>", lambda e: self._open_tv_for_entry())

        # 动作按钮
        ttk.Button(self, text="下载行情", command=self._on_sync).pack(side=tk.LEFT, padx=5)
        ttk.Button(self, text="扫描", command=self._on_scan).pack(side=tk.LEFT, padx=5)
        ttk.Button(self, text="停止", command=self._on_stop).pack(side=tk.LEFT, padx=5)

        # AI 复核
        self.ai_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="AI 复核", variable=self.ai_var).pack(side=tk.LEFT, padx=(16, 0))

        # 信号日（年/月/日三联 Combobox，联动筛选；对齐旧 A gui_dashboard.py:210）
        ttk.Label(self, text="信号日", font=("Microsoft YaHei", 10), foreground=_LABEL_FG).pack(
            side=tk.LEFT, padx=(20, 4)
        )
        self.year_var = tk.StringVar(value=_ALL)
        self.month_var = tk.StringVar(value=_ALL)
        self.day_var = tk.StringVar(value=_ALL)
        combo_kw = {"state": "readonly", "width": 7, "font": ("Consolas", 11), "style": "Date.TCombobox"}
        self.year_combo = ttk.Combobox(self, textvariable=self.year_var, **combo_kw)
        self.year_combo.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(self, text="-", foreground=_LABEL_FG).pack(side=tk.LEFT)
        self.month_combo = ttk.Combobox(self, textvariable=self.month_var, **combo_kw)
        self.month_combo.pack(side=tk.LEFT, padx=(2, 2))
        ttk.Label(self, text="-", foreground=_LABEL_FG).pack(side=tk.LEFT)
        self.day_combo = ttk.Combobox(self, textvariable=self.day_var, **combo_kw)
        self.day_combo.pack(side=tk.LEFT, padx=(2, 0))
        self.year_combo.bind("<<ComboboxSelected>>", self._on_year_change)
        self.month_combo.bind("<<ComboboxSelected>>", self._on_month_change)
        self.day_combo.bind("<<ComboboxSelected>>", self._on_day_change)

        # 依赖 store 的真实信号日填充下拉
        self._load_date_options()

        # 右侧状态
        self._status = tk.StringVar(value="就绪（后端未连线）" if service is None else "就绪")
        ttk.Label(
            self, textvariable=self._status, font=("Consolas", 10), foreground=_LABEL_FG
        ).pack(side=tk.RIGHT, padx=6)

    # ---- 周期切换 ----
    def _fire_tf(self):
        if self._on_tf:
            self._on_tf(self._tf_var.get())

    def set_status(self, text):
        self._status.set(text)

    # ---- 搜索框占位 ----
    def _search_focus(self, _e):
        if self.ent_code.get() == "输入代码送 TV":
            self.ent_code.delete(0, tk.END)
            self.ent_code.config(foreground="#0e1218")

    def _search_blur(self, _e):
        if not self.ent_code.get().strip():
            self.ent_code.insert(0, "输入代码送 TV")
            self.ent_code.config(foreground="#5b6470")

    def _open_tv_for_entry(self):
        code = self.ent_code.get().strip()
        if not code or code == "输入代码送 TV":
            return
        try:
            from .tv import tv_link
            import webbrowser

            url = tv_link(code, self._tf_var.get())
            webbrowser.open(url)
            self.set_status(f"已打开 TradingView：{code}")
        except Exception:
            self.set_status(f"TV 链接：{code}")

    # ---- 信号日三联 Combobox 联动 ----
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
            rows = self.store.rows("SELECT DISTINCT asof FROM observations ORDER BY asof DESC")
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
        self.month_combo["values"] = [_ALL] + self._all_months
        self.day_combo["values"] = [_ALL] + self._all_days

    @staticmethod
    def _parse_asof(asof):
        if not asof:
            return None
        date_part = str(asof).split(" ")[0]
        parts = date_part.split("-")
        if len(parts) != 3:
            return None
        try:
            return tuple(f"{int(p):02d}" for p in parts)
        except ValueError:
            return None

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
        if y == _ALL or m == _ALL:
            days = self._all_days
        else:
            days = self._days_by_ym.get((y, m), [])
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

    # ---- 动作（第二批：提交 service 任务）----
    def _on_sync(self):
        if self.service is None:
            self.set_status("后端未连接：下载行情需接 service")
            return
        try:
            spec = {
                "boards": ["沪深主板", "创业板", "科创板"],
                "start": "2016-01-01",
                "end": None,
                "force": False,
            }
            self.service.submit("sync", spec)
            self.set_status("已提交行情同步")
        except Exception as exc:
            self.set_status(f"同步提交失败：{exc}")

    def _on_scan(self):
        if self.service is None:
            self.set_status("后端未连接：扫描需接 service")
            return
        try:
            from workbench.market import latest_datasets

            ids = [r["id"] for r in latest_datasets(self.store, "baostock")]
            spec = {
                "source": "baostock",
                "boards": ["沪深主板", "创业板", "科创板"],
                "datasets": ids,
                "strategies": None,
                "timeframes": [self._tf_var.get()],
                "asof": None,
            }
            self.service.submit("scan", spec)
            self.set_status("已提交策略扫描")
        except Exception as exc:
            self.set_status(f"扫描提交失败：{exc}")

    def _on_stop(self):
        self.set_status("停止指令（第二批接 service 停止接口）")
