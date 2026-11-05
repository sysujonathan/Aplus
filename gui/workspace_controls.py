"""Shared native toolbar; commands always target the current pane token."""
from dataclasses import asdict
import tkinter as tk
from tkinter import simpledialog, messagebox
import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip

from workbench.workspace_layouts import SHAPES, SYNC_KEYS, new_layout, validate_layout
from .chart_items import ChartItem
from .theme import APP_BG, CONTROL_BG, TEXT, MUTED
from .workspace_icons import icon

PERIOD_LABELS = {'daily':'日线', 'weekly':'周线', 'monthly':'月线', 'm5':'5分钟', 'm60':'1小时', 'm240':'4小时'}
QUICK_PERIODS=(('m5','5分'),('m60','1小时'),('daily','天'),('weekly','周'),('monthly','月'))
RANGE_PRESETS=('1天','5天','1个月','3个月','6个月','YTD','1年','5年','全部')
MAX_FAVORITE_LAYOUTS = 4


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
        self.top = tk.Frame(grid,autostyle=False,background=APP_BG,padx=5,pady=3)
        self.top.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        self.caption = tk.StringVar(value='布局设置无法读取，原设置已保留；暂用单图' if self._load_error else '选择图格后操作')
        self.period = tk.StringVar(value='日线')
        self._layout_icons={}
        self._layout_popup=None
        self._top_actions=[]
        self.fullscreen = False
        self.top.columnconfigure(0,weight=1)
        self.left_actions = tk.Frame(self.top,autostyle=False,background=APP_BG)
        self.left_actions.grid(row=0,column=0,sticky=tk.W)
        self.right = tk.Frame(self.top,autostyle=False,background=APP_BG)
        self.right.grid(row=0,column=1,sticky=tk.E)
        saved_favorites = saved['data'].get('toolbar', {})
        favorite_ids = saved_favorites.get('favoriteLayouts', []) if isinstance(saved_favorites, dict) else []
        known_ids = {layout['id'] for layout in self.layouts}
        self.favorite_layouts = list(dict.fromkeys(
            value for value in favorite_ids if isinstance(value, str) and value in known_ids
        ))[:MAX_FAVORITE_LAYOUTS] if isinstance(favorite_ids, list) else []
        self.symbol = self._top_button(None,'查找标的',lambda:self._ask_symbol(False),text='标的',requires_chart=False)
        self.compare_button = self._top_button('compare','添加比较标的',lambda:self._ask_symbol(True))
        self._separator()
        self._build_period_strip()
        self.chart_types={}
        for glyph,kind,label in [('candle','candles','蜡烛图'),('chartLine','line','折线图')]:
            self.chart_types[kind]=self._top_button(glyph,label,lambda k=kind:self.command('chartType',k))
        self._separator()
        self._top_button('indicators','指标',lambda:self.command('action','indicators'))
        self._separator()
        for action,label in [('alert','提醒'),('replay','回放'),('undo','撤销'),('redo','重做')]:
            self._top_button(action,label,lambda a=action:self.command('action',a))
        self.layout_button=self._top_button(None,'图表布局：选择行列',self._open_layout_picker,
                                           parent=self.right,requires_chart=False)
        self.layout_button.bind('<ButtonPress-1>',self._dismiss_layout_on_press,add='+')
        self.layout_manager=self._menu_button(self.right,text='默认布局')
        ToolTip(self.layout_manager,text='管理已保存布局',delay=650)
        self.layout_shortcuts = tk.Frame(self.right,autostyle=False,background=APP_BG)
        self.layout_shortcuts.pack(side=tk.LEFT)
        self.sync_button=self._menu_button(self.right,text='联动 ⌄')
        sync_menu = self._menu(self.sync_button)
        for key, label in zip(SYNC_KEYS, ('标的','周期','十字线','时间位置','可见日期范围')):
            sync_menu.add_checkbutton(label=label, variable=self.sync[key], command=self.preferences_changed)
        sync_menu.add_separator()
        scope_menu = self._menu(sync_menu)
        for key, label in [('pane','本图'),('layout','当前布局'),('global','全部布局')]:
            scope_menu.add_radiobutton(label=label, variable=self.scope, value=key, command=self.preferences_changed)
        sync_menu.add_cascade(label='新绘图默认范围', menu=scope_menu)
        self.sync_button.configure(menu=sync_menu)
        self._separator(self.right)
        self.quick_search_button = self._top_button('search','快捷搜索（Ctrl+K）',self.open_quick_search,
                                                    parent=self.right,requires_chart=False)
        self.settings_button = self._top_button('settings','活动图设置',lambda:self.command('action','settings'),
                                                 parent=self.right)
        self.fullscreen_button = self._top_button('fullscreen','全屏／退出全屏（F11）',self.toggle_fullscreen,
                                                   parent=self.right,requires_chart=False)
        self.snapshot_button=self._menu_button(self.right,image=self._layout_image('camera',22))
        snapshot=self._menu(self.snapshot_button)
        for label, action, shortcut in [
            ('下载图片','snapshotSave','Ctrl+Alt+S'),
            ('复制图片','snapshot','Ctrl+Shift+S'),
            ('复制本地图片链接','snapshotLink','Alt+S'),
            ('在浏览器新标签页打开','snapshotOpen',''),
        ]:
            snapshot.add_command(label=label,accelerator=shortcut,command=lambda a=action:self.command('action',a))
        snapshot.add_separator()
        snapshot.add_command(label='Post image  #',state=tk.DISABLED)
        self.snapshot_button.configure(menu=snapshot)
        ToolTip(self.snapshot_button,text='活动图快照',delay=650)
        self._top_actions.append(self.snapshot_button)
        idea=self._top_button('idea','发布想法：功能暂未接入',None,parent=self.right,requires_chart=False)
        idea.configure(text='#',compound=tk.RIGHT,state=tk.DISABLED)
        if grid.on_trade:
            self.trade_button = self._top_button(None,'打开交易管理',grid.on_trade,text='交易',
                                                 parent=self.right,requires_chart=False)
        more=self._menu_button(self.left_actions,text='⋯')
        menu=self._menu(more)
        menu.add_command(label='对象树', command=lambda:self.command('action','objects'))
        menu.add_separator()
        for label,method,widget in [('在 TradingView 打开','_open_tv','_tv_btn'),
                                    ('更新活动标的 K 线','_refresh_manual','_history_btn'),
                                    ('搜索活动标的案例','_open_cases','_cases_btn')]:
            menu.add_command(label=label,command=lambda m=method,b=widget:self._active_tile_action(m,b))
        more.configure(menu=menu)
        self._top_actions.append(more)
        self.bottom=tk.Frame(grid,autostyle=False,background=APP_BG)
        self.bottom.grid(row=2,column=1,sticky=tk.EW,padx=2)
        self.ranges=tk.Frame(self.bottom,autostyle=False,background=APP_BG)
        self.ranges.pack(side=tk.LEFT)
        self.range_buttons=[]
        self.range_more=self._menu_button(self.ranges,text='日期范围 ⌄')
        range_menu=self._menu(self.range_more)
        self.range_more.configure(menu=range_menu)
        self._top_actions.append(self.range_more)
        for label in RANGE_PRESETS:
            button=self._top_button(None,f'可见范围：{label}',lambda v=label:self.command('rangePreset',v),
                                    text=label,parent=self.ranges)
            self.range_buttons.append(button)
            range_menu.add_command(label=label,command=lambda v=label:self.command('rangePreset',v))
        self.bottom.bind('<Configure>',self._fit_ranges,add='+')
        self.target = tk.StringVar(value='活动图：未选择标的')
        status_row=ttk.Frame(grid)
        status_row.grid(row=3,column=0,columnspan=2,sticky=tk.EW,padx=5)
        ttk.Label(status_row,textvariable=self.target,foreground=MUTED,anchor=tk.W,width=1).pack(side=tk.LEFT,fill=tk.X,expand=True)
        self.status = ttk.Label(status_row, textvariable=self.caption, foreground=MUTED)
        self.status.pack(side=tk.RIGHT)
        from .workspace_toolbar import SharedDrawingTools
        self.drawing_tools=SharedDrawingTools(self)
        self.left=self.drawing_tools.rail
        toolbar=saved['data'].get('toolbar',{})
        if isinstance(toolbar,dict):
            self.drawing_tools.visible=toolbar.get('favoritesVisible',True) is not False
            position=toolbar.get('position')
            if (isinstance(position,list) and len(position)==2 and
                    all(isinstance(v,(int,float)) and 0<=v<=1 for v in position)):
                self.drawing_tools.position=tuple(position)
            favorites=toolbar.get('favorites')
            if isinstance(favorites,list) and all(isinstance(v,str) and v in self.drawing_tools.favorite_buttons for v in favorites):
                self.drawing_tools.favorite_selection=set(favorites)
                self.drawing_tools.rebuild_favorites()
        self.grid.after_idle(self.drawing_tools.place_favorites)
        for frame in (self.top,self.left_actions,self.right):
            frame.bind('<Configure>',self._fit_header,add='+')
        self._layout_menu()

    def _separator(self,parent=None):
        tk.Frame(parent or self.left_actions,autostyle=False,background='#424242',width=1).pack(side=tk.LEFT,fill=tk.Y,padx=5,pady=4)

    def _fit_header(self,_event=None):
        width = self.left_actions.winfo_reqwidth() + self.right.winfo_reqwidth() + 10
        compact = self.top.winfo_width() < width
        if compact != getattr(self,'_compact_header',None):
            self._compact_header = compact
            self.right.grid_configure(row=1 if compact else 0,column=0 if compact else 1,
                                      columnspan=2 if compact else 1)

    def open_quick_search(self):
        if getattr(self,'_search_open',False):
            return
        from .workspace_toolbar import WorkspaceQuickSearch, FAVORITE_CHOICES
        commands = [('查找标的',lambda:self._ask_symbol(False)),
                    ('选择图表布局',self._open_layout_picker),
                    ('全屏／退出全屏',self.toggle_fullscreen)]
        commands.extend((f'布局 · {layout["name"]}',lambda key=layout['id']:self.open_layout(key))
                        for layout in self.layouts)
        if self.drawing_tools.ready:
            for label, value in [('图表设置','settings'),('指标','indicators'),('价格提醒','alert'),
                                 ('回放','replay'),('对象树','objects'),('下载图片','snapshotSave'),
                                 ('复制图片','snapshot')]:
                commands.append((label,lambda v=value:self.command('action',v)))
            for tool, label in FAVORITE_CHOICES:
                commands.append((label,lambda t=tool:self.command('tool',t)))
        self._search_open = True
        try:
            dialog = WorkspaceQuickSearch(self.grid.winfo_toplevel(),commands)
        finally:
            self._search_open = False
        if dialog.result:
            self.grid.after_idle(dialog.result)

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.fullscreen)

    def set_fullscreen(self, enabled):
        if enabled == self.fullscreen:
            return
        self.fullscreen = enabled
        self.grid.winfo_toplevel().attributes('-fullscreen',enabled)
        if self.grid.on_fullscreen:
            self.grid.on_fullscreen(enabled)
        self.fullscreen_button.configure(image=self._layout_image('restore' if enabled else 'fullscreen',22),
                                         background=CONTROL_BG if enabled else APP_BG)

    def _top_button(self,glyph,title,action,*,text='',parent=None,requires_chart=True):
        button=tk.Button(parent or self.left_actions,autostyle=False,text=text,background=APP_BG,foreground=TEXT,
            activebackground=CONTROL_BG,activeforeground=TEXT,disabledforeground='#686D76',
            borderwidth=0,font=('Microsoft YaHei UI',10),padx=5,pady=4,command=action)
        if glyph:button.configure(image=self._layout_image(glyph,22))
        button.pack(side=tk.LEFT,padx=1)
        ToolTip(button,text=title,delay=650)
        if requires_chart:self._top_actions.append(button)
        return button

    @staticmethod
    def _menu_button(parent,**kwargs):
        button=tk.Menubutton(parent,autostyle=False,foreground=TEXT,background=APP_BG,
            activebackground=CONTROL_BG,activeforeground=TEXT,disabledforeground='#686D76',borderwidth=0,
            font=('Microsoft YaHei UI',10),padx=5,pady=4,indicatoron=False,**kwargs)
        button.pack(side=tk.LEFT,padx=1)
        return button

    @staticmethod
    def _menu(parent):
        return tk.Menu(parent,tearoff=False,background='#17191D',foreground=TEXT,activebackground=CONTROL_BG,
                       activeforeground=TEXT,disabledforeground=MUTED,borderwidth=1)

    def _ask_symbol(self,compare):
        from workbench.market import code_of
        value=simpledialog.askstring('添加比较' if compare else '查找标的','输入证券代码（例如 000906）',parent=self.grid)
        if value:
            try:
                code=code_of(value)
            except ValueError:
                self.caption.set('请输入有效的六位证券代码')
                return
            if compare:
                self.grid.compare(code)
            else:
                self.grid.open_symbol(code)

    def _active_tile_action(self,method,widget):
        index=self.grid._active_slot
        if not 0<=index<len(self.grid._slots):return
        slot=self.grid._slots[index]
        button=getattr(slot,widget,None)
        enabled=(bool(getattr(slot,'_manual_controller',None)) if widget=='_history_btn' else
                 button is not None and button.cget('state')!=tk.DISABLED)
        if enabled:getattr(slot,method)()
        else:self.caption.set('活动图暂不支持此操作')

    def _fit_ranges(self,_event=None):
        required=sum(button.winfo_reqwidth()+2 for button in self.range_buttons)
        compact=self.bottom.winfo_width()<required
        if compact==getattr(self,'_compact_ranges',None):return
        self._compact_ranges=compact
        if compact:
            for button in self.range_buttons:button.pack_forget()
            self.range_more.pack(side=tk.LEFT)
        else:
            self.range_more.pack_forget()
            for button in self.range_buttons:button.pack(side=tk.LEFT,padx=1)

    def _build_period_strip(self):
        strip=tk.Frame(self.left_actions,autostyle=False,background=APP_BG)
        strip.pack(side=tk.LEFT,padx=(0,5))
        self.period_buttons={}
        for period,label in QUICK_PERIODS:
            button=tk.Button(strip,autostyle=False,text=label,foreground=TEXT,background=APP_BG,
                activebackground=CONTROL_BG,activeforeground=TEXT,disabledforeground='#686D76',borderwidth=0,
                font=('Microsoft YaHei UI',10),padx=5,pady=4,command=lambda p=period:self.select_period(p))
            button.pack(side=tk.LEFT,padx=1)
            ToolTip(button,text=f'活动图周期：{PERIOD_LABELS[period]}',delay=650)
            self.period_buttons[period]=button
        self.period_more=self._menu_button(strip,text='⌄')
        self.period_options=self._menu(self.period_more)
        for period,label in PERIOD_LABELS.items():
            self.period_options.add_command(label=label,command=lambda p=period:self.select_period(p))
        self.period_more.configure(menu=self.period_options)
        ToolTip(self.period_more,text='更多图表周期',delay=650)
        self._separator()

    def _paint_period_strip(self,state,ready):
        current=state.get('timeframe')
        available=tuple(state.get('available') or ()) if ready else ()
        visual=(current,available,ready)
        if visual==getattr(self,'_period_visual',None):return
        self._period_visual=visual
        for period,button in self.period_buttons.items():
            button.configure(state=tk.NORMAL if period in available else tk.DISABLED,
                             background='#33363B' if period==current else APP_BG)
        for index,period in enumerate(PERIOD_LABELS):
            self.period_options.entryconfigure(index,state=tk.NORMAL if period in available else tk.DISABLED)
        extra=PERIOD_LABELS.get(current,'') if current not in self.period_buttons else ''
        self.period_more.configure(text=f'{extra} ⌄' if extra else '⌄',state=tk.NORMAL if ready else tk.DISABLED)

    def show_active_target(self,state,ready):
        self._paint_period_strip(state,ready)
        self._show_layout_shape()
        index=self.grid._active_slot
        item=self.grid.current_item()
        symbol=item.code.split('.')[-1] if item else '标的'
        chart_type=state.get('chartType','candles')
        if (symbol,chart_type,ready)!=getattr(self,'_symbol_visual',None):
            self._symbol_visual=(symbol,chart_type,ready)
            self.symbol.configure(text=symbol,background='#252525')
            for kind,button in self.chart_types.items():
                button.configure(background='#33363B' if ready and chart_type==kind else APP_BG)
        slot=self.grid._slots[index] if 0<=index<len(self.grid._slots) else None
        title=slot._title_var.get() if slot else ''
        date=state.get('asof') or ''
        target=f'活动图 {index+1} · {title or (item.code if item else "未选择标的")}'
        if not ready:target+=' · 等待图表就绪'
        elif date and date not in title:target+=f' · 行情 {date}'
        if target!=self.target.get():self.target.set(target)
        if ready!=getattr(self,'_target_ready',None):
            self._target_ready=ready
            for button in self._top_actions:
                button.configure(state=tk.NORMAL if ready else tk.DISABLED)

    def activate(self):
        if hasattr(self,'drawing_tools'):
            self.drawing_tools.refresh()

    def descriptor(self, index):
        return {'layout':self.current['id'], 'pane':str(index), 'defaultScope':self.scope.get(),
                'active':index==self.grid._active_slot,'multi':self.grid.layout_count>1}

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

    def select_period(self,period):
        if period not in self.drawing_tools.state.get('available',()) or not self.drawing_tools.ready:
            return False
        slot = self.grid._slots[self.grid._active_slot]
        if self.command('timeframe',period):
            slot._workspace_restore=None
            return True
        return False

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
        if not 0<=index<len(self.grid._slots):
            return
        slot = self.grid._slots[index]
        host=getattr(slot,'_embedded',None)
        if host and state.get('token') is not None and state.get('token')!=host.token:
            return
        old = self.states.get(index, {})
        self.states[index] = state
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
                result = self.store.save_ui_preferences('chart_layouts',
                    {'layouts':self.layouts, 'active':self.current['id'], 'toolbar':self.toolbar_preferences()}, self.revision)
                self.revision = result['revision']
                self.caption.set(f'{self.current["name"]} · 已自动保存 · 活动图格 {self.grid._active_slot+1}')
            except Exception as exc:
                self.caption.set('布局未保存：' + str(exc))

    def toolbar_preferences(self):
        toolbar=getattr(self,'drawing_tools',None)
        return {'favoritesVisible':toolbar.visible,'position':list(toolbar.position),
                'favorites':sorted(toolbar.favorite_selection),'favoriteLayouts':self.favorite_layouts} if toolbar else {}

    def restore_saved_layout(self):
        self._enabled = True
        if self.current['panes']:
            self.open_layout(self.current['id'], save_previous=False)

    def _layout_image(self,shape,size):
        scale=self.grid.winfo_fpixels('1i')/96
        key=(shape,round(size*scale))
        if key not in self._layout_icons:self._layout_icons[key]=icon(self.grid,shape,key[1])
        return self._layout_icons[key]

    def _show_layout_shape(self):
        shape=self.grid._shape
        if shape!=getattr(self,'_shown_shape',None):
            self._shown_shape=shape
            self.layout_button.configure(image=self._layout_image(shape,24))

    def _close_layout_picker(self):
        if self._layout_popup is not None:
            self._layout_popup.destroy()
            self._layout_popup=None

    def _dismiss_layout_on_press(self,_event):
        if self._layout_popup is not None:
            self._close_layout_picker()
            return 'break'

    def _open_layout_picker(self):
        if self._layout_popup is not None:
            self._close_layout_picker();return
        popup=self._layout_popup=tk.Toplevel(self.grid,autostyle=False)
        popup.withdraw();popup.overrideredirect(True);popup.configure(background='#17191D',padx=7,pady=6)
        ttk.Label(popup,text='图表布局',foreground=MUTED).pack(anchor=tk.W,padx=4,pady=(0,4))
        row=tk.Frame(popup,autostyle=False,background='#17191D');row.pack(fill=tk.X)
        popup.choices={}
        for count,shape in SHAPES.items():
            button=tk.Button(row,autostyle=False,image=self._layout_image(shape,32),text=f'{count}图',
                compound=tk.TOP,foreground=TEXT,background='#34373D' if shape==self.grid._shape else '#17191D',
                activebackground=CONTROL_BG,activeforeground=TEXT,borderwidth=0,padx=6,pady=4,
                command=lambda c=count:self._select_layout_shape(c))
            button.pack(side=tk.LEFT,padx=2);popup.choices[count]=button
            ToolTip(button,text=f'{shape[0]} 行 × {shape[1]} 列',delay=650)
        ttk.Button(popup,text='自定义行列…',bootstyle='secondary',
            command=lambda:(self._close_layout_picker(),self.custom_grid())).pack(anchor=tk.W,pady=(4,0))
        popup.update_idletasks()
        x=min(self.layout_button.winfo_rootx(),popup.winfo_screenwidth()-popup.winfo_reqwidth()-6)
        y=self.layout_button.winfo_rooty()+self.layout_button.winfo_height()+4
        popup.geometry(f'+{max(0,x)}+{max(0,y)}');popup.deiconify();popup.lift();popup.focus_force()
        popup.bind('<Escape>',lambda _e:self._close_layout_picker())
        popup.bind('<FocusOut>',lambda _e:self.grid.after_idle(self._check_layout_focus,popup))

    def _check_layout_focus(self,popup):
        if self._layout_popup is popup:
            focus=self.grid.focus_get()
            if focus is None or focus.winfo_toplevel() is not popup:
                self._close_layout_picker()

    def _select_layout_shape(self,count):
        self._close_layout_picker();self.grid.set_layout(count);self._show_layout_shape()

    def _layout_menu(self):
        menu = self._menu(self.layout_manager)
        for layout in self.layouts:
            star = '★  ' if layout['id'] in self.favorite_layouts else ''
            menu.add_command(label=star+layout['name'], command=lambda i=layout['id']:self.open_layout(i))
        menu.add_separator()
        menu.add_command(label='新建布局…', command=lambda:self.new(False))
        menu.add_command(label='复制当前布局…', command=lambda:self.new(True))
        menu.add_command(label='重命名…', command=self.rename)
        menu.add_command(label='保存布局', command=self.save)
        favorite = self.current['id'] in self.favorite_layouts
        menu.add_command(label='从常用布局移除' if favorite else '设为常用布局',
                         command=self._toggle_favorite_layout)
        menu.add_command(label='归档当前布局（保留画线）…', command=self.delete)
        name=self.current['name']
        self.layout_manager.configure(menu=menu,text=(name if len(name)<=8 else name[:7]+'…')+' ⌄')
        previous = getattr(self,'_saved_layout_menu',None)
        if previous is not None:
            previous.destroy()
        self._saved_layout_menu = menu
        self._refresh_layout_shortcuts()
        self._show_layout_shape()

    def _toggle_favorite_layout(self):
        identity = self.current['id']
        if identity in self.favorite_layouts:
            self.favorite_layouts.remove(identity)
        elif len(self.favorite_layouts) < MAX_FAVORITE_LAYOUTS:
            self.favorite_layouts.append(identity)
        else:
            self.caption.set('常用布局最多 4 个，请先从布局菜单移除一个')
            return
        self._layout_menu()
        self.schedule_save()

    def _refresh_layout_shortcuts(self):
        for child in self.layout_shortcuts.winfo_children():
            child.destroy()
        by_id = {layout['id']:layout for layout in self.layouts}
        for identity in self.favorite_layouts:
            layout = by_id[identity]
            active = identity == self.current['id']
            label = layout['name'][:1]
            button = self._top_button(None,layout['name'],lambda key=identity:self._open_favorite_layout(key),
                                      text=label,parent=self.layout_shortcuts,requires_chart=False)
            button.configure(image=self._layout_image('activeBadge' if active else 'badge',24),
                             compound=tk.CENTER,padx=1,
                             foreground='#161616' if active else TEXT)

    def _open_favorite_layout(self, identity):
        if identity != self.current['id']:
            self.open_layout(identity)

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
                        {'layouts':self.layouts+[layout],'active':layout['id'],'toolbar':self.toolbar_preferences()}, self.revision,
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
            if deleted['id'] in self.favorite_layouts:
                self.favorite_layouts.remove(deleted['id'])
            self.save();self._layout_menu()

    def open_layout(self, identity, save_previous=True):
        if save_previous and self.current['id'] != identity:
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
