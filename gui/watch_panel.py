"""右侧关注列表（第一批：外壳，第三批接 store.watchlist）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class WatchPanel(ttk.Frame):
    def __init__(self, parent, store=None):
        super().__init__(parent)
        self.store = store

        ttk.Label(
            self,
            text="关注列表",
            font=("Microsoft YaHei", 12, "bold"),
        ).pack(anchor=tk.W, padx=6, pady=4)

        self.tree = ttk.Treeview(
            self,
            columns=("code", "status"),
            show="headings",
        )
        self.tree.heading("code", text="股票")
        self.tree.heading("status", text="状态")
        self.tree.column("code", width=112, minwidth=80, anchor=tk.W)
        self.tree.column("status", width=74, minwidth=56, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # 第三批正式接线：第一批若注入了 store 则只读展示，否则留空
        if self.store is not None:
            self._load_from_store()

    # ---- 第三批接线缝：从 store 读取已关注 ----
    def _load_from_store(self):
        try:
            rows = self.store.rows(
                "SELECT code, active FROM watchlist WHERE active=1 ORDER BY created DESC"
            )
            for row in rows:
                self.tree.insert("", tk.END, values=(row["code"], "关注中"))
        except Exception:
            pass

    def refresh(self, data):
        for child in self.tree.get_children():
            self.tree.delete(child)
        for item in data:
            self.tree.insert("", tk.END, values=item)
