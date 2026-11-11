"""工程 A 桌面布局，使用 Aplus Service/Store 后端。"""
from __future__ import annotations

import os
import queue
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
import ttkbootstrap as ttk

from .match_list import MatchList as CandidateTabs
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


def right_list_width(window_width, scale=1):
    """右侧双层清单约为旧关注栏的 1.5 倍，同时保护 K 线空间。"""
    width = max(round(1280*scale), int(window_width or 0))
    return max(round(330*scale), min(round(390*scale), round(width * 0.225)))


class _StartupWatchPanel(WatchPanel):
    """Build with saved view settings, but defer the expensive initial rows."""
    def __init__(self, *args, **kwargs):
        self._all_rows = []
        self._defer_reload = True
        super().__init__(*args, **kwargs)
        self._defer_reload = False

    def reload(self):
        if not self._defer_reload:
            return super().reload()


class AplusMainWindow(ttk.Window):
    def __init__(self, service=None, store=None, enable_tray=False):
        from workbench.branding import APPLICATION_TITLE, apply_window_icon
        super().__init__(themename="darkly", title=APPLICATION_TITLE, iconphoto=None)
        apply_window_icon(self)
        self._display_scale = self.winfo_fpixels('1i') / 96
        listing_font = tkfont.Font(root=self, family='Microsoft YaHei UI', size=9)
        listing_height = max(round(26*self._display_scale),
                             listing_font.metrics('linespace')+round(6*self._display_scale))
        self.style.colors.primary = ACCENT
        self.configure(background=APP_BG)
        self.style.configure("TFrame", background=APP_BG)
        self.style.configure("TLabel", background=APP_BG, foreground=TEXT)
        self.style.configure(
            "Treeview",
            font=("Microsoft YaHei UI", 9),
            rowheight=listing_height,
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
        self.minsize(800, 520)
        try:
            self.state('zoomed')
        except tk.TclError:
            pass  # Non-Windows test desktops may not support zoomed.
        self._tf_var = tk.StringVar(value="daily")
        self._tray_icon = None
        self._tray_actions = queue.SimpleQueue()
        self._closing = False
        self._chart_mode = "candidates"
        self._candidate_source = REALTIME_MARKET_SOURCE
        self._case_comparisons = []
        self.auto_quote_var = tk.BooleanVar(value=self.store.holding_auto_quotes() if self.store else False)
        self._build_ui()
        self._shape_last_run = None
        if self.service is not None:
            self.after(1000,self._poll_shape_results)
        # Let Tk map/paint the shell before running each initial data stage.
        self.after_idle(lambda: self.after(0, self._startup_watch))
        import sys
        if getattr(sys, 'frozen', False):
            from .installed_update import InstalledVersionController as VersionController
        else:
            from .version_update import VersionController
        self.version_controller = VersionController(self, self.section_nav)
        self.toolbar._on_scan_started = self.version_controller.scan_hint
        if enable_tray:
            self._setup_tray()
        else:
            self.protocol('WM_DELETE_WINDOW', self._exit_from_tray)
        from .chart_alerts import ChartAlertMonitor
        self.chart_alert_monitor = ChartAlertMonitor(self, self.store)
        self.chart_alert_monitor.start()
        # Responsiveness watchdog (Issue #132): the ticker only beats a
        # heartbeat; a daemon thread records main-thread stacks on stalls.
        self._watchdog = None
        self._exit_poll_id = None
        self._exit_hosts = None
        if os.environ.get('APLUS_RESPONSIVENESS_WATCHDOG', '1') != '0':
            from .responsiveness import ResponsivenessWatchdog
            self._watchdog = ResponsivenessWatchdog()
            self._watchdog.start()
            self._watchdog_tick()

    def _watchdog_tick(self):
        if self._watchdog is None:
            return
        self._watchdog.beat()
        try:
            self.after(100, self._watchdog_tick)
        except Exception:
            pass

    def _startup_watch(self):
        if self._closing:
            return
        self.watch.reload()
        self.after_idle(lambda: self.after(0, self._startup_candidates))

    def _startup_candidates(self):
        if self._closing:
            return
        self._load_strategies()  # Original callbacks select/render the same first page.
        self.after_idle(self.chart.restore_saved_layout)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 业务板块使用紧凑顶栏，不再用整高侧栏挤压候选和 K 线。
        self.section_nav = ttk.Frame(self, padding=(8, 6))
        self.section_nav.grid(row=0, column=0, sticky=tk.EW)
        self.auto_quote_button = ttk.Checkbutton(
            self.section_nav, text="自动行情", variable=self.auto_quote_var,
            command=self._toggle_auto_quotes, bootstyle="success-round-toggle")
        self.auto_quote_button.pack(side=tk.LEFT, padx=(0, 12))
        self._section_buttons = {}
        self._pages = {}
        section_names = (
            ("premarket", "盘前任务"),
            ("afterhours", "盘后回测"),
            ("strategy", "策略迭代"),
            ("other", "交易管理"),
            ("growth", "成长空间"),
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
        )
        self.toolbar.grid(row=0, column=0, sticky=tk.EW)

        self.body = body = ttk.Frame(premarket, padding=(8, 6))
        body.grid(row=1, column=0, sticky=tk.NSEW)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        self.chart = ChartGrid(
            body,
            self.store,
            layout_count=1,
            on_page_state=self.toolbar.set_chart_page_status,
            on_active_item=self._on_chart_item_activated,
            interactive=True,
            on_layout_request=self._on_chart_layout,
            on_cases=self._open_case_comparison,
            on_symbol=self._open_chart_symbol,
            on_grid_changed=self._apply_list_layout,
            on_fullscreen=self._set_chart_fullscreen,
            on_trade=lambda:self._switch_section('other'),
            auto_quotes=lambda: bool(self.auto_quote_var.get()),
        )
        self.chart.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))

        self.list_pane = ttk.Frame(body)
        self.list_pane.grid(row=0, column=1, sticky=tk.NSEW)
        self.list_pane.columnconfigure(0, weight=1)
        # 高频关注列表通常比当日策略结果更长：上方 1/3、下方 2/3。
        self.list_pane.rowconfigure(0, weight=1, uniform='lists')
        self.list_pane.rowconfigure(2, weight=2, uniform='lists')
        self.list_pane.configure(width=round(360*self._display_scale))
        self.list_pane.grid_propagate(False)
        self.candidates = CandidateTabs(
            self.list_pane,
            self.on_stock_selected,
            on_context=self._candidate_context,
            on_rows_changed=self._on_candidate_rows,
            on_page_request=self._on_chart_page,
            on_manage=self._open_shapes,
        )
        self.candidates.grid(row=0, column=0, sticky=tk.NSEW)
        ttk.Separator(self.list_pane, orient=tk.HORIZONTAL).grid(
            row=1, column=0, sticky=tk.EW, pady=8
        )
        self.watch = _StartupWatchPanel(
            self.list_pane,
            self.store,
            on_select=self.on_watch_selected,
            on_drag_motion=self._on_watch_drag_motion,
            on_drop=self._on_watch_drop,
            on_drag_end=self.chart.clear_drop_highlight,
            on_page_request=self._on_chart_page,
            on_alert=self._watch_alert,
            on_compare=self._watch_compare,
            auto_quotes=lambda:bool(self.auto_quote_var.get()),
        )
        self.watch.grid(row=2, column=0, sticky=tk.NSEW)
        self._sidebar_width = self.watch.views.data.get('sidebarWidth')
        self._list_grip = tk.Frame(body, width=6, bg=CONTROL_BG, cursor='sb_h_double_arrow')
        self._list_grip.place(x=0, y=0, height=1)
        self.list_pane.bind('<Configure>', lambda e:self._list_grip.place(
            x=max(0, self.list_pane.winfo_x()-7), y=0, width=6, height=self.body.winfo_height()), add='+')
        self._list_grip.bind('<B1-Motion>', self._resize_sidebar)
        self._list_grip.bind('<ButtonRelease-1>', self._save_sidebar)
        self._list_rail=tk.Frame(body,autostyle=False,bg=APP_BG)
        self._list_rail.grid(row=0,column=2,sticky=tk.NS)
        from .workspace_icons import icon
        from ttkbootstrap.widgets import ToolTip
        self._list_icon=icon(self,'sidebar',round(22*self._display_scale))
        self._list_button=tk.Button(self._list_rail,autostyle=False,image=self._list_icon,
            command=self._toggle_lists,background=APP_BG,activebackground=CONTROL_BG,borderwidth=0,padx=4,pady=5)
        self._list_button.pack()
        ToolTip(self._list_button,text='显示／隐藏匹配列表和关注列表',delay=650)
        self._lists_override=None
        self._apply_list_layout(self.chart.layout_count)

        self.premarket_status = status_bar = ttk.Frame(premarket, padding=(12, 6))
        status_bar.grid(row=2, column=0, sticky=tk.EW)
        self._status_text = self.toolbar._status
        ttk.Label(status_bar, textvariable=self._status_text, font=("Consolas", 10),
                  foreground=MUTED).pack(side=tk.RIGHT)

        self._switch_section("premarket")
        self.bind("<Configure>", self._resize_layout)
        for key, action in [
            ('<Control-k>', self.chart.controls.open_quick_search),
            ('<F11>', self.chart.controls.toggle_fullscreen),
            ('<Escape>', lambda:self.chart.controls.set_fullscreen(False)),
            ('<Control-Alt-s>', lambda:self.chart.command('action','snapshotSave')),
            ('<Control-Shift-S>', lambda:self.chart.command('action','snapshot')),
            ('<Alt-s>', lambda:self.chart.command('action','snapshotLink')),
        ]:
            self.bind(key,lambda event,a=action:self._chart_shortcut(event,a),add='+')

    def _chart_shortcut(self, event, action):
        if self.chart.winfo_ismapped() and not isinstance(event.widget,(tk.Entry,tk.Text,ttk.Entry,ttk.Combobox)):
            if event.keysym == 'Escape' and not self.chart.controls.fullscreen:
                return
            action()
            return 'break'

    def _set_chart_fullscreen(self, enabled):
        for widget in (self.section_nav,self.toolbar,self.premarket_status):
            if enabled:
                widget.grid_remove()
            else:
                widget.grid()

    def _watch_alert(self, code):
        self.on_watch_selected(code, next((row['observation_id'] for row in self.watch.rows() if row['code']==code), None))
        # The existing alert form validates actual source/period data before save.
        index = self.chart._active_slot
        def open_when_ready(attempt=0):
            item = self.chart.current_item()
            if index != self.chart._active_slot or not item or item.code != code:
                return
            host = self.chart._slots[index]._embedded
            if host and host.ready():
                self.chart.command('action','alert')
            elif attempt < 50:
                self.after(200, lambda:open_when_ready(attempt+1))
            elif self.chart.controls:
                self.chart.controls.caption.set('图表尚未加载完成，请稍后再点提醒')
        self.after(200, open_when_ready)

    def _watch_compare(self, code):
        self.chart.compare(code)

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

    def _section(self, key):
        """Construct hidden business pages only when actually requested."""
        if key not in self._pages:
            if key == 'afterhours':
                from .afterhours import AfterhoursPage
                page = AfterhoursPage(self.page_host, self.service, self.store)
            elif key == 'strategy':
                from .strategy_iteration import StrategyIterationPage
                page = StrategyIterationPage(self.page_host, self.service, self.store)
            elif key == 'other':
                from .trade_management import TradeManagementFrame
                page = TradeManagementFrame(self.page_host, self.store,
                                            auto_quote_var=self.auto_quote_var)
            elif key == 'growth':
                from .growth_space import GrowthSpacePage
                page = GrowthSpacePage(self.page_host, self.store, on_shape=self._shape_from_case)
            else:
                raise ValueError('未知业务页面')
            page.grid(row=0, column=0, sticky=tk.NSEW)
            self._pages[key] = page
        return self._pages[key]

    @property
    def afterhours(self):
        return self._section('afterhours')

    @property
    def strategy_iteration(self):
        return self._section('strategy')

    @property
    def trade_management(self):
        return self._section('other')

    def _switch_section(self, key):
        if key != 'premarket' and self.chart.controls.fullscreen:
            self.chart.controls.set_fullscreen(False)
        self._section(key)
        if key == "other":
            # Mask before raising the page, not after revealing the old frame.
            self.trade_management.hide_private_values()
        if key == 'strategy':
            self.strategy_iteration.refresh()
        if key == 'afterhours':
            self.afterhours.refresh()
        # Native WebView children must be hidden with their parent page, so a
        # chart cannot cover the PA page or steal its clipboard input.
        for name, page in self._pages.items():
            if name != key:
                page.grid_remove()
        self._pages[key].grid(row=0, column=0, sticky=tk.NSEW)
        self._pages[key].tkraise()
        trade_button = self._section_buttons['other']
        if key == 'premarket':
            trade_button.pack_forget()
        elif not trade_button.winfo_manager():
            trade_button.pack(side=tk.LEFT,padx=(0,5),ipady=2,before=self._section_buttons['growth'])
        if key == 'growth':
            self.after_idle(self._pages[key].start)
        for name, button in self._section_buttons.items():
            button.configure(
                bootstyle="primary" if name == key else "secondary"
            )
        if key == "other":
            self.trade_management.reload()

    def _toggle_auto_quotes(self):
        if self.store is not None:
            self.store.holding_auto_quotes(self.auto_quote_var.get())
        self.chart.auto_quotes_changed()
        page = self._pages.get('other')
        if page is not None:
            page._toggle_auto_quotes(persist=False)

    def _open_case_comparison(self):
        item = self.chart.current_item()
        if item is None:
            messagebox.showinfo('历史案例对照', '先选择要对照的行情标的。', parent=self)
            return
        from .growth_space import open_chart_cases
        try:
            host = open_chart_cases(self, self.store, item)
            self._case_comparisons = [value for value in self._case_comparisons if not value.closed()]
            self._case_comparisons.append(host)
        except Exception as exc:
            messagebox.showerror('案例暂时无法打开', str(exc), parent=self)

    def _setup_tray(self):
        """关闭窗口时驻留系统托盘；托盘回调不直接跨线程操作 Tk。"""
        try:
            from PIL import Image
            import pystray
            from workbench.branding import APPLICATION_TITLE, application_icon

            icon_path = application_icon()
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
                "aplus-workbench", image, APPLICATION_TITLE, menu
            )
            self._tray_icon.run_detached()
            self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
            self.after(200, self._poll_tray_actions)
        except Exception as exc:
            self.protocol("WM_DELETE_WINDOW", self._exit_from_tray)
            self._status_text.set(f"系统托盘启动失败：{exc}")

    def _hide_to_tray(self):
        if self._closing:
            return
        self.withdraw()

    def _show_from_tray(self):
        if getattr(self, "_update_pending", False):
            return
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
                    # Preparation may refuse the exit. Keep listening so a
                    # failed update can restore a fully usable tray.
                    if self._closing:
                        return
        except queue.Empty:
            pass
        self.after(200, self._poll_tray_actions)

    def _exit_from_tray(self):
        """托盘“退出”才是真正关闭；运行任务先请求安全停止。

        Issue #132: the chart close handshake is an after()-driven state
        machine, never a busy wait on the Tk thread — the window stays
        responsive while charts acknowledge, a refusal restores the
        session, and the 15s timeout warns and restores instead of
        freezing the window as "not responding".
        """
        if self._closing or getattr(self, "_update_pending", False):
            return
        self._closing = True
        self._begin_exit_flush()

    def _begin_exit_flush(self):
        import time
        chart = getattr(self, 'chart', None)
        hosts = chart.embedded_hosts() if chart else []
        page = getattr(self, '_pages', {}).get('growth')
        if page is not None and page.host is not None:
            hosts.append(page.host)
        hosts.extend(getattr(self, '_case_comparisons', []))
        self._exit_hosts = hosts
        live = [host for host in hosts if not host.closed()]
        if not live:
            self._finish_exit()
            return
        for host in live:
            host.request_close()
        self._exit_started = time.monotonic()
        self._exit_deadline = self._exit_started + 15
        self._exit_poll_id = self.after(50, self._poll_exit_flush)

    def _poll_exit_flush(self):
        import time
        self._exit_poll_id = None
        if not self._closing or not self.winfo_exists():
            return
        hosts = self._exit_hosts or []
        live = [host for host in hosts if not host.closed()]
        if not live:
            for host in hosts:
                host.cleanup()
            self._finish_exit()
            return
        now = time.monotonic()
        refused = any(not host.closing for host in live)
        if refused and now - self._exit_started > .3:
            # A chart declined to close (unsaved work or fault): restore
            # the session exactly like the synchronous guard did.
            self._abort_exit()
            return
        if now >= self._exit_deadline:
            messagebox.showwarning('图表暂未关闭',
                '图表关闭确认未完成，可能是通信中断或分析尚未保存。主窗口已保留，请查看图表提示后重试。', parent=self)
            self._abort_exit()
            return
        self._exit_poll_id = self.after(50, self._poll_exit_flush)

    def _abort_exit(self):
        self._closing = False
        self._exit_hosts = None
        failure = getattr(self, "_exit_failure", None)
        self._exit_completion = self._exit_failure = None
        self._resume_embedded_charts()
        if self._tray_icon is not None:
            # Tray polling stopped when the exit began; a refused exit
            # must restore a fully usable tray.
            self.after(200, self._poll_tray_actions)
        if failure is not None:
            failure()

    def _finish_exit(self):
        self._exit_hosts = None
        completion = getattr(self, "_exit_completion", None)
        self._exit_completion = None
        if completion is not None:
            try:
                completion()
            except Exception:
                self._abort_exit()
                return
            self._exit_failure = None
            return
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

    def _flush_embedded_charts(self):
        # Native children must acknowledge their final save before Tk destroys
        # their HWND parents. Never terminate a chart to hide a save failure.
        if getattr(self, '_chart_exit_wait', False):
            return False
        import time
        chart = getattr(self, 'chart', None)
        hosts = chart.embedded_hosts() if chart else []
        page = getattr(self, '_pages', {}).get('growth')
        if page is not None and page.host is not None:
            hosts.append(page.host)
        hosts.extend(getattr(self, '_case_comparisons', []))
        live = [host for host in hosts if not host.closed()]
        self._chart_exit_wait = True
        try:
            for host in live:
                host.request_close()
            deadline = time.monotonic() + 15
            while any(not host.closed() for host in live) and time.monotonic() < deadline:
                self.update()
                if any(not host.closed() and not host.closing for host in live):
                    break
                time.sleep(.03)
        finally:
            self._chart_exit_wait = False
        if any(not host.closed() for host in live):
            self._closing = False
            if any(not host.closed() and not host.closing for host in live):
                self._resume_embedded_charts()
                return False
            messagebox.showwarning('图表暂未关闭',
                '图表关闭确认未完成，可能是通信中断或分析尚未保存。主窗口已保留，请查看图表提示后重试。', parent=self)
            self._resume_embedded_charts()
            return False
        for host in hosts:
            host.cleanup()
        return True

    def _resume_embedded_charts(self):
        chart = getattr(self, 'chart', None)
        if chart:
            for slot in chart._slots:
                if slot._code and slot._embedded is not None and slot._embedded.closed():
                    slot._show_interactive()

    def destroy(self):
        if getattr(self, '_exit_poll_id', None) is not None:
            try:
                self.after_cancel(self._exit_poll_id)
            except Exception:
                pass
            self._exit_poll_id = None
        if getattr(self, '_watchdog', None) is not None:
            self._watchdog.stop()
        runner = vars(self.service).get('_shape_tasks') if self.service is not None else None
        if runner is not None:
            # Issue #132: window teardown must not synchronously wait for
            # background workers; close() already requested cooperative
            # cancellation, unfinished work is abandoned to daemon threads.
            runner.close(wait=False)
        super().destroy()

    def _close_for_version_update(self, on_failure=None):
        """Keep the update reservation until the shared chart ACK gate finishes."""
        if self._closing:
            return
        self._closing = True
        self._exit_completion = self._finish_version_update
        self._exit_failure = on_failure
        try:
            self._begin_exit_flush()
        except Exception:
            self._abort_exit()
            if on_failure is None:
                raise

    def _finish_version_update(self):
        """Only reached after every chart has confirmed its final save."""
        # The update was reserved while idle; never let a stray task turn
        # this into an unbounded wait on the Tk thread (Issue #132).
        self.service.pool.shutdown(wait=False, cancel_futures=True)
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    def _resize_layout(self, event):
        if event.widget is not self:
            return
        # 两个列表共用右栏，宽度约为旧关注栏的 1.5 倍。
        requested = float(self._sidebar_width) * self._display_scale if self._sidebar_width else right_list_width(event.width,self._display_scale)
        self.list_pane.configure(width=max(round(260*self._display_scale), min(round(event.width*.45),round(requested))))

    def _resize_sidebar(self, event):
        width = self.body.winfo_rootx() + self.body.winfo_width() - event.x_root
        width = max(260*self._display_scale, min(self.body.winfo_width()*.45,width))
        self._sidebar_width = width/self._display_scale
        self.list_pane.configure(width=round(width))

    def _apply_list_layout(self,count):
        if not hasattr(self,'_list_button'):
            return
        visible=self._lists_override if self._lists_override is not None else count==1
        if visible:
            self.list_pane.grid()
            self.chart.grid_configure(padx=(0,8))
        else:
            self.list_pane.grid_remove()
            self._list_grip.place_forget()
            self.chart.grid_configure(padx=(0,2))
        self._list_button.configure(background=CONTROL_BG if visible else APP_BG)

    def _toggle_lists(self):
        self._lists_override=not bool(self.list_pane.grid_info())
        self._apply_list_layout(self.chart.layout_count)

    def _open_shapes(self, case=None):
        if self.store is None or self.service is None:
            return
        from .shape_dialog import ShapeDialog
        self._switch_section('premarket')
        ShapeDialog(self,self.store,self.service,self.toolbar.selected_date(),case=case,
                    on_results=self._shape_results,on_compare=self._compare_shape,
                    current_item=self.chart.current_item())

    def _shape_from_case(self, case):
        self._open_shapes(case)

    def _shape_results(self, asof=None):
        # Manual searches intentionally show their selected day. Automatic
        # tracking only refreshes saved results and never changes this date.
        self._tf_var.set('daily')
        self.chart.set_timeframe('daily')
        self.toolbar._load_date_options()
        if asof:
            self.toolbar._select_date(asof)
        self._on_date_change(*self.toolbar.selected_date())

    def _poll_shape_results(self):
        if self._closing:return
        try:
            status = self.service.shape_tasks().status()
            self.candidates.set_task_status(status)
            if status.get('run_id') and status['run_id'] != self._shape_last_run:
                self._shape_last_run = status['run_id']
                self.toolbar._load_date_options()
                self.candidates.load_from_store(self.store,timeframe=self._tf_var.get(),
                    asof_filter=self.toolbar.selected_date(),source=self._candidate_source,notify=False)
        except (ValueError,OSError) as exc:
            self._status_text.set('形态结果暂未刷新：'+str(exc))
        finally:
            self.after(1000,self._poll_shape_results)

    def _compare_shape(self, row):
        origin = row['template'].get('origin',{})
        if not origin.get('dataset_id'):
            if origin.get('case_id'):
                self._open_shape_case(row)
                return
            from tkinter import messagebox
            messagebox.showinfo('轮廓模板', '手绘和经典模板没有真实OHLC。请在形态模板窗口核对原轮廓；候选仍在当前图表中。', parent=self)
            return
        original = dict(kind='shape',code=origin['code'],name='原案例',source=origin['source'],
            observation_id='shape-template:'+row['template']['version'],dataset_id=origin['dataset_id'],
            sha256=origin['sha256'],start=origin['start'],end=origin['cutoff'],
            label=row['template']['name'],score=100)
        self.chart.set_grid(1,2,render=False)
        self.chart.set_items(chart_items([original,row],'candidate'))

    def _save_sidebar(self, _event=None):
        self.watch.views.data['sidebarWidth'] = self._sidebar_width
        self.watch._save_view()

    # ---- 周期切换：重读候选（保留当前日期筛选）----
    def _on_timeframe(self, tf):
        current = self.chart.current_item()
        if self._chart_mode == 'watch' and current and current.manual:
            self.on_watch_selected(current.code)
            self.toolbar.date_label.configure(text='截至周' if tf == 'weekly' else '交易日')
            return
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
        self.toolbar.set_chart_source("匹配")
        self.chart.set_items(chart_items(rows, "candidate"))

    def on_stock_selected(self, code, observation_id=None):
        if observation_id and self.store is not None:
            if self.candidates.consume_refresh() is True:
                self._chart_mode = 'refresh_candidates'
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
                if not self.chart.focus_observation(observation_id):
                    rows = self.candidates.rows()
                    index = next((i for i,r in enumerate(rows) if r.get('observation_id')==observation_id),0)
                    self.chart.set_items(chart_items(rows,'candidate'),selected_index=index)
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
        rows = self.watch.rows()
        manual = next((r for r in rows if r['code'] == code and r.get('manual')), None)
        if manual:
            items = chart_items([{**r, 'timeframe': self._tf_var.get()} if r.get('manual') else r
                                 for r in rows], 'watch')
            index = next(i for i, item in enumerate(items) if item.code == code)
            self._chart_mode = 'watch'
            self.toolbar.set_chart_source('关注')
            self.chart.set_items(items, selected_index=index)
            self._status_text.set(f'手动关注：{code} · K图按需加载，不运行策略扫描')
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
        if self._chart_mode == 'candidates' and self.candidates.consume_refresh() is True:
            current = self.chart.current_item()
            rows = self.candidates.rows()
            index = next((i for i,r in enumerate(rows) if current and r.get('observation_id')==current.observation_id),0)
            self.chart.set_items(chart_items(rows,'candidate'),selected_index=index)
        self.chart.page(delta)

    def _on_chart_layout(self, count):
        self.chart.set_layout(count)
        self._status_text.set(f"K 线布局：{count} 格")

    def _open_chart_symbol(self,code):
        if self.store is None:return False
        from .data import code_names
        from workbench.watch_history import chart_source
        row=next((r for r in self.watch.rows() if r['code']==code),None)
        items=chart_items([{**row,'timeframe':self._tf_var.get()}] if row else [],'watch')
        item=items[0] if items else ChartItem(code=code,name=code_names(self.store).get(code,''),
            observation_id=None,source=chart_source(self.store,code),mode='watch',
            timeframe=self._tf_var.get(),manual=True)
        return self.chart.replace_slot(self.chart._active_slot,item,notify_active=True)

    def _on_chart_item_activated(self, observation_id):
        if observation_id is not None:
            if str(observation_id).startswith('watch:'):
                self.watch.select_code(observation_id[len('watch:'):])
                return
            if self._chart_mode == "watch":
                self.watch.select_observation(observation_id, take_focus=False)
            else:
                self.candidates.select_observation(observation_id, take_focus=False)

    def _on_watch_drag_motion(self, root_x, root_y):
        self.chart.highlight_drop(root_x, root_y)

    def _on_watch_drop(self, code, observation_id, root_x, root_y):
        manual = next((r for r in self.watch.rows() if r['code'] == code and r.get('manual')), None)
        if manual:
            value = {**manual, 'timeframe': self._tf_var.get()}
            self.chart.replace_at_point(chart_items([value], 'watch')[0], root_x, root_y)
            return
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
    def _on_job_finished(self, kind, status, result=None):
        if self.store is None:
            return
        try:
            # 行情更新只扩充可选日期，不打断交易员正在复核的旧信号；
            # 扫描产出属于用户刚发起的当前任务，应立即显示该次实际扫描日。
            selected_date = self.toolbar.selected_date()
            self.toolbar._load_date_options()
            scanned_day = (result or {}).get('asof') if (
                kind == '扫描策略' and status in ('completed','partial')) else None
            if kind == '一键盘前':
                from workbench.sources import market_source
                item = (result or {}).get('sources', {}).get(market_source(self.store), {})
                if item.get('scan_status') in ('completed','partial'):
                    scanned_day = item.get('scan_result', {}).get('asof')
            if scanned_day and self.toolbar._select_date(scanned_day):
                selected_date = self.toolbar.selected_date()
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
            from workbench.sources import market_source
            return market_source(self.store)

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
        row = next((r for r in self.candidates.rows() if r.get('observation_id')==observation_id),None)
        if row and row.get('kind')=='shape':
            menu.add_command(label='与原模板双图对照',command=lambda:self._compare_shape(row))
            if row['template'].get('origin',{}).get('case_id'):
                menu.add_command(label='返回原案例研究',command=lambda:self._open_shape_case(row))
        menu.tk_popup(event.x_root, event.y_root)

    def _open_shape_case(self,row):
        from .growth_space import open_shape_case
        self._case_comparisons.append(open_shape_case(self,self.store,row['template']['origin']['case_id']))

    def _add_watch(self, code, observation_id):
        try:
            row = next((r for r in self.candidates.rows() if r.get('observation_id')==observation_id),None)
            if row and row.get('kind')=='shape':
                self.store.watch_shape_match(row['run_id'],row['id'])
            else:
                self.store.watch(observation_id)
        except Exception as exc:
            messagebox.showerror("加入关注失败", str(exc), parent=self)
            return
        self.watch.reload()
        self._status_text.set(f"已加入关注：{code}")
