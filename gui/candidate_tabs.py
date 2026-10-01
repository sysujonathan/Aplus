"""工程 A 的竖向策略导航及独立信号清单。"""
from __future__ import annotations
import tkinter as tk
import ttkbootstrap as ttk


class CandidateTabs(ttk.Frame):
    def __init__(self, parent, callback, on_context=None):
        super().__init__(parent)
        self.callback, self.on_context = callback, on_context
        self._labels, self._buttons, self._rows = {}, {}, {}
        self._selected = None
        self._timeframe = None
        self._date_title = "今日信号"
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side = ttk.Frame(self, width=180)
        side.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12))
        side.pack_propagate(False)
        ttk.Label(side, text="策略", font=("Microsoft YaHei", 12, "bold"),
                  foreground="#a1a1a6").pack(anchor=tk.W, pady=(0, 10))
        self.sidebar = ttk.Frame(side)
        self.sidebar.pack(fill=tk.BOTH, expand=True)
        center = ttk.Frame(self)
        center.grid(row=0, column=1, sticky=tk.NSEW)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)
        self.list_title = ttk.Label(center, text="今日信号", font=("Microsoft YaHei", 14, "bold"))
        self.list_title.grid(row=0, column=0, sticky=tk.W, pady=(0, 12))
        self.tree = ttk.Treeview(center, columns=("number", "code", "name"), show="headings", selectmode="browse")
        for col, title, width in (("number", "序", 36), ("code", "代码", 130), ("name", "名称", 100)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60 if col == "name" else width, stretch=col == "name",
                             anchor=tk.CENTER if col == "number" else tk.W)
        self.tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Button-3>", self._context)
        self.tree._obs = {}
        self.tree.tag_configure("hover", background="#4a4a4e")
        self.tree.bind("<Motion>", self._hover)
        self.tree.bind("<Leave>", lambda e: self._clear_hover())

    def build_tabs(self, strategies):
        for widget in self.sidebar.winfo_children():
            widget.destroy()
        self._labels = dict(strategies)
        self._buttons = {}
        for key, label in strategies:
            button = ttk.Button(self.sidebar, text=f"{label}  0", bootstyle="secondary",
                                command=lambda k=key: self._choose(k))
            button.pack(fill=tk.X, pady=(0, 5), ipady=3)
            self._buttons[key] = button
        self._selected = next(iter(self._labels), None)
        self._choose(self._selected)

    def load_from_store(self, store, timeframe="daily", asof_filter=None):
        from .data import load_candidates
        from workbench.strategies import catalog
        entries = catalog(store)
        if self._timeframe != timeframe:
            self._selected = None
        self._timeframe = timeframe
        self._rows = load_candidates(store, timeframe=timeframe, asof_filter=asof_filter)
        parts = asof_filter or (None, None, None)
        self._date_title = ("-".join(parts) + " 信号") if all(parts) else "全部信号"
        visible = []
        for button in self._buttons.values():
            button.pack_forget()
        for key, button in self._buttons.items():
            if key in entries and timeframe in entries[key].timeframes:
                button.pack(fill=tk.X, pady=(0, 5), ipady=3)
                button.configure(text=f"{self._labels[key]}  {len(self._rows.get(key, []))}")
                visible.append(key)
            else:
                button.pack_forget()
        if self._selected not in visible:
            self._selected = visible[0] if visible else None
        self._choose(self._selected)

    def _choose(self, key):
        self._selected = key
        for k, button in self._buttons.items():
            button.configure(bootstyle="primary" if k == key else "secondary")
        self.list_title.configure(text=f"{self._date_title} · {self._labels.get(key, '')}")
        self.tree.delete(*self.tree.get_children())
        self.tree._obs = {}
        for number, row in enumerate(self._rows.get(key, []), 1):
            iid = self.tree.insert("", tk.END, values=(number, row["code"], row["name"]))
            self.tree._obs[iid] = row["observation_id"]
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
        else:
            self.callback(None, None)

    def _clear_hover(self):
        for iid in self.tree.get_children():
            self.tree.item(iid, tags=())

    def _hover(self, event):
        self._clear_hover()
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.item(iid, tags=("hover",))

    def _select(self, event):
        selection = self.tree.selection()
        if selection:
            iid = selection[0]
            self.callback(self.tree.item(iid)["values"][1], self.tree._obs.get(iid))

    def _context(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self.on_context:
            self.tree.selection_set(iid)
            self.on_context(event, self.tree.item(iid)["values"][1], self.tree._obs.get(iid))
