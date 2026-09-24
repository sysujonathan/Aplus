"""Trader-facing local application. Start with 启动A.cmd."""
from __future__ import annotations

import io
import json
from datetime import date
from urllib.parse import urlencode

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from workbench.backtest import Assumptions
from workbench.market import completed_date, latest_datasets, load_dataset, parse_codes
from workbench.service import Service, demo_data, import_csv
from workbench.store import ROOT, Store
from workbench.strategies import catalog, prepare, register, set_active, verify_frozen

st.set_page_config(page_title='A · PA 交易工作台', page_icon='◈', layout='wide')


@st.cache_resource
def resources():
    store = Store()
    return store, Service(store)


store, service = resources()
st.markdown('''<style>
.stApp {background:#f5f7fa;color:#182b38}
[data-testid="stSidebar"] {background:#e8eef2}
h1,h2,h3 {letter-spacing:-.035em}
[data-testid="stMetric"] {background:white;border:1px solid #dde5ea;border-radius:12px;padding:16px}
div.stButton>button[kind="primary"] {background:#176b64;border-color:#176b64}
.eyebrow {font:600 12px monospace;letter-spacing:3px;color:#52777b;margin-bottom:6px}
[data-testid="stAppDeployButton"] {display:none}
</style>''', unsafe_allow_html=True)

STATUS = {'completed':'已完成','partial':'部分完成','failed':'失败','cancelled':'已停止',
          'interrupted':'上次中断','running':'运行中','queued':'排队中'}
KIND = {'sync':'行情同步','scan':'策略匹配','backtest':'回测研究','validate':'策略验证','universe':'股票名单'}
TF = {'daily':'日线','weekly':'周线','monthly':'月线'}
SOURCE = {'baostock':'真实行情 · BaoStock','csv':'自行导入 · CSV','demo':'演示行情 · 非真实市场'}


def submit(kind,spec):
    try:
        jid = service.submit(kind,spec)
        st.success(f'任务已开始：{jid}。下方显示进度，也可去“运行与文件”查看。')
    except Exception as exc:
        st.error(str(exc))


def coverage_panel(audit):
    st.write(f"应有 {audit['expected']} 只 · 日期就绪 {audit['ready']} 只 · "
             f"当日停牌不扫描 {audit['suspended']} 只 · 待处理缺口 {len(audit['gaps'])} 项")
    st.caption(f"应有行情日：{audit['expected_day'] or '尚未核验'}。股票数量与日期核验不等于逐根历史行情无缺失。")
    if audit['gaps']:
        st.warning('数据尚未齐全，不会把缺少的股票当成没有信号。请先同步补齐。')
        with st.expander('查看缺口及原因', expanded=False):
            st.dataframe(pd.DataFrame(audit['gaps']).rename(columns={'code':'股票','error':'原因'}),hide_index=True)


@st.fragment(run_every=2)
def current_job():
    jobs = store.rows('SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT 1')
    if not jobs:
        return
    job = jobs[0]
    with st.container(border=True):
        st.caption(f"最近任务 · {KIND[job['kind']]} · {STATUS[job['status']]} · {job['id']}")
        if job['status'] in {'queued','running'}:
            message = job['message'] or '正在准备'
            if job['kind']=='scan':
                settings = json.loads(job['spec'])
                stocks = len(settings.get('datasets',[]))
                receipt = json.loads(job['result'])
                if receipt.get('coverage'):
                    stocks = receipt['coverage']['ready']
                strategies = max(1,len(settings.get('strategies',[])))
                message = f"已扫描 {min(stocks,job['progress']//strategies)}/{stocks} 只股票 · {strategies} 个策略 · 已计算 {job['progress']}/{stocks*strategies} 次"
            st.progress(min(job['progress']/max(1,job['total']),1.0), text=message)
            if st.button('停止任务',key='stop_'+job['id']):
                service.cancel(job['id'])
                st.info('已请求停止；当前数据请求或策略计算结束后生效。')
        elif job['status'] in {'failed','interrupted','cancelled','partial'}:
            st.warning(job['message'])
        else:
            st.caption('已完成。切换页面或点击“刷新结果”查看新数据。')
        if job['kind']=='sync' and job['status'] not in {'queued','running'}:
            receipt = json.loads(job['result'])
            if 'skipped' in receipt:
                st.write(f"已有行情跳过 {receipt['skipped']} 只 · 新下载 {receipt['downloaded']} 只 · "
                         f"补齐 {receipt['updated']} 只 · 历史刷新 {receipt['refreshed']} 只 · "
                         f"失败 {len(receipt['errors'])} 只")
            if receipt.get('coverage'):
                coverage_panel(receipt['coverage'])
        if job['kind']=='scan':
            receipt = json.loads(job['result'])
            if 'reused' in receipt:
                st.caption(f"复用已有判断 {receipt['reused']} 次 · 本次计算 {receipt['calculated']} 次 · 已保存命中 {receipt['signals']} 条")
        if st.button('刷新结果',key='refresh_job'):
            st.rerun()


