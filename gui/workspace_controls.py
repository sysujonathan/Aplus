"""Shared native toolbar; commands always target the current pane token."""
from dataclasses import asdict
import tkinter as tk
from tkinter import simpledialog, messagebox
import ttkbootstrap as ttk

from workbench.workspace_layouts import SHAPES, SYNC_KEYS, new_layout, validate_layout
from .chart_items import ChartItem
from .theme import MUTED

PERIOD_LABELS = {'daily':'日线', 'weekly':'周线', 'monthly':'月线', 'm5':'5分钟', 'm60':'1小时', 'm240':'4小时'}


class WorkspaceControls:
    def __init__(self, grid):
        self.grid = grid
        self.store = grid.store
        self._load_error = None
        try:
            saved = self.store.load_ui_preferences('chart_layouts') if self.store else {'revision':0,'data':{}}
            values = saved['data'].get('layouts', [])
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError('布局数量无效')
            self.layouts = [validate_layout(v) for v in values]
            if len({v['id'] for v in self.layouts}) != len(self.layouts):
                raise ValueError('布局编号重复')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self._load_error = str(exc)
            saved = {'revision':0,'data':{}}
            self.layouts = []
        self.revision = saved['revision']
        if not self.layouts:
            self.layouts = [new_layout()]
        self.current = next((v for v in self.layouts if v['id'] == saved['data'].get('active')), self.layouts[0])
        self.states = {}
        self.sync = {key: tk.BooleanVar(value=self.current['sync'][key]) for key in SYNC_KEYS}
        self.scope = tk.StringVar(value=self.current.get('drawingScope', 'pane'))
        self._save_timer = None
        self._restoring = False
        self._enabled = False
        self.top = ttk.Frame(grid, padding=(4, 3))
        self.top.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        self.caption = tk.StringVar(value='布局设置无法读取，原设置已保留；暂用单图' if self._load_error else '选择图格后操作')
        self.period = tk.StringVar(value='日线')
        period = ttk.Combobox(self.top, textvariable=self.period, values=list(PERIOD_LABELS.values()), width=6, state='readonly')
        period.pack(side=tk.LEFT)
        period.bind('<<ComboboxSelected>>', self._period_changed)
        for label, action in [('指标','indicators'),('提醒','alert'),('回放','replay'),('↶','undo'),('↷','redo')]:
            ttk.Button(self.top, text=label, command=lambda a=action: self.command('action', a), bootstyle='secondary').pack(side=tk.LEFT, padx=2)
        self.layout_button = ttk.Menubutton(self.top, text='布局', bootstyle='secondary-outline')
        self.layout_button.pack(side=tk.LEFT, padx=5)
        self.sync_button = ttk.Menubutton(self.top, text='联动', bootstyle='secondary-outline')
        self.sync_button.pack(side=tk.LEFT)
        sync_menu = tk.Menu(self.sync_button, tearoff=False)
        for key, label in zip(SYNC_KEYS, ('标的','周期','十字线','时间位置','可见日期范围')):
            sync_menu.add_checkbutton(label=label, variable=self.sync[key], command=self.preferences_changed)
        sync_menu.add_separator()
        scope_menu = tk.Menu(sync_menu, tearoff=False)
        for key, label in [('pane','本图'),('layout','当前布局'),('global','全部布局')]:
            scope_menu.add_radiobutton(label=label, variable=self.scope, value=key, command=self.preferences_changed)
        sync_menu.add_cascade(label='新绘图默认范围', menu=scope_menu)
        self.sync_button.configure(menu=sync_menu)
        more = ttk.Menubutton(self.top, text='⋯', bootstyle='secondary')
        more.pack(side=tk.LEFT, padx=3)
        menu = tk.Menu(more, tearoff=False)
        for label, value in [('图表设置','settings'),('对象树','objects'),('图表快照','snapshot')]:
            menu.add_command(label=label, command=lambda v=value:self.command('action',v))
        ranges = tk.Menu(menu, tearoff=False)
        for label in ('1天','5天','1个月','3个月','6个月','YTD','1年','5年','全部'):
            ranges.add_command(label=label, command=lambda v=label:self.command('rangePreset',v))
        menu.add_cascade(label='日期范围', menu=ranges)
        more.configure(menu=menu)
        self.status = ttk.Label(grid, textvariable=self.caption, foreground=MUTED)
        self.status.grid(row=2, column=0, columnspan=2, sticky=tk.EW, padx=5)
        self.left = ttk.Frame(grid, padding=(2, 3))
        self.left.grid(row=1, column=0, sticky=tk.NS)
        from ttkbootstrap.widgets import ToolTip
        groups = [('线', [('趋势线','trend'),('水平射线','ray'),('通道','channel'),('箭头','arrow'),('路径','path')]),
                  ('形', [('矩形','rectangle'),('向上箭头','up'),('向下箭头','down'),('文字','text')])]
        ttk.Button(self.left, text='＋', width=3, command=lambda:self.command('tool','cross'), bootstyle='secondary').pack(pady=2)
        for label, tools in groups:
            button = ttk.Menubutton(self.left, text=label, width=3, bootstyle='secondary')
            button.pack(pady=2)
            menu = tk.Menu(button, tearoff=False)
            for name, tool in tools:
                menu.add_command(label=name, command=lambda t=tool:self.command('tool',t))
            button.configure(menu=menu)
        for label, tool, title in [('斐','fib','斐波那契'),('多','long','多头仓位'),('空','short','空头仓位'),('磁','magnet','磁吸'),('锁','lock','锁定绘图')]:
            button = ttk.Button(self.left, text=label, width=3, command=lambda t=tool:self.command('tool',t), bootstyle='secondary')
            button.pack(pady=2)
            ToolTip(button,text=title)
        self._layout_menu()

    def descriptor(self, index):
        return {'layout':self.current['id'], 'pane':str(index), 'defaultScope':self.scope.get()}

    def command(self, kind, value=None):
        index = self.grid._active_slot
        if not 0 <= index < len(self.grid._slots):
            return False
        host = self.grid._slots[index]._embedded
        if not host or not host.command(kind, value):
            self.caption.set('请先选择标的，等待图表加载完成后再操作')
            return False
        self.caption.set(f'图格 {index+1} · {self.grid.current_item().code if self.grid.current_item() else ""}')
        return True

    def _period_changed(self, _event=None):
        period = next(key for key, label in PERIOD_LABELS.items() if label == self.period.get())
        slot = self.grid._slots[self.grid._active_slot]
        slot._workspace_restore = None
        self.command('timeframe', period)

    def preferences_changed(self):
        self.current['sync'] = {key: value.get() for key, value in self.sync.items()}
        self.current['drawingScope'] = self.scope.get()
        for index, slot in enumerate(self.grid._slots):
            slot._workspace_descriptor = self.descriptor(index)
            if slot._embedded:
                slot._embedded.workspace = self.descriptor(index)
                slot._embedded.command('defaultScope',self.scope.get())
        self.schedule_save()

    def state_changed(self, index, state):
        old = self.states.get(index, {})
        self.states[index] = state
        slot = self.grid._slots[index]
        restore = getattr(slot, '_workspace_restore', {})
        if restore:
            sent = getattr(slot, '_workspace_restore_sent', 0)
            if sent and state.get('ack', 0) >= sent and state.get('timeframe') == restore.get('timeframe'):
                slot._workspace_restore = None
            elif (not sent and not state.get('dirty')
                  and not state.get('replay') and restore.get('timeframe') in state.get('available', [])):
                if slot._embedded.command('sync',
                        {key:restore[key] for key in ('timeframe','range') if restore.get(key)}):
                    slot._workspace_restore_sent = slot._embedded._command_number
        if state.get('timeframe') != old.get('timeframe'):
            slot._workspace_live = str(state.get('timeframe','')).startswith('m')
            slot._accept_intraday(index == self.grid._active_slot or slot._workspace_live)
        if index != self.grid._active_slot:
            return
        self.period.set(PERIOD_LABELS.get(state.get('timeframe'), '日线'))
        if state.get('dirty') or state.get('replay'):
            self.caption.set(f'图格 {index+1} · ' + (state.get('saveError') or ('回放中，联动暂停' if state.get('replay') else '正在保存绘图…')))
        elif old.get('dirty'):
            self.caption.set(f'图格 {index+1} · 绘图已保存')
        patch = {key:state.get(key) for key in ('timeframe','crosshair','time','range')
                 if self.sync['timeframe' if key=='timeframe' else key].get() and state.get(key) != old.get(key)}
        if patch and not state.get('replay') and not state.get('dirty'):
            for target, slot in enumerate(self.grid._slots):
                if target != index and slot._embedded and not self.states.get(target, {}).get('replay'):
                    slot._embedded.command('sync', patch)
        if any(state.get(key) != old.get(key) for key in ('timeframe','range','time')):
            self.schedule_save()

    def schedule_save(self):
        if not self._enabled or self._restoring:
            return
        if self._save_timer:
            self.grid.after_cancel(self._save_timer)
        self._save_timer = self.grid.after(700, self.save)

    def save(self):
        self._save_timer = None
        if self._load_error:
            self.caption.set('原布局设置未覆盖，请恢复设置备份后重试；图表仍可使用')
            return
        panes = []
        for index, item in enumerate(self.grid._display_items):
            state = self.states.get(index, {})
            # A slow minute feed must not replace the saved layout with its
            # temporary daily boot while restoration is still pending.
            restore = getattr(self.grid._slots[index], '_workspace_restore', None) or {}
            panes.append(dict(item=asdict(item) if item else None,
                timeframe=restore.get('timeframe', state.get('timeframe','daily')),
                range=restore.get('range', state.get('range'))))
        self.current['panes'] = panes
        self.current['rows'], self.current['columns'] = self.grid._shape
        self.current['active'] = self.grid._active_slot
        if self.store:
            try:
                result = self.store.save_ui_preferences('chart_layouts', {'layouts':self.layouts, 'active':self.current['id']}, self.revision)
                self.revision = result['revision']
                self.caption.set(f'{self.current["name"]} · 已自动保存 · 活动图格 {self.grid._active_slot+1}')
            except Exception as exc:
                self.caption.set('布局未保存：' + str(exc))

    def restore_saved_layout(self):
        self._enabled = True
        if self.current['panes']:
            self.open_layout(self.current['id'], save_previous=False)

    def _layout_menu(self):
        menu = tk.Menu(self.layout_button, tearoff=False)
        for count, (rows, columns) in SHAPES.items():
            menu.add_command(label=f'{count} 图 · {rows} × {columns}', command=lambda c=count:self.grid.set_layout(c))
        menu.add_command(label='自定义行列…', command=self.custom_grid)
        menu.add_separator()
        for layout in self.layouts:
            menu.add_command(label=layout['name'], command=lambda i=layout['id']:self.open_layout(i))
        menu.add_separator()
        menu.add_command(label='新建布局…', command=lambda:self.new(False))
        menu.add_command(label='复制当前布局…', command=lambda:self.new(True))
        menu.add_command(label='重命名…', command=self.rename)
        menu.add_command(label='保存布局', command=self.save)
        menu.add_command(label='归档当前布局（保留画线）…', command=self.delete)
        self.layout_button.configure(menu=menu, text=self.current['name'])

    def custom_grid(self):
        value = simpledialog.askstring('自定义布局', '输入行 × 列，例如 3x2；最多九格、三行四列', parent=self.grid)
        if value:
            try:
                rows, columns = map(int,value.lower().replace('×','x').split('x'))
                validate_layout({**self.current,'rows':rows,'columns':columns})
                self.grid.set_grid(rows,columns)
            except (ValueError,TypeError) as exc:
                messagebox.showerror('布局无效',str(exc),parent=self.grid)

    def new(self, copy):
        name = simpledialog.askstring('复制布局' if copy else '新建布局','名称：',parent=self.grid)
        if not name:
            return
        self.save()
        try:
            layout = new_layout(name,*self.grid._shape)
        except ValueError as exc:
            messagebox.showerror('布局无效',str(exc),parent=self.grid); return
        if copy:
            from copy import deepcopy
            if any(state.get('dirty') for state in self.states.values()) or any(
                    item and (not slot._embedded or not slot._embedded.ready() or
                              not (slot._embedded._last_workspace_state or {}).get('identity'))
                    for item, slot in zip(self.grid._display_items, self.grid._slots)):
                messagebox.showinfo('稍后复制','请等待所有图格加载并保存完成，再复制布局。',parent=self.grid)
                return
            layout.update(panes=deepcopy(self.current['panes']),sync=deepcopy(self.current['sync']),
                          drawingScope=self.current.get('drawingScope','pane'))
            if self.store:
                identities = [(slot._embedded._last_workspace_state or {}).get('identity') for slot in self.grid._slots
                              if slot._embedded and (slot._embedded._last_workspace_state or {}).get('identity')]
                try:
                    saved = self.store.save_ui_preferences('chart_layouts',
                        {'layouts':self.layouts+[layout],'active':layout['id']}, self.revision,
                        layout_copy=(self.current['id'],layout['id'],identities))
                    self.revision = saved['revision']
                except Exception as exc:
                    messagebox.showerror('布局未复制',str(exc),parent=self.grid)
                    return
        self.layouts.append(layout)
        self.open_layout(layout['id'], save_previous=False)

    def rename(self):
        name = simpledialog.askstring('重命名布局','名称：',initialvalue=self.current['name'],parent=self.grid)
        if name:
            try:
                valid = validate_layout({**self.current,'name':name})
                self.current.update(valid); self.save(); self._layout_menu()
            except ValueError as exc:
                messagebox.showerror('布局无效',str(exc),parent=self.grid)

    def delete(self):
        if len(self.layouts)==1:
            messagebox.showinfo('保留默认布局','至少保留一个布局。',parent=self.grid); return
        if messagebox.askyesno('归档布局','归档当前布局？布局和画线均保留，仍可从布局菜单重新打开。',parent=self.grid):
            deleted=self.current
            self.open_layout(next(v['id'] for v in self.layouts if v is not deleted))
            # Keep a recoverable layout instead of orphaning pane-scoped drawings.
            deleted['name']='已归档 · '+deleted['name'][:32]
            self.save();self._layout_menu()

    def open_layout(self, identity, save_previous=True):
        if save_previous and not (self.current['id'] == identity):
            self.save()
        layout = next(v for v in self.layouts if v['id']==identity)
        self._restoring = True
        self.current = layout
        self.states = {}
        for key,var in self.sync.items():var.set(layout['sync'][key])
        self.scope.set(layout.get('drawingScope','pane'))
        self.grid.set_grid(layout['rows'],layout['columns'], render=False)
        panes = layout['panes']
        self.grid._display_items = [ChartItem(**pane['item']) if pane.get('item') else None for pane in panes]
        self.grid._display_items += [None] * (self.grid._layout_count-len(panes))
        self.grid._active_slot = layout['active']
        for index,slot in enumerate(self.grid._slots):
            slot._workspace_descriptor = self.descriptor(index)
            slot._workspace_restore = panes[index] if index<len(panes) else {}
            slot._workspace_restore_sent = False
            item = self.grid._display_items[index]
            if item:self.grid._show_item(slot,item)
            else:slot.show_placeholder()
        self.grid._refresh_active_styles()
        self._restoring=False
        self._layout_menu()
        self.schedule_save()
