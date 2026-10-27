"""Verified-session date picker: a calendar where only real trading days click.

The date selectors offer ten years of sessions, which is too many to scroll. This
popup keeps the whitelist intact: days that are closed or not covered by the
verified calendar are shown greyed out and cannot be chosen, so a click can never
produce a date the ledger should not accept.
"""
import calendar
import tkinter as tk
from datetime import date

import ttkbootstrap as ttk

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
EMPTY_HINT = "当前没有可用的交易日：交易日历未覆盖，或尚未同步。"


def month_index(dates):
    """{year: {month: {day, ...}}} for the verified sessions, newest year first."""
    index = {}
    for day in dates:
        try:
            year, month, number = (int(part) for part in str(day).split("-"))
            date(year, month, number)  # rejects 2026-13, 2025-02-30, trailing text
        except (TypeError, ValueError):
            continue
        index.setdefault(year, {}).setdefault(month, set()).add(number)
    return {year: dict(months) for year, months in index.items()}


def available_days(index, year, month):
    """Day numbers selectable in that month, empty when the month has no sessions."""
    return set(index.get(int(year), {}).get(int(month), ()))


class DatePicker(ttk.Toplevel):
    """Modal calendar over verified sessions; ``on_choose`` gets a YYYY-MM-DD."""

    def __init__(self, parent, dates, current="", on_choose=None, title="选择日期"):
        super().__init__(parent)
        self.index = month_index(dates)
        self.on_choose = on_choose
        self.current = str(current or "")
        self.day_buttons = {}
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill=tk.BOTH, expand=True)
        self._place_near(parent)
        if not self.index:
            ttk.Label(self.body, text=EMPTY_HINT, wraplength=240).grid(
                row=0, column=0, columnspan=3, pady=8, sticky=tk.W)
            ttk.Button(self.body, text="关闭", bootstyle="secondary",
                       command=self.destroy).grid(row=1, column=2, sticky=tk.E)
        else:
            self._build(current)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.grab_set()

    # -- construction -----------------------------------------------------
    def _place_near(self, parent):
        try:
            x = parent.winfo_rootx()
            y = parent.winfo_rooty() + parent.winfo_height() + 4
        except tk.TclError:
            return
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build(self, current):
        self.years = sorted(self.index, reverse=True)
        self.year_var = tk.StringVar(value=str(self._start_year(current)))
        self.month_var = tk.StringVar(value=str(self._start_month(current)))
        header = ttk.Frame(self.body)
        header.grid(row=0, column=0, columnspan=7, sticky=tk.EW, pady=(0, 6))
        self.year_box = ttk.Combobox(header, textvariable=self.year_var, width=6,
                                     state="readonly", values=self.years)
        self.year_box.pack(side=tk.LEFT)
        self.month_box = ttk.Combobox(header, textvariable=self.month_var, width=4,
                                      state="readonly", values=self._months())
        self.month_box.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(header, text="✕", width=3, bootstyle="secondary-outline",
                   command=self.destroy).pack(side=tk.RIGHT)
        for column, name in enumerate(WEEKDAYS):
            ttk.Label(self.body, text=name, anchor=tk.CENTER, width=3).grid(
                row=1, column=column, padx=1, pady=(0, 2))
        self.grid_holder = ttk.Frame(self.body)
        self.grid_holder.grid(row=2, column=0, columnspan=7)
        ttk.Label(self.body, text="灰色为休市或未验证，不可选。", foreground="#888888").grid(
            row=3, column=0, columnspan=7, sticky=tk.W, pady=(8, 0))
        self.year_box.bind("<<ComboboxSelected>>", lambda _event: self._on_period_change())
        self.month_box.bind("<<ComboboxSelected>>", lambda _event: self._render_days())
        self._render_days()

    def _start_year(self, current):
        try:
            year = int(str(current)[:4])
            if year in self.index:
                return year
        except (TypeError, ValueError):
            pass
        return self.years[0]

    def _start_month(self, current):
        months = self.index[self._start_year(current)]
        try:
            month = int(str(current)[5:7])
            if month in months:
                return month
        except (TypeError, ValueError):
            pass
        return max(months)

    def _months(self):
        return sorted(self.index[int(self.year_var.get())])

    def _on_period_change(self):
        self.month_box.configure(values=self._months())
        if int(self.month_var.get()) not in self.index[int(self.year_var.get())]:
            self.month_var.set(str(max(self._months())))
        self._render_days()

    def _render_days(self):
        for widget in self.grid_holder.winfo_children():
            widget.destroy()
        self.day_buttons.clear()
        year, month = int(self.year_var.get()), int(self.month_var.get())
        verified = available_days(self.index, year, month)
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
        for row, week in enumerate(weeks):
            for column, number in enumerate(week):
                if not number:
                    continue
                chosen = self.current == f"{year:04d}-{month:02d}-{number:02d}"
                button = ttk.Button(self.grid_holder, text=str(number), width=3,
                                    bootstyle="info" if chosen else
                                    "primary" if number in verified else "secondary-outline")
                button.grid(row=row, column=column, padx=1, pady=1)
                if number in verified:
                    button.configure(command=lambda n=number: self._choose(year, month, n))
                    self.day_buttons[number] = button
                else:
                    button.state(["disabled"])

    # -- selection --------------------------------------------------------
    def _choose(self, year, month, number):
        chosen = f"{year:04d}-{month:02d}-{number:02d}"
        if self.on_choose is not None:
            self.on_choose(chosen)
        self.destroy()


def pick_date(parent, dates, current="", on_choose=None, title="选择日期"):
    """Open the picker above ``parent``; returns the dialog for tests."""
    return DatePicker(parent, dates, current, on_choose, title)
