"""交易管理：持仓、资金账户、已清仓与收益日历。"""
from __future__ import annotations

import calendar
import json
import queue
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont, messagebox

import ttkbootstrap as ttk

from workbench.market import code_of
from workbench.exchange_calendar import is_trading_day
from workbench.holding_quotes import (
    QuotePoller, china_now, saved_quotes, select_quotes, session_status,
    snapshot_reference, value_holdings, net_invested,
)
from workbench.closed_import import MAX_SCREENSHOTS, recognize_screenshots
from workbench.daily_returns import (
    historical_daily_returns, parse_calendar_text, recognize_calendar_screenshots,
)
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
    return "—" if value is None else f"{float(value or 0):,.0f}"


def _price(value):
    return "—" if value in (None, "") else f"{float(value):.2f}"


def _pct(value):
    return "—" if value is None else f"{float(value or 0):.2f}%"


def _sort_value(value):
    text = str(value or "").replace(",", "").replace("%", "").replace("¥", "").replace("*", "")
    text = text.split(" · ", 1)[0]
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
        self._accounts = []
        self._show_fund_values = False
        self._quote_poller = QuotePoller()
        self._live_quotes = {}
        self._quote_failed = set()
        self._base_report = None
        self._quote_timer = None
        self._quote_due = 0.0
        self._quote_view_key = None
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1, minsize=210)
        self.rowconfigure(2, weight=3, minsize=630)
        self._build_header()
        self._build_panels()
        self.bind_all("<Button-1>", self._clear_position_selection, add="+")
        self.reload()
        self.bind("<Destroy>", self._quotes_destroyed, add="+")
        self._quote_timer = self.after(200, self._quote_tick)

    def _build_header(self):
        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 2))
        self.account_var = tk.StringVar()
        self.asof_var = tk.StringVar(value="尚无持仓行情")
        self.refresh_button = ttk.Button(
            header, text="刷新", width=7, bootstyle="secondary-outline",
            command=self._manual_quote_refresh,
        )
        self.refresh_button.pack(side=tk.RIGHT, padx=(6, 0))
        self.auto_quote_var = tk.BooleanVar(value=self.store.holding_auto_quotes() if self.store else False)
        ttk.Checkbutton(header, text="自动行情", variable=self.auto_quote_var,
                        command=self._toggle_auto_quotes, bootstyle="success-round-toggle").pack(
                            side=tk.RIGHT, padx=(8, 0))
        self.eye_button = ttk.Button(
            header, text="👁 显示", width=9, bootstyle="primary",
            command=self._toggle_fund_values,
        )
        self.eye_button.pack(side=tk.RIGHT)
        ttk.Label(header, textvariable=self.asof_var, foreground=MUTED).pack(
            side=tk.RIGHT, padx=(0, 12)
        )

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

        funds = self._panel(1, 1, "资金账户")
        funds.rowconfigure(2, weight=1)
        account_bar = ttk.Frame(funds)
        account_bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 5))
        ttk.Label(account_bar, text="资金账号：", foreground=MUTED).pack(side=tk.LEFT)
        self.account_box = ttk.Combobox(
            account_bar, textvariable=self.account_var, state="readonly", width=26
        )
        self.account_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.account_box.bind("<<ComboboxSelected>>", lambda _e: self.reload_data())
        ttk.Button(
            account_bar, text="•••", width=4, bootstyle="secondary-link",
            command=self._show_fund_menu,
        ).pack(side=tk.LEFT)

        overview = ttk.Frame(funds)
        overview.grid(row=1, column=0, sticky=tk.EW)
        overview.columnconfigure(0, weight=1)
        self.fund_total = tk.StringVar(value="—")
        total_box = ttk.Frame(overview)
        total_box.grid(row=0, column=0, sticky=tk.W)
        ttk.Label(total_box, text="总资产", foreground=MUTED).pack(side=tk.LEFT)
        self.fund_position = tk.StringVar(value="证券仓位：—")
        ttk.Label(total_box, textvariable=self.fund_position, foreground=TEXT,
                  bootstyle="secondary-inverse", padding=(8, 2)).pack(side=tk.LEFT, padx=10)
        ttk.Label(overview, textvariable=self.fund_total,
                  font=("Microsoft YaHei UI", 21, "bold"), foreground=TEXT).grid(
                      row=1, column=0, sticky=tk.W, pady=(0, 5))

        metrics = ttk.Frame(funds)
        metrics.grid(row=2, column=0, sticky=tk.NSEW)
        for column in range(3):
            metrics.columnconfigure(column, weight=1)
        for row in range(2):
            metrics.rowconfigure(row, weight=1)
        self.fund_metrics = {}
        for index, (key, label) in enumerate((
            ("market_value", "证券市值"), ("floating_pnl", "持仓盈亏"),
            ("daily_pnl", "当日盈亏（暂估）"), ("withdrawable_cash", "可取"),
            ("available_cash", "可用"), ("asset_pnl", "资产盈亏"),
        )):
            cell = ttk.Frame(metrics)
            cell.grid(row=index // 3, column=index % 3, sticky=tk.NSEW, padx=(0, 8), pady=2)
            ttk.Label(cell, text=label, foreground=MUTED).pack(anchor=tk.W)
            variable = tk.StringVar(value="—")
            value_label = ttk.Label(cell, textvariable=variable,
                                    font=("Microsoft YaHei UI", 11, "bold"), foreground=TEXT)
            value_label.pack(anchor=tk.W)
            self.fund_metrics[key] = (variable, value_label)
        self.funds_reconciliation = tk.StringVar(value="")
        ttk.Label(funds, textvariable=self.funds_reconciliation, foreground=MUTED,
                  font=("Microsoft YaHei UI", 9), anchor=tk.W).grid(
                      row=3, column=0, sticky=tk.EW, pady=(4, 0)
                  )

        closed = self._panel(2, 0, "已清仓", self._show_closed_add_menu)
        closed.rowconfigure(0, weight=1)
        closed.rowconfigure(1, weight=0)
        self.closed_tree = self._tree(closed, (
            ("number", "序号", 54), ("code", "代码", 74), ("name", "名称", 100),
            ("date", "清仓日期", 92), ("days", "持仓天数", 78),
            ("pnl", "盈亏", 82), ("return", "收益率", 76),
        ), "closed", grid_row=0)
        self.closed_tree.bind("<Button-3>", self._closed_menu)
        self.closed_tree.bind("<Double-1>", lambda _e: self._edit_closed())

        calendar_body = self._panel(2, 1, "盈亏日历")
        self.return_calendar = _ReturnCalendar(calendar_body, self._import_daily_returns,
                                               calendar_provider=self._quote_calendar)
        self.return_calendar.set_hidden(True)

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
        self._accounts = accounts
        self._account_by_label = {self._account_label(row): row["id"] for row in accounts}
        labels = list(self._account_by_label)
        self.account_box.configure(values=labels)
        selected = next((label for label, aid in self._account_by_label.items() if aid == current_id), None)
        self.account_var.set(selected or (labels[0] if labels else "尚未设置账户"))
        self.account_box.configure(state="readonly" if labels else "disabled")
        self.reload_data()

    def current_account_id(self):
        return self._account_by_label.get(self.account_var.get())

    def _account_label(self, account):
        number = str(account.get("broker_account_no") or "").strip()
        if not number:
            return account["name"]
        if self._show_fund_values:
            visible = f"{number[:4]}****{number[-4:]}" if len(number) > 8 else number
        else:
            visible = "••••••••"
        return f"{account['name']} · {visible}"

    def _toggle_fund_values(self):
        self._set_fund_values(not self._show_fund_values)

    def hide_private_values(self):
        """Re-entering the page must not reuse a previous explicit reveal."""
        self._set_fund_values(False)

    def _set_fund_values(self, visible):
        account_id = self.current_account_id()
        self._show_fund_values = visible
        self.eye_button.configure(text="👁 隐藏" if self._show_fund_values else "👁 显示")
        self._account_by_label = {self._account_label(row): row["id"] for row in self._accounts}
        labels = list(self._account_by_label)
        self.account_box.configure(values=labels)
        selected = next((label for label, aid in self._account_by_label.items()
                         if aid == account_id), labels[0] if labels else "尚未设置账户")
        self.account_var.set(selected)
        if self._report:
            self._fill_funds()

    def _show_fund_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="账户设置", command=self._edit_account)
        menu.add_command(label="新增账户", command=lambda: self._edit_account(create_new=True))
        menu.add_separator()
        menu.add_command(label="新增资金调整", command=self._add_cash_flow)
        menu.add_command(label="管理资金调整", command=self._manage_cash_flows)
        menu.add_separator()
        menu.add_command(label="刷新", command=self.reload_data)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def reload_data(self):
        self._quote_poller.invalidate()
        self._base_report = None
        self._quote_view_key = None
        self._quote_due = 0.0
        for button in self._sell_buttons.values():
            button.destroy()
        self._sell_buttons = {}
        for tree in (self.position_tree, self.closed_tree):
            tree.delete(*tree.get_children())
        self._position_ids = {}
        self._closed_ids = {}
        account_id = self.current_account_id()
        if not account_id:
            self._report = None
            self.quick_result.set("请先点“账户设置”，再使用持仓管理与拟建仓速算")
            self.quick_result_label.configure(foreground=MUTED)
            self.fund_total.set("—")
            self.fund_position.set("证券仓位：—")
            for variable, label in self.fund_metrics.values():
                variable.set("—")
                label.configure(foreground=TEXT)
            self.funds_reconciliation.set("请通过右上角“•••”新增或设置资金账户。")
            self.asof_var.set("尚无持仓行情")
            self.return_calendar.set_rows([])
            return
        try:
            self._names = code_names(self.store)
            self._report = management_report(self.store, account_id)
            calculated = historical_daily_returns(self.store, account_id)
            self.store.save_daily_returns(account_id, calculated, replace_local=True)
            daily_rows = self.store.list_daily_returns(account_id)
            self.return_calendar.set_rows(daily_rows)
            quote_day = self._report["summary"]["quote_date"]
            daily = next((r for r in daily_rows if r["date"] == quote_day), None)
            self._report["summary"]["daily_pnl"] = daily["pnl"] if daily else None
            self._base_report = self._report
            self._holding_fills = self.store.rows(
                "SELECT f.*,p.code FROM position_fills f JOIN positions p ON p.id=f.position_id "
                "WHERE p.account_id=? ORDER BY f.trade_date,f.created,f.id", (account_id,))
            self._quote_codes = {row["code"] for row in self._report["positions"]}
            self._quote_codes.update(f["code"] for f in self._holding_fills
                                     if f["trade_date"][:10] == china_now().date().isoformat())
            self._history_quotes = saved_quotes(self.store, self._quote_codes)
            self._cash_reference = None
            if self._report["account"].get("accounting_mode") == "snapshot":
                self._cash_reference = self.store.holding_cash_reference(
                    account_id, snapshot_reference(self._report, self._holding_fills))
            self._apply_quote_view()
            self._fill_positions()
            self._fill_funds()
            self._fill_closed()
        except Exception as exc:
            self.quick_result.set(f"读取交易账本失败：{exc}")
            self.quick_result_label.configure(foreground=DOWN)

    def _quote_calendar(self):
        try:
            return json.loads((self.store.root / "trading_calendar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            return None

    def _toggle_auto_quotes(self):
        self._quote_poller.invalidate()
        if self.store is not None:
            self.store.holding_auto_quotes(self.auto_quote_var.get())
        self._quote_due = 0.0
        self._quote_view_key = None
        if self.auto_quote_var.get() and session_status(china_now(), self._quote_calendar()) == "交易中":
            self._start_quotes()
        self._apply_quote_view()

    def _manual_quote_refresh(self):
        self._start_quotes()

    def _start_quotes(self):
        if not self._base_report or not self._quote_codes:
            self.asof_var.set("暂无持仓，无需更新行情")
            return
        if self._quote_poller.start(self._quote_codes):
            self.refresh_button.configure(state="disabled")
            self.asof_var.set("正在获取报价…")

    def _quotes_destroyed(self, event):
        if event.widget is self:
            self._quote_poller.invalidate()
            if self._quote_timer:
                self.after_cancel(self._quote_timer)
                self._quote_timer = None

    def _quote_tick(self):
        result = self._quote_poller.take()
        if result is not None:
            quotes, self._quote_failed = result
            for code, quote in quotes.items():
                if quote["quote_time"] >= self._live_quotes.get(code, {}).get("quote_time", ""):
                    self._live_quotes[code] = quote
            self._quote_due = time.monotonic() + self._quote_poller.delay
            self._quote_view_key = None
        if not self._quote_poller.busy:
            self.refresh_button.configure(state="normal")
        moment, calendar = china_now(), self._quote_calendar()
        if self.auto_quote_var.get() and session_status(moment, calendar) == "交易中":
            if time.monotonic() >= self._quote_due:
                self._start_quotes()
        if not self._quote_poller.busy:
            self._apply_quote_view(moment, calendar)
        self._quote_timer = self.after(1000, self._quote_tick)

    def _apply_quote_view(self, moment=None, calendar=None):
        if not self._base_report:
            return
        moment = moment or china_now()
        calendar = calendar if calendar is not None else self._quote_calendar()
        quotes = select_quotes(self._history_quotes, self._live_quotes, self._quote_codes,
                               self._quote_failed, moment, calendar)
        status = session_status(moment, calendar)
        key = repr((quotes, status, self.auto_quote_var.get(), moment.date(), self._quote_poller.delay))
        if key == self._quote_view_key:
            return
        self._quote_view_key = key
        self._report = value_holdings(self._base_report, quotes, self._holding_fills,
                                      moment, self._cash_reference)
        # Stable tree identities/order, selection, buttons, scroll and editing dialogs.
        for iid, pid in self._position_ids.items():
            row = next((r for r in self._report["positions"] if r["id"] == pid), None)
            if row and self.position_tree.exists(iid):
                self.position_tree.item(iid, values=self._position_values(row), tags=self._position_tags(row))
        if self.position_tree.exists("__summary__"):
            self.position_tree.item("__summary__", values=self._position_summary())
        self._fill_funds(update_calendar=False)
        times = [q.get("quote_time", "") for q in quotes.values() if q.get("source") in ("sina", "tencent")]
        dates = [q.get("date", "") for q in quotes.values() if q.get("source") == "history"]
        missing = len(self._quote_codes - quotes.keys())
        stale = any(q.get("state") in ("更新失败", "行情滞后") for q in quotes.values())
        if times:
            text = "报价 " + min(times).replace("T", " ")[:19]
            if any(q.get('source') == 'tencent' for q in quotes.values()):
                text += ' · 腾讯' + ('／新浪' if any(q.get('source') == 'sina' for q in quotes.values()) else '')
        else:
            text = "历史收盘 " + min(dates) if dates else ("缺少行情" if self._quote_codes else "暂无持仓")
        if self._cash_reference is None and self._report["account"].get("accounting_mode") == "snapshot":
            text += " · 请在账户设置校准现金基准"
        if self._quote_failed or stale:
            text += " · 更新失败／行情滞后" if self._quote_failed else " · 行情滞后"
            reasons=getattr(self._quote_failed,'details',{})
            if reasons:
                text+='：'+next(iter(reasons.values()))[:95]
        if missing:
            text += f" · {missing} 只缺少行情"
        if times and dates:
            text += f" · {len(dates)} 只历史收盘 {min(dates)}"
        if self.auto_quote_var.get():
            if status == "交易中":
                action = "自动重试" if self._quote_poller.failures else "自动更新"
                text += f" · {action}（{self._quote_poller.delay}秒）"
            else:
                text += " · 自动暂停：" + status
        self.asof_var.set(text)

    @staticmethod
    def _position_tags(row):
        pnl = row["floating_pnl"]
        tag = "profit" if pnl is not None and pnl > 0 else ("loss" if pnl is not None and pnl < 0 else "")
        return (tag,) if tag else ()

    def _position_values(self, row):
        quote = _price(row["current_price"])
        return (numeric_stock_code(row["code"]), row["name"], _money(row["market_value"]),
                _money(row["floating_pnl"]), quote, _price(row["diluted_cost"]),
                _pct(row["allocation_pct"]), row["quantity"], _price(row["stop"]),
                _pct(row["distance_stop_pct"]), _pct(row["trade_risk_pct"]),
                _pct(row["account_risk_pct"]), "")

    def _position_summary(self):
        s = self._report["summary"]
        return ("汇总", "—", _money(s["market_value"]), _money(s["floating_pnl"]),
                "—", "—", _pct(s["position_pct"]),
                sum(int(row["quantity"]) for row in self._report["positions"]),
                "—", "—", "—", _pct(s["risk_pct"]), "")

    def _fill_positions(self):
        for row in self._report["positions"]:
            iid = self.position_tree.insert("", tk.END, values=self._position_values(row),
                                            tags=self._position_tags(row))
            self._position_ids[iid] = row["id"]
            self._sell_buttons[iid] = ttk.Button(
                self.position_tree,
                text="卖出",
                style="PositionSell.primary.Outline.TButton",
                padding=(4, 0, 4, 2),
                command=lambda pid=row["id"]: self._sell_position_by_id(pid),
            )
        if len(self._report["positions"]) > 1:
            self.position_tree.insert("", tk.END, iid="__summary__", values=self._position_summary(),
                                      tags=("summary",))
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

    def _fill_closed(self):
        for number, row in enumerate(self._report["closed"], 1):
            tag = "profit" if row["pnl"] > 0 else ("loss" if row["pnl"] < 0 else "")
            iid = self.closed_tree.insert("", tk.END, values=(
                number, numeric_stock_code(row["code"]), row["name"], row["close_date"],
                row["holding_days"], _money(row["pnl"]), _pct(row["return_pct"]),
            ), tags=(tag,) if tag else ())
            self._closed_ids[iid] = row["id"]
        self.after_idle(self._refit_tree, self.closed_tree)

    def _fill_funds(self, update_calendar=True):
        summary = self._report["summary"]
        account = self._report["account"]
        hidden = not self._show_fund_values
        if update_calendar:
            self.return_calendar.set_hidden(hidden)

        def amount(value, signed=False):
            if hidden:
                return "••••••"
            if value is None:
                return "—"
            return f"{float(value):+,.2f}" if signed else f"{float(value):,.2f}"

        self.fund_total.set(amount(summary["total_assets"]))
        self.fund_position.set("证券仓位：" + _pct(summary['position_pct']))
        values = {
            "market_value": (summary["market_value"], False),
            "floating_pnl": (summary["floating_pnl"], True),
            "daily_pnl": (summary["daily_pnl"], True),
            "withdrawable_cash": (summary["withdrawable_cash"], False),
            "available_cash": (summary["available_cash"], False),
            "asset_pnl": (summary["asset_pnl"], True),
        }
        for key, (value, signed) in values.items():
            variable, label = self.fund_metrics[key]
            variable.set(amount(value, signed=signed))
            if not hidden and signed and value not in (None, 0):
                label.configure(foreground=UP if value > 0 else DOWN)
            else:
                label.configure(foreground=TEXT)
        if hidden:
            self.funds_reconciliation.set("初始资金（推算） •••••• 元")
        elif account.get("accounting_mode") == "history":
            if summary["broker_total_assets"] is not None and summary["implied_initial_equity"] is not None:
                text = f"初始资金（推算） {summary['implied_initial_equity']:,.2f} 元"
            else:
                text = "初始资金（推算） —"
            self.funds_reconciliation.set(text)
        else:
            self.funds_reconciliation.set("初始资金（推算） —")

    def _import_daily_returns(self):
        account_id = self.current_account_id()
        if not account_id:
            messagebox.showinfo("请先设置账户", "请先选择资金账户。", parent=self)
            return
        dialog = _DailyReturnImportDialog(self)
        self.wait_window(dialog.top)
        if dialog.confirmed:
            try:
                self.store.save_daily_returns(account_id, dialog.values)
            except ValueError as exc:
                messagebox.showerror("日历导入失败", str(exc), parent=self)
                return
            self.reload_data()

    def _edit_account(self, create_new=False):
        account = None
        aid = None if create_new else self.current_account_id()
        if aid:
            rows = self.store.rows("SELECT * FROM accounts WHERE id=?", (aid,))
            account = rows[0] if rows else None
        market_value = 0.0 if create_new else (
            self._report["summary"]["market_value"] if self._report else 0.0
        )
        summary = {} if create_new else (self._report["summary"] if self._report else {})
        if account and account.get("accounting_mode") == "history" and self._base_report:
            summary = self._base_report["summary"]
        dialog = _AccountDialog(self, account, market_value=market_value,
                                summary=summary, create_new=create_new)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            account_id = None if create_new else (account or {}).get("id")
            reference = None
            if dialog.values["accounting_mode"] == "snapshot":
                if market_value is None:
                    raise ValueError("持仓行情不全，无法校准快照现金；请先补齐行情。")
                reference = {"cash": dialog.values["current_total_assets"] - market_value,
                             "invested": net_invested(self._holding_fills) if not create_new and self._report else 0,
                             "adjustments": summary.get("cash_adjustments", 0)}
            saved = self.store.save_account(**dialog.values, account_id=account_id,
                                            snapshot_reference=reference)
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
        menu.add_command(label="查看持仓图", command=self._view_position_chart)
        menu.add_separator()
        menu.add_command(label="删除误录持仓", command=self._delete_position)
        menu.tk_popup(event.x_root, event.y_root)

    def _add_position(self):
        self._open_position_dialog(None)

    def _edit_position(self):
        row = self._selected_position()
        if row:
            self._open_position_dialog(row)

    def _view_position_chart(self):
        row = self._selected_position()
        if row:
            self._open_position_chart(self, row)

    def _open_position_chart(self, parent, row):
        from .closed_trade_chart import HoldingTradeChartDialog
        previous_grab = parent.grab_current()
        try:
            quote = self._live_quotes.get(row["code"]) or self._history_quotes.get(row["code"])
            dialog = HoldingTradeChartDialog(parent, self.store, row, quote=quote)
        except Exception as exc:
            messagebox.showerror("无法查看 K 线", str(exc), parent=parent)
            return
        parent.wait_window(dialog.top)
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()

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

    def _show_closed_add_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="粘贴截图（最多 6 张）", command=self._paste_closed_screenshots)
        menu.add_command(label="手工新增", command=self._add_closed)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _paste_closed_screenshots(self):
        if not self.current_account_id():
            messagebox.showinfo("请先设置账户", "先设置账户，再导入历史清仓。", parent=self)
            return
        dialog = _ScreenshotPasteDialog(self, self.store, self._names or code_names(self.store))
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_closed_trades_batch(self.current_account_id(), dialog.values)
        except Exception as exc:
            messagebox.showerror("截图导入失败", str(exc), parent=self)
            return
        self.reload_data()

    def _add_closed(self):
        self._open_closed_dialog(None)

    def _add_cash_flow(self):
        self._open_cash_flow_dialog(None)

    def _manage_cash_flows(self):
        account_id = self.current_account_id()
        if not account_id:
            messagebox.showinfo("请先设置账户", "先设置账户，再管理资金调整。", parent=self)
            return
        dialog = _CashFlowManagerDialog(self, self.store, account_id)
        self.wait_window(dialog.top)
        self.reload_data()

    def _open_cash_flow_dialog(self, row):
        if not self.current_account_id():
            messagebox.showinfo("请先设置账户", "先设置账户，再记录资金流水。", parent=self)
            return
        dialog = _CashFlowDialog(self, row)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_cash_flow(
                self.current_account_id(), **dialog.values,
                flow_id=(row or {}).get("id"),
            )
        except Exception as exc:
            messagebox.showerror("资金流水保存失败", str(exc), parent=self)
            return
        self.reload_data()

    def _edit_closed(self):
        row = self._selected_closed()
        if not row:
            return
        if row.get("position_id"):
            self._view_closed_chart(row)
            return
        self._open_closed_dialog(row)

    def _view_closed_chart(self, row):
        from .closed_trade_chart import ClosedTradeChartDialog
        try:
            dialog = ClosedTradeChartDialog(self, self.store, row)
        except Exception as exc:
            messagebox.showerror("无法查看 K 线", str(exc), parent=self)
            return
        self.wait_window(dialog.top)

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


class _ReturnCalendar:
    """Account daily P&L. Dates, amounts and provenance are separate widgets."""

    def __init__(self, parent, import_command=None, calendar_provider=None):
        self.parent = parent
        self.calendar_provider = calendar_provider
        self.rows = []
        self.hidden = False
        self.anchor = date.today().replace(day=1)
        self._initialized = False
        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))
        self.scale = tk.StringVar(value="月")
        ttk.Combobox(controls, textvariable=self.scale, values=("月", "年"),
                     width=3, state="readonly").pack(side=tk.LEFT)
        self.scale.trace_add("write", lambda *_args: self._scale_changed())
        self.previous_text = tk.StringVar(value="上月")
        self.next_text = tk.StringVar(value="下月")
        ttk.Button(controls, textvariable=self.previous_text, width=5, bootstyle="secondary-outline",
                   command=lambda: self.shift(-1)).pack(side=tk.LEFT, padx=(8, 2))
        self.title = tk.StringVar()
        ttk.Label(controls, textvariable=self.title, font=("Microsoft YaHei UI", 10, "bold"))\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, textvariable=self.next_text, width=5, bootstyle="secondary-outline",
                   command=lambda: self.shift(1)).pack(side=tk.LEFT, padx=2)
        if import_command:
            ttk.Button(controls, text="补录日历", bootstyle="primary-outline",
                       command=import_command).pack(side=tk.RIGHT)
        info = ttk.Label(controls, text="ⓘ", foreground=MUTED)
        info.pack(side=tk.RIGHT, padx=5)
        from ttkbootstrap.widgets import ToolTip
        ToolTip(info, text="账户日盈亏：持仓当天涨跌 + 当天买卖影响 − 税费。\n"
                "券商截图优先；本地计算需完整成交与同日行情。\n"
                "清仓盈亏是整笔盈亏，无法代替日盈亏；缺失数据不记为零。\n"
                "本地行情含前复权历史，跨除权日仅供估算；券商交割记录优先。\n"
                "本地资金调整计入收益，转入转出除外；企业行动等以券商记录核验。")
        self.summary = tk.StringVar()
        ttk.Label(parent, textvariable=self.summary, foreground=MUTED).grid(
            row=2, column=0, sticky=tk.W, pady=(4, 0))
        self.grid = ttk.Frame(parent)
        self.grid.grid(row=1, column=0, sticky=tk.NSEW)
        parent.rowconfigure(1, weight=1)

    def set_rows(self, rows):
        self.rows = list(rows or [])
        valid = []
        for row in self.rows:
            try:
                valid.append(date.fromisoformat(row["date"]))
            except (KeyError, TypeError, ValueError):
                pass
        if valid and not self._initialized:
            latest = max(valid)
            self.anchor = latest.replace(day=1)
        self._initialized = bool(valid) or self._initialized
        self.render()

    def set_hidden(self, hidden):
        if self.hidden != hidden:
            self.hidden = hidden
            self.render()

    def _scale_changed(self):
        yearly = self.scale.get() == "年"
        self.previous_text.set("上年" if yearly else "上月")
        self.next_text.set("下年" if yearly else "下月")
        self.render()

    def shift(self, delta):
        if self.scale.get() == "年":
            self.anchor = self.anchor.replace(year=self.anchor.year + delta)
        else:
            index = self.anchor.year * 12 + self.anchor.month - 1 + delta
            self.anchor = date(index // 12, index % 12 + 1, 1)
        self.render()

    def render(self):
        for child in self.grid.winfo_children():
            child.destroy()
        for index in range(7):
            self.grid.columnconfigure(index, weight=0, minsize=0)
            self.grid.rowconfigure(index, weight=0, minsize=0)
        if self.scale.get() == "年":
            self._render_year()
        else:
            self._render_month()

    def _render_month(self):
        year, month = self.anchor.year, self.anchor.month
        market_calendar = self.calendar_provider() if self.calendar_provider else None
        today = china_now().date()
        self.title.set(f"{year} 年 {month:02d} 月")
        daily = {int(row["date"][-2:]): row for row in self.rows
                 if row["date"].startswith(f"{year}-{month:02d}-")}
        recorded = [r for r in daily.values() if r["status"] == "recorded"]
        total = sum(r["pnl"] for r in recorded)
        amount = "••••••" if self.hidden else f"{total:+,.2f} 元"
        self.summary.set(f"已记录合计 {amount} · {len(recorded)} 天（未补日期不计入）")
        for column, label in enumerate(("一", "二", "三", "四", "五")):
            self.grid.columnconfigure(column, weight=1)
            ttk.Label(self.grid, text=label, anchor=tk.CENTER, foreground=MUTED).grid(
                row=0, column=column, sticky=tk.EW, pady=2)
        weeks = calendar.Calendar().monthdayscalendar(year, month)
        while len(weeks) < 6:
            weeks.append([0] * 7)
        for week_index, week in enumerate(weeks[:6], 1):
            for column, day in enumerate(week[:5]):
                cell = ttk.Frame(self.grid)
                cell.grid(row=week_index, column=column, sticky=tk.NSEW, padx=2, pady=2)
                cell.columnconfigure(0, weight=1)
                cell.rowconfigure(1, weight=1)
                if day:
                    item = daily.get(day)
                    day_date = date(year, month, day)
                    trading = is_trading_day(day_date, market_calendar)
                    # Display only: no fabricated zero-return rows or ledger writes.
                    if not item and trading is False:
                        item = {"status": "closed"}
                    self._cell(cell, f"{day:02d}", item,
                               future=day_date > today, unknown_calendar=trading is None)
                self.grid.rowconfigure(week_index, weight=1, minsize=65)

    def _cell(self, cell, label, item, future=False, unknown_calendar=False):
        ttk.Label(cell, text=label, anchor=tk.CENTER, foreground=TEXT,
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky=tk.EW)
        color, detail = MUTED, ""
        if not item:
            value = "—" if future else ("日历待更新" if unknown_calendar else "待补数据")
        elif item["status"] == "closed":
            value = "休市"
        else:
            pnl = item["pnl"]
            value = "••••••" if self.hidden else f"{pnl:+,.2f}"
            color = MUTED if self.hidden else (UP if pnl > 0 else DOWN if pnl < 0 else MUTED)
            detail = "本地计算" if item["source"] == "local" else ""
        ttk.Label(cell, text=value, anchor=tk.CENTER, foreground=color).grid(
            row=1, column=0, sticky=tk.NSEW)
        if detail:
            ttk.Label(cell, text=detail, anchor=tk.CENTER, foreground=MUTED,
                      font=("Microsoft YaHei UI", 8)).grid(row=2, column=0, sticky=tk.EW)

    def _render_year(self):
        year = self.anchor.year
        self.title.set(f"{year} 年")
        monthly = {month: [] for month in range(1, 13)}
        for row in self.rows:
            try:
                day = date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if day.year == year and row["status"] == "recorded":
                monthly[day.month].append(row)
        for month in range(1, 13):
            rows = monthly[month]
            item = ({"pnl": sum(r["pnl"] for r in rows), "source": "broker"
                     if all(r["source"] == "broker" for r in rows) else "local", "status": "recorded"}
                    if rows else None)
            cell = ttk.Frame(self.grid)
            cell.grid(row=(month - 1) // 4, column=(month - 1) % 4,
                      sticky=tk.NSEW, padx=5, pady=5)
            cell.columnconfigure(0, weight=1)
            cell.rowconfigure(1, weight=1)
            self._cell(cell, f"{month} 月 · {len(rows)} 天", item)
        for index in range(4):
            self.grid.columnconfigure(index, weight=1)
        for index in range(3):
            self.grid.rowconfigure(index, weight=1)
        self.summary.set("按已记录的日盈亏汇总；未补齐月份不代表完整月收益。")


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


class _CashFlowDialog(_BaseDialog):
    CATEGORIES = ("利息归本", "现金分红", "红利税调整", "转入", "转出", "其他收入", "其他支出")

    def __init__(self, parent, row=None):
        super().__init__(parent, "编辑资金流水" if row else "新增资金流水")
        row = row or {}
        ttk.Label(self.form, text="日期").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.flow_date = tk.StringVar(value=row.get("flow_date") or date.today().isoformat())
        ttk.Entry(self.form, textvariable=self.flow_date, width=18).grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 12), pady=4)
        ttk.Label(self.form, text="类别").grid(row=0, column=2, sticky=tk.W, padx=(0, 8), pady=4)
        self.category = tk.StringVar(value=row.get("category") or self.CATEGORIES[0])
        ttk.Combobox(self.form, textvariable=self.category, values=self.CATEGORIES,
                     state="normal", width=16).grid(row=0, column=3, sticky=tk.EW, pady=4)
        self.amount = self.entry(1, "金额（元）", row.get("amount", ""))
        self.notes = self.entry(1, "备注", row.get("notes", ""), column=1)
        ttk.Label(
            self.form,
            text="类别可自行输入；收入填正数，转出/其他支出可填正数并自动记为负数。",
            foreground=MUTED,
        ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))
        self.buttons(3, self._ok)

    def _ok(self):
        try:
            amount = float(self.amount.get())
        except ValueError:
            messagebox.showerror("输入无效", "金额必须填写有效数字。", parent=self.top)
            return
        self.values = {
            "flow_date": self.flow_date.get().strip(),
            "category": self.category.get().strip(),
            "amount": amount,
            "notes": self.notes.get().strip(),
        }
        self.confirmed = True
        self.top.destroy()


