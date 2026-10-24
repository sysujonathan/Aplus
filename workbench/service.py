"""Application use cases and a single-worker job queue with durable receipts."""
from __future__ import annotations

import io
from contextlib import contextmanager
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import numpy as np
import pandas as pd

from .backtest import Assumptions, run_study, summarize
from .market import (BaoStock, completed_date, dataset_records, load_dataset, verify_dataset,
                     save_dataset, validate_bars, select_board_codes, latest_datasets)
from .store import ROOT, digest, dumps, now
from .strategies import (calculate, catalog, prepare, prepare_indicators, scan_engine_version,
                         signal_at_end, verify_frozen)
from .sync import sync_stock
from .readiness import (audit_scope, expected_day, reusable_baostock_basics,
                        save_calendar, save_directory)
from .sync_batch import sync_results
from .sources import market_source, SOURCES, coverage_state, FALLBACK_SOURCES, require_update_source
from .provider_guard import ProviderGuard, ProviderError, access_limited, restricted, error_detail
from .scan_receipt import (begin_receipt, persist_rebuilt_receipt, progress_receipt,
                           recover_running_scans)
from .mtr_acceleration import MTRFastRejector, requested_mode as mtr_requested_mode
from .three_k_acceleration import ThreeKFastRejector, requested_mode as three_k_requested_mode


def merge_scan_reports(previous, current):
    merged = dict(previous, **current)
    for key in ['success','signals','no_signal','reused','calculated','total']:
        merged[key] = previous.get(key,0) + current.get(key,0)
    for key in ['errors','observation_ids']:
        merged[key] = previous.get(key,[]) + current.get(key,[])
    merged['strategy_versions'] = dict(previous.get('strategy_versions',{}), **current.get('strategy_versions',{}))
    merged['checkpoints'] = dict(previous.get('checkpoints',{}), **current.get('checkpoints',{}))
    coverage = current.get('coverage')
    if coverage and coverage.get('source') in SOURCES and current.get('timeframe'):
        merged['coverage_by_timeframe'] = dict(previous.get('coverage_by_timeframe',{}),
                                               **{current['timeframe']:coverage})
    return merged


def _scan_crash_probe(_point):
    """No-op transaction-boundary probe used by hard-crash recovery tests."""


