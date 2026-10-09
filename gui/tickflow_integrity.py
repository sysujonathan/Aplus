"""Read-only quality receipt; repair goes through the normal background queue."""
import tkinter as tk
import ttkbootstrap as ttk


def show_integrity(parent,report,on_repair):
    window=ttk.Toplevel(parent)
    window.title('TickFlow 数据完整性')
    window.geometry('850x580')
    window.minsize(650,400)
    scan=report['scan']
    summary=(f"回执日期 {report['asof']} · {'日线' if report['timeframe']=='daily' else '周线'}\n"
             f"应有 {scan['expected']} · 可扫描 {scan['ready']} · 已确认当日停牌 {scan['suspended']} · 排除 {len(scan['gaps'])}\n"
             f"历史有待核验缺口 {len(report['unknown_history'])} 只；不会自动当成停牌，也不补造 K 线。")
    ttk.Label(window,text=summary,wraplength=810).pack(fill=tk.X,padx=12,pady=12)
    frame=ttk.Frame(window); frame.pack(fill=tk.BOTH,expand=True,padx=12)
    text=tk.Text(frame,wrap='word',font=('Microsoft YaHei UI',10))
    scroll=ttk.Scrollbar(frame,command=text.yview); text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT,fill=tk.Y); text.pack(fill=tk.BOTH,expand=True)
    lines=['当前扫描排除项：']
    lines.extend(f"{g['code']}：{g['error']}" for g in report['excluded'])
    lines.append('\n历史未知缺口（需补拉或查公告，不代表停牌）：')
    lines.extend(f"{g['code']}：{len(g['dates'])} 天 · "+'、'.join(g['dates']) for g in report['unknown_history'])
    lines.append('\n已核验停牌证据（不用补拉）：')
    for g in report.get('current_halts',[]):
        lines.append(f"{g['code']}：当日已确认停牌")
        for proof in g['evidence']:
            lines.append(f"{proof['halt']} ～ {proof['resume'] or '连续停牌、不再复牌'} · "+' '.join(proof['urls']))
    for g in report['confirmed_history']:
        lines.append(f"{g['code']}：{len(g['dates'])} 个交易日")
        for proof in g['evidence']:
            lines.append(f"{proof['halt']} ～ {proof['resume'] or '连续停牌、不再复牌'} · "+' '.join(proof['urls']))
    if report['request_failures']:
        lines.append('\n本次请求/保存失败：')
        lines.extend(f"{g.get('code','')}：{g['error']}" for g in report['request_failures'])
    text.insert('1.0','\n'.join(lines)); text.configure(state='disabled')
    controls=ttk.Frame(window); controls.pack(fill=tk.X,padx=12,pady=12)
    include_history=tk.BooleanVar(value=False)
    ttk.Checkbutton(controls,text='同时补拉扫描区间外历史缺口',variable=include_history).pack(side=tk.LEFT)
    def repair():
        codes=report['repair_codes'] if include_history.get() else report['scan_repair_codes']
        if not codes:
            return
        on_repair(codes); window.destroy()
    button=ttk.Button(controls,text='仅补拉缺口',command=repair,bootstyle='warning')
    button.pack(side=tk.RIGHT,padx=5)
    def selection_changed(*args):
        codes=report['repair_codes'] if include_history.get() else report['scan_repair_codes']
        button.configure(text=f'仅补拉缺口（{len(codes)} 只）')
        button.state(['!disabled'] if codes else ['disabled'])
    include_history.trace_add('write',selection_changed)
    selection_changed()
    ttk.Button(controls,text='关闭',command=window.destroy).pack(side=tk.RIGHT)
    ttk.Label(window,text='补拉每只仅一次；完成后重新核验。仍有缺口会继续显示，不自动循环请求。',
              wraplength=810).pack(fill=tk.X,padx=12,pady=(0,10))
