"""工程 A 白底 K 线与量能；仅读取新工程快照。"""
from __future__ import annotations
import io
import tkinter as tk
import ttkbootstrap as ttk
from PIL import Image, ImageTk


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
    colors = mpf.make_marketcolors(up="red", down="green", edge="inherit", wick="inherit", volume="in")
    style = mpf.make_mpf_style(marketcolors=colors, gridstyle=":", y_on_right=True,
                              rc={"font.family": ["Microsoft YaHei", "DejaVu Sans"], "axes.unicode_minus": False})
    adds = []
    if "ema20" in plot:
        adds.append(mpf.make_addplot(plot.ema20, color="orange", width=1.5))
    signal_column = meta.get("signal_column")
    has_marks = False
    if signal_column in plot:
        marks = plot.low.where(plot[signal_column].fillna(False).astype(bool)) * .98
        if marks.notna().any():
            adds.append(mpf.make_addplot(marks, type="scatter", marker="*", markersize=150, color="red"))
            has_marks = True
    lines, line_colors, styles = [], [], []
    for key, color, dash in (("stop", "#2962FF", "-."), ("target", "red", "--")):
        if payload.get(key) is not None and float(payload[key]) > 0:
            lines.append(float(payload[key]))
            line_colors.append(color)
            styles.append(dash)
    kwargs = {}
    if adds:
        kwargs["addplot"] = adds
    if lines:
        kwargs["hlines"] = dict(hlines=lines, colors=line_colors, linestyle=styles, linewidths=1)
    fig = None
    try:
        fig, axes = mpf.plot(plot, type="candle", style=style, volume=True, title=title,
                            ylabel="", figsize=(11, 8), returnfig=True, **kwargs)
        ax = axes[0]
        facts = []
        for label, key in (("Entry", "entry"), ("SL", "stop"), ("TP1", "target")):
            if payload.get(key) is not None:
                facts.append(f"{label}: {float(payload[key]):.2f}")
        if facts:
            ax.text(.02, .965, "\n".join(facts), transform=ax.transAxes, fontsize=9, va="top",
                    bbox=dict(boxstyle="round", facecolor="white", alpha=.86, edgecolor="gray"))
        for key, label, color in (("stop", "SL", "#2962FF"), ("target", "TP1", "red")):
            if payload.get(key) is not None and float(payload[key]) > 0:
                value = float(payload[key])
                ax.text(.99, value, f"{label}: {value:.2f}", transform=ax.get_yaxis_transform(),
                        ha="right", va="bottom", color=color, fontsize=8)
        if has_marks:
            ax.legend(handles=[Line2D([0], [0], marker="*", color="w", label="Entry",
                                     markerfacecolor="red", markersize=12)], loc="lower left")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor="white")
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


