"""Compact gap review shared by all sources; no direct market writes."""
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from workbench.tickflow_integrity import integrity_view
from workbench.gap_review import (GROUPS, STATES, classify_review, review_summary,
                                 review_selection, repair_selection, review_evidence)


def show_integrity(parent, report, on_repair, store=None, on_changed=None):
    from workbench.sources import SOURCES
    window = ttk.Toplevel(parent)
    window.title(SOURCES.get(report.get('source'), '行情')+' 缺口分类处理')
    window.geometry('920x590'); window.minsize(650, 450)
    window.columnconfigure(0, weight=1); window.rowconfigure(2, weight=1)
    heading = ttk.Frame(window)
    heading.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 0))
    ttk.Label(heading, text=f"回执 {report['asof']} · 扫描周期").pack(side=tk.LEFT)
    period = tk.StringVar(value='日线' if report['timeframe'] == 'daily' else '周线')
    values = ['日线', '周线'] if len(report.get('periods', {})) > 1 else [period.get()]
    ttk.Combobox(heading, textvariable=period, values=values, state='readonly', width=8).pack(side=tk.LEFT, padx=8)
    summary = tk.StringVar()
    summary_label = ttk.Label(window, textvariable=summary, wraplength=890, justify=tk.LEFT)
    summary_label.grid(row=1, column=0, sticky='ew', padx=12, pady=10)
    frame = ttk.Frame(window); frame.grid(row=2, column=0, sticky='nsew', padx=12)
    tree = ttk.Treeview(frame, columns=('count', 'pending', 'state'), selectmode='extended', height=9)
    tree.heading('#0', text='分类（展开可逐只处理）')
    tree.heading('count', text='数量'); tree.heading('pending', text='待处理'); tree.heading('state', text='处理方式')
    tree.column('#0', width=510, minwidth=280, stretch=True)
    tree.column('count', width=65, minwidth=45, stretch=False, anchor=tk.CENTER)
    tree.column('pending', width=65, minwidth=45, stretch=False, anchor=tk.CENTER)
    tree.column('state', width=115, minwidth=85, stretch=False, anchor=tk.CENTER)
    scroll = ttk.Scrollbar(frame, command=tree.yview); tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y); tree.pack(fill=tk.BOTH, expand=True)
    controls = ttk.Frame(window); controls.grid(row=3, column=0, sticky='ew', padx=12, pady=10)
    include_history = tk.BooleanVar(value=False)
    ttk.Checkbutton(controls, text='显示更早历史（不影响当前扫描）', variable=include_history).pack(anchor=tk.W)
    help_label = ttk.Label(controls, text='选择分类或股票后处理；具体缺日和证据在详情中查看。', wraplength=890, justify=tk.LEFT)
    help_label.pack(fill=tk.X, pady=5)
    buttons = ttk.Frame(controls); buttons.pack(fill=tk.X)
    evidence = review_evidence(store, report['asof'], report['source']) if store else {}
    memory, state = {}, {}

    def selected_report():
        return integrity_view(report, 'daily' if period.get() == '日线' else 'weekly')

    def selections():
        return [iid.split(':', 1)[1] for iid in tree.selection()]

    def rows_selected():
        return review_selection(state['review'], selections())

    def selection_changed(*_):
        codes = repair_selection(state['review'], selections())
        repair_button.configure(text=f'继续补拉（{len(codes)} 只）')
        repair_button.state(['!disabled'] if codes else ['disabled'])
        for button in (ignore_button, pending_button, details_button):
            button.state(['!disabled'] if rows_selected() else ['disabled'])

    def refresh(*_):
        old = list(tree.selection())
        opened = {iid for iid in tree.get_children() if tree.item(iid, 'open')}
        view = selected_report()
        decisions = store.gap_review_decisions(view['source']) if store else memory
        review = classify_review(view, evidence, decisions, include_history.get())
        state.update(review=review, view=view); summary.set(review_summary(review))
        tree.delete(*tree.get_children())
        for group in review['groups']:
            iid = 'group:'+group['id']
            tree.insert('', tk.END, iid=iid, text=group['label'], values=(group['count'], group['pending'], group['state']), open=iid in opened)
            for row in group['rows']:
                tree.insert(iid, tk.END, iid='issue:'+row['key'], text=row['code']+' '+row['name'],
                            values=('', '', STATES[row['state']] if row['blocked'] or row['group']=='outside' else '无需处理'))
        retained = [iid for iid in old if tree.exists(iid)]
        if not retained:
            retained = ['group:'+g['id'] for g in review['groups'] if any(r['repairable'] and r['state'] != 'ignored' for r in g['rows'])]
        tree.selection_set(retained); selection_changed()

    def decide(action):
        issues = rows_selected()
        if not issues: return
        try:
            if store:
                store.set_gap_review_decisions(report['source'], issues, action)
            else:
                memory.update({r['key']: {'state': action} for r in issues})
            refresh()
            if on_changed: on_changed()
        except Exception as exc:
            messagebox.showerror('处理方式未保存', str(exc), parent=window)

    def details():
        selected = rows_selected()
        dialog = ttk.Toplevel(window); dialog.title('所选分类详情'); dialog.geometry('800x460')
        container = ttk.Frame(dialog); container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        text = tk.Text(container, wrap='word', font=('Microsoft YaHei UI', 10))
        bar = ttk.Scrollbar(container, command=text.yview); text.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y); text.pack(fill=tk.BOTH, expand=True)
        lines = list(report.get('limitations', []))
        for row in selected:
            lines.extend(['', row['code']+' '+row['name']+' · '+GROUPS[row['group']], row['reason']])
            if row['unchanged']: lines.append('已补拉无改善；再次补拉不保证补齐。')
            if row['proof']: lines.append('核验依据：'+row['proof'])
            if row['raw'].get('dates'): lines.append('缺日：'+'、'.join(row['raw']['dates']))
        text.insert('1.0', '\n'.join(lines)); text.configure(state='disabled')
        ttk.Button(dialog, text='关闭', command=dialog.destroy).pack(pady=(0, 10))

    def repair():
        selected = rows_selected(); codes = repair_selection(state['review'], selections())
        if not codes: return
        issues = [r for r in selected if r['code'] in codes and r['repairable'] and r['state'] != 'ignored']
        if any(r['unchanged'] for r in issues) and not messagebox.askyesno(
                '再次补拉', '所选股票含已补拉无改善项。再次请求不保证补齐，是否继续？', parent=window): return
        try:
            accepted = on_repair(codes, state['view']['timeframe'], include_history.get())
            if accepted is False:
                raise ValueError('补拉未提交；请等待当前任务结束后重试')
            if store: store.set_gap_review_decisions(report['source'], issues, 'repair')
            window.destroy()
        except Exception as exc:
            messagebox.showerror('补拉未开始', str(exc), parent=window)

    details_button = ttk.Button(buttons, text='查看详情', command=details); details_button.pack(side=tk.LEFT)
    ignore_button = ttk.Button(buttons, text='忽略统计', command=lambda: decide('ignored')); ignore_button.pack(side=tk.LEFT, padx=5)
    pending_button = ttk.Button(buttons, text='待定／恢复统计', command=lambda: decide('pending')); pending_button.pack(side=tk.LEFT)
    ttk.Button(buttons, text='关闭', command=window.destroy).pack(side=tk.RIGHT)
    repair_button = ttk.Button(buttons, text='继续补拉', command=repair, bootstyle='warning'); repair_button.pack(side=tk.RIGHT, padx=5)
    footer = ttk.Label(window, text='忽略不等于数据已齐。补拉仅使用当前来源；停牌、新股根数不足和身份问题无需重复下载。', wraplength=890, justify=tk.LEFT)
    footer.grid(row=4, column=0, sticky='ew', padx=12, pady=(0, 10))
    tree.bind('<<TreeviewSelect>>', selection_changed)
    tree.bind('<Double-1>', lambda _: details() if rows_selected() and not str(tree.focus()).startswith('group:') else None)
    traces = [(var, var.trace_add('write', refresh)) for var in (include_history, period)]
    refresh()

    def resized(event):
        if event.widget is window:
            for label in (summary_label, footer, help_label): label.configure(wraplength=max(1, event.width-24))

    def destroyed(event):
        if event.widget is window:
            for var, trace in traces: var.trace_remove('write', trace)
            traces.clear()

    window.bind('<Configure>', resized, add='+'); window.bind('<Destroy>', destroyed, add='+')
    return window
