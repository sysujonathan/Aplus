"""工程 A 白底 K 线、量能和下方交易详情；仅读取新工程快照。"""
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


class ChartPanel(ttk.Frame):
    def __init__(self, parent, store=None):
        super().__init__(parent)
        self.store = store
        self._code = None
        self._tf = "daily"
        self._image = None
        self._photo = None
        self._tv_btn = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.chart_frame = ttk.Frame(self)
        self.chart_frame.grid(row=0, column=0, sticky=tk.NSEW, pady=(0, 14))
        self.chart_frame.grid_propagate(False)
        self.chart_frame.columnconfigure(0, weight=1)
        self.chart_frame.rowconfigure(0, weight=1)
        self.chart_label = ttk.Label(self.chart_frame, text="选中左侧标的查看缩略图",
                                     foreground="#8e8e93", anchor=tk.W)
        self.chart_label.grid(row=0, column=0, sticky=tk.NSEW)
        self.chart_frame.bind("<Configure>", lambda e: self._fit_image())
        self._title = ttk.Label(self, text="", font=("Microsoft YaHei", 14, "bold"))
        self._title.grid(row=1, column=0, sticky=tk.W, pady=(0, 12))
        self._facts = ttk.Label(self, text="", font=("Consolas", 12), foreground="#d1d1d6", justify=tk.LEFT)
        self._facts.grid(row=2, column=0, sticky=tk.W, pady=(0, 12))

    def tv_button(self, parent):
        self._tv_btn = ttk.Button(parent, text="在 TradingView 打开", bootstyle="primary",
                                  command=self._open_tv, state=tk.DISABLED)
        return self._tv_btn

    def set_timeframe(self, tf):
        self._tf = tf or "daily"

    def show_observation(self, store, observation_id):
        self.store = store
        self._code = None
        self._image = None
        self._photo = None
        self._title.configure(text="")
        self._facts.configure(text="")
        self.chart_label.configure(image="", text="选中左侧标的查看缩略图")
        if self._tv_btn:
            self._tv_btn.configure(state=tk.DISABLED)
        if store is None or observation_id is None:
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
            title = f"{name}（{self._code}） {spec.name} · {'周K' if self._tf == 'weekly' else '日K'}"
            self._image = render_chart(calculated, payload, title, instance.get_metadata())
            self._title.configure(text=f"{spec.name}  {self._code} {name}")
            facts = []
            for label, key in (("触发价", "entry"), ("止损价", "stop"), ("目标价", "target")):
                if payload.get(key) is not None:
                    facts.append(f"{label}  {float(payload[key]):.2f}")
            facts.append(f"信号日  {str(observation['asof'])[:10]}")
            self._facts.configure(text="\n".join(facts))
            if self._tv_btn:
                self._tv_btn.configure(state=tk.NORMAL)
            self._fit_image()
        except Exception as exc:
            self._code = None
            self.chart_label.configure(image="", text=f"图表加载失败：{exc}")

    def _fit_image(self):
        if self._image is None:
            return
        w, h = self.chart_frame.winfo_width(), self.chart_frame.winfo_height()
        if min(w, h) < 10:
            return
        scale = min(w / self._image.width, h / self._image.height)
        size = (max(1, int(self._image.width * scale)), max(1, int(self._image.height * scale)))
        self._photo = ImageTk.PhotoImage(self._image.resize(size, Image.Resampling.LANCZOS), master=self)
        self.chart_label.configure(image=self._photo, text="")

    def _open_tv(self):
        if self._code:
            import webbrowser
            from .tv import tv_link
            webbrowser.open(tv_link(self._code, self._tf))
