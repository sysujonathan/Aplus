"""中央 K 线区域（第二批：Canvas 手绘真实蜡烛图，零依赖）。

读取 store 中某观察对应的行情快照，手绘蜡烛图（涨红 #dc6a5e / 跌绿 #24877d，
配色与 Web 端 candles() 一致），叠加 entry(蓝)/stop(红)/target(绿) 水平线。
右侧价轴 + 底部日期轴。无 matplotlib 依赖，像素可控，便于截图级还原。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_UP = "#dc6a5e"       # 涨 红
_DOWN = "#24877d"     # 跌 绿
_GRID = "#2a3443"
_TEXT = "#dce3ef"
_ENTRY = "#487cb6"
_STOP = "#bd594f"
_TARGET = "#298975"
_SIGNAL = "#c9a227"   # 信号日竖线（金黄，与三线区分）
_BG = "#171e28"
_TITLE_FG = "#f5f5f7"


class ChartPanel(ttk.Frame):
    def __init__(self, parent, store=None):
        super().__init__(parent)
        self.store = store
        self._data = None  # (frame, payload)
        self._code = None
        self._signal_date = None  # P3：信号日（observations.asof 前 10 位），画竖线用

        self._tf = "daily"
        self._title = ttk.Label(
            self, text="K 线区域", font=("Microsoft YaHei", 14, "bold"), foreground=_TITLE_FG
        )
        self._title.pack(anchor=tk.W, padx=8, pady=(4, 8))

        self._canvas = tk.Canvas(self, background=_BG, highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", lambda e: self._redraw())

        self._tv_btn = ttk.Button(
            self, text="在 TradingView 打开", command=self._open_tv, state=tk.DISABLED
        )
        self._tv_btn.pack(anchor=tk.E, padx=8, pady=(6, 8), ipady=6)

    def set_timeframe(self, tf):
        self._tf = tf or "daily"

    # ---- 展示某观察 ----
    def show_observation(self, store, observation_id):
        self.store = store
        self._code = None
        self._signal_date = None
        if store is None or observation_id is None:
            self._data = None
            self._title.config(text="K 线区域（未选择标的）")
            self._tv_btn.config(state=tk.DISABLED)
            self._redraw()
            return

        from .data import load_observation_candles

        frame, record, payload = load_observation_candles(store, observation_id)
        if frame is None:
            self._data = None
            self._title.config(text="该观察无行情快照")
            self._tv_btn.config(state=tk.DISABLED)
            self._redraw()
            return

        self._code = record.get("code") if record else None
        self._data = (frame.tail(120).copy(), payload or {})

        obs = store.rows(
            "SELECT code, strategy, timeframe, asof FROM observations WHERE id=?",
            (observation_id,),
        )
        if obs:
            o = obs[0]
            self._title.config(
                text=f"{o['code']} · {o['strategy']} · {o['timeframe']} · 信号 {o['asof']}"
            )
            self._code = o["code"]
            self._tf = o["timeframe"] or self._tf
            self._signal_date = (o["asof"] or "")[:10]
        self._tv_btn.config(state=tk.NORMAL)
        self._redraw()

    def _open_tv(self):
        if not self._code:
            return
        try:
            from .tv import tv_link
            import webbrowser

            webbrowser.open(tv_link(self._code, self._tf))
        except Exception:
            pass

    # ---- 绘制 ----
    def _redraw(self):
        canvas = self._canvas
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if self._data is None:
            if w > 10 and h > 10:
                canvas.create_text(
                    w // 2,
                    h // 2,
                    text="点击左侧候选或右侧关注查看 K 线",
                    fill="#8e8e93",
                    font=("Microsoft YaHei", 11),
                )
            return
        if w < 50 or h < 50:
            return
        frame, payload = self._data
        self._draw_candles(canvas, w, h, frame, payload)

    def _draw_candles(self, canvas, w, h, frame, payload):
        try:
            dates = [str(d) for d in frame["date"].tolist()]
            opens = frame["open"].astype(float).to_numpy()
            highs = frame["high"].astype(float).to_numpy()
            lows = frame["low"].astype(float).to_numpy()
            closes = frame["close"].astype(float).to_numpy()
        except Exception:
            canvas.create_text(w // 2, h // 2, text="行情解析失败", fill=_STOP, font=("Consolas", 12))
            return

        n = len(closes)
        pad_l, pad_r, pad_t, pad_b = 8, 66, 10, 22  # 右侧留价轴
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 0 or plot_h <= 0 or n == 0:
            return

        # 价格范围（含 entry/stop/target）
        lines = [v for v in (payload.get("entry"), payload.get("stop"), payload.get("target")) if v]
        lo = min(float(lows.min()), min(lines)) if lines else float(lows.min())
        hi = max(float(highs.max()), max(lines)) if lines else float(highs.max())
        if hi == lo:
            hi += 1.0
            lo -= 1.0
        span = hi - lo
        lo -= span * 0.05
        hi += span * 0.05

        def y(p):
            return pad_t + (hi - p) / (hi - lo) * plot_h

        def x(i):
            return pad_l + (i + 0.5) / n * plot_w

        # 边框 + 网格 + 价轴
        canvas.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=_GRID)
        for i in range(5):
            yy = pad_t + i / 4 * plot_h
            price = hi - i / 4 * (hi - lo)
            canvas.create_line(pad_l, yy, pad_l + plot_w, yy, fill=_GRID)
            canvas.create_text(
                pad_l + plot_w + 4, yy, text=f"{price:.2f}", fill=_TEXT, anchor=tk.W,
                font=("Consolas", 9),
            )

        # 蜡烛
        cw = max(1.0, plot_w / n * 0.7)
        for i in range(n):
            color = _UP if closes[i] >= opens[i] else _DOWN
            cx = x(i)
            canvas.create_line(cx, y(highs[i]), cx, y(lows[i]), fill=color, width=1)
            yo, yc = y(opens[i]), y(closes[i])
            top, bot = min(yo, yc), max(yo, yc)
            if bot - top < 1:
                bot = top + 1
            canvas.create_rectangle(cx - cw / 2, top, cx + cw / 2, bot, fill=color, outline=color)

        # entry / stop / target
        for value, color in (
            (payload.get("entry"), _ENTRY),
            (payload.get("stop"), _STOP),
            (payload.get("target"), _TARGET),
        ):
            if value and value > 0:
                yy = y(float(value))
                canvas.create_line(pad_l, yy, pad_l + plot_w, yy, fill=color, dash=(4, 3), width=1)
                canvas.create_text(
                    pad_l + plot_w + 4, yy, text=f"{float(value):.2f}", fill=color,
                    anchor=tk.W, font=("Consolas", 9),
                )

        # P3：信号日竖线（金黄虚线，与三线区分）
        if self._signal_date:
            for i, d in enumerate(dates):
                if d[:10] == self._signal_date:
                    xx = x(i)
                    canvas.create_line(
                        xx, pad_t, xx, pad_t + plot_h, fill=_SIGNAL, dash=(2, 2), width=1
                    )
                    canvas.create_text(
                        xx + 3, pad_t + 2, text="信号", fill=_SIGNAL,
                        anchor=tk.NW, font=("Microsoft YaHei", 8),
                    )
                    break

        # P3：信号说明标签（图内左上角，与价轴同色系）
        tx = pad_l + 6
        for name, key, color in (
            ("入场", "entry", _ENTRY), ("止损", "stop", _STOP), ("目标", "target", _TARGET),
        ):
            value = payload.get(key)
            if value and float(value) > 0:
                canvas.create_text(
                    tx, pad_t + 20, text=f"{name} {float(value):.2f}", fill=color,
                    anchor=tk.W, font=("Consolas", 9),
                )
                tx += 78

        # 日期轴（稀疏标注）
        step = max(1, n // 6)
        for i in range(0, n, step):
            canvas.create_text(
                x(i), pad_t + plot_h + 10, text=str(dates[i])[-5:], fill=_TEXT,
                font=("Consolas", 8), anchor=tk.N,
            )
