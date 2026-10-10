"""Application use cases and a single-worker job queue with durable receipts."""
from __future__ import annotations

import io
import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import numpy as np
import pandas as pd

from .backtest import Assumptions, run_study, summarize
from .market import BaoStock, completed_date, load_dataset, save_dataset, validate_bars, select_board_codes, latest_datasets
from .store import ROOT, digest, dumps, now
from .strategies import (calculate, catalog, prepare, prepare_indicators, scan_engine_version,
                         signal_at_end, verify_frozen)
from .sync import sync_stock
from .readiness import audit_scope, expected_day, save_calendar, save_directory
from .sync_batch import sync_results
from .sources import market_source, SOURCES, coverage_state, FALLBACK_SOURCES
from .provider_guard import ProviderGuard, ProviderError, restricted, error_detail


def merge_scan_reports(previous, current):
    merged = dict(previous, **current)
    for key in ['success','signals','no_signal','reused','calculated','total']:
        merged[key] = previous.get(key,0) + current.get(key,0)
    for key in ['errors','observation_ids']:
        merged[key] = previous.get(key,[]) + current.get(key,[])
    merged['strategy_versions'] = dict(previous.get('strategy_versions',{}), **current.get('strategy_versions',{}))
    coverage = current.get('coverage')
    if coverage and coverage.get('source') in SOURCES and current.get('timeframe'):
        merged['coverage_by_timeframe'] = dict(previous.get('coverage_by_timeframe',{}),
                                               **{current['timeframe']:coverage})
    return merged


