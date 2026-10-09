"""Explicit post-sync quality and repair queue, separate from provider transport."""
from .market import latest_datasets, select_board_codes
from .readiness import audit_scope
from .sources import directory_file
from .history_quality import quality_for, calendar_of

import pandas as pd


def integrity_summary(view):
    """Action first; gap count is stocks blocked for this scan, not today's holes."""
    scan=view['scan']; gaps=scan.get('gaps',[])
    counts={category:sum(g.get('category')==category for g in gaps)
            for category in ('input_gap','stale_tail','missing_dataset','insufficient_bars','directory_pending')}
    first=(f"截至 {view['asof']}：可直接扫描 {scan['ready']} 只；暂不扫描 {len(gaps)} 只；"
           f"已确认停牌 {scan['suspended']} 只。")
    reasons=[]
    for category,label in [('input_gap','扫描所需历史缺日'),('stale_tail','最新行情落后'),
                           ('missing_dataset','缺整份行情'),('insufficient_bars','历史根数不足'),
                           ('directory_pending','证券身份待核对')]:
        if counts[category]:reasons.append(f'{label} {counts[category]} 只')
    other=len(gaps)-sum(counts.values())
    if other:reasons.append(f'文件／核验问题 {other} 只')
    lines=[first]
    if reasons:lines.append('暂不扫描原因：'+'；'.join(reasons)+'。不是都缺当天行情。')
    if scan.get('scan_allowed',scan.get('complete',False)):
        lines.append('建议：直接扫描已齐标的，不必等全部缺口补齐。')
    else:
        lines.append('暂不能扫描：请先更新行情或处理下方核验问题。')
    retry=len(view.get('retry_codes',[])); repair=len(view.get('scan_repair_codes',[]))
    if gaps:
        lines.append(f'可补拉 {repair} 只；已补拉无改善 {retry} 只（需查证，重复下载不保证补齐）。')
    return '\n'.join(lines)


def integrity_view(report, timeframe, boards=None, asof=None):
    """Select an already certified period without parsing prices on the UI thread."""
    if boards is not None and set(report.get('boards', [])) != set(boards):
        raise ValueError('完整性回执对应其他板块；请先更新当前范围')
    if asof is not None and report.get('asof') != asof:
        raise ValueError('完整性回执日期已过期；请先更新行情再补拉')
    periods = report.get('periods', {})
    if timeframe not in periods:
        # Read-only compatibility with old one-period receipts.
        if report.get('timeframe') == timeframe:
            return report
        raise ValueError('旧回执尚未核验此周期；请更新一次所选来源行情')
    audit = periods[timeframe]
    result = dict(report, timeframe=timeframe, scan=audit, excluded=audit['gaps'])
    immature = {g['code'] for g in audit['gaps'] if g.get('category') == 'insufficient_bars'}
    directory_pending={g['code'] for g in audit['gaps'] if g.get('category')=='directory_pending'}
    candidates = {g['code'] for g in audit['gaps']} - immature - directory_pending
    halts = {g['code'] for g in report.get('current_halts', [])}
    result['retry_codes'] = sorted(candidates & set(report.get('unchanged_repair_codes',[])))
    result['scan_repair_codes'] = sorted(candidates - halts - {'范围核验'} - set(result['retry_codes']))
    result['repair_codes'] = sorted(set(report['repair_codes']) - set(report.get('unchanged_repair_codes',[])))
    result['input_gaps'] = []
    result['outside_input_history'] = []
    for item in report['unknown_history']:
        window = item.get('windows', {}).get(timeframe)
        if window is None:
            continue
        inside = [d for d in item['dates'] if window['start'] <= d <= window['end']]
        outside = [d for d in item['dates'] if d < window['start'] or d > window['end']]
        if inside:
            result['input_gaps'].append(dict(code=item['code'], dates=inside, window=window))
        if outside:
            result['outside_input_history'].append(dict(code=item['code'], dates=outside))
    result['counts'] = dict(expected=audit['expected'], ready=audit['ready'],
                           suspended=audit['suspended'], excluded=len(audit['gaps']),
                           input_gap_stocks=len(result['input_gaps']),
                           outside_input_stocks=len(result['outside_input_history']))
    for category in ('insufficient_bars', 'input_gap', 'missing_dataset', 'stale_tail', 'invalid_file', 'quality_pending', 'directory_pending'):
        result['counts'][category] = sum(g.get('category') == category for g in audit['gaps'])
    return result


