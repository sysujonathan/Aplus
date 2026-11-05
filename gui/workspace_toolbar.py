"""One native drawing rail and favorites bar, bound to the active renderer."""
import tkinter as tk
from ttkbootstrap.widgets import ToolTip

from .workspace_icons import icon
from .theme import APP_BG, CONTROL_BG, TEXT, MUTED
from .tree_scroll import AutoHideScrollbar, wheel_scroll_units


FAVORITES = (
    ('fib','斐波那契回撤'), ('long','多头仓位'), ('short','空头仓位'),
    ('trend','趋势线'), ('ray','水平射线'), ('up','向上箭头'), ('down','向下箭头'),
    ('rectangle','矩形'), ('channel','平行通道'), ('arrow','箭头标记'), ('path','路径'),
)
GROUPS = {
    'cross': [('十字光标','cross'),('点状光标',None),('箭头光标',None)],
    'trend': [('线',),('趋势线','trend'),('射线',None),('信息线',None),('延长线',None),('趋势角度',None),
              ('水平线',None),('水平射线','ray'),('垂直线',None),('横线',None),
              ('通道',),('平行通道','channel'),('回归趋势',None),('平顶/平底',None),('不相交通道',None),
              ('分叉线',),('分叉线',None),('希夫干草叉',None),('改良希夫干草叉',None),('内部分叉线',None)],
    'fib': [('斐波那契',),('斐波那契回撤','fib'),('斐波那契趋势扩展',None),
            ('斐波那契通道',None),('斐波那契时区',None),('斐波那契速度阻力扇',None),
            ('斐波那契趋势时间',None),('斐波那契圈',None),('斐波那契螺旋',None),
            ('斐波那契速度阻力弧',None),('斐波那契楔形',None),('倾斜扇形',None),
            ('江恩',),('江恩箱',None),('固定江恩正方',None),('江恩正方',None),('江恩扇',None)],
    'patterns': [('形态',),('XABCD形态',None),('头肩形态',None),('艾略特波浪',None)],
    'long': [('预测',),('多头仓位','long'),('空头仓位','short'),('仓位预测',None),('K线形态',None),
             ('模拟K线',None),('扇形',None),('基于成交量',),('锚定VWAP',None),('固定范围成交量分布图',None),
             ('锚定成交量分布图',None),('测量',),('价格范围','ruler'),('日期范围',None),('日期和价格范围',None)],
    'path': [('笔刷',),('笔刷',None),('荧光笔',None),('箭头',),('箭头标记','arrow'),('箭头',None),
             ('向上箭头','up'),('向下箭头','down'),('形状',),('矩形','rectangle'),('旋转矩形',None),
             ('路径','path'),('圆',None),('椭圆',None),('折线',None),('三角形',None),('弧形',None),('曲线',None),('双曲线',None)],
    'text': [('文字','text'),('便签',None)],
    'smile': [('图标','smile')],
    'zoom': [('放大','zoom'),('缩小','zoomOut')],
}


