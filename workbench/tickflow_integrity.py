"""Explicit post-sync quality and repair queue, separate from provider transport."""
from .market import latest_datasets, select_board_codes
from .readiness import audit_scope
from .sources import directory_file
from .history_quality import quality_for, calendar_of

import pandas as pd


def integrity_report(store, boards, asof, errors=(), timeframe='daily'):
    audit = audit_scope(store, boards, asof, source='tickflow', timeframe=timeframe)
    result = dict(source='tickflow', asof=asof, timeframe=timeframe, scan=audit,
                  unknown_history=[], confirmed_history=[], repair_codes=[], scan_repair_codes=[], boards=boards,
                  excluded=[], request_failures=list(errors), evidence_pending=[], current_halts=[])
    if not audit['scope_valid']:
        result['excluded'] = audit['gaps']
        return result
    codes = set(select_board_codes(pd.read_csv(directory_file(store,'tickflow'),dtype=str),boards))
    records = {r['code']:r for r in latest_datasets(store,'tickflow') if r['code'] in codes}
    calendar = calendar_of(store)
    from .suspensions import announcement_evidence
    for code in sorted(codes):
        days,proof=announcement_evidence(code,asof,asof)
        if days:
            result['current_halts'].append(dict(code=code,evidence=proof))
    for code, record in records.items():
        try:
            q = quality_for(store,record,asof,calendar)
            if q['missing_dates']:
                item = dict(code=code, dataset=record['id'], dates=q['missing_dates'])
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
    immature = {g['code'] for g in audit['gaps'] if '至少需要' in g['error']}
    result['repair_codes'] = [c for c in result['repair_codes'] if c not in immature]
    blocked = {g['code'] for g in result['excluded']}
    result['scan_repair_codes'] = [c for c in result['repair_codes'] if c in blocked]
    return result
