"""Closed-trade chart viewer using the existing research renderer/navigation."""
import json
import tkinter as tk
import queue
import threading
import time

import ttkbootstrap as ttk
from PIL import ImageTk

from workbench.closed_chart import load_closed_chart
from workbench.holding_quotes import china_now, session_status
from .chart_renderer import render_chart
from .replay_navigation import ReplayNavigation
from .theme import APP_BG, CHART_BG, MUTED, TEXT


def closed_label_scale(size):
    """Readable small-window labels; bounded proportional growth on large displays."""
    if size is None:
        return 1.25
    return min(2.75, max(1.25, min(size[0] / 1100, size[1] / 500)))


def _rows_key(rows):
    """Identity of the last delivered bar, its live OHLCV included.

    The forming bar keeps moving while its timestamp and the row count stay the
    same, so a timestamp-only key let the poller look live while the chart kept
    an older copy of the current candle.
    """
    tail = rows[-1]
    return (str(tail["date"]), len(rows), float(tail["open"]), float(tail["high"]),
            float(tail["low"]), float(tail["close"]), float(tail["volume"]))


def render_closed_chart(data, *, size=None, viewport=(None, 0, 1.0)):
    bars, offset, price_scale = viewport
    # Same-day batches would draw labels on top of one another. The graph
    # shows one weighted position per direction; the table retains every fill.
    groups = {}
    for event in data["events"]:
        groups.setdefault((event["date"], event["kind"]), []).append(event)
    marks = []
    for group in groups.values():
        mark = dict(group[0])
        if len(group) > 1:
            mark["label"] += f'（{len(group)}笔）'
            if all(e["price"] is not None for e in group):
                total = sum(e["quantity"] or 1 for e in group)
                mark["price"] = sum(e["price"] * (e["quantity"] or 1) for e in group) / total
        marks.append(mark)
    levels = data.get("levels", ())
    caption = "持仓买点／参考水平" if data.get("holding") else "买卖点（事后查看）"
    return render_chart(
        data["frame"], {}, f'{data["code"]} {data["name"]} · {caption}', {},
        replay_events=marks, size=size, code=data["code"],
        view_bars=bars or 120, view_offset=offset, price_scale=price_scale,
        replay_label_scale=closed_label_scale(size),
        holding_levels=levels,
    )


