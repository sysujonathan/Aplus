"""工程 A K 线与量能的石墨主题呈现；仅读取新工程快照。"""
from __future__ import annotations
import time
import threading
import tkinter as tk
from tkinter import ttk as native_ttk
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from .chart_renderer import render_chart
from .chart_items import ChartItem
from .replay_navigation import candle_readout
from .theme import (
    ACCENT,
    APP_BG,
    BORDER,
    CHART_BG,
    CONTROL_BG,
    DROP_TARGET,
    MUTED,
    TEXT,
)


from workbench.workspace_layouts import SHAPES as _LAYOUT_SHAPES


# Process-wide, bounded calculation reuse. Rendering and H2 display plans stay
# mode-specific. Copies keep renderers/workspace consumers out of cached frames.
_calculation_cache = {}
_calculation_lock = threading.Lock()


def _shape_selected(panel):
    # The selection is historical even while its asynchronous boot is pending.
    request = getattr(panel, '_workspace_request', None)
    return ((getattr(panel, '_workspace_context', None) or {}).get('context_kind') == 'shape'
            or isinstance(request, dict) and request.get('shape') is not None)


def _observation_calculation(store, observation, market_dataset_id=None, market_asof=None):
    from copy import deepcopy
    from .data import load_observation_candles
    from workbench.market import load_dataset
    from workbench.strategies import catalog, prepare, calculate
    from workbench.scan_cache import file_state, engine_inputs
    from workbench.store import dumps

    spec = catalog(store)[observation['strategy']]
    did = market_dataset_id or observation.get('dataset_id')
    records = store.rows('SELECT * FROM datasets WHERE id=?', (did,)) if did else []
    record = records[0] if records else None
    cutoff = (market_asof or (record or {}).get('end')) if market_dataset_id else observation['asof']
    key = None
    if record and record.get('path'):
        key = (str(store.root), dumps(observation), dumps(record),
               file_state((store.root / record['path'],)), engine_inputs(),
               spec.id, spec.version, dumps(spec.params), spec.cls,
               file_state((spec.file,)), observation['timeframe'], str(cutoff)[:10])
    with _calculation_lock:
        if key is not None and key in _calculation_cache:
            return (*deepcopy(_calculation_cache[key]), spec, key)
    frame, original_record, payload = load_observation_candles(store, observation.get('id'))
    if frame is None:
        raise ValueError('该观察无可用行情快照')
    record = original_record
    if market_dataset_id:
        frame, record = load_dataset(store, market_dataset_id)
        cutoff = market_asof or (record or {}).get('end')
    bars = prepare(frame, observation['timeframe'], str(cutoff)[:10])
    instance, calculated = calculate(spec, bars)
    result = (record, payload, bars, instance, calculated)
    # Do not publish under an identity that changed while reading/calculating.
    if key is not None and (key[3] == file_state((store.root / record['path'],))
                            and key[4] == engine_inputs()
                            and key[9] == file_state((spec.file,))):
        with _calculation_lock:
            if len(_calculation_cache) >= 32:
                _calculation_cache.pop(next(iter(_calculation_cache)))
            _calculation_cache[key] = deepcopy(result)
    return (*result, spec, key)


def layout_shape(count):
    """返回多图布局的行列；支持专注单图和 4/6/9 格。"""
    return _LAYOUT_SHAPES.get(int(count), _LAYOUT_SHAPES[4])


