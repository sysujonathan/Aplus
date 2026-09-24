"""Small live-provider smoke test; explicitly scoped to a new runtime directory.

Run: python -m tools.acceptance_live --home <new product runtime>
Does not create plans, enable strategies, or access any legacy database.
"""
import argparse
import json
import time
from pathlib import Path

from workbench.market import completed_date, latest_datasets
from workbench.service import Service
from workbench.store import Store, dumps
from workbench.strategies import verify_frozen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home',required=True)
    args = parser.parse_args()
    store = Store(args.home)
    service = Service(store)
    results = []
    def run(kind,spec):
        job = service.submit(kind,spec)
        while True:
            row = store.rows('SELECT * FROM jobs WHERE id=?',(job,))[0]
            if row['status'] not in {'running','queued'}:
                result = json.loads(row['result'])
                print(kind,row['status'],row['message'],flush=True)
                results.append({'job':job,'kind':kind,'status':row['status'],'result':result,'message':row['message']})
                if row['status']!='completed':
                    raise RuntimeError(dumps(results[-1]))
                return result
            time.sleep(.2)
    try:
        verify_frozen()
        run('sync',{'codes':['sh.600000','sz.000001','sz.300750'],'start':'2016-01-01','end':completed_date()})
        ids = [r['id'] for r in latest_datasets(store)]
        run('scan',{'datasets':ids,'strategies':['MTR_MASTER','STRATEGY_3K','STRATEGY_STRUCTURAL_GAP',
                                               'STRATEGY_GAP_PINBAR','STRATEGY_GAP_H2','STRATEGY_AWIL'],
                    'timeframe':'daily','asof':completed_date()})
        run('scan',{'datasets':ids,'strategies':['STRATEGY_STRUCTURAL_GAP','STRATEGY_GAP_PINBAR','STRATEGY_GAP_H2'],
                    'timeframe':'weekly','asof':completed_date()})
        run('backtest',{'datasets':ids[:1],'strategy':'STRATEGY_3K','timeframe':'daily',
                        'start':'2025-01-01','end':'2025-03-31','assumptions':{}})
    finally:
        store.write_artifact('acceptance/live-result.json',dumps(results).encode('utf-8'))
        service.pool.shutdown(wait=True)


if __name__=='__main__':
    main()
