"""Aplus 桌面工作台主窗口（第二批：视觉还原旧 A + 接真实数据）。

布局沿用旧 A 主区网格：左栏固定 180（策略候选 Tab）/ 中栏固定 420（K 线）/ 右栏自适应（关注）。
深色主题、字体与配色对齐旧 A。后端 service / store 通过构造参数注入；
store 连接时启动即从 observations 加载真实候选、K 线读真实行情快照。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .candidate_tabs import CandidateTabs
from .chart_panel import ChartPanel
from .watch_panel import WatchPanel
from .toolbar import ToolBar

_DARK_BG = "#11151c"
_PANEL_BG = "#171e28"


class AplusMainWindow(tk.Tk):
    def __init__(self, service=None, store=None):
        super().__init__()
        self.service = service
        self.store = store

        self.title("Aplus 交易工作台")
        self.geometry("1500x900")
        self.configure(bg=_DARK_BG)

        self._tf_var = tk.StringVar(value="daily")
        self._apply_dark_style()
        self._build_ui()
        self._load_strategies()

    # ---- 外观（深色，对齐旧 A / 1.1.0）----
    def _apply_dark_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("default")
        except Exception:
            pass
        style.configure("TFrame", background=_PANEL_BG)
        style.configure("TLabel", background=_PANEL_BG, foreground="#e3e8ef")
        style.configure(
            "Treeview",
            background="#1b2330",
            foreground="#e3e8ef",
            fieldbackground="#1b2330",
            rowheight=26,
        )
        style.configure("Treeview.Heading", background="#222c3a", foreground="#e3e8ef")
        style.configure("Notebook", background=_DARK_BG)
        style.configure(
            "Notebook.Tab",
            background="#222c3a",
            foreground="#e3e8ef",
            padding=(5, 4),
            font=("Microsoft YaHei", 9),
        )
        style.map("Notebook.Tab", background=[("selected", "#176b64")])
        style.configure("TButton", background="#222c3a", foreground="#e3e8ef")
        style.configure("TCheckbutton", background=_PANEL_BG, foreground="#e3e8ef")
        style.configure("TRadiobutton", background=_PANEL_BG, foreground="#e3e8ef")
        style.configure(
            "TCombobox", fieldbackground="#1b2330", background="#222c3a", foreground="#e3e8ef"
        )
        # 日期/搜索控件在 Windows 下 ttk.Combobox/Entry 常忽略 fieldbackground（只读态字段变浅），
        # 近白文字会白底白字看不清。专用样式强制浅字段 + 深色文字，跨主题可读。
        style.configure(
            "Date.TCombobox", fieldbackground="#eef2f7", background="#222c3a",
            foreground="#0e1218",
        )
        style.map(
            "Date.TCombobox",
            fieldbackground=[("readonly", "#eef2f7"), ("disabled", "#eef2f7")],
            foreground=[("readonly", "#0e1218"), ("disabled", "#8e8e93")],
        )
        style.configure(
            "Search.TEntry", fieldbackground="#eef2f7", foreground="#0e1218",
            insertcolor="#0e1218",
        )

    # ---- 结构（三栏比例对齐旧 A：180 / 420 / 自适应）----
    def _build_ui(self):
        self.toolbar = ToolBar(
            self,
            self.service,
            self.store,
            tf_var=self._tf_var,
            on_timeframe_change=self._on_timeframe,
            on_date_change=self._on_date_change,
            on_job_finished=self._on_job_finished,
        )
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        body.columnconfigure(0, weight=0, minsize=300)  # 左：策略候选（固定，容纳 6 页签不溢出）
        body.columnconfigure(1, weight=1, minsize=420)  # 中：K 线（主力扩张）
        body.columnconfigure(2, weight=0, minsize=260)  # 右：观察池（P2 扩列后加宽）
        body.rowconfigure(0, weight=1)

        self.candidates = CandidateTabs(
            body, self.on_stock_selected, on_context=self._candidate_context
        )
        self.candidates.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 16))
        # 固定侧栏宽度：weight=0 的列默认会撑到内容请求宽度（页签/列宽），
        # 关掉传播 + 显式 width 才能把宽度钉死，给中间 K 线让路。
        self.candidates.configure(width=300)
        self.candidates.grid_propagate(False)
        self.candidates.pack_propagate(False)

        self.chart = ChartPanel(body, self.store)
        self.chart.grid(row=0, column=1, sticky=tk.NSEW, padx=(0, 16))

        self.watch = WatchPanel(body, self.store, on_select=self.on_stock_selected)
        self.watch.grid(row=0, column=2, sticky=tk.NSEW)
        self.watch.configure(width=260)
        self.watch.grid_propagate(False)
        self.watch.pack_propagate(False)

        # 底部状态栏
        status_bar = ttk.Frame(self)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_text = tk.StringVar(value="就绪")
        ttk.Label(
            status_bar, textvariable=self._status_text, font=("Consolas", 10),
            foreground="#a1a1a6",
        ).pack(side=tk.RIGHT, padx=8, pady=4)

    # ---- 周期切换：重读候选（保留当前日期筛选）----
    def _on_timeframe(self, tf):
        if self.store is not None:
            self.candidates.load_from_store(
                self.store, timeframe=tf, asof_filter=getattr(self, "_cur_date", None)
            )
        self._status_text.set(f"周期：{tf}")

    # ---- 信号日筛选：重读候选 ----
    def _on_date_change(self, year, month, day):
        self._cur_date = (year, month, day)
        if self.store is not None:
            self.candidates.load_from_store(
                self.store,
                timeframe=self._tf_var.get(),
                asof_filter=self._cur_date,
            )
        label = f"{year or '*'}-{month or '*'}-{day or '*'}"
        self._status_text.set(f"信号日筛选：{label}")
        self.chart.set_timeframe(self._tf_var.get())

    # ---- 策略 Tab（动态取真实名称，只读，不碰冻结文件）----
    def _load_strategies(self):
        self._cur_date = None
        try:
            from core.strategy_registry import StrategyRegistry as R

            strategies = []
            for key in R.list_strategies():
                try:
                    label = R.get_metadata(key).get("display_name", key)
                except Exception:
                    label = key
                strategies.append((key, label))
        except Exception:
            strategies = [
                ("MTR_MASTER", "MTR"),
                ("STRATEGY_GAP_H2", "GAP H2"),
                ("STRATEGY_AWIL", "AIL"),
            ]
        self.candidates.build_tabs(strategies)
        if self.store is not None:
            self.candidates.load_from_store(
                self.store, timeframe=self._tf_var.get(), asof_filter=self._cur_date
            )

    # ---- 选中标的：展示 K 线 ----
    def on_stock_selected(self, code, observation_id=None):
        if observation_id and self.store is not None:
            self.chart.show_observation(self.store, observation_id)
            self._status_text.set(f"已加载：{code}")
        elif code and self.store is not None:
            from .data import latest_observation

            oid = latest_observation(self.store, code, timeframe=self._tf_var.get())
            if oid:
                self.chart.show_observation(self.store, oid)
                self._status_text.set(f"已加载：{code}")
            else:
                self.chart.show_observation(None, None)
                self._status_text.set(f"无 {code} 的行情快照")
        else:
            self.chart.show_observation(None, None)

    # ---- 任务结束（下载/扫描）：刷新候选、观察池与信号日下拉 ----
    def _on_job_finished(self, kind, status):
        if self.store is None:
            return
        try:
            self.candidates.load_from_store(
                self.store, timeframe=self._tf_var.get(),
                asof_filter=getattr(self, "_cur_date", None),
            )
            self.watch.reload()
            self.toolbar._load_date_options()
        except Exception:
            pass
        self._status_text.set(f"{kind}结束（{status}），候选与观察池已刷新")

    # ---- 左栏候选右键：加入关注（写只走 store.watch）----
    def _candidate_context(self, event, code, observation_id):
        if self.store is None or not observation_id:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label=f"加入关注（{code}）",
            command=lambda: self._add_watch(code, observation_id),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _add_watch(self, code, observation_id):
        try:
            self.store.watch(observation_id)
        except Exception as exc:
            messagebox.showerror("加入关注失败", str(exc), parent=self)
            return
        self.watch.reload()
        self._status_text.set(f"已加入关注：{code}")
