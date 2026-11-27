"""Desktop research workspace: report history, compact analysis, paired replay."""
from datetime import date
import tkinter as tk
from tkinter import messagebox, simpledialog
import ttkbootstrap as ttk

from workbench.market import BOARDS
from workbench.h2_replay import LABELS
from .theme import ACCENT, ACCENT_HOVER, BORDER, CHART_BG, CONTROL_BG, DOWN, HOVER, MUTED, PANEL_BG, TEXT, UP, UI_FONT


def label(parent, text='', variable=None, size=10, color=TEXT, bold=False):
    widget = ttk.Label(parent, text=text, textvariable=variable, foreground=color,
                       font=(UI_FONT, size, 'bold' if bold else 'normal'))
    if isinstance(parent, ttk.Frame) and parent.cget('style') == 'ResearchPanel.TFrame':
        widget.configure(style='ResearchPanel.TLabel')
    return widget


def button(parent, text, command, primary=False):
    return ttk.Button(parent, text=text, command=command,
                      style='ResearchRun.TButton' if primary else 'ResearchAction.TButton')


def tree(parent, columns, height=5):
    widget = ttk.Treeview(parent, columns=[c[0] for c in columns], show='headings',
                         selectmode='browse', height=height, style='ResearchList.Treeview')
    for key, caption, width, anchor in columns:
        widget.heading(key, text=caption, anchor=anchor)
        widget.column(key, width=width, minwidth=width, anchor=anchor, stretch=key in ('name', 'result', 'code'))
    widget.tag_configure('profit', foreground=UP)
    widget.tag_configure('loss', foreground=DOWN)
    return widget


def panel(parent, caption, color=TEXT, *, size=11, padding=10):
    outer = ttk.Frame(parent, padding=padding, style='ResearchPanel.TFrame')
    label(outer, caption, bold=True, color=color, size=size).pack(anchor=tk.W, pady=(0, 6))
    return outer


def backtest_display_name(row):
    display = row['display']
    return (f'{display["number"]:02d} · {display["name"]}' if display['name']
            else f'回测 {display["number"]:02d}')


def latest_research_row(history):
    return next((row for row in history
                 if not row['display']['deleted'] and row['display'].get('research_note')), None)


def build_latest_research(parent, open_report, *, columnspan=1):
    summary = button(parent, '', None)
    summary.configure(width=1)
    summary.grid(row=3, column=0, columnspan=columnspan, sticky=tk.EW, pady=(0, 6))
    summary.grid_remove()

    def refresh(history):
        row = latest_research_row(history)
        if row is None:
            summary.grid_remove()
            return
        note = row['display']['research_note']
        first_line = note['conclusion'].split('\n', 1)[0]
        preview = first_line[:32] + ('…' if len(first_line) > 32 else '')
        details = ' · '.join(part for part in (backtest_display_name(row), note['date'], preview) if part)
        summary.configure(text=f'最新研究 · {details}', command=lambda: open_report(row))
        summary.grid()

    return summary, refresh


class ResearchNoteDialog(simpledialog.Dialog):
    def __init__(self, parent, note, save):
        self.note, self.save = note, save
        self.inputs = {}
        super().__init__(parent, '研究记录')

    def body(self, master):
        master.columnconfigure(1, weight=1)
        for index, (field, caption) in enumerate([
                ('hypothesis', '假设'), ('conclusion', '结论'), ('next_step', '下一步'),
                ('author', '作者'), ('date', '日期 YYYY-MM-DD')]):
            label(master, caption, size=15).grid(row=index, column=0, sticky=tk.NW, padx=8, pady=4)
            if index < 3:
                entry = tk.Text(master, width=48, height=4, wrap=tk.WORD,
                                bg=CONTROL_BG, fg=TEXT, insertbackground=TEXT)
                entry.insert('1.0', self.note.get(field, ''))
            else:
                entry = ttk.Entry(master)
                entry.insert(0, self.note.get(field, date.today().isoformat() if field == 'date' else ''))
            entry.grid(row=index, column=1, sticky=tk.EW, padx=8, pady=4)
            self.inputs[field] = entry
        label(master, '三项正文各最多 500 字，作者最多 40 字。清空全部字段后保存可移除记录。',
              size=14, color=MUTED).grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=8)
        return self.inputs['hypothesis']

    def validate(self):
        note = {field: entry.get('1.0', 'end-1c') if isinstance(entry, tk.Text) else entry.get()
                for field, entry in self.inputs.items()}
        try:
            self.save(note)
        except ValueError as exc:
            messagebox.showerror('不能保存研究记录', str(exc), parent=self)
            return False
        self.result = True
        return True


