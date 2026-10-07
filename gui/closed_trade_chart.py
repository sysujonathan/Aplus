"""Closed-trade chart viewer using the existing research renderer/navigation."""
import tkinter as tk

import ttkbootstrap as ttk
from PIL import ImageTk

from workbench.closed_chart import load_closed_chart
from .chart_renderer import render_chart
from .replay_navigation import ReplayNavigation
from .theme import APP_BG, CHART_BG, MUTED, TEXT


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
    return render_chart(
        data["frame"], {}, f'{data["code"]} {data["name"]} · 买卖点（事后查看）', {},
        replay_events=marks, size=size, code=data["code"],
        view_bars=bars or 120, view_offset=offset, price_scale=price_scale,
    )


class ClosedTradeChartDialog:
    def __init__(self, parent, store, row):
        # Fail before creating a modal window, so the caller can show an error.
        self.data = load_closed_chart(store, row)
        self.top = tk.Toplevel(parent)
        self.top.title(f'K 线买卖点 · {self.data["name"]} {self.data["code"]}')
        self.top.configure(bg=APP_BG)
        # Windows transient/tool dialogs suppress the title-bar maximize box.
        # Keep a normal resizable window; grab_set still protects the editor.
        self.top.resizable(True, True)
        self.top.geometry(f'{min(1200, self.top.winfo_screenwidth()-80)}x'
                          f'{min(800, self.top.winfo_screenheight()-100)}')
        self.top.minsize(720, 480)
        self.top.grab_set()
        self._after = None
        self._photo = None
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.top.bind("<Destroy>", self._destroyed, add="+")
        host = ttk.Frame(self.top, padding=10)
        host.pack(fill=tk.BOTH, expand=True)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(3, weight=1)
        toolbar = ttk.Frame(host)
        toolbar.grid(row=0, column=0, sticky=tk.EW)
        caption = "历史记录推算 · 仅作位置参考" if self.data["estimated"] else "原始成交链路 · 全部买卖批次"
        ttk.Label(toolbar, text=caption).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="关闭", command=self.close, bootstyle="secondary").pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="重置视图", command=self.reset_view,
                   bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=8)
        snapshot = self.data["dataset"]
        warnings = self.data["warnings"][:3]
        if len(self.data["warnings"]) > 3:
            warnings = [*warnings, f'另有 {len(self.data["warnings"])-3} 项缺少行情提示；原始日期见下方成交列表。']
        explanation = (f'{snapshot["source"]} · {snapshot["adjustment"]} · '
                       f'{snapshot["start"]}～{snapshot["end"]} · 快照 {snapshot["id"][:12]}\n'
                       + "\n".join(warnings))
        note = ttk.Label(host, text=explanation, foreground=MUTED, justify=tk.LEFT)
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
        end = min(len(frame), int(frame.date.le(row["close_date"]).sum()) + 12)
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
        self.navigation.reset(False)
        self._set_initial_view()
        self.schedule()
        return "break"

    def _locate(self, _event=None):
        selected = self.events_tree.selection()
        if not selected:
            return
        day = self.data["events"][int(selected[0])]["date"]
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
            image = render_closed_chart(self.data, size=size, viewport=self.navigation.viewport)
            self._photo = ImageTk.PhotoImage(image, master=self.top)
            self.chart.configure(image=self._photo, text="")
            self.navigation.accept(image)
        except Exception as exc:
            self.chart.configure(image="", text=f"K 线绘制失败：{exc}")

    def _destroyed(self, event):
        if event.widget is self.top and self._after:
            self.top.after_cancel(self._after)
            self._after = None

    def close(self):
        self.top.destroy()
