"""Continue certified same-source work; never turn missing bars into halts."""
import json
import time

from .market import completed_date, latest_datasets
from .readiness import audit_scope, expected_day
from .repair_outcomes import before_repair, remember_repairs, unchanged_repairs
from .sources import (SOURCES, FALLBACK_SOURCES, directory_date, directory_file, calendar_file,
                      source_file)
from .store import digest, dumps
from .provider_guard import ProviderGuard, ProviderError, error_detail


PRICE_GAPS = {'input_gap', 'stale_tail', 'missing_dataset'}


def certified_scope(store, source, asof):
    """A dated directory plus its original write hashes, not mere file presence.

    An interrupted sync may already have safely saved its membership. Reuse
    those committed facts, but recheck every price file/scan gate independently.
    No receipt saying 'completed' can excuse changed/missing scope bytes.
    """
    try:
        day = expected_day(store, asof, source)
        if directory_date(store, source) != day:
            return False
        own = source_file(store, 'universe.csv', source)
        if directory_file(store, source) != own:
            return False  # Legacy inventory migration needs normal preflight.
        dated = source_file(store, f'directories/{day}.csv', source)
        paths = [own, dated, calendar_file(store, source)]
        if source == 'baostock':
            paths.append(store.root/f'directories/{day}-basics.csv')
        if own.read_bytes() != dated.read_bytes():
            return False
        for path in paths:
            if path.is_symlink():
                return False
            path = path.resolve()
            if not path.is_relative_to(store.root.resolve()) or path.is_symlink():
                return False
            writes = store.rows("SELECT e.detail,j.kind,j.spec FROM events e LEFT JOIN jobs j ON j.id=e.job_id "
                                "WHERE e.path=? AND e.action='写入文件' ORDER BY e.seq DESC LIMIT 1",
                                (str(path),))
            if not writes or json.loads(writes[0]['detail']).get('sha256') != digest(path.read_bytes()):
                return False
            if path in (own.resolve(), dated.resolve()):
                spec = json.loads(writes[0]['spec'] or '{}')
                if writes[0]['kind'] != 'sync' or spec.get('source', 'baostock') != source:
                    return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def price_repair_codes(store, source, audit):
    if not audit.get('scope_valid'):
        return [], []
    from .gap_review import IDENTITIES
    day = audit['expected_day']
    records = {r['code']: r for r in latest_datasets(store, source)}
    unchanged = set(unchanged_repairs(store, source, day, records))
    candidates = {g['code'] for g in audit.get('gaps', []) if g.get('category') in PRICE_GAPS
                  and not (g['code'] in IDENTITIES and day >= IDENTITIES[g['code']][0])}
    # Weekly readiness may precede today's latest daily tail. Updating prices
    # must still bring active eligible stocks to the target day, not silently
    # treat a Friday weekly scan as a complete midweek daily update.
    eligible = set(audit.get('eligible_ids', []))
    from .sync import tail_pending
    candidates.update(code for code,r in records.items() if r['id'] in eligible and r['end'] < day
                      and tail_pending(store,code,r['end'],day,source))
    return sorted(candidates - unchanged), sorted(candidates & unchanged)


def continue_sync(service, job, spec):
    """One bounded repair pass, then let the normal scan verify qualifications.

    Covered local work uses no vendor connection. Uncertified/new-day scope
    still goes through original official preflight. Network failures/limits are
    never retried within this invocation, nor is historical research expanded.
    """
    store, source = service.store, spec.get('source', 'baostock')
    asof = min(spec.get('end') or completed_date(), completed_date())
    tf = spec.get('scan_timeframe', 'daily')
    raw = dict(spec, resume=False)
    if spec.get('force') or spec.get('full_history') or spec.get('repair_codes') or 'boards' not in spec:
        return service._sync_once(job, raw)
    service.check_stop(job)
    warm = certified_scope(store, source, asof)
    if warm:
        service.progress(job, 0, 0, f'{SOURCES[source]} · 检查已完成行情，只续做未齐部分')
        audit = audit_scope(store, spec['boards'], asof, source=source, timeframe=tf,
                            verify_files=True, check_stop=lambda: service.check_stop(job),
                            progress=lambda done,total,code: service.progress(
                                job,done,total,f'核对已保存行情 {done}/{total} · {code}；不是重新下载'))
        result = dict(source=source, requested=audit['expected'], success=audit['ready']+audit['suspended'],
                      skipped=audit['ready'], suspended=audit['suspended'], errors=audit['gaps'],
                      datasets=audit['eligible_ids'], end_requested=audit['expected_day'],
                      boards=spec['boards'], scan_readiness=audit, scope_reused=True, connections=0)
        if source in FALLBACK_SOURCES:
            # A cached old receipt may describe another board/period. Refresh
            # display facts without a vendor request or a duplicate file audit.
            from .tickflow_integrity import integrity_report
            integrity = integrity_report(store,spec['boards'],audit['expected_day'],timeframe=tf,
                source=source,period_audits={tf:audit},check_stop=lambda:service.check_stop(job))
            result['integrity'] = integrity
            result['scan_periods'] = integrity['periods']
            store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                          (source+'_integrity',dumps(integrity)))
    else:
        result = service._sync_once(job, raw)
        audit = result.get('scan_readiness')
        if not audit or not audit.get('scope_valid'):
            return result
    service.check_stop(job)
    codes, unchanged = price_repair_codes(store, source, audit)
    # Failed transport already made its one attempt. No second connection after
    # a restriction/timeout, even if local gaps still suggest missing prices.
    failed = {e.get('code') for e in result.get('errors', []) if e.get('category') not in PRICE_GAPS}
    if result.get('cooldown') or result.get('remaining', 0) or result.get('network_failures', 0):
        codes = []
    elif not warm:
        codes = [c for c in codes if c not in failed]
        # Tail/missing-dataset were just requested by ordinary sync. Only an
        # interior input hole requires a *different* interval after that stage.
        interior = {g['code'] for g in audit.get('gaps', []) if g.get('category') == 'input_gap'}
        codes = [c for c in codes if c in interior]
    result['resume'] = dict(reused=audit['ready'], repair_codes=codes, unchanged=unchanged,
                          scope_reused=warm)
    guard = ProviderGuard(store,source)
    state = guard.state()
    if codes and state.get('until',0) > time.time():
        result['resume']['waiting_for_cooldown'] = True
        result['cooldown'] = state
        result['stop_reason'] = '供应商访问冷却：保留已完成行情，只扫描本地已核验的合格标的'
        codes = []
    if codes:
        before = before_repair(store, source, codes)
        result['resume']['repair_attempted'] = True
        service.progress(job, 0, len(codes), f'{SOURCES[source]} · 自动补拉 {len(codes)} 只，随后继续扫描')
        try:
            repair = service._sync_once(job, dict(raw, repair_codes=codes, repair_scope='scan',
                                                  resume_scope=True, end=audit['expected_day'], force=False))
        except (ProviderError,TimeoutError) as exc:
            state = getattr(exc,'_provider_guard_state',None) or guard.failure(exc)
            result['cooldown'] = state
            result['errors'] = list(result['errors']) + [error_detail(exc,source)]
            result['stop_reason'] = '补拉未完成：已保存行情保留，没有自动重试'
            return result
        remember_repairs(store, source, audit['expected_day'], before, repair.get('datasets', []))
        repair['resume'] = result['resume']
        result = repair
    elif warm:
        result['note'] = ('已完成行情直接复用；' +
                          ('其余问题保留，已补拉无改善的缺口不重复请求' if audit['gaps'] else '无需下载'))
    return result