def integrity_report(store, boards, asof, errors=(), timeframe='daily', source='tickflow'):
    if timeframe not in ('daily', 'weekly'):
        raise ValueError('请选择日线或周线完整性核验')
    # Both periods are checked once in the background. Changing the display
    # period never forces another market download or weakens the weekly gate.
    periods = {tf:audit_scope(store, boards, asof, source=source, timeframe=tf, verify_files=True)
               for tf in ('daily', 'weekly')}
    audit = periods[timeframe]
    result = dict(source=source, asof=asof, timeframe=timeframe, scan=audit,
                  unknown_history=[], confirmed_history=[], repair_codes=[], scan_repair_codes=[], boards=boards,
                  excluded=[], request_failures=list(errors), evidence_pending=[], current_halts=[], periods=periods)
    if not audit['scope_valid']:
        result['excluded'] = audit['gaps']
        return integrity_view(result, timeframe)
    codes = set(select_board_codes(pd.read_csv(directory_file(store,source),dtype=str),boards))
    records = {r['code']:r for r in latest_datasets(store,source) if r['code'] in codes}
    from .repair_outcomes import unchanged_repairs
    result['unchanged_repair_codes']=unchanged_repairs(store,source,asof,records)
    calendar = calendar_of(store,source)
    from .suspensions import announcement_evidence
    for code in sorted(codes):
        days,proof=announcement_evidence(code,asof,asof)
        if days:
            result['current_halts'].append(dict(code=code,evidence=proof))
    for code, record in records.items():
        try:
            q = quality_for(store,record,asof,calendar)
            if q['missing_dates']:
                item = dict(code=code, dataset=record['id'], dates=q['missing_dates'], windows=q['windows'])
                result['unknown_history'].append(item)
                result['evidence_pending'].append(item)
            confirmed = sorted(set(q['suspended_dates']) & set(calendar['trading_days']))
            if confirmed:
                result['confirmed_history'].append(dict(code=code,dates=confirmed,evidence=q['evidence']))
        except (OSError,ValueError,KeyError) as exc:
            # A broken local file is a repair issue, never an evidence waiver.
            result['excluded'].append(dict(code=code,error=str(exc)))
    result['excluded'].extend(audit['gaps'])
    candidates = {g['code'] for g in result['excluded'] if g['code'] in codes}
    candidates.update(g['code'] for g in result['unknown_history'])
    candidates.update(g.get('code') for g in errors if g.get('code') in codes)
    result['repair_codes'] = sorted(c for c in candidates if asof not in announcement_evidence(c,asof,asof)[0])
    # Too few bars is not a missing-price problem; redownloading a new listing
    # cannot manufacture the 125 bars required by the unchanged strategy engine.
    immature = {g['code'] for g in audit['gaps'] if g.get('category') == 'insufficient_bars'}
    directory_pending={g['code'] for g in audit['gaps'] if g.get('category')=='directory_pending'}
    result['repair_codes'] = [c for c in result['repair_codes'] if c not in immature | directory_pending]
    # An unchanged download is not a pending repair once new evidence closes
    # its hole; keep the marker only for still unresolved price issues.
    unresolved=set(result['repair_codes']) | {g['code'] for p in periods.values() for g in p['gaps']
                                           if g.get('category') != 'insufficient_bars'}
    result['unchanged_repair_codes']=[c for c in result['unchanged_repair_codes'] if c in unresolved]
    blocked = {g['code'] for g in result['excluded']}
    result['scan_repair_codes'] = [c for c in result['repair_codes'] if c in blocked]
    return integrity_view(result, timeframe)
