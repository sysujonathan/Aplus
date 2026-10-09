"""Third independent historical route; no BaoStock or TickFlow requests."""
from .market import completed_date, select_board_codes, board_of
from .sources import coverage_state, directory_file, directory_date, SOURCES
from .readiness import extend_exchange_calendar, expected_day, save_directory
from .store import dumps
from .sync import sync_stock
from .provider_guard import ProviderGuard, ProviderError, error_detail
from .tencent import Tencent


def local_public_catalog(store,day):
    """Public securities identity only, never prices/trade status from another source.

    A verified catalog for the exact target day can bootstrap an isolated Tencent
    inventory. Stale catalogs are not silently stamped as current membership.
    """
    import pandas as pd
    for source in SOURCES:
        if source=='tencent' or directory_date(store,source)!=day:
            continue
        path=directory_file(store,source)
        if not path.exists():continue
        frame=pd.read_csv(path,dtype=str).fillna('')
        if frame.empty or 'code' not in frame or frame.code.duplicated().any():continue
        from .market import code_of
        if any(code_of(c)!=c for c in frame.code):continue
        columns=['code']+(['code_name'] if 'code_name' in frame else [])
        catalog=frame[columns].copy(); catalog['tradeStatus']='unknown'
        return catalog,source
    return None,None


def sync_tencent(service, job, spec):
    start=spec['start']; end=min(spec.get('end') or completed_date(),completed_date())
    if start>end: raise ValueError('同步开始日期不能晚于已完成行情日期')
    extend_exchange_calendar(service.store,start,end,job,source='tencent')
    end=expected_day(service.store,end,'tencent')
    provider=Tencent(); provider.cancel_event=service.cancel_flags[job]
    provider.on_wait=lambda op,n:service.progress(job,0,0,
        f'腾讯路线核验公共证券目录 {n} 只' if op=='directory' else f'腾讯 {op} 等待响应 {n} 秒')
    report=dict(source='tencent',adjustment='不复权',requested=0,success=0,errors=[],datasets=[],
        skipped=0,downloaded=0,updated=0,refreshed=0,suspended=0,connections=1,
        end_requested=end,boards=spec.get('boards',[]),processed=0,
        catalog_source='sina_public_securities_only',repair_codes=spec.get('repair_codes',[]),
        limitations=['腾讯历史为不复权：除权除息可能形成价格跳空，跨除权回测不包含分红及股数调整；不与其他来源混算'])
    repair=set(spec.get('repair_codes') or [])
    with provider:
        if 'boards' in spec:
            if not spec['boards']: raise ValueError('请至少勾选一个板块')
            service.progress(job,0,0,'腾讯路线核验公共证券目录；不连接 BaoStock/TickFlow')
            path=directory_file(service.store,'tencent')
            report['catalog_reused']=path.exists() and directory_date(service.store,'tencent')==end
            if report['catalog_reused']:
                import pandas as pd
                directory=pd.read_csv(path,dtype=str)
                if not {'code','tradeStatus'}.issubset(directory.columns):
                    raise ValueError('腾讯本地目录缺少身份字段，未开始下载价格')
            else:
                directory,origin=local_public_catalog(service.store,end)
                if directory is None:
                    directory=provider.universe(end)
                else:
                    report['catalog_source']='local_verified_securities_only:'+origin
                    report['limitations'].append(f'只读复用 {SOURCES[origin]} 已核验的 {end} 公共证券身份目录；不读取其价格或停牌状态')
            report['unsupported_directory']=[dict(code=r.code,name=getattr(r,'code_name',''))
                for r in directory.itertuples() if board_of(r.code) is None]
            if report['unsupported_directory']:
                report['limitations'].append(f"公共目录有 {len(report['unsupported_directory'])} 只板块身份尚未适配；保留目录记录，不纳入扫描范围")
            from .readiness import filter_tickflow_directory
            directory,retired,excluded=filter_tickflow_directory(service.store,directory,end,job)
            save_directory(service.store,directory,end,job,retired_codes=retired,source='tencent')
            codes=select_board_codes(directory,spec['boards'])
        else:
            codes=list(dict.fromkeys(spec['codes']))
        if repair and ('boards' not in spec or not repair.issubset(set(codes))):
            raise ValueError('补拉标的必须属于当前已核验范围')
        from .suspensions import announcement_evidence
        halts=[c for c in codes if end in announcement_evidence(c,end,end)[0]]
        report['confirmed_halts']=halts
        codes=[c for c in codes if c not in halts and (not repair or c in repair)]
        report['requested']=len(codes)
        for code in codes:
            service.check_stop(job)
            service.progress(job,report['processed'],len(codes),f'腾讯独立历史 {code} · {start}～{end}')
            try:
                if code in repair:
                    from .tickflow_repair import restore_corrupt_snapshot
                    states=coverage_state(service.store,code,'tencent')
                    left=min(start,states[0]['start']) if states else start
                    right=max(end,states[0]['end']) if states else end
                    restore_corrupt_snapshot(service.store,provider,code,left,right,job,source='tencent')
                did,outcome=sync_stock(service.store,lambda:provider,code,start,end,job,
                    force=spec.get('force',False) or code in repair,source='tencent')
                report['datasets'].append(did); report[outcome]+=1; report['success']+=1
            except InterruptedError:
                raise
            except (ProviderError,TimeoutError) as exc:
                report['errors'].append(dict(code=code,**error_detail(exc,'tencent')))
                report['cooldown']=ProviderGuard(service.store,'tencent').failure(exc)
                report['stop_reason']=str(exc)
                report['processed']+=1
                break  # Bounded failure, no reconnect/retry against the same endpoint.
            except (ValueError,OSError) as exc:
                report['errors'].append(dict(code=code,error=str(exc)))
            report['processed']+=1
            service.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
    report['remaining']=len(codes)-report['processed']
    if 'boards' in spec:
        from .tickflow_integrity import integrity_report
        receipt=integrity_report(service.store,spec['boards'],end,report['errors'],
            timeframe=spec.get('scan_timeframe','daily'),source='tencent')
        report.update(integrity=receipt,coverage=receipt['scan'],scan_readiness=receipt['scan'],
                      scan_periods=receipt['periods'])
        receipt['limitations']=report['limitations']
        receipt['unsupported_directory']=report.get('unsupported_directory',[])
        service.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              ('tencent_integrity',dumps(receipt)))
        service.store.write_artifact(f'reports/{job}-tencent-integrity.json',dumps(receipt).encode(),job)
        if receipt['scan']['gaps']:
            known={e['code'] for e in report['errors']}
            report['errors'].extend(g for g in receipt['scan']['gaps'] if g['code'] not in known)
            report.setdefault('stop_reason','腾讯部分标的未就绪；已保存真实行情，完整性窗口可定向补拉')
        if repair:
            pending=set(receipt['repair_codes'] if spec.get('repair_scope')=='history' else receipt['scan_repair_codes'])
            report['repair_resolved']=sorted(repair-pending)
            report['repair_remaining']=sorted(repair&pending)
    if report['errors']: report.setdefault('stop_reason',report['errors'][0]['error'])
    service.progress(job,report['processed'],len(codes),f"腾讯已处理 {report['processed']}/{len(codes)}；未完成见回执")
    return report