class _CashFlowManagerDialog(_BaseDialog):
    """Manage the only user-editable inputs in the account overview."""

    def __init__(self, parent, store, account_id):
        super().__init__(parent, "管理资金调整")
        self.store = store
        self.account_id = account_id
        self.rows = {}
        self.top.resizable(True, True)
        self.top.minsize(720, 430)
        self.form.columnconfigure(0, weight=1)
        self.form.rowconfigure(1, weight=1)
        ttk.Label(
            self.form,
            text="仅在这里记录交易以外的资金变化；总资产、盈亏和可用资金由系统只读计算。",
            foreground=MUTED,
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.tree = ttk.Treeview(
            self.form, columns=("date", "category", "amount", "notes"), show="headings"
        )
        for key, label, width in (
            ("date", "日期", 110), ("category", "类别", 130),
            ("amount", "金额（元）", 120), ("notes", "备注", 300),
        ):
            self.tree.heading(key, text=label, anchor=tk.CENTER)
            self.tree.column(key, width=width, anchor=tk.CENTER if key != "notes" else tk.W)
        self.tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.tree.bind("<Double-1>", lambda _event: self._edit())
        bar = ttk.Frame(self.form)
        bar.grid(row=2, column=0, sticky=tk.E, pady=(10, 0))
        ttk.Button(bar, text="新增", bootstyle="primary", command=self._add).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="编辑", bootstyle="secondary-outline", command=self._edit).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="删除", bootstyle="danger-outline", command=self._delete).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="关闭", bootstyle="secondary", command=self.top.destroy).pack(side=tk.LEFT, padx=4)
        self._reload()

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for row in self.store.list_cash_flows(self.account_id):
            iid = self.tree.insert("", tk.END, values=(
                row["flow_date"], row["category"], f"{row['amount']:+,.2f}", row["notes"],
            ))
            self.rows[iid] = row

    def _selected(self):
        selected = self.tree.selection()
        return self.rows.get(selected[0]) if selected else None

    def _save(self, row=None):
        dialog = _CashFlowDialog(self.top, row)
        self.top.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_cash_flow(
                self.account_id, **dialog.values, flow_id=(row or {}).get("id")
            )
        except Exception as exc:
            messagebox.showerror("资金调整保存失败", str(exc), parent=self.top)
            return
        self._reload()

    def _add(self):
        self._save()

    def _edit(self):
        row = self._selected()
        if row:
            self._save(row)

    def _delete(self):
        row = self._selected()
        if not row or not messagebox.askyesno(
            "删除资金调整",
            f"确认删除 {row['flow_date']} {row['category']} {row['amount']:+,.2f} 元？",
            parent=self.top,
        ):
            return
        try:
            self.store.delete_cash_flow(row["id"])
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self.top)
            return
        self._reload()


