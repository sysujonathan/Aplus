"""Real-snapshot old/fast equality and end-to-end cache benchmark, isolated home."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

import pandas as pd

from workbench.h2_replay import MODEL, run_replay
from workbench.h2_replay_service import load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store, digest, dumps
from workbench.strategies import catalog, verify_frozen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-home', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--limit', type=int, default=24)
    args = parser.parse_args()
    source, target = Path(args.source_home).resolve(), Path(args.home).resolve()
    if target == source or source in target.parents:
        raise ValueError('性能验收须使用独立目录')
    verify_frozen()
    with sqlite3.connect((source / 'workbench.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not version or version[0] not in ('4', '5', '6'):
            raise ValueError('仅接受新 Aplus 数据仓')
        codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM observations WHERE strategy='STRATEGY_GAP_H2' "
                                        "ORDER BY code LIMIT ?", (args.limit,))]
        snapshots = []
        for code in codes:
            row = db.execute("SELECT * FROM datasets WHERE code=? AND source='baostock' AND timeframe='daily' "
                             "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code,)).fetchone()
            if row:
                snapshots.append(dict(row))
    if not snapshots:
        raise ValueError('没有可核验的真实行情样本')
    store = Store(target)
    strategy = catalog(store)['STRATEGY_GAP_H2']
    ids, detail = [], []
    for n, snapshot in enumerate(snapshots):
        path = (source / snapshot['path']).resolve()
        if source not in path.parents or digest(path.read_bytes()) != snapshot['sha256']:
            raise ValueError('源快照路径或校验不一致')
        bars = pd.read_csv(path)
        bars.attrs['code'] = snapshot['code']
        ids.append(save_dataset(store, snapshot['code'], bars, 'baostock', snapshot['adjustment']))
        timings, results = [], []
        for mode in (False, True):
            started = time.perf_counter()
            results.append(run_replay(strategy, bars, args.start, args.end, prefilter=mode))
            timings.append(time.perf_counter() - started)
        equal = dumps(results[0]) == dumps(results[1])
        if not equal:
            raise AssertionError(f'{snapshot["code"]} 逐笔结果不同')
        detail.append(dict(code=snapshot['code'], reference_seconds=timings[0],
                           fast_seconds=timings[1], opportunities=len(results[0]), equal=equal))
        print(f'逐笔对照完成 {n + 1}/{len(snapshots)}', flush=True)
    if (source / 'trading_calendar.json').exists():
        store.write_artifact('trading_calendar.json', (source / 'trading_calendar.json').read_bytes())
    service = Service(store)
    jobs = []
    def run(use_cache):
        started = time.perf_counter()
        job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', execution_model=MODEL,
                            timeframe='daily', datasets=ids, start=args.start, end=args.end, use_cache=use_cache))
        while True:
            row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
            if row['status'] not in ('queued', 'running'):
                break
            time.sleep(.02)
        _, report, records = load_receipt(store, job)
        if report['errors']:
            raise ValueError(dumps(report['errors']))
        jobs.append(dict(status=row['status'], elapsed_seconds=time.perf_counter() - started,
                         performance=report['performance'], opportunities=report['opportunities'],
                         closed=report['closed_trades'], warnings=report['coverage_warnings']))
        return records
    try:
        reference, cold, warm = run(False), run(True), run(True)
        assert dumps(reference) == dumps(cold) == dumps(warm)
        untouched = {table: store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'] for table in
                     ('observations', 'plans', 'plan_history', 'watchlist', 'accounts', 'executions',
                      'positions', 'position_fills', 'closed_trades')}
        assert not any(untouched.values())
        old = sum(r['reference_seconds'] for r in detail)
        fast = sum(r['fast_seconds'] for r in detail)
        evidence = dict(samples=len(detail), opportunities=sum(r['opportunities'] for r in detail),
                        period=[args.start, args.end], reference_seconds=old, fast_seconds=fast,
                        core_speedup=old / max(fast, 1e-9), all_events_equal=True, jobs=jobs,
                        formal_tables=untouched, detail=detail,
                        note='少量已有 H2 档案关联真实股票快照；非全市场耗时保证或收益优势证明。')
        store.write_artifact('benchmark.json', dumps(evidence).encode('utf-8'))
        print(dumps(evidence), flush=True)
    finally:
        service.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
