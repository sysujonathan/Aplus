"""Web uses the same classifications, issue keys and decisions as desktop."""
import streamlit as st
from .gap_review import (classify_review, review_receipt, review_evidence, review_summary,
                         review_selection, repair_selection, status_review_selection)
from .readiness import expected_day
from .sources import SOURCES


def render_gap_review(store, service, source, boards, day, periods, begin, busy):
    with st.expander(SOURCES[source]+' 缺口分类处理'):
        tf = st.selectbox('扫描周期', periods, format_func=lambda x: '日线' if x == 'daily' else '周线',
                          key='gap_review_period', disabled=busy)
        try:
            stamp = expected_day(store, day, source)
            view = review_receipt(store, source, boards, stamp, tf)
        except ValueError as exc:
            st.caption(str(exc)); return
        historical = st.checkbox('显示更早历史（不影响当前扫描）', key='gap_review_history', disabled=busy)
        review = classify_review(view, review_evidence(store, stamp, source),
                                 store.gap_review_decisions(source), historical)
        st.caption(review_summary(review))
        if not review['groups']:
            st.caption('当前没有待处理项。'); return
        st.dataframe([{'分类': g['label'], '数量': g['count'], '待处理': g['pending'], '处理方式': g['state']} for g in review['groups']], hide_index=True)
        labels = {g['id']: g['label'] for g in review['groups']}
        selection = st.multiselect('选择分类', list(labels), format_func=labels.get,
                                   key=f'gap_groups_{source}_{tf}', disabled=busy)
        if st.checkbox('按股票细选', key=f'gap_individual_{source}_{tf}', disabled=busy):
            stocks = {r['key']: r for r in review['rows']}
            selection += st.multiselect('选择股票', list(stocks),
                format_func=lambda key: stocks[key]['code']+' '+stocks[key]['name'],
                key=f'gap_stocks_{source}_{tf}', disabled=busy)
        issues = review_selection(review, selection)
        a, b, c = st.columns(3)
        for column, label, action in ((a, '忽略提醒', 'ignored'), (b, '待定／恢复统计', 'pending')):
            if column.button(label, disabled=busy or not issues, key='gap_'+action):
                store.set_gap_review_decisions(source, issues, action); st.rerun()
        repair = repair_selection(review, selection)
        repeated = any(r['unchanged'] and r['code'] in repair for r in issues)
        confirmed = st.checkbox('确认重试已补拉无改善项', disabled=busy, key='gap_repeat') if repeated else True
        if c.button(f'继续补拉（{len(repair)} 只）', disabled=busy or not repair or not confirmed):
            spec = dict(source=source, boards=boards, start=str(begin), end=day, force=False,
                        repair_codes=repair, scan_timeframe=tf, repair_scope='history' if historical else 'scan')
            try:
                service.submit('sync', spec)
                store.set_gap_review_decisions(source, [r for r in issues if r['code'] in repair and r['repairable'] and r['state'] != 'ignored'], 'repair')
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        status_codes = status_review_selection(review, selection)
        consent = st.checkbox('确认只查询BaoStock交易状态，不下载价格，证据供三源复用', key='gap_status_consent', disabled=busy)
        retry_status = st.checkbox('重查上次未返回明确状态的缺日', key='gap_status_retry', disabled=busy)
        if st.button(f'核验缺日（{len(status_codes)} 只）', key='gap_verify_status', disabled=busy or not status_codes or not consent):
            try:
                service.submit('verify_status', dict(source=source, boards=boards, asof=stamp,
                    timeframe=tf, codes=status_codes, retry=retry_status))
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        with st.expander('所选详情'):
            for limitation in view.get('limitations', []): st.caption(limitation)
            if not issues: st.caption('先选择分类或股票。')
            for row in issues:
                st.write(row['code']+' '+row['name']+'：'+row['reason'])
                if row['unchanged']: st.caption('已补拉无改善，再次请求不保证补齐。')
                if row['proof']: st.caption('核验依据：'+row['proof'])
                if row['raw'].get('dates'): st.caption('缺日：'+'、'.join(row['raw']['dates']))
