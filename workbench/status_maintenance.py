"""Daily missing-date closure; explicit evidence first, same-source prices only."""
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
from .store import dumps, digest
from .trading_status import refresh_status_index, status_facts, halt_evidence

REQUEST_TIMEOUT = 5
RETRY_AFTER = 24 * 3600
ATTEMPT_PREFIX = 'status_maintenance:attempt:'
BATCH_KEY = 'status_maintenance:batch'
_RUN_LOCK = threading.Lock()


def select_targets(store, periods, records, source, day, *, stamp=None, unknown_only=True):
    """All current daily/weekly input holes, never the entire historical archive."""
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
            from .gap_review import IDENTITIES
            if (code in IDENTITIES and day >= IDENTITIES[code][0]) or not code.startswith(('sh.', 'sz.')):
                continue  # Known invalid/old identities are not missing prices.
            if kind not in ('input_gap', 'stale_tail', 'missing_dataset'):
                continue
            if kind == 'input_gap':
                dates = gap['dates']
            elif code not in records or kind == 'missing_dataset':
                dates = [day]  # No history to certify; only today's actual status.
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
        needed = dates - set(halted) - ({d for d,v in known.items() if v=='0'})
        if unknown_only:
            needed -= set(known)
        if not needed:
            continue  # Explicit trading days still need same-source prices.
        attempt = attempts.get(code, {})
        if (unknown_only and stamp < attempt.get('next_check', 0)
                and needed.issubset(attempt.get('dates', []))):
            continue
        pending[code] = sorted(needed)
    def rank(code):
        return (day not in pending[code], attempts.get(code, {}).get('checked', 0), code)
    daily = sorted(primary & pending.keys(), key=rank)
    history = sorted(pending.keys() - primary, key=rank)
    chosen = daily + history
    return {c:pending[c] for c in chosen}, len(pending)


def _merge_audit(original, updated, records, codes):
    """Replace only checked stocks in a certified display receipt, not scan rules."""
    if not updated['scope_valid']:
        raise ValueError('局部停牌核验未完成，保留原扫描资格回执')
    ids = {records[c]['id'] for c in codes if c in records}
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
    """Recheck affected stocks once per period, not once per vendor response."""
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
    current = {r['code']:r for r in latest_datasets(store,source) if r['timeframe']=='daily'}
    for code in changed:
        if code not in current:
            continue
        q = quality_for(store, current[code], day)
        save_quality(store, current[code]['id'], q)
        qualities[code] = q
    if result.get('integrity'):
        from .tickflow_integrity import integrity_view
        receipt = copy.deepcopy(result['integrity'])
        receipt['periods'] = periods
        for field in ('unknown_history', 'evidence_pending', 'confirmed_history', 'current_halts'):
            receipt[field] = [g for g in receipt.get(field, []) if g['code'] not in changed]
        for code,q in qualities.items():
            if q['missing_dates']:
                item = dict(code=code,dataset=current[code]['id'],dates=q['missing_dates'],windows=q['windows'])
                receipt['unknown_history'].append(item)
                receipt['evidence_pending'].append(item)
            if q['suspended_dates']:
                receipt['confirmed_history'].append(dict(code=code,dates=q['suspended_dates'],evidence=q['evidence']))
        for code in changed:
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


