"""Independent vendor downloads, bounded local scans and durable child receipts."""
import copy
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .market import BOARDS, board_of, completed_date, latest_datasets
from .provider_guard import ProviderError, ProviderGuard, error_detail
from .sources import SOURCES, isolate_tickflow_legacy, require_update_source
from .store import dumps, now
from .premarket_store import PremarketStore


def validate_spec(spec):
    sources = spec.get('sources', [])
    if not sources or len(sources) != len(set(sources)) or any(s not in SOURCES for s in sources):
        raise ValueError('请至少选择一个有效数据源，不能重复')
    for source in sources:
        require_update_source(source)
    if not spec.get('boards') or any(b not in BOARDS for b in spec['boards']):
        raise ValueError('请选择有效板块')
    if not spec.get('strategies') or spec.get('timeframe') not in ('daily', 'weekly'):
        raise ValueError('请选择策略与日线／周线周期')


def run_premarket(service, job, spec):
    validate_spec(spec)
    end = min(spec.get('end') or completed_date(), completed_date())
    stop = service.cancel_flags[job]
    report = dict(sources={}, asof=end, complete=False, errors=[])
    notice_lock, scan_lock = threading.Lock(), threading.Lock()
    last_notice = [0.0]
    # Finish any legacy inventory migration before independent vendors write.
    isolate_tickflow_legacy(service.store)

    # Parent and child progress share the same short-connection coordinator.
    # Never wait for its artifact/DB lock while holding notice_lock.
    receipt_store = PremarketStore(service.store.root)
    receipt_store._files_lock = service.store._files_lock

    def notice(source, phase, done=None, total=None, message='', force=False):
        with notice_lock:
            item = report['sources'].setdefault(source, {})
            item.update(phase=phase, message=message)
            if done is not None: item['progress'] = done
            if total is not None: item['total'] = total
            stamp = time.monotonic()
            publish = force or stamp - last_notice[0] >= .5
            if publish: last_notice[0] = stamp
        if publish:
            with receipt_store.connect() as db:
                with notice_lock:
                    snapshot = copy.deepcopy(report)
                text = ' · '.join(f'{SOURCES[s]} {i["phase"]} {i.get("progress",0)}/{i.get("total",0)}'
                                  + ('（逐股回退）' if '整日回退逐股' in i.get('message','') else '')
                                  + (f'：{i.get("message", "")[:75]}' if i['phase'] in ('失败','等待续做') else '')
                                  for s, i in snapshot['sources'].items())
                done_sources = sum(i['phase'] in ('完成','部分完成','未完成','失败','已停止')
                                   for i in snapshot['sources'].values())
                db.execute('UPDATE jobs SET progress=?,total=?,message=?,result=? WHERE id=?',
                           (done_sources,len(spec['sources']),text,dumps(snapshot),job))

    # Separate Store caches per source; same durable database and file lock.
    stores = {}
    for source in spec['sources']:
        child_store = PremarketStore(service.store.root)
        child_store._files_lock = service.store._files_lock
        stores[source] = child_store
        notice(source, '等待', force=True)

    def work(source):
        proxy = copy.copy(service)
        proxy.store = stores[source]
        receipt = report['sources'][source]

        def stage(kind, stage_spec, phase):
            child = uuid.uuid4().hex[:16]
            stage_spec = dict(stage_spec, premarket_parent=job)
            proxy.cancel_flags = {child: stop}
            last_progress = [0.0]
            pending_progress = [None]
            def progress(_job, done, total, msg):
                pending_progress[0] = (done, total, msg, child)
                stamp = time.monotonic()
                if stamp - last_progress[0] >= 1 or (total and done >= total):
                    proxy.store.execute('UPDATE jobs SET progress=?,total=?,message=? WHERE id=?', pending_progress[0])
                    last_progress[0] = stamp
                display_phase = ('等待续做' if phase == '更新' and '临时断网，等待' in msg else
                                 '核对缓存' if phase == '更新' and msg.startswith('核对已保存行情') else phase)
                notice(source, display_phase, done, total, msg)
            proxy.progress = progress
            proxy.store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                                (child, kind, 'running', now(), dumps(stage_spec)))
            proxy.store.event(child, '一键盘前子任务', parent=job, source=source)
            with notice_lock:
                receipt[kind+'_job'] = child
            started = time.monotonic()
            metrics_before = proxy.store.metrics()
            try:
                proxy.check_stop(child)
                result = getattr(proxy, '_'+kind)(child, stage_spec)
                status = 'partial' if result.get('errors') or result.get('complete') is False else 'completed'
                if stop.is_set(): status = 'cancelled'
                message = result.get('stop_reason', '完成')
            except InterruptedError as exc:
                result, status, message = {}, 'cancelled', str(exc)
            except Exception as exc:
                detail = error_detail(exc, source)
                if kind == 'sync' and isinstance(exc, (ProviderError, TimeoutError)):
                    detail['cooldown'] = getattr(exc, '_provider_guard_state', None) or ProviderGuard(proxy.store,source).failure(exc)
                result, status, message = {'errors':[detail]}, 'failed', str(exc)
                proxy.store.event(child,'一键盘前子任务异常',kind=kind,
                                  **detail,traceback=traceback.format_exc())
            finally:
                proxy.store.flush_reads()
                if pending_progress[0]:
                    proxy.store.execute('UPDATE jobs SET progress=?,total=?,message=? WHERE id=?', pending_progress[0])
            elapsed = time.monotonic() - started
            result['local_io'] = {key: round(value - metrics_before[key], 4)
                                  for key, value in proxy.store.metrics().items()}
            proxy.store.execute('UPDATE jobs SET status=?,finished=?,message=?,result=? WHERE id=?',
                                (status, now(), message, dumps(result), child))
            with notice_lock:
                receipt[kind+'_seconds'] = elapsed
                receipt[kind+'_status'] = status
                receipt[kind+'_result'] = {k: result[k] for k in ('asof','success','signals','reused','calculated','stage_reused','daily_batch','batch_fallback_reason','skipped','note','stop_reason','local_io','transport_recovery') if k in result}
                if result.get('resume'):
                    receipt[kind+'_result']['resume'] = resume_summary(result['resume'])
                if kind == 'scan':
                    audits = result.get('coverage_by_timeframe', {}) or {'current': result.get('coverage', {})}
                    excluded = {g['code'] for a in audits.values() for g in a.get('gaps', [])}
                    receipt[kind+'_result']['not_scanned_count'] = len(excluded)
                if result.get('errors'):
                    # Full diagnostics already live in the child receipt.
                    # Do not rewrite thousands of stock errors every .5s.
                    receipt[kind+'_result'].update(error_count=len(result['errors']),
                                                  errors=result['errors'][:3])
            return result, status, message

        sync_spec = dict(source=source, boards=list(spec['boards']), start=spec['start'],
                         end=end, force=False, scan_timeframe=spec['timeframe'], resume=True,
                         full_history=spec.get('full_history', False))
        result, status, message = stage('sync', sync_spec, '更新')
        if status in ('failed','cancelled'):
            notice(source, '失败' if status=='failed' else '已停止', message=message, force=True)
            return
        notice(source, '等待扫描', 0, 0, force=True)
        # A slow vendor never holds this lock while waiting on its network.
        with scan_lock:
            if stop.is_set():
                notice(source, '已停止', force=True)
                return
            ids = [r['id'] for r in latest_datasets(proxy.store,source)
                   if r['timeframe']=='daily' and board_of(r['code']) in spec['boards']]
            result, status, message = stage('scan', dict(source=source, boards=list(spec['boards']),
                datasets=ids, strategies=list(spec['strategies']), timeframes=[spec['timeframe']], asof=end), '扫描')
        notice(source, '完成' if status=='completed' else '部分完成' if status=='partial' else '已停止' if status=='cancelled' else '未完成',
               message=message, force=True)

    with ThreadPoolExecutor(max_workers=len(spec['sources']), thread_name_prefix='premarket-source') as pool:
        futures = [pool.submit(work, s) for s in spec['sources']]
        for future in futures:
            future.result()
    report['complete'] = all(i.get('scan_status') == 'completed' for i in report['sources'].values())
    report['errors'] = [dict(source=s, error=i.get('message','未完成')) for s,i in report['sources'].items()
                        if i.get('scan_status') != 'completed']
    return report


def resume_summary(resume):
    """Parent progress stays bounded; full lists live only in the child receipt."""
    result = {key: resume[key] for key in ('reused', 'scope_reused', 'ordinary_increment',
              'repair_attempted', 'waiting_for_cooldown') if key in resume}
    result.update(repair_count=len(resume.get('repair_codes', [])),
                  unchanged_count=len(resume.get('unchanged', [])))
    return result
