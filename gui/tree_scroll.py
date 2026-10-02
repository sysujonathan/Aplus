"""桌面清单的自适应滚动条与局部鼠标滚轮。"""
from __future__ import annotations

import tkinter as tk
import ttkbootstrap as ttk


class AutoHideScrollbar(ttk.Scrollbar):
    """内容未溢出时隐藏，列表变长时自动出现。"""

    def set(self, first, last):
        first_value = float(first)
        last_value = float(last)
        if first_value <= 0.0 and last_value >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


def wheel_scroll_units(event):
    """把 Windows/macOS/Linux 的滚轮事件统一为 Treeview 滚动行数。"""
    if getattr(event, "num", None) == 4:
        return -3
    if getattr(event, "num", None) == 5:
        return 3
    delta = getattr(event, "delta", 0)
    if not delta:
        return 0
    return -3 if delta > 0 else 3


def bind_tree_mousewheel(tree):
    """只在鼠标位于列表矩形内时滚动该列表。"""

    def scroll(event):
        units = wheel_scroll_units(event)
        if units:
            tree.yview_scroll(units, "units")
        return "break"

    tree.bind("<MouseWheel>", scroll, add="+")
    tree.bind("<Button-4>", scroll, add="+")
    tree.bind("<Button-5>", scroll, add="+")


def attach_vertical_scrollbar(parent, tree, row=0, column=0):
    """把 Treeview 与自动显隐的垂向滚动条放入同一网格。"""
    parent.rowconfigure(row, weight=1)
    parent.columnconfigure(column, weight=1)
    scrollbar = AutoHideScrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=row, column=column + 1, sticky=tk.NS)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.grid(row=row, column=column, sticky=tk.NSEW)
    bind_tree_mousewheel(tree)
    return scrollbar
