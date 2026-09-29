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
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.toolbar = ToolBar(
            self, self.service, self.store, tf_var=self._tf_var,
            on_timeframe_change=self._on_timeframe,
            on_date_change=self._on_date_change,
            on_job_finished=self._on_job_finished,
        )
        self.toolbar.grid(row=0, column=0, sticky=tk.EW)

        self.body = body = ttk.Frame(self, padding=(10, 8))
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.candidates = CandidateTabs(
            body, self.on_stock_selected, on_context=self._candidate_context
        )
        self.candidates.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 16))
        self.candidates.configure(width=420)
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

        status_bar = ttk.Frame(self, padding=(18, 8))
        status_bar.grid(row=2, column=0, sticky=tk.EW)
        self._status_text = self.toolbar._status
        ttk.Label(status_bar, textvariable=self._status_text, font=("Consolas", 10),
                  foreground="#a1a1a6").pack(side=tk.RIGHT)
        self.bind("<Configure>", self._resize_layout)

    def _resize_layout(self, event):
        if event.widget is not self:
            return
        # Match the reference proportions without letting lists cover the chart.
        width = self.winfo_width()
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
