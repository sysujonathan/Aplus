"""Lifecycle research desk, V1: saved paired initial-stop experiments."""
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import messagebox, simpledialog
import ttkbootstrap as ttk
from PIL import ImageTk

from workbench.h2_replay import MODEL as BASE_MODEL
from workbench.stop_research import AnchorSettings, GROUPS, MODEL
from workbench.stop_research_service import load_experiment
from .afterhours_workspace import label, button, tree
from .replay_navigation import ReplayNavigation
from .stop_research_chart import render_comparison
from .theme import CHART_BG, MUTED, PANEL_BG, TEXT


def outcome_text(record):
    if not record.get('filled'):
        return '未成交'
    if record['status']=='open':
        return '仍持有'
    reason=record.get('reason','')
    return 'MM' if reason=='MM 止盈' else '止损' if '止损' in reason else '到期退出'


def display_name(row,prefix='E'):
    display=row['display']
    created=datetime.fromisoformat(row['created']).astimezone(timezone(timedelta(hours=8)))
    return display['name'] or f"{prefix}{display['number']:02d} · {created:%m-%d %H:%M}"


class StrategyIterationPage(ttk.Frame):
    def __init__(self,parent,service=None,store=None):
        super().__init__(parent,padding=10)
        self.service,self.store=service,store
        self.job=self.shown_job=self.selected=None
        self.rows=[];self.report={};self.sources={};self.histories={}
        self.sort_key='setup_date';self.sort_desc=True;self.photo=None;self.draw_id=None
        self.source=tk.StringVar();self.max_bars=tk.StringVar(value='30');self.pause=tk.StringVar(value='0')
        self.group=tk.StringVar(value='全部');self.status=tk.StringVar(value='选择一份盘后回测，创建初始止损对照实验。')
        self.readout=tk.StringVar(value='移到 K 线上查看日期及开、高、低、收')
        self.detail=tk.StringVar(value='选择差异样本查看两种止损与后续走势')
        self.cutoff=tk.StringVar();self.posthoc=tk.BooleanVar(value=False)
        self.columnconfigure(1,weight=1);self.rowconfigure(2,weight=1)
        self._build()
        self.poll_id=self.after(400,self._poll)
        self.bind('<Destroy>',self._destroyed,add='+')

    def _build(self):
        title=ttk.Frame(self);title.grid(row=0,column=0,columnspan=2,sticky=tk.EW)
        label(title,'策略迭代',size=23,bold=True).pack(side=tk.LEFT)
        label(title,'GAP H2 · 初始止损对照 · 研究方案 V0',color=MUTED).pack(side=tk.LEFT,padx=16)
        nav=ttk.Frame(self);nav.grid(row=1,column=0,columnspan=2,sticky=tk.EW,pady=10)
        for text,active in [('背景与形态 · 规划',False),('入场与初始风险',True),('等待与持仓管理 · 规划',False),('退出 · 规划',False)]:
            b=button(nav,text,lambda:None,active);b.pack(side=tk.LEFT,padx=(0,8))
            if not active:b.configure(state=tk.DISABLED)
        side=ttk.Frame(self,width=240,padding=(0,0,10,0));side.grid(row=2,column=0,sticky=tk.NSEW)
        side.grid_propagate(False)
        label(side,'实验记录',size=19,bold=True).pack(anchor=tk.W,pady=(0,8))
        actions=ttk.Frame(side);actions.pack(fill=tk.X)
        button(actions,'重命名',self._rename).pack(side=tk.LEFT)
        button(actions,'删除',self._delete).pack(side=tk.RIGHT)
        button(side,'已删除 / 恢复',self._restore).pack(fill=tk.X,pady=8)
        self.history=tree(side,[('name','实验 / 日期',205,tk.W)],height=14)
        self.history.configure(selectmode='extended')
        self.history.pack(fill=tk.BOTH,expand=True)
        self.history.bind('<<TreeviewSelect>>',self._history_selected)
        button(side,'对比选中两次实验',self._compare).pack(fill=tk.X,pady=(8,0))
        label(side,'按住 Ctrl 选择两次实验',size=14,color=MUTED).pack(anchor=tk.W,pady=6)
        label(side,'每次独立保存；不启用实盘',size=14,color=MUTED).pack(anchor=tk.W,pady=8)
        main=ttk.Frame(self);main.grid(row=2,column=1,sticky=tk.NSEW)
        main.columnconfigure(0,weight=1);main.rowconfigure(6,weight=1)
        source=ttk.Frame(main);source.grid(row=0,column=0,sticky=tk.EW)
        label(source,'样本回测').pack(side=tk.LEFT,padx=(0,8))
        self.source_box=ttk.Combobox(source,textvariable=self.source,state='readonly',font=('Microsoft YaHei UI',-16),width=36)
        self.source_box.pack(side=tk.LEFT,fill=tk.X,expand=True)
        self.source_box.bind('<<ComboboxSelected>>',lambda e:self._source_context())
        button(source,'刷新',self.refresh).pack(side=tk.LEFT,padx=8)
        self.run=button(source,'新建实验',self._run,True);self.run.pack(side=tk.RIGHT)
        self.stop=button(source,'停止',self._stop);self.stop.pack(side=tk.RIGHT,padx=8);self.stop.configure(state=tk.DISABLED)
        self.context=tk.StringVar(value='复用同一批机会与模拟入场，仅改变初始止损')
        self.context_label=label(main,variable=self.context,size=15,color=MUTED)
        self.context_label.grid(row=1,column=0,sticky=tk.EW,pady=8)
        self.context_label.bind('<Configure>',lambda e:self.context_label.configure(wraplength=max(1,e.width)))
        rule=ttk.Frame(main,padding=8,style='ResearchPanel.TFrame');rule.grid(row=2,column=0,sticky=tk.EW)
        label(rule,'下次实验 · SL1 对照 C',bold=True).pack(side=tk.LEFT,padx=(0,14))
        for text,var in [('回溯上限 / 根',self.max_bars),('浅回调容忍 / tick',self.pause)]:
            label(rule,text,size=15).pack(side=tk.LEFT,padx=6)
            ttk.Entry(rule,textvariable=var,width=4,font=('Microsoft YaHei UI',-16)).pack(side=tk.LEFT)
        button(rule,'规则与口径',self._rules).pack(side=tk.RIGHT)
        label(main,'C 仅为低点抬升段的候选底部；未加入强度判定，不代表正式 PA 规则。',size=14,color=MUTED).grid(row=3,column=0,sticky=tk.W,pady=8)
        metrics=ttk.Frame(main);metrics.grid(row=4,column=0,sticky=tk.EW)
        self.metrics=[]
        for i,caption in enumerate(['可对照 / 总机会','宽止损救回','两者到 MM','两者止损','平均差异 · 统一 R']):
            metrics.columnconfigure(i,weight=1,uniform='stat')
            card=ttk.Frame(metrics,padding=10,style='ResearchPanel.TFrame');card.grid(row=0,column=i,sticky=tk.EW,padx=(0,8 if i<4 else 0))
            label(card,caption,size=15,color=MUTED).pack(anchor=tk.W)
            var=tk.StringVar(value='—');self.metrics.append(var);label(card,variable=var,size=26,bold=True).pack(anchor=tk.W)
        filters=ttk.Frame(main);filters.grid(row=5,column=0,sticky=tk.EW,pady=8)
        label(filters,'差异样本',bold=True).pack(side=tk.LEFT,padx=(0,8))
        box=ttk.Combobox(filters,textvariable=self.group,values=GROUPS,state='readonly',width=19,font=('Microsoft YaHei UI',-16));box.pack(side=tk.LEFT)
        box.bind('<<ComboboxSelected>>',lambda e:self._fill_rows())
        label(filters,'点表头排序 · 选一笔看图',size=14,color=MUTED).pack(side=tk.LEFT,padx=12)
        self.body=ttk.Panedwindow(main,orient=tk.HORIZONTAL);self.body.grid(row=6,column=0,sticky=tk.NSEW)
        self.body.bind('<Configure>',lambda e:self.body.sashpos(0,min(540,max(460,round(e.width*.36)))) if len(self.body.panes())==2 else None)
        list_host=ttk.Frame(self.body);list_host.columnconfigure(0,weight=1);list_host.rowconfigure(0,weight=1)
        columns=[('setup_date','H2 日期',106,tk.W),('code','代码',100,tk.W),('baseline','SL1 结局',86,tk.W),('candidate','C 结局',86,tk.W),('delta','差异 R',78,tk.E)]
        self.samples=tree(list_host,columns,height=12);self.samples.grid(row=0,column=0,sticky=tk.NSEW)
        vs=ttk.Scrollbar(list_host,command=self.samples.yview);vs.grid(row=0,column=1,sticky=tk.NS)
        hs=ttk.Scrollbar(list_host,orient=tk.HORIZONTAL,command=self.samples.xview);hs.grid(row=1,column=0,sticky=tk.EW)
        self.samples.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        for key,caption,_,_ in columns:self.samples.heading(key,text=caption+' ↕',command=lambda k=key:self._sort(k))
        self.samples.bind('<<TreeviewSelect>>',self._selected)
        self.body.add(list_host,weight=4)
        charts=ttk.Frame(self.body,padding=(8,0,0,0));charts.columnconfigure(0,weight=1);charts.rowconfigure(3,weight=1)
        detail=label(charts,variable=self.detail,size=15);detail.grid(row=0,column=0,sticky=tk.EW)
        detail.bind('<Configure>',lambda e:detail.configure(wraplength=max(1,e.width)))
        chartnav=ttk.Frame(charts);chartnav.grid(row=1,column=0,sticky=tk.EW,pady=6)
        self.event_box=ttk.Combobox(chartnav,textvariable=self.cutoff,state='readonly',width=12,font=('Microsoft YaHei UI',-15));self.event_box.pack(side=tk.LEFT)
        self.event_box.bind('<<ComboboxSelected>>',lambda e:self._schedule_draw())
        ttk.Style().configure('Iteration.TCheckbutton',font=('Microsoft YaHei UI',-15))
        ttk.Checkbutton(chartnav,text='完整走势（事后）',variable=self.posthoc,command=self._schedule_draw,style='Iteration.TCheckbutton').pack(side=tk.LEFT,padx=8)
        button(chartnav,'复位',lambda:self.navigation.reset()).pack(side=tk.RIGHT)
        label(charts,variable=self.readout,size=13,color=MUTED).grid(row=2,column=0,sticky=tk.EW)
        self.chart=tk.Label(charts,bg=CHART_BG,fg=TEXT,text='选择样本查看 C 与 SL1');self.chart.grid(row=3,column=0,sticky=tk.NSEW)
        self.chart.bind('<Configure>',lambda e:self._schedule_draw())
        self.navigation=ReplayNavigation(self.chart,self._schedule_draw,lambda t:self.readout.set(t or '移到 K 线上查看日期及开、高、低、收'))
        self.body.add(charts,weight=6)
        self.status_label=label(main,variable=self.status,size=14,color=MUTED);self.status_label.grid(row=7,column=0,sticky=tk.EW,pady=8)
        self.status_label.bind('<Configure>',lambda e:self.status_label.configure(wraplength=max(1,e.width)))

    def refresh(self):
        if self.store is None:return
        sources=self.store.backtest_history(BASE_MODEL)
        self.sources={display_name(r,'R'):r for r in sources}
        self.source_box.configure(values=list(self.sources))
        if self.source.get() not in self.sources:self.source.set(next(iter(self.sources),''))
        self._source_context()
        self.histories={r['id']:r for r in self.store.backtest_history(MODEL)}
        self.history.delete(*self.history.get_children())
        for identity,row in self.histories.items():self.history.insert('',tk.END,iid=identity,values=(display_name(row),))
        if self.shown_job in self.histories:self.history.selection_set(self.shown_job)
        elif self.histories:self.history.selection_set(next(iter(self.histories)))

    def _source_context(self):
        import json
        row=self.sources.get(self.source.get())
        if row:
            r=json.loads(row['result']);self.context.set(f"{r['start']} → {r['end']} · {r['opportunities']} 次机会 · 持有上限 {r['assumptions']['holding_bars']} 根 · "+('完整样本' if r.get('complete') else '部分样本'))

    def _run(self):
        if self.service is None or self.store is None:return
        try:
            source=self.sources.get(self.source.get())
            if not source:raise ValueError('先在盘后回测完成一次 GAP H2 回测，再选择样本')
            settings=AnchorSettings(int(self.max_bars.get()),int(self.pause.get()));settings.validate()
            from dataclasses import asdict
            self.job=self.service.submit('backtest',dict(execution_model=MODEL,source_job=source['id'],anchor_settings=asdict(settings)))
            self.run.configure(state=tk.DISABLED);self.stop.configure(state=tk.NORMAL)
            self.status.set('正在逐笔比较；实验结束或停止后保存结果')
        except Exception as exc:messagebox.showerror('无法开始研究',str(exc),parent=self)

    def _stop(self):
        if self.job:self.service.cancel(self.job)

    def _poll(self):
        if self.job and self.store:
            jobs=self.store.rows('SELECT * FROM jobs WHERE id=?',(self.job,))
            if jobs:
                row=jobs[0];self.status.set(row['message'] or '正在研究')
                if row['status'] not in ('queued','running'):
                    done=self.job;self.job=None;self.run.configure(state=tk.NORMAL);self.stop.configure(state=tk.DISABLED)
                    self.shown_job=done;self.refresh()
                    if row.get('result'):self._load(done)
                    elif row['status']=='failed':messagebox.showerror('研究失败',row['message'],parent=self)
        self.poll_id=self.after(400,self._poll)

    def _history_selected(self,event=None):
        ids=self.history.selection()
        if len(ids)==1 and ids[0]!=self.shown_job:self._load(ids[0])

    def _compare(self):
        ids=self.history.selection()
        if len(ids)!=2:
            messagebox.showinfo('比较实验','按住 Ctrl 选择两次实验，再点击对比。',parent=self);return
        try:
            a,ra,pa=load_experiment(self.store,ids[0]);b,rb,pb=load_experiment(self.store,ids[1])
            if ra['source_job']!=rb['source_job']:
                raise ValueError('两次实验使用不同源回测。请用同一份盘后回测创建实验，才能比较规则变化。')
            win=ttk.Toplevel(self);win.title('实验对比');win.geometry('820x560')
            label(win,'同一批样本 · 不同候选 C 规则',bold=True,size=20).pack(anchor=tk.W,padx=16,pady=12)
            table=tree(win,[('metric','项目',210,tk.W),('a',display_name(self.histories[ids[0]]),230,tk.W),('b',display_name(self.histories[ids[1]]),230,tk.W)])
            table.pack(fill=tk.BOTH,expand=True,padx=16)
            fields=[('回溯上限 / 根',ra['anchor_settings']['max_bars'],rb['anchor_settings']['max_bars']),
                    ('浅回调容忍 / tick',ra['anchor_settings']['pause_ticks'],rb['anchor_settings']['pause_ticks']),
                    ('可对照样本',ra['comparable'],rb['comparable']),('无法对照',ra['excluded'],rb['excluded'])]
            fields.extend((g,ra['groups'][g],rb['groups'][g]) for g in GROUPS[1:])
            for caption,va,vb in fields:table.insert('',tk.END,values=(caption,va,vb))
            la={r['id']:r for r in pa if 'delta_common_r' in r};lb={r['id']:r for r in pb if 'delta_common_r' in r}
            common=la.keys() & lb.keys()
            delta=sum(lb[k]['delta_common_r']-la[k]['delta_common_r'] for k in common)/len(common) if common else None
            text=f'两次都可对照且都已结束：{len(common)} 笔。'
            text+=f'右方案相对左方案平均变化 {delta:+.2f} 统一 R。' if delta is not None else '没有可计算平均差异的共同样本。'
            label(win,text,size=16,color=MUTED).pack(anchor=tk.W,padx=16,pady=14)
        except Exception as exc:messagebox.showerror('无法比较',str(exc),parent=self)

    def _load(self,identity):
        try:
            job,self.report,self.rows=load_experiment(self.store,identity);self.shown_job=identity
            r=self.report;g=r['groups'];delta=r['mean_delta_common_r']
            for v,text in zip(self.metrics,[f"{r['comparable']} / {r['opportunities']}",str(g['宽止损救回']),str(g['两者到 MM']),str(g['两者止损']),'—' if delta is None else f'{delta:+.2f}R']):v.set(text)
            settings=r['anchor_settings'];self.max_bars.set(str(settings['max_bars']));self.pause.set(str(settings['pause_ticks']))
            source_name=next((name for name,row in self.sources.items() if row['id']==r['source_job']),None)
            self.source.set(source_name or '')
            if source_name:self._source_context()
            else:self.context.set(f"历史源回测 {r['source_job'][:6]} · {r['start']} → {r['end']}（已从回测列表移除）")
            self.group.set('全部');self.selected=None;self.posthoc.set(False);self.navigation.reset(False)
            title=display_name(self.histories[identity]) if identity in self.histories else '本次实验'
            self.status.set(f"{title} · {'完整完成' if r['complete'] else '部分结果'} · 无法对照 {r['excluded']} · 配对已结束 {r['paired_closed']} · 仍持有 {r['unresolved']} · 失败 {len(r['errors'])}")
            self._fill_rows()
        except Exception as exc:messagebox.showerror('读取实验失败',str(exc),parent=self)

    def _sort(self,key):
        self.sort_desc=not self.sort_desc if self.sort_key==key else key=='delta'
        self.sort_key=key;self._fill_rows()

    def _fill_rows(self):
        old=self.samples.selection();self.samples.delete(*self.samples.get_children())
        rows=[r for r in self.rows if self.group.get()=='全部' or r['group']==self.group.get()]
        def key(r):
            if self.sort_key=='delta':return (r.get('delta_common_r') is not None,r.get('delta_common_r',0))
            if self.sort_key in ('baseline','candidate'):return outcome_text(r.get(self.sort_key,{}))
            return r[self.sort_key]
        for r in sorted(rows,key=key,reverse=self.sort_desc):
            delta=r.get('delta_common_r')
            self.samples.insert('',tk.END,iid=r['id'],values=(r['setup_date'],r['code'],outcome_text(r['baseline']),outcome_text(r['candidate']) if r.get('comparable') else '无法对照','—' if delta is None else f'{delta:+.2f}'))
        ids=self.samples.get_children()
        if ids:self.samples.selection_set(old[0] if old and old[0] in ids else ids[0])
        else:
            self.selected=None;self.photo=None;self.chart.configure(image='',text='该分类没有样本');self.detail.set('该分类没有样本');self.navigation.reset(False)

    def _selected(self,event=None):
        ids=self.samples.selection()
        if not ids:return
        self.selected=next(r for r in self.rows if r['id']==ids[0]);r=self.selected
        self.posthoc.set(False);self.navigation.reset(False)
        days=sorted({e['date'] for e in r['baseline']['events']}|{e['date'] for e in r.get('candidate',{}).get('events',[])})
        self.event_box.configure(values=days);self.cutoff.set(r['setup_date'])
        anchor=r.get('anchor',{})
        desc=f"{r['code']} · H2 {r['setup_date']} · {r['group']}"
        if r['comparable']:
            desc+=f"\nSL1 {r['baseline']['stop']:.2f} / C {anchor['stop']:.2f} · 候选段 {anchor['bars']} 根"
            desc+=f"\nSL1：{r['baseline'].get('exit_date','未结束')} {outcome_text(r['baseline'])}；C：{r['candidate'].get('exit_date','未结束')} {outcome_text(r['candidate'])}"
        else:desc+='\n'+r['reason']
        self.detail.set(desc);self._schedule_draw()

    def _schedule_draw(self):
        if self.draw_id:self.after_cancel(self.draw_id)
        self.draw_id=self.after(120,self._draw)

    def _draw(self):
        self.draw_id=None
        if not self.selected:return
        try:
            image=render_comparison(self.store,self.selected,self.cutoff.get(),self.posthoc.get(),
                    size=(max(100,self.chart.winfo_width()),max(100,self.chart.winfo_height())),viewport=self.navigation.viewport)
            self.navigation.accept(image);self.photo=ImageTk.PhotoImage(image);self.chart.configure(image=self.photo,text='')
        except Exception as exc:self.photo=None;self.chart.configure(image='',text=f'看图失败：{exc}');self.navigation.reset(False)

    def _rules(self):
        messagebox.showinfo('研究规则与对照口径',
            '候选 C V0：从原 BO 向前回溯低点抬升段。低点下降超过浅回调容忍 tick 时切分，取该段最低点下方一个品种 tick。未找到分界、候选段不足两根或 C 不低于缺口下沿时单列无法对照。\n\n'
            '尚未加入强突破判定；这是待验证假设。\n\n'
            '固定源回测机会、入场、MM、持有期限与费用。T+1、停牌和同日双触碰沿用盘后口径。平均差异只计算两方案都已结束的配对样本，以原 SL1 风险为统一 R；不将仍持有记作获救。\n\n'
            '本版研究不写入实盘匹配、人工计划或实际持仓；其他生命周期入口仍在规划。',parent=self)

    def _rename(self):
        if self.shown_job not in self.histories:return
        name=simpledialog.askstring('重命名实验','实验名称（1～40 字）',initialvalue=display_name(self.histories[self.shown_job]),parent=self)
        if name is not None:
            try:self.store.rename_backtest(self.shown_job,name);self.refresh()
            except Exception as exc:messagebox.showerror('无法重命名',str(exc),parent=self)

    def _delete(self):
        if self.shown_job not in self.histories:return
        if messagebox.askyesno('删除实验','将此实验移至已删除记录？可以恢复。',parent=self):
            self.store.set_backtest_deleted(self.shown_job);self.shown_job=None;self.refresh()
            if not self.histories:self.rows=[];self._fill_rows();self.status.set('实验已移至已删除记录')

    def _restore(self):
        rows=[r for r in self.store.backtest_history(MODEL,include_deleted=True) if r['display']['deleted']]
        win=ttk.Toplevel(self);win.title('已删除实验');win.geometry('480x360')
        listing=tk.Listbox(win,font=('Microsoft YaHei UI',-16),bg=PANEL_BG,fg=TEXT)
        listing.pack(fill=tk.BOTH,expand=True,padx=12,pady=12)
        for r in rows:listing.insert(tk.END,display_name(r))
        def restore():
            selected=listing.curselection()
            if selected:self.store.set_backtest_deleted(rows[selected[0]]['id'],False);win.destroy();self.refresh()
        button(win,'恢复选中实验',restore).pack(pady=8)

    def _destroyed(self,event):
        if event.widget is self:
            if self.poll_id:self.after_cancel(self.poll_id)
            if self.draw_id:self.after_cancel(self.draw_id)
