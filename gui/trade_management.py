"""交易管理：当前持仓、作战卡、已清仓与绩效单页。"""
from __future__ import annotations

import json
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import font as tkfont, messagebox

import ttkbootstrap as ttk

from workbench.market import code_of
from workbench.trading import (
    history_reconciliation,
    management_report,
    proposed_quantity,
    trading_dates,
)

from .data import StockNameLookup, code_names
from .theme import APP_BG, DOWN, MUTED, PANEL_BG, REPEAT, TEXT, UP
from .tree_scroll import numeric_stock_code


def _money(value):
    return f"{float(value or 0):,.0f}"


def _price(value):
    return "—" if value in (None, "") else f"{float(value):.2f}"


def _pct(value):
    return f"{float(value or 0):.2f}%"


def _sort_value(value):
    text = str(value or "").replace(",", "").replace("%", "").replace("¥", "").replace("*", "")
    try:
        return 0, float(text)
    except ValueError:
        return 1, text.casefold()


class TradeManagementFrame(ttk.Frame):
    def __init__(self, parent, store=None):
        super().__init__(parent, padding=(12, 8))
        self.store = store
        self._account_by_label = {}
        self._report = None
        self._names = {}
        self._position_ids = {}
        self._closed_ids = {}
        self._sell_buttons = {}
        self._sort_reverse = {}
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=3)
        self.rowconfigure(2, weight=2)
        self._build_header()
        self._build_panels()
        self.bind_all("<Button-1>", self._clear_position_selection, add="+")
        self.reload()

    def _build_header(self):
        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))
        ttk.Label(header, text="账户", foreground=MUTED).pack(side=tk.LEFT, padx=(2, 6))
        self.account_var = tk.StringVar()
        self.account_box = ttk.Combobox(header, textvariable=self.account_var,
                                        state="readonly", width=22)
        self.account_box.pack(side=tk.LEFT, padx=(0, 6))
        self.account_box.bind("<<ComboboxSelected>>", lambda _e: self.reload_data())
        ttk.Button(header, text="账户设置", bootstyle="secondary",
                   command=self._edit_account).pack(side=tk.LEFT, padx=3)
        ttk.Button(header, text="刷新", bootstyle="secondary",
                   command=self.reload_data).pack(side=tk.LEFT, padx=3)
        self.asof_var = tk.StringVar(value="尚无持仓行情")
        ttk.Label(header, textvariable=self.asof_var, foreground=MUTED).pack(side=tk.RIGHT)

    def _panel(self, row, column, title, add_command=None):
        outer = ttk.Frame(self, padding=1, style="secondary.TFrame")
        outer.grid(row=row, column=column, sticky=tk.NSEW,
                   padx=(0, 6) if column == 0 else (6, 0), pady=6)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        head = ttk.Frame(outer, padding=(10, 7))
        head.grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(head, text=title, font=("Microsoft YaHei", 11, "bold"),
                  foreground=TEXT).pack(side=tk.LEFT)
        if add_command:
            ttk.Button(head, text="＋", width=3, bootstyle="primary-outline",
                       command=add_command).pack(side=tk.RIGHT)
        body = ttk.Frame(outer, padding=(8, 2, 8, 8))
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        return body

    def _tree(self, body, columns, key, grid_row=1):
        host = ttk.Frame(body)
        host.grid(row=grid_row, column=0, sticky=tk.NSEW)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        style_name = "Position.Treeview" if key == "positions" else "Trade.Treeview"
        ttk.Style().configure(
            style_name, rowheight=68 if key == "positions" else 34,
            font=("Microsoft YaHei UI", 11 if key == "positions" else 10),
        )
        if key == "positions":
            # 继承 primary-outline 配色，仅校正雅黑中文字形的视觉基线。
            ttk.Style().configure(
                "PositionSell.primary.Outline.TButton",
                anchor=tk.CENTER,
                font=("Microsoft YaHei UI", 9),
                padding=(4, 0, 4, 2),
            )
        tree = ttk.Treeview(host, columns=tuple(item[0] for item in columns),
                            show="headings", style=style_name)
        tree._column_specs = columns
        tree._measure_font = tkfont.Font(
            root=tree, family="Microsoft YaHei UI",
            size=11 if key == "positions" else 10,
        )
        minimums = {}
        for column, label, width in columns:
            tree.heading(column, text=label, anchor=tk.CENTER,
                         command=lambda c=column, t=tree, k=key: self._sort_tree(t, c, k))
            minimum = max(46, tree._measure_font.measure(label) + 24)
            minimums[column] = minimum
            tree.column(column, width=width, minwidth=minimum, anchor=tk.CENTER, stretch=True)
        tree._column_minimums = minimums
        vertical = ttk.Scrollbar(host, orient=tk.VERTICAL, command=tree.yview)
        horizontal = ttk.Scrollbar(host, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(
            yscrollcommand=lambda first, last, t=tree, bar=vertical:
            self._tree_scrolled(t, bar, first, last),
            xscrollcommand=lambda first, last, t=tree, bar=horizontal:
            self._tree_scrolled(t, bar, first, last),
        )
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        tree.bind("<MouseWheel>", lambda event, t=tree: self._wheel(t, event), add="+")
        tree.bind("<Configure>", lambda _e, t=tree, c=columns, m=minimums:
                  self._fit_tree_columns(t, c, m), add="+")
        tree.tag_configure("profit", foreground=UP)
        tree.tag_configure("loss", foreground=DOWN)
        tree.tag_configure("warn", foreground=REPEAT)
        tree.tag_configure("summary", font=("Microsoft YaHei UI", 11, "bold"))
        return tree

    def _tree_scrolled(self, tree, scrollbar, first, last):
        scrollbar.set(first, last)
        try:
            needed = float(first) > 0.0001 or float(last) < 0.9999
            if needed:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
        except (TypeError, ValueError, tk.TclError):
            pass
        if tree is getattr(self, "position_tree", None):
            self.after_idle(self._layout_position_overlays)

    @staticmethod
    def _fit_tree_columns(tree, columns, minimums):
        available = tree.winfo_width() - 4
        if available <= 40:
            return
        sample = tree.get_children("")[:300]
        measured = {}
        for column, label, _width in columns:
            widest = tree._measure_font.measure(label)
            for iid in sample:
                widest = max(widest, tree._measure_font.measure(str(tree.set(iid, column))))
            measured[column] = max(minimums[column], widest + 28)
            tree.column(column, minwidth=measured[column])
        requested = [max(measured[column], width) for column, _label, width in columns]
        total = sum(requested)
        if available >= total:
            extra = available - total
            widths = [value + int(extra * value / total) for value in requested]
        else:
            minimum_total = sum(measured.values())
            if available < minimum_total:
                widths = [measured[column] for column, _label, _width in columns]
            else:
                room = available - minimum_total
                flexible = sum(max(0, requested[i] - measured[column])
                               for i, (column, _label, _width) in enumerate(columns)) or 1
                widths = [measured[column] + int(room * max(0, requested[i] - measured[column]) / flexible)
                          for i, (column, _label, _width) in enumerate(columns)]
        for (column, _label, _width), fitted in zip(columns, widths):
            tree.column(column, width=max(measured[column], fitted))

    def _refit_tree(self, tree):
        self._fit_tree_columns(tree, tree._column_specs, tree._column_minimums)

    @staticmethod
    def _wheel(tree, event):
        tree.yview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _sort_tree(self, tree, column, key):
        marker = (key, column)
        reverse = not self._sort_reverse.get(marker, False)
        self._sort_reverse[marker] = reverse
        summary_iid = "__summary__" if tree.exists("__summary__") else None
        rows = [(tree.set(iid, column), iid) for iid in tree.get_children("") if iid != summary_iid]
        rows.sort(key=lambda item: _sort_value(item[0]), reverse=reverse)
        for index, (_value, iid) in enumerate(rows):
            tree.move(iid, "", index)
        if summary_iid:
            tree.move(summary_iid, "", tk.END)

    def _build_panels(self):
        holding = self._panel(1, 0, "持仓管理", self._add_position)
        holding.rowconfigure(0, weight=0)
        holding.rowconfigure(1, weight=1)
        self._build_quick_calculator(holding)
        self.position_tree = self._tree(holding, (
            ("code", "代码", 64), ("name", "名称", 88), ("value", "市值", 72),
            ("pnl", "浮盈亏", 76), ("price", "现价", 62), ("cost", "成本价", 68),
            ("allocation", "仓位", 58), ("quantity", "持仓", 62), ("stop", "止损", 62),
            ("distance", "距止损", 70), ("trade_risk", "单笔风险", 76),
            ("risk", "风险", 58), ("action", "操作", 64),
        ), "positions", grid_row=1)
        self.position_tree.bind("<ButtonRelease-1>", self._position_click, add="+")
        self.position_tree.bind("<Configure>", lambda _e: self.after_idle(self._layout_position_overlays), add="+")
        self.position_tree.bind("<MouseWheel>", lambda _e: self.after_idle(self._layout_position_overlays), add="+")
        self.position_tree.bind("<Button-3>", self._position_menu)
        self.position_tree.bind("<Double-1>", lambda _e: self._edit_position())
        self._summary_separator = ttk.Separator(self.position_tree, orient=tk.HORIZONTAL)

        cards = self._panel(1, 1, "作战卡")
        self.card_summary = tk.StringVar(value="")
        ttk.Label(cards, textvariable=self.card_summary, foreground=MUTED).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 5)
        )
        self.card_tree = self._tree(cards, (
            ("code", "代码", 70), ("strategy", "策略", 86), ("entry", "Entry", 65),
            ("stop", "SL1", 65), ("tp1", "TP1", 65), ("state", "状态", 104),
        ), "cards")

        closed = self._panel(2, 0, "已清仓", self._add_closed)
        closed.rowconfigure(0, weight=1)
        closed.rowconfigure(1, weight=0)
        self.closed_tree = self._tree(closed, (
            ("number", "序号", 54), ("code", "代码", 74), ("name", "名称", 100),
            ("date", "清仓日期", 92), ("days", "持仓天数", 78),
            ("pnl", "盈亏", 82), ("return", "收益率", 76),
        ), "closed", grid_row=0)
        self.closed_tree.bind("<Button-3>", self._closed_menu)
        self.closed_tree.bind("<Double-1>", lambda _e: self._edit_closed())

        performance = self._panel(2, 1, "绩效分析")
        self.performance_text = tk.Text(
            performance, height=8, bg=PANEL_BG, fg=TEXT, relief=tk.FLAT,
            highlightthickness=0, font=("Microsoft YaHei UI", 10), wrap=tk.WORD,
        )
        self.performance_text.grid(row=0, column=0, rowspan=2, sticky=tk.NSEW)
        self.performance_text.configure(state=tk.DISABLED)

    def _build_quick_calculator(self, body):
        quick = ttk.Labelframe(body, text="拟建仓速算", padding=(8, 5))
        quick.grid(row=0, column=0, sticky=tk.EW, pady=(0, 7))
        ttk.Label(quick, text="拟买价").pack(side=tk.LEFT, padx=(0, 5))
        self.quick_buy = tk.StringVar()
        ttk.Entry(quick, textvariable=self.quick_buy, width=9).pack(side=tk.LEFT)
        ttk.Label(quick, text="拟止损").pack(side=tk.LEFT, padx=(12, 5))
        self.quick_stop = tk.StringVar()
        ttk.Entry(quick, textvariable=self.quick_stop, width=9).pack(side=tk.LEFT)
        ttk.Button(quick, text="推算手数", bootstyle="primary",
                   command=self._calculate_position_size).pack(side=tk.LEFT, padx=(12, 5))
        ttk.Button(quick, text="示例填充", bootstyle="secondary-outline",
                   command=self._fill_quick_example).pack(side=tk.LEFT, padx=3)
        self.quick_result = tk.StringVar(value="按账户剩余风险预算推算")
        self.quick_result_label = ttk.Label(quick, textvariable=self.quick_result, foreground=MUTED)
        self.quick_result_label.pack(side=tk.LEFT, padx=(14, 0))

    def _fill_quick_example(self):
        self.quick_buy.set("10.00")
        self.quick_stop.set("9.50")
        self._calculate_position_size()

    def _calculate_position_size(self):
        if not self._report:
            self.quick_result.set("请先设置账户")
            self.quick_result_label.configure(foreground=REPEAT)
            return
        try:
            buy = float(self.quick_buy.get())
            stop = float(self.quick_stop.get())
            summary = self._report["summary"]
            account = dict(self._report["account"])
            account["initial_equity"] = summary["total_assets"]
            shares = proposed_quantity(account, buy, stop, summary["risk_amount"])
        except (TypeError, ValueError) as exc:
            self.quick_result.set(f"请核对拟买价与拟止损：{exc}")
            self.quick_result_label.configure(foreground=REPEAT)
            return
        if shares <= 0:
            self.quick_result.set(
                f"现有持仓风险 {summary['risk_pct']:.2f}%，已无可用风险预算，不可新开仓"
            )
            self.quick_result_label.configure(foreground=DOWN)
        else:
            self.quick_result.set(f"建议最多 {shares // 100} 手（{shares} 股）")
            self.quick_result_label.configure(foreground=UP)

    def reload(self):
        if self.store is None:
            return
        try:
            accounts = self.store.list_accounts()
        except Exception as exc:
            self.quick_result.set(f"交易管理加载失败：{exc}")
            self.quick_result_label.configure(foreground=DOWN)
            return
        current_id = self.current_account_id()
        self._account_by_label = {row["name"]: row["id"] for row in accounts}
        labels = list(self._account_by_label)
        self.account_box.configure(values=labels)
        selected = next((label for label, aid in self._account_by_label.items() if aid == current_id), None)
        self.account_var.set(selected or (labels[0] if labels else "尚未设置账户"))
        self.account_box.configure(state="readonly" if labels else "disabled")
        self.reload_data()

    def current_account_id(self):
        return self._account_by_label.get(self.account_var.get())

    def reload_data(self):
        for button in self._sell_buttons.values():
            button.destroy()
        self._sell_buttons = {}
        for tree in (self.position_tree, self.card_tree, self.closed_tree):
            tree.delete(*tree.get_children())
        self._position_ids = {}
        self._closed_ids = {}
        account_id = self.current_account_id()
        if not account_id:
            self.quick_result.set("请先点“账户设置”，再使用持仓管理与拟建仓速算")
            self.quick_result_label.configure(foreground=MUTED)
            self.card_summary.set("")
            self.asof_var.set("尚无持仓行情")
            self._set_performance("两种建账方式均不要求逐笔补录全部历史成交。")
            return
        try:
            self._names = code_names(self.store)
            self._report = management_report(self.store, account_id)
            self._fill_positions()
            self._fill_cards()
            self._fill_closed()
            self._fill_performance()
        except Exception as exc:
            self.quick_result.set(f"读取交易账本失败：{exc}")
            self.quick_result_label.configure(foreground=DOWN)

    def _fill_positions(self):
        s = self._report["summary"]
        self.asof_var.set(f"持仓行情 {s['quote_date']}" if s["quote_date"] else "持仓暂无最新行情")
        for row in self._report["positions"]:
            tag = "profit" if row["floating_pnl"] > 0 else ("loss" if row["floating_pnl"] < 0 else "")
            iid = self.position_tree.insert("", tk.END, values=(
                numeric_stock_code(row["code"]), row["name"], _money(row["market_value"]),
                _money(row["floating_pnl"]), _price(row["current_price"]) + ("" if row["has_quote"] else "*"),
                _price(row["diluted_cost"]), _pct(row["allocation_pct"]), row["quantity"],
                _price(row["stop"]), _pct(row["distance_stop_pct"]),
                _pct(row["trade_risk_pct"]), _pct(row["account_risk_pct"]), "",
            ), tags=(tag,) if tag else ())
            self._position_ids[iid] = row["id"]
            self._sell_buttons[iid] = ttk.Button(
                self.position_tree,
                text="卖出",
                style="PositionSell.primary.Outline.TButton",
                padding=(4, 0, 4, 2),
                command=lambda pid=row["id"]: self._sell_position_by_id(pid),
            )
        if len(self._report["positions"]) > 1:
            self.position_tree.insert("", tk.END, iid="__summary__", values=(
                "汇总", "—", _money(s["market_value"]), _money(s["floating_pnl"]),
                "—", "—", _pct(s["position_pct"]),
                sum(int(row["quantity"]) for row in self._report["positions"]),
                "—", "—", "—", _pct(s["risk_pct"]), "",
            ), tags=("summary",))
        self.after_idle(self._refit_tree, self.position_tree)
        self.after_idle(self._layout_position_overlays)

    def _layout_position_overlays(self):
        if not getattr(self, "position_tree", None) or not self.position_tree.winfo_exists():
            return
        for iid, button in list(self._sell_buttons.items()):
            if not self.position_tree.exists(iid):
                button.destroy()
                self._sell_buttons.pop(iid, None)
                continue
            box = self.position_tree.bbox(iid, "action")
            if not box:
                button.place_forget()
                continue
            x, y, width, height = box
            button_height = min(36, max(height - 18, 28))
            button.place(x=x + 5, y=y + (height - button_height) // 2,
                         width=max(width - 10, 46), height=button_height)
        summary_box = self.position_tree.bbox("__summary__") if self.position_tree.exists("__summary__") else ()
        if summary_box:
            _x, y, _width, _height = summary_box
            self._summary_separator.place(x=0, y=max(y - 2, 0),
                                          width=self.position_tree.winfo_width(), height=2)
            self._summary_separator.lift()
        else:
            self._summary_separator.place_forget()

    def _clear_position_selection(self, event):
        if not getattr(self, "position_tree", None):
            return
        widget = event.widget
        current = widget
        while current is not None:
            if current is self.position_tree:
                return
            try:
                current = current.master
            except (AttributeError, tk.TclError):
                break
        self.position_tree.selection_remove(self.position_tree.selection())

    def _fill_cards(self):
        rows = self.store.rows(
            "SELECT p.code,p.strategy,p.entry,p.stop,p.target,p.state,o.payload "
            "FROM plans p JOIN observations o ON o.id=p.observation_id "
            "WHERE p.state IN ('观察','计划交易','已手工入场') ORDER BY p.updated DESC"
        )
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            state = payload.get("pending_state") or row["state"]
            label = {"PENDING": "待挂", "TRIGGERED": "核对成交", "INVALID": "失效",
                     "EXPIRED": "过期"}.get(state, state)
            self.card_tree.insert("", tk.END, values=(
                numeric_stock_code(row["code"]), row["strategy"], _price(row["entry"]),
                _price(row["stop"]), _price(row["target"]), label,
            ), tags=("warn",) if state == "TRIGGERED" else ())
        self.card_summary.set(f"{len(rows)} 张人工计划")
        self.after_idle(self._refit_tree, self.card_tree)

    def _fill_closed(self):
        for number, row in enumerate(self._report["closed"], 1):
            tag = "profit" if row["pnl"] > 0 else ("loss" if row["pnl"] < 0 else "")
            iid = self.closed_tree.insert("", tk.END, values=(
                number, numeric_stock_code(row["code"]), row["name"], row["close_date"],
                row["holding_days"], _money(row["pnl"]), _pct(row["return_pct"]),
            ), tags=(tag,) if tag else ())
            self._closed_ids[iid] = row["id"]
        self.after_idle(self._refit_tree, self.closed_tree)

    def _fill_performance(self):
        s = self._report["summary"]
        account = self._report["account"]
        total_label = (
            "系统账面总资产" if account.get("accounting_mode") == "history" else "总资产"
        )
        lines = [
            f"{total_label:<8}  {_money(s['total_assets'])} 元",
            f"持仓市值      {_money(s['market_value'])} 元",
            f"可用资金      {_money(s['available_cash'])} 元",
            f"证券仓位      {s['position_pct']:.2f}%",
            "",
            f"持仓浮盈亏    {_money(s['floating_pnl'])} 元",
            f"历史清仓盈亏  {_money(s['closed_pnl'])} 元",
            f"组合风险      {s['risk_pct']:.2f}%",
            f"清仓胜率      {s['win_rate']:.1f}%（{s['wins']} 盈 / {s['losses']} 亏）",
        ]
        if account.get("accounting_mode") == "history":
            lines.extend([
                "",
                f"开户初始资金  {_money(account['initial_equity'])} 元",
                f"系统账面资产  {_money(s['historical_equity'])} 元",
            ])
            if s["broker_total_assets"] is not None:
                lines.extend([
                    f"券商资产快照  {_money(s['broker_total_assets'])} 元",
                    f"反推开户资金  {_money(s['implied_initial_equity'])} 元",
                    f"对账差额      {_money(s['reconciliation'])} 元（券商－系统）",
                ])
                if abs(s["reconciliation"]) <= 1:
                    lines.append("✓ 对账一致")
                else:
                    lines.append("⚠ 差额通常来自漏记、转入转出或分红税费，请核对。")
            else:
                lines.append("未填写券商资产快照，暂不执行独立对账。")
        self._set_performance("\n".join(lines))

    def _set_performance(self, text):
        self.performance_text.configure(state=tk.NORMAL)
        self.performance_text.delete("1.0", tk.END)
        self.performance_text.insert("1.0", text)
        self.performance_text.configure(state=tk.DISABLED)

    def _edit_account(self):
        account = None
        aid = self.current_account_id()
        if aid:
            rows = self.store.rows("SELECT * FROM accounts WHERE id=?", (aid,))
            account = rows[0] if rows else None
        market_value = self._report["summary"]["market_value"] if self._report else 0.0
        summary = self._report["summary"] if self._report else {}
        dialog = _AccountDialog(
            self, account, market_value=market_value, summary=summary
        )
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            account_id = None if dialog.create_new else (account or {}).get("id")
            saved = self.store.save_account(**dialog.values, account_id=account_id)
        except Exception as exc:
            messagebox.showerror("账户保存失败", str(exc), parent=self)
            return
        self.reload()
        for label, value in self._account_by_label.items():
            if value == saved:
                self.account_var.set(label)
                break
        self.reload_data()

    def _selected_position(self):
        selected = self.position_tree.selection()
        if not selected or not self._report:
            return None
        pid = self._position_ids.get(selected[0])
        return next((row for row in self._report["positions"] if row["id"] == pid), None)

    def _position_click(self, event):
        iid = self.position_tree.identify_row(event.y)
        if iid and iid != "__summary__":
            self.position_tree.selection_set(iid)
        else:
            self.position_tree.selection_remove(self.position_tree.selection())

    def _position_menu(self, event):
        iid = self.position_tree.identify_row(event.y)
        if not iid or iid == "__summary__":
            return
        self.position_tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="卖出", command=self._sell_position)
        menu.add_command(label="编辑持仓", command=self._edit_position)
        menu.add_separator()
        menu.add_command(label="删除误录持仓", command=self._delete_position)
        menu.tk_popup(event.x_root, event.y_root)

    def _add_position(self):
        self._open_position_dialog(None)

    def _edit_position(self):
        row = self._selected_position()
        if row:
            self._open_position_dialog(row)

    def _open_position_dialog(self, position):
        if not self.current_account_id():
            messagebox.showinfo("请先设置账户", "先设置账户，再新增持仓。", parent=self)
            return
        dialog = _PositionDialog(self, self.store, self._names or code_names(self.store), position)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_position(self.current_account_id(), **dialog.values,
                                     position_id=(position or {}).get("id"))
        except Exception as exc:
            messagebox.showerror("持仓保存失败", str(exc), parent=self)
            return
        self.reload_data()

    def _delete_position(self):
        row = self._selected_position()
        if not row:
            return
        if not messagebox.askyesno("删除误录持仓", f"确认删除 {row['code']} {row['name']} 及其买卖批次？",
                                   parent=self):
            return
        try:
            self.store.delete_position(row["id"])
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.reload_data()

    def _sell_position(self):
        row = self._selected_position()
        if not row:
            return
        self._open_sell_dialog(row)

    def _sell_position_by_id(self, position_id):
        row = next((item for item in (self._report or {}).get("positions", [])
                    if item["id"] == position_id), None)
        if row:
            self._open_sell_dialog(row)

    def _open_sell_dialog(self, row):
        dialog = _SellDialog(self, self.store, row)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.sell_position(row["id"], dialog.batches, dialog.fees_total)
        except Exception as exc:
            messagebox.showerror("卖出记录失败", str(exc), parent=self)
            return
        self.reload_data()

    def _selected_closed(self):
        selected = self.closed_tree.selection()
        if not selected or not self._report:
            return None
        cid = self._closed_ids.get(selected[0])
        return next((row for row in self._report["closed"] if row["id"] == cid), None)

    def _closed_menu(self, event):
        iid = self.closed_tree.identify_row(event.y)
        if not iid:
            return
        self.closed_tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="编辑清仓记录", command=self._edit_closed)
        menu.add_command(label="删除误录记录", command=self._delete_closed)
        menu.tk_popup(event.x_root, event.y_root)

    def _add_closed(self):
        self._open_closed_dialog(None)

    def _edit_closed(self):
        row = self._selected_closed()
        if not row:
            return
        if row.get("position_id"):
            messagebox.showinfo("完整成交链路", "这笔清仓由持仓全部卖出自动归档，保留原始买卖链路。",
                                parent=self)
            return
        self._open_closed_dialog(row)

    def _open_closed_dialog(self, row):
        if not self.current_account_id():
            messagebox.showinfo("请先设置账户", "先设置账户，再补录历史清仓。", parent=self)
            return
        dialog = _ClosedDialog(self, self.store, self._names or code_names(self.store), row)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            if dialog.batch_values is not None:
                self.store.save_closed_trades_batch(self.current_account_id(), dialog.batch_values)
            else:
                self.store.save_closed_trade(self.current_account_id(), **dialog.values,
                                             closed_id=(row or {}).get("id"))
        except Exception as exc:
            messagebox.showerror("清仓记录保存失败", str(exc), parent=self)
            return
        self.reload_data()

    def _delete_closed(self):
        row = self._selected_closed()
        if not row:
            return
        if not messagebox.askyesno("删除误录记录", f"确认删除 {row['code']} 的清仓记录？", parent=self):
            return
        try:
            self.store.delete_closed_trade(row["id"])
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.reload_data()


