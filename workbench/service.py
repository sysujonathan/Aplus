"""Application use cases and a single-worker job queue with durable receipts."""
from __future__ import annotations

import io
import json
import threading
import traceback
import uuid
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import numpy as np
import pandas as pd

from .backtest import Assumptions, run_study, summarize
from .market import BaoStock, completed_date, load_dataset, save_dataset, validate_bars, select_board_codes
from .store import ROOT, digest, dumps, now
from .strategies import calculate, catalog, prepare, signal_at_end, verify_frozen
from .sync import sync_stock


class Service:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='a-workbench')
        self.cancel_flags = {}
        self.lock = threading.Lock()
        store.execute("UPDATE jobs SET status='interrupted',finished=?,message='程序重启；任务未完成，可重新发起' "
                      "WHERE status IN ('queued','running')", (now(),))

    def submit(self, kind, spec):
        if kind not in {'sync','scan','backtest','validate','universe'}:
            raise ValueError('未知任务类型')
        if kind in {'scan','backtest','validate'}:
            verify_frozen()
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
            state = 'partial' if report.get('errors') else 'completed'
            if self.cancel_flags[job].is_set():
                state = 'cancelled'
            self.store.execute('UPDATE jobs SET status=?,finished=?,result=?,message=? WHERE id=?',
                               (state,now(),dumps(report),'完成' if state=='completed' else report.get('stop_reason','请查看回执中的未完成项目'),job))
        except InterruptedError as exc:
            self.store.execute("UPDATE jobs SET status='cancelled',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        except Exception as exc:
            self.store.event(job,'任务异常',error=str(exc),traceback=traceback.format_exc())
            self.store.execute("UPDATE jobs SET status='failed',finished=?,message=? WHERE id=?", (now(),str(exc),job))
        finally:
            self.store.event(job,'任务结束',self.store.path)
            self.cancel_flags.pop(job,None)

    def check_stop(self, job):
        if self.cancel_flags[job].is_set():
            raise InterruptedError('任务已停止；已保存的记录保留，但不宣称全部完成')

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
                    self.store.write_artifact('universe.csv',result.to_csv(index=False).encode('utf-8-sig'),job)
                    return {'stocks':len(result),'date':str(day-timedelta(days=n))}
        raise ValueError('最近十天未取得股票名单，请检查行情服务')

    def _sync(self, job,spec):
        start,end = spec['start'],min(spec['end'],completed_date())
        if start > end:
            raise ValueError('同步开始日期不能晚于已完成行情日期')
        if 'boards' in spec:
            if not spec['boards']:
                raise ValueError('请至少勾选一个板块')
            if not (self.store.root/'universe.csv').exists():
                self.progress(job,0,1,'正在获取股票目录')
                self._universe(job, {'date':end})
            universe = pd.read_csv(io.BytesIO(self.store.read_artifact('universe.csv',job)))
            codes = select_board_codes(universe,spec['boards'])
        else:
            codes = list(dict.fromkeys(spec['codes']))
        if not codes:
            raise ValueError('请先选择股票范围')
        report = {'requested':len(codes),'success':0,'errors':[], 'datasets':[], 'end_requested':end,
                  'skipped':0,'downloaded':0,'updated':0,'refreshed':0}
        report['boards'] = spec.get('boards',[])
        with ExitStack() as stack:
            connection = None
            failures = 0
            def provider():
                nonlocal connection
                if connection is None:
                    candidate = BaoStock()
                    candidate.cancel_event = self.cancel_flags[job]
                    candidate.on_wait = lambda op,secs: self.progress(job,i,len(codes),f'{code} 等待行情响应 {secs} 秒，超时将结束请求')
                    connection = stack.enter_context(candidate)
                return connection
            for i,code in enumerate(codes):
                if self.cancel_flags[job].is_set():
                    break
                self.progress(job,i,len(codes),f'检查 {code}，已有行情跳过，缺少部分补齐')
                try:
                    did, outcome = sync_stock(self.store,provider,code,start,end,job,spec.get('force',False))
                    report[outcome] += 1
                    report['datasets'].append(did)
                    report['success'] += 1
                    failures = 0
                except InterruptedError:
                    break
                except Exception as exc:
                    failures += 1
                    report['errors'].append({'code':code,'error':str(exc)})
                    self.store.event(job,'股票同步失败',code=code,error=str(exc))
                    if '黑名单' in str(exc) or 'blacklist' in str(exc).lower():
                        report['stop_reason'] = '数据源限制访问，已停止整批同步，请稍后再试'
                        break
                    if failures >= 3:
                        report['stop_reason'] = '连续三只股票下载失败，已停止，避免反复请求'
                        break
                self.progress(job,i+1,len(codes),f"已处理 {i+1}/{len(codes)} · 跳过 {report['skipped']} · "
                              f"新下载 {report['downloaded']} · 补齐 {report['updated']} · 历史刷新 {report['refreshed']}")
                self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
        report['remaining'] = len(codes)-report['success']-len(report['errors'])
        return report

    def _scan(self, job,spec):
        entries = catalog(self.store)
        strategies = [entries[k] for k in spec['strategies']]
        timeframe = spec['timeframe']
        if not strategies or not spec['datasets']:
            raise ValueError('请选择行情和策略')
        if any(s.state!='active' or timeframe not in s.timeframes for s in strategies):
            raise ValueError('日常扫描只能使用已启用且支持所选周期的策略')
        report = {'success':0,'signals':0,'errors':[], 'no_signal':0,'observation_ids':[], 'datasets':spec['datasets'],
                  'strategy_versions':{s.id:s.version for s in strategies},'reused':0,'calculated':0}
        total = len(spec['datasets'])*len(strategies)
        done = 0
        asof = min(spec['asof'],completed_date())
        engine = digest(b''.join((ROOT/'workbench'/name).read_bytes() for name in
                                ['strategies.py','market.py','service.py']) + verify_frozen().encode())
        report.update(engine_version=engine,asof=asof,total=total)
        self.store.execute('UPDATE jobs SET result=? WHERE id=?',(dumps(report),job))
        for did in spec['datasets']:
            self.check_stop(job)
            try:
                data,record = load_dataset(self.store,did,job)
                data = prepare(data,timeframe,asof)
                data_error = None
            except Exception as exc:
                data_error = str(exc)
                record = {'code':did}
            for strategy in strategies:
                self.check_stop(job)
                self.progress(job,done,total,f"匹配 {record['code']} · {strategy.name}")
                try:
                    if data_error:
                        raise ValueError(data_error)
                    if len(data)<125:
                        raise ValueError(f'已完成 K 线仅 {len(data)} 根，至少需要 125 根')
                    key = digest(dumps([did,strategy.id,strategy.version,timeframe,asof,engine]).encode())
                    cached = self.store.rows('SELECT signal FROM scan_cache WHERE key=?',(key,))
                    if cached:
                        signal = json.loads(cached[0]['signal'])
                        report['reused'] += 1
                        self.store.event(job,'复用已完成策略判断',strategy.file,code=record['code'],key=key)
                    else:
                        self.store.event(job,'调用策略',strategy.file,strategy=strategy.id,code=record['code'],dataset=did)
                        signal = signal_at_end(strategy,data)
                        report['calculated'] += 1
                    if signal:
                        identity = digest(f"{did}|{strategy.id}|{strategy.version}|{timeframe}|{signal['asof']}".encode())
                        self.store.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (identity,job,record['code'],strategy.id,strategy.version,timeframe,signal['asof'],
                             signal['setup_date'],did,dumps(signal),now()))
                        report['signals'] += 1
                        report['observation_ids'].append(identity)
                        self.store.event(job,'记录策略命中',self.store.path,observation=identity,code=record['code'])
                    else:
                        report['no_signal'] += 1
                    self.store.execute('INSERT OR IGNORE INTO scan_cache VALUES(?,?,?)',(key,dumps(signal),now()))
                    report['success'] += 1
                except Exception as exc:
                    report['errors'].append({'code':record['code'],'strategy':strategy.id,'error':str(exc)})
                    self.store.event(job,'策略计算失败',strategy.file,code=record['code'],error=str(exc))
                done += 1
                self.store.execute('UPDATE jobs SET progress=?,total=?,result=?,message=? WHERE id=?',
                    (done,total,dumps(report),f"已处理 {done}/{total} 次 · 复用 {report['reused']} · 新计算 {report['calculated']}",job))
        return report

    def _backtest(self, job,spec):
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
                      limitations=['不是组合收益；不模拟仓位资金约束','前复权历史快照不是严格的当时数据版本',
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
