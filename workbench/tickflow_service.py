"""Independent TickFlow sync and integrity receipts; no BaoStock network calls."""
import json

from .market import completed_date, select_board_codes
from .readiness import audit_scope, expected_day, save_directory
from .sources import coverage_state
from .store import dumps
from .sync import sync_stock
from .provider_guard import ProviderGuard, ProviderError, error_detail


def sync_tickflow(service, job, spec):
    from .tickflow import TickFlow
    from .readiness import extend_exchange_calendar, filter_tickflow_directory
    from .sync import local_history
    start=spec['start']
    repair_scope = spec.get('repair_scope', 'history')
    if repair_scope not in ('scan', 'history'):
        raise ValueError('补拉范围必须为当前扫描或历史核对')
    end=min(spec.get('end') or completed_date(),completed_date())
    if start>end:
        raise ValueError('同步开始日期不能晚于已完成行情日期')
    extend_exchange_calendar(service.store,start,end,job,source='tickflow')
    end=expected_day(service.store,end,'tickflow')
    provider=TickFlow(); provider.cancel_event=service.cancel_flags[job]
    with provider:
        if 'boards' in spec:
            if not spec['boards']:
                raise ValueError('请至少勾选一个板块')
            service.progress(job,0,1,'TickFlow 核对股票目录；不访问 BaoStock')
            directory=provider.universe(end)
            directory, retired, excluded = filter_tickflow_directory(service.store, directory, end, job)
            save_directory(service.store,directory,end,job,retired_codes=retired,source='tickflow')
            codes=select_board_codes(directory,spec['boards'])
        else:
            codes=list(dict.fromkeys(spec['codes']))
        repair = set(spec.get('repair_codes') or [])
        if repair and ('boards' not in spec or not repair.issubset(set(codes))):
            raise ValueError('补拉标的必须属于已核验的所选板块')
        from .suspensions import announcement_evidence
        proven_halts = [c for c in codes if end in announcement_evidence(c, end, end)[0]]
        codes = [c for c in codes if c not in proven_halts and (not repair or c in repair)]
        report=dict(source='tickflow',requested=len(codes),success=0,errors=[],datasets=[],connections=1,
            end_requested=end,skipped=0,downloaded=0,updated=0,refreshed=0,suspended=0,boards=spec.get('boards',[]),
            excluded_retired=excluded if 'boards' in spec else [], history_warnings=[],
            confirmed_halts=proven_halts, repair_codes=sorted(repair), processed=0)
        groups={}
        for code in codes:
            service.check_stop(job)
            states=coverage_state(service.store,code,'tickflow')
            try:
                if states:
                    state=states[0]
                elif code in repair:
                    # Repair can recover a PR27 snapshot without loading its
                    # possibly missing/corrupt file during request planning.
                    rows=service.store.rows("SELECT * FROM datasets WHERE source='tickflow' AND code=? "
                        "AND adjustment='前复权' AND timeframe='daily' "
                        "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1",(code,))
                    state=dict(dataset_id=rows[0]['id'],start=rows[0]['start'],end=rows[0]['end']) if rows else None
                else:
                    state=local_history(service.store,code,job,source='tickflow')[1]
            except (OSError,ValueError) as exc:
                report['errors'].append(dict(code=code,error=str(exc)))
                report['processed'] += 1
                continue  # One broken cache does not discard the other stocks.
            if state and not spec.get('force') and code not in repair and state['start']<=start and end<=state['end']:
                try:
                    did,outcome=sync_stock(service.store,lambda:provider,code,start,end,job,source='tickflow')
                    report[outcome]+=1; report['success']+=1; report['datasets'].append(did)
                except (OSError, ValueError) as exc:
                    report['errors'].append(dict(code=code,error=str(exc)))
                report['processed'] += 1
                if report['processed'] % 10 == 0:
                    service.progress(job,report['processed'],len(codes),'TickFlow 后台核验已有快照与历史缺口')
                    service.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
                continue
            # First source switch takes a full same-source history, never a BaoStock tail.
            left=min(start,state['start']) if state else start
            right=max(end,state['end']) if state else end
            if state and not spec.get('force') and code not in repair and start>=state['start']:
                left=state['end']
            groups.setdefault((left,right),[]).append(code)
        stopped=False
        for (left,right),group in groups.items():
            for offset in range(0,len(group),100):
                service.check_stop(job)
                batch=group[offset:offset+100]
                service.progress(job,report['success'],len(codes),f'TickFlow 批量获取 {len(batch)} 只 · {left}～{right}')
                try:
                    provider.preload(batch,left,right)
                except (ProviderError,TimeoutError) as exc:
                    report['cooldown']=ProviderGuard(service.store,'tickflow').failure(exc)
                    report['stop_reason']=str(exc); stopped=True
                    report['errors'].extend(dict(code=code,**error_detail(exc,'tickflow')) for code in batch)
                    report['processed'] += len(batch)
                    break  # One bounded request; never restart a failed batch automatically.
                for code in batch:
                    service.check_stop(job)
                    try:
                        if code in repair:
                            from .tickflow_repair import restore_corrupt_snapshot
                            restore_corrupt_snapshot(service.store,provider,code,left,right,job)
                        did,outcome=sync_stock(service.store,lambda:provider,code,start,end,job,
                                              spec.get('force',False) or code in repair,source='tickflow')
                        report[outcome]+=1; report['success']+=1; report['datasets'].append(did)
                    except (ProviderError,TimeoutError) as exc:
                        state=ProviderGuard(service.store,'tickflow').failure(exc)
                        report['cooldown']=state; report['stop_reason']=str(exc); stopped=True
                        report['errors'].append(dict(code=code,**error_detail(exc,'tickflow')))
                        report['processed'] += 1
                        break
                    except (ValueError,OSError) as exc:
                        report['errors'].append(dict(code=code,error=str(exc)))
                    report['processed'] += 1
                    service.progress(job,report['success']+len(report['errors']),len(codes),
                                  f"TickFlow 已保存 {report['success']}/{len(codes)} · 缺口 {len(report['errors'])}")
                    service.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
                if stopped: break
            if stopped: break
    report['remaining']=max(0,len(codes)-report['processed'])
    for did in report['datasets']:
        rows = service.store.rows('SELECT value FROM meta WHERE key=?', ('market_quality:'+did,))
        if rows:
            quality = json.loads(rows[0]['value'])
            if quality['missing_dates']:
                report['history_warnings'].append(dict(dataset=did, count=len(quality['missing_dates']),
                    first=quality['missing_dates'][0], last=quality['missing_dates'][-1]))
    if report['errors']:
        report.setdefault('stop_reason',report['errors'][0]['error'])
    if 'boards' in spec:
        audit=audit_scope(service.store,spec['boards'],end,source='tickflow')
        report['coverage']=audit
        from .tickflow_integrity import integrity_report
        report['integrity']=integrity_report(service.store,spec['boards'],end,report['errors'],
                                             timeframe=spec.get('scan_timeframe','daily'))
        report['scan_readiness']=report['integrity']['scan']
        report['scan_periods']=report['integrity']['periods']
        service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              ('tickflow_integrity',dumps(report['integrity'])))
        service.store.write_artifact(f'reports/{job}-tickflow-integrity.json',dumps(report['integrity']).encode(),job)
        scan_audit=report['scan_readiness']
        if not audit['complete'] or not scan_audit['complete']:
            report.setdefault('stop_reason','TickFlow 部分标的未就绪；可扫描已核验输入，排除项见回执')
            known={e['code'] for e in report['errors']}
            for g in audit['gaps']+scan_audit['gaps']:
                if g['code'] not in known:
                    report['errors'].append(g); known.add(g['code'])
        if repair:
            pending = ({g['code'] for g in report['integrity']['scan']['gaps']} if repair_scope == 'scan'
                       else set(report['integrity']['repair_codes']))
            remaining=sorted(repair & pending)
            report['repair_scope']=repair_scope
            report['repair_remaining']=remaining
            report['repair_resolved']=sorted(repair - set(remaining))
            report['repair_history_pending']=sorted(repair & {g['code'] for g in report['integrity']['unknown_history']})
            if remaining:
                known={e['code'] for e in report['errors']}
                report['errors'].extend(dict(code=c,error='补拉后仍有未知缺口；已保留真实数据，需查公告或稍后再试')
                                        for c in remaining if c not in known)
                target='当前扫描' if repair_scope == 'scan' else '历史核对'
                report['stop_reason']=f'定向补拉后仍有 {len(remaining)} 只{target}未齐；没有自动再次请求'
    return report