def choose_data(key):
    source = st.selectbox('行情来源',list(SOURCE),format_func=SOURCE.get,key=key+'_source')
    if source=='demo':
        st.warning('合成演示数据仅用于熟悉操作，不代表任何股票的真实走势或策略效果。')
    rows = latest_datasets(store,source)
    by_id = {r['id']:r for r in rows}
    if key == 'scan':
        from workbench.scope import selected_boards, scan_datasets
        boards = selected_boards(store)
        rows = scan_datasets(store,source)
        by_id = {r['id']:r for r in rows}
        st.caption(f"扫描范围继承市场数据：{'、'.join(boards) or '未选择板块'}；本地可扫描 {len(by_id)} 只股票。使用各股票最新行情版本。")
        if not rows:
            st.info('还没有可扫描的行情，请先到“市场数据”更新行情。')
        return list(by_id)
    all_codes = st.checkbox('选择该来源全部股票',value=True,key=key+'_all')
    codes = st.multiselect('研究 / 扫描范围',list(by_id),default=[] if all_codes else list(by_id)[:3],disabled=all_codes,
                          format_func=lambda x:f"{by_id[x]['code']} · {by_id[x]['rows']} 根 · 至 {by_id[x]['end']}",key=key+'_ids')
    if all_codes:
        codes = list(by_id)
        st.caption(f'当前选择 {len(codes)} 只股票；取消上方勾选可挑选少量股票。')
    if not rows:
        st.info('这个来源还没有行情。请先到“市场数据”同步或导入。')
    return codes


def candles(frame,entry=None,stop=None,target=None):
    frame = frame.tail(100)
    fig = go.Figure(go.Candlestick(x=frame.date,open=frame.open,high=frame.high,low=frame.low,close=frame.close,
                                  increasing_line_color='#dc6a5e',decreasing_line_color='#24877d',name='K 线'))
    for value,color in [(entry,'#487cb6'),(stop,'#bd594f'),(target,'#298975')]:
        if value and value>0:
            fig.add_hline(y=value,line_dash='dot',line_color=color,
                         annotation_text=f'{value:.3f}'.rstrip('0').rstrip('.'),
                         annotation_position='top right')
    fig.update_layout(height=460,margin=dict(l=10,r=65,t=20,b=10),xaxis_rangeslider_visible=False,
                      paper_bgcolor='white',plot_bgcolor='white',font=dict(color='#253b48'))
    st.plotly_chart(fig,width='stretch')


def tv_link(code):
    prefix,ticker = code.split('.')
    return 'https://www.tradingview.com/chart/?'+urlencode({'symbol':{'sh':'SSE','sz':'SZSE','bj':'BSE'}[prefix]+':'+ticker})