class _AccountDialog(_BaseDialog):
    def __init__(self, parent, account=None, market_value=0.0, summary=None, create_new=False):
        super().__init__(parent, "新增账户" if create_new else "账户设置")
        account = account or {}
        summary = summary or {}
        self.create_new = bool(create_new)
        self.market_value = float(market_value or 0)
        self.closed_pnl = float(summary.get("closed_pnl") or 0)
        self.floating_pnl = float(summary.get("floating_pnl") or 0)
        self.cash_adjustments = float(summary.get("cash_adjustments") or 0)
        self._implied_initial = None
        self.mode = tk.StringVar(value=account.get("accounting_mode", "snapshot"))
        ttk.Label(self.form, text="建账方式").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Radiobutton(self.form, text="当前资产快照", variable=self.mode,
                        value="snapshot", command=self._mode_changed).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(self.form, text="初始资金＋历史清仓", variable=self.mode,
                        value="history", command=self._mode_changed).grid(row=0, column=2, columnspan=2, sticky=tk.W)
        self.name = self.entry(1, "账户名称", account.get("name", "" if create_new else "主账户"))
        self.account_number = self.entry(
            1, "资金账号", account.get("broker_account_no", ""), column=1
        )
        self.initial_label = ttk.Label(self.form, text="账户资金")
        self.initial_label.grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        initial = account.get("initial_equity", 100000)
        if account.get("accounting_mode") == "snapshot" and summary.get("total_assets") is not None:
            initial = summary["total_assets"]
        self.initial = tk.StringVar(value=initial)
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
            initial, None, self.closed_pnl, self.floating_pnl, self.cash_adjustments
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
            initial, broker, self.closed_pnl, self.floating_pnl, self.cash_adjustments
        )
        self._implied_initial = result["implied_initial_equity"]
        if self._implied_initial is None:
            self.reverse_initial.set("—")
            self.reconciliation.set(
                f"已清仓 {self.closed_pnl:+,.2f} · 持仓 {self.floating_pnl:+,.2f} · "
                f"资金调整 {self.cash_adjustments:+,.2f} 元 · 未填写券商资产快照"
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
            self.hint.set("核对证券 App 当前总资产与可用资金；确认时保存现金基准，之后现金只随成交及资金调整变化。")
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
                "开户初始资金是固定基准；系统账面总资产 = 初始资金＋已清仓净盈亏＋当前持仓浮盈亏"
                "＋资金调整。券商资产快照只用于独立对账，不会自动覆盖初始资金。"
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
                "broker_account_no": self.account_number.get().strip(),
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
    def __init__(self, parent, row, dates, batches=None, inherit_first=True, include_fees=False):
        self.parent = parent
        self.start_row = row
        self.dates = list(dates)
        self.inherit_first = inherit_first
        self.include_fees = include_fees
        self.rows = []
        self._next_row = row + 1
        ttk.Label(parent, text="日期").grid(row=row, column=0, pady=(8, 2))
        ttk.Label(parent, text="价格").grid(row=row, column=1, pady=(8, 2))
        ttk.Label(parent, text="手数（100股/手）").grid(row=row, column=2, pady=(8, 2))
        if include_fees:
            ttk.Label(parent, text="税费合计").grid(row=row, column=3, pady=(8, 2))
        ttk.Button(parent, text="＋", width=3, bootstyle="primary-outline",
                   command=self.add).grid(row=row, column=4 if include_fees else 3, padx=4)
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
        fees_var = tk.StringVar(value=batch.get("fees", "") if self.include_fees else "0")
        widgets = [
            ttk.Combobox(self.parent, textvariable=date_var, values=self.dates,
                         width=13, state="readonly"),
            ttk.Entry(self.parent, textvariable=price_var, width=13),
            ttk.Entry(self.parent, textvariable=hands_var, width=15),
        ]
        if self.include_fees:
            widgets.append(ttk.Entry(self.parent, textvariable=fees_var, width=12))
        for column, widget in enumerate(widgets):
            widget.grid(row=row_number, column=column, padx=3, pady=3)
        remove = ttk.Button(self.parent, text="−", width=3, bootstyle="secondary-outline",
                            command=lambda record=None: self.remove_record(record))
        record = {"date": date_var, "price": price_var, "hands": hands_var, "fees": fees_var,
                  "widgets": [*widgets, remove]}
        remove.configure(command=lambda r=record: self.remove_record(r))
        remove.grid(row=row_number, column=4 if self.include_fees else 3, padx=4)
        self.rows.append(record)

    def remove_record(self, record):
        if len(self.rows) == 1 or record not in self.rows:
            return
        for widget in record["widgets"]:
            widget.destroy()
        self.rows.remove(record)

    def values(self):
        return [{"date": row["date"].get().strip(), "price": float(row["price"].get()),
                 "hands": int(row["hands"].get()), "fees": float(row["fees"].get() or 0)}
                for row in self.rows]


class _PositionDialog(_IdentityDialog):
    def __init__(self, parent, store, names, position=None):
        super().__init__(parent, "编辑持仓" if position else "新增持仓", store, names)
        position = position or {}
        self._editing_position = bool(position.get("id"))
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
        self.batches = _BatchEditor(
            batch_frame, 0, dates, position.get("buy_batches"), include_fees=True
        )
        self.hint = tk.StringVar(value="输入代码后可自动带入名称，并识别最新策略结果或关注信号。")
        ttk.Label(self.form, textvariable=self.hint, foreground=MUTED).grid(
            row=5, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        self.buttons(6, self._ok)
        if position.get("id"):
            self.hint.set("持仓图使用已保存的成交与风控价位；修改后请先确认。")
            ttk.Button(self.form, text="查看持仓图", bootstyle="primary-outline",
                       command=lambda: parent._open_position_chart(self.top, position)).grid(
                           row=6, column=0, columnspan=2, sticky=tk.W, pady=8)

    def _resolve_code(self):
        code = super()._resolve_code()
        if code and not self._editing_position:
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
        self.record = dict(row)
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
        self.chart_button = ttk.Button(self.form, text="查看 K 线买卖点", bootstyle="primary-outline",
                                       command=self._view_chart)
        self.chart_button.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        self.buttons(5, self._ok)

    def _view_chart(self):
        from .closed_trade_chart import ClosedTradeChartDialog
        try:
            code = self._resolve_name()
            if not code:
                raise ValueError("请填写有效的股票代码或名称。")
            row = {**self.record, "code": code, "name": self.name.get().strip(),
                   "close_date": self.close_date.get().strip(), "holding_days": int(self.days.get())}
            dialog = ClosedTradeChartDialog(self.top, self.store, row)
        except Exception as exc:
            messagebox.showerror("无法查看 K 线", str(exc), parent=self.top)
            return
        self.top.wait_window(dialog.top)
        if self.top.winfo_exists():
            self.top.grab_set()

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

    def __init__(self, parent, store, names, initial_records=None, validation_text=""):
        super().__init__(parent, "确认截图识别结果" if initial_records else "批量填写历史清仓")
        self.top.resizable(True, True)
        self.store = store
        self.names = names
        self.lookup = StockNameLookup(names)
        self.by_name = {name: code for code, name in names.items() if name}
        self.rows = []
        self._next_grid_row = 2
        self.dates = trading_dates(store)
        hint = "请逐行核对；名称会自动匹配代码，确认后才写入本地账本。"
        if validation_text:
            hint += "\n" + validation_text
        ttk.Label(self.form, text=hint, foreground=MUTED, justify=tk.LEFT,
                  wraplength=820).grid(row=0, column=0, columnspan=5, sticky=tk.W, pady=(0, 8))
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
        records = list(initial_records or [])
        if records:
            for record in records:
                self._add_row(record)
        else:
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

    def _add_row(self, values=None):
        grid_row = self._next_grid_row
        self._next_grid_row += 1
        variables = [tk.StringVar() for _item in self.COLUMNS]
        values = values or {}
        default_date = self.dates[0] if self.dates else date.today().isoformat()
        initial = (
            numeric_stock_code(values.get("code", "")), values.get("name", ""),
            values.get("close_date") or default_date, values.get("holding_days", ""),
            values.get("pnl", ""), values.get("return_pct", ""),
        )
        for variable, value in zip(variables, initial):
            variable.set(value)
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


class _ScreenshotPasteDialog(_BaseDialog):
    def __init__(self, parent, store, names):
        super().__init__(parent, "粘贴已清仓截图")
        self.top.resizable(True, True)
        self.store = store
        self.names = names
        self.images = []
        self.status = tk.StringVar(value=f"在此窗口按 Ctrl+V 粘贴截图；一次最多 {MAX_SCREENSHOTS} 张。")
        ttk.Label(self.form, textvariable=self.status, foreground=MUTED,
                  wraplength=650, justify=tk.LEFT).grid(row=0, column=0, columnspan=3,
                                                        sticky=tk.W, pady=(0, 8))
        self.listbox = tk.Listbox(
            self.form, height=8, width=72, bg=PANEL_BG, fg=TEXT,
            selectbackground="#245a92", relief=tk.FLAT,
        )
        self.listbox.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)
        self.form.rowconfigure(1, weight=1)
        self.form.columnconfigure(0, weight=1)
        ttk.Button(self.form, text="粘贴截图", bootstyle="primary-outline",
                   command=self._paste).grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        ttk.Button(self.form, text="移除选中", bootstyle="secondary-outline",
                   command=self._remove).grid(row=2, column=1, pady=(10, 0))
        self.recognize_button = ttk.Button(
            self.form, text="识别并预览", bootstyle="primary", command=self._recognize
        )
        self.recognize_button.grid(row=2, column=2, sticky=tk.E, pady=(10, 0))
        ttk.Button(self.form, text="取消", bootstyle="secondary",
                   command=self.top.destroy).grid(row=3, column=2, sticky=tk.E, pady=(8, 0))
        self.top.bind("<Control-v>", self._paste)
        self.top.bind("<Control-V>", self._paste)
        self.top.after(100, self.top.focus_force)

    def _paste(self, _event=None):
        if len(self.images) >= MAX_SCREENSHOTS:
            messagebox.showinfo("已达上限", f"一次最多粘贴 {MAX_SCREENSHOTS} 张截图。", parent=self.top)
            return "break"
        try:
            from PIL import Image, ImageGrab
            content = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("读取剪贴板失败", str(exc), parent=self.top)
            return "break"
        candidates = []
        if isinstance(content, Image.Image):
            candidates = [content.copy()]
        elif isinstance(content, list):
            for path in content:
                try:
                    candidates.append(Image.open(path).convert("RGB"))
                except Exception:
                    continue
        if not candidates:
            messagebox.showinfo("没有截图", "剪贴板中没有可识别的图片，请先在微信中复制截图。", parent=self.top)
            return "break"
        room = MAX_SCREENSHOTS - len(self.images)
        for image in candidates[:room]:
            self.images.append(image.convert("RGB"))
            self.listbox.insert(tk.END, f"截图 {len(self.images)}  ·  {image.width} × {image.height}")
        if len(candidates) > room:
            messagebox.showinfo("部分已忽略", f"已保留前 {MAX_SCREENSHOTS} 张，超出部分未加入。", parent=self.top)
        self.status.set(f"已粘贴 {len(self.images)}/{MAX_SCREENSHOTS} 张；可继续粘贴或开始识别。")
        return "break"

    def _remove(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            self.listbox.delete(index)
            self.images.pop(index)
        self.listbox.delete(0, tk.END)
        for index, image in enumerate(self.images, 1):
            self.listbox.insert(tk.END, f"截图 {index}  ·  {image.width} × {image.height}")
        self.status.set(f"已粘贴 {len(self.images)}/{MAX_SCREENSHOTS} 张。")

    def _recognize(self):
        if not self.images:
            messagebox.showinfo("请先粘贴", "请先粘贴至少一张已清仓截图。", parent=self.top)
            return
        self.recognize_button.state(["disabled"])
        self.status.set(f"正在本地识别 {len(self.images)} 张截图，请稍候……")
        images = [image.copy() for image in self.images]
        self._recognition_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                self._recognition_queue.put((recognize_screenshots(images), None))
            except Exception as exc:
                self._recognition_queue.put((None, exc))

        threading.Thread(target=worker, daemon=True).start()
        self.top.after(100, self._poll_recognition)

    def _poll_recognition(self):
        if not self.top.winfo_exists():
            return
        try:
            result, error = self._recognition_queue.get_nowait()
        except queue.Empty:
            self.top.after(100, self._poll_recognition)
            return
        self._recognition_done(result, error)

    def _recognition_done(self, result, error):
        if not self.top.winfo_exists():
            return
        self.recognize_button.state(["!disabled"])
        if error:
            self.status.set("识别失败；截图仍保留，可调整后重试。")
            messagebox.showerror("截图识别失败", str(error), parent=self.top)
            return
        records, summaries, warnings = result
        if not records:
            self.status.set("未识别出完整交易记录。")
            detail = "\n".join(warnings[:8]) or "请确认截图包含证券名称、清仓日期、持仓天数、盈亏和收益率。"
            messagebox.showwarning("没有可预览记录", detail, parent=self.top)
            return
        summary_text = f"识别 {len(records)} 笔（跨截图重复项已合并）"
        if summaries:
            recognised = {}
            for record in records:
                if record["pnl"] == "":
                    continue
                month = record["close_date"].replace("-", "")[:6]
                bucket = recognised.setdefault(month, [0, 0.0])
                bucket[0] += 1
                bucket[1] += float(record["pnl"])
            checks, seen_months = [], set()
            for item in summaries:
                if item["month"] in seen_months:
                    continue
                seen_months.add(item["month"])
                count, pnl = recognised.get(item["month"], [0, 0.0])
                if count == item["count"] and abs(pnl - item["pnl"]) <= 0.02:
                    checks.append(f"{item['month']} 月汇总一致")
                else:
                    checks.append(
                        f"{item['month']} 月截图汇总 {item['count']}笔/{item['pnl']:+,.2f}元，"
                        f"当前完整识别 {count}笔/{pnl:+,.2f}元"
                    )
            summary_text += "；" + "；".join(checks)
        incomplete = sum(
            any(record[key] == "" for key in ("holding_days", "pnl", "return_pct"))
            for record in records
        )
        if incomplete:
            summary_text += f"；{incomplete} 行存在空字段，必须在预览中补全或删除"
        dialog = _ClosedBatchDialog(
            self.top, self.store, self.names, initial_records=records,
            validation_text=summary_text,
        )
        self.top.wait_window(dialog.top)
        if not dialog.confirmed:
            self.status.set("预览已取消；截图仍保留，可重新识别。")
            return
        self.values = dialog.values
        self.confirmed = True
        self.top.destroy()


class _DailyReturnImportDialog(_BaseDialog):
    """Paste continuously; a serial background worker never touches Tk."""

    def __init__(self, parent):
        super().__init__(parent, "补录券商盈亏日历")
        self.top.resizable(True, True)
        self.top.minsize(740, 650)
        self.items = []
        self._sequence = 0
        self._running_id = None
        self._closed = False
        self._result_queue = queue.Queue()
        self.status = tk.StringVar(value="Ctrl+V 连续粘贴完整月日历截图，最多 6 张；识别在后台进行。")
        ttk.Label(self.form, textvariable=self.status, foreground=MUTED,
                  wraplength=680).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Label(self.form, text="识别完成后集中核对；确认前不保存，不改变资产或清仓记录。",
                  foreground=MUTED).grid(row=1, column=0, columnspan=3, sticky=tk.W)
        self.gallery = ttk.Frame(self.form)
        self.gallery.grid(row=2, column=0, columnspan=3, sticky=tk.NSEW, pady=10)
        self.form.rowconfigure(2, weight=1)
        self.form.columnconfigure(0, weight=1)
        self.paste_button = ttk.Button(self.form, text="粘贴截图", bootstyle="primary-outline",
                                      command=self._paste_image)
        self.paste_button.grid(row=3, column=0, sticky=tk.W)
        self.text_button = ttk.Button(self.form, text="录入文本", bootstyle="secondary-outline",
                                      command=lambda: self._review(manual=True))
        self.text_button.grid(row=4, column=0, sticky=tk.W, pady=(8, 0))
        self.save_button = ttk.Button(self.form, text="核对并导入", bootstyle="primary", command=self._review)
        self.save_button.grid(row=3, column=1, padx=8, sticky=tk.E)
        ttk.Button(self.form, text="取消", command=self.top.destroy).grid(row=3, column=2, sticky=tk.E)
        self.top.bind("<Control-v>", self._paste_image)
        self.top.bind("<Control-V>", self._paste_image)
        self.top.bind("<Destroy>", self._on_destroy, add="+")
        self._render_items()
        self.top.after(100, self._poll)

    def _on_destroy(self, event):
        if event.widget == self.top:
            self._closed = True

    def _paste_image(self, event=None):
        if len(self.items) >= MAX_SCREENSHOTS:
            self.status.set(f"已达 {MAX_SCREENSHOTS} 张上限；可移除截图后继续粘贴。")
            return "break"
        from PIL import ImageGrab

        try:
            clipboard = ImageGrab.grabclipboard()
            try:
                text = self.top.clipboard_get() if clipboard is None else ""
            except tk.TclError:
                text = ""
            images = _calendar_clipboard_images(clipboard, text, MAX_SCREENSHOTS - len(self.items))
        except Exception as exc:
            self.status.set(f"读取截图失败：{exc}")
            return "break"
        for image in images:
            self._sequence += 1
            self.items.append({"id": self._sequence, "image": image, "state": "queued",
                               "rows": [], "error": ""})
        self._start_next()
        self._render_items()
        # Always consume image paste: native Text paste must not insert paths.
        return "break"

    def _start_next(self):
        if self._closed or self._running_id is not None:
            return
        item = next((item for item in self.items if item["state"] == "queued"), None)
        if item is None:
            return
        item["state"] = "running"
        self._running_id = item["id"]
        image, identity, results = item["image"], item["id"], self._result_queue

        def worker():
            try:
                results.put((identity, recognize_calendar_screenshots([image]), None))
            except Exception as exc:
                results.put((identity, None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self._closed:
            return
        changed = False
        while True:
            try:
                identity, rows, error = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if identity == self._running_id:
                self._running_id = None
            item = next((item for item in self.items if item["id"] == identity), None)
            # A removed screenshot's late result must not reappear or be saved.
            if item is not None:
                item.update(state="error" if error else "done", rows=rows or [], error=error or "")
            changed = True
        self._start_next()
        if changed:
            self._render_items()
        self.top.after(100, self._poll)

    def _remove(self, identity):
        self.items = [item for item in self.items if item["id"] != identity]
        self._start_next()
        self._render_items()

    def _retry(self, identity):
        item = next((item for item in self.items if item["id"] == identity), None)
        if item is not None and item["state"] == "error":
            item.update(state="queued", error="", rows=[])
            self._start_next()
            self._render_items()

    def _render_items(self):
        from PIL import ImageTk

        for child in self.gallery.winfo_children():
            child.destroy()
        for column in range(3):
            self.gallery.columnconfigure(column, weight=1, uniform="screenshots", minsize=210)
        for row in range(2):
            self.gallery.rowconfigure(row, weight=1, minsize=235)
        if not self.items:
            ttk.Label(self.gallery, text="从微信复制月日历截图后，在此按 Ctrl+V\n可连续粘贴，无需等待识别。",
                      anchor=tk.CENTER, justify=tk.CENTER, foreground=MUTED).grid(
                          row=0, column=0, rowspan=2, columnspan=3, sticky=tk.NSEW)
        for index, item in enumerate(self.items):
            card = ttk.Labelframe(self.gallery, text=f"截图 {index + 1}", padding=5)
            card.grid(row=index // 3, column=index % 3, sticky=tk.NSEW, padx=4, pady=4)
            thumbnail = item["image"].copy()
            thumbnail.thumbnail((185, 165))
            item["thumbnail"] = ImageTk.PhotoImage(thumbnail, master=self.top)
            ttk.Label(card, image=item["thumbnail"], anchor=tk.CENTER).pack(fill=tk.BOTH, expand=True)
            state = item["state"]
            label = {"queued": "等待识别", "running": "正在识别…", "done": f"已识别 {len(item['rows'])} 天",
                     "error": "识别失败，请重试或移除"}[state]
            ttk.Label(card, text=label, foreground=DOWN if state == "error" else MUTED,
                      anchor=tk.CENTER).pack(fill=tk.X, pady=3)
            if state == "error":
                from ttkbootstrap.widgets import ToolTip
                ToolTip(card, text=item["error"])
            controls = ttk.Frame(card)
            controls.pack(fill=tk.X)
            ttk.Button(controls, text="移除", bootstyle="secondary-outline",
                       command=lambda identity=item["id"]: self._remove(identity)).pack(side=tk.RIGHT)
            if state == "error":
                ttk.Button(controls, text="重试", bootstyle="primary-outline",
                           command=lambda identity=item["id"]: self._retry(identity)).pack(side=tk.LEFT)
        pending = sum(item["state"] in {"queued", "running"} for item in self.items)
        failed = sum(item["state"] == "error" for item in self.items)
        done = sum(item["state"] == "done" for item in self.items)
        self.status.set(f"已粘贴 {len(self.items)}/{MAX_SCREENSHOTS} 张 · 完成 {done} · 待完成 {pending}"
                        + (f" · 失败 {failed}，请重试或移除" if failed else "；可继续粘贴。"))
        ready = bool(self.items) and not pending and not failed
        self.save_button.state(["!disabled" if ready else "disabled"])
        self.text_button.state(["disabled" if pending or failed else "!disabled"])
        self.paste_button.state(["disabled" if len(self.items) >= MAX_SCREENSHOTS else "!disabled"])

    def _review(self, manual=False):
        if any(item["state"] != "done" for item in self.items) or (not self.items and not manual):
            self.status.set("请等待全部截图识别完成，并重试或移除失败截图。")
            return
        rows = [row for item in self.items for row in item["rows"]]
        dialog = _DailyReturnReviewDialog(self.top, rows)
        self.top.wait_window(dialog.top)
        if dialog.confirmed:
            self.values = dialog.values
            self.confirmed = True
            self.top.destroy()
        elif not self._closed:
            self.top.grab_set()


def _calendar_clipboard_images(content, text="", room=MAX_SCREENSHOTS):
    """Read image bits, file-drop lists or WeChat's absolute local image path."""
    from PIL import Image

    if isinstance(content, Image.Image):
        if room < 1:
            raise ValueError("截图已达上限")
        return [content.convert("RGB")]
    paths = content if isinstance(content, list) else str(content or text).strip().splitlines()
    paths = [str(path).strip().strip('"') for path in paths if str(path).strip()]
    if not paths:
        raise ValueError("剪贴板没有图片，请先在微信复制截图")
    if len(paths) > room:
        raise ValueError(f"本次图片超出上限，还能加入 {room} 张；本次未加入，请分批复制")
    images = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}:
            raise ValueError("剪贴板不是本地图片；日盈亏文本请使用“录入文本”")
        with Image.open(path) as image:
            images.append(image.convert("RGB"))
    return images


class _DailyReturnReviewDialog(_BaseDialog):
    """Editable review is separate from the continuous image-paste surface."""

    def __init__(self, parent, rows):
        super().__init__(parent, "核对日历识别结果")
        self.top.resizable(True, True)
        ttk.Label(self.form, text="每行：日期 盈亏（或休市）。重叠日期相同值自动合并；冲突值请更正或删除。\n"
                  "例如：2026-09-30 -24.00。确认后仅补充日历，不改变资产或清仓记录。",
                  foreground=MUTED, wraplength=680).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self.editor = tk.Text(self.form, width=68, height=20, bg=PANEL_BG, fg=TEXT,
                              insertbackground=TEXT, font=("Consolas", 11), undo=True)
        self.editor.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, pady=8)
        self.form.rowconfigure(1, weight=1)
        self.form.columnconfigure(0, weight=1)
        for row in sorted(rows, key=lambda row: row["date"]):
            value = "休市" if row["status"] == "closed" else f"{row['pnl']:+.2f}"
            self.editor.insert(tk.END, f"{row['date']} {value}\n")
        ttk.Button(self.form, text="确认导入", bootstyle="primary", command=self._confirm).grid(row=2, column=0, sticky=tk.E)
        ttk.Button(self.form, text="返回截图", command=self.top.destroy).grid(row=2, column=1, padx=8)
        # Widget bindings run before Text's default paste, unlike Toplevel bindings.
        self.editor.bind("<<Paste>>", self._guard_image_paste)

    def _guard_image_paste(self, event=None):
        from PIL import Image, ImageGrab
        try:
            content = ImageGrab.grabclipboard()
            text = self.editor.clipboard_get() if content is None else ""
            path = Path(text.strip().strip('"'))
            is_image = isinstance(content, (Image.Image, list)) or (
                path.is_absolute() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"})
        except Exception:
            return None  # Plain daily-record text is still pasted normally.
        if not is_image:
            return None
        messagebox.showinfo("请返回截图列表", "请点“返回截图”继续粘贴图片；这里仅核对日期和金额。", parent=self.top)
        return "break"

    def _confirm(self):
        try:
            self.values = parse_calendar_text(self.editor.get("1.0", tk.END))
        except ValueError as exc:
            messagebox.showerror("请核对日历", str(exc), parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()
