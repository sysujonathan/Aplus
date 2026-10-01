"""工程 A 的横向策略导航及信号清单。"""
from __future__ import annotations

from collections import Counter
import tkinter as tk

import ttkbootstrap as ttk

from .theme import ACCENT, CONTROL_BG, HOVER, MUTED, REPEAT, TEXT


_COLUMN_TITLES = {"number": "序", "code": "代码", "name": "名称"}
_STRATEGY_PAGE_SIZE = 4


def short_strategy_label(label):
    """在不改变候选区宽度的前提下给策略按钮提供稳定简称。"""
    text = str(label or "").strip()
    upper = text.upper().replace("_", " ")
    if "PINBAR" in upper or "PIN BAR" in upper:
        return "GPb"
    if "GAP" in upper and "H1" in upper:
        return "GH1"
    if "GAP" in upper and "H2" in upper:
        return "GH2"
    if "MTR" in upper:
        return "MTR"
    if "3K" in upper or "THREE K" in upper:
        return "3K"
    if "AWIL" in upper or upper == "AIL":
        return "AIL"
    return text[:9]


def strategy_window(keys, start=0, page_size=_STRATEGY_PAGE_SIZE):
    """返回紧凑策略栏的一窗，并让末页尽量保持满四组。"""
    values = list(keys)
    size = max(1, int(page_size))
    last_start = max(0, len(values) - size)
    start = max(0, min(int(start), last_start))
    return values[start:start + size], start


def candidate_repeat_counts(rows):
    """统计当前筛选范围内同一股票重复出现的次数。"""
    return Counter(row.get("code") for row in rows if row.get("code"))


def collapse_candidate_rows(rows):
    """每只股票只展示最新观察，同时保留当前筛选范围内的出现次数。"""
    counts = candidate_repeat_counts(rows)
    seen = set()
    collapsed = []
    for row in rows:
        code = row.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        item = dict(row)
        item["repeat_count"] = counts[code]
        collapsed.append(item)
    return collapsed