def market_page():
    st.title('市场数据')
    st.write('先取得已完成的行情，再匹配策略。每次同步保留可追溯的行情版本，不覆盖旧项目。')
    a,b,c = st.tabs(['在线同步','导入行情','数据清单'])
    with a:
        st.caption('BaoStock · A 股日线 · 前复权。联网情况与供应商数据可用性会影响同步。')
        from workbench.market import BOARDS, board_of, select_board_codes
        st.write('选择要更新行情的板块（可多选）')
        columns = st.columns(4)
        from workbench.scope import selected_boards, save_boards
        saved_boards = selected_boards(store)
        boards = [name for column,name in zip(columns,BOARDS)
                  if column.checkbox(name,value=name in saved_boards,key='sync_board_'+name)]
        save_boards(store,boards)
        universe = store.root/'universe.csv'
        if universe.exists():
            directory = pd.read_csv(universe)
            counts = directory.code.map(board_of).value_counts()
            st.caption(' · '.join(f'{name} {int(counts.get(name,0))} 只' for name in BOARDS))
            st.caption(f'本次选择 {sum(int(counts.get(name,0)) for name in boards)} 只股票；仅更新所选板块，不删除其他板块已存行情。')
            if '北交所' in boards and not counts.get('北交所',0):
                st.warning('当前数据源目录未提供北交所股票，暂不能在线同步该板块。请取消北交所后同步其他板块；北交所行情仍可通过“导入行情”导入。')
        else:
            st.caption('首次同步会自动获取股票目录，再按勾选板块更新行情。')
        with st.expander('股票目录（新股上市后可更新）'):
            if st.button('更新股票目录'):
                submit('universe',{'date':completed_date()})
            st.caption('目录用于识别各板块的股票代码，不是行情数据；不包含退市历史全集。')
        left,right = st.columns(2)
        start = left.date_input('历史起点',date(2016,1,1),key='sync_start')
        end = right.date_input('同步至',date.fromisoformat(completed_date()),max_value=date.fromisoformat(completed_date()),key='sync_end')
        st.caption('先核对交易日和股票目录，已有范围跳过，只补历史和新增日期；追加时核对衔接价格。出现缺口后再次同步会复用已完成部分，不需要重新下载全部。')
        force = st.checkbox('重新核验并刷新历史（仅在怀疑数据有误时使用，耗时较长）',value=False)
        if st.button('同步市场数据',type='primary'):
            try:
                if not boards:
                    raise ValueError('请至少勾选一个板块')
                if universe.exists():
                    select_board_codes(directory,boards)
                submit('sync',{'boards':boards,'start':str(start),'end':str(end),'force':force})
            except Exception as exc:
                st.error(str(exc))
    with b:
        st.write('CSV 必须包含：date、open、high、low、close、volume。日期使用 YYYY-MM-DD；每份文件一只股票。')
        st.download_button('下载 CSV 格式示例','date,open,high,low,close,volume\n2025-01-02,10,10.5,9.8,10.2,100000\n','行情格式示例.csv')
        uploaded = st.file_uploader('行情 CSV',type=['csv'])
        code = st.text_input('该文件对应股票','sh.600000')
        adjustment = st.selectbox('价格口径',['前复权','后复权','不复权'])
        if st.button('检查并导入'):
            try:
                if uploaded is None:
                    raise ValueError('请先选择 CSV 文件')
                identifier = import_csv(store,uploaded.getvalue(),parse_codes(code)[0],adjustment)
                st.success(f'已导入并留存快照：{identifier[:12]}')
            except Exception as exc:
                st.error(str(exc))
        with st.expander('没有行情？先用演示数据熟悉操作'):
            st.caption('生成三只股票代码下的合成走势，独立标记为 demo，不会混入真实行情。')
            if st.button('生成演示行情'):
                demo_data(store)
                st.success('演示行情已生成。扫描或研究时请选择“演示行情”来源。')
    with c:
        rows = store.rows('SELECT code,timeframe,source,adjustment,start,end,rows,created,id FROM datasets ORDER BY created DESC,rowid DESC')
        st.dataframe(pd.DataFrame(rows).rename(columns={'code':'股票','source':'来源','adjustment':'复权口径','start':'开始','end':'结束','rows':'根数','created':'保存时间','id':'快照编号','timeframe':'周期'}),hide_index=True,width='stretch')
        st.caption('同一股票可以有多个历史版本，回测始终指向当时选中的版本。这里不提供删除历史数据按钮。')
    current_job()


