"""Shared dated list. Quant signals and shape scores remain distinct evidence."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
import ttkbootstrap as ttk

from .candidate_tabs import CandidateTabs, short_strategy_label
from .theme import ACCENT, CONTROL_BG, MUTED, TEXT
from .tree_scroll import numeric_stock_code

SOURCES = ('全部','量化策略','形态捕手')


class MatchList(CandidateTabs):
    def __init__(self, *args, on_manage=None, **kwargs):
        super().__init__(*args, **kwargs)
        for child in self.winfo_children():
            if isinstance(child,ttk.Label):
                child.configure(text='匹配列表')
        self.origin = '全部'
        self._filter_key = None
        self._all_rows = []
        self._row_by_iid = {}
        self.source_buttons = {}
        for index,label in enumerate(SOURCES):
            self.sidebar.columnconfigure(index,weight=1)
            button = ttk.Button(self.sidebar,text=label,command=lambda v=label:self.set_source(v),padding=(4,3))
            button.grid(row=0,column=index,sticky=tk.EW,padx=1)
            self.source_buttons[label] = button
        controls = ttk.Frame(self.sidebar)
        controls.grid(row=1,column=0,columnspan=3,sticky=tk.EW,pady=(4,0))
        controls.columnconfigure(0,weight=1)
        self.filter_var = tk.StringVar(value='全部策略／模板')
        self.filter_box = ttk.Combobox(controls,textvariable=self.filter_var,state='readonly',width=14)
        self.filter_box.grid(row=0,column=0,sticky=tk.EW)
        self.filter_box.bind('<<ComboboxSelected>>',self._filter_changed)
        ttk.Button(controls,text='形态模板',command=on_manage,padding=(5,3)).grid(row=0,column=1,padx=(4,0))
        self.tree.configure(columns=('number','code','name','match'))
        for column,title in (('number','序'),('code','代码'),('name','名称')):
            self.tree.heading(column,text=title,anchor=tk.CENTER,command=lambda c=column:self._sort_column(c))
            self.tree.column(column,anchor=tk.CENTER)
        self.tree.heading('match',text='匹配依据',anchor=tk.W)
        self.tree.column('match',width=110,minwidth=70,stretch=True,anchor=tk.W)
        self.tree.unbind('<Configure>')
        self.tree.bind('<Configure>',self._fit_columns)
        self.detail = tk.StringVar(value='相似度为图形相似程度，不是交易胜率')
        self.summary = tk.StringVar(value='相似度不是交易胜率')
        ttk.Label(self,textvariable=self.summary,foreground=MUTED,font=('Microsoft YaHei UI',8)).grid(
            row=3,column=0,sticky=tk.EW,pady=(4,0))
        from ttkbootstrap.widgets import ToolTip
        self._detail_tip = ToolTip(self.tree,text=self.detail.get(),delay=600)
        self.tree.configure(height=3)
        self._filter_values = {}
        self._listing_font = tkfont.Font(root=self,font=ttk.Style().lookup('Treeview','font') or 'TkDefaultFont')
        self._template_labels = {}
        self.task_status = tk.StringVar()
        ttk.Label(self,textvariable=self.task_status,foreground=MUTED,font=('Microsoft YaHei UI',8)).grid(
            row=0,column=0,sticky=tk.E,padx=2)

    def set_task_status(self, value):
        source = value.get('source','')
        state = value.get('state')
        if state in ('queued','running'):
            self.task_status.set(f"形态 {source} {value.get('progress',0)}/{value.get('total',0)}")
        elif state in ('failed','cancelled','interrupted','partial'):
            self.task_status.set({'failed':'形态匹配失败','cancelled':'形态匹配已取消','interrupted':'形态待续做','partial':'形态部分完成'}[state])
        elif value.get('skipped_templates'):
            self.task_status.set(f"形态 {len(value['skipped_templates'])}模板未运行")
        else:
            self.task_status.set('')

    def _fit_columns(self,event):
        width = max(290,event.width)
        font = getattr(self,'_listing_font',None)
        number = max(28,font.measure('999')+10) if font else 28
        code = max(64,font.measure('888888')+12) if font else 64
        name = max(font.measure('国邦医药')+12,int(width*.23)) if font else 72
        for column,size in (('number',number),('code',code),('name',name)):
            self.tree.column(column,width=size,minwidth=size,stretch=False)
        self.tree.column('match',width=max(60,width-number-code-name-3),minwidth=60,stretch=True)

    def build_tabs(self, strategies):
        self._labels = dict(strategies)
        self._buttons = {}
        self._choose(None)

    def load_from_store(self,store,timeframe='daily',asof_filter=None,source='baostock',notify=True):
        from .data import load_candidates, code_names, data_inputs, snapshot_inputs
        from workbench.scan_cache import engine_inputs, file_state
        from workbench.sources import market_source
        self._timeframe, self._source = timeframe,source
        # Store publication includes selected quant/shape receipts, no-signal
        # checkpoints, template edits and name updates. This conservative token
        # also handles old imports without receipts and repaired/deleted evidence.
        receipts = store.rows("SELECT id,status,result FROM jobs WHERE kind='scan' "
                              "ORDER BY created DESC,rowid DESC LIMIT 32")
        shape_receipts = [r for r in store.shape_runs(market_source(store))
                          if r['status'] in ('completed', 'partial') and
                          (not asof_filter or all(not part or str(part) == r['asof'].split('-')[i]
                                                for i, part in enumerate(asof_filter)))] if timeframe == 'daily' else []
        artifacts = file_state(store.root / r['path'] for r in shape_receipts)
        key = (str(store.root), tuple(asof_filter or ()), source, timeframe,
               repr(receipts), repr(shape_receipts), artifacts, data_inputs(store), engine_inputs(), tuple(self._labels.items()),
               snapshot_inputs())
        cached = getattr(self, '_pipeline_cache', None)
        if cached is not None and cached[0] == key:
            from copy import deepcopy
            self._rows, self._all_rows, self._template_labels, self._shape_error = deepcopy(cached[1])
            self._refresh_filters()
            self._choose(self._filter_key, notify=notify)
            self._show_shape_error()
            return
        groups = load_candidates(store,timeframe,source,asof_filter)
        quant = [dict(row,kind='quant',label=self._labels.get(key,key)) for key,rows in groups.items() for row in rows]
        self._shape_error = None
        try:
            from .background_status import display_store
            from workbench.shape_store import candidates
            shapes = candidates(display_store(store),asof_filter,market_source(store)) if timeframe=='daily' else []
        except (ValueError,OSError) as exc:
            shapes=[]
            self._shape_error = str(exc)
        names = code_names(store) if shapes else {}
        self._all_rows = quant+[dict(row,name=names.get(row['code'],row.get('name',''))) for row in shapes]
        self._rows = groups
        try:
            self._template_labels = {t['id']:t['name'] for t in store.shape_templates()}
        except ValueError as exc:
            self._template_labels = {}
            self._shape_error = str(exc)
        if not self._shape_error:
            from copy import deepcopy
            self._pipeline_cache = (key, deepcopy((self._rows, self._all_rows,
                                                  self._template_labels, self._shape_error)))
        self._refresh_filters()
        self._choose(self._filter_key,notify=notify)
        self._show_shape_error()

    def _show_shape_error(self):
        if self._shape_error:
            self.detail.set('形态历史暂不可用：'+self._shape_error)
            self.summary.set('形态历史校验未通过 · 量化策略仍可查看')
            self._detail_tip.text = self.detail.get()

    def _refresh_filters(self):
        rows = self._source_rows()
        counts = {}
        if self.origin in ('全部','量化策略'):
            counts.update((('quant',key),[name,0]) for key,name in self._labels.items())
        if self.origin in ('全部','形态捕手'):
            counts.update((('shape',key),[name,0]) for key,name in self._template_labels.items())
        for row in rows:
            key = (row['kind'],row['strategy'])
            if key not in counts:
                counts[key] = [row['label'],0]
            counts[key][1] += 1
        self._filter_values = {'全部策略／模板':None}
        for key,(name,count) in counts.items():
            label = f"{'形态' if key[0]=='shape' else '策略'} · {name} ({count})"
            self._filter_values[label] = key
        self.filter_box.configure(values=list(self._filter_values))
        selected = next((label for label,key in self._filter_values.items() if key==self._filter_key),'全部策略／模板')
        self.filter_var.set(selected)
        self._filter_key = self._filter_values[selected]
        for label,button in self.source_buttons.items():
            button.configure(bootstyle='primary' if self.origin==label else 'secondary')

    def _source_rows(self):
        kind = {'量化策略':'quant','形态捕手':'shape'}.get(self.origin)
        return [row for row in self._all_rows if kind is None or row['kind']==kind]

    def set_source(self, label):
        if label not in SOURCES:
            return
        self.origin = label
        self._refresh_filters()
        self._choose(self._filter_key)

    def _filter_changed(self,_event=None):
        self._filter_key = self._filter_values.get(self.filter_var.get())
        self._choose(self._filter_key)

    def _choose(self,key,notify=True):
        self._rows_changed_silently = not notify
        selected = self.tree.selection()
        previous = self.tree._obs.get(selected[0]) if selected else None
        self._muted = not notify
        self._selected = self._filter_key = key
        self.tree.delete(*self.tree.get_children())
        self.tree._obs, self.tree._codes, self.tree._base_tags = {},{},{}
        self._row_by_iid = {}
        self._display_rows = [r for r in self._source_rows() if key is None or (r['kind'],r['strategy'])==key]
        for number,row in enumerate(self._display_rows,1):
            shape = row['kind']=='shape'
            label = f"{row['label']} {row['score']:.1f}" if shape else short_strategy_label(row['label'])
            tags = ('row_even' if number%2==0 else 'row_odd',)
            iid = self.tree.insert('',tk.END,values=(number,numeric_stock_code(row['code']),row.get('name',''),label),tags=tags)
            self.tree._obs[iid], self.tree._codes[iid] = row['observation_id'],row['code']
            self.tree._base_tags[iid], self._row_by_iid[iid] = tags,row
        if self.on_rows_changed and notify:
            self.on_rows_changed(list(self._display_rows))
        children = self.tree.get_children()
        if children:
            target = next((i for i in children if self.tree._obs.get(i)==previous),children[0]) if not notify else children[0]
            self.tree.selection_set(target); self.tree.focus(target)
        else:
            self.detail.set('该交易日没有此来源的已保存匹配结果')
            self.summary.set('该日暂无此来源结果')
            if notify:self.callback(None,None)
        if not notify:self.after_idle(self._unmute)

    def _unmute(self):
        self._muted = False

    def consume_refresh(self):
        pending = getattr(self,'_rows_changed_silently',False)
        self._rows_changed_silently = False
        return pending

    def _select(self,event):
        if getattr(self,'_muted',False):return
        selection = self.tree.selection()
        row = self._row_by_iid.get(selection[0]) if selection else None
        if row:
            if row['kind']=='shape':
                mode = '事后历史回放' if row['scan_mode']=='replay' else '当时保存'
                self.detail.set(f"{row['label']} · 相似 {row['score']:.1f} · {row['window']} BAR\n"
                                f"{row['start']}—{row['end']} · {mode} · 非交易信号\n"
                                f"轮廓误差 {row['mae']:.3f} · 相关 {row['correlation']:.3f}")
                self.summary.set(f"{row['label'][:10]} · 相似 {row['score']:.1f} · 研究")
            else:
                self.detail.set(f"量化策略 · {row['label']} · {row['date']}")
                self.summary.set(f"{short_strategy_label(row['label'])} · {row['date']}")
            self._detail_tip.text = self.detail.get()
        super()._select(event)

    def _sort_column(self,column):
        super()._sort_column(column)
        self._display_rows = [self._row_by_iid[i] for i in self.tree.get_children()]
        if self.on_rows_changed:
            self.on_rows_changed(self.rows())
        self._select(None)