class SharedDrawingTools:
    def __init__(self, controls):
        self.controls, self.grid = controls, controls.grid
        self.scale = self.grid.winfo_fpixels('1i') / 96
        self.images, self.buttons, self.arrows = {}, {}, {}
        self.popup = None
        self.active_key = None
        self.ready = False
        self.state = {}
        self.position = (.5, .10)
        self.visible = True
        self._timer = None
        self._active_sends={}
        self._last_visual=None
        self.group_primary={'cross':'cross','trend':'trend','fib':'fib','patterns':None,
                            'long':'long','path':None,'text':'text','smile':'smile','zoom':'zoom'}
        self.favorite_selection={tool for tool,_ in FAVORITES}
        self.rail = tk.Frame(self.grid,autostyle=False, background=APP_BG, padx=2, pady=3)
        self.rail.grid(row=1,column=0,sticky=tk.NS)
        for tool, title in [('cross','光标'),('trend','趋势线工具'),('fib','斐波那契与江恩工具'),
                            ('patterns','形态工具'),('long','预测与测量工具'),
                            ('path','几何形状'),('text','注释工具'),('smile','图标')]:
            self._group(tool,title)
        self._separator()
        self._button(self.rail,'ruler','测量：在图上拖动，仅读取幅度与跨度').pack(pady=1)
        self._group('zoom','框选缩放')
        self._separator()
        self._group('magnet','磁铁模式', [('弱磁铁','magnetMode','weak'),('强磁铁',None),('吸附到指标',None)],
                    command=lambda:self.send('tool','magnet'))
        stay=tk.Frame(self.rail,autostyle=False,background=APP_BG)
        stay.pack(fill=tk.X)
        self._button(stay,'stay','继续绘图：功能暂未实现').pack(side=tk.LEFT)
        self.buttons['stay'][0]._unimplemented=True
        tk.Label(stay,autostyle=False,text='#',font=('Segoe UI',9),foreground='#777C84',background=APP_BG).pack(side=tk.RIGHT)
        self._group('lock','锁定绘图', [('锁定绘图','tool','lock'),('锁定指标',None),('锁定所有',None)],
                    command=lambda:self.send('tool','lock'))
        self._group('eye','显隐工具', [('显示／隐藏上次选择','visibility',None),
            ('显示／隐藏绘图','visibility','drawings'),('显示／隐藏指标','visibility','indicators'),
            ('显示／隐藏策略证据','visibility','strategy'),('显示／隐藏全部','visibility','all')],
            command=lambda:self.send('visibility'))
        self._separator()
        self._group('trash','移除对象', [('移除绘图','remove','drawings'),
            ('移除指标','remove','indicators'),('移除绘图和指标','remove','all')],
            command=lambda:self.menu('trash'))
        self._button(self.rail,'star','显示／隐藏收藏工具栏',command=self.toggle_favorites,
                     pane=False).pack(side=tk.BOTTOM,pady=5)
        self.favorites = tk.Frame(self.grid,autostyle=False,background='#181818',highlightbackground='#303030',
                                  highlightthickness=1,padx=5,pady=3)
        handle = tk.Canvas(self.favorites,autostyle=False,width=round(11*self.scale),height=round(18*self.scale),
                           background='#181818',highlightthickness=0,cursor='fleur')
        for x in (3,8):
            for y in (4,9,14):
                handle.create_oval(*[v*self.scale for v in (x-1,y-1,x+1,y+1)],fill='#686B71',outline='')
        handle.pack(side=tk.LEFT,padx=(0,4))
        handle.bind('<ButtonPress-1>',self._drag_start)
        handle.bind('<B1-Motion>',self._drag)
        handle.bind('<ButtonRelease-1>',lambda _e:self.controls.schedule_save())
        for tool,title in FAVORITES:
            self._button(self.favorites,tool,title).pack(side=tk.LEFT,padx=1)
        self.grid.bind('<Configure>',lambda _e:self.place_favorites(),add='+')
        self.grid.bind('<Destroy>',self._destroy,add='+')
        self._timer=self.grid.after(100,self._poll)

    def _image(self,name,disabled=False,latched=False):
        key=(name,disabled,latched)
        if key not in self.images:
            color='#555B63' if disabled else '#171717' if latched else '#C4C6CB'
            self.images[key]=icon(self.grid,name,round(24*self.scale),color)
        return self.images[key]

    def _button(self,parent,tool,title,command=None,pane=True):
        button=tk.Button(parent,autostyle=False,image=self._image(tool),background=APP_BG if parent is self.rail else '#181818',
            activebackground='#353535',disabledforeground='#555B63',borderwidth=0,relief=tk.FLAT,
            padx=round(5*self.scale),pady=round(4*self.scale),takefocus=True,
            command=command or (lambda:self.send('tool',tool)))
        button._pane_control=pane
        button._tool=tool
        self.buttons.setdefault(tool,[]).append(button)
        ToolTip(button,text=title,delay=650,wraplength=240)
        return button

    def _separator(self):
        tk.Frame(self.rail,autostyle=False,background='#393C41',height=1).pack(fill=tk.X,padx=5,pady=5)

    def _group(self,tool,title,entries=None,command=None):
        row=tk.Frame(self.rail,autostyle=False,background=APP_BG)
        row.pack(fill=tk.X,pady=1)
        unsupported=tool=='patterns'
        self._button(row,tool,'功能暂未实现' if unsupported else title,
            command=command or (lambda:self.primary(tool))).pack(side=tk.LEFT)
        self.buttons[tool][-1]._group=tool
        if unsupported:
            tk.Label(row,autostyle=False,text='#',font=('Segoe UI',9),background=APP_BG,foreground='#777C84').place(relx=.62,y=0)
        arrow=tk.Button(row,autostyle=False,text='›',font=('Segoe UI',10),background=APP_BG,foreground=MUTED,
            activebackground=CONTROL_BG,borderwidth=0,padx=0,width=1,command=lambda:self.menu(tool))
        arrow.pack(side=tk.RIGHT,fill=tk.Y)
        ToolTip(arrow,text=title+'：展开子菜单',delay=650)
        self.arrows[tool]=arrow
        if not hasattr(self,'entries'):self.entries={}
        self.entries[tool]=entries or GROUPS[tool]

    def primary(self,group):
        tool=self.group_primary.get(group)
        return self.send('tool',tool) if tool else self.menu(group)

    def send(self,kind,value=None):
        return self.controls.command(kind,value)

    def close_menu(self):
        if self.popup is not None:
            try:self.popup.destroy()
            except tk.TclError:pass
            self.popup=None

    def menu(self,group):
        self.close_menu()
        popup=self.popup=tk.Toplevel(self.grid)
        popup.withdraw();popup.overrideredirect(True)
        popup.configure(background='#33363B')
        surface=tk.Frame(popup,autostyle=False,background='#16181C')
        surface.pack(fill=tk.BOTH,expand=True,padx=1,pady=1)
        canvas=tk.Canvas(surface,autostyle=False,background='#16181C',highlightthickness=0,width=round(292*self.scale))
        scroll=AutoHideScrollbar(surface,orient=tk.VERTICAL,command=canvas.yview,bootstyle='secondary-round')
        canvas.configure(yscrollcommand=scroll.set)
        surface.rowconfigure(0,weight=1);surface.columnconfigure(0,weight=1)
        scroll.grid(row=0,column=1,sticky=tk.NS);canvas.grid(row=0,column=0,sticky=tk.NSEW)
        body=tk.Frame(canvas,autostyle=False,background='#16181C')
        item=canvas.create_window(0,0,window=body,anchor=tk.NW)
        body.bind('<Configure>',lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        popup.bind('<MouseWheel>',lambda e:canvas.yview_scroll(wheel_scroll_units(e),'units'))
        popup._entries=[]
        for entry in self.entries[group]:
            if len(entry)==1:
                tk.Label(body,autostyle=False,text=entry[0],foreground='#8B9098',background='#16181C',anchor=tk.W,
                         font=('Microsoft YaHei UI',9),padx=14,pady=6).pack(fill=tk.X)
                continue
            name,action=entry[:2]
            value=entry[2] if len(entry)>2 else action
            kind=action if len(entry)>2 else 'tool'
            available=action is not None
            enabled=available and self.ready
            if kind=='remove':
                drawings=int(self.state.get('drawingCount',0))
                indicators=int(self.state.get('indicatorCount',0))
                name={'drawings':f'移除{drawings}个绘图','indicators':f'移除{indicators}个指标',
                      'all':f'移除{drawings}个绘图和{indicators}个指标'}[value]
                enabled=enabled and {'drawings':drawings,'indicators':indicators,'all':drawings+indicators}[value]>0
            row=tk.Frame(body,autostyle=False,background='#16181C')
            row.pack(fill=tk.X,padx=5,pady=1)
            button=tk.Button(row,autostyle=False,text=name,image=self._image(value if kind=='tool' else group),compound=tk.LEFT,
                anchor=tk.W,padx=8,pady=round(5*self.scale),background='#16181C',foreground=TEXT,
                activebackground='#2B3038',activeforeground=TEXT,borderwidth=0,
                font=('Microsoft YaHei UI',10),state=tk.NORMAL if enabled else tk.DISABLED,
                command=lambda k=kind,v=value:self._choose(k,v))
            button.pack(side=tk.LEFT,fill=tk.X,expand=True)
            if not available:
                marker=tk.Label(row,autostyle=False,text='#',font=('Segoe UI',9),foreground='#777C84',background='#16181C')
                marker.pack(side=tk.RIGHT,padx=8)
                ToolTip(marker,text='功能暂未实现',delay=400)
                ToolTip(button,text='功能暂未实现',delay=400)
            popup._entries.append((name,action,button))
            if kind=='tool' and value in dict(FAVORITES):
                favorite=tk.Button(row,autostyle=False,text='★' if value in self.favorite_selection else '☆',
                    foreground='#B8BBC2',background='#16181C',activebackground='#30343B',
                    borderwidth=0,font=('Segoe UI',12),command=lambda t=value:self.toggle_favorite(t))
                favorite.pack(side=tk.RIGHT,padx=4)
                ToolTip(favorite,text='添加到／从收藏工具栏移除',delay=600)
        if group=='trash':
            self.remove_locked=tk.BooleanVar(value=bool(self.state.get('removeLocked')))
            tk.Checkbutton(body,autostyle=False,text='始终删除锁定的绘图',variable=self.remove_locked,
                background='#16181C',foreground=TEXT,selectcolor='#282D35',activebackground='#16181C',
                font=('Microsoft YaHei UI',9),state=tk.NORMAL if self.ready else tk.DISABLED,
                command=lambda:self.send('removeLocked',self.remove_locked.get())).pack(padx=8,pady=7,anchor=tk.W)
        popup.update_idletasks()
        height=min(body.winfo_reqheight()+2,popup.winfo_screenheight()-80)
        arrow=self.arrows[group]
        x=max(0,min(arrow.winfo_rootx()+arrow.winfo_width()+4,popup.winfo_screenwidth()-surface.winfo_reqwidth()-8))
        y=max(8,min(arrow.winfo_rooty(),popup.winfo_screenheight()-height-20))
        popup.geometry(f'{surface.winfo_reqwidth()}x{height}+{x}+{y}')
        popup.deiconify();popup.lift();popup.focus_force()
        popup.bind('<Escape>',lambda _e:self.close_menu())
        popup.bind('<FocusOut>',lambda _e:self.grid.after_idle(self._menu_focus))

    def _menu_focus(self):
        if self.popup and not str(self.grid.focus_get() or '').startswith(str(self.popup)):
            self.close_menu()

    def _choose(self,kind,value):
        self.close_menu();self.send(kind,value)

    def toggle_favorite(self,tool):
        if tool in self.favorite_selection:self.favorite_selection.remove(tool)
        else:self.favorite_selection.add(tool)
        self.rebuild_favorites()
        self.close_menu();self.controls.schedule_save()

    def rebuild_favorites(self):
        for tool,_ in FAVORITES:
            for button in self.buttons.get(tool,[]):
                if button.master is self.favorites:
                    button.pack_forget()
                    if tool in self.favorite_selection:button.pack(side=tk.LEFT,padx=1)
        self.place_favorites()

    def toggle_favorites(self):
        self.visible=not self.visible
        self.place_favorites();self.controls.schedule_save()
        self.refresh()

    def place_favorites(self):
        if not hasattr(self.grid,'host') or not self.visible or not self.favorite_selection:
            self.favorites.place_forget()
            self.favorites.pack_forget()
            self.controls._fit_ranges()
            return
        if self.grid.layout_count>1:
            self.favorites.place_forget()
            self.favorites.pack(in_=self.controls.bottom,side=tk.LEFT,padx=(6,0))
            self.controls._fit_ranges()
            return
        self.favorites.pack_forget()
        host=self.grid.host
        self.favorites.update_idletasks()
        width=self.favorites.winfo_reqwidth()
        x=max(0,min(round(host.winfo_width()*self.position[0]-width/2),host.winfo_width()-width))
        y=max(0,min(round(host.winfo_height()*self.position[1]),host.winfo_height()-self.favorites.winfo_reqheight()))
        self.favorites.place(in_=host,x=x,y=y)
        self.favorites.lift()
        self.controls._fit_ranges()

    def _drag_start(self,event):
        host=self.grid.host
        self._drag_origin=(event.x_root,event.y_root,
            self.favorites.winfo_rootx()-host.winfo_rootx(),self.favorites.winfo_rooty()-host.winfo_rooty())

    def _drag(self,event):
        if self.grid.layout_count>1:return
        ox,oy,x,y=self._drag_origin
        host=self.grid.host
        fx=x+event.x_root-ox+self.favorites.winfo_width()/2
        fy=y+event.y_root-oy
        self.position=(max(0,min(1,fx/max(1,host.winfo_width()))),max(0,min(1,fy/max(1,host.winfo_height()))))
        self.place_favorites()

    def refresh(self):
        slots=self.grid._slots
        index=self.grid._active_slot
        for pane,candidate in enumerate(slots):
            peer=getattr(candidate,'_embedded',None)
            desired=(getattr(peer,'token',None),pane==index)
            if peer and self._active_sends.get(pane)!=desired and peer.ready():
                if peer.command('active',pane==index):self._active_sends[pane]=desired
        slot=slots[index] if 0<=index<len(slots) else None
        host=getattr(slot,'_embedded',None)
        state=getattr(host,'_last_workspace_state',None) or {}
        key=(index,getattr(host,'token',None))
        if key!=self.active_key:
            self.close_menu();self.active_key=key
        self.ready=bool(host and host.ready() and state.get('token')==host.token and state.get('identity'))
        self.state=state if self.ready else {}
        tool=self.state.get('tool','cross')
        visual=(self.ready,tool,self.state.get('magnet'),self.state.get('locked'),self.state.get('hidden'),self.visible)
        if visual!=self._last_visual:
            self._last_visual=visual
            self._paint_state(tool)
        self.controls.show_active_target(self.state,self.ready)

    def _paint_state(self,tool):
        for group,entries in self.entries.items():
            if group in self.group_primary and any(len(e)==2 and e[1]==tool for e in entries):
                self.group_primary[group]=tool
        active={tool, *(['magnet'] if self.state.get('magnet') else []),
                *(['lock'] if self.state.get('locked') else []),
                *(['eye'] if self.state.get('hidden') else []), *(['star'] if self.visible else [])}
        for name,buttons in self.buttons.items():
            for button in buttons:
                enabled=self.ready or not button._pane_control
                if getattr(button,'_unimplemented',False):enabled=False
                group=getattr(button,'_group',None)
                glyph=self.group_primary.get(group) or ('brush' if group=='path' else name)
                selected=(self.group_primary.get(group)==tool if group in self.group_primary else name in active)
                latched=enabled and selected and name in ('magnet','lock','eye','star')
                button.configure(state=tk.NORMAL if enabled else tk.DISABLED,
                    image=self._image(glyph,not enabled,latched),background='#ECECEC' if latched else
                    '#34373D' if enabled and selected else
                    '#181818' if button.master is self.favorites else APP_BG)

    def _poll(self):
        self.refresh()
        self._timer=self.grid.after(150,self._poll)

    def _destroy(self,event):
        if event.widget is self.grid:
            self.close_menu()
            if self._timer:self.grid.after_cancel(self._timer)
