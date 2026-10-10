"""Bounded, optional status-only review; price sync never depends on this network."""
from .provider_guard import ProviderGuard, ProviderError, error_detail
from .store import dumps, now
from .trading_status import (refresh_status_index, status_facts, halt_evidence,
                             save_status_rows, validate_status_rows)


def verify_status_targets(store, targets, provider_factory, job=None, retry=False,
                          check_stop=lambda:None, progress=lambda *args:None,
                          cancel_event=None):
    refresh_status_index(store, verify_files=True)
    # Validate the entire specification before opening any vendor connection.
    targets = {code:sorted(set(days)) for code, days in targets.items() if days}
    for code, days in targets.items():
        for day in days:
            validate_status_rows(code, day, day, [])
    report = dict(source='baostock', price_downloads=0, queried=0, reused=0,
                  confirmed_halt=[], traded=[], unknown=[], errors=[])
    provider = None
    guard = ProviderGuard(store, 'baostock')
    try:
        for n, (code, dates) in enumerate(sorted(targets.items())):
            check_stop()
            progress(n, len(targets), f'核验缺日状态 · {code} · {dates[0]}～{dates[-1]}（不下载价格）')
            known, _ = status_facts(store, code)
            proven, _ = halt_evidence(store, code, dates[0], dates[-1])
            needed = [d for d in dates if d not in known and d not in proven]
            key = 'status_review_attempt:'+code
            previous = store.rows('SELECT value FROM meta WHERE key=?', (key,))
            import json
            tried = set(json.loads(previous[0]['value']).get('unknown', [])) if previous else set()
            if not retry:
                needed = [d for d in needed if d not in tried]
            if needed:
                guard.check()
                if provider is None:
                    provider = provider_factory()
                    if cancel_event is not None:
                        provider.cancel_event = cancel_event
                    provider.on_wait = lambda op,seconds:progress(n,len(targets),f'核验缺日状态 · {code} · 等待 {seconds}秒')
                    provider.__enter__()
                try:
                    raw = provider.trading_status(code, needed[0], needed[-1])
                    rows = validate_status_rows(code, needed[0], needed[-1], raw.to_dict(orient='records'))
                    # Keep only explicitly requested dates; no prices or range inference.
                    rows = [r for r in rows if r['date'] in needed]
                    save_status_rows(store, code, needed[0], needed[-1], rows, job)
                    report['queried'] += 1
                    known, _ = status_facts(store, code)
                    unknown = sorted((tried | set(needed)) - set(known) - set(proven))
                    store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                  (key, dumps(dict(unknown=unknown, checked=now()))))
                except (ProviderError, TimeoutError) as exc:
                    guard.failure(exc)
                    report['errors'].append(dict(code=code, **error_detail(exc,'baostock','trading_status')))
                    report['stop_reason'] = '状态核验网络失败，已停止；保留已核验事实，不自动重试，也不阻塞其他行情源更新'
                    break
            else:
                report['reused'] += 1
            for day in dates:
                group = 'confirmed_halt' if day in proven or known.get(day)=='0' else 'traded' if known.get(day)=='1' else 'unknown'
                report[group].append(dict(code=code, date=day))
            store.event(job, '逐股缺日核验回执', code=code, dates=dates,
                        halted=[d for d in dates if d in proven or known.get(d)=='0'],
                        traded=[d for d in dates if known.get(d)=='1'],
                        unknown=[d for d in dates if d not in proven and d not in known])
        processed = {r['code'] for k in ('confirmed_halt','traded','unknown') for r in report[k]}
        progress(len(processed), len(targets), '缺日状态核验结束；明确停牌可复用，未知缺日仍待查证')
    except (ProviderError, TimeoutError) as exc:
        guard.failure(exc)
        report['errors'].append(error_detail(exc,'baostock','trading_status'))
        report['stop_reason'] = '状态连接失败；未放行未知缺日，可继续使用其他行情源'
    finally:
        if provider is not None:
            provider.__exit__(None, None, None)
    processed = {r['code'] for k in ('confirmed_halt','traded','unknown') for r in report[k]}
    report['remaining'] = sorted(set(targets)-processed)
    report['complete'] = not (report['unknown'] or report['errors'] or report['remaining'])
    if not report['complete'] and not report.get('stop_reason'):
        report['stop_reason'] = '部分缺日仍无明确状态；不认作停牌、不重复下载价格，保留待核验'
    return report


def review_job(service, job, spec):
    from .sources import SOURCES
    from .readiness import audit_scope
    from .history_quality import save_cached_quality
    from .market import BaoStock, completed_date
    source = spec['source']
    if source not in SOURCES or spec['timeframe'] not in ('daily','weekly'):
        raise ValueError('请选择正式行情来源及日／周线')
    day = min(spec['asof'], completed_date())
    audit = audit_scope(service.store, spec['boards'], day, source=source,
                        timeframe=spec['timeframe'], verify_files=True,
                        check_stop=lambda:service.check_stop(job))
    if not audit['scope_valid']:
        raise ValueError('范围未核验，不能推断逐股缺日')
    wanted = set(spec['codes'])
    gaps = {g['code']:g for g in audit['gaps']}
    if not wanted or not wanted.issubset(gaps):
        raise ValueError('仅可核验当前来源、当前范围中仍受阻的所选股票')
    targets = {}
    for code in sorted(wanted):
        gap = gaps[code]
        if gap.get('category') == 'input_gap':
            targets[code] = gap['dates']
        elif gap.get('category') == 'stale_tail':
            targets[code] = [audit['expected_day']]
        else:
            raise ValueError(f'{code} 不是历史缺日或尾日落后；身份／根数／文件问题不能通过停牌核验放行')
    report = verify_status_targets(service.store, targets, BaoStock, job,
        retry=bool(spec.get('retry')), check_stop=lambda:service.check_stop(job),
        progress=lambda n,total,message:service.progress(job,n,total,message),
        cancel_event=service.cancel_flags[job])
    report['selected_source'] = source
    report['coverage'] = audit_scope(service.store, spec['boards'], day, source=source,
        timeframe=spec['timeframe'], verify_files=True, check_stop=lambda:service.check_stop(job))
    from .market import latest_datasets
    save_cached_quality(service.store, [r['id'] for r in latest_datasets(service.store,source)], day)
    if source in ('tickflow','tencent'):
        from .tickflow_integrity import integrity_report
        receipt = integrity_report(service.store, spec['boards'], day, timeframe=spec['timeframe'], source=source)
        service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (source+'_integrity', dumps(receipt)))
    return report
