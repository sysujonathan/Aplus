"""Desktop research workspace: report history, compact analysis, paired replay."""
import tkinter as tk
import ttkbootstrap as ttk

from workbench.market import BOARDS
from workbench.h2_replay import LABELS
from .theme import ACCENT, BORDER, CHART_BG, CONTROL_BG, DOWN, MUTED, PANEL_BG, SELECTION, TEXT, UP


def label(parent, text='', variable=None, size=17, color=TEXT, bold=False):
    widget = ttk.Label(parent, text=text, textvariable=variable, foreground=color,
                       font=('Microsoft YaHei UI', -size, 'bold' if bold else 'normal'))
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


def panel(parent, caption, color=TEXT):
    outer = ttk.Frame(parent, padding=10, style='ResearchPanel.TFrame')
    label(outer, caption, bold=True, color=color).pack(anchor=tk.W, pady=(0, 6))
    return outer


def build_workspace(page):
    p = page
    style = ttk.Style()
    style.configure('ResearchPanel.TFrame', background=PANEL_BG, borderwidth=1, relief='solid')
    style.configure('ResearchPanel.TLabel', background=PANEL_BG)
    style.configure('ResearchList.Treeview', rowheight=32, font=('Microsoft YaHei UI', -17),
                    background=PANEL_BG, fieldbackground=PANEL_BG, foreground=TEXT)
    style.configure('ResearchList.Treeview.Heading', font=('Microsoft YaHei UI', -17, 'bold'))
    for name, bg in [('ResearchAction.TButton', CONTROL_BG), ('ResearchRun.TButton', ACCENT)]:
        style.configure(name, font=('Microsoft YaHei UI', -17), padding=(8, 5), background=bg,
                        foreground=TEXT, bordercolor=BORDER)
        style.map(name, background=[('active', SELECTION)], foreground=[('disabled', MUTED)])
    style.configure('ResearchScope.TMenubutton', font=('Microsoft YaHei UI', -17), padding=(8, 5),
                    background=CONTROL_BG, foreground=TEXT, bordercolor=BORDER)
    bar = ttk.Frame(p)
    bar.grid(row=0, column=0, sticky=tk.EW)
    left = ttk.Frame(bar)
    left.pack(side=tk.LEFT)
    label(left, 'GAP H2 · 日线').pack(side=tk.LEFT, padx=(0, 12))
    p.scope_button = ttk.Menubutton(left, style='ResearchScope.TMenubutton')
    p.scope_button.pack(side=tk.LEFT, padx=(0, 12))
    menu = tk.Menu(p.scope_button, tearoff=False, font=('Microsoft YaHei UI', -17))
    for board in BOARDS:
        menu.add_checkbutton(label=board, variable=p.board_vars[board], command=p._board_changed)
    menu.add_separator()
    menu.add_radiobutton(label='关注名单', variable=p.scope, value='watch', command=p._scope_changed)
    p.scope_button.configure(menu=menu)
    p._scope_changed()
    for caption, var in [('从', p.start), ('至', p.end)]:
        label(left, caption).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(left, textvariable=var, width=10, font=('Microsoft YaHei UI', -17)).pack(side=tk.LEFT, padx=(0, 10))
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
    receipt = ttk.Frame(p)
    receipt.grid(row=1, column=0, sticky=tk.EW, pady=8)
    p.status_label = label(receipt, variable=p.status, color=MUTED, size=16)
    p.status_label.pack(fill=tk.X)
    p.status_label.bind('<Configure>', lambda e: p.status_label.configure(wraplength=max(1, e.width)))
    p.progress = ttk.Progressbar(p, maximum=100, mode='determinate')
    p.progress.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
    p.progress.grid_remove()
    p.host = ttk.Frame(p)
    p.host.grid(row=3, column=0, sticky=tk.NSEW)
    p.host.columnconfigure(1, weight=1)
    p.host.rowconfigure(0, weight=1)
    p.history_host = history = ttk.Frame(p.host, width=270, padding=(0, 0, 12, 0))
    history.grid(row=0, column=0, sticky=tk.NSEW)
    history.grid_propagate(False)
    label(history, '回测记录', bold=True, size=19).pack(anchor=tk.W, pady=(0, 6))
    p.compare_button = button(history, '勾选两次回测后对比', p._compare_reports, True)
    p.compare_button.configure(state=tk.DISABLED)
    p.compare_button.pack(fill=tk.X, pady=(0, 8))
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
    title = ttk.Frame(p.home)
    title.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
    p.result_title = tk.StringVar(value='选择范围与区间，新建回测')
    label(title, variable=p.result_title, bold=True, size=20).pack(side=tk.LEFT)
    p.result_context = tk.StringVar(value='研究结果与真实交易记录独立')
    p.context_label = label(title, variable=p.result_context, color=MUTED, size=15)
    p.context_label.pack(side=tk.RIGHT)
    metrics = ttk.Frame(p.home)
    metrics.grid(row=1, column=0, sticky=tk.EW, pady=(0, 8))
    p.stats = []
    for i, caption in enumerate(['机会数', '模拟成交', '胜率 · 已结束', '平均净 R', '初始风险中位数']):
        metrics.columnconfigure(i, weight=1, uniform='metric')
        card = ttk.Frame(metrics, padding=(10, 6), style='ResearchPanel.TFrame')
        card.grid(row=0, column=i, sticky=tk.EW, padx=(0, 8 if i<4 else 0))
        label(card, caption, color=MUTED, size=16).pack(anchor=tk.W)
        var = tk.StringVar(value='—')
        label(card, variable=var, size=30, bold=True).pack(anchor=tk.W)
        p.stats.append(var)
    analysis = ttk.Frame(p.home)
    analysis.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
    p.boundary_trees = {}
    for i, (group, caption) in enumerate([('profit', 'Top 盈利 · 净 R'), ('loss', 'Top 亏损 · 净 R')]):
        analysis.columnconfigure(i, weight=1, uniform='analysis')
        box = panel(analysis, caption, UP if group == 'profit' else DOWN)
        box.grid(row=0, column=i, sticky=tk.NSEW)
        widget = tree(box, [('code', '代码 / H2 日期', 155, tk.W), ('r', '净 R', 70, tk.E)])
        widget.pack(fill=tk.BOTH, expand=True)
        widget.bind('<Double-1>', lambda e, g=group: p._open_boundary(g))
        widget.bind('<Return>', lambda e, g=group: p._open_boundary(g))
        widget.bind('<<TreeviewSelect>>', lambda e, g=group: p._preview_boundary(g))
        p.boundary_trees[group] = widget
    analysis.columnconfigure(2, weight=1, uniform='analysis')
    box = panel(analysis, '净 R 分布 · 已结束')
    box.grid(row=0, column=2, sticky=tk.NSEW)
    p.histogram = tk.Canvas(box, height=178, bg=PANEL_BG, highlightthickness=0, width=240)
    p.histogram.configure(bg=PANEL_BG)
    p.histogram.pack(fill=tk.BOTH, expand=True)
    p.histogram.bind('<Configure>', lambda e: p._histogram())
    p.distribution = tk.StringVar()
    filters = ttk.Frame(p.home)
    filters.grid(row=3, column=0, sticky=tk.EW, pady=(0, 8))
    label(filters, '逐笔复盘', bold=True).pack(side=tk.LEFT, padx=(0, 8))
    p.category = tk.StringVar(value='全部')
    for caption in ['全部', '已结束', '未成交', '持仓未结束']:
        button(filters, caption, lambda c=caption: p._set_category(c)).pack(side=tk.LEFT, padx=3)
    p.filter_box = ttk.Combobox(filters, textvariable=p.filter, values=['全部', *LABELS.values()],
                               width=12, state='readonly', font=('Microsoft YaHei UI', -16))
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
                          ('entry', '成交/待挂', 110, tk.E), ('stop', 'SL1', 82, tk.E),
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
    preview.rowconfigure(1, weight=1)
    preview.columnconfigure(0, weight=1)
    preview.grid_propagate(False)
    head = ttk.Frame(preview)
    head.grid(row=0, column=0, sticky=tk.EW)
    p.preview_title = tk.StringVar(value='选择一笔机会看 K 线')
    label(head, variable=p.preview_title, size=16).pack(side=tk.LEFT)
    button(head, '完整复盘 ↗', p._open_preview).pack(side=tk.RIGHT)
    p.preview_chart = tk.Label(preview, bg=CHART_BG, fg=TEXT, text='选择清单或 Top 样本', font=('Microsoft YaHei UI', -17))
    p.preview_chart.configure(bg=CHART_BG, fg=TEXT)
    p.preview_chart.grid(row=1, column=0, sticky=tk.NSEW, pady=6)
    p.preview_chart.bind('<Configure>', p._schedule_preview)
    from .replay_navigation import ReplayNavigation
    p.preview_navigation = ReplayNavigation(p.preview_chart, p._schedule_preview)
    p.preview_event = tk.StringVar()
    p.preview_event_box = ttk.Combobox(preview, textvariable=p.preview_event, state='readonly', font=('Microsoft YaHei UI', -16))
    p.preview_event_box.grid(row=2, column=0, sticky=tk.EW)
    p.preview_event_box.bind('<<ComboboxSelected>>', p._preview_event_selected)
    label(preview, '滚轮缩放 · 拖动价格轴 · 双击复位', size=14, color=MUTED).grid(row=3, column=0, sticky=tk.W, pady=6)
    p.completeness = tk.StringVar(value='尚未运行，不展示演示交易。')
    p.completeness_label = label(p.home, variable=p.completeness, size=15, color=MUTED)
    p.completeness_label.grid(row=5, column=0, sticky=tk.EW, pady=(8, 0))
    p.completeness_label.bind('<Configure>', lambda e: p.completeness_label.configure(wraplength=max(1, e.width)))
    p._build_detail()
    p.detail.grid_configure(column=1)
    p.home.tkraise()
    p._font_widgets = []
    def collect(widget):
        if 'font' in widget.keys():
            font = p.tk.splitlist(str(widget.cget('font')))
            if len(font) > 1 and str(font[1]).startswith('-'):
                p._font_widgets.append((widget, font))
        for child in widget.winfo_children():
            collect(child)
    collect(p)
    p._ui_scale = 1
    p._compact_analysis = None
    p.bind('<Configure>', p._resize_research, add='+')
