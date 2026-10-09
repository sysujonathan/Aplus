"""Daily trader desk: update, screen, inspect, watch. No strategy logic here."""
import json
from datetime import date

import pandas as pd
import streamlit as st

from .market import BOARDS, board_of, completed_date, load_dataset, latest_datasets
from .readiness import audit_scope
from .scope import selected_boards, save_boards, scan_datasets
from .strategies import prepare
from .store import dumps
from .sources import SOURCES,market_source,set_market_source

TF = {'daily':'日线', 'weekly':'周线'}


def names(store):
    try:
        result = {}
        meta = store.rows("SELECT value FROM meta WHERE key='universe_date'")
        if meta:
            path = store.root/f"directories/{meta[0]['value']}-basics.csv"
            if path.exists():
                basic=pd.read_csv(path,dtype=str).fillna('')
                if 'code_name' in basic:
                    result.update(zip(basic.code,basic.code_name))
        from .sources import directory_file
        frame = pd.read_csv(directory_file(store, market_source(store)),dtype=str)
        result.update(zip(frame.code,frame.code_name))
        return result
    except (OSError, AttributeError, ValueError):
        return {}


def start(service, kind, spec):
    try:
        service.submit(kind,spec)
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


def render(store, service, entries, candles, tv_link, coverage_panel, current_job):
    st.title('交易工作台')
    st.caption('更新行情 → 匹配策略 → TradingView 人工分析 · 想再观察几天，加入右侧关注列表')
    busy = bool(store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')"))
    with st.expander('数据与扫描设置', expanded=True):
        source=st.selectbox('日 K 数据源',list(SOURCES),index=list(SOURCES).index(market_source(store)),
                            format_func=SOURCES.get,disabled=busy)
        if source!=market_source(store):
            set_market_source(store,source)
        selected = selected_boards(store)
        columns = st.columns(4)
        boards = [name for col,name in zip(columns,BOARDS)
                  if col.checkbox(name,value=name in selected,key='desk_board_'+name,disabled=busy)]
        save_boards(store,boards)
        a,b,c,d = st.columns([1,1,1,1])
        begin = a.date_input('历史起点',date(2016,1,1),disabled=busy)
        end = b.date_input('行情 / 扫描日期',date.fromisoformat(completed_date()),
                           max_value=date.fromisoformat(completed_date()),disabled=busy)
        c.checkbox('日线',value=True,disabled=True)
        weekly = d.checkbox('同时扫描周线',value=False,disabled=busy)
        periods = ['daily','weekly'] if weekly else ['daily']
        st.caption('启用策略（勾选后生效；只在策略支持的周期计算）')
        active = {k:s for k,s in entries.items() if s.state=='active' and any(tf in s.timeframes for tf in periods)}
        columns = st.columns(min(6,max(1,len(active))))
        strategies = [key for i,(key,s) in enumerate(active.items())
                      if columns[i % len(columns)].checkbox(s.name,value=True,key='desk_strategy_'+key,disabled=busy)]
        ids = [r['id'] for r in scan_datasets(store,source)]
        audits = [audit_scope(store,boards,str(end),ids,timeframe=tf,history_cache_only=True) for tf in periods]
        audit = audits[0]
        a,b,c = st.columns([1,1,3])
        if a.button('同步市场数据',type='primary',disabled=busy):
            if not boards:
                st.error('请至少勾选一个板块')
            elif begin > end:
                st.error('历史起点不能晚于行情日期')
            else:
                start(service,'sync',{'source':source,'boards':boards,'start':str(begin),'end':str(end),'force':False})
        if b.button('匹配策略',type='primary',disabled=busy or not any(a['scan_allowed'] for a in audits) or not strategies):
            start(service,'scan',{'source':source,'boards':boards,'datasets':ids,
                                 'strategies':strategies,'timeframes':periods,'asof':str(end)})
        c.caption(f"应有 {audit['expected']} 只 · 日期就绪 {audit['ready']} 只 · 停牌 {audit['suspended']} 只 · "
                  f"缺口 {len(audit['gaps'])} 项。目录、上市与退市状态随同步自动核对。")
        if not audit['complete']:
            with st.expander('数据尚未就绪 · 查看原因'):
                coverage_panel(audit)
        if source=='tickflow':
            rows=store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")
            if rows:
                integrity=json.loads(rows[0]['value'])
                if set(integrity.get('boards',[]))==set(boards):
                    with st.expander('TickFlow 完整性 / 定向补拉'):
                        st.caption(f"回执日期 {integrity['asof']} · 历史待核验 {len(integrity['unknown_history'])} 只；已证明停牌不用补拉")
                        st.json(integrity)
                        historical=st.checkbox('同时补拉扫描区间外历史缺口',key='tf_repair_history',disabled=busy)
                        repair=integrity['repair_codes'] if historical else integrity['scan_repair_codes']
                        if st.button(f'仅补拉缺口（{len(repair)} 只）',disabled=busy or not repair):
                            start(service,'sync',dict(source='tickflow',boards=boards,start=str(begin),end=str(end),
                                force=False,repair_codes=repair,scan_timeframe='daily'))
        st.caption('北交所仍受当前数据源覆盖限制，不能用沪深数据代替；研究、维护入口留在侧栏。')
    with st.expander('运行进度与回执',expanded=busy):
        current_job()

    code_names = names(store)
    # Only genuine market scans appear on the daily desk; historical demo stays in storage.
    jobs = store.rows("SELECT * FROM jobs WHERE kind='scan' ORDER BY created DESC,rowid DESC LIMIT 100")
    observations = store.rows("SELECT o.*,d.source,d.adjustment FROM observations o JOIN datasets d ON d.id=o.dataset_id "
                              "WHERE d.source=? ORDER BY o.created DESC",(source,))
    by_oid = {o['id']:o for o in observations}
    jobs = [j for j in jobs if json.loads(j['spec']).get('source')==source
            or any(oid in by_oid for oid in json.loads(j['result']).get('observation_ids',[]))]
    rows, receipt_key, settings = [], 'empty', {}
    if jobs:
        by_job = {j['id']:j for j in jobs}
        with st.expander('扫描批次与异常',expanded=False):
            receipt_key = st.selectbox('扫描批次',list(by_job),
                         format_func=lambda k:f"{by_job[k]['created']} · {by_job[k]['status']} · {k[:8]}")
            report = json.loads(by_job[receipt_key]['result'])
            st.caption(f"完成判断 {report.get('success',0)} 次 · 命中 {report.get('signals',0)} 条 · 异常 {len(report.get('errors',[]))} 项")
            if report.get('errors'):
                st.dataframe(report['errors'],hide_index=True)
        report = json.loads(by_job[receipt_key]['result'])
        settings = json.loads(by_job[receipt_key]['spec'])
        rows = [by_oid[oid] for oid in report.get('observation_ids',[]) if oid in by_oid]
    watch = store.rows('SELECT * FROM watchlist WHERE active=1 ORDER BY created DESC')
    watch_codes = {w['code'] for w in watch}
    left,center,right = st.columns([1.2,2.7,1.05],gap='small')
    with left:
        st.subheader('策略候选')
        periods_present = settings.get('timeframes',[settings.get('timeframe','daily')])
        tf = periods_present[0]
        if len(periods_present)>1:
            tf = st.radio('结果周期',periods_present,format_func=TF.get,horizontal=True,key='desk_tf_'+receipt_key)
        rows = [r for r in rows if r['timeframe']==tf]
        counts = {k:sum(r['strategy']==k for r in rows) for k in settings.get('strategies',[])
                  if k not in entries or tf in entries[k].timeframes}
        if counts:
            group = st.radio('策略分类',['all',*counts],format_func=lambda k:'全部' if k=='all' else f"{entries[k].name if k in entries else k} · {counts[k]}",key='desk_group_'+receipt_key)
            rows = [r for r in rows if group=='all' or r['strategy']==group]
        else:
            group = 'all'
        table_key = f'desk_candidates_{receipt_key}_{group}_{tf}'
        if rows:
            selected = st.dataframe([{'关注':'●' if r['code'] in watch_codes else '', '代码':r['code'],
                         '名称':code_names.get(r['code'],''),'周期':TF[r['timeframe']]} for r in rows],
                         height=410,hide_index=True,on_select='rerun',selection_mode='single-row',key=table_key)
            clicked = list(selected.selection.rows)
            if clicked and clicked != st.session_state.get(table_key+'_last'):
                st.session_state['desk_focus'] = rows[clicked[0]]['id']
                st.session_state['desk_from_watch'] = False
            st.session_state[table_key+'_last'] = clicked
            if st.session_state.get('desk_scope') != table_key:
                st.session_state['desk_focus'] = rows[0]['id']
                st.session_state['desk_from_watch'] = False
                st.session_state['desk_scope'] = table_key
            a,b = st.columns(2)
            current = next((i for i,r in enumerate(rows) if r['id']==st.session_state.get('desk_focus')),0)
            if a.button('← 上一只',disabled=current==0):
                st.session_state['desk_focus']=rows[current-1]['id']
                st.session_state['desk_from_watch']=False
            if b.button('下一只 →',disabled=current==len(rows)-1):
                st.session_state['desk_focus']=rows[current+1]['id']
                st.session_state['desk_from_watch']=False
        else:
            if not st.session_state.get('desk_from_watch'):
                st.session_state.pop('desk_focus',None)
            st.info('尚无本批候选。先更新行情并匹配策略；关注列表不随批次清空。')
    with right:
        st.subheader('持续关注')
        st.caption('跨天保留，不等于交易计划')
        if watch:
            display = [{'代码':w['code'],'名称':code_names.get(w['code'],''),'加入':w['created'][:10]} for w in watch]
            selected = st.dataframe(display,height=410,hide_index=True,on_select='rerun',selection_mode='single-row',key='desk_watch')
            clicked = list(selected.selection.rows)
            signature = (tuple(w['code'] for w in watch),tuple(clicked))
            if clicked and signature != st.session_state.get('watch_last_click'):
                st.session_state['desk_focus']=watch[clicked[0]]['observation_id']
                st.session_state['desk_from_watch']=True
            st.session_state['watch_last_click']=signature
            if not rows and st.session_state.get('desk_focus') not in by_oid:
                st.session_state['desk_focus']=watch[0]['observation_id']
                st.session_state['desk_from_watch']=True
        else:
            st.info('从候选中点“加入关注”，以后即使未再命中也会保留。')
        with st.expander('已结束关注'):
            archived=store.rows('SELECT * FROM watchlist WHERE active=0 ORDER BY updated DESC')
            if archived:
                code=st.selectbox('恢复关注',[w['code'] for w in archived])
                if st.button('恢复此股票'):
                    item=next(w for w in archived if w['code']==code)
                    store.update_watch(code,item['notes'],True)
                    st.rerun()
            else:
                st.caption('暂无')
    with center:
        oid = st.session_state.get('desk_focus')
        observation = by_oid.get(oid)
        if observation is None:
            st.info('点击左侧候选或右侧关注股票，查看 K 线并跳转 TradingView。')
            return
        o = observation
        payload=json.loads(o['payload'])
        h2 = o['strategy'] == 'STRATEGY_GAP_H2'
        st.subheader(f"{o['code']} {code_names.get(o['code'],'')}")
        st.caption(f"{entries[o['strategy']].name if o['strategy'] in entries else o['strategy']} · {TF[o['timeframe']]} · 原信号 {o['asof']}")
        a,b=st.columns([2,1])
        a.link_button('在 TradingView 打开 ↗',tv_link(o['code'],o['timeframe']),type='primary',use_container_width=True)
        if b.button('更新关注锚点' if o['code'] in watch_codes else '＋ 加入关注',use_container_width=True):
            store.watch(o['id'])
            st.rerun()
        frame,record=load_dataset(store,o['dataset_id'])
        from_watch=st.session_state.get('desk_from_watch',False)
        reference_ok=True
        if from_watch:
            latest=next((r for r in latest_datasets(store,source) if r['code']==o['code'] and r['timeframe']=='daily'),None)
            if latest:
                current,current_record=load_dataset(store,latest['id'])
                overlap=frame.merge(current,on='date',suffixes=('_old','_new'))
                import numpy as np
                reference_ok=not overlap.empty and np.allclose(overlap.close_old,overlap.close_new,rtol=1e-9,atol=1e-8)
                frame,record=current,current_record
            st.caption(f"关注视图展示最新本地行情至 {record['end']}；原信号不是今天的新命中。")
            if not reference_ok and not h2:
                st.warning('新旧快照的复权价格发生变化，隐藏旧信号价格线；请在 TradingView 重新核对。')
        bars = prepare(frame,o['timeframe'],completed_date() if from_watch else o['asof'])
        if h2:
            from .h2_plan import display_plan, STATE_LABELS
            from .strategies import calculate
            from gui.chart_renderer import render_chart
            spec = entries[o['strategy']]
            payload = display_plan(spec, bars, payload, o['code'], o['setup_date'],
                                   'watch' if from_watch else 'candidate')
            instance, calculated = calculate(spec, bars)
            st.caption(f"H2信号 {payload['h2_setup_date']} · 行情 {bars.date.iloc[-1]} · " +
                       (f"明日计划基于{payload['plan_asof']}收盘"
                        if payload['pending_state'] == 'PENDING' else STATE_LABELS[payload['pending_state']]))
            st.image(render_chart(calculated, payload, '', instance.get_metadata(), code=o['code'],
                                 instrument_type=bars.attrs.get('instrument_type')), width='stretch')
        else:
            candles(bars,payload.get('entry') if reference_ok else None,
                    payload.get('stop') if reference_ok else None,payload.get('target') if reference_ok else None)
        if o['code'] in watch_codes:
            w=next(w for w in watch if w['code']==o['code'])
            with st.expander('关注笔记 / 结束关注',expanded=from_watch):
                notes=st.text_area('观察什么、何时放弃',w['notes'],key='watch_notes_'+o['code'])
                a,b=st.columns(2)
                if a.button('保存关注笔记'):
                    store.update_watch(o['code'],notes)
                    st.success('已保存')
                if b.button('结束关注（可恢复）'):
                    store.update_watch(o['code'],notes,False)
                    st.session_state.pop('desk_focus',None)
                    st.rerun()
        with st.expander('制定交易计划'):
            with st.form('desk_plan_'+o['id']):
                st.caption('只有你主动保存才产生计划；关注本身不会建仓或生成计划。' +
                           ('参考价来自所示收盘的 H2 挂单计划。' if h2 else '价格来自原信号，请核对当前复权。'))
                a,b,c=st.columns(3)
                entry=a.number_input('计划入场',min_value=0.,value=float(payload.get('entry') or 0),format='%.3f')
                stop=b.number_input('计划止损',min_value=0.,value=float(payload.get('stop') or 0),format='%.3f')
                target=c.number_input('计划目标',min_value=0.,value=float(payload.get('target') or 0),format='%.3f')
                qty=st.number_input('股数',min_value=0,value=100,step=100)
                notes=st.text_area('交易计划备注')
                if st.form_submit_button('保存我的计划'):
                    store.save_plan(o['id'],'计划交易',entry,stop,target,qty,notes)
                    st.success('已保存至我的计划；不自动下单。')
