"""工程 A K 线与量能的石墨主题呈现；仅读取新工程快照。"""
from __future__ import annotations
import io
import tkinter as tk
from tkinter import ttk as native_ttk
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from .chart_items import ChartItem
from .theme import (
    ACCENT,
    APP_BG,
    AVERAGE,
    BORDER,
    CHART_BG,
    CONTROL_BG,
    DOWN,
    DROP_TARGET,
    GRID,
    MUTED,
    STOP,
    TARGET,
    TEXT,
    UP,
)


def render_chart(frame, payload, title, meta):
    """Render only supplied, as-of-filtered strategy output; never reads old A."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    import mplfinance as mpf
    import pandas as pd

    plot = frame.tail(120).copy()
    plot.index = pd.to_datetime(plot["date"])
    colors = mpf.make_marketcolors(
        up=UP,
        down=DOWN,
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    style = mpf.make_mpf_style(
        marketcolors=colors,
        facecolor=CHART_BG,
        figcolor=CHART_BG,
        gridcolor=GRID,
        gridstyle="-",
        y_on_right=True,
        rc={
            "font.family": ["Microsoft YaHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": MUTED,
            "text.color": TEXT,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
        },
    )
    adds = []
    if "ema20" in plot:
        adds.append(mpf.make_addplot(plot.ema20, color=AVERAGE, width=1.35))
    signal_column = meta.get("signal_column")
    has_marks = False
    if signal_column in plot:
        marks = plot.low.where(plot[signal_column].fillna(False).astype(bool)) * .98
        if marks.notna().any():
            adds.append(
                mpf.make_addplot(
                    marks, type="scatter", marker="*", markersize=95, color=TARGET
                )
            )
            has_marks = True
    lines, line_colors, styles = [], [], []
    for key, color, dash in (("stop", STOP, "-."), ("target", TARGET, "--")):
        if payload.get(key) is not None and float(payload[key]) > 0:
            lines.append(float(payload[key]))
            line_colors.append(color)
            styles.append(dash)
    kwargs = {}
    if adds:
        kwargs["addplot"] = adds
    if lines:
        kwargs["hlines"] = dict(hlines=lines, colors=line_colors, linestyle=styles, linewidths=1)
    # 远离当前价格区间的止损/目标仍显示在参数框中，但不再把整段 K 线
    # 压缩到图角。接近当前行情的价位线会纳入可视范围。
    price_low = float(plot["low"].min())
    price_high = float(plot["high"].max())
    price_span = max(price_high - price_low, abs(price_high) * .02, .01)
    view_low = price_low - price_span * .06
    view_high = price_high + price_span * .06
    for level in lines:
        if price_low - price_span * .18 <= level <= price_high + price_span * .18:
            view_low = min(view_low, level - price_span * .02)
            view_high = max(view_high, level + price_span * .02)
    kwargs["ylim"] = (view_low, view_high)
    fig = None
    try:
        fig, axes = mpf.plot(
            plot,
            type="candle",
            style=style,
            volume=True,
            title=title,
            ylabel="",
            figsize=(12.2, 7.5),
            tight_layout=True,
            returnfig=True,
            **kwargs,
        )
        ax = axes[0]
        for axis in axes:
            axis.set_facecolor(CHART_BG)
            axis.tick_params(colors=MUTED, labelsize=8)
            for spine in axis.spines.values():
                spine.set_color(BORDER)
        facts = []
        for label, key in (("Entry", "entry"), ("SL", "stop"), ("TP1", "target")):
            if payload.get(key) is not None:
                facts.append(f"{label}: {float(payload[key]):.2f}")
        if facts:
            ax.text(
                .02,
                .965,
                "\n".join(facts),
                transform=ax.transAxes,
                fontsize=8,
                color=TEXT,
                va="top",
                bbox=dict(
                    boxstyle="round,pad=.28",
                    facecolor=CONTROL_BG,
                    alpha=.94,
                    edgecolor=BORDER,
                ),
            )
        for key, label, color in (("stop", "SL", STOP), ("target", "TP1", TARGET)):
            if payload.get(key) is not None and float(payload[key]) > 0:
                value = float(payload[key])
                if value > view_high:
                    ax.text(
                        .99, .985, f"{label}↑ {value:.2f}", transform=ax.transAxes,
                        ha="right", va="top", color=color, fontsize=8,
                    )
                elif value < view_low:
                    ax.text(
                        .99, .015, f"{label}↓ {value:.2f}", transform=ax.transAxes,
                        ha="right", va="bottom", color=color, fontsize=8,
                    )
                else:
                    ax.text(
                        .99, value, f"{label}: {value:.2f}",
                        transform=ax.get_yaxis_transform(), ha="right", va="bottom",
                        color=color, fontsize=8,
                    )
        if has_marks:
            legend = ax.legend(
                handles=[
                    Line2D(
                        [0],
                        [0],
                        marker="*",
                        color=CHART_BG,
                        label="Entry",
                        markerfacecolor=TARGET,
                        markersize=9,
                    )
                ],
                loc="lower left",
                framealpha=.9,
                fontsize=8,
            )
            legend.get_frame().set_facecolor(CONTROL_BG)
            legend.get_frame().set_edgecolor(BORDER)
            for label in legend.get_texts():
                label.set_color(TEXT)
        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=110,
            bbox_inches="tight",
            pad_inches=.02,
            facecolor=CHART_BG,
        )
        buf.seek(0)
        return Image.open(buf).copy()
    finally:
        if fig is not None:
            plt.close(fig)


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

    def __init__(self, parent, store=None, on_activate=None, image_cache=None):
        _configure_chart_styles()
        super().__init__(parent, style="ChartPanel.TFrame", borderwidth=2, relief=tk.SOLID)
        self.store = store
        self._on_activate = on_activate
        self._image_cache = image_cache if image_cache is not None else {}
        self._code = None
        self._observation_id = None
        self._tf = "daily"
        self._image = None
        self._photo = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

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

        self.chart_frame = native_ttk.Frame(self, style="ChartBody.TFrame")
        self.chart_frame.grid(row=1, column=0, sticky=tk.NSEW)
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
        self.chart_frame.bind("<Configure>", lambda _event: self._fit_image())
        for widget in (self, self.header, self.title_label, self.chart_label):
            widget.bind("<Button-1>", self._activate, add="+")

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

    def set_drop_target(self, active):
        if active:
            self.configure(style="Drop.ChartPanel.TFrame")
            self.header.configure(bg=DROP_TARGET)
            self.title_label.configure(bg=DROP_TARGET)
            self._tv_btn.configure(bg=DROP_TARGET)

    def show_placeholder(self, title="空位"):
        self._code = None
        self._observation_id = None
        self._image = None
        self._photo = None
        self._title_var.set(title)
        self.chart_label.configure(image="", text="拖入关注标的或切换候选页")
        self._tv_btn.configure(state=tk.DISABLED)

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
        self.store = store
        self._code = None
        self._observation_id = observation_id
        self._image = None
        self._photo = None
        self._title_var.set("加载中…")
        self.chart_label.configure(image="", text="正在生成 K 线…")
        self._tv_btn.configure(state=tk.DISABLED)
        if store is None or observation_id is None:
            self.show_placeholder()
            return
        cache = getattr(self, "_image_cache", None)
        cache_key = (observation_id, market_dataset_id or "", mode)
        cached = cache.get(cache_key) if isinstance(cache, dict) else None
        if cached is not None:
            self._image, self._code, self._tf, title = cached
            self._title_var.set(title)
            self._tv_btn.configure(state=tk.NORMAL)
            self._fit_image()
            return
        from .data import load_observation_candles, code_names
        from workbench.market import load_dataset
        from workbench.strategies import catalog, prepare, calculate
        try:
            frame, record, payload = load_observation_candles(store, observation_id)
            if frame is None:
                raise ValueError("该观察无可用行情快照")
            observation = store.rows("SELECT * FROM observations WHERE id=?", (observation_id,))[0]
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
            name = code_names(store).get(self._code, "")
            period = "周K" if self._tf == "weekly" else "日K"
            # 股票、策略和周期合并到标题条，图内不再占一行标题，把空间留给 K 线。
            self._image = render_chart(calculated, payload, "", instance.get_metadata())
            chart_identity = f"{self._code}  {name}".rstrip()
            if mode == "watch":
                current_day = str(cutoff)[:10]
                signal_day = str(anchor_asof or observation["asof"])[:10]
                title = (
                    f"{chart_identity}  ·  {spec.name} · {period}"
                    f" · 行情 {current_day} · 信号 {signal_day}"
                    " · 计划线来自信号日"
                )
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
            self._fit_image()
        except Exception as exc:
            self._code = None
            self._title_var.set("加载失败")
            self.chart_label.configure(image="", text=f"图表加载失败：{exc}")

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

    def _open_tv(self):
        if self._code:
            import webbrowser
            from .tv import tv_link
            webbrowser.open(tv_link(self._code, self._tf))


class ChartGrid(ttk.Frame):
    """TradingView 式活动图格：候选分页，关注点击/拖拽只替换指定格。"""

    def __init__(self, parent, store=None, layout_count=4, on_page_state=None,
                 on_active_item=None):
        super().__init__(parent)
        self.store = store
        self._layout_count = layout_count if layout_count in _LAYOUT_SHAPES else 4
        self._on_page_state = on_page_state
        self._on_active_item = on_active_item
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
            self._on_active_item(item.observation_id)

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

    def replace_slot(self, slot_index, item):
        if not isinstance(item, ChartItem):
            raise TypeError("替换图格需要 ChartItem")
        if not item.observation_id or not 0 <= int(slot_index) < len(self._slots):
            return False
        slot_index = int(slot_index)
        while len(self._display_items) < len(self._slots):
            self._display_items.append(None)
        self._display_items[slot_index] = item
        self._active_slot = slot_index
        self._show_item(self._slots[slot_index], item)
        self._refresh_active_styles()
        return True

    def replace_at_point(self, item, root_x, root_y):
        index = self.slot_at_point(root_x, root_y)
        if index is None:
            return False
        return self.replace_slot(index, item)

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
        for child in self.host.winfo_children():
            child.destroy()
        self._slots = []
        rows, columns = layout_shape(self._layout_count)
        for row in range(3):
            self.host.rowconfigure(row, weight=1 if row < rows else 0)
        for column in range(3):
            self.host.columnconfigure(column, weight=1 if column < columns else 0)
        for index in range(self._layout_count):
            row, column = divmod(index, columns)
            slot = ChartPanel(
                self.host,
                self.store,
                on_activate=lambda i=index: self._activate_slot(i),
                image_cache=self._image_cache,
            )
            slot.grid(row=row, column=column, sticky=tk.NSEW, padx=3, pady=3)
            self._slots.append(slot)

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
        slot.show_observation(
            self.store,
            item.observation_id,
            market_dataset_id=item.market_dataset_id,
            mode=item.mode,
            market_asof=item.market_asof,
            anchor_asof=item.anchor_asof,
        )

    def _activate_slot(self, index):
        self._active_slot = index
        self._refresh_active_styles()
        item = self.current_item()
        if item and self._on_active_item:
            self._on_active_item(item.observation_id)

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
