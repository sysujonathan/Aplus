"""Source-neutral gap presentation. Decisions never certify or alter prices/gates."""
import csv
import json
from pathlib import Path

from .sources import SOURCES, FALLBACK_SOURCES, directory_file, calendar_file
from .store import digest, dumps

GROUPS = {
    'history': '历史缺日待核验',
    'halt': '当日停牌',
    'short': '历史根数不足',
    'identity': '证券身份问题',
    'download': '行情待补拉',
    'other': '其他核验问题',
    'outside': '更早历史（不影响当前扫描）',
}
STATES = {'pending': '待定', 'ignored': '已忽略统计', 'repair': '继续补拉'}
IDENTITIES = {
    'sh.601313': ('2018-02-28', '旧代码，已变更为601360（三六零）',
                 'https://static.cninfo.com.cn/finalpage/2018-04-27/1204804713.PDF'),
    'sz.002525': ('2011-04-11', '首次公开发行许可被撤销，非当前在市证券',
                 'https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=694972&stockid=002525'),
}


def csv_rows(path):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            return {r['code']: r for r in csv.DictReader(stream)}
    except (OSError, ValueError, KeyError):
        return {}


def review_evidence(store, day, source):
    """Only dated security/status facts are shared, never OHLCV or coverage."""
    own = csv_rows(directory_file(store, source))
    # Exact-day files, not a mutable latest catalog with an unrelated date.
    dated = store.root / f'directories/{day}.csv'
    statuses = csv_rows(dated)
    basics_files = sorted(p for p in (store.root/'directories').glob('*-basics.csv')
                          if p.name[:10] <= day)
    basics_path = basics_files[-1] if basics_files else None
    basics = csv_rows(basics_path) if basics_path else {}
    calendar = {}
    try:
        calendar = json.loads(calendar_file(store, source).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    return dict(names={code: r.get('code_name', '') for code, r in own.items()},
                statuses=statuses, status_path=str(dated), basics=basics,
                basics_path=str(basics_path or ''), calendar=calendar)


def classify_review(view, evidence=None, decisions=None, include_history=False):
    """Build a small category table and optional per-stock detail, read-only."""
    evidence = evidence or {}
    decisions = decisions or {}
    source, day, tf = view['source'], view['asof'], view['timeframe']
    scan = view['scan']
    retry = set(view.get('retry_codes', []))
    repairable = set(view.get('scan_repair_codes', [])) | retry
    rows = []

    def add(gap, blocked=True, outside=False):
        code = gap['code']
        category = gap.get('category', '')
        if not category:
            error = gap.get('error', '')
            if error.startswith('本地缺少行情') or error == '所选日期尚无本地行情':
                category = 'missing_dataset'
            elif error.startswith('行情只到 '):
                category = 'stale_tail'
            elif error == '行情文件缺失':
                category = 'invalid_file'
        group = {'input_gap': 'history', 'insufficient_bars': 'short',
                 'stale_tail': 'download', 'missing_dataset': 'download',
                 'directory_pending': 'identity'}.get(category, 'other')
        reason, proof = gap.get('error', ''), ''
        status = evidence.get('statuses', {}).get(code, {})
        known = IDENTITIES.get(code)
        if outside:
            group, reason = 'outside', f"更早历史缺少 {len(gap.get('dates', []))} 个交易日"
        elif known and day >= known[0]:
            group, reason, proof = 'identity', known[1], known[2]
        elif category == 'current_halt' or status.get('tradeStatus') == '0':
            group, reason = 'halt', '当日停牌，不需要补拉当天K线'
            proof = evidence.get('status_path', '') if status.get('tradeStatus') == '0' else dumps(gap.get('evidence', []))
        elif category == 'insufficient_bars':
            window = gap.get('window', {})
            basic = evidence.get('basics', {}).get(code, {})
            ipo = basic.get('ipoDate', '')
            calendar = evidence.get('calendar', {})
            # A short response is not automatically a new listing.
            if (tf == 'daily' and ipo == window.get('start') and ipo <= day and
                    calendar.get('start', day) <= ipo and calendar.get('end', '') >= day and
                    sum(ipo <= d <= day for d in calendar.get('trading_days', [])) == window.get('rows')):
                reason = f"新股：{ipo}上市，日K {window['rows']}/125根；无需补拉"
                proof = evidence.get('basics_path', '')
            else:
                unit = '日K' if tf == 'daily' else '周K'
                reason = f"{unit} {window.get('rows', '?')}/125根；需核对上市时间或历史范围"
        # Stable issue identity: another day/period/changed hole cannot inherit an ignore.
        key = digest(dumps(dict(source=source, day=day, timeframe=tf, code=code,
                                category=category, group=group, dates=gap.get('dates'),
                                window=gap.get('window'), error=gap.get('error'), proof=proof)).encode())
        state = decisions.get(key, {}).get('state', 'pending')
        if state not in STATES:
            state = 'pending'
        can_repair = (code in (set(view.get('repair_codes', [])) | retry) if outside else code in repairable)
        can_repair = can_repair and group not in ('halt', 'short', 'identity', 'other')
        rows.append(dict(key=key, code=code, name=evidence.get('names', {}).get(code, ''),
                         group=group, state=state, blocked=blocked, repairable=can_repair,
                         unchanged=code in retry, reason=reason, proof=proof, raw=gap))

    for gap in scan.get('gaps', []):
        add(gap)
    existing = {r['code'] for r in rows}
    for gap in view.get('current_halts', []):
        if gap['code'] not in existing:
            add(dict(gap, category='current_halt'), blocked=False)
            existing.add(gap['code'])
    if include_history:
        for gap in view.get('outside_input_history', []):
            add(dict(gap, category='outside'), blocked=False, outside=True)
    groups = []
    for key, label in GROUPS.items():
        items = [r for r in rows if r['group'] == key]
        if items:
            states = {r['state'] for r in items}
            groups.append(dict(id=key, label=label, count=len(items),
                               pending=sum(r['blocked'] and r['state'] != 'ignored' for r in items),
                               ignored=sum(r['state'] == 'ignored' for r in items),
                               state=STATES[next(iter(states))] if len(states) == 1 else '分别处理', rows=items))
    ignored = sum(r['blocked'] and r['state'] == 'ignored' for r in rows)
    return dict(source=source, day=day, timeframe=tf, rows=rows, groups=groups,
                pending=len(scan.get('gaps', []))-ignored, ignored=ignored,
                ready=scan.get('ready', 0), excluded=len(scan.get('gaps', [])),
                scan_allowed=scan.get('scan_allowed', scan.get('complete', False)))


def pending_count(store, audit, timeframe):
    """Toolbar count uses the same issue identities as the category window."""
    source = audit.get('source')
    day = audit.get('expected_day')
    if source not in SOURCES or not day:
        return len(audit.get('gaps', []))
    view = dict(source=source, asof=day, timeframe=timeframe, scan=audit)
    return classify_review(view, review_evidence(store, day, source),
                           store.gap_review_decisions(source))['pending']


def review_summary(review):
    action = '可直接扫描已齐标的' if review['scan_allowed'] else '扫描前仍需通过原完整性检查'
    return (f"可扫描 {review['ready']} 只 · 待处理 {review['pending']} 只 · 已忽略 {review['ignored']} 只\n"
            + action + '；忽略仅移出待处理统计，缺口股票仍不参与扫描。')


def review_selection(review, selections):
    """Group keys and stock issue keys share one selection contract."""
    return [r for r in review['rows'] if r['group'] in selections or r['key'] in selections]


def repair_selection(review, selections):
    return sorted({r['code'] for r in review_selection(review, selections)
                   if r['repairable'] and r['state'] != 'ignored'})


def review_receipt(store, source, boards, day, timeframe):
    """One entry for all sources, without fetching or recertifying histories."""
    if source in FALLBACK_SOURCES:
        from .tickflow_integrity import integrity_view
        records = store.rows('SELECT value FROM meta WHERE key=?', (source+'_integrity',))
        if not records:
            raise ValueError('请先更新所选来源行情，生成分类回执')
        report = json.loads(records[0]['value'])
        if 'unchanged_repair_codes' not in report:
            from .market import latest_datasets
            from .repair_outcomes import unchanged_repairs
            latest = {r['code']: r for r in latest_datasets(store, source)}
            report['unchanged_repair_codes'] = unchanged_repairs(store, source, day, latest)
        return integrity_view(report, timeframe, boards, day)
    from .readiness import audit_scope
    audit = audit_scope(store, boards, day, source=source, timeframe=timeframe, history_cache_only=True)
    repair = [g['code'] for g in audit['gaps'] if g['code'] != '范围核验']
    own = csv_rows(directory_file(store, source))
    halts = [dict(code=code, evidence=[]) for code, row in own.items()
             if row.get('tradeStatus') == '0'] if audit['scope_valid'] else []
    from .market import select_board_codes
    import pandas as pd
    selected = set(select_board_codes(pd.DataFrame(list(own.values())), boards)) if own else set()
    return dict(source=source, asof=day, timeframe=timeframe, boards=boards, scan=audit,
                excluded=audit['gaps'], repair_codes=repair, scan_repair_codes=repair,
                retry_codes=[], current_halts=[g for g in halts if g['code'] in selected],
                outside_input_history=[], unknown_history=[], confirmed_history=[], request_failures=[],
                periods={timeframe: audit})