class ClosedTradeChartDialog:
    def __init__(self, parent, store, row, *, data=None, interactive=True):
        # Fail before creating a modal window, so the caller can show an error.
        self.data = load_closed_chart(store, row) if data is None else data
        self.store = store
        self._interactive = interactive
        self._embedded = None
        self._embedded_data = None
        self._closing_chart = False
        self.top = tk.Toplevel(parent)
        self.top.title(f'K 线买卖点 · {self.data["name"]} {self.data["code"]}')
        self.top.configure(bg=APP_BG)
        # Windows transient/tool dialogs suppress the title-bar maximize box.
        # Keep a normal resizable window; grab_set still protects the editor.
        self.top.resizable(True, True)
        self.top.geometry(f'{min(1200, self.top.winfo_screenwidth()-80)}x'
                          f'{min(800, self.top.winfo_screenheight()-100)}')
        self.top.minsize(720, 480)
        if interactive and self.top.tk.call('tk', 'windowingsystem') == 'win32':
            # Keep candles usable on high-DPI displays; users can restore freely.
            self.top.state('zoomed')
        self.top.grab_set()
        self._after = None
        self._photo = None
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.top.bind("<Destroy>", self._destroyed, add="+")
        host = ttk.Frame(self.top, padding=10)
        self.host = host
        host.pack(fill=tk.BOTH, expand=True)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(3, weight=1)
        toolbar = ttk.Frame(host)
        self.toolbar = toolbar
        toolbar.grid(row=0, column=0, sticky=tk.EW)
        caption = "历史记录推算 · 仅作位置参考" if self.data["estimated"] else "原始成交链路 · 全部买卖批次"
        if self.data.get("holding"):
            caption = "持仓买点 · 当前成本／止盈止损"
        ttk.Label(toolbar, text=caption).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="关闭", command=self.close, bootstyle="secondary").pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="A++ 图表工作区", command=self.open_workspace,
                   bootstyle="primary-outline").pack(side=tk.RIGHT, padx=8)
        ttk.Button(toolbar, text="重置视图", command=self.reset_view,
                   bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=8)
        snapshot = self.data["dataset"]
        warnings = self.data["warnings"][:3]
        if len(self.data["warnings"]) > 3:
            warnings = [*warnings, f'另有 {len(self.data["warnings"])-3} 项缺少行情提示；原始日期见下方成交列表。']
        explanation = (f'{snapshot["source"]} · {snapshot["adjustment"]} · '
                       f'{snapshot["start"]}～{snapshot["end"]} · 快照 {snapshot["id"][:12]}\n'
                       + "\n".join(warnings))
        if self.data.get("levels"):
            explanation += "\n" + " · ".join(f'{level["label"]} {level["value"]:.4f}' for level in self.data["levels"])
        note = self.note = ttk.Label(host, text=explanation, foreground=MUTED, justify=tk.LEFT)
        note.grid(row=1, column=0, sticky=tk.EW, pady=(6, 4))
        note.bind("<Configure>", lambda e: note.configure(wraplength=max(1, e.width)))
        self.readout = tk.StringVar(value="滚轮缩放 · 拖动平移／价格轴 · 双击复位 · 悬停查看 OHLC")
        ttk.Label(host, textvariable=self.readout, foreground=MUTED).grid(row=2, column=0, sticky=tk.W)
        self.chart = tk.Label(host, bg=CHART_BG, fg=TEXT, text="正在绘制 K 线…")
        self.chart.grid(row=3, column=0, sticky=tk.NSEW, pady=6)
        self.navigation = ReplayNavigation(self.chart, self.schedule,
            lambda text: self.readout.set(text or "滚轮缩放 · 拖动平移／价格轴 · 双击复位 · 悬停查看 OHLC"))
        frame = self.data["frame"]
        anchor = min(e["date"] for e in self.data["events"])
        end = min(len(frame), int(frame.date.le(self.data.get("close_date", row.get("close_date", ""))).sum()) + 12)
        span = int(frame.iloc[:end].date.ge(anchor).sum()) + 35
        self._initial_viewport = (min(len(frame), max(80, span)), len(frame)-end, 1.0)
        self._set_initial_view()
        self.chart.bind("<Configure>", self.schedule)
        self.chart.bind("<Double-1>", self.reset_view)
        columns = ("date", "side", "price", "quantity", "fees")
        tree = self.events_tree = ttk.Treeview(host, columns=columns, show="headings",
                                             height=min(5, len(self.data["events"])))
        for key, heading, width in zip(columns, ("日期", "买卖", "成交价／参考收盘", "股数", "税费合计"),
                                       (130, 120, 180, 100, 100)):
            tree.heading(key, text=heading)
            tree.column(key, width=width, anchor=tk.CENTER)
        tree.grid(row=4, column=0, sticky=tk.EW)
        scroll = ttk.Scrollbar(host, command=tree.yview)
        scroll.grid(row=4, column=1, sticky=tk.NS)
        tree.configure(yscrollcommand=scroll.set)
        for i, event in enumerate(self.data["events"]):
            price = event["execution_price"] if not event["estimated"] else event["price"]
            price_text = f'{price:.4f}' + ("（参考）" if event["estimated"] else "")
            tree.insert("", tk.END, iid=str(i), values=(event["date"], event["label"], price_text,
                        event["quantity"] if event["quantity"] is not None else "未知",
                        f'{event["fees"]:.2f}' if event["fees"] is not None else "未知"))
        tree.bind("<<TreeviewSelect>>", self._locate)
        self.schedule()

    def _set_initial_view(self):
        self.navigation.bars, self.navigation.offset, self.navigation.price_scale = self._initial_viewport

    def reset_view(self, _event=None):
        if self._embedded is not None and self._embedded.navigate():
            return 'break'
        self.navigation.reset(False)
        self._set_initial_view()
        self.schedule()
        return "break"

    def _locate(self, _event=None):
        selected = self.events_tree.selection()
        if not selected:
            return
        day = self.data["events"][int(selected[0])]["date"]
        if self._embedded is not None and self._embedded.navigate(day):
            return
        frame = self.data["frame"]
        if not frame.date.eq(day).any():
            return
        bars = self.navigation.bars or 80
        end = min(len(frame), int(frame.date.le(day).sum()) + bars//2)
        self.navigation.offset = min(max(0, len(frame)-bars), len(frame)-end)
        self.schedule()

    def schedule(self, _event=None):
        if self._after:
            self.top.after_cancel(self._after)
        self._after = self.top.after(120, self.draw)

    def draw(self):
        self._after = None
        size = (self.chart.winfo_width(), self.chart.winfo_height())
        if min(size) < 100:
            return
        try:
            from .chart_embed import EmbeddedChartHost
            if self._interactive and EmbeddedChartHost.available():
                self._show_interactive()
                return
            image = render_closed_chart(self.data, size=size, viewport=self.navigation.viewport)
            self._photo = ImageTk.PhotoImage(image, master=self.top)
            self.chart.configure(image=self._photo, text="")
            self.navigation.accept(image)
            self._show_interactive()
        except Exception as exc:
            self.chart.configure(image="", text=f"K 线绘制失败：{exc}")

    def _workspace_context(self):
        return dict(frame=self.data['frame'],
            record={**self.data.get('dataset', {}), 'timeframe': 'daily'},
            code=self.data['code'], name=self.data['name'], timeframe='daily',
            holding_levels=self.data.get('levels', ()), replay_events=self.data.get('events', ()),
            context_kind='holding' if self.data.get('holding') else 'closed')

    def _show_interactive(self):
        if not self._interactive or self._embedded_data is self.data:
            return
        from .chart_embed import EmbeddedChartHost
        if not EmbeddedChartHost.available():
            self.readout.set('交互主图不可用，当前为兼容预览；请核对 WebView2 环境')
            return
        try:
            if self._embedded is None:
                self._embedded = EmbeddedChartHost(self.chart, self.store,
                    self._embedded_error)
            self._embedded.show(self._workspace_context())
            self._embedded_data = self.data
        except Exception as exc:
            self._embedded_error(str(exc))

    def _embedded_error(self, error):
        self.readout.set('交互主图未就绪：' + error + '；可重开图表重试')
        self.chart.configure(image='', text='交互图表加载失败；关闭后重开可重试。\n' + error)

    def _destroyed(self, event):
        if event.widget is self.top and self._after:
            self.top.after_cancel(self._after)
            self._after = None

    def open_workspace(self):
        from .chart_workspace import open_chart_workspace
        open_chart_workspace(self.top, self.store, **self._workspace_context(),
            title=f"A++ 图表工作区 · {self.data['name']} {self.data['code']}")

    def close(self):
        if self._closing_chart:
            return
        self._closing_chart = True
        if self._embedded is None or self._embedded.closed():
            self.top.destroy()
            return
        self._embedded.request_close()
        import time
        deadline = time.monotonic() + 15
        def check():
            if not self.top.winfo_exists():
                return
            if self._embedded.closed():
                self._embedded.cleanup()
                self.top.destroy()
            elif time.monotonic() >= deadline:
                self._closing_chart = False
                self.readout.set('图表尚未确认保存，窗口已保留；请检查后再关闭')
            else:
                self.top.after(100, check)
        check()


class HoldingTradeChartDialog(ClosedTradeChartDialog):
    """Holding chart with an optional live 5-minute reader.

    The daily frame stays the only ledger-anchored series. Intraday bars are
    transport for execution timing: they never reach Store history, strategies
    or the trade ledger, and a failed read keeps the last verified frame.
    """

    def __init__(self, parent, store, row, *, quote=None, data=None, interactive=True,
                 auto_quotes=False, minute_fetch=None, prime_minutes=True):
        from workbench.holding_chart import load_holding_chart
        from workbench.live_minutes import MinutePoller, fetch_minute_bars
        fetch = minute_fetch or fetch_minute_bars
        self._reference_cancel = threading.Event()
        self._reference_after = None
        self._reference_results = queue.Queue()
        self._auto_quotes = auto_quotes
        self._live_cancel = threading.Event()
        self._live_after = None
        self._live_due = 0.0
        self._live_sent = None
        self._live_error = ""
        self._last_live_at = ""
        self._live_poller = MinutePoller(fetch=fetch)
        self._live_primed = False
        payload = data if data is not None else load_holding_chart(store, row, quote=quote)
        # One bounded read before the window opens: the trader asked for live
        # bars, so the first frame must not wait for a later push to arrive.
        primed, prime_error = (self._prime_minutes(row["code"]) if prime_minutes
                               else (None, None))
        self._live_primed = bool(prime_minutes)
        self._live_error = prime_error or ""
        if primed is not None:
            payload = {**payload, "minute_series": primed}
            self._last_live_at = str(primed.iloc[-1]["date"])
            from workbench.live_minutes import minute_rows
            try:
                # Same row shape the poller publishes, so the first tick after
                # the prime compares like for like instead of re-sending it.
                self._live_sent = _rows_key(minute_rows(primed))
            except ValueError:
                self._live_sent = None
        super().__init__(parent, store, row, data=payload, interactive=interactive)
        self.top.title(f'持仓图 · {self.data["name"]} {self.data["code"]}')
        self.live_var = tk.StringVar(value="5 分钟行情：读取中…")
        self.live_label = ttk.Label(self.toolbar, textvariable=self.live_var, foreground=MUTED)
        self.live_label.pack(side=tk.LEFT, padx=12)
        if self._last_live_at:
            self._show_live_status(self._last_live_at)
        elif self._live_error:
            # A failed first read must not leave the toolbar saying "读取中…"
            # while the chart has already fallen back to daily bars.
            self._show_live_status()
        if self.data.get("reference_needed"):
            self._start_reference()
        self._start_live()

    # ---- live intraday reader -------------------------------------------
    def _prime_minutes(self, code, *, timeout=2.0):
        """One bounded read through the live poller, so a slow read is reused.

        Priming on a private thread used to leave the request running past the
        wait while ``_start_live`` asked for the same bars again: one window,
        two reads for one frame. Priming through the poller keeps a single read
        in flight; when it is slower than the wait, the first live tick consumes
        that same result instead of repeating the request.

        Returns ``(frame, error)``. The error is handed back instead of dropped:
        a failed first read still opens on daily bars, and the toolbar has to
        say why rather than keep claiming it is still reading.
        """
        poller = self._live_poller
        poller.start(code, attempts=1)
        deadline = time.monotonic() + max(.1, timeout)
        while time.monotonic() < deadline:
            result = poller.take()
            if result is not None:
                frame, error = result
                if frame is None or len(frame) < 2:
                    return None, error or '分钟行情为空，本次按日线显示'
                return frame, None
            time.sleep(.02)
        return None, None

    def _workspace_context(self):
        context = super()._workspace_context()
        minute = self.data.get("minute_series")
        if minute is not None:
            from workbench.live_minutes import INTERVAL
            context["minute_series"] = minute
            context["display_timeframe"] = "m5"
            context["minute_interval"] = INTERVAL
        return context

    def _auto_enabled(self):
        value = self._auto_quotes
        return bool(value() if callable(value) else value)

    def _live_calendar(self):
        try:
            return json.loads((self.store.root / "trading_calendar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            return None

    def _start_live(self):
        # Priming already asked for one frame, or left that request in flight.
        # Asking again here is what made every window pay for two reads.
        if not self._live_primed:
            self._live_poller.start(self.data["code"], attempts=2)
        self._live_due = time.monotonic() + self._live_poller.delay
        self._live_after = self.top.after(300, self._live_tick)

    def _live_tick(self):
        self._live_after = None
        if self._live_cancel.is_set() or not self.top.winfo_exists():
            return
        result = self._live_poller.take()
        if result is not None:
            frame, error = result
            if frame is not None:
                self._publish_live(frame)
            else:
                self._live_error = error or "分钟行情读取失败"
                self._show_live_status()
            self._live_due = time.monotonic() + self._live_poller.delay
        moment, calendar = china_now(), self._live_calendar()
        if self._auto_enabled() and session_status(moment, calendar) == "交易中":
            if time.monotonic() >= self._live_due and not self._live_poller.busy:
                self._live_poller.start(self.data["code"])
        self._live_after = self.top.after(400, self._live_tick)

    def _publish_live(self, frame):
        from workbench.live_minutes import minute_rows, INTERVAL
        try:
            rows = minute_rows(frame)
        except ValueError as exc:
            self._live_error = str(exc)
            self._show_live_status()
            return
        self._live_error = ""
        key = _rows_key(rows)
        embedded = self._embedded
        if key != self._live_sent and embedded is not None and not embedded.closed():
            payload = {"series": {"m5": rows},
                       "live": {"interval": INTERVAL, "source": "腾讯 5 分钟",
                                "note": "盘中实时读取；不写入正式行情、不参与策略和账本。"}}
            if embedded.push_live(payload):
                self._live_sent = key
                self._last_live_at = rows[-1]["date"]
        self._show_live_status(rows[-1]["date"])

    def _show_live_status(self, stamp=None):
        if self._live_error:
            # With no earlier frame there is nothing to keep, so say what the
            # chart actually shows instead of promising a preserved screen.
            kept = "保留上一次成功数据" if self._last_live_at else "本次按日线显示"
            self.live_var.set(f"5 分钟读取失败，{kept}：" + self._live_error[:80])
            self.live_label.configure(foreground="#f23645")
            return
        moment, calendar = china_now(), self._live_calendar()
        state = session_status(moment, calendar)
        tail = getattr(self, "_last_live_at", None) or stamp or ""
        text = f"5 分钟 · 腾讯 · 最后 {tail[5:] if tail else '等待数据'}"
        if self._auto_enabled() and state == "交易中":
            text += f" · 自动左移更新（{self._live_poller.delay}秒）"
            self.live_label.configure(foreground="#26a69a")
        elif state == "交易中":
            text += " · 自动行情未开启，仅此一次读取"
            self.live_label.configure(foreground=MUTED)
        else:
            text += f" · 自动暂停：{state}"
            self.live_label.configure(foreground=MUTED)
        self.live_var.set(text)

    def _start_reference(self):
        from workbench.holding_chart import fetch_holding_reference
        data, cancel, results = self.data, self._reference_cancel, self._reference_results
        def work():
            try:
                results.put((fetch_holding_reference(data, cancel), None))
            except Exception as exc:
                results.put((None, str(exc)))
        threading.Thread(target=work, daemon=True, name="holding-chart-reference").start()
        self._reference_after = self.top.after(100, self._poll_reference)

    def _poll_reference(self):
        self._reference_after = None
        if self._reference_cancel.is_set():
            return
        try:
            raw, error = self._reference_results.get_nowait()
        except queue.Empty:
            self._reference_after = self.top.after(100, self._poll_reference)
            return
        if error is None:
            from workbench.holding_chart import apply_holding_reference
            try:
                self.data = apply_holding_reference(self.data, raw)
            except Exception as exc:
                error = str(exc)
        warnings = [w for w in self.data["warnings"] if not w.startswith("缺少对应的不复权收盘价")]
        if error:
            warnings.insert(0, f"水平线坐标核验失败：{error}。请关闭后重试；不将原价直接画到复权坐标。")
        snapshot = self.data["dataset"]
        text = (f'{snapshot["source"]} · {snapshot["adjustment"]} · '
                f'{snapshot["start"]}～{snapshot["end"]} · 快照 {snapshot["id"][:12]}\n'
                + "\n".join(warnings[:3]) + "\n"
                + " · ".join(f'{level["label"]} {level["value"]:.4f}' for level in self.data["levels"]))
        self.note.configure(text=text)
        self.schedule()

    def _destroyed(self, event):
        if event.widget is self.top:
            self._reference_cancel.set()
            self._live_cancel.set()
            self._live_poller.invalidate()
            for attribute in ("_reference_after", "_live_after"):
                handle = getattr(self, attribute, None)
                if handle:
                    try:
                        self.top.after_cancel(handle)
                    except tk.TclError:
                        pass
                    setattr(self, attribute, None)
        super()._destroyed(event)


def fetch_missing_holding_chart_dialog(parent, store, row, *, quote=None):
    """Responsive modal fetch for a holding absent from premarket history."""
    from workbench.holding_chart import fetch_missing_holding_chart
    owner = parent.winfo_toplevel()
    top = tk.Toplevel(parent)
    top.title("正在准备持仓图")
    top.configure(bg=APP_BG)
    top.resizable(False, False)
    top.transient(owner)
    top.grab_set()
    cancel = threading.Event()
    results = queue.Queue()
    ttk.Label(top, text="本地没有该标的日K，正在按需获取持仓图行情…",
              padding=(24, 20)).pack(fill=tk.X)
    ttk.Label(top, text="不会写入或混入盘前的 BaoStock、TickFlow、腾讯行情。",
              foreground=MUTED, padding=(24, 0, 24, 14)).pack(fill=tk.X)
    ttk.Button(top, text="取消", bootstyle="secondary",
               command=lambda: (cancel.set(), top.destroy())).pack(pady=(0, 18))
    top.protocol("WM_DELETE_WINDOW", lambda: (cancel.set(), top.destroy()))

    def work():
        try:
            results.put((fetch_missing_holding_chart(store, row, cancel, quote=quote), None))
        except Exception as exc:
            results.put((None, exc))

    threading.Thread(target=work, daemon=True, name="holding-chart-history").start()
    outcome = {"data": None, "error": None}

    def poll():
        if cancel.is_set() or not top.winfo_exists():
            return
        try:
            outcome["data"], outcome["error"] = results.get_nowait()
        except queue.Empty:
            top.after(100, poll)
            return
        top.destroy()

    top.after(100, poll)
    parent.wait_window(top)
    if cancel.is_set() and outcome["data"] is None:
        return None
    if outcome["error"] is not None:
        raise ValueError("未能取得该标的持仓图日K；持仓记录不受影响。\n" + str(outcome["error"]))
    return outcome["data"]
