"""工程 A K 线与量能的石墨主题呈现；仅读取新工程快照。"""
from __future__ import annotations
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


_LAYOUT_SHAPES = {1: (1, 1), 4: (2, 2), 6: (2, 3), 9: (3, 3)}


def layout_shape(count):
    """返回多图布局的行列；支持专注单图和 4/6/9 格。"""
    return _LAYOUT_SHAPES.get(int(count), _LAYOUT_SHAPES[4])


def page_start_for(index, count):
    """让指定候选落在其所在整页，避免逐股切换时整组不停抖动。"""
    count = int(count) if int(count) in _LAYOUT_SHAPES else 4
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


class ChartPanel(native_ttk.Frame):
    """一个独立图格：自身标的、活动状态以及独立 TradingView 入口。"""

    def __init__(self, parent, store=None, on_activate=None, image_cache=None, interactive=False, on_layout=None):
        _configure_chart_styles()
        super().__init__(parent, style="ChartPanel.TFrame", borderwidth=2, relief=tk.SOLID)
        self.store = store
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

    def _show_interactive(self):
        if not self._interactive or not self.store:
            return
        from .chart_embed import EmbeddedChartHost
        if not EmbeddedChartHost.available():
            self.readout_frame.grid()
            self.readout.set('交互主图不可用，当前为兼容预览；请核对 WebView2 环境')
            return
        try:
            context = self._workspace_context or self._rebuild_workspace_context()
            if self._embedded is None:
                self._embedded = EmbeddedChartHost(self.chart_frame, self.store, self._embedded_error,
                    on_layout=self._on_layout, on_ready=self.readout_frame.grid_remove, on_active=self._activate)
            self.readout_frame.grid()
            self.readout.set('正在加载交互主图；当前分析会先保存到本机')
            self._embedded.show(context)
        except Exception as exc:
            self._embedded_error(str(exc))

    def _embedded_error(self, error):
        self.readout_frame.grid()
        self.readout.set('交互主图未就绪：' + error)
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
        color = ACCENT if active else CONTROL_BG
        self.configure(
            style="Active.ChartPanel.TFrame" if active else "ChartPanel.TFrame"
        )
        self.header.configure(bg=color)
        self.title_label.configure(bg=color)
        self._tv_btn.configure(bg=color)
        self._analysis_btn.configure(bg=color)

    def set_drop_target(self, active):
        if active:
            self.configure(style="Drop.ChartPanel.TFrame")
            self.header.configure(bg=DROP_TARGET)
            self.title_label.configure(bg=DROP_TARGET)
            self._tv_btn.configure(bg=DROP_TARGET)
            self._analysis_btn.configure(bg=DROP_TARGET)

    def show_placeholder(self, title="空位"):
        self._cancel_manual()
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
        self._analysis_btn.configure(state=tk.DISABLED)
        if store is None or observation_id is None:
            self.show_placeholder()
            return
        # Keep the real Store on self for explicitly saved workspace edits,
        # but reading candles must not wait to write a "read file" DB event.
        from .background_status import display_store
        store = display_store(store)
        cache = getattr(self, "_image_cache", None)
        cache_key = (observation_id, market_dataset_id or "", mode, market_asof, anchor_asof)
        cached = cache.get(cache_key) if isinstance(cache, dict) else None
        if cached is not None:
            self._image, self._code, self._tf, title = cached
            self._title_var.set(title)
            self._tv_btn.configure(state=tk.NORMAL)
            self._analysis_btn.configure(state=tk.NORMAL)
            self._fit_image()
            self._show_interactive()
            return
        from .data import load_observation_candles, code_names
        from workbench.market import load_dataset
        from workbench.strategies import catalog, prepare, calculate
        try:
            frame, record, payload = load_observation_candles(store, observation_id)
            if frame is None:
                raise ValueError("该观察无可用行情快照")
            observation = store.rows("SELECT * FROM observations WHERE id=?", (observation_id,))[0]
            # 早期迁入 Aplus 的观察记录可能只在 observations 表保存日期，
            # payload 内没有 asof/setup_date。绘图层补齐只读上下文，保证
            # 专属标注仍锚定这笔历史信号，而不是误取窗口内另一笔信号。
            payload = dict(payload or {})
            for key in ("asof", "setup_date"):
                if observation.get(key):
                    payload.setdefault(key, observation[key])
            self._code = observation["code"]
            self._tf = observation["timeframe"]
            if mode == "watch" and market_dataset_id:
                # observation 仍是关注原因和参数来源；只把绘图行情推进到
                # 该股票在 Aplus 正式行情源中的最新不可变快照。
                frame, record = load_dataset(store, market_dataset_id)
            spec = catalog(store)[observation["strategy"]]
            # 候选严格停在信号日；关注标的随正式行情向前推进。
            cutoff = (
                (market_asof or (record or {}).get("end"))
                if mode == "watch" and market_dataset_id
                else observation["asof"]
            )
            bars = prepare(frame, self._tf, str(cutoff)[:10])
            instance, calculated = calculate(spec, bars)
            from workbench.h2_plan import display_plan, is_h2, STATE_LABELS
            h2 = spec.id == 'STRATEGY_GAP_H2' and is_h2(instance.get_metadata())
            if h2:
                payload = display_plan(spec, bars, payload, self._code,
                                       observation.get('setup_date') or observation['asof'], mode)
            name = code_names(store).get(self._code, "")
            period = "周K" if self._tf == "weekly" else "日K"
            # 股票、策略和周期合并到标题条，图内不再占一行标题，把空间留给 K 线。
            from .chart_embed import EmbeddedChartHost
            interactive_ready = getattr(self, '_interactive', False) is True and EmbeddedChartHost.available()
            self._image = None if interactive_ready else render_chart(
                calculated,
                payload,
                "",
                instance.get_metadata(),
                strategy=instance,
                strategy_type=observation["strategy"],
                code=self._code,
                instrument_type=bars.attrs.get('instrument_type'),
            )
            self._workspace_context = dict(
                frame=calculated, record={**(record or {}), 'timeframe': self._tf}, code=self._code,
                name=name, timeframe=self._tf, payload=payload, metadata=instance.get_metadata(),
                strategy=instance, strategy_type=observation['strategy'],
                instrument_type=bars.attrs.get('instrument_type'), title='', context_kind=mode,
            )
            chart_identity = f"{self._code}  {name}".rstrip()
            if mode == "watch":
                current_day = str(bars.date.iloc[-1])[:10]
                signal_day = str(anchor_asof or observation["asof"])[:10]
                title = (
                    f"{chart_identity}  ·  {spec.name} · {period}"
                    f" · 行情 {current_day} · 信号 {signal_day}"
                )
                if h2:
                    signal_day = payload.get('h2_setup_date', signal_day)
                    title = (f"{chart_identity}  ·  {spec.name} · {period}"
                             f" · 行情 {current_day} · 机会起始 {signal_day}")
                    title += (f" · 明日计划基于{payload['plan_asof']}收盘"
                              if payload.get('pending_state') == 'PENDING'
                              else f" · {STATE_LABELS[payload['pending_state']]}")
                else:
                    title += " · 计划线来自信号日"
            else:
                title = f"{chart_identity}  ·  {spec.name} · {period}"
            self._title_var.set(title)
            if isinstance(cache, dict):
                cache[cache_key] = (
                    self._image,
                    self._code,
                    self._tf,
                    self._title_var.get(),
                )
                if len(cache) > 72:
                    cache.pop(next(iter(cache)))
            self._tv_btn.configure(state=tk.NORMAL)
            self._analysis_btn.configure(state=tk.NORMAL)
            self._fit_image()
            self._show_interactive()
        except Exception as exc:
            self._code = None
            self._image = self._photo = None
            self._title_var.set("加载失败")
            self.chart_label.configure(image="", text=f"图表加载失败：{exc}")
            self._analysis_btn.configure(state=tk.DISABLED)

    def _cancel_manual(self):
        controller = getattr(self, '_manual_controller', None)
        if controller is not None:
            controller.close()
            self._manual_controller = None
        if hasattr(self, '_history_btn'):
            self._history_btn.pack_forget()

    def _destroy_manual(self, event):
        if event.widget is self:
            self._cancel_manual()

    def _refresh_manual(self):
        if self._manual_controller is not None:
            self._manual_controller.refresh()

    def show_manual(self, store, item):
        self.show_placeholder(item.name or item.code)
        self.store, self._code, self._tf = store, item.code, item.timeframe or self._tf
        self._title_var.set(f'{item.code} {item.name} · 手动关注 · K图准备中…')
        self.chart_label.configure(text='正在读取本地K图；没有缓存时仅获取这一只标的…')
        self._tv_btn.configure(state=tk.NORMAL)
        self._history_btn.pack(side=tk.RIGHT, fill=tk.Y)
        from .watch_chart import WatchChartController
        self._manual_controller = WatchChartController(self, item)

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
        from .data import load_observation_candles, code_names
        from workbench.market import load_dataset
        from workbench.strategies import catalog, prepare, calculate
        request = self._workspace_request
        frame, record, payload = load_observation_candles(self.store, request['observation_id'])
        if frame is None:
            raise ValueError('该观察无可用行情快照')
        observation = self.store.rows('SELECT * FROM observations WHERE id=?',
                                      (request['observation_id'],))[0]
        payload = dict(payload or {})
        for key in ('asof','setup_date'):
            if observation.get(key): payload.setdefault(key,observation[key])
        if request['mode']=='watch' and request['market_dataset_id']:
            frame,record=load_dataset(self.store,request['market_dataset_id'])
        cutoff=((request['market_asof'] or (record or {}).get('end'))
                if request['mode']=='watch' and request['market_dataset_id'] else observation['asof'])
        spec=catalog(self.store)[observation['strategy']]
        bars=prepare(frame,observation['timeframe'],str(cutoff)[:10])
        instance,calculated=calculate(spec,bars)
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
                 on_active_item=None, interactive=False, on_layout_request=None):
        super().__init__(parent)
        self.store = store
        self._interactive = interactive
        self._slot_pool = []
        self._layout_count = layout_count if layout_count in _LAYOUT_SHAPES else 4
        self._on_page_state = on_page_state
        self._on_active_item = on_active_item
        self._on_layout_request = on_layout_request
        self._items = []
        self._display_items = []
        self._page_start = 0
        self._active_slot = 0
        self._render_token = 0
        self._image_cache = {}
        self._slots = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        _configure_chart_styles()
        self.host = native_ttk.Frame(self, style="ChartHost.TFrame")
        self.host.grid(row=0, column=0, sticky=tk.NSEW)
        self._build_slots()

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
        if count not in _LAYOUT_SHAPES or count == self._layout_count:
            return
        active_index = self._page_start + self._active_slot
        self._layout_count = count
        self._page_start = page_start_for(active_index, count)
        self._active_slot = max(0, active_index - self._page_start)
        self._build_slots()
        self._render_page()

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
        self._show_item(self._slots[slot_index], item)
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
        rows, columns = layout_shape(self._layout_count)
        for row in range(3):
            self.host.rowconfigure(row, weight=1 if row < rows else 0)
        for column in range(3):
            self.host.columnconfigure(column, weight=1 if column < columns else 0)
        for index in range(self._layout_count):
            row, column = divmod(index, columns)
            if index < len(self._slot_pool):
                slot = self._slot_pool[index]
            else:
                slot = ChartPanel(self.host, self.store,
                    on_activate=lambda i=index: self._activate_slot(i),
                    image_cache=self._image_cache, interactive=self._interactive,
                    on_layout=self._on_layout_request or self.set_layout)
                self._slot_pool.append(slot)
            slot.grid(row=row, column=column, sticky=tk.NSEW, padx=3, pady=3)
            self._slots.append(slot)

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

    def _render_slot(self, index, item, token):
        if token != self._render_token or not self.winfo_exists():
            return
        if index >= len(self._display_items) or self._display_items[index] is not item:
            return
        self._show_item(self._slots[index], item)

    def _show_item(self, slot, item):
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
        item = self.current_item()
        # Every pointerdown inside the embedded WebView reports its active tile.
        # Re-selecting the already active sidebar row would steal native focus
        # from the chart and create a visible jump on every trading action.
        if item and self._on_active_item:
            self._on_active_item(('watch:' + item.code) if item.manual else item.observation_id)

    def _refresh_active_styles(self):
        for index, slot in enumerate(self._slots):
            slot.set_active(index == self._active_slot)

    def _notify_page_state(self):
        if not self._on_page_state:
            return
        total = len(self._items)
        start = self._page_start + 1 if total else 0
        end = min(self._page_start + self._layout_count, total)
        self._on_page_state(start, end, total)