class CandidateTabs(ttk.Frame):
    def __init__(self, parent, callback, on_context=None, on_rows_changed=None,
                 on_page_request=None):
        super().__init__(parent)
        self.callback, self.on_context = callback, on_context
        self.on_rows_changed = on_rows_changed
        self.on_page_request = on_page_request
        self._labels, self._buttons, self._rows = {}, {}, {}
        self._display_rows = []
        self._selected = None
        self._timeframe = None
        self._source = "baostock"
        self._eligible_keys = []
        self._strategy_start = 0
        self._sort_next_desc = {key: False for key in _COLUMN_TITLES}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(
            self,
            text="策略匹配",
            font=("Microsoft YaHei", 10, "bold"),
            foreground=TEXT,
        ).grid(row=0, column=0, sticky=tk.W, padx=2, pady=(0, 5))

        self.sidebar = ttk.Frame(self)
        self.sidebar.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.tree = ttk.Treeview(
            self,
            columns=("number", "code", "name"),
            show="headings",
            selectmode="browse",
        )
        for col, width in (("number", 30), ("code", 88), ("name", 92)):
            self.tree.heading(
                col,
                text=_COLUMN_TITLES[col],
                command=lambda c=col: self._sort_column(c),
                anchor=tk.CENTER,
            )
            self.tree.column(
                col,
                width=width,
                minwidth=28 if col == "number" else 76,
                stretch=col == "name",
                anchor=tk.CENTER if col == "number" else tk.W,
            )
        self.tree.grid(row=2, column=0, sticky=tk.NSEW)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Button-3>", self._context)
        self.tree.bind("<Up>", lambda _event: self._move_selection(-1))
        self.tree.bind("<Down>", lambda _event: self._move_selection(1))
        self.tree.bind("<Left>", lambda _event: self._request_page(-1))
        self.tree.bind("<Right>", lambda _event: self._request_page(1))
        self.tree._obs = {}
        self.tree._codes = {}
        self.tree._base_tags = {}
        self.tree.tag_configure("repeat", foreground=REPEAT)
        self.tree.tag_configure("hover", background=HOVER)
        self.tree.bind("<Motion>", self._hover)
        self.tree.bind("<Leave>", lambda _e: self._clear_hover())

    def build_tabs(self, strategies):
        for widget in self.sidebar.winfo_children():
            widget.destroy()
        self._labels = dict(strategies)
        self._buttons = {}
        self._eligible_keys = list(self._labels)
        self._strategy_start = 0
        self.sidebar.columnconfigure(0, weight=0)
        for col in range(1, _STRATEGY_PAGE_SIZE + 1):
            self.sidebar.columnconfigure(col, weight=1, uniform="strategy")
        self.sidebar.columnconfigure(_STRATEGY_PAGE_SIZE + 1, weight=0)
        self._prev_strategy = self._strategy_arrow("◀", -1)
        self._prev_strategy.grid(row=0, column=0, sticky=tk.NS, padx=(0, 2))
        self._next_strategy = self._strategy_arrow("▶", 1)
        self._next_strategy.grid(
            row=0, column=_STRATEGY_PAGE_SIZE + 1, sticky=tk.NS, padx=(2, 0)
        )
        for key, label in strategies:
            button = tk.Label(
                self.sidebar,
                text=f"{short_strategy_label(label)}\n0",
                font=("Microsoft YaHei", 8),
                foreground=TEXT,
                background=CONTROL_BG,
                justify=tk.CENTER,
                cursor="hand2",
                padx=1,
                pady=3,
                relief=tk.FLAT,
                highlightbackground=CONTROL_BG,
                highlightthickness=1,
            )
            button.bind("<Button-1>", lambda _event, k=key: self._choose(k))
            self._buttons[key] = button
        self._selected = next(iter(self._labels), None)
        self._refresh_strategy_bar()
        self._choose(self._selected)

    def _strategy_arrow(self, text, delta):
        return tk.Button(
            self.sidebar,
            text=text,
            width=1,
            command=lambda: self._shift_strategy_page(delta),
            bg=CONTROL_BG,
            fg=MUTED,
            activebackground=ACCENT,
            activeforeground=TEXT,
            disabledforeground="#4B5563",
            borderwidth=0,
            padx=1,
            cursor="hand2",
        )

    def _refresh_strategy_bar(self):
        for button in self._buttons.values():
            button.grid_remove()
        visible, self._strategy_start = strategy_window(
            self._eligible_keys, self._strategy_start
        )
        for index, key in enumerate(visible, 1):
            self._buttons[key].grid(row=0, column=index, sticky=tk.NSEW, padx=1)
        last_start = max(0, len(self._eligible_keys) - _STRATEGY_PAGE_SIZE)
        self._prev_strategy.configure(
            state=tk.NORMAL if self._strategy_start > 0 else tk.DISABLED,
            cursor="hand2" if self._strategy_start > 0 else "",
        )
        self._next_strategy.configure(
            state=tk.NORMAL if self._strategy_start < last_start else tk.DISABLED,
            cursor="hand2" if self._strategy_start < last_start else "",
        )
        return visible

    def _shift_strategy_page(self, delta):
        old_start = self._strategy_start
        self._strategy_start += int(delta) * _STRATEGY_PAGE_SIZE
        visible = self._refresh_strategy_bar()
        if self._strategy_start != old_start and visible and self._selected not in visible:
            self._choose(visible[0])

    def load_from_store(self, store, timeframe="daily", asof_filter=None,
                        source="baostock"):
        from .data import LEGACY_MARKET_SOURCE, load_candidates, load_legacy_candidates
        from workbench.strategies import catalog

        entries = catalog(store)
        if self._timeframe != timeframe or self._source != source:
            self._selected = None
            self._strategy_start = 0
        self._timeframe = timeframe
        self._source = source
        if source == LEGACY_MARKET_SOURCE:
            self._rows = load_legacy_candidates(
                store, timeframe=timeframe, asof_filter=asof_filter
            )
        else:
            self._rows = load_candidates(
                store,
                timeframe=timeframe,
                source=source,
                asof_filter=asof_filter,
            )
        eligible = []
        for key, button in self._buttons.items():
            if key in entries and timeframe in entries[key].timeframes:
                button.configure(
                    text=f"{short_strategy_label(self._labels[key])}\n{len(self._rows.get(key, []))}"
                )
                eligible.append(key)
        self._eligible_keys = eligible
        visible = self._refresh_strategy_bar()
        if self._selected not in eligible:
            self._selected = visible[0] if visible else None
        elif self._selected not in visible:
            selected_index = eligible.index(self._selected)
            self._strategy_start = selected_index
            self._refresh_strategy_bar()
        self._choose(self._selected)

    def _choose(self, key):
        self._selected = key
        for k, button in self._buttons.items():
            selected = k == key
            button.configure(
                background=ACCENT if selected else CONTROL_BG,
                highlightbackground=ACCENT if selected else CONTROL_BG,
            )
        self.tree.delete(*self.tree.get_children())
        self.tree._obs = {}
        self.tree._codes = {}
        self.tree._base_tags = {}
        self._display_rows = collapse_candidate_rows(self._rows.get(key, []))
        for number, row in enumerate(self._display_rows, 1):
            count = row["repeat_count"]
            name = row["name"] or ""
            if count > 1:
                name = f"{name} ×{count}" if name else f"×{count}"
            tags = ("repeat",) if count > 1 else ()
            iid = self.tree.insert("", tk.END, values=(number, row["code"], name), tags=tags)
            self.tree._obs[iid] = row["observation_id"]
            self.tree._codes[iid] = row["code"]
            self.tree._base_tags[iid] = tags
        if self.on_rows_changed:
            self.on_rows_changed(list(self._display_rows))
        self._reset_headings()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
        else:
            self.callback(None, None)

    def select_index(self, index):
        """选中当前策略结果中的指定行，供多图分页和图格点击联动。"""
        children = self.tree.get_children()
        if not children:
            return False
        index = max(0, min(int(index), len(children) - 1))
        iid = children[index]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.tree.focus_set()
        return True

    def select_observation(self, observation_id):
        """按稳定观察编号定位行；列表排序后仍能选中正确股票。"""
        for iid, value in self.tree._obs.items():
            if value == observation_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.tree.focus_set()
                return True
        return False

    def rows(self):
        """提供当前策略筛选后的可见顺序，供关注浏览切回候选。"""
        return [dict(row) for row in self._display_rows]

    def _move_selection(self, delta):
        children = self.tree.get_children()
        if not children:
            return "break"
        selection = self.tree.selection()
        try:
            index = children.index(selection[0]) if selection else 0
        except ValueError:
            index = 0
        self.select_index(index + delta)
        return "break"

    def _request_page(self, delta):
        if self.on_page_request:
            self.on_page_request(delta)
        return "break"

    def _reset_headings(self):
        self._sort_next_desc = {key: False for key in _COLUMN_TITLES}
        for col, title in _COLUMN_TITLES.items():
            self.tree.heading(col, text=title)

    def _sort_column(self, column):
        children = list(self.tree.get_children())
        if not children:
            return
        descending = self._sort_next_desc[column]

        def key(iid):
            value = self.tree.set(iid, column)
            if column == "number":
                try:
                    return int(value)
                except ValueError:
                    return 0
            return str(value).casefold()

        children.sort(key=key, reverse=descending)
        for position, iid in enumerate(children):
            self.tree.move(iid, "", position)
        for col, title in _COLUMN_TITLES.items():
            self.tree.heading(col, text=title)
        arrow = "↓" if descending else "↑"
        self.tree.heading(column, text=f"{_COLUMN_TITLES[column]} {arrow}")
        self._sort_next_desc[column] = not descending

    def _clear_hover(self):
        for iid in self.tree.get_children():
            self.tree.item(iid, tags=self.tree._base_tags.get(iid, ()))

    def _hover(self, event):
        self._clear_hover()
        iid = self.tree.identify_row(event.y)
        if iid:
            tags = tuple(self.tree._base_tags.get(iid, ())) + ("hover",)
            self.tree.item(iid, tags=tags)

    def _select(self, _event):
        selection = self.tree.selection()
        if selection:
            iid = selection[0]
            self.callback(self.tree._codes.get(iid), self.tree._obs.get(iid))

    def _context(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self.on_context:
            self.tree.selection_set(iid)
            self.on_context(event, self.tree._codes.get(iid), self.tree._obs.get(iid))
