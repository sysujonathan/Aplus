"""Read new-product snapshots read-only; replay copies in a separate evidence home.

Never reads legacy project databases. This verifies execution plumbing, not edge.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time

import pandas as pd

from workbench.h2_replay import MODEL
from workbench.h2_replay_service import load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store, digest, dumps
from workbench.strategies import verify_frozen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-home', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--codes', required=True, nargs='+')
    args = parser.parse_args()
    source, destination = Path(args.source_home).resolve(), Path(args.home).resolve()
    if source == destination or source in destination.parents:
        raise ValueError('验收必须使用独立目录，不能写入源行情仓')
    verify_frozen()
    with sqlite3.connect((source / 'workbench.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not version or version[0] != '4':
            raise ValueError('源目录必须是新 Aplus 的版本 4 数据仓')
        snapshots = []
        for code in args.codes:
            row = db.execute("SELECT * FROM datasets WHERE code=? AND timeframe='daily' AND source='baostock' "
                             "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code,)).fetchone()
            if row is None:
                raise ValueError(f'{code} 没有真实行情快照')
            snapshots.append(dict(row))
    store = Store(destination)
    ids = []
    for snapshot in snapshots:
        path = (source / snapshot['path']).resolve()
        if source not in path.parents:
            raise ValueError('快照路径超出源行情仓')
        content = path.read_bytes()
        if digest(content) != snapshot['sha256']:
            raise ValueError('源快照校验不一致')
        frame = pd.read_csv(path)
        ids.append(save_dataset(store, snapshot['code'], frame, snapshot['source'], snapshot['adjustment']))
    calendar = source / 'trading_calendar.json'
    if calendar.exists():
        store.write_artifact('trading_calendar.json', calendar.read_bytes())
    service = Service(store)
    try:
        job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                    datasets=ids, start=args.start, end=args.end, scope='指定真实行情验收样本'))
        while True:
            row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
            if row['status'] not in ('queued', 'running'):
                break
            time.sleep(.2)
        _, report, records = load_receipt(store, job)
        untouched = {table: store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n']
                     for table in ('observations', 'plans', 'plan_history', 'watchlist')}
        assert all(value == 0 for value in untouched.values())
        evidence = dict(job=job, status=row['status'], real_data=report['real_data'],
                        snapshots=len(snapshots), opportunities=report['opportunities'],
                        triggered=report['triggered'], filled=report['filled'], closed=report['closed_trades'],
                        counts=report['counts'], warnings=report['coverage_warnings'], errors=report['errors'],
                        formal_tables=untouched, frozen=verify_frozen(),
                        note='指定少量真实日线的工程验收；不是全市场表现或长期实际使用验收。')
        if records:
            from gui.h2_replay_chart import render_replay
            record = next((r for r in records if r['filled']), records[0])
            image = render_replay(store, record, len(record['events']) - 1)
            image.save(destination / 'acceptance-replay.png')
            evidence['chart'] = 'acceptance-replay.png'
        store.write_artifact('acceptance.json', dumps(evidence).encode('utf-8'))
        print(dumps(evidence))
        if report['errors'] or not report['real_data']:
            raise RuntimeError('真实行情验收未通过')
    finally:
        service.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