def scan_page():
    st.title('日常扫描')
    st.write('同步 → 匹配策略 → 到 TradingView 分析 → 记录自己的计划。工具不代替你下单。')
    with st.expander('开始一次扫描',expanded=True):
        ids = choose_data('scan')
        tf = st.selectbox('扫描周期',['daily','weekly'],format_func=TF.get)
        available = {k:s for k,s in entries.items() if s.state=='active' and tf in s.timeframes}
        selected = st.multiselect('启用的策略',list(available),default=list(available),format_func=lambda k:available[k].name)
        st.caption(f'本次扫描 {len(ids)} 只股票 × {len(selected)} 个策略，共 {len(ids)*len(selected)} 次策略计算。')
        asof = st.date_input('只使用此日期之前已完成的 K 线',date.fromisoformat(completed_date()),max_value=date.fromisoformat(completed_date()))
        st.caption('扫描只读取本地行情，不自动补数据。列表会显示实际行情日；旧行情命中不等于今天的新机会。')
        from workbench.scope import selected_boards
        source = st.session_state.get('scan_source','baostock')
        audit = None
        if source == 'baostock':
            from workbench.readiness import audit_scope
            audit = audit_scope(store,selected_boards(store),str(asof),ids)
            coverage_panel(audit)
        if st.button('匹配策略',type='primary',disabled=bool(audit and not audit['complete'])):
            submit('scan',{'datasets':ids,'strategies':selected,'timeframe':tf,'asof':str(asof),'boards':selected_boards(store),'source':source})
    current_job()
    st.subheader('扫描结果')
    scans = store.rows("SELECT * FROM jobs WHERE kind='scan' ORDER BY created DESC,rowid DESC LIMIT 100")
    if not scans:
        st.info('运行一次扫描后，这里显示命中结果；没有命中与计算失败会分别说明。')
        return
    by_id = {j['id']:j for j in scans}
    jid = st.selectbox('选择扫描回执',list(by_id),format_func=lambda k:f"{by_id[k]['created']} · {STATUS[by_id[k]['status']]} · {k}")
    job = by_id[jid]
    report = json.loads(job['result'])
    if report.get('coverage'):
        coverage_panel(report['coverage'])
    cols = st.columns(3)
    cols[0].metric('成功匹配',report.get('success',0))
    cols[1].metric('命中',report.get('signals',0))
    cols[2].metric('失败',len(report.get('errors',[])))
    if report.get('errors'):
        st.warning('部分股票或策略未完成，不能把它们视为“没有信号”。')
        st.dataframe(report['errors'],hide_index=True)
    settings = json.loads(job['spec'])
    # A re-run may reuse an immutable observation written by a prior job.
    rows = store.rows('SELECT o.*,d.source,d.adjustment FROM observations o JOIN datasets d ON d.id=o.dataset_id ORDER BY o.asof DESC,o.created DESC')
    rows = [r for r in rows if r['dataset_id'] in settings['datasets'] and r['strategy'] in settings['strategies']
            and r['timeframe']==settings['timeframe'] and r['asof']<=settings['asof']
            and (r['job_id']==jid or (report.get('strategy_versions',{}).get(r['strategy'])==r['version']))]
    # Only observations actually seen as of the scan date, not all historical hits.
    if report.get('observation_ids') is not None:
        rows = [r for r in rows if r['id'] in report['observation_ids']]
    if not rows:
        st.info('这次回执没有命中记录。请同时查看任务是否完整成功。')
        return
    counts = {key:sum(r['strategy']==key for r in rows) for key in settings['strategies']}
    total_hits = len(rows)
    group = st.radio('按策略查看',['all',*counts],horizontal=True,key='result_group_'+jid,
                     format_func=lambda k:f"全部（{total_hits}）" if k=='all' else f"{entries[k].name}（{counts[k]}）")
    all_rows = rows
    rows = [r for r in all_rows if group=='all' or r['strategy']==group]
    if not rows:
        st.info('该策略在这次扫描中没有命中。')
        return
    display = []
    for r in rows:
        p = json.loads(r['payload'])
        display.append({'股票':r['code'],'策略':entries[r['strategy']].name,'行情日':r['asof'],'形态日':r['setup_date'],
                        '入场参考':p['entry'],'止损参考':p['stop'],'目标参考':p['target'],'来源':SOURCE[r['source']]})
    st.caption('点击股票所在行查看 K 线，也可用“上一只 / 下一只”连续翻看。')
    view_key = 'result_view_'+jid+'_'+group
    chosen = st.dataframe(display,hide_index=True,width='stretch',height=260,
                          on_select='rerun',selection_mode='single-row',key=view_key)
    position_key = view_key+'_position'
    last_key = view_key+'_last_click'
    selected_rows = chosen.selection.rows
    if selected_rows and selected_rows != st.session_state.get(last_key):
        st.session_state[position_key] = selected_rows[0]
    st.session_state[last_key] = list(selected_rows)
    index = min(st.session_state.get(position_key,0),len(rows)-1)
    previous, counter, following = st.columns([1,3,1])
    if previous.button('← 上一只',disabled=index==0,key=view_key+'_previous'):
        index -= 1
    if following.button('下一只 →',disabled=index==len(rows)-1,key=view_key+'_next'):
        index += 1
    st.session_state[position_key] = index
    counter.markdown(f"**{index+1} / {len(rows)} · {rows[index]['code']} · {entries[rows[index]['strategy']].name}**")
    st.download_button('导出当前分类 CSV',pd.DataFrame(display).to_csv(index=False).encode('utf-8-sig'),f'候选-{jid}-{group}.csv')
    o = rows[index]
    p = json.loads(o['payload'])
    st.link_button('打开 TradingView，人工分析',tv_link(o['code']))
    st.caption(f"价格口径：{o['adjustment']}。请核对 TradingView 的复权与周期设置；参考价格不是委托单。")
    if o['source']=='demo':
        st.warning('这是合成演示走势，与 TradingView 的真实行情不一致。')
    if p['warning']:
        st.warning(p['warning'])
    frame,_ = load_dataset(store,o['dataset_id'])
    candles(prepare(frame,o['timeframe'],o['asof']),p['entry'],p['stop'],p['target'])
    with st.expander('原始信号与评级'):
        st.json(p)
    with st.form('plan_'+o['id']):
        st.subheader('我的交易计划')
        st.caption('策略给出参考，你可以独立修改自己的计划；不回写策略。重复保存保留修改历史。')
        state = st.selectbox('我的决定',['观察','计划交易','已手工入场','已手工退出','忽略'])
        c1,c2,c3,c4 = st.columns(4)
        entry = c1.number_input('计划入场',min_value=0.,value=float(p['entry'] or 0),format='%.3f')
        stop = c2.number_input('计划止损',min_value=0.,value=float(p['stop'] or 0),format='%.3f')
        target = c3.number_input('计划目标',min_value=0.,value=float(p['target'] or 0),format='%.3f')
        quantity = c4.number_input('计划股数',min_value=0,value=100,step=100)
        notes = st.text_area('行情背景、入场条件、放弃条件与复盘备注')
        if st.form_submit_button('保存我的计划',type='primary'):
            try:
                store.save_plan(o['id'],state,entry,stop,target,quantity,notes)
                st.success('已保存，可到“我的计划”继续管理。')
            except Exception as exc:
                st.error(str(exc))


