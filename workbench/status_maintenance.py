"""Small daily status-only batches; never infer a halt or replace price data."""
import copy
import json
import threading
import time

from .history_quality import calendar_of, quality_for, save_quality
from .market import BaoStock, latest_datasets
from .provider_guard import ProviderError, ProviderGuard, baostock_connection_active
from .readiness import audit_scope
from .sources import SOURCES
from .status_review import verify_status_targets
from .store import dumps
from .trading_status import refresh_status_index, status_facts, halt_evidence

MAX_STOCKS = 5
NETWORK_BUDGET = 20
REQUEST_TIMEOUT = 5
RETRY_AFTER = 24 * 3600
ATTEMPT_PREFIX = 'status_maintenance:attempt:'
BATCH_KEY = 'status_maintenance:batch'
_RUN_LOCK = threading.Lock()


def select_targets(store, periods, records, source, day, *, stamp=None):
    """New daily holes first; reserve a slot for rotating weekly history debt."""
    stamp = time.time() if stamp is None else stamp
    refresh_status_index(store)
    attempts = {r['key'][len(ATTEMPT_PREFIX):]:json.loads(r['value']) for r in store.rows(
        'SELECT key,value FROM meta WHERE key LIKE ?', (ATTEMPT_PREFIX+'%',))}
    calendar = calendar_of(store, source)
    candidates, primary = {}, set()
    for timeframe in ('daily', 'weekly'):
        audit = periods.get(timeframe, {})
        if not audit.get('scope_valid') or audit.get('expected_day') != day:
            continue
        for gap in audit['gaps']:
            code, kind = gap['code'], gap.get('category')
            if code not in records or kind not in ('input_gap', 'stale_tail'):
                continue
            if kind == 'input_gap':
                dates = gap['dates']
            else:
                dates = [d for d in calendar['trading_days'] if records[code]['end'] < d <= day]
            candidates.setdefault(code, set()).update(d for d in dates if d <= day)
            if timeframe == 'daily':
                primary.add(code)
    pending = {}
    for code, dates in candidates.items():
        if not dates:
            continue
        known, _ = status_facts(store, code)
        halted, _ = halt_evidence(store, code, min(dates), max(dates))
        needed = dates - set(known) - set(halted)
        if not needed:
            continue  # Explicit trading days still need same-source prices.
        attempt = attempts.get(code, {})
        if (stamp < attempt.get('next_check', 0)
                and needed.issubset(attempt.get('dates', []))):
            continue
        pending[code] = sorted(needed)
    def rank(code):
        return (day not in pending[code], attempts.get(code, {}).get('checked', 0), code)
    daily = sorted(primary & pending.keys(), key=rank)
    history = sorted(pending.keys() - primary, key=rank)
    # Fairness: continuous new daily gaps must not starve old weekly holes.
    reserve = int(bool(history and daily) and MAX_STOCKS > 1)
    chosen = daily[:MAX_STOCKS - reserve]
    chosen += history[:MAX_STOCKS-len(chosen)]
    chosen += [c for c in daily if c not in chosen][:MAX_STOCKS-len(chosen)]
    return {c:pending[c] for c in chosen}, len(pending)


def _merge_audit(original, updated, records, codes):
    """Replace only checked stocks in a certified display receipt, not scan rules."""
    if not updated['scope_valid']:
        raise ValueError('局部停牌核验未完成，保留原扫描资格回执')
    ids = {records[c]['id'] for c in codes}
    old_ready = len(ids & set(original['eligible_ids']))
    old_gaps = {g['code'] for g in original['gaps'] if g['code'] in codes}
    old_suspended = len(codes) - old_ready - len(old_gaps)
    result = dict(original)
    result['ready'] += updated['ready'] - old_ready
    result['suspended'] += updated['suspended'] - old_suspended
    result['eligible_ids'] = [d for d in original['eligible_ids'] if d not in ids] + updated['eligible_ids']
    result['gaps'] = [g for g in original['gaps'] if g['code'] not in codes] + updated['gaps']
    result['warnings'] = [g for g in original.get('warnings', []) if g['code'] not in codes] + updated['warnings']
    result['complete'] = not result['gaps'] and result['expected'] > 0
    result['scan_allowed'] = result['scope_valid'] and (result['ready'] > 0 or result['complete'])
    return result