def _repair_traded(service, job, spec, result, periods, records, selection):
    """One same-source pass for explicit trading dates, including weekly holes.

    Persist completed attempts by source/date/snapshot/positive evidence. A
    restart reuses facts, but a killed, uncompleted request remains retryable.
    """
    source, store = spec['source'], service.store
    day = periods['daily']['expected_day']
    if (result.get('cooldown') or result.get('remaining',0) or result.get('network_failures',0)
            or ProviderGuard(store,source).state().get('until',0)>time.time()):
        return
    targets, _ = select_targets(store,selection,records,source,day,unknown_only=False)
    failed = {e.get('code') for e in result.get('errors',[])
              if e.get('category') not in ('input_gap','stale_tail','missing_dataset')}
    attempts = {}
    for code, dates in targets.items():
        if code in failed:
            continue  # An ordinary transport failure is not another price attempt.
        known, _ = status_facts(store,code)
        traded = sorted(d for d in dates if known.get(d)=='1')
        if not traded:
            continue  # An empty reply never authorizes guessing a halt or trading day.
        fingerprint = digest(dumps(dict(day=day,dataset=records.get(code,{}).get('id'),dates=traded)).encode())
        key = 'status_maintenance:price:'+source+':'+code
        previous = store.rows('SELECT value FROM meta WHERE key=?',(key,))
        if previous and json.loads(previous[0]['value']).get('fingerprint')==fingerprint:
            continue
        attempts[code] = (key,fingerprint)
    if not attempts:
        return
    from .repair_outcomes import before_repair, remember_repairs
    codes = sorted(attempts)
    repaired, failures = set(), []
    # Completed weeks do not include this week's forming daily candles. Try
    # weekly holes first, then only daily holes still needing actual prices.
    for timeframe in ('weekly','daily'):
        if timeframe not in selection:
            continue
        if result.get('cooldown') or result.get('remaining',0) or result.get('network_failures',0):
            break
        candidates,_ = select_targets(store,{timeframe:selection[timeframe]},records,source,day,unknown_only=False)
        wanted = set(candidates) & set(attempts)
        if timeframe=='daily':
            wanted &= {g['code'] for g in result['scan_readiness']['gaps']}
        wanted = sorted(c for c in wanted if any(status_facts(store,c)[0].get(d)=='1' for d in candidates[c]))
        if not wanted:
            continue
        before = before_repair(store,source,wanted)
        request = dict(spec,resume=False,repair_codes=wanted,repair_scope='scan',
                       scan_timeframe=timeframe,resume_scope=True,end=day,force=False)
        service.check_stop(job)
        try:
            repair = service._sync_once(job,request)
        except (ProviderError,TimeoutError) as exc:
            from .provider_guard import error_detail
            state = getattr(exc,'_provider_guard_state',None) or ProviderGuard(store,source).failure(exc)
            result['cooldown'] = state
            result['errors'] = list(result.get('errors',[])) + [error_detail(exc,source)]
            break
        remember_repairs(store,source,day,before,repair.get('datasets',[]))
        repaired.update(wanted)
        failures.extend(repair.get('errors',[]))
        for field in ('cooldown','remaining','network_failures','transport_recovery'):
            if repair.get(field): result[field] = repair[field]
        _refresh(service,job,spec,result,periods,records,set(wanted))
        periods = result['scan_periods']
        records = {r['code']:r for r in latest_datasets(store,source) if r['timeframe']=='daily'}
        result['datasets'] = sorted(set(result.get('datasets',[])) | set(repair.get('datasets',[])))
    for code in repaired:
        key,fingerprint = attempts[code]
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (key,dumps(dict(fingerprint=fingerprint,checked=time.time()))))
    result['status_price_repair'] = dict(codes=sorted(repaired),errors=failures)
    daily_gaps = {g['code'] for g in result['scan_readiness']['gaps']}
    result['errors'] += [e for e in failures if e not in result['errors']
                        and (e.get('code') in daily_gaps or not e.get('code'))]


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
        batch_key = BATCH_KEY+':'+source
        rows = service.store.rows('SELECT value FROM meta WHERE key=?', (batch_key,))
        if rows and json.loads(rows[0]['value']).get('job') == parent:
            return result  # One pass per source; status evidence is shared across sources.
        records = {r['code']:r for r in latest_datasets(service.store, source) if r['timeframe']=='daily'}
        selection = dict(periods)
        if 'weekly' not in selection:
            if not result.get('integrity'):
                # BaoStock's warm daily receipt has no cross-period integrity
                # summary. Read cached quality for weekly selection too; actual
                # changed files and the eventual scan retain their own gates.
                weekly = audit_scope(service.store,spec['boards'],audit['expected_day'],
                    source=source,timeframe='weekly',check_stop=lambda:service.check_stop(job))
                if weekly['scope_valid']:
                    periods = dict(periods,weekly=weekly)
                    selection['weekly'] = weekly
            # Warm resume may reuse only the daily audit. Its integrity receipt
            # still holds certified real weekly windows; do not re-audit every
            # price file just to find historical-status candidates.
            gaps = []
            for item in result.get('integrity', {}).get('unknown_history', []):
                window = item.get('windows', {}).get('weekly', {})
                if window.get('rows', 0) < 125:
                    continue
                dates = [d for d in item['dates'] if window['start'] <= d <= window['end']]
                if dates:
                    gaps.append(dict(code=item['code'],category='input_gap',dates=dates))
            selection.setdefault('weekly',dict(scope_valid=True,expected_day=audit['expected_day'],gaps=gaps))
        targets,pending = select_targets(service.store,selection,records,source,audit['expected_day'])
        service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (batch_key,dumps(dict(job=parent,source=source,checked=time.time()))))
        def completed(code, dates, unknown):
            stamp = time.time()
            service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (ATTEMPT_PREFIX+code,dumps(dict(checked=stamp,next_check=stamp+RETRY_AFTER,dates=unknown))))
        available = False
        if targets and ProviderGuard(service.store,'baostock').state().get('until',0)<=time.time():
            try:
                available = not baostock_connection_active()
            except ProviderError as exc:
                service.store.event(job,'状态连接暂不可用，保留未知缺日',error=str(exc))
        report = dict(queried=0,reused=0,confirmed_halt=[],traded=[],unknown=[],errors=[],remaining=list(targets),price_downloads=0)
        if targets and available:
            report = verify_status_targets(service.store,targets,BaoStock,job,retry=True,
                check_stop=lambda:service.check_stop(job), cancel_event=getattr(service,'cancel_flags',{}).get(job),
                # Detailed phases belong to receipts, not the foreground status line.
                progress=lambda *args:None, request_timeout=REQUEST_TIMEOUT,
                min_interval=1, refresh_index=False, on_result=completed,preserve_order=True)
        changed = {r['code'] for field in ('confirmed_halt','traded') for r in report[field]}
        if changed:
            _refresh(service,job,dict(spec,source=source),result,periods,records,changed)
            periods = result['scan_periods']
        _repair_traded(service,job,dict(spec,source=source),result,periods,records,selection)
        summary = dict(checked=report['queried'], halted=len({r['code'] for r in report['confirmed_halt']}),
            unknown=len({r['code'] for r in report['unknown']}), deferred=max(0,pending-report['queried']),
            errors=len(report['errors']), budget_exhausted=report.get('budget_exhausted',False))
        result['status_maintenance'] = report
        result['status_maintenance_summary'] = summary
        service.store.event(job,'日常缺口自动处理',source=source,asof=audit['expected_day'],**summary)
    except InterruptedError:
        raise
    except (ProviderError, TimeoutError, OSError, ValueError, KeyError) as exc:
        # Corrupt evidence still fails the actual scan's unchanged file gate.
        result['status_maintenance_summary'] = dict(errors=1,note=str(exc))
        service.store.event(job,'日常缺日核验未完成，保留原资格',source=source,error=str(exc))
    finally:
        _RUN_LOCK.release()
    return result