def plans_page():
    st.title('我的计划')
    st.write('这里是你的判断记录，不是系统模拟成交记录，更不是券商持仓。')
    rows = store.rows('SELECT * FROM plans ORDER BY updated DESC')
    if not rows:
        st.info('在扫描结果中展开机会，保存自己的计划后会显示在这里。')
        return
    st.dataframe(pd.DataFrame(rows)[['code','strategy','timeframe','setup_date','state','entry','stop','target','quantity','notes']].rename(
        columns={'code':'股票','strategy':'策略','timeframe':'周期','setup_date':'形态日','state':'人工状态','entry':'计划入场','stop':'计划止损','target':'计划目标','quantity':'股数','notes':'备注'}),hide_index=True,width='stretch')
    st.download_button('导出计划与备注',pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig'),'我的计划.csv')
    ix = st.selectbox('继续管理',range(len(rows)),format_func=lambda i:f"{rows[i]['code']} · {rows[i]['state']} · {rows[i]['setup_date']}")
    p = rows[ix]
    st.link_button('TradingView',tv_link(p['code']))
    with st.form('edit_'+p['id']):
        states = ['观察','计划交易','已手工入场','已手工退出','忽略']
        state = st.selectbox('更新状态',states,index=states.index(p['state']))
        c1,c2,c3,c4 = st.columns(4)
        en = c1.number_input('计划入场',min_value=0.,value=float(p['entry'] or 0))
        sl = c2.number_input('计划止损',min_value=0.,value=float(p['stop'] or 0))
        tp = c3.number_input('计划目标',min_value=0.,value=float(p['target'] or 0))
        qty = c4.number_input('股数',min_value=0,value=int(p['quantity'] or 0),step=100)
        notes = st.text_area('分析与复盘',p['notes'],height=180)
        st.caption('实际成交价、日期和复盘可记在备注中；本版不把手工记录当作券商成交回报。')
        if st.form_submit_button('保存更新'):
            try:
                store.save_plan(p['observation_id'],state,en,sl,tp,qty,notes)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with st.expander('查看修改历史'):
        st.dataframe(store.rows('SELECT time,payload FROM plan_history WHERE plan_id=? ORDER BY seq DESC',(p['id'],)),hide_index=True)


def research_page():
    st.title('回测研究')
    st.write('先验证一个明确问题，再决定是否调整策略。研究条件不会修改原策略文件或正式计划。')
    st.info('本版是“独立信号研究”：每个形态首次观察后，以固定参考价模拟。不是资金组合回测，也不复刻 H2 动态挂单。')
    ids = choose_data('research')
    key = st.selectbox('研究策略',list(entries),format_func=lambda k:entries[k].name+' · '+('日常已启用' if entries[k].state=='active' else '仅研究'))
    strategy = entries[key]
    tf = st.selectbox('研究周期',strategy.timeframes,format_func=TF.get)
    c1,c2 = st.columns(2)
    start = c1.date_input('样本开始',date(2025,1,1))
    end = c2.date_input('样本结束',date.fromisoformat(completed_date()),max_value=date.fromisoformat(completed_date()))
    st.caption('最初 124 根用于热身，不产生研究样本。逐日重算比直接读取历史信号列慢，但避免使用未来 K 线。建议先少量股票、短日期试跑。')
    with st.expander('研究条件与成交假设',expanded=True):
        c1,c2,c3 = st.columns(3)
        wait = c1.number_input('最多等待入场（根）',1,100,5)
        hold = c2.number_input('最多持有（根）',1,500,20)
        rr = c3.number_input('最低计划盈亏比',0.,20.,0.,step=0.5)
        score = st.number_input('最低原始策略分数（各策略量纲不同，0 表示不额外筛分）',0.,10000.,0.)
        c1,c2,c3 = st.columns(3)
        fee = c1.number_input('单边费率（万分之）',0.,100.,3.)
        tax = c2.number_input('卖出额外费用（万分之）',0.,100.,5.)
        slip = c3.number_input('单边滑点（万分之）',0.,100.,5.)
        st.caption('这些是可修改的研究假设，不是当前券商收费标准。下一根才允许退出；同根触及止盈止损按止损处理；单一价格 K 线不成交。')
    if st.button('开始回测研究',type='primary'):
        submit('backtest',{'datasets':ids,'strategy':key,'timeframe':tf,'start':str(start),'end':str(end),
                          'assumptions':dict(wait_bars=wait,holding_bars=hold,min_rr=rr,min_score=score,
                                             commission_bps=fee,sell_tax_bps=tax,slippage_bps=slip)})
    current_job()
    st.subheader('研究报告与对比')
    jobs = store.rows("SELECT * FROM jobs WHERE kind='backtest' AND status IN ('completed','partial') ORDER BY created DESC,rowid DESC")
    if not jobs:
        st.caption('完整或部分完成的报告会保留在这里；失败和停止的任务在运行回执中。')
        return
    mapping = {j['id']:j for j in jobs}
    chosen = st.multiselect('选择报告，可同时对比多个条件',list(mapping),default=[jobs[0]['id']],
                            format_func=lambda k:f"{mapping[k]['created']} · {json.loads(mapping[k]['spec'])['strategy']} · {k}")
    comparison = []
    for jid in chosen:
        job = mapping[jid]
        report = json.loads(job['result'])
        comparison.append({'报告':jid,'状态':STATUS[job['status']],'已结束样本':report['closed_trades'],
                           '胜率':report['win_rate'],'平均净收益率':report['mean_return'],'平均R':report['mean_r'],
                           '仍持有':report['open_trades'],'版本':report['strategy_version'][:12]})
        with st.expander('报告详情 · '+jid,expanded=len(chosen)==1):
            if not report['real_data']:
                st.warning('此报告含合成演示数据，不可用于判断交易效果或启用策略。')
            if report['errors']:
                st.error('部分样本失败；该报告不作为新策略启用依据。')
                st.json(report['errors'])
            st.write(report['note'])
            st.json({'研究条件':json.loads(job['spec']),'限制':report['limitations']})
            content = store.read_artifact(report['trades_path'])
            st.download_button('下载逐笔研究 CSV',content,jid+'-trades.csv',key=jid+'_csv')
            st.download_button('下载完整回执 JSON',store.read_artifact(report['report_path']),jid+'-report.json',key=jid+'_report')
            try:
                trades = pd.read_csv(io.BytesIO(content))
                st.dataframe(trades,hide_index=True,width='stretch')
            except pd.errors.EmptyDataError:
                st.info('该条件下没有可统计的交易样本。')
    if comparison:
        st.dataframe(comparison,hide_index=True,width='stretch')


def registry_page():
    st.title('策略工厂')
    st.write('开发 → 注册到研究区 → 验证 → 回测 → 人工决定启用。回测好看不会自动替你启用策略。')
    st.success('原策略及计算依赖的文件校验已通过；第一阶段不提供修改原策略按钮。')
    st.dataframe([{'策略':s.name,'编号':s.id,'用途':'日常扫描' if s.state=='active' else '仅研究',
                   '周期':' / '.join(TF[t] for t in s.timeframes),'版本':s.version[:12]} for s in entries.values()],hide_index=True,width='stretch')
    with st.expander('注册自己的新策略 / 新参数版本'):
        st.warning('策略文件是可执行 Python 代码，拥有当前用户的本机权限。只注册你自己开发或已经审查、信任的文件；这不是隔离沙箱。')
        st.download_button('下载新策略接口示例',(ROOT/'examples/new_strategy.py').read_bytes(),'new_strategy.py')
        upload = st.file_uploader('单文件策略插件',type=['py'])
        name = st.text_input('策略显示名称')
        cls = st.text_input('文件里的策略类名','ExampleStrategy')
        params = st.text_area('构造参数 JSON（新参数注册为独立版本）','{}')
        tfs = st.multiselect('适用周期',['daily','weekly','monthly'],default=['daily'],format_func=TF.get)
        trusted = st.checkbox('我确认这个文件可信，并同意在本机执行它以验证接口')
        if st.button('注册到研究区',disabled=not trusted):
            try:
                if upload is None:
                    raise ValueError('请先选择策略文件')
                register(store,upload.getvalue(),cls,name,json.loads(params),tfs)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.subheader('验证与启用')
    key = st.selectbox('选择策略',list(entries),format_func=lambda k:entries[k].name,key='validate_key')
    ids = choose_data('validate')
    tf = st.selectbox('验证周期',entries[key].timeframes,format_func=TF.get,key='validate_tf')
    if st.button('运行接口与重复性验证'):
        if not ids:
            st.error('请先选择一份行情')
        else:
            submit('validate',{'strategy':key,'dataset':ids[0],'timeframe':tf})
    if key.startswith('CUSTOM_'):
        reason = st.text_area('人工决定的理由：依据哪次研究，观察到了什么风险？')
        approved = st.checkbox('我已阅读验证及真实行情回测结果，并决定用于日常扫描')
        c1,c2 = st.columns(2)
        if c1.button('启用当前策略版本',disabled=not approved):
            try:
                set_active(store,key,True,reason)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if c2.button('退回研究区'):
            try:
                set_active(store,key,False,reason)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    current_job()


def operations_page():
    st.title('运行与文件')
    st.write('知道系统做了什么，也知道它没有完成什么。这里记录工具自身的操作，不监控其他程序。')
    current_job()
    jobs = store.rows('SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT 300')
    if jobs:
        st.dataframe(pd.DataFrame(jobs)[['id','kind','status','created','finished','message']],hide_index=True,width='stretch')
        byid = {j['id']:j for j in jobs}
        jid = st.selectbox('查看完整回执',list(byid))
        j = byid[jid]
        st.json({'任务':KIND[j['kind']],'状态':STATUS[j['status']],'请求':json.loads(j['spec']),
                 '结果':json.loads(j['result']),'说明':j['message']})
        events = store.rows('SELECT time,action,path,detail FROM events WHERE job_id=? ORDER BY seq',(jid,))
        st.dataframe(events,hide_index=True,width='stretch')
        st.download_button('导出操作记录',pd.DataFrame(events).to_csv(index=False).encode('utf-8-sig'),jid+'-operations.csv')
    with st.expander('最近的文件与人工操作（包括非任务操作）'):
        st.dataframe(store.rows('SELECT time,action,path,detail FROM events ORDER BY seq DESC LIMIT 200'),hide_index=True,width='stretch')
    st.subheader('文件用途')
    st.dataframe([
        {'位置':'core/strategies/','作用':'原策略，原样保留','规则':'第一阶段不修改'},
        {'位置':'workbench/','作用':'新版数据、研究、记录、任务管理','规则':'修改后运行验收测试'},
        {'位置':'runtime/workbench.sqlite3','作用':'行情目录、信号、人工计划、回执','规则':'重要记录，不手工编辑'},
        {'位置':'runtime/market/','作用':'每次同步留存的行情版本','规则':'不可修改；研究报告按编号引用'},
        {'位置':'runtime/research/','作用':'逐笔研究结果与成交假设','规则':'不混入正式计划'},
        {'位置':'runtime/plugins/','作用':'你注册的研究策略版本','规则':'新改动应注册新版本'},
        {'位置':'docs/','作用':'使用说明、系统地图、修改约束','规则':'与实现同步维护'},
    ],hide_index=True,width='stretch')
    st.caption(f'当前运行目录：{store.root}。界面未提供任何删除旧数据或自动下单功能。')


with st.sidebar:
    st.markdown('## ◈ A WORKBENCH')
    st.caption('PA 交易工作台 · 本地版 1.0.1')
    page = st.radio('工作区',['市场数据','日常扫描','我的计划','回测研究','策略工厂','运行与文件','使用说明'],label_visibility='collapsed')
    st.divider()
    st.caption('策略提供线索\n\n你负责判断与执行')
    st.caption('首次使用：市场数据 → 同步 / 导入 → 日常扫描')

st.markdown('<div class="eyebrow">OBSERVE / RESEARCH / DECIDE</div>',unsafe_allow_html=True)
try:
    entries = catalog(store)
    {'日常扫描':scan_page,'市场数据':market_page,'我的计划':plans_page,'回测研究':research_page,
     '策略工厂':registry_page,'运行与文件':operations_page,
     '使用说明':lambda:st.markdown((ROOT/'docs/使用说明.md').read_text(encoding='utf-8'))}[page]()
except Exception as exc:
    st.error('当前操作未能完成：'+str(exc))
    st.info('数据不会被自动清理。可检查“运行与文件”中的回执；如是策略校验失败，请恢复原文件后重启。')
    with st.expander('供维护人员定位的详情'):
        st.exception(exc)