def _refresh(service, job, spec, result, periods, records, changed):
    """Recheck at most five price snapshots, not another whole-market audit."""
    store, source, day = service.store, spec['source'], periods['daily']['expected_day']
    before = {g['code']:g for g in periods['daily']['gaps']}
    periods = {tf:_merge_audit(audit, audit_scope(store, spec['boards'], day,
        source=source, timeframe=tf, verify_files=True, codes=changed,
        check_stop=lambda:service.check_stop(job)), records, changed)
        for tf,audit in periods.items()}
    result['scan_readiness'] = periods['daily']
    result['scan_periods'] = periods
    result['errors'] = [e for e in result.get('errors', []) if not (
        e.get('code') in changed and e.get('code') not in {g['code'] for g in periods['daily']['gaps']}
        and (e.get('category') in ('input_gap', 'stale_tail')
             or e.get('error') == before.get(e.get('code'), {}).get('error')))]
    qualities = {}
    for code in changed:
        q = quality_for(store, records[code], day)
        save_quality(store, records[code]['id'], q)
        qualities[code] = q
    if result.get('integrity'):
        from .tickflow_integrity import integrity_view
        receipt = copy.deepcopy(result['integrity'])
        receipt['periods'] = periods
        for field in ('unknown_history', 'evidence_pending', 'confirmed_history', 'current_halts'):
            receipt[field] = [g for g in receipt.get(field, []) if g['code'] not in changed]
        for code,q in qualities.items():
            if q['missing_dates']:
                item = dict(code=code,dataset=records[code]['id'],dates=q['missing_dates'],windows=q['windows'])
                receipt['unknown_history'].append(item)
                receipt['evidence_pending'].append(item)
            if q['suspended_dates']:
                receipt['confirmed_history'].append(dict(code=code,dates=q['suspended_dates'],evidence=q['evidence']))
            dates,proof = halt_evidence(store,code,day,day)
            if dates:
                receipt['current_halts'].append(dict(code=code,evidence=proof))
        unresolved = {g['code'] for a in periods.values() for g in a['gaps']}
        unresolved.update(g['code'] for g in receipt['unknown_history'])
        for field in ('repair_codes','unchanged_repair_codes'):
            receipt[field] = [c for c in receipt.get(field, []) if c not in changed or c in unresolved]
        receipt = integrity_view(receipt, 'daily')
        receipt['request_failures'] = result['errors']
        result['integrity'] = receipt
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (source+'_integrity', dumps(receipt)))
        store.write_artifact(f'reports/{job}-{source}-integrity.json', dumps(receipt).encode(), job)
    # Coverage-only receipt remains diagnostic; never replace it with period data.
    if not result['errors'] and periods['daily']['complete']:
        result['stop_reason'] = '行情已就绪；缺日状态已核验，停牌日不补造K线'


def maintain_daily_status(service, job, spec, result):
    """Optional post-price stage. Network/unknowns do not fail a usable sync."""
    source = spec.get('source', 'baostock')
    if (source not in SOURCES or spec.get('scan_timeframe') != 'daily'
            or not spec.get('boards') or any(spec.get(k) for k in ('force','full_history','repair_codes'))):
        return result
    audit = result.get('scan_readiness', {})
    if not audit.get('scope_valid'):
        return result
    periods = result.get('scan_periods') or {'daily':audit}
    if not periods.get('daily', {}).get('scope_valid'):
        return result
    if not _RUN_LOCK.acquire(blocking=False):
        return result
    try:
        service.check_stop(job)
        parent = spec.get('premarket_parent') or job
        rows = service.store.rows('SELECT value FROM meta WHERE key=?', (BATCH_KEY,))
        if rows and json.loads(rows[0]['value']).get('job') == parent:
            return result  # One budget across parallel vendors and boundary resyncs.
        if (ProviderGuard(service.store, 'baostock').state().get('until',0) > time.time()
                or baostock_connection_active()):
            return result  # Do not compete with another source's price connection.
        records = {r['code']:r for r in latest_datasets(service.store, source) if r['timeframe']=='daily'}
        selection = dict(periods)
        if 'weekly' not in selection:
            # Warm resume may reuse only the daily audit. Its integrity receipt
            # still holds certified real weekly windows; do not re-audit every
            # price file just to find a bounded historical-status candidate.
            gaps = []
            for item in result.get('integrity', {}).get('unknown_history', []):
                window = item.get('windows', {}).get('weekly', {})
                if window.get('rows', 0) < 125:
                    continue
                dates = [d for d in item['dates'] if window['start'] <= d <= window['end']]
                if dates:
                    gaps.append(dict(code=item['code'],category='input_gap',dates=dates))
            selection['weekly'] = dict(scope_valid=True,expected_day=audit['expected_day'],gaps=gaps)
        targets,pending = select_targets(service.store,selection,records,source,audit['expected_day'])
        if not targets:
            return result
        service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (BATCH_KEY,dumps(dict(job=parent,source=source,checked=time.time()))))
        def attempted(code, dates):
            stamp = time.time()
            service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (ATTEMPT_PREFIX+code,dumps(dict(checked=stamp,next_check=stamp+RETRY_AFTER,dates=dates))))
        report = verify_status_targets(service.store,targets,BaoStock,job,retry=True,
            check_stop=lambda:service.check_stop(job), cancel_event=service.cancel_flags[job],
            progress=lambda n,total,msg:service.progress(job,n,total,'自动'+msg),
            budget_seconds=NETWORK_BUDGET, request_timeout=REQUEST_TIMEOUT,
            min_interval=1, refresh_index=False, on_attempt=attempted,preserve_order=True)
        changed = {r['code'] for field in ('confirmed_halt','traded') for r in report[field]}
        if changed:
            _refresh(service,job,dict(spec,source=source),result,periods,records,changed)
        summary = dict(checked=report['queried'], halted=len({r['code'] for r in report['confirmed_halt']}),
            unknown=len({r['code'] for r in report['unknown']}), deferred=max(0,pending-report['queried']),
            errors=len(report['errors']), budget_exhausted=report.get('budget_exhausted',False))
        result['status_maintenance'] = report
        result['status_maintenance_summary'] = summary
        service.store.event(job,'日常小批缺日核验',source=source,asof=audit['expected_day'],**summary)
    except InterruptedError:
        raise
    except (ProviderError, TimeoutError, OSError, ValueError, KeyError) as exc:
        # Corrupt evidence still fails the actual scan's unchanged file gate.
        result['status_maintenance_summary'] = dict(errors=1,note=str(exc))
        service.store.event(job,'日常缺日核验未完成，保留原资格',source=source,error=str(exc))
    finally:
        _RUN_LOCK.release()
    return result
