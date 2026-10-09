"""Durable research-only receipts using the existing job and artifact store."""
from dataclasses import asdict
from datetime import date
import json
import time

import pandas as pd

from .backtest import Assumptions
from .h2_replay import H2Settings, MODEL, run_replay, summarize_replay
from .h2_replay_cache import cache_key, engine_version, read_cache, write_cache
from .market import BOARDS, board_of, completed_date, load_dataset
from .store import digest, dumps
from .strategies import catalog, prepare
from .sources import market_source, SOURCES


def available_snapshots(store, scope='market', boards=None):
    """One immutable daily snapshot per code; synthetic demo is never eligible."""
    if scope not in ('market', 'watch'):
        raise ValueError('行情范围无效')
    if scope == 'market' and boards is not None and (not boards or any(b not in BOARDS for b in boards)):
        raise ValueError('请至少选择一个有效市场板块')
    watch = {r['code'] for r in store.rows('SELECT code FROM watchlist WHERE active=1')}
    rows = store.rows("SELECT * FROM datasets WHERE timeframe='daily' AND source!='demo' "
                      "ORDER BY end DESC,created DESC,rowid DESC")
    if any(r['source'] in SOURCES for r in rows):
        rows=[r for r in rows if r['source']==market_source(store)]
    selected = {}
    for row in rows:
        if scope == 'market' and boards is not None and board_of(row['code']) not in boards:
            continue
        if (scope == 'market' or row['code'] in watch) and row['code'] not in selected:
            selected[row['code']] = row
    return list(selected.values()), sorted(watch - set(selected)) if scope == 'watch' else []


