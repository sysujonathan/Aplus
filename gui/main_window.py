"""工程 A 桌面布局，使用 Aplus Service/Store 后端。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk

from .candidate_tabs import CandidateTabs
from .chart_panel import ChartPanel
from .watch_panel import WatchPanel
from .toolbar import ToolBar


class AplusMainWindow(ttk.Window):
    def __init__(self, service=None, store=None):
        super().__init__(themename="darkly", title="Brooks-AI 操盘台")
        self.style.colors.primary = "#007AFF"
        self.style.configure("Treeview", rowheight=34)
        self.style.configure("Treeview.Heading", font=("Microsoft YaHei", 11, "bold"))
        self.service = service
        self.store = store
        self.geometry("1600x1000")
        self.minsize(1280, 760)
        self._tf_var = tk.StringVar(value="daily")
        self._build_ui()
        self._load_strategies()

    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.section_nav = ttk.Frame(self, padding=(10, 14), width=138)
        self.section_nav.grid(row=0, column=0, sticky=tk.NS)
        self.section_nav.grid_propagate(False)
        ttk.Label(
            self.section_nav,
            text="A · 工作台",
            font=("Microsoft YaHei", 13, "bold"),
            foreground="#f5f5f7",
        ).pack(anchor=tk.W, padx=4, pady=(0, 18))
        self._section_buttons = {}
        self._pages = {}
        section_names = (
            ("premarket", "盘前任务"),
            ("afterhours", "盘后回测"),
            ("strategy", "策略迭代"),
            ("other", "其他工具"),
        )
        for key, label in section_names:
            button = ttk.Button(
                self.section_nav,
                text=label,
                bootstyle="secondary",
                command=lambda k=key: self._switch_section(k),
            )
            button.pack(fill=tk.X, pady=(0, 7), ipady=5)
            self._section_buttons[key] = button
        ttk.Separator(self.section_nav).pack(fill=tk.X, pady=(14, 12))
        ttk.Label(
            self.section_nav,
            text="盘前：行情 → 扫描\n盘后：回测 → 复盘",
            font=("Microsoft YaHei", 9),
            foreground="#8e8e93",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=4)

        self.page_host = ttk.Frame(self)
        self.page_host.grid(row=0, column=1, sticky=tk.NSEW)
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)

        premarket = ttk.Frame(self.page_host)
        premarket.grid(row=0, column=0, sticky=tk.NSEW)
        premarket.columnconfigure(0, weight=1)
        premarket.rowconfigure(1, weight=1)
        self._pages["premarket"] = premarket
        self.toolbar = ToolBar(
            premarket, self.service, self.store, tf_var=self._tf_var,
            on_timeframe_change=self._on_timeframe,
            on_date_change=self._on_date_change,
            on_job_finished=self._on_job_finished,
        )
        self.toolbar.grid(row=0, column=0, sticky=tk.EW)

        self.body = body = ttk.Frame(premarket, padding=(10, 8))
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.candidates = CandidateTabs(
            body, self.on_stock_selected, on_context=self._candidate_context
        )
        self.candidates.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 16))
        self.candidates.configure(width=460)
        self.candidates.grid_propagate(False)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky=tk.NSEW)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=0)
        right.rowconfigure(0, weight=1)
        self.chart = ChartPanel(right, self.store)
        self.chart.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 16))
        self.watch = WatchPanel(right, self.store, on_select=self.on_stock_selected)
        self.watch.grid(row=0, column=1, sticky=tk.NSEW)
        self.watch.configure(width=240)
        self.watch.pack_propagate(False)
        self.chart.tv_button(right).grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0), ipady=6)

        status_bar = ttk.Frame(premarket, padding=(18, 8))
        status_bar.grid(row=2, column=0, sticky=tk.EW)
        self._status_text = self.toolbar._status
        ttk.Label(status_bar, textvariable=self._status_text, font=("Consolas", 10),
                  foreground="#a1a1a6").pack(side=tk.RIGHT)

        self._pages["afterhours"] = self._placeholder_page(
            "盘后回测",
            "用当日收盘后的完整行情复盘信号、检验策略表现。",
            "现有回测能力保持不变；桌面入口将在后续版本接入。",
        )
        self._pages["strategy"] = self._placeholder_page(
            "策略迭代",
            "注册研究策略、运行验证、比较版本，再由交易员决定是否启用。",
            "原有正式策略仍处于冻结保护中，本次界面调整不会改动它们。",
        )
        self._pages["other"] = self._placeholder_page(
            "其他工具",
            "为 AI 辅助、盘中观察和后续 T+0 工具预留独立工作区。",
            "这些能力尚未接入，不会以占位按钮冒充可用功能。",
        )
        self._switch_section("premarket")
        self.bind("<Configure>", self._resize_layout)

    def _placeholder_page(self, title, subtitle, boundary):
        page = ttk.Frame(self.page_host, padding=(46, 42))
        page.grid(row=0, column=0, sticky=tk.NSEW)
        ttk.Label(
            page,
            text=title,
            font=("Microsoft YaHei", 24, "bold"),
            foreground="#f5f5f7",
        ).pack(anchor=tk.W)
        ttk.Label(
            page,
            text=subtitle,
            font=("Microsoft YaHei", 12),
            foreground="#d1d1d6",
            wraplength=680,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(16, 8))
        ttk.Label(
            page,
            text=boundary,
            font=("Microsoft YaHei", 10),
            foreground="#8e8e93",
            wraplength=680,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        return page

    def _switch_section(self, key):
        for name, page in self._pages.items():
            if name == key:
                page.tkraise()
            self._section_buttons[name].configure(
                bootstyle="primary" if name == key else "secondary"
            )

    def _resize_layout(self, event):
        if event.widget is not self:
            return
        # Match the reference proportions without letting lists cover the chart.
        width = self.page_host.winfo_width()
        self.candidates.configure(width=max(460, min(640, round(width * .225))))
        self.watch.configure(width=max(280, min(360, round(width * .125))))

    # ---- 周期切换：重读候选（保留当前日期筛选）----
    def _on_timeframe(self, tf):
        self._cur_date = self.toolbar.selected_date()
        self.chart.set_timeframe(tf)
        self.chart.show_observation(None, None)
        if self.store is not None:
            self.candidates.load_from_store(
                self.store, timeframe=tf, asof_filter=getattr(self, "_cur_date", None)
            )
        self.toolbar.date_label.configure(text="截至周" if tf == "weekly" else "信号日")
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
        self._cur_date = self.toolbar.selected_date()
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
            self.toolbar._load_market_status()
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
