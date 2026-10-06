"""Read only new Aplus snapshots; run research in an isolated evidence home."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.h2_replay import MODEL as BASE_MODEL
from workbench.h2_replay_service import load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.stop_research import MODEL
from workbench.stop_research_service import load_experiment
from workbench.store import Store, dumps
from workbench.strategies import verify_frozen


def wait(store,job):
    for _ in range(2400):
        row=store.rows('SELECT * FROM jobs WHERE id=?',(job,))[0]
        if row['status'] not in ('queued','running'):return row
        time.sleep(.1)
    raise TimeoutError('验收任务未完成')


def main():
    import pandas as pd
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-home',required=True)
    parser.add_argument('--home',required=True)
    parser.add_argument('--screenshot',action='store_true')
    args=parser.parse_args()
    source,destination=Path(args.source_home).resolve(),Path(args.home).resolve()
    if source==destination or source in destination.parents:
        raise ValueError('验收目录必须与真实数据仓隔离')
    frozen=verify_frozen()
    db=sqlite3.connect((source/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    version=db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if not version or version[0] not in ('4','5','6','7','8'):
        raise ValueError('源目录不是兼容的新 Aplus 数据仓')
    store=Store(destination);ids=[]
    for code in ('sz.002870','sz.300870','sz.300837','sz.301387'):
        row=dict(db.execute("SELECT * FROM datasets WHERE code=? AND source='baostock' AND timeframe='daily' ORDER BY end DESC,created DESC LIMIT 1",(code,)).fetchone())
        path=(source/row['path']).resolve()
        if source not in path.parents:raise ValueError('快照超出源行情仓')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=row['sha256']:raise ValueError('源快照内容不一致')
        ids.append(save_dataset(store,code,pd.read_csv(path),'baostock',row['adjustment']))
    db.close()
    calendar=source/'trading_calendar.json'
    if calendar.exists():store.write_artifact('trading_calendar.json',calendar.read_bytes())
    service=Service(store)
    try:
        baseline=service.submit('backtest',dict(execution_model=BASE_MODEL,strategy='STRATEGY_GAP_H2',timeframe='daily',datasets=ids,start='2026-01-01',end='2026-09-30'))
        source_status=wait(store,baseline)['status']
        _,base_report,original=load_receipt(store,baseline)
        job=service.submit('backtest',dict(execution_model=MODEL,source_job=baseline))
        job_status=wait(store,job)['status']
        _,report,pairs=load_experiment(store,job)
        second=service.submit('backtest',dict(execution_model=MODEL,source_job=baseline,anchor_settings=dict(max_bars=8,pause_ticks=1)))
        wait(store,second)
        assert not report['errors'],report['errors']
        assert report['real_data'] and base_report['real_data']
        assert load_receipt(store,baseline)[2]==original
        assert verify_frozen()==frozen
        for table in ('observations','plans','plan_history','watchlist','accounts','executions','positions','position_fills','closed_trades'):
            assert store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n']==0
        evidence=dict(source_status=source_status,status=job_status,real_data=True,
                      opportunities=report['opportunities'],comparable=report['comparable'],groups=report['groups'],
                      baseline_unchanged=True,formal_tables_empty=True,frozen_unchanged=True,
                      job=job,second=second,source=baseline,
                      note='四只真实行情快照的工程与图表验收；不是全市场表现或长期实际使用验收。')
        if args.screenshot:
            from gui.main_window import AplusMainWindow
            from PIL import ImageGrab
            window=AplusMainWindow(service,store)
            window.geometry('1800x1100')
            window._switch_section('strategy');window.update()
            page=window.strategy_iteration
            page._load(job);page.history.selection_set(job)
            for _ in range(8):window.update();time.sleep(.1)
            candidates=[r for r in pairs if r['comparable']]
            if candidates:
                page.samples.selection_set(candidates[0]['id'])
                for _ in range(6):window.update();time.sleep(.1)
                assert page.photo is not None
                assert page.chart.winfo_width()>350
                assert page.samples.winfo_width()>300
            # Capture only this test-owned window, never the surrounding desktop.
            ImageGrab.grab(window=window.winfo_id()).save(destination/'strategy-iteration-desktop.png')
            evidence['screenshot']='strategy-iteration-desktop.png'
            evidence['gui_chart_size']=[page.chart.winfo_width(),page.chart.winfo_height()]
            window.destroy()
        store.write_artifact('stop-acceptance.json',dumps(evidence).encode('utf-8'))
        print(dumps(evidence))
    finally:service.pool.shutdown()


if __name__=='__main__':main()
