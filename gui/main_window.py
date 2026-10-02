"""工程 A 桌面布局，使用 Aplus Service/Store 后端。"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk

from .candidate_tabs import CandidateTabs
from .chart_items import ChartItem, chart_items
from .chart_panel import ChartGrid
from .data import REALTIME_MARKET_SOURCE
from .watch_panel import WatchPanel
from .toolbar import ToolBar
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    APP_BG,
    CONTROL_BG,
    MUTED,
    PANEL_BG,
    SELECTION,
    TEXT,
)


def side_panel_widths(window_width):
    """按窗口宽度收紧两侧清单，优先把水平空间留给 K 线。"""
    width = max(1280, int(window_width or 0))
    candidates = max(238, min(260, round(width * 0.145)))
    watch = max(222, min(242, round(width * 0.115)))
    return candidates, watch


class AplusMainWindow(ttk.Window):
    def __init__(self, service=None, store=None, enable_tray=False):
        super().__init__(themename="darkly", title="Brooks-AI 操盘台")
        self.style.colors.primary = ACCENT
        self.configure(background=APP_BG)
        self.style.configure("TFrame", background=APP_BG)
        self.style.configure("TLabel", background=APP_BG, foreground=TEXT)
        self.style.configure(
            "Treeview",
            font=("Microsoft YaHei UI", 9),
            rowheight=26,
            background=PANEL_BG,
            fieldbackground=PANEL_BG,
            foreground=TEXT,
            borderwidth=0,
        )
        self.style.map(
            "Treeview",
            background=[("selected", SELECTION)],
            foreground=[("selected", TEXT)],
        )
        self.style.configure(
            "Treeview.Heading",
            font=("Microsoft YaHei", 9, "bold"),
            background=CONTROL_BG,
            foreground=TEXT,
            relief="flat",
        )
        self.style.configure(
            "primary.TButton",
            background=ACCENT,
            bordercolor=ACCENT,
            foreground=TEXT,
        )
        self.style.map(
            "primary.TButton",
            background=[("active", ACCENT_HOVER)],
            bordercolor=[("active", ACCENT_HOVER)],
        )
        self.style.configure(
            "secondary.TButton",
            background=CONTROL_BG,
            bordercolor=CONTROL_BG,
            foreground=TEXT,
        )
        self.service = service
        self.store = store
        self.geometry("1600x1000")
        self.minsize(1280, 760)
        self._tf_var = tk.StringVar(value="daily")
        self._tray_icon = None
        self._tray_actions = queue.SimpleQueue()
        self._closing = False
        self._chart_mode = "candidates"
        self._candidate_source = REALTIME_MARKET_SOURCE
        self._build_ui()
        self._load_strategies()
        if enable_tray:
            self._setup_tray()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 业务板块使用紧凑顶栏，不再用整高侧栏挤压候选和 K 线。
        self.section_nav = ttk.Frame(self, padding=(8, 6))
        self.section_nav.grid(row=0, column=0, sticky=tk.EW)
        self._section_buttons = {}
        self._pages = {}
        section_names = (
            ("premarket", "盘前任务"),
            ("afterhours", "盘后回测"),
            ("strategy", "策略迭代"),
            ("other", "其他工具"),
        )
        for key, label in section_names:
            button = ttk.Button(
                self.section_nav,
                text=label,
                bootstyle="secondary",
                command=lambda k=key: self._switch_section(k),
            )
            button.pack(side=tk.LEFT, padx=(0, 5), ipady=2)
            self._section_buttons[key] = button

        self.page_host = ttk.Frame(self)
        self.page_host.grid(row=1, column=0, sticky=tk.NSEW)
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)

        premarket = ttk.Frame(self.page_host)
        premarket.grid(row=0, column=0, sticky=tk.NSEW)
        premarket.columnconfigure(0, weight=1)
        premarket.rowconfigure(1, weight=1)
        self._pages["premarket"] = premarket
        self.toolbar = ToolBar(
            premarket, self.service, self.store, tf_var=self._tf_var,
            on_timeframe_change=self._on_timeframe,
            on_date_change=self._on_date_change,
            on_job_finished=self._on_job_finished,
            on_chart_page=self._on_chart_page,
            on_chart_layout=self._on_chart_layout,
        )
        self.toolbar.grid(row=0, column=0, sticky=tk.EW)

        self.body = body = ttk.Frame(premarket, padding=(8, 6))
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.candidates = CandidateTabs(
            body,
            self.on_stock_selected,
            on_context=self._candidate_context,
            on_rows_changed=self._on_candidate_rows,
            on_page_request=self._on_chart_page,
        )
        self.candidates.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))
        self.candidates.configure(width=238)
        self.candidates.grid_propagate(False)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky=tk.NSEW)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=0)
        right.rowconfigure(0, weight=1)
        self.chart = ChartGrid(
            right,
            self.store,
            layout_count=4,
            on_page_state=self.toolbar.set_chart_page_status,
            on_active_item=self._on_chart_item_activated,
        )
        self.chart.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))
        self.watch = WatchPanel(
            right,
            self.store,
            on_select=self.on_watch_selected,
            on_drag_motion=self._on_watch_drag_motion,
            on_drop=self._on_watch_drop,
            on_drag_end=self.chart.clear_drop_highlight,
            on_page_request=self._on_chart_page,
        )
        self.watch.grid(row=0, column=1, sticky=tk.NSEW)
        self.watch.configure(width=222)
        self.watch.pack_propagate(False)

        status_bar = ttk.Frame(premarket, padding=(12, 6))
        status_bar.grid(row=2, column=0, sticky=tk.EW)
        self._status_text = self.toolbar._status
        ttk.Label(status_bar, textvariable=self._status_text, font=("Consolas", 10),
                  foreground=MUTED).pack(side=tk.RIGHT)

        self._pages["afterhours"] = self._placeholder_page(
            "盘后回测",
            "用当日收盘后的完整行情复盘信号、检验策略表现。",
            "现有回测能力保持不变；桌面入口将在后续版本接入。",
        )
        self._pages["strategy"] = self._placeholder_page(
            "策略迭代",
            "注册研究策略、运行验证、比较版本，再由交易员决定是否启用。",
            "原有正式策略仍处于冻结保护中，本次界面调整不会改动它们。",
        )
        self._pages["other"] = self._placeholder_page(
            "其他工具",
            "为 AI 辅助、盘中观察和后续 T+0 工具预留独立工作区。",
            "这些能力尚未接入，不会以占位按钮冒充可用功能。",
        )
        self._switch_section("premarket")
        self.bind("<Configure>", self._resize_layout)

    def _placeholder_page(self, title, subtitle, boundary):
        page = ttk.Frame(self.page_host, padding=(46, 42))
        page.grid(row=0, column=0, sticky=tk.NSEW)
        ttk.Label(
            page,
            text=title,
            font=("Microsoft YaHei", 24, "bold"),
            foreground=TEXT,
        ).pack(anchor=tk.W)
        ttk.Label(
            page,
            text=subtitle,
            font=("Microsoft YaHei", 12),
            foreground=TEXT,
            wraplength=680,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(16, 8))
        ttk.Label(
            page,
            text=boundary,
            font=("Microsoft YaHei", 10),
            foreground=MUTED,
            wraplength=680,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        return page

    def _switch_section(self, key):
        for name, page in self._pages.items():
            if name == key:
                page.tkraise()
            self._section_buttons[name].configure(
                bootstyle="primary" if name == key else "secondary"
            )

    def _setup_tray(self):
        """关闭窗口时驻留系统托盘；托盘回调不直接跨线程操作 Tk。"""
        try:
            from pathlib import Path

            from PIL import Image
            import pystray

            icon_path = Path(__file__).resolve().parents[1] / "icon_aplus.ico"
            image = Image.open(icon_path).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem(
                    "打开主界面",
                    lambda _icon, _item: self._tray_actions.put("show"),
                    default=True,
                ),
                pystray.MenuItem(
                    "退出",
                    lambda _icon, _item: self._tray_actions.put("quit"),
                ),
            )
            self._tray_icon = pystray.Icon(
                "aplus-workbench", image, "Brooks-AI 操盘台", menu
            )
            self._tray_icon.run_detached()
            self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
            self.after(200, self._poll_tray_actions)
        except Exception as exc:
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self._status_text.set(f"系统托盘启动失败：{exc}")

    def _hide_to_tray(self):
        if self._closing:
            return
        self.withdraw()

    def _show_from_tray(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _poll_tray_actions(self):
        if self._closing:
            return
        try:
            while True:
                action = self._tray_actions.get_nowait()
                if action == "show":
                    self._show_from_tray()
                elif action == "quit":
                    self._exit_from_tray()
                    return
        except queue.Empty:
            pass
        self.after(200, self._poll_tray_actions)

    def _exit_from_tray(self):
        """托盘“退出”才是真正关闭；运行任务先请求安全停止。"""
        if self._closing:
            return
        self._closing = True
        if self.toolbar._job_id and self.service is not None:
            try:
                self.service.cancel(self.toolbar._job_id)
            except Exception:
                pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    def _resize_layout(self, event):
        if event.widget is not self:
            return
        # Lists follow their actual three-column content and yield spare width to charts.
        width = self.page_host.winfo_width()
        candidate_width, watch_width = side_panel_widths(width)
        self.candidates.configure(width=candidate_width)
        self.watch.configure(width=watch_width)

    # ---- 周期切换：重读候选（保留当前日期筛选）----
    def _on_timeframe(self, tf):
        self._cur_date = self.toolbar.selected_date()
        self._candidate_source = self._source_for_current_date(tf)
        self.chart.set_timeframe(tf)
        self.chart.clear()
        if self.store is not None:
            self.candidates.load_from_store(
                self.store,
                timeframe=tf,
                asof_filter=getattr(self, "_cur_date", None),
                source=self._candidate_source,
            )
        self.toolbar.date_label.configure(text="截至周" if tf == "weekly" else "交易日")
        self._status_text.set(f"周期：{tf}")

    # ---- 信号日筛选：重读候选 ----
    def _on_date_change(self, year, month, day):
        self._cur_date = (year, month, day)
        self._candidate_source = self._source_for_current_date(self._tf_var.get())
        if self.store is not None:
            self.candidates.load_from_store(
                self.store,
                timeframe=self._tf_var.get(),
                asof_filter=self._cur_date,
                source=self._candidate_source,
            )
        label = f"{year or '*'}-{month or '*'}-{day or '*'}"
        self._status_text.set(f"信号日筛选：{label}")
        self.chart.set_timeframe(self._tf_var.get())

    # ---- 策略 Tab（动态取真实名称，只读，不碰冻结文件）----
    def _load_strategies(self):
        self._cur_date = self.toolbar.selected_date()
        self._candidate_source = self._source_for_current_date(self._tf_var.get())
        try:
            from core.strategy_registry import StrategyRegistry as R

            strategies = []
            for key in R.list_strategies():
                try:
                    label = R.get_metadata(key).get("display_name", key)
                except Exception:
                    label = key
                strategies.append((key, label))
        except Exception:
            strategies = [
                ("MTR_MASTER", "MTR"),
                ("STRATEGY_GAP_H2", "GAP H2"),
                ("STRATEGY_AWIL", "AIL"),
            ]
        self.candidates.build_tabs(strategies)
        if self.store is not None:
            self.candidates.load_from_store(
                self.store,
                timeframe=self._tf_var.get(),
                asof_filter=self._cur_date,
                source=self._candidate_source,
            )

    # ---- 候选与多图联动 ----
    def _on_candidate_rows(self, rows):
        self._chart_mode = "candidates"
        self.toolbar.set_chart_source("策略")
        self.chart.set_items(chart_items(rows, "candidate"))

    def on_stock_selected(self, code, observation_id=None):
        if observation_id and self.store is not None:
            if self._chart_mode != "candidates":
                rows = self.candidates.rows()
                items = chart_items(rows, "candidate")
                index = next(
                    (i for i, item in enumerate(items)
                     if item.observation_id == observation_id),
                    0,
                )
                self._chart_mode = "candidates"
                self.toolbar.set_chart_source("策略")
                self.chart.set_items(items, selected_index=index)
            else:
                self.chart.focus_observation(observation_id)
            self._status_text.set(f"已加载：{code}")
        elif code and self.store is not None:
            from .data import latest_observation

            oid = latest_observation(
                self.store,
                code,
                timeframe=self._tf_var.get(),
                source=self._candidate_source,
            )
            if oid:
                item = ChartItem(
                    code=code,
                    name="",
                    observation_id=oid,
                    source=self._candidate_source,
                    mode="candidate",
                    timeframe=self._tf_var.get(),
                )
                self.chart.replace_active(item)
                self._status_text.set(f"已加载：{code}")
            else:
                self._status_text.set(f"无 {code} 的行情快照")
        else:
            self.chart.clear()

    def on_watch_selected(self, code, observation_id=None):
        if not code or self.store is None:
            return
        if not observation_id:
            from .data import latest_observation

            observation_id = latest_observation(
                self.store,
                code,
                timeframe=self._tf_var.get(),
                source=self._candidate_source,
            )
        if observation_id:
            rows = self.watch.rows()
            items = chart_items(rows, "watch")
            if not any(item.observation_id == observation_id for item in items):
                items.append(
                    ChartItem(
                        code=code,
                        name="",
                        observation_id=observation_id,
                        source=self._candidate_source,
                        mode="watch",
                        timeframe=self._tf_var.get(),
                    )
                )
            index = next(
                (i for i, item in enumerate(items)
                 if item.observation_id == observation_id or item.code == code),
                0,
            )
            self._chart_mode = "watch"
            self.toolbar.set_chart_source("关注")
            self.chart.set_items(items, selected_index=index)
            self._status_text.set(f"关注浏览：{code}（左右键切换关注列表）")
        else:
            self._status_text.set(f"无 {code} 的可用行情快照")

    def _on_chart_page(self, delta):
        self.chart.page(delta)

    def _on_chart_layout(self, count):
        self.chart.set_layout(count)
        self._status_text.set(f"K 线布局：{count} 格")

    def _on_chart_item_activated(self, observation_id):
        if observation_id is not None:
            if self._chart_mode == "watch":
                self.watch.select_observation(observation_id)
            else:
                self.candidates.select_observation(observation_id)

    def _on_watch_drag_motion(self, root_x, root_y):
        self.chart.highlight_drop(root_x, root_y)

    def _on_watch_drop(self, code, observation_id, root_x, root_y):
        if not observation_id and code and self.store is not None:
            from .data import latest_observation

            observation_id = latest_observation(
                self.store,
                code,
                timeframe=self._tf_var.get(),
                source=self._candidate_source,
            )
        rows = self.watch.rows()
        matching = [
            row for row in rows
            if row.get("observation_id") == observation_id or row.get("code") == code
        ]
        items = chart_items(matching[:1], "watch")
        item = items[0] if items else None
        if observation_id and item is None:
            item = ChartItem(
                code=code,
                name="",
                observation_id=observation_id,
                source=self._candidate_source,
                mode="watch",
                timeframe=self._tf_var.get(),
            )
        if item and self.chart.replace_at_point(item, root_x, root_y):
            self._status_text.set(f"已把关注标的 {code} 放入指定图格")
        else:
            self._status_text.set("拖拽未落在 K 线图格内，未替换")

    # ---- 任务结束（下载/扫描）：刷新候选、观察池与信号日下拉 ----
    def _on_job_finished(self, kind, status):
        if self.store is None:
            return
        try:
            # 同步后把新行情日加入下拉，但保持交易员当前正在看的扫描日；
            # 扫描后也由交易员主动切到新日期，不抢走当前复核上下文。
            selected_date = self.toolbar.selected_date()
            self.toolbar._load_date_options()
            self._cur_date = selected_date
            self._candidate_source = self._source_for_current_date(self._tf_var.get())
            self.candidates.load_from_store(
                self.store, timeframe=self._tf_var.get(),
                asof_filter=getattr(self, "_cur_date", None),
                source=self._candidate_source,
            )
            self.watch.reload()
            self.toolbar._load_market_status()
        except Exception:
            pass
        # ToolBar 已保留任务结果，并在行情更新后明确提示“行情最新/信号最新/待扫描”。
        # 此处只刷新视图，不再用泛化文案覆盖那条关键结果。

    def _source_for_current_date(self, timeframe):
        """每个日期只选一个来源；正式 Aplus 同日结果优先于迁入基线。"""
        if self.store is None:
            return REALTIME_MARKET_SOURCE
        from .data import candidate_source_for_date

        # 没有具体日期时按当前 Aplus 查询；启动时通常已自动定位到最近
        # 一次本地扫描日，因此迁入的历史成果仍能作为默认工作基线。
        current_date = getattr(self, "_cur_date", None)
        if not current_date or not any(current_date):
            return REALTIME_MARKET_SOURCE

        return candidate_source_for_date(
            self.store,
            timeframe=timeframe,
            asof_filter=current_date,
        )

    # ---- 左栏候选右键：加入关注（写只走 store.watch）----
    def _candidate_context(self, event, code, observation_id):
        if self.store is None or not observation_id:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label=f"加入关注（{code}）",
            command=lambda: self._add_watch(code, observation_id),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _add_watch(self, code, observation_id):
        try:
            self.store.watch(observation_id)
        except Exception as exc:
            messagebox.showerror("加入关注失败", str(exc), parent=self)
            return
        self.watch.reload()
        self._status_text.set(f"已加入关注：{code}")
