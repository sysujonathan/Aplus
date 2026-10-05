"""Compact trader-oriented GAP H2 research, isolated from the premarket desk."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
import tkinter as tk
from tkinter import messagebox

import ttkbootstrap as ttk
from PIL import ImageTk

from workbench.backtest import Assumptions
from workbench.h2_replay import H2Settings, LABELS, MODEL
from workbench.h2_replay_service import available_snapshots, load_receipt
from workbench.market import BOARDS, completed_date
from .data import code_names
from .h2_replay_chart import render_replay
from .research_views import comparison_rows, outcome, receipt_time, scope_caption
from .toolbar import format_board_scope
from .theme import CHART_BG, CONTROL_BG, DOWN, MUTED, SELECTION, TEXT, UP


def number(value, suffix='', percent=False):
    if value is None:
        return '—'
    return f'{value * (100 if percent else 1):.2f}{suffix}'


def list_prices(record):
    """Only a live pending opportunity gets current pending prices."""
    if record['filled']:
        return record['entry'], record['stop'], record['initial_risk_pct']
    if record['status'] == 'pending':
        plan = record['latest_plan']
        return plan.get('entry'), plan.get('stop'), plan.get('initial_risk_pct')
    return None, None, None


class AfterhoursPage(ttk.Frame):
    def __init__(self, parent, service=None, store=None):
        super().__init__(parent, padding=8)
        self.service, self.store = service, store
        self.settings, self.costs = H2Settings(), Assumptions()
        self.records, self.report = [], {}
        self.job = None
        self.receipts = {}
        self._loaded = False
        self._group = '全部'
        self._trade = None
        self._event_index = 0
        self._image = self._photo = None
        self._chart_cache = {}
        self._fit_after = None
        self._detail_visible = False
        self._names = {}
        self._history_rows = {}
        self._compare_ids = []
        self._compare_vars = {}
        self._shown_job = None
        self._preview_trade = None
        self._preview_photo = None
        self._preview_after = None
        self._preview_index = 0
        self._preview_group = '全部'
        self._list_expanded = False
        self._sort_column, self._sort_desc = 'date', True
        self._visible_ids = []
        self.scope = tk.StringVar(value='market')
        self.board_vars = {b: tk.BooleanVar(value=True) for b in BOARDS}
        self.start = tk.StringVar(value=(date.fromisoformat(completed_date()) - timedelta(days=365)).isoformat())
        self.end = tk.StringVar(value=completed_date())
        self.status = tk.StringVar(value='请选择日期与范围；首次使用请先在盘前任务更新行情。')
        self.filter = tk.StringVar(value='全部')
        self.history = tk.StringVar()
        self.posthoc = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build()
        self._poll_id = self.after(350, self._poll)
        self.bind('<Destroy>', self._destroyed, add='+')

    def _build(self):
        from .afterhours_workspace import build_workspace
        build_workspace(self)

    def _build_detail(self):
        self.detail = ttk.Frame(self.host)
        self.detail.grid(row=0, column=0, sticky=tk.NSEW)
        self.detail.rowconfigure(1, weight=1)
        self.detail.columnconfigure(0, weight=1)
        nav = ttk.Frame(self.detail)
        nav.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        ttk.Button(nav, text='← 返回结果', command=self._back, bootstyle='secondary').pack(side=tk.LEFT)
        ttk.Button(nav, text='上一笔 ↑', command=lambda: self._step_trade(-1), bootstyle='secondary').pack(side=tk.LEFT, padx=6)
        ttk.Button(nav, text='下一笔 ↓', command=lambda: self._step_trade(1), bootstyle='secondary').pack(side=tk.LEFT)
        self.detail_title = tk.StringVar()
        ttk.Label(nav, textvariable=self.detail_title).pack(side=tk.LEFT, padx=12)
        ttk.Checkbutton(nav, text='完整走势（事后）', variable=self.posthoc, command=self._draw).pack(side=tk.RIGHT)
        for widget in nav.winfo_children():
            if isinstance(widget, ttk.Button):
                widget.configure(style='ResearchAction.TButton')
            elif 'font' in widget.keys():
                widget.configure(font=('Microsoft YaHei UI', -17))
        replay = ttk.Frame(self.detail)
        replay.grid(row=1, column=0, sticky=tk.NSEW)
        replay.rowconfigure(0, weight=1)
        replay.columnconfigure(0, weight=76, uniform='replay')
        replay.columnconfigure(1, weight=24, uniform='replay')
        chart_host = ttk.Frame(replay)
        chart_host.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        chart_host.grid_propagate(False)
        chart_host.columnconfigure(0, weight=1)
        chart_host.rowconfigure(1, weight=1)
        self.chart_readout = tk.StringVar(value='移到 K 线上查看开、高、低、收')
        readout = ttk.Label(chart_host, textvariable=self.chart_readout, foreground=MUTED,
                            font=('Microsoft YaHei UI', -14))
        readout.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        readout.bind('<Configure>', lambda e: readout.configure(wraplength=max(1, e.width)))
        self.chart = tk.Label(chart_host, bg=CHART_BG, fg=TEXT, text='选择一笔机会查看 K 线')
        self.chart.grid(row=1, column=0, sticky=tk.NSEW)
        self.chart.bind('<Configure>', self._schedule_fit)
        from .replay_navigation import ReplayNavigation
        self.chart_navigation = ReplayNavigation(self.chart, self._schedule_fit,
            lambda text: self.chart_readout.set(text or '移到 K 线上查看开、高、低、收'))
        ttk.Button(nav, text='重置视图', command=self.chart_navigation.reset,
                   style='ResearchAction.TButton').pack(side=tk.RIGHT, padx=8)
        timeline_host = ttk.Frame(replay)
        timeline_host.grid(row=0, column=1, sticky=tk.NSEW)
        timeline_host.rowconfigure(1, weight=1)
        timeline_host.columnconfigure(0, weight=1)
        timeline_heading = ttk.Label(timeline_host, text='逐日事件 · ← → 切换\n滚轮缩放 · 拖动价格轴 · 双击复位',
                                     font=('Microsoft YaHei UI', -14))
        timeline_heading.grid(row=0, column=0, sticky=tk.EW)
        timeline_heading.bind('<Configure>', lambda e: timeline_heading.configure(wraplength=max(1, e.width)))
        self.timeline = ttk.Treeview(timeline_host, columns=('date', 'kind'), show='headings', selectmode='browse', style='ResearchList.Treeview')
        self.timeline.heading('date', text='日期', anchor=tk.W)
        self.timeline.heading('kind', text='事件', anchor=tk.W)
        self.timeline.column('date', width=112, minwidth=100, stretch=False)
        self.timeline.column('kind', width=140, minwidth=80)
        self.timeline.grid(row=1, column=0, sticky=tk.NSEW, pady=6)
        scroll = ttk.Scrollbar(timeline_host, command=self.timeline.yview)
        scroll.grid(row=1, column=1, sticky=tk.NS)
        self.timeline.configure(yscrollcommand=scroll.set)
        self.timeline.bind('<<TreeviewSelect>>', self._event_selected)
        self.event_text = tk.StringVar()
        event_label = ttk.Label(timeline_host, textvariable=self.event_text, justify=tk.LEFT, foreground=MUTED)
        event_label.grid(row=2, column=0, sticky=tk.EW, pady=10)
        event_label.bind('<Configure>', lambda e: event_label.configure(wraplength=max(1, e.width)))
        self.detail.bind('<Up>', lambda e: self._step_trade(-1))
        self.detail.bind('<Down>', lambda e: self._step_trade(1))
        self.detail.bind('<Left>', lambda e: self._step_event(-1))
        self.detail.bind('<Right>', lambda e: self._step_event(1))
        self.detail.bind('<Escape>', lambda e: self._back())
        for widget in (self.timeline, self.chart):
            for key, command in [('<Up>', lambda e: self._step_trade(-1)),
                                 ('<Down>', lambda e: self._step_trade(1)),
                                 ('<Left>', lambda e: self._step_event(-1)),
                                 ('<Right>', lambda e: self._step_event(1)),
                                 ('<Escape>', lambda e: self._back())]:
                widget.bind(key, command)
        self.home.tkraise()

    def refresh(self):
        if self.store is None:
            return
        self._names = code_names(self.store)
        self._refresh_history()
        if not self._loaded and self.receipts:
            self.history.set(next(iter(self.receipts)))
            self._history_selected()
        self._loaded = True

    def _board_changed(self):
        self.scope.set('market')
        self._scope_changed()

    def _resize_research(self, event):
        if event.widget is not self:
            return
        compact = event.height < 1100
        if compact != self._compact_analysis:
            self._compact_analysis = compact
            for widget in self.boundary_trees.values():
                widget.configure(height=3 if compact else 5)
            self.histogram.configure(height=120 if compact else 178)
        scale = 1.25 if event.width >= 2000 else 1
        if scale == self._ui_scale:
            return
        self._ui_scale = scale
        for widget, font in self._font_widgets:
            if widget.winfo_exists():
                widget.configure(font=(font[0], round(int(font[1])*scale), *font[2:]))
        style = ttk.Style()
        style.configure('ResearchList.Treeview', rowheight=round(32*scale), font=('Microsoft YaHei UI', -round(17*scale)))
        style.configure('ResearchList.Treeview.Heading', font=('Microsoft YaHei UI', -round(17*scale), 'bold'))
        for name in ('ResearchAction.TButton', 'ResearchRun.TButton', 'ResearchScope.TMenubutton'):
            style.configure(name, font=('Microsoft YaHei UI', -round(17*scale)))
        self.history_host.configure(width=round(270*scale))
        self._render_history()
        self._histogram()
        self._layout_list()

    def _scope_changed(self):
        boards = [b for b, v in self.board_vars.items() if v.get()]
        caption = '关注名单' if self.scope.get() == 'watch' else format_board_scope(boards).replace('范围：', '')
        self.scope_button.configure(text='市场范围：' + caption)

    def _select_history(self, job):
        label = next((k for k, v in self.receipts.items() if v == job), None)
        if label:
            self.history.set(label)
            self._history_selected()

    def _render_history(self):
        position = self.history_canvas.yview()[0]
        for widget in self.history_cards.winfo_children():
            widget.destroy()
        self._compare_ids = [job for job in self._compare_ids if job in self._history_rows]
        self._compare_vars = {}
        for job, (row, report) in self._history_rows.items():
            bg = SELECTION if job == self._shown_job else CONTROL_BG
            card = tk.Frame(self.history_cards, bg=bg, padx=7, pady=8)
            card.configure(bg=bg)
            card.pack(fill=tk.X, pady=3)
            var = tk.BooleanVar(value=job in self._compare_ids)
            self._compare_vars[job] = var
            check = tk.Checkbutton(card, variable=var, command=lambda j=job: self._mark_comparison(j),
                                   bg=bg, activebackground=bg, selectcolor=bg)
            check.configure(bg=bg, activebackground=bg, selectcolor=bg)
            check.pack(side=tk.LEFT, anchor=tk.N)
            state = {'completed': '完成', 'partial': '部分', 'cancelled': '已停止'}.get(row['status'], '未完成')
            scope = scope_caption(report).replace('市场范围未记录（旧报告）', '旧报告 · 范围未记录')
            text = (receipt_time(row['created']) + ' · ' + state + '\n' + scope + '\n'
                    + report.get('start', '') + '\n至 ' + report.get('end', '') + '\n'
                    + f'{report.get("opportunities", 0)} 次机会 · {number(report.get("mean_r"), "R")}')
            widget = tk.Button(card, text=text, command=lambda j=job: self._select_history(j),
                               bg=bg, fg=TEXT, activebackground=SELECTION, activeforeground=TEXT,
                               relief=tk.FLAT, anchor=tk.W, justify=tk.LEFT, wraplength=round(210*self._ui_scale),
                               font=('Microsoft YaHei UI', -round(16*self._ui_scale)))
            widget.configure(bg=bg, fg=TEXT, activebackground=SELECTION, activeforeground=TEXT)
            widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            def wheel(event):
                self.history_canvas.yview_scroll(-int(event.delta/120), 'units')
                return 'break'
            for target in (card, check, widget):
                target.bind('<MouseWheel>', wheel)
        self.history_canvas.yview_moveto(position)
        self._comparison_state()

    def _comparison_state(self):
        count = len(self._compare_ids)
        self.compare_button.configure(state=tk.NORMAL if count == 2 else tk.DISABLED,
                                      text='对比已选 2 次 →' if count == 2 else f'已选 {count}/2 · 勾选后对比')

    def _mark_comparison(self, job):
        if self._compare_vars[job].get():
            if len(self._compare_ids) == 2:
                removed = self._compare_ids.pop(0)
                self._compare_vars[removed].set(False)
            self._compare_ids.append(job)
        else:
            self._compare_ids.remove(job)
        self._comparison_state()

    def _compare_reports(self):
        if len(self._compare_ids) != 2:
            return
        arow, a = self._history_rows[self._compare_ids[0]]
        brow, b = self._history_rows[self._compare_ids[1]]
        dialog = ttk.Toplevel(master=self)
        dialog.title('两次回测对比 · A − B')
        dialog.geometry('1100x700')
        dialog.transient(self.winfo_toplevel())
        dialog.lift()
        dialog.focus_set()
        warning = ('含部分结果，不能直接判断策略优劣。' if any(r['status'] != 'completed' for r in (arow, brow))
                   else '先核对条件和数据差异，再解读结果差异。')
        ttk.Label(dialog, text=warning + ' 同一市场名称也可能使用不同快照。',
                  font=('Microsoft YaHei UI', -17), padding=12).pack(anchor=tk.W)
        from .afterhours_workspace import tree
        table_host = ttk.Frame(dialog)
        table_host.pack(fill=tk.BOTH, expand=True, padx=12)
        widget = tree(table_host, [('field', '项目', 160, tk.W), ('a', 'A · '+receipt_time(arow['created']), 320, tk.W),
                               ('b', 'B · '+receipt_time(brow['created']), 320, tk.W), ('delta', 'A − B / 条件差异', 190, tk.W)])
        widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(table_host, command=widget.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        widget.configure(yscrollcommand=scroll.set)
        states = {'completed': '完整完成', 'partial': '部分结果', 'cancelled': '已停止 · 部分结果'}
        source = lambda report: '未记录' if 'real_data' not in report else '真实行情来源' if report['real_data'] else '导入研究／未认证'
        for values in [('任务状态', states.get(arow['status'], '未完成'), states.get(brow['status'], '未完成'), '—'),
                       ('来源口径', source(a), source(b), '—'),
                       *comparison_rows(a, b)]:
            widget.insert('', tk.END, values=values)
        # Long parameter cells can be inspected in full without widening the window.
        text = tk.Text(dialog, height=7, wrap='word', font=('Microsoft YaHei UI', -16))
        text.pack(fill=tk.X, padx=12, pady=10)
        text.insert('1.0', '选择一行查看完整内容。')
        text.configure(state=tk.DISABLED)
        def show(event):
            if not widget.selection():
                return
            values = widget.item(widget.selection()[0], 'values')
            text.configure(state=tk.NORMAL)
            text.delete('1.0', tk.END)
            text.insert('1.0', f'{values[0]}\nA：{values[1]}\nB：{values[2]}\n差异：{values[3]}')
            text.configure(state=tk.DISABLED)
        widget.bind('<<TreeviewSelect>>', show)
        dialog.bind('<Escape>', lambda e: dialog.destroy())

    def _set_category(self, value):
        self.category.set(value)
        self.filter.set('全部')
        self._fill_rows()

    def _sort_rows(self, column):
        self._sort_desc = not self._sort_desc if self._sort_column == column else True
        self._sort_column = column
        for key, title in zip(self.tree['columns'], ['H2 日期', '代码', '名称', '成交/待挂', 'SL1', '风险', '结局', '净 R']):
            self.tree.heading(key, text=title + (' ↓' if self._sort_desc else ' ↑') if key == column else title)
        self._fill_rows()

    def _layout_list(self, event=None):
        from tkinter.font import Font
        font = Font(self, font=ttk.Style().lookup('ResearchList.Treeview', 'font'))
        heading_font = Font(self, font=ttk.Style().lookup('ResearchList.Treeview.Heading', 'font'))
        columns = self.tree['displaycolumns']
        if columns == ('#all',):
            columns = self.tree['columns']
        samples = dict(date='2026-10-04', code='sz.300870', name='中远海能', entry='1234.56',
                       stop='1234.56', risk='28.40%', result='触发未成交', r='-10.00R')
        widths = {key: max(font.measure(samples[key]), heading_font.measure(self.tree.heading(key, 'text'))) + 22
                  for key in columns}
        total = sum(widths.values())
        if not self._list_expanded:
            self.review.columnconfigure(0, minsize=min(total+32, round(self.review.winfo_width()*.62)))
        available = max(1, self.tree.winfo_width()-20)
        factor = max(1, available/total) if self._list_expanded else 1
        for key, width in widths.items():
            self.tree.column(key, width=round(width*factor), minwidth=width, stretch=False)

    def _toggle_list(self):
        self.home.tkraise()
        self._list_expanded = not self._list_expanded
        self.expand_button.configure(text='收起清单' if self._list_expanded else '展开清单')
        if self._list_expanded:
            self.list_host.grid_configure(columnspan=2)
            self.list_host.tkraise()
            self.tree.configure(displaycolumns=self.tree['columns'])
        else:
            self.list_host.grid_configure(columnspan=1)
            self.preview_host.tkraise()
            self.tree.configure(displaycolumns=('date', 'code', 'entry', 'risk', 'result', 'r'))
            self._schedule_preview()
        self._layout_list()

    def _preview_selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        record = next((r for r in self.records if r['id'] == selected[0]), None)
        if record is None:
            return
        if self._preview_trade and self._preview_trade['id'] == record['id']:
            return
        self._preview_trade, self._preview_index = record, 0
        self.preview_navigation.reset(False)
        self._preview_group = '全部'
        self.preview_title.set(f'{record["code"]} · {self._names.get(record["code"], "")} · H2 {record["setup_date"]}')
        events = [f'{e["date"]} · {e.get("text", e["kind"])}' for e in record['events']]
        self.preview_event_box.configure(values=events)
        if events:
            self.preview_event_box.current(0)
        self._schedule_preview()

    def _preview_event_selected(self, event=None):
        self._preview_index = self.preview_event_box.current()
        self.preview_navigation.info = {}
        self.preview_readout.set('移到 K 线上查看开、高、低、收')
        self._schedule_preview()

    def _schedule_preview(self, event=None):
        if self._preview_after:
            self.after_cancel(self._preview_after)
        self._preview_after = self.after(150, self._draw_preview)

    def _draw_preview(self):
        self._preview_after = None
        if not self._preview_trade or self._preview_index < 0 or self._list_expanded:
            return
        try:
            self.update_idletasks()
            size = (max(100, self.preview_chart.winfo_width()), max(100, self.preview_chart.winfo_height()))
            image = self._render_image(self._preview_trade, self._preview_index, False, size, self.preview_navigation.viewport)
            self.preview_navigation.accept(image)
            self._preview_photo = ImageTk.PhotoImage(image)
            self.preview_chart.configure(image=self._preview_photo, text='')
        except Exception as exc:
            self._preview_photo = None
            self.preview_navigation.reset(False)
            self.preview_chart.configure(image='', text=f'K 线读取失败：{exc}')

    def _open_preview(self):
        if self._preview_trade:
            index = self._preview_index
            self._group = self._preview_group
            self._open(self._preview_trade['id'])
            self.timeline.selection_set(str(index))
            self._event_index = index
            self._draw()

    def _preview_boundary(self, group):
        selection = self.boundary_trees[group].selection()
        if not selection:
            return
        record = next((r for r in self.records if r['id'] == selection[0]), None)
        if record is None:
            return
        # The sample remains inspectable even when a category hides its row.
        self._preview_trade = record
        self.preview_navigation.reset(False)
        self._preview_group = group
        self._preview_index = 0
        self.preview_title.set(f'{record["code"]} · H2 {record["setup_date"]}')
        values = [f'{e["date"]} · {e.get("text", e["kind"])}' for e in record['events']]
        self.preview_event_box.configure(values=values)
        if values:
            self.preview_event_box.current(0)
        self._schedule_preview()

    def _refresh_history(self):
        rows = self.store.rows("SELECT id,status,created,result FROM jobs WHERE kind='backtest' "
                               "AND status NOT IN ('queued','running') ORDER BY created DESC,rowid DESC")
        self.receipts = {}
        self._history_rows = {}
        for row in rows:
            try:
                report = json.loads(row['result'])
            except (ValueError, TypeError):
                continue
            if report.get('execution_model') == MODEL:
                state = {'completed': '完成', 'partial': '部分完成', 'cancelled': '已停止'}.get(row['status'], '未完成')
                label = f'{row["created"][:16]} · {state} · {row["id"][:5]}'
                self.receipts[label] = row['id']
                self._history_rows[row['id']] = (row, report)
        self.history_box.configure(values=list(self.receipts))
        self._render_history()

    def _history_selected(self, _event=None):
        if self.history.get() not in self.receipts:
            return
        try:
            row, report, records = load_receipt(self.store, self.receipts[self.history.get()])
            self._shown_job = row['id']
            self._show_report(row, report, records)
            self._render_history()
        except Exception as exc:
            self.status.set(f'回执读取失败：{exc}')

    def _run(self):
        if self.service is None or self.store is None:
            return
        try:
            start, end = self.start.get(), self.end.get()
            date.fromisoformat(start)
            date.fromisoformat(end)
            if start > end or end > completed_date():
                raise ValueError('请使用有效的已完成行情日期范围')
            scope = self.scope.get()
            boards = [b for b, var in self.board_vars.items() if var.get()] if scope == 'market' else None
            snapshots, missing = available_snapshots(self.store, scope, boards)
            if missing:
                raise ValueError(f'关注名单中 {len(missing)} 个标的未存日线，请先更新行情')
            if not snapshots:
                raise ValueError('没有可回放的真实行情；请先更新行情')
            self.job = self.service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily',
                execution_model=MODEL, datasets=[r['id'] for r in snapshots], start=start, end=end,
                scope=scope, boards=boards, h2_settings=asdict(self.settings), assumptions=asdict(self.costs)))
            self._back()
            self.progress.grid()
            self.completeness.set('新回测进行中；这里保留上次选中的结果，完成后展示新回执。')
            self.run_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.status.set(f'准备回放 {len(snapshots)} 个已存标的；未保存的市场行情不在本次范围内')
        except Exception as exc:
            messagebox.showerror('不能开始回测', str(exc), parent=self)

    def _stop(self):
        if self.job:
            self.service.cancel(self.job)
            self.status.set('正在停止；保留已完整回放的标的，结果会标记为部分。')

    def _poll(self):
        if self.job:
            rows = self.store.rows('SELECT * FROM jobs WHERE id=?', (self.job,))
            if rows:
                row = rows[0]
                self.status.set(row['message'])
                self.progress['value'] = row['progress'] / max(row['total'], 1) * 100
                if row['status'] not in ('running', 'queued'):
                    finished = self.job
                    self.job = None
                    self.progress.grid_remove()
                    self.run_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.history_box.configure(state='readonly')
                    self._refresh_history()
                    label = next((k for k, v in self.receipts.items() if v == finished), None)
                    if label:
                        self.history.set(label)
                        self._history_selected()
                    else:
                        self.completeness.set('本次未生成可用回执；旧结果保留。' + row['message'])
        self._poll_id = self.after(350, self._poll)

    def _show_report(self, row, report, records):
        self.report, self.records = report, records
        self._trade = self._preview_trade = None
        self.preview_navigation.reset(False)
        self.chart_navigation.reset(False)
        self._preview_photo = None
        self.preview_chart.configure(image='', text='选择一笔机会看 K 线')
        self.preview_event_box.configure(values=[])
        self.preview_event.set('')
        self.category.set('全部')
        self._chart_cache.clear()
        self._image = self._photo = None
        self.filter.set('全部')
        self.home.tkraise()
        self._detail_visible = False
        status = {'completed': '完成', 'partial': '部分完成', 'cancelled': '已停止'}.get(row['status'], row['status'])
        self.result_title.set(f'{self._shown_job[:6] if self._shown_job else "本次"} 回测结果 · {status}')
        self.result_context.set(scope_caption(report) + f' · {report["start"]} → {report["end"]}')
        self.status.set(f'{status} · {report["start"]} 至 {report["end"]} · '
                        f'已回放 {report["processed_datasets"]}/{report["requested_datasets"]} 个标的 · '
                        f'覆盖提示 {len(report["coverage_warnings"])} · 失败 {len(report["errors"])}'
                        + (' · 导入研究，来源未认证' if not report['real_data'] else ''))
        performance = report.get('performance')
        if performance:
            self.status.set(self.status.get() + f' · 耗时 {performance["elapsed_seconds"]:.1f} 秒'
                            + f' · 复用 {performance["reused"]} 个标的')
        values = [str(report['opportunities']), str(report['filled']),
                  number(report['win_rate'], '%', True), number(report['mean_r'], 'R'),
                  number(report['median_risk_pct'], '%')]
        for var, value in zip(self.stats, values):
            var.set(value)
        self.completeness.set(f'触发 {report["triggered"]} · 模拟成交 {report["filled"]} · '
                              f'触发未成交 {report["unfilled"]} · 已结束 {report["closed_trades"]} · '
                              f'持仓未结束 {report["counts"]["open"]} · 条件过滤 {report["counts"]["filtered"]}')
        counts = report['counts']
        self.filter_box.configure(values=['全部', *[f'{label} ({counts[key]})' for key, label in LABELS.items()]])
        self._fill_rows()
        lookup = {r['id']: r for r in records}
        for group, tree in self.boundary_trees.items():
            tree.delete(*tree.get_children())
            for identity in report['boundaries'][group]:
                r = lookup[identity]
                tree.insert('', tk.END, iid=identity, values=(f'{r["code"]} / {r["setup_date"][5:]}', number(r['r_multiple'], 'R')), tags=(group,))
        labels = ['< -2R', '-2 至 -1R', '-1 至 0R', '0 至 1R', '1 至 2R', '≥ 2R']
        self.distribution.set('\n'.join(f'{label:12}  {count} 笔' for label, count in zip(labels, report['distribution'])))
        self._histogram()
        self._layout_list()

    def _histogram(self):
        self.histogram.delete('all')
        values = self.report.get('distribution', [])
        if not values or not sum(values):
            self.histogram.create_text(10, 45, text='没有已结束交易', fill=MUTED, anchor=tk.W)
            return
        width = max(240, self.histogram.winfo_width())
        maximum = max(values)
        for i, value in enumerate(values):
            step = min(27*self._ui_scale, max(20, self.histogram.winfo_height()/6))
            y = step/2 + i * step
            self.histogram.create_text(0, y, text=['< −2R', '−2～−1', '−1～0', '0～1', '1～2', '≥ 2R'][i],
                                       fill=MUTED, anchor=tk.W, font=('Microsoft YaHei UI', -round(14*self._ui_scale)))
            left, right = 68, width - 72
            self.histogram.create_rectangle(left, y-4, right, y+4, fill=CONTROL_BG, outline='')
            self.histogram.create_rectangle(left, y-4, left+(right-left)*value/maximum, y+4,
                                            fill=DOWN if i < 3 else UP, outline='')
            self.histogram.create_text(width-48, y, text=str(value), fill=TEXT, anchor=tk.E, font=('Consolas', -15))
            self.histogram.create_text(width-2, y, text=f'{value/sum(values):.0%}', fill=MUTED, anchor=tk.E,
                                       font=('Consolas', -15))

    def _fill_rows(self):
        old = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        selected = self.filter.get().split(' (')[0]
        rows = []
        for r in self.records:
            if selected != '全部' and LABELS[r['status']] != selected:
                continue
            category = self.category.get()
            if category == '已结束' and r['status'] != 'closed' or category == '未成交' and r['filled']:
                continue
            if category == '持仓未结束' and r['status'] != 'open':
                continue
            rows.append(r)
        def key(r):
            entry, stop, risk = list_prices(r)
            return {'date': r['setup_date'], 'code': r['code'], 'name': self._names.get(r['code'], ''),
                    'entry': entry, 'stop': stop, 'risk': risk, 'result': outcome(r), 'r': r.get('r_multiple')}[self._sort_column]
        rows = sorted([r for r in rows if key(r) is not None], key=key, reverse=self._sort_desc) + [r for r in rows if key(r) is None]
        self._visible_ids = [r['id'] for r in rows]
        for r in rows:
            entry, stop, risk = list_prices(r)
            self.tree.insert('', tk.END, iid=r['id'], values=(r['setup_date'], r['code'], self._names.get(r['code'], ''),
                             number(entry), number(stop), number(risk, '%'), outcome(r), number(r.get('r_multiple'), 'R')),
                             tags=('profit',) if (r.get('r_multiple') or 0)>0 else ('loss',) if (r.get('r_multiple') or 0)<0 else ())
        if old and self.tree.exists(old[0]):
            self.tree.selection_set(old[0])
        elif rows:
            self.tree.selection_set(rows[0]['id'])
        else:
            self._preview_trade = self._preview_photo = None
            self.preview_navigation.reset(False)
            self.preview_chart.configure(image='', text='当前分类没有机会')
            self.preview_title.set('当前分类没有机会')
            self.preview_event_box.configure(values=[])
            self.preview_event.set('')
        self._layout_list()

    def _open_selected(self):
        selected = self.tree.selection()
        if selected:
            self._group = '全部'
            self._open(selected[0])
        return 'break'

    def _open_boundary(self, group):
        selected = self.boundary_trees[group].selection()
        if selected:
            self._group = group
            self._open(selected[0])
        return 'break'

    def _open(self, identity):
        self._trade = next(r for r in self.records if r['id'] == identity)
        self.chart_navigation.reset(False)
        self._event_index = 0
        self.posthoc.set(False)
        self.detail.tkraise()
        self._detail_visible = True
        self.detail.focus_set()
        self.detail_title.set(f'{self._trade["code"]} · H2 {self._trade["setup_date"]} · {LABELS[self._trade["status"]]}')
        self.timeline.delete(*self.timeline.get_children())
        titles = {'setup': 'H2 成立', 'plan': '更新次日计划', 'trigger': '价格触发', 'fill': '模拟成交',
                  'holding': '持仓', 'exit': '模拟退出', 't1': 'T+1 限制', 'blocked': '成交受限'}
        for i, event in enumerate(self._trade['events']):
            self.timeline.insert('', tk.END, iid=str(i), values=(event['date'], titles.get(event['kind'], LABELS.get(event['kind'], event['kind']))))
        self.timeline.selection_set('0')
        self._draw()

    def _back(self):
        self.home.tkraise()
        self._detail_visible = False
        if self._trade:
            identity = self._trade['id']
            tree = self.tree if self._group == '全部' else self.boundary_trees[self._group]
            if tree.exists(identity):
                tree.selection_set(identity)
                tree.see(identity)
                tree.focus(identity)
                tree.focus_set()

    def _step_trade(self, delta):
        ids = list(self.tree.get_children()) if self._group == '全部' else self.report['boundaries'][self._group]
        if self._trade and self._trade['id'] in ids:
            i = ids.index(self._trade['id']) + delta
            if 0 <= i < len(ids):
                self._open(ids[i])
        return 'break'

    def _step_event(self, delta):
        if self._trade:
            i = self._event_index + delta
            if 0 <= i < len(self._trade['events']):
                self.timeline.selection_set(str(i))
                self.timeline.see(str(i))
        return 'break'

    def _event_selected(self, _event=None):
        selected = self.timeline.selection()
        if selected:
            self._event_index = int(selected[0])
            self._draw()

    def _draw(self):
        if not self._trade:
            return
        event = self._trade['events'][self._event_index]
        self.event_text.set(event['date'] + '\n' + event['text'] + '\n\n' +
                            ('事后走势：图中含此事件之后的数据' if self.posthoc.get() else '图中仅有此日及之前的数据'))
        try:
            self._fit()
        except Exception as exc:
            self._image = self._photo = None
            self.chart_navigation.reset(False)
            self.chart.configure(image='', text=f'K 线读取失败：{exc}')

    def _schedule_fit(self, _event=None):
        if self._fit_after:
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(100, self._fit)

    def _fit(self):
        self._fit_after = None
        if self._trade is None:
            return
        self.update_idletasks()
        size = (max(self.chart.winfo_width(), 100), max(self.chart.winfo_height(), 100))
        try:
            self._image = self._render_image(self._trade, self._event_index, self.posthoc.get(), size, self.chart_navigation.viewport)
            self.chart_navigation.accept(self._image)
            self._photo = ImageTk.PhotoImage(self._image)
            self.chart.configure(image=self._photo, text='')
        except Exception as exc:
            self._image = self._photo = None
            self.chart_navigation.reset(False)
            self.chart.configure(image='', text=f'K 线读取失败：{exc}')

    def _render_image(self, record, event, posthoc, size, viewport=(None, 0, 1.0)):
        key = (record['id'], event, posthoc, size, viewport)
        if key not in self._chart_cache:
            self._chart_cache[key] = render_replay(self.store, record, event, posthoc, size=size, viewport=viewport)
            if len(self._chart_cache) > 12:
                self._chart_cache.pop(next(iter(self._chart_cache)))
        return self._chart_cache[key]

    def _settings_dialog(self):
        fields = [('lookback', '突破观察窗口（根）'), ('min_pullback', '最短回调（根）'),
                  ('max_pullback', '最长回调（根）'), ('wait_bars', 'H2 后等待期限（根）'),
                  ('max_risk_pct', '初始风险上限（%，0 不筛选）'), ('min_mm_r', '最低 MM 收益（R，0 不筛选）')]
        self._parameter_dialog('GAP H2 · 本次研究条件', self.settings, fields, H2Settings, 'settings',
                               '只作用于下次回测；不改变盘前正式策略。\n风险与 MM 是额外筛选条件，在 H2 成立日执行。')

    def _parameter_dialog(self, title, obj, fields, cls, attribute, note):
        dialog = ttk.Toplevel(master=self)
        dialog.title(title)
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, padding=20)
        content.pack(fill=tk.BOTH, expand=True)
        variables = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(content, text=label).grid(row=i, column=0, sticky=tk.W, pady=5)
            var = tk.StringVar(value=str(getattr(obj, key)))
            ttk.Entry(content, textvariable=var, width=14).grid(row=i, column=1, padx=12)
            variables[key] = var
        ttk.Label(content, text=note, foreground=MUTED, wraplength=460).grid(row=len(fields), column=0, columnspan=2, pady=15)
        def save():
            try:
                values = asdict(obj)
                for key, var in variables.items():
                    values[key] = int(var.get()) if type(getattr(obj, key)) is int else float(var.get())
                changed = cls(**values)
                changed.validate()
                setattr(self, attribute, changed)
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror('设置需要调整', str(exc), parent=dialog)
        ttk.Button(content, text='应用到下次回测', command=save).grid(row=len(fields) + 1, column=1)
        ttk.Button(content, text='恢复默认', command=lambda: [var.set(str(getattr(cls(), key))) for key, var in variables.items()],
                   bootstyle='secondary').grid(row=len(fields) + 1, column=0, sticky=tk.W)

    def _receipt_dialog(self):
        dialog = ttk.Toplevel(master=self)
        dialog.title('成交口径与研究条件')
        dialog.transient(self.winfo_toplevel())
        box = ttk.Frame(dialog, padding=18)
        box.pack(fill=tk.BOTH, expand=True)
        receipt = self.report
        settings = receipt.get('h2_settings', asdict(self.settings))
        costs = receipt.get('assumptions', asdict(self.costs))
        text = ('此结果的研究条件（未运行时显示下次条件）：\n'
                + f'突破观察 {settings["lookback"]} 根；回调 {settings["min_pullback"]}～{settings["max_pullback"]} 根；'
                + f'H2 后等待 {settings["wait_bars"]} 根\n'
                + f'风险上限 {settings["max_risk_pct"]}%；最低 MM {settings["min_mm_r"]}R（0 表示不筛选）\n\n'
                + f'成交假设：最长持有 {costs["holding_bars"]} 个交易日；单边佣金 {costs["commission_bps"]} 基点；'
                + f'卖出税费 {costs["sell_tax_bps"]} 基点；单边滑点 {costs["slippage_bps"]} 基点\n'
                + '1 基点 = 0.01%；假设值可按自己的交易成本调整。\n\n'
                + '\n'.join(receipt.get('limitations', ['仅使用已保存真实日线；保守成交，A 股 T+1。'])))
        widget = tk.Text(box, width=90, height=25, wrap='word', font=('Microsoft YaHei UI', -17))
        widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(box, command=widget.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        widget.configure(yscrollcommand=scroll.set)
        widget.insert('1.0', text)
        widget.configure(state=tk.DISABLED)
        ttk.Button(dialog, text='调整下次成交假设', command=lambda: self._parameter_dialog(
            '下次回测 · 成交假设', self.costs,
            [('holding_bars', '最长持有（交易日）'), ('commission_bps', '单边佣金（基点）'),
             ('sell_tax_bps', '卖出税费（基点）'), ('slippage_bps', '单边滑点（基点）')],
            Assumptions, 'costs', '1 基点 = 0.01%；这是研究假设，不代表实际收费或成交。'),
            bootstyle='secondary').pack(pady=10)

    def _data_receipt_dialog(self):
        receipt = self.report
        text = ('覆盖提示与失败：\n' + '\n'.join(
            f'{r.get("code", r.get("dataset", ""))}：{r.get("reason", r.get("error", ""))}'
            for r in receipt.get('coverage_warnings', []) + receipt.get('errors', []))
            + '\n\n所用行情：\n' + '\n'.join(f'{r["code"]} · {r["start"]} 至 {r["end"]} · '
                f'{r["source"]} · {r["adjustment"]}' for r in receipt.get('datasets', []))
            + '\n\n报告位置：' + receipt.get('report_path', '未运行')
            + '\n回放版本校验：' + receipt.get('engine_version', '未运行'))
        dialog = ttk.Toplevel(master=self)
        dialog.title('当前回测 · 数据回执')
        dialog.transient(self.winfo_toplevel())
        box = ttk.Frame(dialog, padding=18)
        box.pack(fill=tk.BOTH, expand=True)
        widget = tk.Text(box, width=90, height=28, wrap='word', font=('Microsoft YaHei UI', -17))
        widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(box, command=widget.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        widget.configure(yscrollcommand=scroll.set)
        widget.insert('1.0', text)
        widget.configure(state=tk.DISABLED)

    def _destroyed(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)
            if self._fit_after:
                self.after_cancel(self._fit_after)
            if self._preview_after:
                self.after_cancel(self._preview_after)
