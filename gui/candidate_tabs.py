"""左侧策略候选 Tab（第二批：接真实观察数据）。

每个策略一个 Notebook 页（策略键 -> 显示名），页内 Treeview 显示真实候选（代码/名称/信号日）。
双击触发回调，把 (代码, 观察id) 交给主窗口。load_from_store() 从 store.observations 读取，
按 strategy 分组填充。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_HDR_FG = "#a1a1a6"


class CandidateTabs(ttk.Frame):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self._trees = {}  # strategy key -> Treeview

    def build_tabs(self, strategies):
        # strategies: list of (key, label)
        for key, label in strategies:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=label)
            tree = ttk.Treeview(frame, columns=("code", "name", "date"), show="headings")
            tree.heading("code", text="代码")
            tree.heading("name", text="名称")
            tree.heading("date", text="信号日")
            tree.column("code", width=64, minwidth=58, anchor=tk.W)
            tree.column("name", width=68, minwidth=48, anchor=tk.W, stretch=True)
            tree.column("date", width=64, minwidth=56, anchor=tk.W)
            tree.pack(fill=tk.BOTH, expand=True)
            # 单击（选中即切换）K 线；<<TreeviewSelect>> 覆盖鼠标与键盘选择
            tree.bind("<<TreeviewSelect>>", self._select)
            tree._obs = {}  # iid -> observation_id
            self._trees[key] = tree

    # ---- 第二批：从 store 读真实候选 ----
    def load_from_store(self, store, timeframe="daily", asof_filter=None):
        from .data import load_candidates

        grouped = load_candidates(store, timeframe=timeframe, asof_filter=asof_filter)
        for key, tree in self._trees.items():
            self._fill(tree, grouped.get(key, []))

    def _fill(self, tree, rows):
        for child in tree.get_children():
            tree.delete(child)
        tree._obs = {}
        for row in rows:
            iid = tree.insert(
                "", tk.END, values=(row["code"], row["name"], row["date"])
            )
            tree._obs[iid] = row["observation_id"]

    def _select(self, event):
        tree = event.widget
        selection = tree.selection()
        if not selection:
            return
        iid = selection[0]
        obs_id = getattr(tree, "_obs", {}).get(iid)
        values = tree.item(iid)["values"]
        code = values[0] if values else None
        self.callback(code, obs_id)
