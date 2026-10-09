"""Explicit read-only public quote probe; never certifies historical scan input."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.tencent_quotes import fetch_tencent_quotes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codes',default='sh.600000,sh.600519,sz.000906,sz.002259,bj.920002')
    p.add_argument('--directory',type=Path,help='Existing public universe CSV; no account/database reading')
    p.add_argument('--limit',type=int,default=100)
    args=p.parse_args()
    if not 1<=args.limit<=1000:
        p.error('Each explicit probe is limited to 1–1000 symbols')
    if args.directory:
        import pandas as pd
        codes=pd.read_csv(args.directory,usecols=['code'],dtype=str).code.drop_duplicates().tolist()[:args.limit]
    else:
        codes=args.codes.split(',')[:args.limit]
    started=time.monotonic()
    quotes,errors=fetch_tencent_quotes(codes)
    stamps=[q['quote_time'] for q in quotes.values()]
    print(json.dumps(dict(requested=len(codes),valid=len(quotes),failed=len(errors),
        seconds=round(time.monotonic()-started,3),batches=(len(codes)+99)//100,
        first_time=min(stamps) if stamps else None,last_time=max(stamps) if stamps else None,
        kind='unadjusted_snapshot',not_historical_scan_input=True,
        sample_errors=list(errors.items())[:3]),ensure_ascii=False))
    return 0 if not errors else 2


if __name__=='__main__':
    raise SystemExit(main())