class ChartPanel(ttk.Frame):
    """一个独立图格：自身标的、活动状态以及独立 TradingView 入口。"""

    def __init__(self, parent, store=None, on_activate=None, image_cache=None):
        super().__init__(parent)
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

        self.header = tk.Frame(self, bg="#3a3a3c", height=28)
        self.header.grid(row=0, column=0, sticky=tk.EW)
        self.header.grid_propagate(False)
        self._title_var = tk.StringVar(value="空位")
        self.title_label = tk.Label(
            self.header,
            textvariable=self._title_var,
            bg="#3a3a3c",
            fg="#f5f5f7",
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
            bg="#3a3a3c",
            fg="#d1d1d6",
            activebackground="#007AFF",
            activeforeground="white",
            borderwidth=0,
            padx=8,
            cursor="hand2",
        )
        self._tv_btn.pack(side=tk.RIGHT, fill=tk.Y)

        self.chart_frame = ttk.Frame(self)
        self.chart_frame.grid(row=1, column=0, sticky=tk.NSEW)
        self.chart_frame.grid_propagate(False)
        self.chart_frame.columnconfigure(0, weight=1)
        self.chart_frame.rowconfigure(0, weight=1)
        self.chart_label = ttk.Label(
            self.chart_frame,
            text="等待候选",
            foreground="#8e8e93",
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
        color = "#007AFF" if active else "#3a3a3c"
        self.header.configure(bg=color)
        self.title_label.configure(bg=color)
        self._tv_btn.configure(bg=color)

    def set_drop_target(self, active):
        if active:
            self.header.configure(bg="#bf6b00")
            self.title_label.configure(bg="#bf6b00")
            self._tv_btn.configure(bg="#bf6b00")

    def show_placeholder(self, title="空位"):
        self._code = None
        self._observation_id = None
        self._image = None
        self._photo = None
        self._title_var.set(title)
        self.chart_label.configure(image="", text="拖入关注标的或切换候选页")
        self._tv_btn.configure(state=tk.DISABLED)

    def show_observation(self, store, observation_id):
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
        cached = cache.get(observation_id) if isinstance(cache, dict) else None
        if cached is not None:
            self._image, self._code, self._tf, title = cached
            self._title_var.set(title)
            self._tv_btn.configure(state=tk.NORMAL)
            self._fit_image()
            return
        from .data import load_observation_candles, code_names
        from workbench.strategies import catalog, prepare, calculate
        try:
            frame, record, payload = load_observation_candles(store, observation_id)
            if frame is None:
                raise ValueError("该观察无可用行情快照")
            observation = store.rows("SELECT * FROM observations WHERE id=?", (observation_id,))[0]
            self._code = observation["code"]
            self._tf = observation["timeframe"]
            spec = catalog(store)[observation["strategy"]]
            # Historical selection and weekly views must not draw later/daily bars.
            bars = prepare(frame, self._tf, str(observation["asof"])[:10])
            instance, calculated = calculate(spec, bars)
            name = code_names(store).get(self._code, "")
            period = "周K" if self._tf == "weekly" else "日K"
            # 股票身份固定在深色图格标题条；白底图内只保留策略与周期，避免九格时重复挤占空间。
            title = f"{spec.name} · {period}"
            self._image = render_chart(calculated, payload, title, instance.get_metadata())
            self._title_var.set(f"{self._code}  {name}")
            if isinstance(cache, dict):
                cache[observation_id] = (
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
        self.host = ttk.Frame(self)
        self.host.grid(row=0, column=0, sticky=tk.NSEW)
        self._build_slots()

    @property
    def layout_count(self):
        return self._layout_count

    def set_items(self, rows, selected_index=0):
        self._items = []
        for index, row in enumerate(rows or []):
            item = dict(row)
            item["candidate_index"] = index
            self._items.append(item)
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
            self._on_active_item(item.get("observation_id"))

    def focus_observation(self, observation_id):
        if not observation_id:
            return False
        for index, item in enumerate(self._items):
            if item.get("observation_id") == observation_id:
                start = page_start_for(index, self._layout_count)
                if start != self._page_start:
                    self._page_start = start
                    self._active_slot = index - start
                    self._render_page()
                else:
                    self._active_slot = index - start
                    displayed = self._display_items[self._active_slot]
                    if not displayed or displayed.get("observation_id") != observation_id:
                        base_item = self._items[index]
                        self._display_items[self._active_slot] = base_item
                        self._slots[self._active_slot].show_observation(
                            self.store, observation_id
                        )
                    self._refresh_active_styles()
                    self._notify_page_state()
                return True
        return False

    def replace_active(self, code, observation_id):
        return self.replace_slot(self._active_slot, code, observation_id)

    def replace_slot(self, slot_index, code, observation_id):
        if not observation_id or not 0 <= int(slot_index) < len(self._slots):
            return False
        slot_index = int(slot_index)
        item = {
            "code": code,
            "name": "",
            "observation_id": observation_id,
            "candidate_index": None,
            "watch_override": True,
        }
        while len(self._display_items) < len(self._slots):
            self._display_items.append(None)
        self._display_items[slot_index] = item
        self._active_slot = slot_index
        self._slots[slot_index].show_observation(self.store, observation_id)
        self._refresh_active_styles()
        return True

    def replace_at_point(self, code, observation_id, root_x, root_y):
        index = self.slot_at_point(root_x, root_y)
        if index is None:
            return False
        return self.replace_slot(index, code, observation_id)

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
            slot.grid(row=row, column=column, sticky=tk.NSEW, padx=2, pady=2)
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
                label = item.get("name") or item.get("code") or "加载中"
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
        self._slots[index].show_observation(self.store, item.get("observation_id"))

    def _activate_slot(self, index):
        self._active_slot = index
        self._refresh_active_styles()
        item = self.current_item()
        if item and self._on_active_item:
            self._on_active_item(item.get("observation_id"))

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
