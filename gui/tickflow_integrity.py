"""Read-only quality receipt; repair goes through the normal background queue."""
import tkinter as tk
import ttkbootstrap as ttk
from workbench.tickflow_integrity import integrity_view, integrity_summary


def show_integrity(parent,report,on_repair):
    window=ttk.Toplevel(parent)
    from workbench.sources import SOURCES
    window.title(SOURCES.get(report.get('source'),'备用源')+' 数据完整性')
    window.geometry('850x580')
    window.minsize(650,400)
    window.columnconfigure(0,weight=1)
    window.rowconfigure(2,weight=1)
    heading=ttk.Frame(window); heading.grid(row=0,column=0,sticky='ew',padx=12,pady=(12,0))
    ttk.Label(heading,text=f"回执日期 {report['asof']} · 扫描周期").pack(side=tk.LEFT)
    period=tk.StringVar(value='日线' if report['timeframe']=='daily' else '周线')
    values=['日线','周线'] if report.get('periods') else [period.get()]
    ttk.Combobox(heading,textvariable=period,values=values,state='readonly',width=8).pack(side=tk.LEFT,padx=8)
    summary=tk.StringVar()
    summary_label=ttk.Label(window,textvariable=summary,wraplength=810,justify=tk.LEFT)
    summary_label.grid(row=1,column=0,sticky='ew',padx=12,pady=12)
    frame=ttk.Frame(window); frame.grid(row=2,column=0,sticky='nsew',padx=12)
    text=tk.Text(frame,wrap='word',font=('Microsoft YaHei UI',10))
    scroll=ttk.Scrollbar(frame,command=text.yview); text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT,fill=tk.Y); text.pack(fill=tk.BOTH,expand=True)
    controls=ttk.Frame(window); controls.grid(row=3,column=0,sticky='ew',padx=12,pady=12)
    include_history=tk.BooleanVar(value=False)
    retry_unchanged=tk.BooleanVar(value=False)
    ttk.Checkbutton(controls,text='高级：同时核对区间外历史',variable=include_history).pack(anchor=tk.W)
    ttk.Checkbutton(controls,text='重试已补拉但未变化的缺口',variable=retry_unchanged).pack(anchor=tk.W)
    buttons=ttk.Frame(controls); buttons.pack(fill=tk.X,pady=(5,0))
    def selected_report():
        return integrity_view(report,'daily' if period.get()=='日线' else 'weekly')
    def selected_codes(view):
        codes=set(view['repair_codes'] if include_history.get() else view['scan_repair_codes'])
        if retry_unchanged.get():
            codes.update(view.get('unchanged_repair_codes',[]) if include_history.get() else view.get('retry_codes',[]))
        return sorted(codes)
    def repair():
        view=selected_report()
        codes=selected_codes(view)
        if not codes:
            return
        on_repair(codes,view['timeframe'],include_history.get()); window.destroy()
    button=ttk.Button(buttons,text='仅补拉缺口',command=repair,bootstyle='warning')
    button.pack(side=tk.RIGHT,padx=5)
    def selection_changed(*args):
        view=selected_report(); scan=view['scan']
        counts=view.get('counts',{})
        summary.set(integrity_summary(view))
        if 'counts' not in view:
            summary.set(f"应有 {scan['expected']} · 可扫描 {scan['ready']} · 停牌 {scan['suspended']} · 排除 {len(scan['gaps'])}\n"
                        '旧版回执尚未细分输入与区间外缺口；请更新一次以生成双周期核验。')
        lines=list(view.get('limitations',[]))
        if view.get('unsupported_directory'):
            lines.append('公共目录板块身份待适配（保留记录，未纳入当前范围）：')
            lines.extend(f"{g['code']} {g.get('name','')}" for g in view['unsupported_directory'])
        if view.get('retry_codes'):
            lines.append(f"已补拉无改善 {len(view['retry_codes'])} 只：需查证，默认不重复下载。")
        lines.append('数据未齐，暂不参与当前扫描（不是策略筛选未命中）：')
        lines.extend(f"{g['code']}：{g['error']}" for g in view['excluded'])
        if not view['excluded']:
            lines.append('无；当前周期输入已核验。' if scan['complete'] else '范围尚未核验。')
        lines.append('\n区间外历史待核对（不影响本周期输入认证）：')
        for g in view.get('outside_input_history',[]):
            preview='、'.join(g['dates'][:5])
            lines.append(f"{g['code']}：{len(g['dates'])} 天 · {preview}"+(' …' if len(g['dates'])>5 else ''))
        if include_history.get():
            lines.append('\n全历史未知日期（高级核对）：')
            lines.extend(f"{g['code']}："+'、'.join(g['dates']) for g in view['unknown_history'])
        elif 'counts' not in view:
            lines.append('\n旧版全历史未知缺口（尚未细分，不能当作停牌）：')
            lines.extend(f"{g['code']}：{len(g['dates'])} 天" for g in view['unknown_history'])
        lines.append('\n已核验停牌证据（不用补拉）：')
        for g in view.get('current_halts',[])+view['confirmed_history']:
            lines.append(g['code'])
            for proof in g['evidence']:
                lines.append(f"{proof['halt']} ～ {proof['resume'] or '连续停牌、不再复牌'} · "+' '.join(proof['urls']))
        if view['request_failures']:
            lines.append('\n本次请求/保存失败：')
            lines.extend(f"{g.get('code','')}：{g['error']}" for g in view['request_failures'])
        text.configure(state='normal'); text.delete('1.0',tk.END)
        text.insert('1.0','\n'.join(lines)); text.configure(state='disabled')
        codes=selected_codes(view)
        button.configure(text=f'仅补拉缺口（{len(codes)} 只）')
        button.state(['!disabled'] if codes else ['disabled'])
    traces=[(var,var.trace_add('write',selection_changed)) for var in (include_history,period,retry_unchanged)]
    selection_changed()
    ttk.Button(buttons,text='关闭',command=window.destroy).pack(side=tk.RIGHT)
    footer=ttk.Label(window,text='仅请求所选缺口标的；同源历史重取以保持复权一致。完成后复检，不自动循环请求。',
                     wraplength=810,justify=tk.LEFT)
    footer.grid(row=4,column=0,sticky='ew',padx=12,pady=(0,10))
    def resized(event):
        if event.widget is window:
            for label in (summary_label,footer):
                label.configure(wraplength=max(1,event.width-24))
    def destroyed(event):
        if event.widget is window:
            for var,trace in traces:
                var.trace_remove('write',trace)
            traces.clear()
    window.bind('<Configure>',resized,add='+')
    window.bind('<Destroy>',destroyed,add='+')
    return window