class _BaseDialog:
    def __init__(self, parent, title):
        self.confirmed = False
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=APP_BG)
        self.top.transient(parent.winfo_toplevel())
        self.top.grab_set()
        self.top.resizable(False, False)
        self.form = ttk.Frame(self.top, padding=14)
        self.form.pack(fill=tk.BOTH, expand=True)

    def entry(self, row, label, value="", width=22, column=0):
        base = column * 2
        ttk.Label(self.form, text=label).grid(row=row, column=base, sticky=tk.W, padx=(0, 8), pady=4)
        var = tk.StringVar(value="" if value is None else str(value))
        ttk.Entry(self.form, textvariable=var, width=width).grid(row=row, column=base + 1,
                                                                 sticky=tk.EW, padx=(0, 12), pady=4)
        return var

    def buttons(self, row, command, span=4):
        box = ttk.Frame(self.form)
        box.grid(row=row, column=0, columnspan=span, sticky=tk.E, pady=(12, 0))
        ttk.Button(box, text="确认", bootstyle="primary", command=command).pack(side=tk.LEFT, padx=4)
        ttk.Button(box, text="取消", bootstyle="secondary", command=self.top.destroy).pack(side=tk.LEFT, padx=4)


class _AccountDialog(_BaseDialog):
    def __init__(self, parent, account=None, market_value=0.0, summary=None):
        super().__init__(parent, "账户设置")
        account = account or {}
        summary = summary or {}
        self.create_new = False
        self.market_value = float(market_value or 0)
        self.closed_pnl = float(summary.get("closed_pnl") or 0)
        self.floating_pnl = float(summary.get("floating_pnl") or 0)
        self._implied_initial = None
        self.mode = tk.StringVar(value=account.get("accounting_mode", "snapshot"))
        ttk.Label(self.form, text="建账方式").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Radiobutton(self.form, text="当前资产快照", variable=self.mode,
                        value="snapshot", command=self._mode_changed).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(self.form, text="初始资金＋历史清仓", variable=self.mode,
                        value="history", command=self._mode_changed).grid(row=0, column=2, columnspan=2, sticky=tk.W)
        self.name = self.entry(1, "账户名称", account.get("name", "主账户"))
        ttk.Button(self.form, text="＋ 新增账户", bootstyle="primary-outline",
                   command=self._new_account).grid(
                       row=1, column=2, columnspan=2, sticky=tk.EW,
                       padx=(0, 12), pady=4,
                   )
        self.initial_label = ttk.Label(self.form, text="账户资金")
        self.initial_label.grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.initial = tk.StringVar(value=account.get("initial_equity", 100000))
        self.initial_entry = ttk.Entry(self.form, textvariable=self.initial, width=22)
        self.initial_entry.grid(row=2, column=1, sticky=tk.EW, padx=(0, 12))
        self.available_label = ttk.Label(self.form, text="可用资金")
        self.available_label.grid(row=2, column=2, sticky=tk.W, padx=(0, 8), pady=4)
        self.available = tk.StringVar()
        self.available_entry = ttk.Entry(self.form, textvariable=self.available, width=22)
        self.available_entry.grid(row=2, column=3, sticky=tk.EW, padx=(0, 12))
        self.available_entry.bind("<FocusOut>", self._apply_available)
        self.available_entry.bind("<Return>", self._apply_available)
        self.system_label = ttk.Label(self.form, text="系统账面总资产")
        self.system_label.grid(row=2, column=2, sticky=tk.W, padx=(0, 8), pady=4)
        self.system_total = tk.StringVar()
        self.system_entry = ttk.Entry(
            self.form, textvariable=self.system_total, width=22, state="readonly"
        )
        self.system_entry.grid(row=2, column=3, sticky=tk.EW, padx=(0, 12))
        self.total_label = ttk.Label(self.form, text="券商资产快照")
        self.total_label.grid(row=3, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.total = tk.StringVar(value=account.get("current_total_assets") or "")
        self.total_entry = ttk.Entry(self.form, textvariable=self.total, width=22)
        self.total_entry.grid(row=3, column=1, sticky=tk.EW, padx=(0, 12))
        self.reverse_label = ttk.Label(self.form, text="反推开户资金")
        self.reverse_label.grid(row=3, column=2, sticky=tk.W, padx=(0, 8), pady=4)
        self.reverse_initial = tk.StringVar(value="—")
        self.reverse_entry = ttk.Entry(
            self.form, textvariable=self.reverse_initial, width=22, state="readonly"
        )
        self.reverse_entry.grid(row=3, column=3, sticky=tk.EW, padx=(0, 12))
        self.reconciliation = tk.StringVar()
        self.reconciliation_label = ttk.Label(
            self.form, textvariable=self.reconciliation, foreground=MUTED
        )
        self.reconciliation_label.grid(
            row=4, column=0, columnspan=3, sticky=tk.W, pady=(4, 0)
        )
        self.use_reverse_button = ttk.Button(
            self.form,
            text="采用反推值",
            bootstyle="secondary-outline",
            command=self._use_implied_initial,
        )
        self.use_reverse_button.grid(row=4, column=3, sticky=tk.E, padx=(0, 12), pady=(4, 0))
        self.risk_limit = self.entry(5, "总风险上限 %", account.get("risk_limit_pct", 3))
        self.trade_risk = self.entry(5, "单笔风险 %", account.get("per_trade_risk_pct", 1), column=1)
        self.max_position = self.entry(6, "单票仓位上限 %", account.get("max_position_pct", 30))
        self.cash_reserve = self.entry(6, "最低现金 %", account.get("cash_reserve_pct", 10), column=1)
        self.hint = tk.StringVar()
        ttk.Label(self.form, textvariable=self.hint, foreground=MUTED, wraplength=600).grid(
            row=7, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        self.buttons(8, self._ok)
        self.initial.trace_add("write", lambda *_args: self._recalculate_account())
        self.total.trace_add("write", lambda *_args: self._recalculate_account())
        self._mode_changed()

    def _new_account(self):
        self.create_new = True
        self.top.title("新增账户")
        self.market_value = 0.0
        self.closed_pnl = 0.0
        self.floating_pnl = 0.0
        self._implied_initial = None
        self.mode.set("snapshot")
        self.name.set("")
        self.initial.set("100000")
        self.total.set("")
        self.risk_limit.set("3")
        self.trade_risk.set("1")
        self.max_position.set("30")
        self.cash_reserve.set("10")
        self._mode_changed()
        self.hint.set("填写新账户资料并确认；保存后会自动切换到这个账户。")

    def _update_available(self):
        try:
            available = float(self.initial.get()) - self.market_value
            self.available.set(f"{available:.2f}")
        except ValueError:
            self.available.set("")

    def _recalculate_account(self):
        if self.mode.get() == "snapshot":
            self._update_available()
            return
        try:
            initial = float(self.initial.get())
        except ValueError:
            self.system_total.set("")
            self.reverse_initial.set("—")
            self._implied_initial = None
            self.reconciliation.set("请填写有效的开户初始资金")
            self.reconciliation_label.configure(foreground=DOWN)
            self.use_reverse_button.state(["disabled"])
            return
        system = history_reconciliation(
            initial, None, self.closed_pnl, self.floating_pnl
        )
        self.system_total.set(f"{system['system_total_assets']:.2f}")
        try:
            broker = float(self.total.get()) if self.total.get().strip() else None
        except ValueError:
            self.reverse_initial.set("—")
            self._implied_initial = None
            self.reconciliation.set("券商资产快照需填写有效数字")
            self.reconciliation_label.configure(foreground=DOWN)
            self.use_reverse_button.state(["disabled"])
            return
        result = history_reconciliation(
            initial, broker, self.closed_pnl, self.floating_pnl
        )
        self._implied_initial = result["implied_initial_equity"]
        if self._implied_initial is None:
            self.reverse_initial.set("—")
            self.reconciliation.set(
                f"已清仓 {self.closed_pnl:+,.2f} 元 · 持仓浮盈亏 {self.floating_pnl:+,.2f} 元 · 未填写券商资产快照"
            )
            self.reconciliation_label.configure(foreground=MUTED)
            self.use_reverse_button.state(["disabled"])
            return
        self.reverse_initial.set(f"{self._implied_initial:.2f}")
        difference = result["reconciliation"]
        if abs(difference) <= 1:
            self.reconciliation.set(
                f"✓ 对账一致 · 差额 {difference:+,.2f} 元（券商－系统）"
            )
            self.reconciliation_label.configure(foreground=UP)
        else:
            self.reconciliation.set(
                f"⚠ 对账差额 {difference:+,.2f} 元（券商－系统），请核对漏记或资金变动"
            )
            self.reconciliation_label.configure(foreground=DOWN)
        self.use_reverse_button.state(["!disabled"])

    def _use_implied_initial(self):
        if isinstance(self._implied_initial, (int, float)) and self._implied_initial > 0:
            self.initial.set(f"{self._implied_initial:.2f}")

    def _apply_available(self, _event=None):
        if self.mode.get() != "snapshot":
            return
        try:
            self.initial.set(f"{self.market_value + float(self.available.get()):.2f}")
        except ValueError:
            pass

    def _mode_changed(self):
        if self.mode.get() == "snapshot":
            self.initial_label.configure(text="当前总资产")
            self.available_label.grid()
            self.available_entry.grid()
            self.system_label.grid_remove()
            self.system_entry.grid_remove()
            self.total_label.grid_remove()
            self.total_entry.grid_remove()
            self.reverse_label.grid_remove()
            self.reverse_entry.grid_remove()
            self.reconciliation_label.grid_remove()
            self.use_reverse_button.grid_remove()
            self._update_available()
            self.hint.set("直接填写证券 App 当前总资产；增加持仓后自动计算持仓市值、可用资金和仓位。")
        else:
            self.initial_label.configure(text="开户初始资金")
            self.available_label.grid_remove()
            self.available_entry.grid_remove()
            self.system_label.grid()
            self.system_entry.grid()
            self.total_label.configure(text="券商资产快照")
            self.total_label.grid()
            self.total_entry.grid()
            self.reverse_label.grid()
            self.reverse_entry.grid()
            self.reconciliation_label.grid()
            self.use_reverse_button.grid()
            self._recalculate_account()
            self.hint.set(
                "开户初始资金是固定基准；系统账面总资产 = 初始资金＋已清仓净盈亏＋当前持仓浮盈亏。"
                "券商资产快照只用于独立对账，不会自动覆盖初始资金；转入转出、分红税费需另行核对。"
            )

    def _ok(self):
        try:
            initial = float(self.initial.get())
            current = float(self.total.get()) if self.total.get().strip() else None
            if self.mode.get() == "snapshot":
                available = float(self.available.get())
                if available < 0:
                    raise ValueError("可用资金不能小于零")
                current = self.market_value + available
                initial = current
            self.values = {
                "name": self.name.get().strip(), "initial_equity": initial,
                "accounting_mode": self.mode.get(), "current_total_assets": current,
                "risk_limit_pct": float(self.risk_limit.get()),
                "per_trade_risk_pct": float(self.trade_risk.get()),
                "max_position_pct": float(self.max_position.get()),
                "cash_reserve_pct": float(self.cash_reserve.get()),
            }
        except ValueError as exc:
            messagebox.showerror("输入无效", f"资金和比例必须填写有效数字：{exc}", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()


class _IdentityDialog(_BaseDialog):
    def __init__(self, parent, title, store, names):
        super().__init__(parent, title)
        self.store = store
        self.names = names
        self.lookup = StockNameLookup(names)
        self.by_name = {}
        for code, name in names.items():
            if name and name not in self.by_name:
                self.by_name[name] = code

    def identity_fields(self, code="", name=""):
        self.code = self.entry(0, "代码", numeric_stock_code(code))
        self.name = self.entry(0, "名称", name, column=1)
        self.form.grid_slaves(row=0, column=1)[0].bind("<FocusOut>", lambda _e: self._resolve_code())
        self.form.grid_slaves(row=0, column=3)[0].bind("<FocusOut>", lambda _e: self._resolve_name())

    def _resolve_code(self):
        text = self.code.get().strip()
        if not text:
            return None
        try:
            full = code_of(text)
        except ValueError:
            return None
        self.code.set(numeric_stock_code(full))
        if self.names.get(full):
            self.name.set(self.names[full])
        return full

    def _resolve_name(self):
        query = self.name.get().strip()
        code = self.by_name.get(query)
        resolved_name = query
        if not code and query:
            code, resolved_name = self.lookup.resolve(query)
        if code:
            self.code.set(numeric_stock_code(code))
            self.name.set(resolved_name or self.names.get(code, query))
        return code or self._resolve_code()


class _BatchEditor:
    def __init__(self, parent, row, dates, batches=None, inherit_first=True):
        self.parent = parent
        self.start_row = row
        self.dates = list(dates)
        self.inherit_first = inherit_first
        self.rows = []
        self._next_row = row + 1
        ttk.Label(parent, text="日期").grid(row=row, column=0, pady=(8, 2))
        ttk.Label(parent, text="价格").grid(row=row, column=1, pady=(8, 2))
        ttk.Label(parent, text="手数（100股/手）").grid(row=row, column=2, pady=(8, 2))
        ttk.Button(parent, text="＋", width=3, bootstyle="primary-outline",
                   command=self.add).grid(row=row, column=3, padx=4)
        for batch in batches or [{}]:
            self.add(batch)

    def add(self, batch=None):
        batch = batch or {}
        row_number = self._next_row
        self._next_row += 1
        inherited = self.rows[0]["date"].get() if self.rows and self.inherit_first else ""
        default_date = batch.get("date") or inherited or (self.dates[0] if self.dates else date.today().isoformat())
        date_var = tk.StringVar(value=default_date)
        price_var = tk.StringVar(value=batch.get("price", ""))
        hands_var = tk.StringVar(value=batch.get("hands", ""))
        widgets = [
            ttk.Combobox(self.parent, textvariable=date_var, values=self.dates,
                         width=13, state="readonly"),
            ttk.Entry(self.parent, textvariable=price_var, width=13),
            ttk.Entry(self.parent, textvariable=hands_var, width=15),
        ]
        for column, widget in enumerate(widgets):
            widget.grid(row=row_number, column=column, padx=3, pady=3)
        remove = ttk.Button(self.parent, text="−", width=3, bootstyle="secondary-outline",
                            command=lambda record=None: self.remove_record(record))
        record = {"date": date_var, "price": price_var, "hands": hands_var,
                  "widgets": [*widgets, remove]}
        remove.configure(command=lambda r=record: self.remove_record(r))
        remove.grid(row=row_number, column=3, padx=4)
        self.rows.append(record)

    def remove_record(self, record):
        if len(self.rows) == 1 or record not in self.rows:
            return
        for widget in record["widgets"]:
            widget.destroy()
        self.rows.remove(record)

    def values(self):
        return [{"date": row["date"].get().strip(), "price": float(row["price"].get()),
                 "hands": int(row["hands"].get())} for row in self.rows]


class _PositionDialog(_IdentityDialog):
    def __init__(self, parent, store, names, position=None):
        super().__init__(parent, "编辑持仓" if position else "新增持仓", store, names)
        position = position or {}
        self.identity_fields(position.get("code", ""), position.get("name", ""))
        self.plan_id = position.get("plan_id")
        self.observation_id = position.get("observation_id")
        self.entry_price = self.entry(1, "买点 Entry", position.get("entry", ""))
        self.stop = self.entry(1, "止损 SL1 *", position.get("stop", ""), column=1)
        self.tp1 = self.entry(2, "TP1 / MM *", position.get("tp1", ""))
        self.tp2 = self.entry(2, "TP2", position.get("tp2", ""), column=1)
        self.tp3 = self.entry(3, "TP3", position.get("tp3", ""))
        ttk.Button(self.form, text="识别最新策略计划", bootstyle="secondary",
                   command=self._autofill_plan).grid(row=3, column=2, columnspan=2, sticky=tk.W, pady=4)
        dates = trading_dates(store, position.get("code")) or trading_dates(store)
        batch_frame = ttk.Frame(self.form)
        batch_frame.grid(row=4, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        self.batches = _BatchEditor(batch_frame, 0, dates, position.get("buy_batches"))
        self.hint = tk.StringVar(value="输入代码后可自动带入名称，并识别最新策略结果或关注信号。")
        ttk.Label(self.form, textvariable=self.hint, foreground=MUTED).grid(
            row=5, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        self.buttons(6, self._ok)

    def _resolve_code(self):
        code = super()._resolve_code()
        if code:
            self._autofill_plan(silent=True, resolved=code)
        return code

    def _autofill_plan(self, silent=False, resolved=None):
        code = resolved or super()._resolve_name()
        if not code:
            if not silent:
                messagebox.showinfo("未识别代码", "请先输入有效股票代码或准确名称。", parent=self.top)
            return
        rows = self.store.rows(
            "SELECT o.id AS observation_id,o.payload,o.strategy,p.id AS plan_id,p.entry,p.stop,p.target "
            "FROM observations o LEFT JOIN plans p ON p.observation_id=o.id "
            "WHERE o.code=? ORDER BY o.created DESC,p.updated DESC LIMIT 1", (code,)
        )
        if not rows:
            if not silent:
                self.hint.set("最新策略结果和关注列表中未找到该标的，交易计划请手工填写。")
            return
        row = rows[0]
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        entry = row.get("entry") or payload.get("entry")
        stop = row.get("stop") or payload.get("stop") or payload.get("sl1")
        target = row.get("target") or payload.get("target") or payload.get("mm_target") or payload.get("tp1")
        if entry:
            self.entry_price.set(str(entry))
        if stop:
            self.stop.set(str(stop))
        if target:
            self.tp1.set(str(target))
        self.plan_id = row.get("plan_id")
        self.observation_id = row.get("observation_id")
        self.hint.set(f"已带入 {row['strategy']} 的最新计划；请按券商实际成交修正买入批次。")

    def _ok(self):
        try:
            code = super()._resolve_name()
            if not code:
                raise ValueError("无法识别股票代码")
            self.values = {
                "code": code, "name": self.name.get().strip(),
                "entry": float(self.entry_price.get()), "stop": float(self.stop.get()),
                "tp1": float(self.tp1.get()),
                "tp2": float(self.tp2.get()) if self.tp2.get().strip() else None,
                "tp3": float(self.tp3.get()) if self.tp3.get().strip() else None,
                "buy_batches": self.batches.values(), "plan_id": self.plan_id,
                "observation_id": self.observation_id,
            }
        except ValueError as exc:
            messagebox.showerror("输入无效", f"请核对交易计划和买入批次：{exc}", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()


def _next_sell_date(first_buy, dates):
    available = sorted(day for day in dates if day > first_buy)
    if available:
        return available[0]
    try:
        candidate = datetime.fromisoformat(first_buy[:10]).date() + timedelta(days=1)
    except ValueError:
        candidate = date.today()
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


class _SellDialog(_BaseDialog):
    def __init__(self, parent, store, position):
        super().__init__(parent, f"卖出 · {numeric_stock_code(position['code'])} {position['name']}")
        ttk.Label(self.form, text=f"当前持仓 {position['quantity']} 股 · 摊薄成本 {_price(position['diluted_cost'])}",
                  font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        dates = trading_dates(store, position["code"]) or trading_dates(store)
        first_sell = _next_sell_date(position["first_buy_date"], dates)
        ordered_dates = [first_sell] + [day for day in dates if day != first_sell]
        batch_frame = ttk.Frame(self.form)
        batch_frame.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(4, 0))
        self.editor = _BatchEditor(batch_frame, 0, ordered_dates, [{"date": first_sell}])
        self.fees = self.entry(2, "税费合计 *", "")
        self.buttons(3, self._ok)

    def _ok(self):
        try:
            self.batches = self.editor.values()
            self.fees_total = float(self.fees.get())
        except ValueError as exc:
            messagebox.showerror("输入无效", f"请核对卖出批次和税费：{exc}", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()


class _ClosedDialog(_IdentityDialog):
    def __init__(self, parent, store, names, row=None):
        super().__init__(parent, "编辑清仓记录" if row else "新增历史清仓", store, names)
        row = row or {}
        self.batch_values = None
        self.identity_fields(row.get("code", ""), row.get("name", ""))
        dates = trading_dates(store, row.get("code")) or trading_dates(store)
        ttk.Label(self.form, text="清仓日期").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.close_date = tk.StringVar(value=row.get("close_date") or (dates[0] if dates else date.today().isoformat()))
        ttk.Combobox(self.form, textvariable=self.close_date, values=dates,
                     width=20, state="readonly").grid(
            row=1, column=1, sticky=tk.EW, padx=(0, 12), pady=4
        )
        self.days = self.entry(1, "持仓天数", row.get("holding_days", ""), column=1)
        self.pnl = self.entry(2, "盈亏", row.get("pnl", ""))
        self.return_pct = self.entry(2, "收益率 %", row.get("return_pct", ""), column=1)
        self.notes = self.entry(3, "备注", row.get("notes", ""))
        if not row:
            ttk.Button(self.form, text="批量填表", bootstyle="primary",
                       command=self._open_batch).grid(row=3, column=2, columnspan=2,
                                                      sticky=tk.EW, padx=(0, 12), pady=4)
        self.buttons(4, self._ok)

    def _open_batch(self):
        dialog = _ClosedBatchDialog(self.top, self.store, self.names)
        self.top.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        self.batch_values = dialog.values
        self.confirmed = True
        self.top.destroy()

    def _ok(self):
        try:
            code = self._resolve_name()
            if not code:
                raise ValueError("无法识别股票代码")
            self.values = {
                "code": code, "name": self.name.get().strip(),
                "close_date": self.close_date.get().strip(),
                "holding_days": int(self.days.get()), "pnl": float(self.pnl.get()),
                "return_pct": float(self.return_pct.get()), "notes": self.notes.get().strip(),
            }
        except ValueError as exc:
            messagebox.showerror("输入无效", f"请核对清仓记录：{exc}", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()


class _ClosedBatchDialog(_BaseDialog):
    COLUMNS = (("代码", 11), ("名称", 14), ("清仓日期", 12), ("持仓天数", 9),
               ("盈亏", 10), ("收益率 %", 10))

    def __init__(self, parent, store, names):
        super().__init__(parent, "批量填写历史清仓")
        self.top.resizable(True, True)
        self.store = store
        self.names = names
        self.lookup = StockNameLookup(names)
        self.by_name = {name: code for code, name in names.items() if name}
        self.rows = []
        self._next_grid_row = 2
        self.dates = trading_dates(store)
        ttk.Label(self.form, text="每行一笔；代码或名称填写一项即可自动关联。",
                  foreground=MUTED).grid(row=0, column=0, columnspan=5, sticky=tk.W, pady=(0, 8))
        ttk.Button(
            self.form,
            text="＋ 增加五行",
            bootstyle="secondary-outline",
            command=lambda: self._add_rows(5),
        ).grid(row=0, column=5, columnspan=2, sticky=tk.E, padx=2, pady=(0, 8))
        for column, (label, _width) in enumerate(self.COLUMNS):
            ttk.Label(self.form, text=label, anchor=tk.CENTER,
                      font=("Microsoft YaHei UI", 9, "bold")).grid(
                          row=1, column=column, sticky=tk.EW, padx=2, pady=2)
        ttk.Label(self.form, text="操作", anchor=tk.CENTER,
                  font=("Microsoft YaHei UI", 9, "bold")).grid(row=1, column=6, padx=2)
        self._add_rows(5)
        self.controls = ttk.Frame(self.form)
        self.controls.grid(row=self._next_grid_row, column=0, columnspan=7, sticky=tk.E, pady=(12, 0))
        ttk.Button(self.controls, text="确认导入", bootstyle="primary",
                   command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.controls, text="取消", bootstyle="secondary",
                   command=self.top.destroy).pack(side=tk.LEFT, padx=4)

    def _add_rows(self, count=5):
        for _index in range(max(1, int(count))):
            self._add_row()

    def _add_row(self):
        grid_row = self._next_grid_row
        self._next_grid_row += 1
        variables = [tk.StringVar() for _item in self.COLUMNS]
        default_date = self.dates[0] if self.dates else date.today().isoformat()
        variables[2].set(default_date)
        widgets = []
        for column, ((_label, width), variable) in enumerate(zip(self.COLUMNS, variables)):
            if column == 2:
                widget = ttk.Combobox(self.form, textvariable=variable,
                                      values=self.dates, width=width, state="readonly")
            else:
                widget = ttk.Entry(self.form, textvariable=variable, width=width)
            widget.grid(row=grid_row, column=column, sticky=tk.EW, padx=2, pady=2)
            widgets.append(widget)
        record = {"vars": variables, "widgets": widgets}
        remove = ttk.Button(self.form, text="−", width=3, bootstyle="secondary-outline",
                            command=lambda r=record: self._remove_row(r))
        remove.grid(row=grid_row, column=6, padx=2, pady=2)
        record["widgets"].append(remove)
        widgets[0].bind("<FocusOut>", lambda _e, r=record: self._resolve_row(r, True))
        widgets[1].bind("<FocusOut>", lambda _e, r=record: self._resolve_row(r, False))
        for column, widget in enumerate(widgets[:len(self.COLUMNS)]):
            widget.bind("<Up>", lambda _e, r=record, c=column: self._move_cell(r, c, -1, 0))
            widget.bind("<Down>", lambda _e, r=record, c=column: self._move_cell(r, c, 1, 0))
            widget.bind("<Left>", lambda _e, r=record, c=column: self._move_cell(r, c, 0, -1))
            widget.bind("<Right>", lambda _e, r=record, c=column: self._move_cell(r, c, 0, 1))
        self.rows.append(record)
        if hasattr(self, "controls"):
            self.controls.grid_configure(row=self._next_grid_row)

    def _remove_row(self, record):
        if len(self.rows) <= 1 or record not in self.rows:
            return
        for widget in record["widgets"]:
            widget.destroy()
        self.rows.remove(record)

    def _resolve_row(self, record, from_code):
        code_var, name_var = record["vars"][:2]
        if from_code and code_var.get().strip():
            try:
                full = code_of(code_var.get().strip())
            except ValueError:
                return None
            code_var.set(numeric_stock_code(full))
            if self.names.get(full):
                name_var.set(self.names[full])
            return full
        query = name_var.get().strip()
        full = self.by_name.get(query)
        resolved_name = query
        if not full and query:
            full, resolved_name = self.lookup.resolve(query)
        if full:
            code_var.set(numeric_stock_code(full))
            name_var.set(resolved_name or self.names.get(full, query))
            return full
        return self._resolve_row(record, True) if code_var.get().strip() else None

    def _move_cell(self, record, column, row_delta, column_delta):
        if column in (0, 1):
            self._resolve_row(record, column == 0)
        try:
            row_index = self.rows.index(record)
        except ValueError:
            return "break"
        target_row = min(max(row_index + row_delta, 0), len(self.rows) - 1)
        target_column = min(max(column + column_delta, 0), len(self.COLUMNS) - 1)
        target = self.rows[target_row]["widgets"][target_column]
        target.focus_set()
        if isinstance(target, ttk.Entry):
            target.icursor(tk.END)
        return "break"

    def _ok(self):
        values = []
        try:
            for record in self.rows:
                raw = [variable.get().strip() for variable in record["vars"]]
                if not any((raw[0], raw[1], raw[3], raw[4], raw[5])):
                    continue
                code = self._resolve_row(record, bool(raw[0]))
                if not code:
                    raise ValueError("存在无法识别的股票代码或名称")
                raw = [variable.get().strip() for variable in record["vars"]]
                values.append({
                    "code": code, "name": raw[1], "close_date": raw[2],
                    "holding_days": int(raw[3]), "pnl": float(raw[4]),
                    "return_pct": float(raw[5]), "notes": "",
                })
            if not values:
                raise ValueError("请至少填写一行完整记录")
        except ValueError as exc:
            messagebox.showerror("输入无效", f"请核对批量清仓表：{exc}", parent=self.top)
            return
        self.values = values
        self.confirmed = True
        self.top.destroy()
