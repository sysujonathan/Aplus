"""Opt-in real TickFlow acceptance in a NEW isolated runtime, never a ledger.

The directory is explicitly restricted to the requested public sample. This is
not whole-market acceptance and cannot certify every historical announcement.
"""
import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch

from workbench.store import Store, dumps
from workbench.tickflow import TickFlow
from workbench.service import Service
from workbench.market import completed_date, latest_datasets
from workbench.sources import set_market_source, coverage_state
from workbench.strategies import catalog
from workbench.tickflow_integrity import integrity_view


def wait(store,service,job):
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        row=store.rows('SELECT * FROM jobs WHERE id=?',(job,))[0]
        if row['status'] not in ('queued','running'):
            report=json.loads(row['result'])
            print(dumps(dict(job=job,status=row['status'],message=row['message'],
                success=report.get('success'),errors=len(report.get('errors',[])),
                counts=report.get('integrity',{}).get('counts'))),flush=True)
            if row['status'] in ('failed','cancelled','interrupted') or report.get('cooldown'):
                raise RuntimeError('验收停止；保留回执，没有自动重试：'+row['message'])
            return row,report
        time.sleep(.2)
    service.cancel(job)
    raise TimeoutError('验收任务超时；已请求停止，不自动重试')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True,help='New isolated directory; existing database is rejected')
    parser.add_argument('--calendar',required=True,help='Read-only verified exchange calendar JSON')
    parser.add_argument('--end',default=completed_date())
    args=parser.parse_args()
    root=Path(args.runtime).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('验收目录必须为空；不能打开或覆盖正式运行数据')
    if args.end>completed_date():
        raise ValueError('不能验收尚未完成的行情日期')
    calendar=json.loads(Path(args.calendar).read_text(encoding='utf-8'))
    start='2016-01-01'
    if not calendar['start']<=start<=args.end<=calendar['end']:
        raise ValueError('给定已核验交易日历未覆盖验收区间')
    days=[d for d in calendar['trading_days'] if d<=args.end]
    end=days[-1]; previous=days[-2]
    codes=['sz.000906','sz.002259','sh.600058','sh.600707','sh.603029','sh.603822',
           'sh.601198','sz.300750','sh.688981','bj.920002']
    boards=['沪深主板','创业板','科创板','北交所']
    store=Store(root)
    store.write_artifact('sources/tickflow/trading_calendar.json',dumps(calendar).encode())
    calls=[]

    class SampleTickFlow(TickFlow):
        def universe(self,day):
            directory=super().universe(day)
            selected=directory[directory.code.isin(codes)].copy()
            if set(selected.code)!=set(codes):
                raise ValueError('公开样本目录未返回全部预设标的；不会静默缩减样本')
            return selected
        def preload(self,codes,start,end,**kwargs):
            calls.append(dict(codes=list(codes),start=start,end=end))
            return super().preload(codes,start,end,**kwargs)

    def no_baostock(*args,**kwargs):
        raise AssertionError('TickFlow 验收不允许连接 BaoStock')

    results=dict(scope='ten explicitly selected public stocks, NOT whole market',codes=codes,
                 start=start,end=end,previous=previous)
    service=Service(store)
    try:
        with patch('workbench.tickflow.TickFlow',SampleTickFlow),patch('workbench.service.BaoStock',no_baostock):
            spec=dict(source='tickflow',boards=boards,start=start,end=previous,scan_timeframe='daily')
            _,results['initial']=wait(store,service,service.submit('sync',spec))
            assert results['initial']['integrity']['scan']['expected']==len(codes)
            _,results['incremental']=wait(store,service,service.submit('sync',dict(spec,end=end)))
            before=len(calls)
            _,results['reuse']=wait(store,service,service.submit('sync',dict(spec,end=end)))
            assert len(calls)==before,'covered bars should not be requested again'
            receipt=results['reuse']['integrity']
            # Exercise a real unresolved input, if present; same blank is never halt proof.
            view=integrity_view(receipt,'weekly')
            repair=view['scan_repair_codes'][:1]
            if repair:
                before=len(calls)
                _,results['repair']=wait(store,service,service.submit('sync',dict(spec,end=end,
                    scan_timeframe='weekly',repair_codes=repair,repair_scope='scan')))
                assert calls[before:] and all(set(c['codes'])<=set(repair) for c in calls[before:])
                receipt=results['repair']['integrity']
            else:
                results['repair_note']='No unresolved current-input sample; repair is covered by software fixtures only.'
            strategies=[k for k,s in catalog(store).items() if s.state=='active' and 'daily' in s.timeframes]
            ids=[r['id'] for r in latest_datasets(store,'tickflow')]
            _,results['scan']=wait(store,service,service.submit('scan',dict(source='tickflow',boards=boards,
                datasets=ids,strategies=strategies,timeframes=['daily','weekly'],asof=end)))
            assert results['scan'].get('success',0)>0,'real strategy calculations were not exercised'
            before_records=store.rows('SELECT * FROM datasets ORDER BY id')
            before_files={r['path']:(store.root/r['path']).read_bytes() for r in before_records}
            before_coverage={c:coverage_state(store,c,'tickflow') for c in codes}
            before_receipt=store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")
            before=len(calls)
            for source in ['tickflow','baostock','tickflow']:
                set_market_source(store,source)
            assert len(calls)==before
            assert before_records==store.rows('SELECT * FROM datasets ORDER BY id')
            assert before_coverage=={c:coverage_state(store,c,'tickflow') for c in codes}
            assert before_receipt==store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")
            assert all((store.root/path).read_bytes()==content for path,content in before_files.items())
            for table in ('plans','accounts','positions','position_fills','closed_trades','cash_flows'):
                assert store.rows(f'SELECT count(*) AS n FROM {table}')[0]['n']==0
            results.update(batch_calls=calls,source_switch_preserved=True,ledger_empty=True)
            store.write_artifact('reports/current-source-acceptance.json',dumps(results).encode())
            print('PASS: real sample update/increment/reuse/scan/switch; NOT whole-market or long-term acceptance.',flush=True)
    finally:
        service.pool.shutdown()


if __name__=='__main__':
    main()
