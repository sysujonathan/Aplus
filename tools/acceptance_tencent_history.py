"""Explicit small live Tencent acceptance; temporary Store, no account DB."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from workbench.market import completed_date, parse_codes, select_board_codes, BOARDS, board_of
from workbench.readiness import save_directory
from workbench.sources import set_market_source
from workbench.service import Service
from workbench.store import Store
from workbench.tencent import Tencent


def wait(store, job):
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        row=store.rows('SELECT * FROM jobs WHERE id=?',(job,))[0]
        if row['status'] not in ('queued','running'):return row
        time.sleep(.2)
    raise TimeoutError('隔离验收超过240秒')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--calendar',required=True,help='只读公开 trading_calendar.json；不读取账户数据库')
    parser.add_argument('--codes',default='sh.600000,sz.000906,sh.688981,bj.920002')
    parser.add_argument('--start',default='2024-01-01')
    parser.add_argument('--directory-check',action='store_true',help='显式核验全证券目录，不下载全市场历史价格')
    args=parser.parse_args()
    codes=parse_codes(args.codes)
    if not 1<=len(codes)<=10:parser.error('真实历史验收限1～10只公开股票')
    end=completed_date(); started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='aplus-tencent-public-') as root:
        store=Store(Path(root))
        store.write_artifact('sources/tencent/trading_calendar.json',Path(args.calendar).read_bytes())
        catalog=None
        if args.directory_check:
            with Tencent() as provider:
                provider.on_wait=lambda op,n:print(json.dumps({'stage':op,'checked':n}),flush=True) if op=='directory' and n%800==0 else None
                catalog=provider.universe(end)
            print(json.dumps({'directory':len(catalog),'boards':{b:len(select_board_codes(catalog,[b])) for b in BOARDS},
                'unsupported_codes':[r.code for r in catalog.itertuples() if board_of(r.code) is None]},ensure_ascii=False),flush=True)
        service=Service(store)
        try:
            job=service.submit('sync',dict(source='tencent',codes=codes,start=args.start,end=end))
            sync=wait(store,job); report=json.loads(sync['result'])
            print(json.dumps({'stage':'sync','status':sync['status'],'report':report},ensure_ascii=False),flush=True)
            ids=report.get('datasets',[])
            if not ids:return 2
            # Explicitly selected stocks, NOT a claim of complete market coverage.
            import pandas as pd
            actual=store.rows("SELECT code,end FROM datasets WHERE source='tencent'")
            day=min(r['end'] for r in actual)
            save_directory(store,pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*len(codes))),day,source='tencent')
            set_market_source(store,'tencent')
            boards=list(dict.fromkeys(board_of(c) for c in codes))
            scan=wait(store,service.submit('scan',dict(source='tencent',boards=boards,datasets=ids,
                strategies=['MTR_MASTER','STRATEGY_3K','STRATEGY_STRUCTURAL_GAP','STRATEGY_GAP_PINBAR','STRATEGY_GAP_H2','STRATEGY_AWIL'],
                timeframes=['daily'],asof=day)))
            result=json.loads(scan['result'])
            print(json.dumps({'stage':'scan','status':scan['status'],'success':result.get('success'),
                'signals':result.get('signals'),'errors':result.get('errors'),
                'specified_stock_scope_only':True},ensure_ascii=False),flush=True)
            valid=result.get('datasets',[])
            if valid:
                bt=wait(store,service.submit('backtest',dict(source='tencent',datasets=valid[:1],
                    strategy='STRATEGY_GAP_H2',timeframe='daily',start=args.start,end=day,
                    assumptions={})))
                study=json.loads(bt['result'])
                print(json.dumps({'stage':'backtest','status':bt['status'],'datasets':
                    [{k:r[k] for k in ('code','source','adjustment','rows','start','end')} for r in study.get('datasets',[])],
                    'errors':study.get('errors'),'limitation':'不复权研究不包含分红和除权股数调整'},ensure_ascii=False),flush=True)
                if bt['status']!='completed':return 2
            print(json.dumps({'elapsed_seconds':round(time.monotonic()-started,3),'formal_account_access':False}),flush=True)
            return 0 if sync['status']=='completed' and scan['status']=='completed' else 2
        finally:service.pool.shutdown()


if __name__=='__main__':raise SystemExit(main())
