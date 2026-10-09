"""Explicit post-sync quality and repair queue, separate from provider transport."""
from .market import latest_datasets, select_board_codes
from .readiness import audit_scope
from .sources import directory_file
from .history_quality import quality_for, calendar_of

import pandas as pd


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
    candidates = {g['code'] for g in audit['gaps']} - immature
    halts = {g['code'] for g in report.get('current_halts', [])}
    result['scan_repair_codes'] = sorted(candidates - halts - {'范围核验'})
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
    for category in ('insufficient_bars', 'input_gap', 'missing_dataset', 'stale_tail', 'invalid_file', 'quality_pending'):
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
    result['repair_codes'] = [c for c in result['repair_codes'] if c not in immature]
    blocked = {g['code'] for g in result['excluded']}
    result['scan_repair_codes'] = [c for c in result['repair_codes'] if c in blocked]
    return integrity_view(result, timeframe)