def build_workspace(page):
    p = page
    style = ttk.Style()
    style.configure('ResearchPanel.TFrame', background=PANEL_BG, borderwidth=1, relief='solid')
    style.configure('ResearchPanel.TLabel', background=PANEL_BG)
    style.configure('ResearchPanelBody.TFrame', background=PANEL_BG)
    dpi = max(1, p.winfo_fpixels('1i') / 96)
    style.configure('ResearchList.Treeview', rowheight=round(32*dpi), font=(UI_FONT, 10),
                    background=PANEL_BG, fieldbackground=PANEL_BG, foreground=TEXT)
    style.configure('ResearchList.Treeview.Heading', font=(UI_FONT, 10, 'bold'))
    style.configure('ResearchBoundary.Treeview', rowheight=round(25*dpi), font=(UI_FONT, 10),
                    background=PANEL_BG, fieldbackground=PANEL_BG, foreground=TEXT)
    style.configure('ResearchBoundary.Treeview.Heading', font=(UI_FONT, 10, 'bold'))
    style.configure('Holding.Toolbutton', font=(UI_FONT, 10), padding=(3, 2))
    for name, bg in [('ResearchAction.TButton', CONTROL_BG), ('ResearchRun.TButton', ACCENT)]:
        style.configure(name, font=(UI_FONT, 10), padding=(8, 5), background=bg,
                        foreground=TEXT, bordercolor=BORDER)
        style.map(name, background=[('active', ACCENT_HOVER if bg == ACCENT else HOVER)], foreground=[('disabled', MUTED)])
    style.configure('ResearchScope.TMenubutton', font=(UI_FONT, 10), padding=(8, 5),
                    background=CONTROL_BG, foreground=TEXT, bordercolor=BORDER)
    bar = ttk.Frame(p)
    bar.pack_propagate(False)
    bar.grid(row=0, column=0, sticky=tk.EW)
    left = ttk.Frame(bar)
    left.pack(side=tk.LEFT)
    label(left, 'GAP H2 · 日线', size=11, bold=True).pack(side=tk.LEFT, padx=(0, 12))
    p.scope_button = ttk.Menubutton(left, style='ResearchScope.TMenubutton')
    p.scope_button.pack(side=tk.LEFT, padx=(0, 12))
    menu = tk.Menu(p.scope_button, tearoff=False, font=(UI_FONT, 10))
    for board in BOARDS:
        menu.add_checkbutton(label=board, variable=p.board_vars[board], command=p._board_changed)
    menu.add_separator()
    menu.add_radiobutton(label='关注名单', variable=p.scope, value='watch', command=p._scope_changed)
    menu.add_radiobutton(label='长历史研究快照', variable=p.scope, value='research', command=p._scope_changed)
    p.scope_button.configure(menu=menu)
    p._scope_changed()
    for caption, var in [('从', p.start), ('至', p.end)]:
        label(left, caption).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(left, textvariable=var, width=10, font=(UI_FONT, 10)).pack(side=tk.LEFT, padx=(0, 10))
    button(left, '研究条件', p._settings_dialog).pack(side=tk.LEFT)
    actions = ttk.Frame(bar)
    actions.pack(side=tk.RIGHT)
    button(actions, '成交口径', p._receipt_dialog).pack(side=tk.LEFT, padx=6)
    button(actions, '数据回执', p._data_receipt_dialog).pack(side=tk.LEFT, padx=6)
    p.stop_button = button(actions, '停止', p._stop)
    p.stop_button.configure(state=tk.DISABLED)
    p.stop_button.pack(side=tk.LEFT, padx=6)
    p.run_button = button(actions, '新建回测', p._run, True)
    p.run_button.pack(side=tk.LEFT)
    def reflow(_event, bar=bar, left=left, actions=actions):
        narrow = bar.winfo_width() < left.winfo_reqwidth() + actions.winfo_reqwidth() + 16
        left.pack_configure(side=tk.TOP if narrow else tk.LEFT, anchor=tk.W)
        actions.pack_configure(side=tk.TOP if narrow else tk.RIGHT, anchor=tk.E)
        bar.configure(height=(left.winfo_reqheight()+actions.winfo_reqheight() if narrow else
                              max(left.winfo_reqheight(),actions.winfo_reqheight()))+4)
    bar.bind('<Configure>', reflow)
    left.bind('<Configure>', reflow)
    actions.bind('<Configure>', reflow)
    p._reflow_toolbar = reflow
    p.after_idle(lambda:reflow(None))
    receipt = ttk.Frame(p)
    receipt.grid(row=1, column=0, sticky=tk.EW, pady=8)
    p.status_label = label(receipt, variable=p.status, color=MUTED, size=9)
    p.status_label.pack(fill=tk.X)
    p.status_label.bind('<Configure>', lambda e: p.status_label.configure(wraplength=max(1, e.width)))
    p.progress = ttk.Progressbar(p, maximum=100, mode='determinate')
    p.progress.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
    p.progress.grid_remove()
    p.latest_research, p._refresh_latest_research = build_latest_research(p, p.open_research_report)
    p.host = ttk.Frame(p._research_body)
    p.host.grid(row=0, column=0, sticky=tk.NSEW)
    p.host.columnconfigure(1, weight=1)
    p.host.rowconfigure(0, weight=1)
    p.history_host = history = ttk.Frame(p.host, width=270, padding=(0, 0, 12, 0))
    history.grid(row=0, column=0, sticky=tk.NSEW)
    history.grid_propagate(False)
    label(history, '回测记录', bold=True, size=11).pack(anchor=tk.W, pady=(0, 6))
    p.compare_button = button(history, '勾选两次回测后对比', p._compare_reports, True)
    p.compare_button.configure(state=tk.DISABLED)
    p.compare_button.pack(fill=tk.X, pady=(0, 8))
    actions = ttk.Frame(history)
    actions.pack(fill=tk.X, pady=(0,8))
    p.rename_button = button(actions, '重命名', p._rename_history)
    p.rename_button.pack(side=tk.LEFT)
    p.research_note_button = button(actions, '研究记录', p._edit_research_note)
    p.research_note_button.pack(side=tk.LEFT, padx=4)
    p.delete_button = button(actions, '删除', p._delete_history)
    p.delete_button.pack(side=tk.RIGHT)
    p.restore_button = button(history, '已删除 / 恢复', p._restore_history_dialog)
    p.restore_button.pack(fill=tk.X, pady=(0,8))
    label(history, '点名称查看 · 勾选两次对比', size=9, color=MUTED).pack(anchor=tk.W,pady=(0,6))
    area = ttk.Frame(history)
    area.pack(fill=tk.BOTH, expand=True)
    p.history_canvas = tk.Canvas(area, bg=PANEL_BG, highlightthickness=0, width=210)
    p.history_canvas.configure(bg=PANEL_BG)
    p.history_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll = ttk.Scrollbar(area, command=p.history_canvas.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    p.history_canvas.configure(yscrollcommand=scroll.set)
    p.history_cards = tk.Frame(p.history_canvas, bg=PANEL_BG)
    p.history_cards.configure(bg=PANEL_BG)
    win = p.history_canvas.create_window((0, 0), window=p.history_cards, anchor=tk.NW)
    p.history_canvas.bind('<Configure>', lambda e: p.history_canvas.itemconfigure(win, width=e.width))
    p.history_cards.bind('<Configure>', lambda e: p.history_canvas.configure(scrollregion=p.history_canvas.bbox('all')))
    # Retain the variable/selector interface used by receipt polling, without
    # displaying a second history picker.
    p.history_box = ttk.Combobox(history, textvariable=p.history, state='readonly')
    p.home = ttk.Frame(p.host)
    p.home.grid(row=0, column=1, sticky=tk.NSEW)
    p.home.columnconfigure(0, weight=1)
    p.home.rowconfigure(4, weight=1)
    header = ttk.Frame(p.home)
    header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
    title = ttk.Frame(header)
    title.pack(fill=tk.X)
    p.result_title = tk.StringVar(value='选择范围与区间，新建回测')
    label(title, variable=p.result_title, bold=True, size=11).pack(side=tk.LEFT)
    p.result_context = tk.StringVar(value='研究结果与真实交易记录独立')
    p.context_label = label(title, variable=p.result_context, color=MUTED, size=10)
    p.annual_button = button(title, '分年统计', p._show_annual_statistics)
    p.annual_button.configure(state=tk.DISABLED)
    p.annual_button.pack(side=tk.RIGHT, padx=(8, 0))
    p.context_label.pack(side=tk.RIGHT)
    p.research_note_header = ttk.Frame(header)
    p.research_conclusion = label(p.research_note_header, size=15)
    p.research_details = label(p.research_note_header, size=14, color=MUTED)
    for widget in (p.research_conclusion, p.research_details):
        widget.pack(fill=tk.X)
        widget.configure(justify=tk.LEFT)
        widget.bind('<Configure>', lambda e: e.widget.configure(wraplength=max(1, e.width)))
    metrics = ttk.Frame(p.home)
    metrics.grid(row=1, column=0, sticky=tk.EW, pady=(0, 8))
    p.stats = []
    for i, caption in enumerate(['机会数', '模拟成交', '胜率 · 已结束', '平均净 R',
                                 '持仓K线中位数', '初始风险中位数']):
        metrics.columnconfigure(i, weight=1, uniform='metric')
        card = ttk.Frame(metrics, padding=(8, 4), style='ResearchPanel.TFrame')
        card.grid(row=0, column=i, sticky=tk.EW, padx=(0, 6 if i<5 else 0))
        label(card, caption, color=MUTED, size=9).pack(anchor=tk.W)
        var = tk.StringVar(value='—')
        label(card, variable=var, size=21, bold=True).pack(anchor=tk.W)
        p.stats.append(var)
    p.analysis = analysis = ttk.Frame(p.home)
    analysis.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
    p.boundary_trees = {}
    for i, (group, caption) in enumerate([('profit', 'Top 盈利 · 净 R'), ('loss', 'Top 亏损 · 净 R')]):
        analysis.columnconfigure(i, weight=1, uniform='analysis')
        box = panel(analysis, caption, UP if group == 'profit' else DOWN, size=10, padding=(6, 4))
        box.grid(row=0, column=i, sticky=tk.NSEW)
        widget = tree(box, [('code', '代码 / H2 日期', 155, tk.W), ('r', '净 R', 70, tk.E)])
        widget.configure(style='ResearchBoundary.Treeview')
        widget.pack(fill=tk.BOTH, expand=True)
        widget.bind('<Double-1>', lambda e, g=group: p._open_boundary(g))
        widget.bind('<Return>', lambda e, g=group: p._open_boundary(g))
        widget.bind('<<TreeviewSelect>>', lambda e, g=group: p._preview_boundary(g))
        p.boundary_trees[group] = widget
    analysis.columnconfigure(2, weight=1, uniform='analysis')
    box = panel(analysis, '净 R 分布 · 已结束', size=10, padding=(6, 4))
    box.grid(row=0, column=2, sticky=tk.NSEW)
    p.histogram = tk.Canvas(box, height=178, bg=PANEL_BG, highlightthickness=0, width=240)
    p.histogram.configure(bg=PANEL_BG)
    p.histogram.pack(fill=tk.BOTH, expand=True)
    p.histogram.bind('<Configure>', lambda e: p._histogram())
    p.distribution = tk.StringVar()
    analysis.columnconfigure(3, weight=1, uniform='analysis')
    holding = ttk.Frame(analysis, padding=(6, 4), style='ResearchPanel.TFrame')
    holding.grid(row=0, column=3, sticky=tk.NSEW)
    heading = ttk.Frame(holding, style='ResearchPanelBody.TFrame')
    heading.pack(fill=tk.X, pady=(0, 6))
    label(heading, '持仓K线分布 · 已结束', bold=True, size=10).pack(side=tk.LEFT)
    p.holding_category = tk.StringVar(value='全部')
    p.holding_buttons = {}
    for caption in ('亏损', '盈利', '全部'):
        btn = ttk.Radiobutton(heading, text=caption, variable=p.holding_category, value=caption,
                              style='Holding.Toolbutton', command=p._holding_changed)
        btn.pack(side=tk.RIGHT)
        p.holding_buttons[caption] = btn
    p.holding_histogram = tk.Canvas(holding, height=120, bg=PANEL_BG, highlightthickness=0, width=220)
    p.holding_histogram.pack(fill=tk.BOTH, expand=True)
    p.holding_histogram.bind('<Configure>', lambda e: p._holding_histogram())
    auxiliary = ttk.Frame(analysis)
    auxiliary.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(6, 0))
    p.aux_stats, p.aux_labels, p.aux_captions = {}, {}, {}
    for i, (key, caption, color, weight) in enumerate([
            ('win', '平均盈利R', UP, 1), ('loss', '平均亏损R', DOWN, 1),
            ('exits', '退出原因分布', MUTED, 2), ('excess', '超额亏损占比', MUTED, 2)]):
        auxiliary.columnconfigure(i, weight=weight, uniform='aux')
        box = ttk.Frame(auxiliary, padding=(8, 3), style='ResearchPanel.TFrame')
        box.grid(row=0, column=i, sticky=tk.NSEW, padx=(0, 4 if i<3 else 0))
        box.pack_propagate(False)
        box.configure(height=58)
        caption_label = label(box, caption, color=color, size=10)
        caption_label.pack(anchor=tk.W)
        var = tk.StringVar(value='—')
        value = label(box, variable=var, color=color if key in ('win', 'loss') else TEXT,
                      size=11 if key in ('win', 'loss') else 10, bold=key in ('win', 'loss'))
        value.pack(fill=tk.X)
        def fit_auxiliary(event, w=value, host=box, title=caption_label):
            w.configure(wraplength=max(1, event.width))
            small = getattr(p, '_compact_auxiliary', False)
            content_height = (max(title.winfo_reqheight(), w.winfo_reqheight()) if small else
                              title.winfo_reqheight()+w.winfo_reqheight())
            host.configure(height=max(round((30 if small else 48)*getattr(p, '_ui_scale', 1)),
                                      content_height+8))
        value.bind('<Configure>', fit_auxiliary)
        p.aux_stats[key], p.aux_labels[key] = var, value
        p.aux_captions[key] = caption_label
    filters = ttk.Frame(p.home)
    filters.grid(row=3, column=0, sticky=tk.EW, pady=(0, 8))
    label(filters, '逐笔复盘', size=11, bold=True).pack(side=tk.LEFT, padx=(0, 8))
    p.category = tk.StringVar(value='全部')
    for caption in ['全部', '已结束', '未成交', '持仓未结束']:
        button(filters, caption, lambda c=caption: p._set_category(c)).pack(side=tk.LEFT, padx=3)
    p.filter_box = ttk.Combobox(filters, textvariable=p.filter, values=['全部', *LABELS.values()],
                               width=12, state='readonly', font=(UI_FONT, 10))
    p.filter_box.pack(side=tk.LEFT, padx=6)
    p.filter_box.bind('<<ComboboxSelected>>', lambda e: p._fill_rows())
    p.expand_button = button(filters, '展开清单', p._toggle_list)
    p.expand_button.pack(side=tk.RIGHT)
    p.review = ttk.Frame(p.home)
    p.review.grid(row=4, column=0, sticky=tk.NSEW)
    p.review.grid_propagate(False)
    p.review.rowconfigure(0, weight=1)
    p.review.columnconfigure(0, weight=0)
    p.review.columnconfigure(1, weight=1)
    p.review.bind('<Configure>', p._layout_list)
    p.list_host = table = ttk.Frame(p.review)
    table.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))
    table.columnconfigure(0, weight=1)
    table.rowconfigure(0, weight=1)
    table.grid_propagate(False)
    p.tree = tree(table, [('date', 'H2 日期', 112, tk.W), ('code', '代码', 106, tk.W), ('name', '名称', 105, tk.W),
                          ('entry', '成交/待挂', 110, tk.E), ('stop', 'SL', 82, tk.E),
                          ('risk', '风险', 78, tk.E), ('result', '结局', 114, tk.W), ('r', '净 R', 70, tk.E)])
    p.tree.configure(displaycolumns=('date', 'code', 'entry', 'risk', 'result', 'r'))
    for key in p.tree['columns']:
        p.tree.heading(key, command=lambda c=key: p._sort_rows(c))
    p.tree.grid(row=0, column=0, sticky=tk.NSEW)
    scroll = ttk.Scrollbar(table, command=p.tree.yview)
    scroll.grid(row=0, column=1, sticky=tk.NS)
    p.tree.configure(yscrollcommand=scroll.set)
    horizontal = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=p.tree.xview)
    horizontal.grid(row=1, column=0, sticky=tk.EW)
    p.tree.configure(xscrollcommand=horizontal.set)
    p.tree.bind('<Double-1>', lambda e: p._open_selected())
    p.tree.bind('<Return>', lambda e: p._open_selected())
    p.tree.bind('<<TreeviewSelect>>', p._preview_selected)
    p.preview_host = preview = ttk.Frame(p.review)
    preview.grid(row=0, column=1, sticky=tk.NSEW)
    preview.rowconfigure(2, weight=1)
    preview.columnconfigure(0, weight=1)
    preview.grid_propagate(False)
    head = ttk.Frame(preview)
    head.grid(row=0, column=0, sticky=tk.EW)
    p.preview_title = tk.StringVar(value='选择一笔机会看 K 线')
    label(head, variable=p.preview_title, size=10).pack(side=tk.LEFT)
    button(head, '完整复盘 ↗', p._open_preview).pack(side=tk.RIGHT)
    p.preview_readout = tk.StringVar(value='移到 K 线上查看开、高、低、收')
    readout = label(preview, variable=p.preview_readout, color=MUTED, size=9)
    readout.grid(row=1, column=0, sticky=tk.EW, pady=4)
    readout.bind('<Configure>', lambda e: readout.configure(wraplength=max(1, e.width)))
    p.preview_chart = tk.Label(preview, bg=CHART_BG, fg=TEXT, text='选择清单或 Top 样本', font=(UI_FONT, 10))
    p.preview_chart.configure(bg=CHART_BG, fg=TEXT)
    p.preview_chart.grid(row=2, column=0, sticky=tk.NSEW, pady=6)
    p.preview_chart.bind('<Configure>', p._schedule_preview)
    from .replay_navigation import ReplayNavigation
    p.preview_navigation = ReplayNavigation(p.preview_chart, p._schedule_preview,
        lambda text: p.preview_readout.set(text or '移到 K 线上查看开、高、低、收'))
    p.preview_event = tk.StringVar()
    p.preview_event_box = ttk.Combobox(preview, textvariable=p.preview_event, state='readonly', font=(UI_FONT, 10))
    p.preview_event_box.grid(row=3, column=0, sticky=tk.EW)
    p.preview_event_box.bind('<<ComboboxSelected>>', p._preview_event_selected)
    label(preview, '滚轮缩放 · 拖动价格轴 · 双击复位', size=9, color=MUTED).grid(row=4, column=0, sticky=tk.W, pady=6)
    p.completeness = tk.StringVar(value='尚未运行，不展示演示交易。')
    p.completeness_label = label(p.home, variable=p.completeness, size=10, color=MUTED)
    p.completeness_label.grid(row=5, column=0, sticky=tk.EW, pady=(8, 0))
    p.completeness_label.bind('<Configure>', lambda e: p.completeness_label.configure(wraplength=max(1, e.width)))
    p._build_detail()
    p.detail.grid_configure(column=1)
    p.home.tkraise()
    p._ui_scale = 1
    p._compact_analysis = None
    p.bind('<Configure>', p._resize_research, add='+')