def page_start_for(index, count):
    """让指定候选落在其所在整页，避免逐股切换时整组不停抖动。"""
    count = int(count) if 1 <= int(count) <= 9 else 4
    return max(0, int(index) // count * count)


def _configure_chart_styles():
    # ttkbootstrap 会把任意样式前缀解析成 Bootstyle；这里用原生 ttk
    # 注册项目局部样式，避免把 ChartCanvas 误判为不存在的控件类型。
    style = native_ttk.Style()
    for name, border in (
        ("ChartPanel.TFrame", BORDER),
        ("Active.ChartPanel.TFrame", ACCENT),
        ("Drop.ChartPanel.TFrame", DROP_TARGET),
    ):
        style.configure(
            name,
            background=CHART_BG,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
    style.configure("ChartBody.TFrame", background=CHART_BG)
    style.configure(
        "ChartBody.TLabel", background=CHART_BG, foreground=MUTED, borderwidth=0
    )
    style.configure("ChartHost.TFrame", background=APP_BG)


def should_read_intraday(*, loaded, in_session):
    """Whether this tick should ask the provider for minutes.

    The first read of a symbol happens even with the market shut: opening the
    watch list at noon or in the evening must still fill the five-minute, hourly
    and half-day units instead of leaving three dead buttons.  Only the repeat
    polling is limited to the session.
    """
    return bool(in_session or not loaded)


class ChartPanel(native_ttk.Frame):
    """一个独立图格：自身标的、活动状态以及独立 TradingView 入口。"""

    #: 休市时只做一次首帧读取；失败后隔这么久再试，避免离线时反复打网络。
    CLOSED_RETRY_SECONDS = 300
    def __init__(self, parent, store=None, on_activate=None, image_cache=None, interactive=False, on_layout=None,
                 minute_fetch=None, hourly_fetch=None, auto_quotes=None, on_cases=None):
        _configure_chart_styles()
        super().__init__(parent, style="ChartPanel.TFrame", borderwidth=2, relief=tk.SOLID)
        self.store = store
        self._on_cases = on_cases
        self._on_activate = on_activate
        self._on_layout = on_layout
        self._image_cache = image_cache if image_cache is not None else {}
        self._code = None
        self._observation_id = None
        self._tf = "daily"
        self._image = None
        self._photo = None
        self._workspace_context = None
        self._workspace_request = None
        self._embedded = None
        self._interactive = interactive
        self._manual_controller = None
        self._observation_reader = None
        self._observation_poll = None
        # Live intraday reader. Only the active tile polls, so a four-tile page
        # still costs one request per cycle instead of four.
        self._intraday_on = False
        self._intraday_poller = None
        self._intraday_source = None
        self._minute_fetch = minute_fetch
        self._hourly_fetch = hourly_fetch
        self._auto_quotes = auto_quotes
        self._intraday_attempted = False
        self._intraday_frame = None
        self._hourly = None
        self._intraday_after = None
        self._intraday_due = 0.0
        self._intraday_code = None
        self._intraday_sent = None
        self._intraday_loaded = False
        self._intraday_calendar = None
        self.bind('<Destroy>', self._destroy_manual, add='+')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.header = tk.Frame(self, bg=CONTROL_BG, height=24)
        self.header.grid(row=0, column=0, sticky=tk.EW)
        self.header.grid_propagate(False)
        self._title_var = tk.StringVar(value="空位")
        self.title_label = tk.Label(
            self.header,
            textvariable=self._title_var,
            bg=CONTROL_BG,
            fg=TEXT,
            anchor=tk.W,
            font=("Microsoft YaHei", 9, "bold"),
            padx=7,
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tv_btn = tk.Button(
            self.header,
            text="TV ↗",
            command=self._open_tv,
            state=tk.DISABLED,
            bg=CONTROL_BG,
            fg=MUTED,
            activebackground=ACCENT,
            activeforeground=TEXT,
            borderwidth=0,
            padx=8,
            cursor="hand2",
        )
        self._tv_btn.pack(side=tk.RIGHT, fill=tk.Y)
        self._analysis_btn = tk.Button(
            self.header, text="A++ 图表", command=self._open_workspace, state=tk.DISABLED,
            bg=CONTROL_BG, fg=MUTED, activebackground=ACCENT, activeforeground=TEXT,
            borderwidth=0, padx=8, cursor="hand2",
        )
        self._analysis_btn.pack(side=tk.RIGHT, fill=tk.Y)
        if on_cases is not None:
            self._cases_btn = tk.Button(self.header, text='案例搜索', command=self._open_cases, state=tk.DISABLED,
                bg=CONTROL_BG, fg=TEXT, activebackground=ACCENT, borderwidth=0, padx=8, cursor='hand2')
            self._cases_btn.pack(side=tk.RIGHT, fill=tk.Y)
        self._history_btn = tk.Button(self.header, text='更新K线', command=self._refresh_manual,
                                      bg=CONTROL_BG, fg=TEXT, borderwidth=0, padx=5, cursor='hand2')

        self.readout = tk.StringVar(value='移到 K 线上查看开、高、低、收')
        self.readout_frame = native_ttk.Frame(self, style='ChartBody.TFrame', height=42)
        self.readout_frame.grid(row=1, column=0, sticky=tk.EW)
        self.readout_frame.pack_propagate(False)
        readout_label = native_ttk.Label(self.readout_frame, textvariable=self.readout, style='ChartBody.TLabel',
                                        font=('Microsoft YaHei UI', -12), padding=(6,3))
        readout_label.pack(fill=tk.BOTH, expand=True)
        def size_readout(event):
            readout_label.configure(wraplength=max(1,event.width-12))
            self.readout_frame.configure(height=42 if event.width<480 else 24)
        self.readout_frame.bind('<Configure>', size_readout)
        self.chart_frame = native_ttk.Frame(self, style="ChartBody.TFrame")
        self.chart_frame.grid(row=2, column=0, sticky=tk.NSEW)
        self.chart_frame.grid_propagate(False)
        self.chart_frame.columnconfigure(0, weight=1)
        self.chart_frame.rowconfigure(0, weight=1)
        self.chart_label = native_ttk.Label(
            self.chart_frame,
            text="等待候选",
            style="ChartBody.TLabel",
            anchor=tk.CENTER,
        )
        self.chart_label.grid(row=0, column=0, sticky=tk.NSEW)
        self.chart_label.bind('<Motion>', self._hover_candle)
        self.chart_label.bind('<Leave>', lambda e: self.readout.set('移到 K 线上查看开、高、低、收'))
        self.chart_frame.bind("<Configure>", lambda _event: self._fit_image())
        for widget in (self, self.header, self.title_label, self.chart_label):
            widget.bind("<Button-1>", self._activate, add="+")
        if interactive:
            # The embedded workspace is already the complete editor. Keeping a
            # second detached-workspace action here duplicates the same chart
            # and costs scarce header space; the external TV reference remains.
            self._analysis_btn.pack_forget()
            self.readout_frame.grid_remove()

    def _show_interactive(self, *, refresh_history=False, prepared_boot=None):
        if not self._interactive or not self.store:
            return
        from .chart_embed import EmbeddedChartHost
        if not EmbeddedChartHost.available():
            self.readout_frame.grid()
            self.readout.set('交互主图不可用，当前为兼容预览；请核对 WebView2 环境')
            return
        if refresh_history and self._embedded is not None and not self._embedded.closed():
            try:
                context = self._workspace_context or self._rebuild_workspace_context()
                accepted = (self._embedded.push_history(context) if prepared_boot is None else
                            self._embedded.push_history(context, prepared_boot=prepared_boot))
                if not accepted:
                    raise ValueError('当前图表会话尚不能接收日K刷新')
                if self._embedded.ready():
                    self.readout_frame.grid_remove()
            except Exception as exc:
                self._history_refresh_result(False, str(exc))
            return  # A read-only refresh failure must not close a usable editor.
        try:
            context = self._workspace_context or self._rebuild_workspace_context()
            if self._embedded is None:
                self._embedded = EmbeddedChartHost(self.chart_frame, self.store, self._embedded_error,
                    on_layout=self._on_layout, on_ready=self._interactive_ready, on_active=self._activate,
                    on_history_result=self._history_refresh_result,
                    workspace=getattr(self, '_workspace_descriptor', None),
                    on_workspace_state=getattr(self, '_on_workspace_state', None),
                    on_workspace_shortcut=getattr(self, '_on_workspace_shortcut', None))
            self._embedded.workspace = getattr(self, '_workspace_descriptor', None)
            self.header.grid()
            self.readout_frame.grid()
            self.readout.set('正在切换图表…')
            if prepared_boot is None:
                self._embedded.show(context)
            else:
                self._embedded.show(context, prepared_boot=prepared_boot)
            # A fresh page has a new token even when the code did not change.
            # Re-send the already read minutes, including while the market is shut.
            self._intraday_sent = None
            if self._intraday_code == self._code and self._intraday_frame is not None:
                self._publish_intraday(self._intraday_frame)
        except Exception as exc:
            self._embedded_error(str(exc))

    def _interactive_ready(self):
        self.readout_frame.grid_remove()
        self._update_tile_header()

    def _update_tile_header(self):
        host=self._embedded
        if getattr(self,'_workspace_multi',False) and host and host.ready():
            self.header.grid_remove()
        else:
            self.header.grid()

    def _history_refresh_result(self, accepted, detail=''):
        if accepted:
            if self._embedded is not None and self._embedded.ready():
                self.readout_frame.grid_remove()
        else:
            self.readout_frame.grid()
            self.readout.set('日K刷新暂未显示，当前图表已保留；可稍后点“更新K线”重试。' + str(detail))

    def _embedded_error(self, error):
        self.header.grid()
        self.readout_frame.grid()
        self.readout.set('交互主图未就绪：' + error)
        # An embedded blank/stale page covers Tk's preview and error label.
        # Close through the normal save acknowledgement, never kill a page
        # that may still contain unsaved drawings.
        embedded = getattr(self, '_embedded', None)
        if embedded is not None and not embedded.closed():
            embedded.request_close()
        if self._image is None and self._workspace_context:
            try:
                context = self._workspace_context
                self._image = render_chart(context['frame'],context['payload'],'',context['metadata'],
                    strategy=context['strategy'],strategy_type=context['strategy_type'],code=context['code'],
                    instrument_type=context.get('instrument_type'))
                self._fit_image()
                self.readout.set('兼容预览：' + error)
            except Exception:
                self.chart_label.configure(text='交互主图未就绪：' + error)

    def tv_button(self, _parent=None):
        """兼容旧调用；多图模式下按钮固定在各自图格右上角。"""
        return self._tv_btn

    def set_timeframe(self, tf):
        self._tf = tf or "daily"

    def set_active(self, active):
        color = CONTROL_BG
        self.configure(
            style="Active.ChartPanel.TFrame" if active else "ChartPanel.TFrame"
        )
        self.header.configure(bg=color)
        self.title_label.configure(bg=color)
        self._tv_btn.configure(bg=color)
        self._analysis_btn.configure(bg=color)
        self._accept_intraday(bool(active) or getattr(self, '_workspace_live', False))

    # ---- live intraday on the active tile --------------------------------
    def _auto_quotes_enabled(self):
        value = getattr(self, '_auto_quotes', None)
        return True if value is None else bool(value() if callable(value) else value)

    def auto_quotes_changed(self):
        if self._intraday_poller is not None:
            self._intraday_poller.invalidate()
        self._intraday_due = 0.0

    def _accept_intraday(self, on):
        """Poll live minutes only while this tile is the one being read.

        A four-tile page would otherwise pay four provider requests per cycle
        for charts nobody is looking at. Switching tiles invalidates the old
        reader, so a late answer for the previous symbol is discarded instead
        of being drawn on the new one.
        """
        if _shape_selected(self):
            on = False
        if not on:
            self._intraday_on = False
            if self._intraday_poller is not None:
                self._intraday_poller.invalidate()
            return
        if not self._interactive or not self.store:
            return
        self._intraday_on = True
        if self._intraday_poller is None:
            from workbench.live_minutes import HourlyHistory, IntradaySource, MinutePoller
            self._intraday_source = self._minute_fetch or IntradaySource()
            fetch = self._hourly_fetch or self._minute_fetch
            self._hourly = HourlyHistory(fetch=fetch) if fetch is not None else HourlyHistory()
            self._intraday_poller = MinutePoller(fetch=self._intraday_source)
        if self._intraday_after is None:
            self._intraday_after = self.after(600, self._intraday_tick)

    def _intraday_calendar_state(self):
        if self._intraday_calendar is None:
            try:
                import json
                self._intraday_calendar = json.loads(
                    (self.store.root / "trading_calendar.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, AttributeError):
                self._intraday_calendar = False
        return self._intraday_calendar or None

    def _intraday_tick(self):
        self._intraday_after = None
        if _shape_selected(self):
            return  # Saved dated research must not silently advance to live bars.
        if not self._intraday_on or not self.winfo_exists():
            return
        poller = self._intraday_poller
        if self._code != self._intraday_code and poller is not None:
            poller.invalidate()
            self._intraday_code, self._intraday_sent = self._code, None
            self._intraday_loaded = False
            self._intraday_attempted = False
            self._intraday_frame = None
            self._intraday_due = 0.0
        result = poller.take() if poller is not None else None
        if result is not None and result[0] is not None:
            self._publish_intraday(result[0])
        from workbench.holding_quotes import china_now, session_status
        if self._code and poller is not None:
            from workbench.live_minutes import minute_refresh_phase
            moment = china_now()
            calendar = self._intraday_calendar_state()
            phase = minute_refresh_phase(moment, calendar)
            previous_phase = getattr(self, '_intraday_phase', None)
            self._intraday_phase = phase
            if previous_phase is not None and phase != previous_phase:
                self._intraday_loaded = False
                self._intraday_due = 0.0  # Read the last AM/PM bar before pausing.
            in_session = session_status(moment, calendar) == "交易中"
            # The first read for a symbol happens even with the market shut: a
            # trader opening the watch list at noon or in the evening must still
            # get the available minute periods. Only repeat polling is limited
            # to the session.
            auto = self._auto_quotes_enabled()
            read = ((not getattr(self, '_intraday_attempted', False)) or
                    (auto and should_read_intraday(loaded=self._intraday_loaded, in_session=in_session)))
            if read \
                    and time.monotonic() >= self._intraday_due:
                # start() rejects an in-flight read of this generation, but a
                # slow response for the previous symbol must not block this one.
                if poller.start(self._code):
                    self._intraday_attempted = True
                    self._intraday_due = time.monotonic() + (
                        poller.delay if in_session else self.CLOSED_RETRY_SECONDS)
        if self._hourly is not None and self._code in self._hourly.take(successful_only=True):
            # The hourly history arrived after the first frame was drawn.
            self._intraday_sent = None
            if self._intraday_frame is not None:
                self._publish_intraday(self._intraday_frame)
        self._intraday_after = self.after(500, self._intraday_tick)

    def _publish_intraday(self, frame):
        # Both same-symbol cache replay and late poller/hourly replies enter
        # here. A dated shape session must never receive any minute transport.
        if _shape_selected(self):
            return
        from workbench.live_minutes import (chart_minute_series, expected_minute_close,
                                            live_metadata, minute_rows)
        from workbench.holding_quotes import china_now
        embedded = self._embedded
        if embedded is None or embedded.closed():
            return
        try:
            rows = minute_rows(frame)
        except ValueError:
            return
        tail = rows[-1]
        self._intraday_frame = frame
        metadata = live_metadata(frame)
        key = (tail['date'], len(rows), tail['open'], tail['high'], tail['low'], tail['close'], tail['volume'],
               str(metadata.get('quote')), frame.attrs.get('m1'))
        if key == self._intraday_sent:
            return
        if self._hourly is not None:
            self._hourly.request(self._code)
        history = self._hourly.history(self._code) if self._hourly is not None else None
        series = chart_minute_series(frame, history)
        payload = {"series": series,"live": metadata}
        if embedded.push_live(payload):
            self._intraday_sent = key
            expected = expected_minute_close(china_now())
            self._intraday_loaded = not expected or tail['date'] >= expected

    def set_drop_target(self, active):
        if active:
            self.configure(style="Drop.ChartPanel.TFrame")
            self.header.configure(bg=DROP_TARGET)
            self.title_label.configure(bg=DROP_TARGET)
            self._tv_btn.configure(bg=DROP_TARGET)
            self._analysis_btn.configure(bg=DROP_TARGET)

    def show_placeholder(self, title="空位"):
        self._cancel_manual()
        self.header.grid()
        self.readout.set('移到 K 线上查看开、高、低、收')
        self._code = None
        self._observation_id = None
        self._image = None
        self._photo = None
        self._workspace_context = None
        self._workspace_request = None
        self._title_var.set(title)
        self.chart_label.configure(image="", text="拖入关注标的或切换候选页")
        self._tv_btn.configure(state=tk.DISABLED)
        if hasattr(self, '_cases_btn'): self._cases_btn.configure(state=tk.DISABLED)
        self._analysis_btn.configure(state=tk.DISABLED)
        if self._embedded is not None and not self._embedded.closed():
            self._embedded.show(None)

    def suspend_interactive(self):
        """Release a hidden layout tile after its final acknowledged save."""
        self._cancel_manual()
        if self._embedded is not None and not self._embedded.closed():
            self._embedded.request_close()

    def show_observation(
        self,
        store,
        observation_id,
        *,
        market_dataset_id=None,
        mode="candidate",
        market_asof=None,
        anchor_asof=None,
    ):
        self._cancel_manual()
        self.store = store
        self._workspace_request = dict(observation_id=observation_id,
            market_dataset_id=market_dataset_id, mode=mode, market_asof=market_asof,
            anchor_asof=anchor_asof)
        self._workspace_context = None
        self._code = None
        self._observation_id = observation_id
        self._image = None
        self._photo = None
        self._title_var.set("加载中…")
        if hasattr(self, 'readout'):
            self.readout.set('移到 K 线上查看开、高、低、收')
        self.chart_label.configure(image="", text="正在生成 K 线…")
        self._tv_btn.configure(state=tk.DISABLED)
        if hasattr(self, '_cases_btn'): self._cases_btn.configure(state=tk.DISABLED)
        self._analysis_btn.configure(state=tk.DISABLED)
        if store is None or observation_id is None:
            self.show_placeholder()
            return
        from .observation_chart import load_observation_chart
        from .chart_embed import EmbeddedChartHost
        interactive = self._interactive is True and EmbeddedChartHost.available()
        options = dict(interactive=interactive, workspace=getattr(self, '_workspace_descriptor', None))
        request = (store, dict(self._workspace_request), options)
        ChartPanel._prepare_selection(self, load_observation_chart, request)

    def _prepare_selection(self, loader, request):
        if self._interactive is True:
            from .background_status import LatestRefresh
            if self._observation_reader is None:
                self._observation_reader = LatestRefresh(
                    lambda value, check: value[0](*value[1:], check=check))
            self._observation_reader.submit((loader, *request))
            self._observation_poll = self.after(30, self._poll_observation)
        else:
            try:
                ChartPanel._apply_observation(self, loader(*request))
            except Exception as exc:
                ChartPanel._observation_error(self, exc)

    def _poll_observation(self):
        self._observation_poll = None
        reply = self._observation_reader.poll()
        if reply is None:
            self._observation_poll = self.after(30, self._poll_observation)
            return
        _, _, result, error = reply
        if error:
            self._observation_error(error)
        else:
            try:
                self._apply_observation(result)
            except Exception as exc:
                self._observation_error(exc)

    def _apply_observation(self, result):
        context = result['context']
        self._code, self._tf = context['code'], context['timeframe']
        self._image = result['image']
        self._workspace_context = context
        if context.get('context_kind') == 'shape':
            self._accept_intraday(False)
        self._title_var.set(result['title'])
        self._tv_btn.configure(state=tk.NORMAL)
        if hasattr(self, '_cases_btn'): self._cases_btn.configure(state=tk.NORMAL)
        self._analysis_btn.configure(state=tk.NORMAL)
        self._fit_image()
        if result['boot'] is not None:
            self._show_interactive(prepared_boot=result['boot'])
        else:
            self._show_interactive()

    def _observation_error(self, error):
        self._code = None
        self._image = self._photo = None
        self._title_var.set('加载失败')
        self.chart_label.configure(image='', text=f'图表加载失败：{error}')
        self._analysis_btn.configure(state=tk.DISABLED)

    def _cancel_manual(self):
        reader = getattr(self, '_observation_reader', None)
        if reader is not None:
            reader.invalidate()
        poll = getattr(self, '_observation_poll', None)
        if poll is not None:
            self.after_cancel(poll)
            self._observation_poll = None
        controller = getattr(self, '_manual_controller', None)
        if controller is not None:
            controller.close()
            self._manual_controller = None
        if hasattr(self, '_history_btn'):
            self._history_btn.pack_forget()

    def _destroy_manual(self, event):
        if event.widget is self:
            self._cancel_manual()
            if self._observation_reader is not None:
                self._observation_reader.close()

    def _refresh_manual(self):
        self._intraday_attempted = False
        self._intraday_due = 0.0
        if self._manual_controller is not None:
            self._manual_controller.refresh()

    def show_manual(self, store, item):
        self.show_placeholder(item.name or item.code)
        self.store, self._code, self._tf = store, item.code, item.timeframe or self._tf
        self._title_var.set(f'{item.code} {item.name} · 手动关注 · K图准备中…')
        self.chart_label.configure(text='正在读取本地K图；没有缓存时仅获取这一只标的…')
        self._tv_btn.configure(state=tk.NORMAL)
        if hasattr(self, '_cases_btn'): self._cases_btn.configure(state=tk.NORMAL)
        self._history_btn.pack(side=tk.RIGHT, fill=tk.Y)
        from .watch_chart import WatchChartController
        self._manual_controller = WatchChartController(self, item)

    def show_shape(self, store, item):
        """Read the saved candidate prefix, never infer a formal strategy signal."""
        from .observation_chart import load_shape_chart
        from .chart_embed import EmbeddedChartHost
        self.show_placeholder(item.name or item.code)
        self._history_btn.pack_forget()
        self.store, self._code, self._tf = store, item.code, 'daily'
        self._workspace_request = {'shape': item.shape}
        self._accept_intraday(False)
        self._observation_id = item.observation_id
        options = dict(interactive=self._interactive is True and EmbeddedChartHost.available(),
                       workspace=getattr(self, '_workspace_descriptor', None))
        self._prepare_selection(load_shape_chart, (store, item, options))

    def _open_cases(self):
        self._activate()
        if self._code and self._on_cases:
            self._on_cases()

    def _activate(self, _event=None):
        if self._on_activate:
            self._on_activate()

    def _fit_image(self):
        if self._image is None:
            return
        w, h = self.chart_frame.winfo_width(), self.chart_frame.winfo_height()
        if min(w, h) < 10:
            return
        scale = min(w / self._image.width, h / self._image.height)
        size = (max(1, int(self._image.width * scale)), max(1, int(self._image.height * scale)))
        resized = self._image.resize(size, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized, master=self)
        self.chart_label.configure(image=self._photo, text="")
        self.readout.set('移到 K 线上查看开、高、低、收')

    def _hover_candle(self, event):
        text = ''
        if self._image is not None and self._photo is not None:
            # The fitted image is centered, with possible letterboxing. Convert
            # both resize and margins back to original PNG coordinates.
            w, h = self._photo.width(), self._photo.height()
            left = (self.chart_label.winfo_width()-w)/2
            top = (self.chart_label.winfo_height()-h)/2
            if left <= event.x < left+w and top <= event.y < top+h:
                x, y = (event.x-left)*self._image.width/w, (event.y-top)*self._image.height/h
                text = candle_readout(self._image.info.get('replay_view', {}), x, y)
        self.readout.set(text or '移到 K 线上查看开、高、低、收')

    def _open_tv(self):
        if self._code:
            import webbrowser
            from .tv import tv_link
            webbrowser.open(tv_link(self._code, self._tf))

    def _open_workspace(self):
        if not self._workspace_request or not self.store:
            return
        try:
            context = self._workspace_context or self._rebuild_workspace_context()
            from .chart_workspace import open_chart_workspace
            open_chart_workspace(self, self.store, **context)
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror('图表工作区无法打开', str(exc), parent=self)

    def _rebuild_workspace_context(self):
        """Rebuild context when the lightweight image cache was used."""
        from .data import code_names
        from .background_status import display_store
        store = display_store(self.store)
        request = self._workspace_request
        observation = store.rows('SELECT * FROM observations WHERE id=?',
                                 (request['observation_id'],))[0]
        observation = {**observation, 'id': request['observation_id']}
        record, payload, bars, instance, calculated, spec, _ = _observation_calculation(
            store, observation, request['market_dataset_id'] if request['mode'] == 'watch' else None,
            request['market_asof'])
        payload = dict(payload or {})
        for key in ('asof', 'setup_date'):
            if observation.get(key): payload.setdefault(key, observation[key])
        from workbench.h2_plan import display_plan, is_h2
        if spec.id == 'STRATEGY_GAP_H2' and is_h2(instance.get_metadata()):
            payload = display_plan(
                spec, bars, payload, observation['code'],
                observation.get('setup_date') or observation['asof'], request['mode'],
            )
        context=dict(frame=calculated,record={**(record or {}),'timeframe':observation['timeframe']},
            code=observation['code'],name=code_names(self.store).get(observation['code'],''),
            timeframe=observation['timeframe'],payload=payload,metadata=instance.get_metadata(),
            strategy=instance,strategy_type=observation['strategy'],
            instrument_type=bars.attrs.get('instrument_type'),title='',
            context_kind=request['mode'])
        self._workspace_context=context
        return context


class ChartGrid(ttk.Frame):
    """TradingView 式活动图格：候选分页，关注点击/拖拽只替换指定格。"""

    def __init__(self, parent, store=None, layout_count=4, on_page_state=None,
                 on_active_item=None, interactive=False, on_layout_request=None, auto_quotes=None, on_cases=None,
                 on_symbol=None,on_grid_changed=None,on_fullscreen=None,on_trade=None):
        super().__init__(parent)
        self.store = store
        self._interactive = interactive
        self._auto_quotes = auto_quotes
        self._slot_pool = []
        self._layout_count = layout_count if layout_count in _LAYOUT_SHAPES else 4
        self._shape = layout_shape(self._layout_count)
        self._on_page_state = on_page_state
        self._on_cases = on_cases
        self._on_active_item = on_active_item
        self._on_layout_request = on_layout_request
        self._on_symbol = on_symbol
        self._on_grid_changed = on_grid_changed
        self.on_fullscreen = on_fullscreen
        self.on_trade = on_trade
        self._items = []
        self._display_items = []
        self._page_start = 0
        self._active_slot = 0
        self._render_token = 0
        self._image_cache = {}
        self._slots = []
        self.columnconfigure(0, weight=0 if interactive else 1)
        self.rowconfigure(1 if interactive else 0, weight=1)
        self.columnconfigure(1 if interactive else 0, weight=1)
        _configure_chart_styles()
        self.controls = None
        if interactive:
            from .workspace_controls import WorkspaceControls
            self.controls = WorkspaceControls(self)
        self.host = native_ttk.Frame(self, style="ChartHost.TFrame")
        self.host.grid(row=1 if interactive else 0, column=1 if interactive else 0, sticky=tk.NSEW)
        self._build_slots()

    def restore_saved_layout(self):
        if self.controls:
            self.controls.restore_saved_layout()

    def command(self, kind, value=None):
        return self.controls.command(kind, value) if self.controls else False

    def open_symbol(self,code):
        if self._on_symbol:
            return self._on_symbol(code)
        return False

    def compare(self, code):
        from tkinter import messagebox
        from .data import latest_market_dataset
        from workbench.market import load_dataset
        from .chart_bridge import _bars, _resample
        item = self.current_item()
        if not item or not self.controls:
            return False
        try:
            record = latest_market_dataset(self.store, code, timeframe='daily', source=item.source)
            if not record:
                raise ValueError('本地没有该标的同来源日线；请先更新或打开该标的')
            frame, record = load_dataset(self.store, record['id'])
            return self.command('compare', dict(symbol=code, source=record['source'], adjustment=record['adjustment'],
                series={'daily':_bars(frame), 'weekly':_bars(_resample(frame,'W-FRI')), 'monthly':_bars(_resample(frame,'ME'))}))
        except Exception as exc:
            messagebox.showinfo('暂不能比较',str(exc),parent=self)
            return False

    @property
    def layout_count(self):
        return self._layout_count

    def set_items(self, rows, selected_index=0):
        values = list(rows or [])
        if any(not isinstance(item, ChartItem) for item in values):
            raise TypeError("ChartGrid 只接受 ChartItem，调用方必须明确数据来源和模式")
        self._items = values
        if self._items:
            selected_index = max(0, min(int(selected_index), len(self._items) - 1))
            self._page_start = page_start_for(selected_index, self._layout_count)
            self._active_slot = selected_index - self._page_start
        else:
            self._page_start = 0
            self._active_slot = 0
        self._render_page()

    def set_layout(self, count):
        count = int(count)
        if count not in _LAYOUT_SHAPES or _LAYOUT_SHAPES[count] == self._shape:
            return
        self.set_grid(*_LAYOUT_SHAPES[count])

    def set_grid(self, rows, columns, *, render=True):
        if not 1 <= rows <= 3 or not 1 <= columns <= 4 or rows * columns > 9:
            raise ValueError('布局最多九格')
        active_index = self._page_start + self._active_slot
        count = rows * columns
        self._shape = (rows, columns)
        self._layout_count = count
        self._page_start = active_index // count * count
        self._active_slot = max(0, active_index - self._page_start)
        self._build_slots()
        if render:
            self._render_page()
        if self.controls:
            self.controls.drawing_tools.place_favorites()
        if self._on_grid_changed:
            self._on_grid_changed(count)

    def page(self, delta):
        if not self._items:
            return
        last_start = page_start_for(len(self._items) - 1, self._layout_count)
        new_start = self._page_start + int(delta) * self._layout_count
        self._page_start = max(0, min(new_start, last_start))
        self._active_slot = 0
        self._render_page()
        item = self.current_item()
        if item and self._on_active_item:
            self._on_active_item(('watch:' + item.code) if item.manual else item.observation_id)

    def focus_observation(self, observation_id):
        if not observation_id:
            return False
        for index, item in enumerate(self._items):
            if item.observation_id == observation_id:
                start = page_start_for(index, self._layout_count)
                if start != self._page_start:
                    self._page_start = start
                    self._active_slot = index - start
                    self._render_page()
                else:
                    self._active_slot = index - start
                    displayed = self._display_items[self._active_slot]
                    if not displayed or displayed.observation_id != observation_id:
                        base_item = self._items[index]
                        self._display_items[self._active_slot] = base_item
                        self._show_item(self._slots[self._active_slot], base_item)
                    self._refresh_active_styles()
                    self._notify_page_state()
                return True
        return False

    def replace_active(self, item):
        return self.replace_slot(self._active_slot, item)

    def replace_slot(self, slot_index, item, *, notify_active=False):
        if not isinstance(item, ChartItem):
            raise TypeError("替换图格需要 ChartItem")
        if not (item.observation_id or item.manual) or not 0 <= int(slot_index) < len(self._slots):
            return False
        slot_index = int(slot_index)
        while len(self._display_items) < len(self._slots):
            self._display_items.append(None)
        self._display_items[slot_index] = item
        self._active_slot = slot_index
        self._slots[slot_index]._workspace_restore = None
        self._show_item(self._slots[slot_index], item)
        if self.controls and self.controls.sync['symbol'].get():
            for index, slot in enumerate(self._slots):
                if index != slot_index:
                    self._display_items[index] = item
                    slot._workspace_restore = None
                    self._show_item(slot, item)
        if self.controls:
            self.controls.schedule_save()
        self._refresh_active_styles()
        if notify_active and self._on_active_item:
            self._on_active_item(('watch:' + item.code) if item.manual else item.observation_id)
        return True

    def replace_at_point(self, item, root_x, root_y):
        index = self.slot_at_point(root_x, root_y)
        if index is None:
            return False
        # A drop changes both the tile and the trader's active instrument, so
        # synchronize the sidebar now. A later click on this already-active
        # tile is intentionally ignored to keep the WebView focus stable.
        return self.replace_slot(index, item, notify_active=True)

    def highlight_drop(self, root_x, root_y):
        target = self.slot_at_point(root_x, root_y)
        self._refresh_active_styles()
        if target is not None:
            self._slots[target].set_drop_target(True)
        return target is not None

    def clear_drop_highlight(self):
        self._refresh_active_styles()

    def slot_at_point(self, root_x, root_y):
        for index, slot in enumerate(self._slots):
            x, y = slot.winfo_rootx(), slot.winfo_rooty()
            if x <= root_x < x + slot.winfo_width() and y <= root_y < y + slot.winfo_height():
                return index
        return None

    def current_item(self):
        if 0 <= self._active_slot < len(self._display_items):
            return self._display_items[self._active_slot]
        return None

    def set_timeframe(self, tf):
        for slot in self._slots:
            slot.set_timeframe(tf)

    def auto_quotes_changed(self):
        for slot in self._slot_pool:
            slot.auto_quotes_changed()

    def clear(self):
        self.set_items([])

    def _build_slots(self):
        # Only visible tiles keep a WebView process. A hidden tile first asks
        # its page to flush the Store-backed document, then releases the child.
        for index, child in enumerate(self._slot_pool):
            child.grid_remove()
            if index >= self._layout_count:
                child.suspend_interactive()
        self._slots = []
        rows, columns = self._shape
        for row in range(3):
            self.host.rowconfigure(row, weight=1 if row < rows else 0,
                                   uniform='chart-rows' if row < rows else '')
        for column in range(4):
            self.host.columnconfigure(column, weight=1 if column < columns else 0,
                                      uniform='chart-columns' if column < columns else '')
        for index in range(self._layout_count):
            row, column = divmod(index, columns)
            if index < len(self._slot_pool):
                slot = self._slot_pool[index]
            else:
                slot = ChartPanel(self.host, self.store,
                    on_activate=lambda i=index: self._activate_slot(i),
                    image_cache=self._image_cache, interactive=self._interactive,
                    on_layout=self._on_layout_request or self.set_layout,
                    auto_quotes=self._auto_quotes, on_cases=self._on_cases)
                self._slot_pool.append(slot)
            gap=1 if self._interactive else 3
            slot.grid(row=row, column=column, sticky=tk.NSEW, padx=gap, pady=gap)
            if self.controls:
                slot._workspace_multi=self._layout_count>1
                slot._update_tile_header()
                slot._workspace_descriptor = self.controls.descriptor(index)
                slot._on_workspace_state = lambda state, i=index: self.controls.state_changed(i, state)
                slot._on_workspace_shortcut = lambda action, i=index: self._workspace_shortcut(i, action)
                if slot._embedded:
                    slot._embedded.workspace = slot._workspace_descriptor
                    slot._embedded.on_workspace_state = slot._on_workspace_state
            self._slots.append(slot)

    def _workspace_shortcut(self, index, action):
        if index != self._active_slot or not self.winfo_ismapped():
            return
        if action == 'search':
            self.controls.open_quick_search()
        elif action == 'fullscreen':
            self.controls.toggle_fullscreen()
        elif action == 'leaveFullscreen':
            self.controls.set_fullscreen(False)

    def embedded_hosts(self):
        return [slot._embedded for slot in self._slot_pool if slot._embedded is not None]

    def _render_page(self):
        self._render_token += 1
        token = self._render_token
        page = self._items[self._page_start:self._page_start + self._layout_count]
        self._display_items = list(page) + [None] * (self._layout_count - len(page))
        for index, slot in enumerate(self._slots):
            item = self._display_items[index]
            if item is None:
                slot.show_placeholder()
            else:
                label = item.name or item.code or "加载中"
                slot.show_placeholder(label)
                self.after(
                    index * 12,
                    lambda i=index, value=item, generation=token: self._render_slot(
                        i, value, generation
                    ),
                )
        self._refresh_active_styles()
        self._notify_page_state()
        if self.controls:
            self.controls.schedule_save()

    def _render_slot(self, index, item, token):
        if token != self._render_token or not self.winfo_exists():
            return
        if index >= len(self._display_items) or self._display_items[index] is not item:
            return
        self._show_item(self._slots[index], item)

    def _show_item(self, slot, item):
        if item.shape:
            slot.show_shape(self.store,item)
            return
        if item.manual:
            slot.show_manual(self.store, item)
            return
        slot.show_observation(
            self.store,
            item.observation_id,
            market_dataset_id=item.market_dataset_id,
            mode=item.mode,
            market_asof=item.market_asof,
            anchor_asof=item.anchor_asof,
        )

    def _activate_slot(self, index):
        if index == self._active_slot:
            return
        self._active_slot = index
        self._refresh_active_styles()
        if self.controls:
            state = self.controls.states.get(index, {})
            from .workspace_controls import PERIOD_LABELS
            self.controls.period.set(PERIOD_LABELS.get(state.get('timeframe'), '日线'))
            self.controls.schedule_save()
        item = self.current_item()
        # Every pointerdown inside the embedded WebView reports its active tile.
        # Re-selecting the already active sidebar row would steal native focus
        # from the chart and create a visible jump on every trading action.
        if item and self._on_active_item:
            self._on_active_item(('watch:' + item.code) if item.manual else item.observation_id)

    def _refresh_active_styles(self):
        for index, slot in enumerate(self._slots):
            slot.set_active(index == self._active_slot)
        if self.controls:
            self.controls.activate()

    def _notify_page_state(self):
        if not self._on_page_state:
            return
        total = len(self._items)
        start = self._page_start + 1 if total else 0
        end = min(self._page_start + self._layout_count, total)
        self._on_page_state(start, end, total)