class Service:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='a-workbench')
        self.cancel_flags = {}
        self.lock = threading.Lock()
        self._code_update_reserved = False
        recover_running_scans(store)
        store.execute("UPDATE jobs SET status='interrupted',finished=?,message='程序重启；任务未完成，可重新发起' "
                      "WHERE status IN ('queued','running')", (now(),))

    def submit(self, kind, spec):
        if kind not in {'sync','scan','backtest','validate','universe','verify_status','premarket'}:
            raise ValueError('未知任务类型')
        if kind in {'scan','backtest','validate'}:
            verify_frozen()
        if kind == 'premarket':
            from .premarket import validate_spec
            validate_spec(spec)
            verify_frozen()
        if kind=='sync':
            spec=dict(spec,source=spec.get('source') or market_source(self.store))
            require_update_source(spec['source'])
            if not spec.get('resume'):
                ProviderGuard(self.store,spec['source']).check()
        with self.lock:
            if self._code_update_reserved:
                raise ValueError('正在准备版本更新，请完成更新或取消后再开始任务')
            if self.store.rows("SELECT id FROM jobs WHERE status IN ('running','queued')"):
                raise ValueError('已有任务正在运行。请等待结束或停止后再开始，避免争用行情连接')
            for key, pending in self.cancel_flags.items():
                if key.startswith('chart:'):
                    pending.set()  # Explicit business work takes priority.
            job = uuid.uuid4().hex[:16]
            self.store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                               (job,kind,'queued',now(),dumps(spec)))
            self.cancel_flags[job] = threading.Event()
            self.pool.submit(self._execute, job,kind,spec)
        return job

    def reserve_code_update(self):
        """No job can enter between the idle check and code-update reservation."""
        with self.lock:
            if self._code_update_reserved or self.cancel_flags or self.store.rows(
                    "SELECT id FROM jobs WHERE status IN ('queued','running')"):
                raise ValueError('行情、扫描或研究任务尚未结束，请等待后再更新版本')
            self._code_update_reserved = True

    def release_code_update(self):
        with self.lock:
            self._code_update_reserved = False

    @contextmanager
    def chart_access(self, cancel_event):
        """An on-demand chart read must finish before code can be replaced."""
        key = 'chart:' + uuid.uuid4().hex
        with self.lock:
            if self._code_update_reserved:
                raise ValueError('正在准备软件更新，请完成更新后再获取K图')
            if any(not key.startswith('chart:') for key in self.cancel_flags):
                raise ValueError('盘前或研究任务正在进行；先保留本地K图，任务结束后可更新K线')
            self.cancel_flags[key] = cancel_event
        try:
            yield
        finally:
            with self.lock:
                self.cancel_flags.pop(key, None)

    def cancel(self, job):
        if job in self.cancel_flags:
            self.cancel_flags[job].set()
            self.store.event(job,'请求停止',detail='当前股票/数据请求结束后停止')

    def progress(self, job, done, total, message):
        self.store.execute('UPDATE jobs SET progress=?,total=?,message=? WHERE id=?', (done,total,message,job))

    def _execute(self, job,kind,spec):
        self.store.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.store.event(job, '开始任务', kind=kind, settings=spec)
        try:
            self._wait_for_chart_reads(job)
            report = getattr(self,'_'+kind)(job,spec)
            state = 'partial' if report.get('errors') or report.get('complete') is False else 'completed'
            if self.cancel_flags[job].is_set():
                state = 'cancelled'
            self.store.execute('UPDATE jobs SET status=?,finished=?,result=?,message=? WHERE id=?',
                               (state,now(),dumps(report),'完成' if state=='completed' else report.get('stop_reason','请查看回执中的未完成项目'),job))
        except InterruptedError as exc:
            if kind == 'scan':
                persist_rebuilt_receipt(self.store, job)
            self.store.execute("UPDATE jobs SET status='cancelled',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        except Exception as exc:
            source=spec.get('source','baostock')
            detail=error_detail(exc,source)
            recorded_guard = getattr(exc, '_provider_guard_state', None)
            if recorded_guard is not None:
                detail['cooldown'] = recorded_guard
            elif kind in ('sync','verify_status') and isinstance(exc,(ProviderError,TimeoutError)):
                if kind == 'verify_status':
                    source = 'baostock'
                detail['cooldown']=ProviderGuard(self.store,source).failure(exc)
            self.store.event(job,'任务异常',**detail,traceback=traceback.format_exc())
            report = persist_rebuilt_receipt(self.store, job) if kind == 'scan' else None
            if report is None:
                report = {'errors':[detail],'stop_reason':str(exc),'source':source}
            else:
                report['errors'] = list(report.get('errors', [])) + [detail]
                report.update(stop_reason=str(exc), source=source)
            self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
            self.store.execute("UPDATE jobs SET status='failed',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        finally:
            self.store.event(job,'任务结束',self.store.path)
            with self.lock:
                self.cancel_flags.pop(job,None)

    def _wait_for_chart_reads(self, job):
        """Do not race a cancelled on-demand read for the vendor OS lease."""
        deadline = time.monotonic() + 30
        while True:
            with self.lock:
                active = any(key.startswith('chart:') for key in self.cancel_flags)
            if not active:
                return
            if self.cancel_flags[job].wait(.05):
                raise InterruptedError('已停止等待关注K图连接释放')
            if time.monotonic() >= deadline:
                raise ValueError('关注K图连接尚未安全释放，本次任务未发出行情请求，请稍后再试')

    def check_stop(self, job):
        if self.cancel_flags[job].is_set():
            raise InterruptedError('任务已停止；已保存的记录保留，但不宣称全部完成')

    def _verify_status(self, job, spec):
        from .status_review import review_job
        return review_job(self, job, spec)

    def _premarket(self, job, spec):
        from .premarket import run_premarket
        return run_premarket(self, job, spec)

    def _universe(self, job,spec):
        provider = BaoStock()
        provider.cancel_event = self.cancel_flags[job]
        with provider:
            from datetime import date,timedelta
            day = date.fromisoformat(min(spec.get('date',completed_date()),completed_date()))
            for n in range(10):
                self.check_stop(job)
                result = provider.universe((day-timedelta(days=n)).isoformat())
                if not result.empty:
                    save_directory(self.store,result,str(day-timedelta(days=n)),job)
                    return {'stocks':len(result),'date':str(day-timedelta(days=n))}
        raise ValueError('最近十天未取得股票名单，请检查行情服务')

    def _sync(self, job,spec):
        if spec.get('resume'):
            from .premarket_resume import continue_sync
            return continue_sync(self, job, spec)
        return self._sync_once(job, spec)

    def _sync_once(self, job,spec):
        source=spec.get('source','baostock')
        require_update_source(source)
        ProviderGuard(self.store,source).check()
        if source=='tickflow':
            return self._sync_tickflow(job,spec)
        # end=None 表示“直到最新交易日”。completed_date() 返回字符串，不能与 None
        # 直接 min（会报 "'<' not supported between str and NoneType"），故先归一化。
        end = completed_date() if not spec.get('end') else min(spec['end'], completed_date())
        start = spec['start']
        if start > end:
            raise ValueError('同步开始日期不能晚于已完成行情日期')
        scope_reused = False
        basics_reused = False
        daily_batch = None
        daily_batch_codes = []
        daily_batch_request_seconds = 0.0
        daily_batch_request_count = 0
        if 'boards' in spec:
            if not spec['boards']:
                raise ValueError('请至少勾选一个板块')
            from .repair_scope import cached_baostock_repair_scope
            cached = cached_baostock_repair_scope(self.store, end) if spec.get('repair_codes') else None
            if cached is not None:
                end, universe = cached
                scope_reused = True
                self.progress(job,0,len(spec['repair_codes']),'复用已核验日历与目录，直接补拉所选股票')
            else:
                self.progress(job,0,1,'核对交易日历和当日股票目录')
                candidate = BaoStock()
                candidate.cancel_event = self.cancel_flags[job]
                with candidate:
                    # Public dates already certified locally need no repeat
                    # 36-year query. Prices/connection remain isolated.
                    try:
                        expected_day(self.store, end)
                    except ValueError:
                        save_calendar(self.store, candidate.calendar('1990-12-19', end), '1990-12-19', end, job)
                    end = expected_day(self.store, end)
                    directory = candidate.universe(end)
                    if directory.empty:
                        raise ValueError('应有交易日股票目录为空，未开始下载')
                    self.progress(job,0,1,'自动核对新增上市、退市与停牌状态')
                    basics = reusable_baostock_basics(self.store, directory, end)
                    basics_reused = basics is not None
                    if basics is None:
                        basics = candidate.basics()
                    else:
                        evidence = basics.attrs['reused_from']
                        self.store.event(job, '复用同源证券档案；当日官方目录逐只验证一致',
                                         date=end, evidence=evidence, count=len(directory))
                        self.progress(job,0,1,'当日目录与同源证券档案完全一致，免除重复下载')
                    save_directory(self.store, directory, end, job, basics=basics)
                    selected = select_board_codes(directory, spec['boards'])
                    if (not spec.get('force') and not spec.get('repair_codes')):
                        from .baostock_daily_batch import one_day_tail_candidates
                        active = [code for code in selected if code not in set(
                            directory.loc[directory.tradeStatus.astype(str) == '0', 'code'])]
                        if spec.get('increment_codes'):
                            active = [code for code in active if code in set(spec['increment_codes'])]
                        daily_batch_codes = one_day_tail_candidates(self.store, active, start, end)
                        # Two official requests replace thousands of single-stock
                        # tail requests only when the saving is material.
                        if len(daily_batch_codes) >= 50:
                            self.progress(job, 0, len(selected),
                                          f'BaoStock · 获取 {end} 整日行情并核对复权事件')
                            stamp = time.monotonic()
                            try:
                                archive = candidate.daily_astock(end)
                                daily_batch_request_count += 1
                                factors = candidate.daily_adjust_factor(end)
                                daily_batch_request_count += 1
                                daily_batch = (archive, factors)
                            except Exception as exc:
                                # A malformed/empty bulk response is only an
                                # optimization failure.  An explicit provider
                                # restriction is different: do not reconnect
                                # and issue even one more per-stock request.
                                # Record the durable cooldown here; _execute
                                # reuses it instead of counting the same refusal twice.
                                if access_limited(exc):
                                    state = ProviderGuard(self.store, source).failure(exc)
                                    setattr(exc, '_provider_guard_state', state)
                                    self.store.event(job, '整日行情遭供应商限制，立即停止整批同步',
                                                     date=end, **error_detail(exc, source))
                                    raise
                                if isinstance(exc, (ProviderError, TimeoutError)):
                                    ProviderGuard(self.store, source).failure(exc)
                                daily_batch = None
                                daily_batch_codes = []
                                self.store.event(job, '整日行情不可用，自动改用逐股更新',
                                                 date=end, error=str(exc))
                                daily_batch = {'unavailable': str(exc)}
                            finally:
                                daily_batch_request_seconds = time.monotonic() - stamp
                universe = pd.read_csv(io.BytesIO(self.store.read_artifact('universe.csv',job)))
            codes = select_board_codes(universe,spec['boards'])
        else:
            codes = list(dict.fromkeys(spec['codes']))
        if spec.get('increment_codes'):
            increments = set(spec['increment_codes'])
            if 'boards' not in spec or not increments.issubset(codes):
                raise ValueError('续做标的必须属于已核验的所选板块')
            codes = [code for code in codes if code in increments]
        if spec.get('repair_codes'):
            # Explicit UI selection only; provider connection/protection unchanged.
            requested = set(spec['repair_codes'])
            if not requested.issubset(codes):
                raise ValueError('所选补拉股票不属于当前来源／板块目录')
            codes = [code for code in codes if code in requested]
        if not codes:
            raise ValueError('请先选择股票范围')
        report = {'source':source,'requested':len(codes),'success':0,'errors':[], 'datasets':[], 'end_requested':end,
                  'skipped':0,'downloaded':0,'updated':0,'refreshed':0,'suspended':0,'partial_saved':0}
        report['boards'] = spec.get('boards',[])
        report['scope_reused'] = scope_reused
        report['basics_reused'] = basics_reused
        suspended = set()
        if 'boards' in spec and 'tradeStatus' in universe.columns:
            suspended = set(universe.loc[universe.tradeStatus.astype(str) == '0', 'code'])
        suspended &= set(codes)
        for code in suspended:
            self.store.event(job,'当日停牌，暂不扫描',code=code,date=end)
        report['suspended'] = report['success'] = len(suspended)
        failures, processed = 0, len(suspended)
        halt = threading.Event()
        current_request = ['BaoStock · 准备行情']
        def waiting(code, operation, seconds):
            self.progress(job,processed,len(codes),f'{current_request[0]} · 等待响应 {seconds}秒')
        workers = 1  # BaoStock public rules prohibit concurrent connections.
        report['connections'] = workers
        report['timings'] = dict(history_requests=0, history_request_seconds=0.0,
                                 stock_processing_seconds=0.0,
                                 daily_batch_requests=daily_batch_request_count,
                                 daily_batch_request_seconds=daily_batch_request_seconds,
                                 daily_batch_processing_seconds=0.0)
        def timing(code, elapsed):
            for key, value in elapsed.items():
                report['timings'][key] += value
        from .sync_progress import sync_progress
        def requesting(code, operation, a, b):
            current_request[0]=sync_progress(source,operation,code,a,b)
            self.progress(job,processed,len(codes),current_request[0])
        self.progress(job,processed,len(codes),'BaoStock · 准备行情；复用已有缓存')
        selective_repair = bool(spec.get('repair_codes') and spec.get('repair_scope') != 'history'
                                and not spec.get('force'))
        repair_timeframe = spec.get('scan_timeframe', 'daily') if selective_repair else None
        report['repair_timeframe'] = repair_timeframe
        batch_saved, batch_fallback = {}, {}
        if isinstance(daily_batch, dict):
            report['daily_batch'] = dict(saved=0, fallback=len(codes), reason=daily_batch['unavailable'])
        if isinstance(daily_batch, tuple) and daily_batch_codes:
            from .baostock_daily_batch import apply_one_day_tail
            stamp = time.monotonic()
            last_batch_notice = [0.0]
            def batch_progress(done, total, code, outcome):
                moment = time.monotonic()
                if done == total or moment - last_batch_notice[0] >= .5:
                    last_batch_notice[0] = moment
                    self.progress(job, len(suspended) + done, len(codes),
                                  f'BaoStock · 校验并保存整日行情 {done}/{total} · {code}')
            try:
                batch_saved, batch_fallback = apply_one_day_tail(
                    self.store, daily_batch_codes, daily_batch[0], daily_batch[1], start, end, job,
                    on_progress=batch_progress, check_stop=lambda: self.check_stop(job))
            except InterruptedError:
                raise
            except Exception as exc:
                # Archive-level validation is an optimization failure, not a
                # market-sync failure. The established per-stock path remains
                # the source of truth and rechecks every candidate.
                batch_saved = {}
                batch_fallback = {code: '整日行情整体校验未通过' for code in daily_batch_codes}
                self.store.event(job, '整日行情整体校验未通过，自动改用逐股更新',
                                 date=end, error=str(exc))
                report['batch_fallback_reason'] = str(exc)
            report['timings']['daily_batch_processing_seconds'] = time.monotonic() - stamp
            report['updated'] += len(batch_saved)
            report['success'] += len(batch_saved)
            report['datasets'].extend(batch_saved.values())
            processed += len(batch_saved)
            report['daily_batch'] = {
                'eligible': len(daily_batch_codes), 'saved': len(batch_saved),
                'fallback': len(batch_fallback),
            }
            self.store.event(job, '整日行情处理完成', date=end,
                             **report['daily_batch'],
                             fallback_reasons=dict(list(batch_fallback.items())[:20]))
        remaining_codes = [c for c in codes if c not in suspended and c not in batch_saved]
        batch_info = report.get('daily_batch', {})
        if batch_info.get('fallback'):
            reason = report.get('batch_fallback_reason') or batch_info.get('reason')
            suffix = f'；整日回退逐股 {batch_info["fallback"]} 只' + (f'：{reason}' if reason else '')
            current_request[0] += suffix
            base_requesting = requesting
            def requesting(code, operation, a, b):
                base_requesting(code, operation, a, b)
                current_request[0] += suffix
                self.progress(job, processed, len(codes), current_request[0])
        results = sync_results(self.store, BaoStock, remaining_codes, start, end,
                               job, bool(spec.get('force') or (spec.get('repair_codes') and not selective_repair)),
                               self.cancel_flags[job], halt, workers, waiting,
                               on_request=requesting, repair_timeframe=repair_timeframe, on_timing=timing)
        last_checkpoint = 0.0
        try:
            for code,did,outcome,exc in results:
                if getattr(exc, 'latest_dataset_id', None):
                    report['datasets'].append(exc.latest_dataset_id)
                    report['partial_saved'] += 1
                if isinstance(exc, InterruptedError):
                    if getattr(exc, 'latest_dataset_id', None):
                        report['stop_reason'] = '任务已停止；最新尾部已保存，历史扩展／缺口补拉未完成'
                    continue
                processed += 1
                if exc is None:
                    report[outcome] += 1
                    report['datasets'].append(did)
                    report['success'] += 1
                    if outcome!='skipped':
                        failures = 0
                else:
                    failures += 1
                    detail=dict(code=code,**error_detail(exc,source))
                    if getattr(exc, 'latest_dataset_id', None):
                        detail['latest_dataset_id'] = exc.latest_dataset_id
                        detail['latest_saved'] = True
                        detail['error'] += '；最新尾部已保存，历史扩展／缺口补拉未完成'
                    report['errors'].append(detail)
                    self.store.event(job,'股票同步失败',**detail)
                    recent=0
                    if isinstance(exc,(ProviderError,TimeoutError)):
                        state=ProviderGuard(self.store,source).failure(exc)
                        recent=len(state['failures'])
                        report['cooldown']=state
                    if restricted(exc):
                        report['stop_reason'] = '数据源限制访问，已停止整批同步，请稍后再试'
                        halt.set()
                    elif failures >= 3 or recent>=3:
                        report['stop_reason'] = '连续三只股票下载失败，已停止，避免反复请求'
                        halt.set()
                self.progress(job,processed,len(codes),current_request[0])
                # Snapshots/coverage remain durable per stock; only the growing
                # UI report is checkpointed less often for fast cache batches.
                stamp = time.monotonic()
                if exc is not None or stamp-last_checkpoint >= 1.0:
                    self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
                    last_checkpoint = stamp
        finally:
            results.close()
            self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
        report['remaining'] = len(codes)-report['success']-len(report['errors'])
        if 'boards' in spec:
            audit = audit_scope(self.store, spec['boards'], end, source=source)
            report['coverage'] = audit
            scan_audit = audit_scope(self.store, spec['boards'], end, source=source,
                timeframe=spec.get('scan_timeframe','daily'), verify_files=True,
                check_stop=lambda:self.check_stop(job))
            report['scan_readiness'] = scan_audit
            from .history_quality import save_cached_quality
            save_cached_quality(self.store, [r['id'] for r in latest_datasets(self.store,source)], end)
            if not scan_audit['complete']:
                known = {e['code'] for e in report['errors']}
                report['errors'].extend(g for g in scan_audit['gaps'] if g['code'] not in known)
            if not audit['complete']:
                report.setdefault('stop_reason',f"已处理不等于数据已齐：应有 {audit['expected']} 只，就绪 {audit['ready']} 只，确认停牌 {audit['suspended']} 只；请查看缺口")
                existing = {e['code'] for e in report['errors']}
                report['errors'].extend(g for g in audit['gaps'] if g['code'] not in existing)
        return report

    def _sync_tickflow(self, job, spec):
        from .tickflow_service import sync_tickflow
        return sync_tickflow(self, job, spec)

    def _scan(self, job,spec):
        from .scan_reads import ScanReads
        previous = getattr(self.store, '_scan_reads', None)
        self.store._scan_reads = ScanReads()
        try:
            return self._scan_flow(job, spec)
        finally:
            self.store._scan_reads = previous

    def _scan_flow(self, job,spec):
        if 'timeframes' not in spec:
            return self._scan_one(job,spec)
        periods = spec['timeframes']
        if periods not in [['daily'], ['weekly'], ['daily','weekly']]:
            raise ValueError('请选择日线，或日线加周线')
        entries = catalog(self.store)
        if not spec['strategies']:
            raise ValueError('请至少勾选一个策略')
        groups = {tf:[key for key in spec['strategies'] if tf in entries[key].timeframes] for tf in periods}
        if any(not group for group in groups.values()):
            raise ValueError('所选周期没有可用策略，请检查勾选')
        total = len(spec['datasets']) * sum(len(v) for v in groups.values())
        audits = {}
        if spec.get('source') in SOURCES:
            total = 0
            for tf, keys in groups.items():
                self.check_stop(job)
                self.progress(job,0,0,f'扫描前核验 {tf} 行情完整性；不联网补拉')
                audits[tf] = audit_scope(self.store,spec['boards'],min(spec['asof'],completed_date()),
                    spec['datasets'],source=spec['source'],timeframe=tf,
                    verify_files=True,check_stop=lambda:self.check_stop(job),
                    progress=lambda n,count,code:self.progress(job,n,count,f'扫描前核验 {tf} · {code}'))
                total += len(audits[tf]['eligible_ids']) * len(keys)
            if spec['source'] in SOURCES:
                self.check_stop(job)
                from .history_quality import save_cached_quality
                save_cached_quality(self.store,spec['datasets'],min(spec['asof'],completed_date()))
        aggregate = {}
        for tf, keys in groups.items():
            self.check_stop(job)
            report = self._scan_one(job,dict(spec,timeframe=tf,strategies=keys),aggregate,total,
                                    coverage=audits.get(tf))
            aggregate = merge_scan_reports(aggregate,report)
            if report.get('coverage') and not report['coverage']['scan_allowed']:
                if not report['coverage']['scope_valid']:
                    break
        aggregate['timeframes'] = periods
        return aggregate

    def _scan_one(self, job,spec,previous=None,overall_total=None,coverage=None):
        previous = previous or {}
        prefetched_records = None
        # Daily market scans must not silently shrink the universe to downloaded files.
        market_scan = spec.get('source') in SOURCES
        if not market_scan and 'boards' in spec:
            prefetched_records = dataset_records(self.store, spec['datasets'])
            market_scan = any(prefetched_records.get(did, {}).get('source') in SOURCES
                              for did in spec['datasets'])
        if market_scan:
            if coverage is None:
                self.progress(job,0,0,'扫描前核验行情完整性；不联网补拉')
                coverage = audit_scope(self.store, spec['boards'], min(spec['asof'], completed_date()), spec['datasets'],
                                       source=spec.get('source'),timeframe=spec['timeframe'],
                                       verify_files=True,
                                       check_stop=lambda:self.check_stop(job),
                                       progress=lambda n,count,code:self.progress(job,n,count,f'扫描前核验 · {code}'))
            if not coverage['scan_allowed']:
                return {'success': 0, 'signals': 0, 'errors': coverage['gaps'], 'coverage': coverage,
                        'timeframe': spec['timeframe'],
                        'observation_ids': [], 'stop_reason': '行情范围或日期未齐，已拦截扫描；先到市场数据补齐缺口'}
            from .history_quality import save_cached_quality
            save_cached_quality(self.store,spec['datasets'],min(spec['asof'], completed_date()))
            spec = dict(spec, datasets=coverage['eligible_ids'])
            if not spec['datasets']:
                return {'success': 0, 'signals': 0, 'errors': [], 'coverage': coverage,
                        'timeframe': spec['timeframe'],
                        'observation_ids': [], 'note': '所选范围当日全部停牌，没有可扫描股票'}
        entries = catalog(self.store)
        strategies = [entries[k] for k in spec['strategies']]
        mtr_strategy = next((strategy for strategy in strategies if strategy.id == 'MTR_MASTER'), None)
        three_k_strategy = next((strategy for strategy in strategies
                                 if strategy.id == 'STRATEGY_3K'), None)
        mtr_fast = None
        three_k_fast = None
        # The default OFF path keeps the existing cache/checkpoint/call route.
        # Any opt-in mode remains advisory until exact bindings pass.
        if mtr_strategy is not None and mtr_requested_mode() != 'OFF':
            try:
                candidate = MTRFastRejector(self.store, job, mtr_strategy)
                mtr_fast = candidate if candidate.active else None
            except Exception as exc:
                try:
                    self.store.event(job, 'MTR快速拒绝回退原版', mode=mtr_requested_mode(),
                                     reason=f'灰度控制器不可用：{type(exc).__name__}: {exc}')
                except Exception:
                    pass  # Optional acceleration never blocks the original detector.
        if three_k_strategy is not None and three_k_requested_mode() != 'OFF':
            try:
                candidate = ThreeKFastRejector(self.store, job, three_k_strategy)
                three_k_fast = candidate if candidate.active else None
            except Exception as exc:
                try:
                    self.store.event(job, '3K快速拒绝回退原版', mode=three_k_requested_mode(),
                                     reason=f'灰度控制器不可用：{type(exc).__name__}: {exc}')
                except Exception:
                    pass  # Optional acceleration never blocks the original detector.
        timeframe = spec['timeframe']
        if not strategies or not spec['datasets']:
            raise ValueError('请选择行情和策略')
        if any(s.state!='active' or timeframe not in s.timeframes for s in strategies):
            raise ValueError('日常扫描只能使用已启用且支持所选周期的策略')
        report = {'success':0,'signals':0,'errors':[], 'no_signal':0,'observation_ids':[], 'datasets':spec['datasets'],
                  'timeframe':timeframe,
                  'strategy_versions':{s.id:s.version for s in strategies},'reused':0,'calculated':0}
        if coverage is not None:
            report['coverage'] = coverage
            if not coverage['complete']:
                report['errors'].extend(dict(g, timeframe=timeframe) for g in coverage['gaps'])
                report['stop_reason'] = (f"本周期可扫描 {coverage['ready']}/{coverage['expected']} 只；"
                                         f"排除 {len(coverage['gaps'])} 只，详情见回执；不是全范围扫描完成")
        total = len(spec['datasets'])*len(strategies)
        done = 0
        asof = min(spec['asof'],completed_date())
        # Only calculation semantics belong in the cache fingerprint.  Progress
        # wording and orchestration changes in this service must not invalidate
        # every strategy result on the next daily run.
        from .scan_cache import calculation_versions, cache_key, cached_answers, finished_scan, compatible_engines
        versions = calculation_versions(strategies)
        compatible = compatible_engines(strategies)
        engine = scan_engine_version(include_frozen=False)
        legacy_engine = scan_engine_version()
        checkpoint_engine = mtr_fast.cache_engine(engine) if mtr_fast and mtr_fast.active else engine
        if three_k_fast and three_k_fast.active:
            checkpoint_engine = three_k_fast.cache_engine(checkpoint_engine)
        checkpoint = digest(dumps([spec.get('source'), sorted(spec['datasets']),
            sorted((s.id,s.version,versions[s.id]) for s in strategies),timeframe,asof,checkpoint_engine]).encode())
        report.update(engine_version=engine,calculation_versions=versions,asof=asof,total=total)
        lookups = []
        lookup_keys = []
        for did in spec['datasets']:
            def bound_engine(strategy, base):
                if strategy.id == 'MTR_MASTER' and mtr_fast and mtr_fast.active:
                    return mtr_fast.cache_engine(base)
                if strategy.id == 'STRATEGY_3K' and three_k_fast and three_k_fast.active:
                    return three_k_fast.cache_engine(base)
                return base

            keys = {s.id:cache_key(did,s.id,versions[s.id],timeframe,asof,bound_engine(s,engine))
                    for s in strategies}
            legacy = {s.id:cache_key(did,s.id,s.version,timeframe,asof,bound_engine(s,legacy_engine))
                      for s in strategies}
            bridged = {s.id:cache_key(did,s.id,versions[s.id],timeframe,asof,
                                     bound_engine(s,compatible[s.id]))
                       for s in strategies if s.id in compatible}
            lookups.append((keys, legacy, bridged))
            lookup_keys.extend([*keys.values(), *legacy.values(), *bridged.values()])
        answer_keys = [key for keys, _legacy, _bridged in lookups for key in keys.values()]
        complete = None if spec.get('force') else finished_scan(self.store,checkpoint,timeframe,answer_keys)
        if complete is not None:
            # Market scans just verified all files in their actual audit. Direct
            # imports need the same tamper check before their stage is reused.
            if coverage is None:
                try:
                    if prefetched_records is None:
                        prefetched_records = dataset_records(self.store, spec['datasets'])
                    for did in spec['datasets']:
                        self.check_stop(job)
                        record = prefetched_records.get(did)
                        if record is None:
                            raise ValueError('行情快照不存在')
                        verify_dataset(self.store,did,job,record=record)
                except (OSError,ValueError):
                    complete = None  # Original per-stock error/exclusion path.
        if complete is not None:
            self.check_stop(job)
            report.update(success=total,reused=total,signals=complete['signals'],
                          no_signal=total-complete['signals'],observation_ids=complete['observation_ids'],
                          checkpoints={timeframe:complete},stage_reused=True)
            self.store.event(job,'复用已完成扫描阶段',source=spec.get('source'),timeframe=timeframe,
                             asof=asof,judgments=total)
            self.progress(job,previous.get('total',0)+total,overall_total or total,
                          f'{timeframe} 已完成判断直接复用 {total} 次，无需重新扫描')
            return report
        begin_receipt(self.store, job, merge_scan_reports(previous, report), timeframe)
        prefetched_signals = {} if spec.get('force') else cached_answers(self.store, lookup_keys)
        del lookup_keys  # Keep only rows that actually exist, not every candidate key.
        metadata_batch_failed = False
        for did, (keys, legacy, bridged) in zip(spec['datasets'], lookups):
            self.check_stop(job)
            event_rows, cache_rows, observation_rows = [], [], []

            def queue_event(action, path='', **detail):
                event_rows.append((now(), job, action, str(path), dumps(detail)))

            stock_lookup_keys = [*keys.values(), *legacy.values(), *bridged.values()]
            cached_signals = {key:prefetched_signals[key] for key in stock_lookup_keys
                              if key in prefetched_signals}
            for s in strategies:
                if keys[s.id] not in cached_signals and legacy[s.id] in cached_signals:
                    cached_signals[keys[s.id]] = cached_signals[legacy[s.id]]
                if keys[s.id] not in cached_signals and bridged.get(s.id) in cached_signals:
                    cached_signals[keys[s.id]] = cached_signals[bridged[s.id]]
            if mtr_fast and mtr_fast.fused and mtr_strategy is not None:
                cached_signals.pop(keys[mtr_strategy.id], None)
            if three_k_fast and three_k_fast.fused and three_k_strategy is not None:
                cached_signals.pop(keys[three_k_strategy.id], None)
            all_cached = all(key in cached_signals for key in keys.values())
            try:
                if prefetched_records is None and not metadata_batch_failed:
                    try:
                        prefetched_records = dataset_records(self.store, spec['datasets'])
                    except Exception:
                        # The former path exposed a metadata read failure on the
                        # current stock, then retried later stocks one by one.
                        metadata_batch_failed = True
                        raise
                record = (prefetched_records or {}).get(did)
                if record is None and not metadata_batch_failed:
                    raise ValueError('行情快照不存在')
                if all_cached:
                    record = verify_dataset(self.store, did, job, record=record)
                    data = None
                else:
                    data,record = load_dataset(self.store,did,job,record=record)
                    data = prepare(data,timeframe,asof)
                    data.attrs['code'] = record['code']
                data_error = None
            except Exception as exc:
                data_error = str(exc)
                record = {'code':did}
            self.progress(job,previous.get('total',0)+done,overall_total or total,
                          f"{timeframe} · {record['code']} · {len(strategies)} 个策略")

            eligible = not data_error and (all_cached or len(data) >= 125)
            if not eligible:
                cached_signals = {}
            prepared = None
            prepare_error = None
            if eligible and any(keys[strategy.id] not in cached_signals for strategy in strategies):
                # TSP's most useful pattern for Aplus: one stock is enriched
                # once, then each unchanged detector receives its own copy.
                try:
                    prepared = prepare_indicators(data)
                except Exception as exc:
                    prepare_error = str(exc)
            stock_done = 0
            for strategy in strategies:
                if self.cancel_flags[job].is_set():
                    break  # Commit finished judgments before honouring stop.
                accelerated_mtr = strategy.id == 'MTR_MASTER' and mtr_fast is not None
                accelerated_three_k = strategy.id == 'STRATEGY_3K' and three_k_fast is not None
                mtr_original_called = False
                mtr_call_event_queued = False
                three_k_original_called = False
                three_k_call_event_queued = False
                try:
                    if data_error:
                        raise ValueError(data_error)
                    if not all_cached and len(data)<125:
                        raise ValueError(f'已完成 K 线仅 {len(data)} 根，至少需要 125 根')
                    key = keys[strategy.id]
                    fused_acceleration = ((accelerated_mtr and mtr_fast.fused)
                                          or (accelerated_three_k and three_k_fast.fused))
                    if key in cached_signals and not fused_acceleration:
                        signal = json.loads(cached_signals[key])
                        report['reused'] += 1
                        queue_event('复用已完成策略判断',strategy.file,code=record['code'],key=key)
                    else:
                        if prepare_error:
                            raise ValueError(prepare_error)
                        if accelerated_mtr:
                            queue_event('MTR灰度门禁判断', strategy.file, strategy=strategy.id,
                                        code=record['code'], dataset=did,
                                        mode=mtr_fast.requested_mode, binding=mtr_fast.binding_id)

                            def original_mtr():
                                nonlocal mtr_original_called
                                mtr_original_called = True
                                return signal_at_end(strategy, data, prepared=prepared)

                            fast_result = mtr_fast.run(
                                original_mtr,
                                data, prepared, f'{did}|{timeframe}|{asof}|{key}')
                            signal = fast_result.signal
                            if mtr_original_called:
                                queue_event('调用策略', strategy.file, strategy=strategy.id,
                                            code=record['code'], dataset=did)
                                mtr_call_event_queued = True
                            if fast_result.event_action:
                                queue_event(fast_result.event_action, strategy.file,
                                            code=record['code'], dataset=did,
                                            **(fast_result.event_detail or {}))
                            if (mtr_fast.fused and mtr_fast.fuse_persist_error
                                    and mtr_fast.fuse_evidence):
                                queue_event('MTR快速拒绝熔断', strategy.file,
                                            **mtr_fast.fuse_evidence,
                                            persistence_error=mtr_fast.fuse_persist_error)
                        elif accelerated_three_k:
                            queue_event('3K灰度门禁判断', strategy.file, strategy=strategy.id,
                                        code=record['code'], dataset=did,
                                        mode=three_k_fast.requested_mode,
                                        binding=three_k_fast.binding_id)

                            def original_three_k():
                                nonlocal three_k_original_called
                                three_k_original_called = True
                                return signal_at_end(strategy, data, prepared=prepared)

                            fast_result = three_k_fast.run(
                                original_three_k,
                                data, prepared, f'{did}|{timeframe}|{asof}|{key}')
                            signal = fast_result.signal
                            if three_k_original_called:
                                queue_event('调用策略', strategy.file, strategy=strategy.id,
                                            code=record['code'], dataset=did)
                                three_k_call_event_queued = True
                            if fast_result.event_action:
                                queue_event(fast_result.event_action, strategy.file,
                                            code=record['code'], dataset=did,
                                            **(fast_result.event_detail or {}))
                            if (three_k_fast.fused and three_k_fast.fuse_persist_error
                                    and three_k_fast.fuse_evidence):
                                queue_event('3K快速拒绝熔断', strategy.file,
                                            **three_k_fast.fuse_evidence,
                                            persistence_error=three_k_fast.fuse_persist_error)
                        else:
                            queue_event('调用策略',strategy.file,strategy=strategy.id,
                                        code=record['code'],dataset=did)
                            signal = signal_at_end(strategy,data,prepared=prepared)
                        report['calculated'] += 1
                    if signal:
                        identity = digest(f"{did}|{strategy.id}|{strategy.version}|{timeframe}|{signal['asof']}".encode())
                        if signal.get('plan_version'):
                            # Keep earlier archived observations intact on a plan-layer
                            # upgrade; Store still keys manual plans by original setup.
                            identity = digest((identity + signal['plan_version']).encode())
                        observation_rows.append(
                            (identity,job,record['code'],strategy.id,strategy.version,timeframe,signal['asof'],
                             signal['setup_date'],did,dumps(signal),now()))
                        report['signals'] += 1
                        report['observation_ids'].append(identity)
                        queue_event('记录策略命中',self.store.path,observation=identity,code=record['code'])
                    else:
                        report['no_signal'] += 1
                    if not ((accelerated_mtr and mtr_fast.fused)
                            or (accelerated_three_k and three_k_fast.fused)):
                        cache_rows.append((key,dumps(signal),now()))
                    report['success'] += 1
                except Exception as exc:
                    if accelerated_mtr and mtr_original_called and not mtr_call_event_queued:
                        queue_event('调用策略', strategy.file, strategy=strategy.id,
                                    code=record['code'], dataset=did)
                    if (accelerated_three_k and three_k_original_called
                            and not three_k_call_event_queued):
                        queue_event('调用策略', strategy.file, strategy=strategy.id,
                                    code=record['code'], dataset=did)
                    if (accelerated_mtr and mtr_fast.fused and mtr_fast.fuse_persist_error
                            and mtr_fast.fuse_evidence):
                        queue_event('MTR快速拒绝熔断', strategy.file,
                                    **mtr_fast.fuse_evidence,
                                    persistence_error=mtr_fast.fuse_persist_error)
                    if (accelerated_three_k and three_k_fast.fused
                            and three_k_fast.fuse_persist_error
                            and three_k_fast.fuse_evidence):
                        queue_event('3K快速拒绝熔断', strategy.file,
                                    **three_k_fast.fuse_evidence,
                                    persistence_error=three_k_fast.fuse_persist_error)
                    report['errors'].append({'code':record['code'],'strategy':strategy.id,'timeframe':timeframe,'error':str(exc)})
                    queue_event('策略计算失败',strategy.file,code=record['code'],strategy=strategy.id,
                                timeframe=timeframe,error=str(exc))
                stock_done += 1
            done += stock_done
            # One durable checkpoint per stock replaces dozens of tiny SQLite
            # connections/commits while preserving the same cache, observation
            # and audit records.
            _scan_crash_probe('before_stock_transaction')
            with self.store.connect() as db:
                if observation_rows:
                    db.executemany('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                   observation_rows)
                if cache_rows:
                    db.executemany('INSERT OR IGNORE INTO scan_cache VALUES(?,?,?)', cache_rows)
                if event_rows:
                    db.executemany('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)', event_rows)
                merged_report = merge_scan_reports(previous, report)
                db.execute('UPDATE jobs SET progress=?,total=?,result=?,message=? WHERE id=?',
                    (previous.get('total',0)+done,overall_total or total,dumps(progress_receipt(merged_report, timeframe)),
                     f"{timeframe} 已处理 {done}/{total} 次 · 复用 {report['reused']} · 新计算 {report['calculated']}",job))
                _scan_crash_probe('inside_stock_transaction')
            _scan_crash_probe('after_stock_commit')
            if not spec.get('force'):
                prefetched_signals.update((key, signal) for key, signal, _created in cache_rows)
            self.check_stop(job)
        if (report['success'] == total
                and not (mtr_fast and mtr_fast.fused)
                and not (three_k_fast and three_k_fast.fused)):
            report['checkpoints'] = {timeframe:dict(identity=checkpoint,signals=report['signals'],
                                                  observation_ids=report['observation_ids'])}
        return report

    def _backtest(self, job,spec):
        historical_sources={r['source'] for did in spec.get('datasets',[]) for r in
            self.store.rows('SELECT source FROM datasets WHERE id=?',(did,)) if r['source'] in SOURCES}
        if len(historical_sources)>1:
            raise ValueError('一次回测只能使用一个行情来源；三源快照和报告独立，不混合比较收益')
        if spec.get('execution_model') == 'gap-h2-stop-comparison-v1':
            from .stop_research_service import execute_experiment
            return execute_experiment(self, job, spec)
        if spec.get('execution_model') == 'gap-h2-daily-execution-v1':
            from .h2_replay_service import execute_replay
            return execute_replay(self, job, spec)
        strategy = catalog(self.store)[spec['strategy']]
        tf = spec['timeframe']
        if tf not in strategy.timeframes:
            raise ValueError('策略不支持所选周期')
        if not spec['datasets'] or spec['start'] > spec['end']:
            raise ValueError('需要有效的股票范围和起止日期')
        a = Assumptions(**spec['assumptions'])
        a.validate()
        records,errors,snapshots = [],[],[]
        for n,did in enumerate(spec['datasets']):
            self.check_stop(job)
            try:
                data,record = load_dataset(self.store,did,job)
                snapshots.append(record)
                from .history_quality import require_research_history
                require_research_history(self.store, record, min(spec['end'], completed_date()),spec['start'])
                data = prepare(data,tf,min(spec['end'],completed_date()))
                if len(data)<125:
                    raise ValueError('不足 125 根已完成 K 线')
                def update(i,total):
                    self.progress(job,n*100+(100*i//max(total,1)),len(spec['datasets'])*100,
                                  f"回放 {record['code']}：{i}/{total} 根；每次只给策略当时可见的行情")
                trades = run_study(strategy,data,spec['start'],spec['end'],a,update,self.cancel_flags[job].is_set)
                for trade in trades:
                    trade.update(code=record['code'],dataset_id=did)
                records.extend(trades)
            except InterruptedError:
                raise
            except Exception as exc:
                errors.append({'dataset':did,'error':str(exc)})
        report = summarize(records)
        report.update(errors=errors, strategy_version=strategy.version, assumptions=asdict(a),
                      datasets=snapshots, real_data=bool(snapshots) and all(r['source']!='demo' for r in snapshots),
                      engine_version=digest(b''.join((ROOT/'workbench'/f).read_bytes() for f in ['backtest.py','strategies.py','market.py'])),
                      execution_model='signal-study-v1-fixed-next-bar',timeframe=tf,
                      limitations=['不是组合收益；不模拟仓位资金约束','历史快照不是严格的当时数据版本；腾讯不复权回测不包含分红及除权股数调整',
                                   '不保证涨跌停、停牌、流动性及排队成交；单一价格 K 线不成交',
                                   '每个形态只研究首次观察时的固定入场/止损/第一目标；不复刻动态挂单',
                                   '同根止盈止损按止损；最早下一根退出，周/月线因此更保守',
                                   '今天的股票名单存在幸存者偏差；需自行补入退市历史样本'])
        rel = f'research/{job}'
        self.store.write_artifact(rel+'/trades.csv',pd.DataFrame(records).to_csv(index=False).encode('utf-8-sig'),job)
        self.store.write_artifact(rel+'/report.json',dumps(report).encode('utf-8'),job)
        report['report_path'] = rel+'/report.json'
        report['trades_path'] = rel+'/trades.csv'
        return report

    def _validate(self, job,spec):
        strategy = catalog(self.store)[spec['strategy']]
        frame,record = load_dataset(self.store,spec['dataset'],job)
        data = prepare(frame,spec['timeframe'],completed_date())
        if len(data)<125:
            raise ValueError('验证至少需要 125 根已完成 K 线')
        errors, checks = [],[]
        for count in sorted(set([125,min(200,len(data)),min(300,len(data)),len(data)])):
            self.check_stop(job)
            prefix = data.iloc[:count].copy()
            before = prefix.copy(deep=True)
            try:
                _,first = calculate(strategy,prefix)
                _,second = calculate(strategy,prefix)
                pd.testing.assert_frame_equal(first,second)
                pd.testing.assert_frame_equal(prefix,before)
                checks.append(f'{count} 根：接口、重复计算一致性、输入保护通过')
            except Exception as exc:
                errors.append(f'{count} 根：{exc}')
        report = {'checks':checks,'errors':errors,'strategy_version':strategy.version,'timeframe':spec['timeframe'],
                  'note':'这是工程接口验证，不证明策略有交易优势，也不能证明外部代码没有访问未来数据。'}
        self.store.execute('INSERT INTO validations VALUES(?,?,?,?,?,?,?)',
                          (uuid.uuid4().hex,strategy.id,strategy.version,job,int(not errors),dumps(report),now()))
        return report


def import_csv(store,content,code,adjustment):
    frame = validate_bars(pd.read_csv(io.BytesIO(content)))
    if frame.date.max() > completed_date():
        raise ValueError('CSV 含未完成或未来日期，请只导入已完成行情')
    return save_dataset(store,code,frame,'csv',adjustment)


def demo_data(store):
    rng = np.random.default_rng(20260923)
    dates = pd.bdate_range(end='2025-12-31',periods=650)
    results = []
    for k,code in enumerate(['sh.600000','sz.000001','sz.300750']):
        close = 20*np.exp(np.cumsum(rng.normal(0.0005,0.018,len(dates))))
        opening = np.r_[close[0],close[:-1]]*(1+rng.normal(0,0.004,len(dates)))
        frame = pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'open':opening,'close':close,
                              'high':np.maximum(opening,close)*1.012,'low':np.minimum(opening,close)*0.988,
                              'volume':rng.integers(100000,800000,len(dates)).astype(float)})
        results.append(save_dataset(store,code,frame,'demo','合成演示，不是真实行情'))
    return results
