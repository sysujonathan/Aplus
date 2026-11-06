"""Low-frequency template maintenance in the existing premarket workspace."""
from __future__ import annotations

from datetime import date
import io
import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from PIL import Image, ImageTk

from workbench.market import code_of, load_dataset, BOARDS
from workbench.shape_matching import TEMPLATES, classic, keywords, drawing_path, normalize
from workbench.shape_store import options
from workbench.sources import market_source, SOURCES
from workbench.store import digest
from .theme import APP_BG, PANEL_BG, TEXT, MUTED, ACCENT


class ShapeDialog(tk.Toplevel):
    def __init__(self,owner,store,service,asof_filter,*,case=None,on_results=None,on_compare=None,current_item=None):
        super().__init__(owner)
        self.title('本地形态捕手 · 模板与每日跟踪')
        self.geometry('1040x780')
        self.minsize(880,700)
        self.configure(background=APP_BG)
        self.store,self.service = store,service
        self.runner = service.shape_tasks()
        self.source = market_source(store)
        self.case,self.current_item = case,current_item
        self.on_results,self.on_compare = on_results,on_compare
        self.current = None
        self.points,self.path = [],None
        self.screenshot,self.photo,self.crop = None,None,None
        self._last_run = None
        self._timer = None
        self._rows = {}
        from .data import latest_market_date
        self.asof = '-'.join(asof_filter) if asof_filter and all(asof_filter) else latest_market_date(store,'daily',self.source)
        self.name = tk.StringVar(value=case.get('title','案例模板') if case else TEMPLATES['w_bottom'][0])
        self.track = tk.BooleanVar(value=False)
        self.window = tk.StringVar(value='60')
        self.threshold = tk.StringVar(value='75')
        self.topn = tk.StringVar(value='30')
        self.scope = tk.StringVar(value='全部本地A股')
        self.codes = tk.StringVar()
        self.status = tk.StringVar(value='相似度仅代表图形接近程度，不是入场信号或胜率')
        self._build()
        self._load_templates()
        if case:
            self.tabs.select(self.case_page)
            self.code.set(case.get('symbol',''))
            self.cutoff.set('')  # A screenshot's date boundary must be confirmed.
        else:
            self._classic_changed()
        self._refresh_history()
        self._poll()
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _build(self):
        self.columnconfigure(0,weight=1)
        self.rowconfigure(1,weight=1)
        header = ttk.Frame(self,padding=(16,12))
        header.grid(row=0,column=0,sticky=tk.EW)
        ttk.Label(header,text='本地形态捕手',font=('Microsoft YaHei UI',15,'bold')).pack(side=tk.LEFT)
        ttk.Label(header,text=f"研究 · {SOURCES[self.source]} · 匹配截止 {self.asof or '暂无行情'}",foreground=MUTED).pack(side=tk.RIGHT)
        body = ttk.Frame(self,padding=(16,0,16,8))
        body.grid(row=1,column=0,sticky=tk.NSEW)
        body.columnconfigure(1,weight=1)
        body.rowconfigure(0,weight=1)
        saved = ttk.Frame(body,width=210)
        saved.grid(row=0,column=0,sticky=tk.NSEW,padx=(0,14))
        saved.rowconfigure(1,weight=1);saved.columnconfigure(0,weight=1)
        ttk.Label(saved,text='已存模板／每日跟踪',foreground=TEXT).grid(row=0,column=0,sticky=tk.W,pady=(0,6))
        self.saved = ttk.Treeview(saved,columns=('name','track'),show='headings',height=9)
        self.saved.heading('name',text='模板');self.saved.heading('track',text='跟踪')
        self.saved.column('name',width=160);self.saved.column('track',width=40,stretch=False)
        self.saved.grid(row=1,column=0,sticky=tk.NSEW)
        self.saved.bind('<<TreeviewSelect>>',self._select_template)
        ttk.Button(saved,text='新建模板',command=self._new).grid(row=2,column=0,sticky=tk.EW,pady=5)
        self.form = form = ttk.Frame(body)
        form.grid(row=0,column=1,sticky=tk.NSEW)
        form.columnconfigure(0,weight=1)
        form.rowconfigure(1,weight=1)
        title = ttk.Frame(form);title.grid(row=0,column=0,sticky=tk.EW,pady=(0,7))
        title.columnconfigure(1,weight=1)
        ttk.Label(title,text='模板名').grid(row=0,column=0,padx=(0,7))
        ttk.Entry(title,textvariable=self.name).grid(row=0,column=1,sticky=tk.EW)
        self.tabs = ttk.Notebook(form)
        self.tabs.grid(row=1,column=0,sticky=tk.NSEW)
        self.classic_page = ttk.Frame(self.tabs,padding=10)
        self.case_page = ttk.Frame(self.tabs,padding=10)
        self.draw_page = ttk.Frame(self.tabs,padding=10)
        for frame,label in ((self.classic_page,'经典／文字'),(self.case_page,'案例／当前图表'),(self.draw_page,'手绘／截图')):
            self.tabs.add(frame,text=label)
        self.classic_key = tk.StringVar(value=TEMPLATES['w_bottom'][0])
        combo = ttk.Combobox(self.classic_page,textvariable=self.classic_key,values=[v[0] for v in TEMPLATES.values()],state='readonly')
        combo.pack(fill=tk.X)
        combo.bind('<<ComboboxSelected>>',self._classic_changed)
        line = ttk.Frame(self.classic_page);line.pack(fill=tk.X,pady=8)
        self.query = tk.StringVar()
        ttk.Entry(line,textvariable=self.query).pack(side=tk.LEFT,fill=tk.X,expand=True)
        ttk.Button(line,text='识别关键词',command=self._route).pack(side=tk.LEFT,padx=(6,0))
        ttk.Label(self.classic_page,text='文字入口映射六类经典形态；复杂结构请从案例行情或手绘建立。',foreground=MUTED,wraplength=600).pack(anchor=tk.W)
        self.classic_canvas = tk.Canvas(self.classic_page,height=130,bg=PANEL_BG,highlightthickness=0)
        self.classic_canvas.pack(fill=tk.BOTH,expand=True,pady=8)
        self._case_form()
        self._drawing_form()
        parameters = ttk.Frame(form);parameters.grid(row=2,column=0,sticky=tk.EW,pady=(8,0))
        for col,(label,var,values) in enumerate((('BAR',self.window,('30','60','120')),('最低相似',self.threshold,None),('最多结果',self.topn,None))):
            ttk.Label(parameters,text=label).grid(row=0,column=col*2,padx=(0,4))
            field = ttk.Combobox(parameters,textvariable=var,values=values,state='readonly',width=5) if values else ttk.Entry(parameters,textvariable=var,width=5)
            field.grid(row=0,column=col*2+1,padx=(0,12))
        scope = ttk.Frame(form);scope.grid(row=3,column=0,sticky=tk.EW,pady=(8,0))
        ttk.Combobox(scope,textvariable=self.scope,values=['全部本地A股',*BOARDS,'公共关注列表','指定代码'],state='readonly',width=16).pack(side=tk.LEFT)
        ttk.Entry(scope,textvariable=self.codes).pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(8,0))
        ttk.Label(form,text='指定代码以空格或逗号分隔；同一来源匹配，不拼接复权价格。',foreground=MUTED).grid(row=4,column=0,sticky=tk.W,pady=4)
        actions = ttk.Frame(form);actions.grid(row=5,column=0,sticky=tk.EW,pady=(4,8))
        ttk.Checkbutton(actions,text='行情更新后每日跟踪',variable=self.track).pack(side=tk.LEFT)
        ttk.Button(actions,text='保存模板',command=self._save).pack(side=tk.RIGHT,padx=(5,0))
        ttk.Button(actions,text='保存并匹配',command=self._search,bootstyle='primary').pack(side=tk.RIGHT)
        bottom = ttk.Frame(self,padding=(16,0,16,12))
        bottom.grid(row=2,column=0,sticky=tk.EW);bottom.columnconfigure(0,weight=1)
        self.progress = ttk.Progressbar(bottom,mode='determinate')
        self.progress.grid(row=0,column=0,columnspan=2,sticky=tk.EW,pady=4)
        ttk.Label(bottom,textvariable=self.status,foreground=MUTED,wraplength=850).grid(row=1,column=0,sticky=tk.W)
        ttk.Button(bottom,text='取消匹配',command=self.runner.cancel).grid(row=1,column=1)
        history = ttk.Frame(bottom);history.grid(row=2,column=0,columnspan=2,sticky=tk.EW,pady=(8,4))
        ttk.Label(history,text='历史记录').pack(side=tk.LEFT)
        self.history_var = tk.StringVar()
        self.history = ttk.Combobox(history,textvariable=self.history_var,state='readonly',width=55)
        self.history.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=8)
        self.history.bind('<<ComboboxSelected>>',self._open_history)
        ttk.Button(history,text='继续未完成匹配',command=self._resume).pack(side=tk.RIGHT)
        self.results = ttk.Treeview(bottom,columns=('code','template','score','date'),show='headings',height=5)
        for col,label,width in (('code','标的',130),('template','模板',250),('score','相似度',80),('date','匹配截止',120)):
            self.results.heading(col,text=label);self.results.column(col,width=width)
        self.results.grid(row=3,column=0,columnspan=2,sticky=tk.EW,pady=4)
        bar = ttk.Frame(bottom);bar.grid(row=4,column=0,columnspan=2,sticky=tk.EW)
        ttk.Button(bar,text='在公共匹配列表查看',command=self._show_results).pack(side=tk.RIGHT)
        ttk.Button(bar,text='候选／原案例双图',command=self._compare).pack(side=tk.RIGHT,padx=5)
        ttk.Button(bar,text='加入公共关注',command=self._watch).pack(side=tk.RIGHT)

    def _case_form(self):
        self.code = tk.StringVar(value=self.current_item.code if self.current_item else '')
        self.start = tk.StringVar()
        self.cutoff = tk.StringVar(value=self.asof or '')
        for label,var in (('股票代码',self.code),('模板起始日',self.start),('模板截止K线',self.cutoff)):
            line = ttk.Frame(self.case_page);line.pack(fill=tk.X,pady=4)
            ttk.Label(line,text=label,width=14).pack(side=tk.LEFT)
            ttk.Entry(line,textvariable=var).pack(side=tk.LEFT,fill=tk.X,expand=True)
        ttk.Button(self.case_page,text='读取本地行情并预览（默认最近60根）',command=self._preview_case).pack(anchor=tk.W,pady=8)
        ttk.Label(self.case_page,text='日期格式 YYYY-MM-DD。截止K线须为案例当时可见的最后一根；不使用其后走势。\n无法关联本地真实行情时，请使用手绘轮廓，不会凭截图编造OHLC。',foreground=MUTED,wraplength=590).pack(anchor=tk.W)
        self.case_canvas = tk.Canvas(self.case_page,height=110,bg=PANEL_BG,highlightthickness=0)
        self.case_canvas.pack(fill=tk.BOTH,expand=True,pady=8)

    def _drawing_form(self):
        bar = ttk.Frame(self.draw_page);bar.pack(fill=tk.X)
        ttk.Button(bar,text='导入截图',command=self._load_image).pack(side=tk.LEFT)
        ttk.Button(bar,text='清空描绘',command=self._clear_drawing).pack(side=tk.LEFT,padx=4)
        self.draw_mode = tk.StringVar(value='描绘')
        ttk.Combobox(bar,textvariable=self.draw_mode,values=['描绘','框选价格区'],state='readonly',width=12).pack(side=tk.LEFT)
        self.scheme = tk.StringVar(value='白蓝')
        ttk.Combobox(bar,textvariable=self.scheme,values=['白蓝','红绿'],state='readonly',width=5).pack(side=tk.LEFT,padx=4)
        ttk.Button(bar,text='尝试本地识别',command=self._recognize).pack(side=tk.LEFT)
        self.draw_canvas = tk.Canvas(self.draw_page,width=600,height=180,bg=PANEL_BG,highlightthickness=1,highlightbackground=MUTED)
        self.draw_canvas.pack(fill=tk.BOTH,expand=True,pady=7)
        self.draw_canvas.bind('<Button-1>',self._draw_start)
        self.draw_canvas.bind('<B1-Motion>',self._draw_motion)
        self.draw_canvas.bind('<ButtonRelease-1>',self._draw_end)
        ttk.Label(self.draw_page,text='从左往右手绘；截图先框选主价格区，再识别或沿原图描绘。识别轮廓须人工核对确认。',foreground=MUTED,wraplength=600).pack(anchor=tk.W)

    def _draw_path(self,canvas,path):
        canvas.delete('preview')
        self.update_idletasks()
        width,height = max(200,canvas.winfo_width()),max(80,canvas.winfo_height())
        values = normalize(path)
        points = [coordinate for i,y in enumerate(values) for coordinate in (12+i*(width-24)/(len(values)-1),height-12-float(y)*(height-24))]
        canvas.create_line(*points,fill=ACCENT,width=2,tags='preview')

    def _classic_changed(self,_event=None):
        self.name.set(self.classic_key.get())
        key = next(k for k,v in TEMPLATES.items() if v[0]==self.classic_key.get())
        self._draw_path(self.classic_canvas,classic(key))

    def _route(self):
        matches = keywords(self.query.get())
        if len(matches)!=1:
            self.status.set('关键词未明确对应一个经典形态，请直接选择；复杂结构使用案例或手绘。')
            return
        self.classic_key.set(TEMPLATES[matches[0]][0]);self._classic_changed()

    def _case_definition(self):
        code = code_of(self.code.get())
        rows = self.store.rows("SELECT * FROM datasets WHERE code=? AND source=? AND timeframe='daily' ORDER BY end DESC,created DESC,rowid DESC LIMIT 1",(code,self.source))
        if not rows:
            raise ValueError('没有此标的同来源本地行情，请确认代码或先更新行情')
        frame,record = load_dataset(self.store,rows[0]['id'])
        cutoff = self.cutoff.get().strip()
        date.fromisoformat(cutoff)
        prefix = frame[frame.date <= cutoff]
        if prefix.empty or str(prefix.date.iloc[-1])!=cutoff:
            raise ValueError('截止日没有该股票的已完成K线，请选择真实交易日')
        start = self.start.get().strip()
        if not start:
            start = str(prefix.tail(60).date.iloc[0]);self.start.set(start)
        date.fromisoformat(start)
        clip = prefix[prefix.date >= start]
        if len(clip)<3:
            raise ValueError('案例区间至少需要三根真实K线')
        return dict(path=clip.close.tolist(),origin=dict(dataset_id=record['id'],sha256=record['sha256'],code=code,
            source=record['source'],adjustment=record['adjustment'],start=start,cutoff=cutoff,
            case_id=(self.case or {}).get('id'),case_title=(self.case or {}).get('title','')))

    def _preview_case(self):
        try:
            definition = self._case_definition()
            self._draw_path(self.case_canvas,definition['path'])
            self.status.set(f"真实行情轮廓 · {len(definition['path'])} BAR · 截止 {definition['origin']['cutoff']}")
        except Exception as exc:
            self.status.set(str(exc))

    def _new(self):
        self.current = None;self.name.set('新形态模板');self.track.set(False)
        self.saved.selection_remove(*self.saved.selection())

    def _load_templates(self):
        self.templates = self.store.shape_templates()
        self.saved.delete(*self.saved.get_children())
        for t in self.templates:
            self.saved.insert('',tk.END,iid=t['id'],values=(t['name'],'●' if t['tracking'] else '—'))

    def _select_template(self,_event=None):
        selection = self.saved.selection()
        if not selection:return
        self.current = next(t for t in self.templates if t['id']==selection[0])
        t = self.current
        self.name.set(t['name']);self.track.set(t['tracking'])
        self.window.set(str(t['settings']['window']));self.threshold.set(str(t['settings']['threshold']));self.topn.set(str(t['settings']['topn']))
        config=t['settings']
        self.scope.set('指定代码' if config['codes'] else config['boards'][0] if len(config['boards'])==1 else '全部本地A股')
        self.codes.set(' '.join(config['codes']))
        self.path = t['path']
        if t['kind']=='classic':
            self.tabs.select(self.classic_page)
            self.classic_key.set(TEMPLATES[t['origin']['classic_key']][0]);self._draw_path(self.classic_canvas,t['path'])
        elif t['kind'] in ('case','chart'):
            self.tabs.select(self.case_page)
            self.code.set(t['origin']['code']);self.start.set(t['origin']['start']);self.cutoff.set(t['origin']['cutoff'])
            self.case = dict(id=t['origin'].get('case_id'),title=t['origin'].get('case_title')) if t['kind']=='case' else None
            self._draw_path(self.case_canvas,t['path'])
        else:
            self.tabs.select(self.draw_page);self._draw_path(self.draw_canvas,t['path'])

    def _settings(self):
        scope = self.scope.get()
        codes=[]
        if scope=='公共关注列表':
            codes=[r['code'] for r in self.store.rows('SELECT code FROM watchlist WHERE active=1')]
            if not codes:raise ValueError('公共关注列表为空')
        if scope=='指定代码':
            codes=self.codes.get().replace('，',' ').replace(',',' ').split()
            if not codes:raise ValueError('请输入指定股票代码')
        return options(dict(window=self.window.get(),threshold=self.threshold.get(),topn=self.topn.get(),
                            boards=[scope] if scope in BOARDS else list(BOARDS),codes=codes))

    def _definition(self):
        page = self.tabs.select()
        if page==str(self.classic_page):
            key=next(k for k,v in TEMPLATES.items() if v[0]==self.classic_key.get())
            definition=dict(kind='classic',path=classic(key),origin={'classic_key':key})
        elif page==str(self.case_page):
            definition=dict(kind='case' if self.case else 'chart',**self._case_definition())
        else:
            path = drawing_path(self.points) if self.points else self.path
            if path is None:raise ValueError('请先绘制轮廓或识别截图')
            definition=dict(kind='screenshot' if self.screenshot else 'drawing',path=path,
                            origin=dict(image_sha256=getattr(self,'image_sha',None),method=getattr(self,'image_method','manual-contour')))
        definition.update(name=self.name.get(),tracking=self.track.get(),settings=self._settings())
        if self.current:definition['id']=self.current['id']
        return definition

    def _save(self):
        try:
            result=self.store.save_shape_template(self._definition(),(self.current or {}).get('revision'))
            self.current=result;self._load_templates();self.status.set('模板已保存在本机；历史版本保留')
            return result
        except Exception as exc:
            self.status.set(str(exc));return None

    def _search(self):
        if not self.asof:
            self.status.set('请先更新行情，再从盘前交易日选择匹配日期');return
        template=self._save()
        if template:
            try:self.runner.submit([template],self.asof,self.source)
            except Exception as exc:self.status.set(str(exc))

    def _poll(self):
        status=self.runner.status()
        if status.get('state') in ('queued','running'):
            self.status.set(f"本地形态匹配 · {status.get('progress',0)}/{status.get('total',0)} · {status.get('message','')}")
            self.progress.configure(maximum=max(1,status.get('total',0)),value=status.get('progress',0))
        elif status.get('run_id') and status['run_id']!=self._last_run:
            self._last_run=status['run_id'];self._refresh_history();self._display_run(self._last_run)
            t=status.get('timings',{})
            self.status.set(status['message']+f" · 读取 {t.get('read',0):.2f}s / SHA {t.get('verify',0):.2f}s / 解析 {t.get('parse',0):.2f}s / 计算 {t.get('compute',0):.2f}s / 保存 {t.get('save',0):.2f}s")
        elif status.get('state') in ('failed','interrupted','cancelled'):
            self.status.set(status.get('message','匹配未完成'))
        self._timer=self.after(400,self._poll)

    def _refresh_history(self):
        self.history_rows=self.store.shape_runs(self.source)
        self._history_map={f"{r['asof']} · {'事后回放' if r['mode']=='replay' else '当时保存'} · {r['status']} · {r['id'][:6]}":r['id'] for r in self.history_rows}
        self.history.configure(values=list(self._history_map))

    def _open_history(self,_event=None):
        identifier=self._history_map.get(self.history_var.get())
        if identifier:self._display_run(identifier)

    def _display_run(self,identifier):
        self.report=self.store.shape_run(identifier)
        self._view_run=identifier
        label=next((k for k,v in self._history_map.items() if v==identifier),'')
        self.history_var.set(label)
        self.results.delete(*self.results.get_children());self._rows={}
        from .data import code_names
        names=code_names(self.store)
        for match in self.report['matches']:
            template=next(t for t in self.report['templates'] if t['id']==match['template_id'])
            row=dict(match,kind='shape',name=names.get(match['code'],''),label=template['name'],template=template,
                     source=self.report['source'],timeframe='daily',date=self.report['asof'],
                     observation_id='shape:'+identifier+':'+match['id'],run_id=identifier)
            iid=self.results.insert('',tk.END,values=(match['code'],template['name'],match['score'],match['end']))
            self._rows[iid]=row
        children=self.results.get_children()
        if children:self.results.selection_set(children[0])
        self.status.set(f"{self.report['asof']} · {self.report['status']} · {len(self.report['matches'])} 项 · 跳过 {len(self.report['skipped'])} / 错误 {len(self.report['errors'])}")

    def _resume(self):
        if not hasattr(self,'report'):return
        try:self.runner.submit(self.report['templates'],self.report['asof'],self.report['source'],checkpoint=self.report['id'])
        except Exception as exc:self.status.set(str(exc))

    def _selected(self):
        selection=self.results.selection()
        return self._rows.get(selection[0]) if selection else None

    def _watch(self):
        row=self._selected()
        if row:
            self.store.watch_shape_match(row['run_id'],row['id'])
            self.master.watch.reload();self.status.set('已加入公共关注；没有创建交易计划')

    def _compare(self):
        row=self._selected()
        if row and self.on_compare:self.on_compare(row)

    def _show_results(self):
        if hasattr(self,'report') and self.on_results:
            self.on_results(self.report['asof']);self.master.candidates.set_source('形态捕手');self._close()

    def _load_image(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('截图','*.png *.jpg *.jpeg *.webp')])
        if not path:return
        try:
            from pathlib import Path
            data=Path(path).read_bytes()
            if len(data)>15*1024*1024:raise ValueError('截图超过15MB，请先裁切')
            image=Image.open(io.BytesIO(data));image.load()
            self.screenshot=image.convert('RGB');self.image_sha=digest(data)
            self.points=[];self.path=None;self.crop=None
            self.draw_canvas.delete('all')
            self.update_idletasks()
            shown=self.screenshot.copy();shown.thumbnail((self.draw_canvas.winfo_width(),self.draw_canvas.winfo_height()))
            self._image_scale=self.screenshot.width/shown.width
            self.photo=ImageTk.PhotoImage(shown,master=self)
            self.draw_canvas.create_image(0,0,image=self.photo,anchor=tk.NW,tags='background')
            self.status.set('请框选不含成交量／标注／坐标的价格区，再尝试识别或描绘。')
        except Exception as exc:self.status.set(str(exc))

    def _clear_drawing(self):
        self.points=[];self.path=None;self.draw_canvas.delete('trace');self.draw_canvas.delete('preview')

    def _draw_start(self,event):
        if self.draw_mode.get()=='框选价格区':
            self._crop_start=(event.x,event.y);self.draw_canvas.delete('crop')
        else:
            self._clear_drawing();self.points=[(event.x,event.y)]

    def _draw_motion(self,event):
        if self.draw_mode.get()=='框选价格区':
            start=getattr(self,'_crop_start',(event.x,event.y));self.draw_canvas.delete('crop')
            self.draw_canvas.create_rectangle(*start,event.x,event.y,outline=ACCENT,tags='crop')
        elif self.points:
            self.draw_canvas.create_line(*self.points[-1],event.x,event.y,fill=ACCENT,width=2,tags='trace')
            self.points.append((event.x,event.y))

    def _draw_end(self,event):
        if self.draw_mode.get()=='框选价格区' and hasattr(self,'_crop_start'):
            x,y=self._crop_start
            self.crop=(min(x,event.x),min(y,event.y),max(x,event.x),max(y,event.y))

    def _recognize(self):
        if self.screenshot is None or self.crop is None:
            self.status.set('先导入截图并框选价格区；识别失败时可以直接沿原图描绘。');return
        try:
            from workbench.shape_image import candle_contour
            bounds=tuple(round(v*self._image_scale) for v in self.crop)
            extracted=candle_contour(self.screenshot.crop(bounds),'white-blue' if self.scheme.get()=='白蓝' else 'red-green')
            self._draw_path(self.draw_canvas,extracted['path'])
            if not messagebox.askyesno('核对截图轮廓',f"识别 {extracted['candles']} 根，间距可靠度 {extracted['confidence']:.0%}。\n{extracted['message']}\n确认使用蓝色预览轮廓？",parent=self):
                self.path=None;self.status.set('识别未采用，请重新框选或人工描绘');return
            self.points=[];self.path=extracted['path'];self.image_method=extracted['method']
            self.status.set('截图轮廓已人工确认，保存后可本地匹配；不作为还原OHLC')
        except Exception as exc:self.status.set(str(exc))

    def _close(self):
        if self._timer:self.after_cancel(self._timer)
        self.destroy()
