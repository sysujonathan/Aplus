"""右侧观察池（P2：watchlist JOIN observations/plans，读写只走 store 方法）。

读：watchlist LEFT JOIN observations（取策略/信号日）LEFT JOIN plans（取人工状态）。
写：加入关注来自左栏候选右键（main_window 调 store.watch）；
    本面板右键菜单：修改备注（update_watch）/ 切换计划状态（save_plan）/ 结束关注（update_watch）。
单击行 -> on_select(code, observation_id) 替换活动图；拖拽可替换指定图格。
零 schema 改动：全部复用 store 现有表与方法。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter import font as tkfont
import ttkbootstrap as ttk

from .theme import APP_BG, PANEL_BG, TEXT
from workbench.watch_views import WatchViews, FLAGS, COLUMNS, import_codes, export_rows
from .tree_scroll import (
    attach_vertical_scrollbar,
    AutoHideScrollbar,
    numeric_stock_code,
)

# 与 store.save_plan 的合法状态保持一致（勿在此处增删）
_PLAN_STATES = ("观察", "计划交易", "已手工入场", "已手工退出", "忽略")


class WatchPanel(ttk.Frame):
    def __init__(self, parent, store=None, on_select=None, on_drag_motion=None,
                 on_drop=None, on_drag_end=None, on_page_request=None, on_alert=None, on_compare=None, auto_quotes=None):
        super().__init__(parent)
        self.store = store
        self.on_select = on_select
        self.on_drag_motion = on_drag_motion
        self.on_drop = on_drop
        self.on_drag_end = on_drag_end
        self.on_page_request = on_page_request
        self.on_alert, self.on_compare = on_alert, on_compare
        self._auto_quotes = auto_quotes
        from workbench.holding_quotes import QuotePoller
        self._live_poller = QuotePoller()
        self._live_due = 0
        self._live_codes = ()
        self._display_rows = []
        self._drag_item = None
        self._drag_origin = None
        self._dragging = False
        self.views = WatchViews(store)
        self._sort = (None, False)
        self._quotes = {}
        self._history_quotes = {}
        self._live_quotes = {}
        self._quote_results = queue.Queue()
        self._quote_generation = 0
        self._quote_busy = False

        heading = ttk.Frame(self)
        heading.pack(fill=tk.X, pady=(0, 6))
        self.add_button = ttk.Button(heading, text='+', width=3, command=self._add_manual,
                                     bootstyle='primary-outline')
        self.add_button.pack(side=tk.RIGHT)
        self.list_name = tk.StringVar()
        self.list_button = ttk.Menubutton(heading, textvariable=self.list_name, bootstyle='secondary')
        self.list_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.settings_button = ttk.Button(heading, text='⋯', width=3, command=self._settings_menu, bootstyle='secondary-outline')
        self.settings_button.pack(side=tk.RIGHT, padx=3)
        self.query = tk.StringVar()
        search = ttk.Entry(self, textvariable=self.query)
        search.pack(fill=tk.X, pady=(0, 5))
        search.bind('<Return>', self._quick_add)
        self.query.trace_add('write', lambda *_: self._render_rows())
        from ttkbootstrap.widgets import ToolTip
        ToolTip(search, text='输入代码或名称筛选；按 Enter 加入当前自选表。拖动行可排序，拖出列表可放到图格。')
        self._rebuild_list_menu()

        self.tree_host = ttk.Frame(self)
        self.tree_host.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            self.tree_host,
            columns=COLUMNS,
            displaycolumns=self.views.columns(),
            show="headings",
        )
        for col, text, width, stretch in (
            ('flag', '标记', 38, False),
            ("number", "序", 44, False),
            ("code", "代码", 100, False),
            ("name", "名称", 156, False),
            ("strategy", "策略", 52, False),
            ("date", "信号日", 56, False),
            ("state", "状态", 52, False),
            ("notes", "备注", 40, True),
            ('price', '最新价', 76, False), ('change', '涨跌额', 76, False),
            ('percent', '涨跌幅', 76, False), ('asof', '行情时间', 120, False),
        ):
            self.tree.heading(col, text=text, command=lambda c=col: self._sort_column(c))
            minwidth = 32 if col in ('flag', 'number', 'notes') else 72 if col != 'name' else 100
            self.tree.column(
                col, width=width, minwidth=minwidth,
                anchor=tk.W if col in ('name', 'notes', 'state') else tk.E if col in ('price', 'change', 'percent') else tk.CENTER,
                stretch=col == 'name',
            )
        self.vscroll = attach_vertical_scrollbar(self.tree_host, self.tree)
        horizontal = AutoHideScrollbar(self.tree_host, orient=tk.HORIZONTAL, command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind('<Configure>', self._fit_columns, add='+')
        self.tree.tag_configure("row_even", background=PANEL_BG)
        self.tree.tag_configure("row_odd", background=APP_BG)
        self.tree._obs = {}    # iid -> observation_id
        self.tree._codes = {}  # iid -> code
        for color, value in FLAGS.items():
            self.tree.tag_configure('flag:' + color, foreground=value)
        self.hint = tk.StringVar(value='自选表设置无法读取，原设置已保留；暂用全部关注' if self.views.error else '拖动排序 · 拖出列表放入图格')
        ttk.Label(self, textvariable=self.hint, foreground='#9BA7B4', wraplength=360).pack(fill=tk.X, pady=3)
        self.tree.bind('<Motion>', self._hover_row, add='+')
        self._hover_code = None
        self._remove_hover = ttk.Button(self.tree, text='×', width=2, bootstyle='secondary',
            command=lambda: self._remove_from_list(self._hover_code) if self._hover_code else None)
        self._alert_hover = ttk.Button(self.tree, text='♧', width=2, bootstyle='secondary',
            command=lambda: self.on_alert(self._hover_code) if self.on_alert and self._hover_code else None)
        ToolTip(self._alert_hover, text='在当前活动图添加提醒')
        ToolTip(self._remove_hover, text='从当前自选表移除')
        self.tree.bind('<Return>', self._on_select)
        self.tree.bind('<Alt-Return>', self._on_select)
        self.tree.bind('<Delete>', lambda _e: self._remove_selected())

        # 单击在松开时切换活动图；拖拽时不先误改活动图。
        # 右键操作菜单
        self.tree.bind("<Button-3>", self._context_menu)
        # 左键仍可直接切换活动图；按住拖到某个图格则精确替换该格。
        self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._drag_release, add="+")
        self.tree.bind("<Left>", lambda _event: self._request_page(-1))
        self.tree.bind("<Right>", lambda _event: self._request_page(1))
        self.tree.bind("<Up>", lambda _event: self._move_selection(-1))
        self.tree.bind("<Down>", lambda _event: self._move_selection(1))

        if self.store is not None:
            self.reload()
        self._quote_timer = self.after(200, self._poll_quotes)
        self.bind('<Destroy>', self._destroyed, add='+')

    # ---- 读：观察池（active=1 的关注 + 关联观察与计划状态）----
    def reload(self):
        """从 store 重读观察池。任何写操作后由主窗口/本面板调用刷新。"""
        self._all_rows = []
        if self.store is None:
            return
        try:
            rows = self.store.rows(
                "SELECT w.code AS code, w.notes AS wnotes, "
                "o.id AS observation_id, o.strategy AS strategy, o.asof AS asof, "
                "o.timeframe AS timeframe, d.source AS source, "
                "p.state AS state "
                "FROM watchlist w "
                "LEFT JOIN observations o ON o.id = w.observation_id "
                "LEFT JOIN datasets d ON d.id = o.dataset_id "
                "LEFT JOIN plans p ON p.observation_id = o.id "
                "WHERE w.active=1 ORDER BY w.updated DESC"
            )
        except Exception:
            return
        from .data import code_names, latest_market_dataset

        names = code_names(self.store)
        names = {**{r['key'][len('watch_name:'):]: r['value'] for r in self.store.rows(
            "SELECT key,value FROM meta WHERE key LIKE 'watch_name:%'")}, **names}
        held = {r['code'] for r in self.store.rows("SELECT DISTINCT code FROM positions WHERE status='OPEN'")}
        for number, r in enumerate(rows, 1):
            code = r["code"]
            state = r["state"] if r["state"] else "关注中"
            timeframe = r["timeframe"] or "daily"
            # 关注的是股票；旧 observation 只保存当初为什么关注。K 线始终
            # 指向本项目正式行情源的最新快照，且不会读取工程 A 数据源。
            from workbench.watch_history import chart_source
            manual = not r['observation_id']
            source = chart_source(self.store, code) if manual else None
            market = latest_market_dataset(self.store, code, timeframe=timeframe, source=source)
            item = {
                "code": code,
                "name": names.get(code, ""),
                "observation_id": r["observation_id"],
                "source": (market or {}).get("source") or source or r["source"] or "unknown",
                "strategy": r["strategy"],
                "timeframe": timeframe,
                "market_dataset_id": (market or {}).get("id"),
                "market_asof": (market or {}).get("end"),
                "anchor_asof": r["asof"],
                "manual": manual,
                'state': ('实际持仓 · ' if code in held else '') + state,
                'notes': r['wnotes'] or '', 'date': (r['asof'] or '')[:10],
            }
            self._all_rows.append(item)
        self._render_rows()
        self._request_quotes()

    def _render_rows(self):
        if not hasattr(self, 'tree'):
            return
        self._remove_hover.place_forget()
        self._alert_hover.place_forget()
        selected = self.tree._codes.get(next(iter(self.tree.selection()), ''))
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.tree._obs, self.tree._codes = {}, {}
        rows = self.views.visible(getattr(self, '_all_rows', []), self.query.get())
        column, reverse = self._sort
        if column:
            rows.sort(key=lambda row: self._sort_key(row, column), reverse=reverse)
        self._display_rows = rows
        groups = self.views.data['sections'].get(self.views.data['active'], {})
        order = list(dict.fromkeys(groups.get(row['code'], '') for row in rows))
        rows = [row for group in order for row in rows if groups.get(row['code'], '') == group]
        self._display_rows = rows
        shown_group = None
        for number, row in enumerate(rows, 1):
            code = row['code']
            group = groups.get(code, '')
            if group and group != shown_group:
                heading = {c:'' for c in COLUMNS}
                heading['name'] = '── ' + group
                self.tree.insert('',tk.END,values=[heading[c] for c in COLUMNS],tags=('row_even',))
            shown_group = group
            quote = self._quotes.get(code, {})
            price, previous = quote.get('price'), quote.get('previous_close')
            delta = price - previous if price is not None and previous else None
            from .trade_management import _price
            values = dict(row, number=number, code=numeric_stock_code(code),
                flag='●' if self.views.data['flags'].get(code) else '·',
                price=_price(price, code), change=_price(delta, code),
                percent=f'{delta / previous * 100:+.2f}%' if delta is not None else '—',
                asof=(str(quote.get('quote_time') or quote.get('date', '—')) +
                      (' · 复权收盘' if quote.get('adjusted') else ' · 收盘' if quote.get('source')=='history' else
                       ' · 腾讯' if quote.get('source')=='tencent' else ' · 新浪' if quote.get('source')=='sina' else '')),
                notes='✎' if row['notes'] else '',
                strategy=row['strategy'] or '手动关注')
            tags = ['row_even' if number % 2 == 0 else 'row_odd']
            flag = self.views.data['flags'].get(code)
            if flag:
                tags.append('flag:' + flag)
            iid = self.tree.insert('', tk.END, values=[values.get(c, '') for c in COLUMNS], tags=tags)
            self.tree._obs[iid], self.tree._codes[iid] = row['observation_id'], code
        if selected:
            self.select_code(selected)
        self._fit_columns()

    def _fit_columns(self, _event=None):
        """Reserve readable compact fields; give the name all remaining space."""
        style = ttk.Style()
        body = tkfont.Font(root=self.tree, font=style.lookup('Treeview', 'font') or 'TkDefaultFont')
        heading = tkfont.Font(root=self.tree, font=style.lookup('Treeview.Heading', 'font') or 'TkDefaultFont')
        state = max({str(row.get('state') or '关注') for row in self._display_rows},
                    key=body.measure, default='关注')
        samples = dict(flag='●', number='9999', code='000000', state=state,
                       notes='✎', strategy='手动关注', date='2026-11-04', price='999.999',
                       change='+99.999', percent='+99.99%', asof='11-04 15:00:00')
        widths = {}
        for column in self.tree['displaycolumns']:
            if column == 'name':
                continue
            width = max(heading.measure(self.tree.heading(column, 'text')),
                        body.measure(samples.get(column, ''))) + 16
            widths[column] = width
            self.tree.column(column, width=width, minwidth=width, stretch=False)
        minimum = body.measure('证券名称名称') + 16
        available = max(minimum, self.tree.winfo_width() - 4 - sum(widths.values()))
        self.tree.column('name', width=available, minwidth=minimum, stretch=True)

    def _sort_key(self, row, column):
        quote = self._quotes.get(row['code'], {})
        if column in ('price', 'change', 'percent'):
            value, prev = quote.get('price'), quote.get('previous_close')
            if column != 'price':
                value = (value - prev) * (100 / prev if column == 'percent' else 1) if value is not None and prev else None
            return value is not None, value or 0
        return str(self.views.data['flags'].get(row['code'], '') if column == 'flag' else row.get(column, '')).casefold()

    def _sort_column(self, column):
        self._sort = (column, not self._sort[1] if self._sort[0] == column else False)
        self._render_rows()

    def _request_quotes(self):
        if self._quote_busy or not set(self.views.columns()) & {'price', 'change', 'percent', 'asof'}:
            return
        self._quote_busy = True
        self._quote_generation += 1
        generation = self._quote_generation
        codes = [row['code'] for row in self._all_rows]
        def read():
            from workbench.holding_quotes import saved_quotes
            try:
                result = saved_quotes(self.store, codes)
                self._quote_results.put((generation, result, '本地历史收盘；行情日期见时间列，非实时报价'))
            except Exception:
                self._quote_results.put((generation, {}, '行情暂不可读，关注记录不受影响'))
        threading.Thread(target=read, daemon=True).start()

    def _poll_quotes(self):
        try:
            while True:
                generation, quotes, hint = self._quote_results.get_nowait()
                self._quote_busy = False
                if generation == self._quote_generation:
                    self._history_quotes = quotes
                    self._select_quote_display()
                    self.hint.set(hint)
                    self._render_rows()
        except queue.Empty:
            pass
        enabled = bool(self._auto_quotes() if callable(self._auto_quotes) else self._auto_quotes)
        price_columns = set(self.views.columns()) & {'price','change','percent','asof'}
        wanted = tuple(sorted(r['code'] for r in self._display_rows[:80])) if enabled and price_columns and self.winfo_ismapped() else ()
        if wanted != self._live_codes:
            self._live_codes = wanted
            self._live_poller.invalidate()
            self._live_due = 0
        result = self._live_poller.take()
        if result:
            quotes, failed = result
            for code, quote in quotes.items():
                if quote.get('quote_time','') >= self._live_quotes.get(code,{}).get('quote_time',''):
                    self._live_quotes[code] = quote
            self._select_quote_display(failed)
            self._render_rows()
            self.hint.set('腾讯／新浪报价；具体来源和时间见行情时间列' if quotes else '实时报价暂不可用，保留原行情时间')
            self._live_due = time.monotonic() + self._live_poller.delay
        if wanted and set(self.views.columns()) & {'price','change','percent','asof'} and time.monotonic() >= self._live_due:
            from workbench.holding_quotes import china_now, session_status
            if session_status(china_now(), None) == '交易中':
                self._live_poller.start(wanted)
        self._quote_timer = self.after(200, self._poll_quotes)

    def _select_quote_display(self, failed=()):
        from workbench.holding_quotes import select_quotes, china_now
        codes = [r['code'] for r in getattr(self, '_all_rows', [])]
        self._quotes = select_quotes(self._history_quotes, self._live_quotes, codes, failed, china_now(), None)

    def _destroyed(self, event):
        if event.widget is self and self._quote_timer:
            self._live_poller.invalidate()
            self.after_cancel(self._quote_timer)
            self._quote_timer = None

    def _hover_row(self, event):
        iid = self.tree.identify_row(event.y)
        code = self.tree._codes.get(iid)
        row = next((r for r in self._display_rows if r['code'] == code), None)
        if row:
            self._hover_code = code
            self.hint.set(row['state'] + (' · ' + row['notes'] if row['notes'] else '') + ' · 右键管理')
            box = self.tree.bbox(iid)
            if box:
                height = box[3]
                width = max(32, round(height * 1.1))
                self._remove_hover.place(x=max(0, self.tree.winfo_width() - width - 4), y=box[1], width=width, height=height)
                if self.on_alert:
                    self._alert_hover.place(x=max(0, self.tree.winfo_width() - 2 * width - 4), y=box[1], width=width, height=height)
        else:
            self._remove_hover.place_forget()
            self._alert_hover.place_forget()

    def _rebuild_list_menu(self):
        menu = tk.Menu(self.list_button, tearoff=False)
        values = [('all', '全部关注')] + [(item['id'], item['name']) for item in self.views.data['lists']]
        for identity, name in values:
            menu.add_command(label=name, command=lambda i=identity: self._choose_list(i))
        menu.add_separator()
        menu.add_command(label='新建自选表', command=self._new_list)
        menu.add_command(label='管理自选表…', command=self._manage_lists)
        self.list_button.configure(menu=menu)
        self.list_name.set(dict(values).get(self.views.data['active'], '全部关注'))

    def _choose_list(self, identity):
        self.views.data['active'] = identity
        self._save_view()
        self._sort = (None, False)
        self.query.set('')
        self._rebuild_list_menu()
        self._render_rows()

    def _save_view(self):
        try:
            self.views.save()
            return True
        except Exception as exc:
            messagebox.showerror('设置未保存', str(exc), parent=self)
            self.views.reload()
            self._rebuild_list_menu()
            self._render_rows()
            return False

    def _new_list(self):
        name = simpledialog.askstring('新建自选表', '名称：', parent=self)
        if name:
            self._view_action(lambda: self.views.create(name))

    def _view_action(self, action):
        try:
            action()
        except Exception as exc:
            messagebox.showerror('设置未保存', str(exc), parent=self)
            self.views.reload()
        self._rebuild_list_menu()
        self._render_rows()

    def _manage_lists(self):
        window = tk.Toplevel(self)
        window.title('管理自选表')
        window.transient(self.winfo_toplevel())
        listing = tk.Listbox(window, height=8, exportselection=False)
        listing.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        items = self.views.data['lists']
        for item in items:
            listing.insert(tk.END, item['name'])
        def act(kind):
            choice = listing.curselection()
            if not choice:
                return
            index = choice[0]
            self.views.data['active'] = items[index]['id']
            if kind == 'rename':
                name = simpledialog.askstring('重命名', '名称：', initialvalue=items[index]['name'], parent=window)
                if name:
                    self._view_action(lambda: self.views.rename(name))
            elif kind == 'delete':
                if messagebox.askyesno('删除自选表', '仅删除此自选表，标的仍保留在“全部关注”。', parent=window):
                    self._view_action(self.views.delete)
            else:
                target = max(0, min(len(items) - 1, index + kind))
                items[index], items[target] = items[target], items[index]
                self._save_view()
            window.destroy()
            self._rebuild_list_menu()
            self._render_rows()
        actions = ttk.Frame(window)
        actions.pack(padx=12, pady=12)
        for label, kind in [('重命名', 'rename'), ('删除', 'delete'), ('上移', -1), ('下移', 1)]:
            ttk.Button(actions, text=label, command=lambda k=kind: act(k)).pack(side=tk.LEFT, padx=3)

    def _settings_menu(self):
        menu = tk.Menu(self, tearoff=False)
        columns = tk.Menu(menu, tearoff=False)
        self._column_vars = {}
        for col in COLUMNS:
            if col in ('code', 'name'):
                continue
            var = tk.BooleanVar(value=col in self.views.columns())
            self._column_vars[col] = var
            columns.add_checkbutton(label=self.tree.heading(col, 'text'), variable=var, command=self._change_columns)
        menu.add_cascade(label='显示列', menu=columns)
        menu.add_command(label='恢复手动排序', command=lambda: (setattr(self, '_sort', (None, False)), self._render_rows()))
        menu.add_command(label='刷新本地行情列', command=self._request_quotes)
        menu.add_command(label='导入代码 CSV…', command=self._import_csv)
        menu.add_command(label='导出当前自选表…', command=self._export_csv)
        menu.add_command(label='市值 / PE / 财务：尚无可信数据源', state=tk.DISABLED)
        menu.tk_popup(self.settings_button.winfo_rootx(), self.settings_button.winfo_rooty() + self.settings_button.winfo_height())

    def _change_columns(self):
        self.views.data['columns'] = ['code', 'name'] + [c for c, var in self._column_vars.items() if var.get()]
        if self._save_view():
            self.tree.configure(displaycolumns=self.views.columns())
            self._fit_columns()
            self._request_quotes()

    def _quick_add(self, _event=None):
        from .data import StockNameLookup, code_names
        from workbench.market import code_of
        query = self.query.get().strip()
        try:
            names = code_names(self.store)
            exact = [c for c, name in names.items() if name == query]
            code = exact[0] if len(exact) == 1 else code_of(query.split()[0] if query else '')
            self.store.add_manual_watch(code, names.get(code, ''))
            self._manual_added(code)
        except Exception as exc:
            self.hint.set(str(exc))
        return 'break'

    def _import_csv(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            if Path(path).stat().st_size > 2 * 1024 * 1024:
                raise ValueError('CSV 文件不能超过 2MB')
            codes = import_codes(Path(path).read_text(encoding='utf-8-sig'))
            if self.views.error:
                raise ValueError('请先恢复自选表设置备份；原关注记录未改动')
            from copy import deepcopy
            data = deepcopy(self.views.data)
            item = next((v for v in data['lists'] if v['id'] == data['active']), None)
            if item:
                item['codes'] = list(dict.fromkeys(item['codes']+codes))
            self.store.import_manual_watches(codes, data, self.views.revision)
            self.views.reload()
            self.reload()
        except Exception as exc:
            self.views.reload()
            messagebox.showerror('导入未完成', str(exc), parent=self)

    def _export_csv(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                Path(path).write_text(export_rows(self.rows()), encoding='utf-8-sig')
            except OSError as exc:
                messagebox.showerror('导出失败', str(exc), parent=self)

    def _remove_selected(self):
        code = self.tree._codes.get(next(iter(self.tree.selection()), ''))
        if code:
            self._remove_from_list(code)

    def _remove_from_list(self, code):
        if self.views.collection():
            self._view_action(lambda: self.views.remove(code))
        else:
            self._end_watch(code)

    def rows(self):
        """按关注列表当前顺序提供 K 线分页数据。"""
        return [dict(row) for row in self._display_rows]

    def select_code(self, code):
        for iid, value in self.tree._codes.items():
            if value == code:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return True
        return False

    def _add_manual(self):
        if self.store is not None:
            ManualWatchDialog(self, self.store, self._manual_added)

    def _manual_added(self, code):
        self.views.add(code)
        self.query.set('')
        self.reload()
        self.select_code(code)
        if self.on_select:
            row = next(r for r in self._display_rows if r['code'] == code)
            self.on_select(code, row['observation_id'])

    def select_observation(self, observation_id, *, take_focus=True):
        for iid, value in self.tree._obs.items():
            if value == observation_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                if take_focus:
                    self.tree.focus_set()
                return True
        return False

    def _move_selection(self, delta):
        children = tuple(iid for iid in self.tree.get_children() if iid in self.tree._codes)
        if not children:
            return "break"
        selection = self.tree.selection()
        try:
            index = children.index(selection[0]) if selection else 0
        except ValueError:
            index = 0
        index = max(0, min(index + int(delta), len(children) - 1))
        iid = children[index]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.tree.focus_set()
        if self.on_select:
            self.on_select(self.tree._codes.get(iid), self.tree._obs.get(iid))
        return "break"

    def _request_page(self, delta):
        if self.on_page_request:
            self.on_page_request(delta)
        return "break"

    # ---- 选中 -> 主窗口联动 K 线 ----
    def _on_select(self, event):
        if self.on_select is None:
            return
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        code = self.tree._codes.get(iid)
        obs_id = self.tree._obs.get(iid)
        if code:
            self.on_select(code, obs_id)

    def _drag_start(self, event):
        iid = self.tree.identify_row(event.y)
        if iid not in self.tree._codes:
            self._drag_item = None
            return
        self.tree.selection_set(iid)
        self._drag_item = (
            self.tree._codes.get(iid),
            self.tree._obs.get(iid),
        )
        self._drag_origin = (event.x_root, event.y_root)
        self._dragging = False

    def _drag_motion(self, event):
        if not self._drag_item or not self._drag_origin:
            return
        dx = abs(event.x_root - self._drag_origin[0])
        dy = abs(event.y_root - self._drag_origin[1])
        if max(dx, dy) < 6:
            return
        self._dragging = True
        self.tree.configure(cursor="hand2")
        if self.on_drag_motion:
            self.on_drag_motion(event.x_root, event.y_root)

    def _drag_release(self, event):
        try:
            inside = (self.tree.winfo_rootx() <= event.x_root < self.tree.winfo_rootx() + self.tree.winfo_width()
                      and self.tree.winfo_rooty() <= event.y_root < self.tree.winfo_rooty() + self.tree.winfo_height())
            if self._dragging and self._drag_item and inside:
                target = self.tree._codes.get(self.tree.identify_row(event.y))
                codes = [r['code'] for r in self._display_rows]
                code = self._drag_item[0]
                if target and target != code:
                    codes.remove(code)
                    codes.insert(codes.index(target), code)
                    # Keep filtered-out members' relative order too.
                    codes += [r['code'] for r in self.views.visible(self._all_rows) if r['code'] not in codes]
                    self._sort = (None, False)
                    self._view_action(lambda: self.views.reorder(codes))
            elif self._dragging and self._drag_item and self.on_drop:
                code, observation_id = self._drag_item
                self.on_drop(code, observation_id, event.x_root, event.y_root)
            elif self._drag_item and self.on_select:
                code, observation_id = self._drag_item
                self.on_select(code, observation_id)
        finally:
            self.tree.configure(cursor="")
            self._drag_item = None
            self._drag_origin = None
            self._dragging = False
            if self.on_drag_end:
                self.on_drag_end()

    # ---- 右键菜单 ----
    def _context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid not in self.tree._codes:
            return
        self.tree.selection_set(iid)
        code = self.tree._codes.get(iid)
        obs_id = self.tree._obs.get(iid)
        menu = tk.Menu(self, tearoff=0)
        flags = tk.Menu(menu, tearoff=False)
        for key, name in zip(FLAGS, ('红', '蓝', '绿', '黄', '紫', '青', '粉')):
            flags.add_command(label='● ' + name, foreground=FLAGS[key],
                command=lambda color=key: self._view_action(lambda: self.views.flag(code, color)))
        flags.add_command(label='取消标记', command=lambda: self._view_action(lambda: self.views.flag(code, None)))
        menu.add_cascade(label='标记颜色', menu=flags)
        menu.add_command(label='取消当前表全部标记', command=self._clear_flags)
        lists = tk.Menu(menu, tearoff=False)
        for item in self.views.data['lists']:
            lists.add_command(label=item['name'], command=lambda i=item['id']: self._view_action(lambda: self.views.add(code, i)))
        lists.add_command(label='新建自选表…', command=self._new_list)
        menu.add_cascade(label='添加到自选表', menu=lists)
        menu.add_command(label="修改备注", command=lambda: self._edit_notes(code))
        menu.add_command(label='分组标签…', command=lambda:self._set_section(code))
        menu.add_command(label='添加到当前图比较', command=lambda: self.on_compare(code) if self.on_compare else None,
                         state=tk.NORMAL if self.on_compare else tk.DISABLED)
        if self.on_alert:
            menu.add_command(label='添加提醒', command=lambda: self.on_alert(code))
        state_menu = tk.Menu(menu, tearoff=0)
        for st in _PLAN_STATES:
            state_menu.add_command(
                label=st, command=lambda s=st: self._change_state(code, obs_id, s)
            )
        state_label = "计划状态…"
        if obs_id:
            menu.add_cascade(label=state_label, menu=state_menu)
        else:
            menu.add_command(label='手动关注 · 仅看K图', state=tk.DISABLED)
        menu.add_separator()
        menu.add_command(label='从当前自选表移除', command=lambda: self._remove_from_list(code))
        if self.views.collection():
            menu.add_command(label="结束全部关注…", command=lambda: self._end_watch(code))
        menu.tk_popup(event.x_root, event.y_root)

    def _clear_flags(self):
        if messagebox.askyesno('清除标记', '清除当前自选表所有标的的颜色标记？其他表中同标的也会取消标记。', parent=self):
            for row in self.views.visible(self._all_rows):
                self.views.data['flags'].pop(row['code'], None)
            self._save_view()
            self._render_rows()

    def _set_section(self, code):
        groups = self.views.data['sections'].setdefault(self.views.data['active'], {})
        text = simpledialog.askstring('分组标签', '输入分组名（留空取消分组；不自动推断行业）：', initialvalue=groups.get(code,''), parent=self)
        if text is None:
            return
        if len(text)>40:
            messagebox.showerror('名称过长','分组名最多 40 字。',parent=self); return
        if text.strip():groups[code]=text.strip()
        else:groups.pop(code,None)
        self._save_view();self._render_rows()

    # ---- 写：修改备注（update_watch）----
    def _edit_notes(self, code):
        if self.store is None or not code:
            return
        try:
            rows = self.store.rows("SELECT notes FROM watchlist WHERE code=?", (code,))
            current = rows[0]["notes"] if rows else ""
        except Exception:
            current = ""
        notes = simpledialog.askstring(
            "修改备注", f"{code} 的备注：", initialvalue=current, parent=self
        )
        if notes is None:
            return
        try:
            self.store.update_watch(code, notes, active=True)
        except Exception as exc:
            messagebox.showerror("修改备注失败", str(exc), parent=self)
            return
        self.reload()

    # ---- 写：切换计划状态（save_plan，含内置风控校验）----
    def _change_state(self, code, obs_id, state):
        if self.store is None or not code:
            return
        if not obs_id:
            messagebox.showinfo(
                "无关联观察", f"{code} 的关注记录没有关联的观察信号，无法建立计划。", parent=self
            )
            return
        # 预填：现有计划优先，其次观察 payload（entry/stop/target）
        entry = stop = target = 0.0
        quantity = 0
        notes = ""
        plans = self.store.rows(
            "SELECT entry, stop, target, quantity, notes FROM plans "
            "WHERE observation_id=? ORDER BY updated DESC LIMIT 1",
            (obs_id,),
        )
        if plans:
            p = plans[0]
            entry = p["entry"] or 0.0
            stop = p["stop"] or 0.0
            target = p["target"] or 0.0
            quantity = p["quantity"] or 0
            notes = p["notes"] or ""
        else:
            obs = self.store.rows("SELECT payload FROM observations WHERE id=?", (obs_id,))
            if obs and obs[0]["payload"]:
                try:
                    pl = json.loads(obs[0]["payload"])
                    entry = float(pl.get("entry") or 0)
                    stop = float(pl.get("stop") or 0)
                    target = float(pl.get("target") or 0)
                except Exception:
                    pass
        dialog = _PlanDialog(self, code, state, entry, stop, target, quantity, notes)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_plan(
                obs_id, state, dialog.entry, dialog.stop,
                dialog.target, dialog.quantity, dialog.notes,
            )
        except Exception as exc:
            # store 内置风控校验（0<止损<入场<目标 等）不通过会在这里抛出
            messagebox.showerror("保存计划失败", str(exc), parent=self)
            return
        self.reload()

    # ---- 写：结束关注（update_watch active=False，行从池中消失）----
    def _end_watch(self, code):
        if self.store is None or not code:
            return
        if not messagebox.askyesno("结束关注", f"结束关注 {code}？", parent=self):
            return
        try:
            rows = self.store.rows("SELECT notes FROM watchlist WHERE code=?", (code,))
            notes = rows[0]["notes"] if rows else ""
            self.store.update_watch(code, notes, active=False)
        except Exception as exc:
            messagebox.showerror("结束关注失败", str(exc), parent=self)
            return
        self.reload()


class ManualWatchDialog(tk.Toplevel):
    """A security and optional note, never a trading-plan form."""

    def __init__(self, parent, store, on_added):
        super().__init__(parent)
        from .data import StockNameLookup, code_names
        self.store, self.on_added = store, on_added
        self.lookup = StockNameLookup(code_names(store))
        self.title('添加关注')
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.query = tk.StringVar()
        self.notes = tk.StringVar()
        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text='输入股票 / ETF 代码或名称').pack(anchor=tk.W)
        self.entry = ttk.Entry(body, textvariable=self.query, width=38)
        self.entry.pack(fill=tk.X, pady=8)
        self.matches = tk.Listbox(body, height=5, exportselection=False)
        self.matches.pack(fill=tk.X)
        self.matches.bind('<<ListboxSelect>>', self._choose)
        self.entry.bind('<KeyRelease>', self._suggest)
        ttk.Label(body, text='备注（可不填）').pack(anchor=tk.W, pady=(12, 4))
        ttk.Entry(body, textvariable=self.notes).pack(fill=tk.X)
        self.error = tk.StringVar(value='只加入关注，不扫描、不建立交易计划。')
        ttk.Label(body, textvariable=self.error, wraplength=360).pack(anchor=tk.W, pady=10)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text='加入关注', command=self._save).pack(side=tk.RIGHT)
        ttk.Button(actions, text='取消', command=self.destroy, bootstyle='secondary').pack(side=tk.RIGHT, padx=8)
        self.bind('<Return>', lambda _event: self._save())
        self.bind('<Escape>', lambda _event: self.destroy())
        self.entry.focus_set()
        self._suggestions = []

    def _suggest(self, _event=None):
        query = self.query.get().strip()
        if query.isdigit():
            self._suggestions = [(c, n) for c, n in self.lookup.names.items()
                                 if c.split('.')[-1].startswith(query)][:8] if query else []
        else:
            self._suggestions = self.lookup.suggest(query)
        self.matches.delete(0, tk.END)
        for code, name in self._suggestions:
            self.matches.insert(tk.END, code + ' ' + name)

    def _choose(self, _event=None):
        selected = self.matches.curselection()
        if selected:
            code, name = self._suggestions[selected[0]]
            self.query.set(code + ' ' + name)

    def _save(self):
        from workbench.market import code_of
        query = self.query.get().strip()
        try:
            try:
                code = code_of(query.split()[0] if query else '')
            except ValueError:
                exact = [(c, n) for c, n in self.lookup.names.items() if n == query]
                if len(exact) != 1:
                    raise ValueError('请输入六位代码，或从匹配列表中选择一个标的')
                code = exact[0][0]
            code = self.store.add_manual_watch(code, self.lookup.names.get(code, ''), self.notes.get())
        except ValueError as exc:
            self.error.set(str(exc))
            return
        self.on_added(code)
        self.destroy()


class _PlanDialog:
    """轻量计划录入框：一个 Toplevel，确定后回读字段（不引第三方依赖）。"""

    def __init__(self, parent, code, state, entry, stop, target, quantity, notes):
        self.confirmed = False
        self.entry = self.stop = self.target = 0.0
        self.quantity = 0
        self.notes = ""
        self.top = tk.Toplevel(parent)
        self.top.title(f"计划状态：{state} · {code}")
        self.top.configure(bg=APP_BG)
        self.top.transient(parent.winfo_toplevel())
        self.top.grab_set()
        self.top.resizable(False, False)

        ttk.Label(self.top, text=f"{code} → {state}", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, padx=8, pady=(8, 2)
        )
        self._vars = {}
        fields = (
            ("入场价", entry), ("止损价", stop), ("目标价", target),
            ("股数", quantity), ("备注", notes),
        )
        for i, (label, value) in enumerate(fields, start=1):
            ttk.Label(self.top, text=label).grid(row=i, column=0, sticky=tk.W, padx=8, pady=3)
            var = tk.StringVar(value=str(value))
            entry_widget = ttk.Entry(self.top, textvariable=var, width=14)
            entry_widget.grid(row=i, column=1, sticky=tk.EW, padx=8, pady=3)
            self._vars[label] = var
        ttk.Button(self.top, text="确定", command=self._ok).grid(
            row=len(fields) + 1, column=0, pady=8, padx=8, sticky=tk.E
        )
        ttk.Button(self.top, text="取消", command=self.top.destroy).grid(
            row=len(fields) + 1, column=1, pady=8, padx=8, sticky=tk.W
        )

    def _ok(self):
        try:
            self.entry = float(self._vars["入场价"].get())
            self.stop = float(self._vars["止损价"].get())
            self.target = float(self._vars["目标价"].get())
            self.quantity = int(float(self._vars["股数"].get()))
            self.notes = self._vars["备注"].get()
        except ValueError:
            messagebox.showerror("输入无效", "价格/股数必须是数字", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()