def execute_replay(service, job, spec):
    if spec.get('timeframe') != 'daily' or spec.get('strategy') != 'STRATEGY_GAP_H2':
        raise ValueError('当前执行回放仅支持原 GAP H2 日线')
    ids = spec.get('datasets', [])
    boards = spec.get('boards') if spec.get('scope') == 'market' else None
    if boards is not None and (not boards or any(b not in BOARDS for b in boards)):
        raise ValueError('请至少选择一个有效市场板块')
    date.fromisoformat(spec['start'])
    date.fromisoformat(spec['end'])
    if spec['start'] > spec['end']:
        raise ValueError('开始日期不能晚于结束日期')
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('需要非空、不重复的行情快照范围')
    settings = H2Settings(**spec.get('h2_settings', {}))
    settings.validate()
    costs = Assumptions(**spec.get('assumptions', {}))
    costs.validate()
    strategy = catalog(service.store)[spec['strategy']]
    engine = engine_version()
    started = time.perf_counter()
    reused, calculated = 0, 0
    use_cache = spec.get('use_cache', True)
    records, errors, snapshots, warnings = [], [], [], []
    seen_codes = set()
    end = min(spec['end'], completed_date())
    from .readiness import expected_day
    try:
        first_source=service.store.rows('SELECT source FROM datasets WHERE id=?',(ids[0],))
        coverage_end = expected_day(service.store, end,first_source[0]['source'] if first_source and first_source[0]['source'] in SOURCES else 'baostock')
    except ValueError:
        # CSV can be researched without a certified exchange calendar, but its
        # last trading day must not be mistaken for a verified complete range.
        coverage_end = end
    stopped = False
    last_progress = 0.0
    def show_progress(done, message):
        nonlocal last_progress
        current = time.perf_counter()
        if current - last_progress >= .35 or done == len(ids) * 100:
            service.progress(job, done, len(ids) * 100, message)
            last_progress = current
    show_progress(0, f'准备回放 {len(ids)} 个已存标的')
    for n, did in enumerate(ids):
        try:
            service.check_stop(job)
            frame, snapshot = load_dataset(service.store, did, job)
            if boards is not None and board_of(snapshot['code']) not in boards:
                raise ValueError('行情快照不属于本次所选市场板块')
            if snapshot['code'] in seen_codes:
                raise ValueError('同一次回放不能把同一股票的多个快照重复统计')
            if snapshot['source'] == 'demo':
                raise ValueError('合成演示行情不能进入此真实行情回放入口')
            if snapshot['timeframe'] != 'daily':
                raise ValueError('需要日线行情快照')
            from .history_quality import require_research_history
            require_research_history(service.store, snapshot, end, spec['start'])
            frame = prepare(frame, 'daily', end)
            frame.attrs['code'] = snapshot['code']
            # ETF settlement and tick require explicit instrument metadata. CSV
            # schema has none, so this entry point currently accepts stocks only.
            if board_of(snapshot['code']) is None:
                raise ValueError('此入口暂仅支持已确认 tick 与 T+1 的 A 股股票')
            if len(frame) < 125:
                raise ValueError('不足 125 根已完成日线；不能完整预热原指标')
            if int((frame.date < spec['start']).sum()) < 124:
                warnings.append({'code': snapshot['code'], 'reason': '开始日期之前不足 124 根；早期区间无法完整研究'})
            if snapshot['end'] < coverage_end:
                warnings.append({'code': snapshot['code'], 'reason': f'行情快照仅到 {snapshot["end"]}，结束区间覆盖请核对停牌／交易日'})
            def progress(i, total):
                show_progress(n * 100 + int(100 * i / max(total, 1)),
                              f'已完成 {n}/{len(ids)} · 回放 {snapshot["code"]}：{i}/{total} 日 · 复用 {reused}')
            key = cache_key(snapshot, strategy, spec['start'], end, settings, costs, engine)
            trades = read_cache(service.store, key, job) if use_cache else None
            if trades is None:
                trades = run_replay(strategy, frame, spec['start'], end, settings, costs,
                                    progress, service.cancel_flags[job].is_set)
                calculated += 1
                if use_cache:
                    write_cache(service.store, key, trades, job)
            else:
                reused += 1
            for trade in trades:
                trade['dataset_id'] = did
                trade['study_end'] = end
            records.extend(trades)
            snapshots.append(snapshot)
            seen_codes.add(snapshot['code'])
        except InterruptedError:
            stopped = True
            break
        except Exception as exc:
            errors.append({'dataset': did, 'error': str(exc)})
        show_progress((n + 1) * 100,
                      f'完成 {n + 1}/{len(ids)} · 新回放 {calculated} · 复用 {reused} · 失败 {len(errors)}')
    report = summarize_replay(records)
    rel = f'research/{job}'
    report.update(execution_model=MODEL, errors=errors, coverage_warnings=warnings,
                  complete=not stopped and not errors and not warnings,
                  stop_reason='已停止；只保存完整回放完毕的标的，本次是部分结果' if stopped else '',
                  requested_datasets=len(ids), processed_datasets=len(snapshots),
                  datasets=snapshots, real_data=bool(snapshots) and all(
                      r['source'] in ('baostock', 'tickflow', 'tencent', 'legacy-engine-a') for r in snapshots),
                  strategy_version=strategy.version,
                  h2_settings=asdict(settings), assumptions={k: v for k, v in asdict(costs).items()
                      if k in ('holding_bars', 'commission_bps', 'sell_tax_bps', 'slippage_bps')}, timeframe='daily',
                  start=spec['start'], end=end, coverage_end=coverage_end, scope=spec.get('scope', 'specified'),
                  research_version=digest((strategy.version + dumps(asdict(settings))).encode()),
                  engine_version=engine,
                  performance=dict(elapsed_seconds=time.perf_counter() - started,
                                   calculated=calculated, reused=reused, cache_enabled=bool(use_cache)),
                  limitations=['独立机会允许重叠；不模拟组合资金、仓位或复利',
                               '腾讯不复权历史跨除权可能影响信号；不包含分红及除权股数调整，不直接等同总回报',
                               '日线无法确认盘中先后、涨跌停排队及实际流动性；单一价格不成交',
                               '入场与结构失效／MM 同日发生时保守不成交',
                               'A 股买入日按 T+1 不退出；买入日破 SL1 则下一可交易日开盘延迟止损',
                               'SL1／MM 成交后固定；超过持有期限在可交易日收盘退出',
                               '未结束仓位不强制平仓，不计入胜率或 R 排名',
                               '使用当前历史快照，前复权不是当时可用数据版本；现存名单有幸存者偏差',
                               'CSV 来源未经行情源认证，仅作为导入研究，不作为真实行情验收证明',
                               '仅研究所选范围内首次确认的 H2；区间开始前成立的机会不纳入',
                               '额外风险／MM 筛选在 H2 成立时执行，不是原策略识别条件'],
                  records_path=rel + '/replay.json', report_path=rel + '/report.json',
                  trades_path=rel + '/trades.csv')
    if boards is not None:
        report['boards'] = [b for b in BOARDS if b in boards]
    service.store.write_artifact(report['records_path'], dumps(records).encode('utf-8'), job)
    flat = [{k: v for k, v in r.items() if k not in ('events', 'structure', 'latest_plan', 'trigger_plan')}
            for r in records]
    service.store.write_artifact(report['trades_path'], pd.DataFrame(flat).to_csv(index=False).encode('utf-8-sig'), job)
    service.store.write_artifact(report['report_path'], dumps(report).encode('utf-8'), job)
    return report


def load_receipt(store, job):
    rows = store.rows("SELECT * FROM jobs WHERE id=? AND kind='backtest'", (job,))
    if not rows:
        raise ValueError('回测回执不存在')
    report = json.loads(rows[0]['result'])
    if report.get('execution_model') != MODEL:
        raise ValueError('此记录不属于 GAP H2 执行回放')
    records = json.loads(store.read_artifact(report['records_path']).decode('utf-8'))
    return rows[0], report, records