class Service:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='a-workbench')
        self.cancel_flags = {}
        self.lock = threading.Lock()
        store.execute("UPDATE jobs SET status='interrupted',finished=?,message='程序重启；任务未完成，可重新发起' "
                      "WHERE status IN ('queued','running')", (now(),))

    def submit(self, kind, spec):
        if kind not in {'sync','scan','backtest','validate','universe','verify_status'}:
            raise ValueError('未知任务类型')
        if kind in {'scan','backtest','validate'}:
            verify_frozen()
        if kind=='sync':
            spec=dict(spec,source=spec.get('source') or market_source(self.store))
            if spec['source'] not in SOURCES:
                raise ValueError('未知日 K 数据源')
            ProviderGuard(self.store,spec['source']).check()
        with self.lock:
            if self.store.rows("SELECT id FROM jobs WHERE status IN ('running','queued')"):
                raise ValueError('已有任务正在运行。请等待结束或停止后再开始，避免争用行情连接')
            job = uuid.uuid4().hex[:16]
            self.store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                               (job,kind,'queued',now(),dumps(spec)))
            self.cancel_flags[job] = threading.Event()
            self.pool.submit(self._execute, job,kind,spec)
        return job

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
            report = getattr(self,'_'+kind)(job,spec)
            state = 'partial' if report.get('errors') or report.get('complete') is False else 'completed'
            if self.cancel_flags[job].is_set():
                state = 'cancelled'
            self.store.execute('UPDATE jobs SET status=?,finished=?,result=?,message=? WHERE id=?',
                               (state,now(),dumps(report),'完成' if state=='completed' else report.get('stop_reason','请查看回执中的未完成项目'),job))
        except InterruptedError as exc:
            self.store.execute("UPDATE jobs SET status='cancelled',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        except Exception as exc:
            source=spec.get('source','baostock')
            detail=error_detail(exc,source)
            if kind in ('sync','verify_status') and isinstance(exc,(ProviderError,TimeoutError)):
                if kind == 'verify_status':
                    source = 'baostock'
                detail['cooldown']=ProviderGuard(self.store,source).failure(exc)
            self.store.event(job,'任务异常',**detail,traceback=traceback.format_exc())
            self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps({'errors':[detail],'stop_reason':str(exc),'source':source}),job))
            self.store.execute("UPDATE jobs SET status='failed',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        finally:
            self.store.event(job,'任务结束',self.store.path)
            self.cancel_flags.pop(job,None)

    def check_stop(self, job):
        if self.cancel_flags[job].is_set():
            raise InterruptedError('任务已停止；已保存的记录保留，但不宣称全部完成')

    def _verify_status(self, job, spec):
        from .status_review import review_job
        return review_job(self, job, spec)

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
        source=spec.get('source','baostock')
        ProviderGuard(self.store,source).check()
        if source=='tickflow':
            return self._sync_tickflow(job,spec)
        if source=='tencent':
            from .tencent_service import sync_tencent
            return sync_tencent(self,job,spec)
        # end=None 表示“直到最新交易日”。completed_date() 返回字符串，不能与 None
        # 直接 min（会报 "'<' not supported between str and NoneType"），故先归一化。
        end = completed_date() if not spec.get('end') else min(spec['end'], completed_date())
        start = spec['start']
        if start > end:
            raise ValueError('同步开始日期不能晚于已完成行情日期')
        scope_reused = False
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
                    basics = candidate.basics()
                    save_directory(self.store, directory, end, job, basics=basics)
                universe = pd.read_csv(io.BytesIO(self.store.read_artifact('universe.csv',job)))
            codes = select_board_codes(universe,spec['boards'])
        else:
            codes = list(dict.fromkeys(spec['codes']))
        if spec.get('repair_codes'):
            # Explicit UI selection only; provider connection/protection unchanged.
            requested = set(spec['repair_codes'])
            if not requested.issubset(codes):
                raise ValueError('所选补拉股票不属于当前来源／板块目录')
            codes = [code for code in codes if code in requested]
        if not codes:
            raise ValueError('请先选择股票范围')
        report = {'source':source,'requested':len(codes),'success':0,'errors':[], 'datasets':[], 'end_requested':end,
                  'skipped':0,'downloaded':0,'updated':0,'refreshed':0,'suspended':0}
        report['boards'] = spec.get('boards',[])
        report['scope_reused'] = scope_reused
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
        from .sync_progress import sync_progress
        def requesting(code, operation, a, b):
            current_request[0]=sync_progress(source,operation,code,a,b)
            self.progress(job,processed,len(codes),current_request[0])
        self.progress(job,processed,len(codes),'BaoStock · 准备行情；复用已有缓存')
        results = sync_results(self.store, BaoStock, [c for c in codes if c not in suspended], start, end,
                               job, bool(spec.get('force') or spec.get('repair_codes')), self.cancel_flags[job], halt, workers, waiting,
                               on_request=requesting)
        try:
            for code,did,outcome,exc in results:
                if isinstance(exc, InterruptedError):
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
                self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
        finally:
            results.close()
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
        # Daily market scans must not silently shrink the universe to downloaded files.
        if spec.get('source') in SOURCES or ('boards' in spec and any(
                r['source'] in SOURCES for did in spec['datasets']
                for r in self.store.rows('SELECT source FROM datasets WHERE id=?', (did,)))):
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
        engine = scan_engine_version()
        report.update(engine_version=engine,asof=asof,total=total)
        self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(merge_scan_reports(previous,report)),job))
        for did in spec['datasets']:
            self.check_stop(job)
            event_rows, cache_rows, observation_rows = [], [], []

            def queue_event(action, path='', **detail):
                event_rows.append((now(), job, action, str(path), dumps(detail)))

            try:
                data,record = load_dataset(self.store,did,job)
                data = prepare(data,timeframe,asof)
                data.attrs['code'] = record['code']
                data_error = None
            except Exception as exc:
                data_error = str(exc)
                record = {'code':did}
            self.progress(job,previous.get('total',0)+done,overall_total or total,
                          f"{timeframe} · {record['code']} · {len(strategies)} 个策略")

            keys = {strategy.id: digest(dumps(
                [did,strategy.id,strategy.version,timeframe,asof,engine]).encode())
                for strategy in strategies}
            cached_signals = {}
            eligible = not data_error and len(data) >= 125
            if eligible:
                placeholders = ','.join('?' for _ in keys)
                rows = self.store.rows(
                    f'SELECT key,signal FROM scan_cache WHERE key IN ({placeholders})', tuple(keys.values()))
                cached_signals = {row['key']: row['signal'] for row in rows}
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
                try:
                    if data_error:
                        raise ValueError(data_error)
                    if len(data)<125:
                        raise ValueError(f'已完成 K 线仅 {len(data)} 根，至少需要 125 根')
                    key = keys[strategy.id]
                    if key in cached_signals:
                        signal = json.loads(cached_signals[key])
                        report['reused'] += 1
                        queue_event('复用已完成策略判断',strategy.file,code=record['code'],key=key)
                    else:
                        if prepare_error:
                            raise ValueError(prepare_error)
                        queue_event('调用策略',strategy.file,strategy=strategy.id,code=record['code'],dataset=did)
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
                    cache_rows.append((key,dumps(signal),now()))
                    report['success'] += 1
                except Exception as exc:
                    report['errors'].append({'code':record['code'],'strategy':strategy.id,'timeframe':timeframe,'error':str(exc)})
                    queue_event('策略计算失败',strategy.file,code=record['code'],strategy=strategy.id,error=str(exc))
                stock_done += 1
            done += stock_done
            # One durable checkpoint per stock replaces dozens of tiny SQLite
            # connections/commits while preserving the same cache, observation
            # and audit records.
            with self.store.connect() as db:
                if observation_rows:
                    db.executemany('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                   observation_rows)
                if cache_rows:
                    db.executemany('INSERT OR IGNORE INTO scan_cache VALUES(?,?,?)', cache_rows)
                if event_rows:
                    db.executemany('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)', event_rows)
                db.execute('UPDATE jobs SET progress=?,total=?,result=?,message=? WHERE id=?',
                    (previous.get('total',0)+done,overall_total or total,dumps(merge_scan_reports(previous,report)),
                     f"{timeframe} 已处理 {done}/{total} 次 · 复用 {report['reused']} · 新计算 {report['calculated']}",job))
            self.check_stop(job)
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
