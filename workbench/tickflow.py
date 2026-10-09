"""TickFlow free daily bars: explicit qfq, batch <=100, no implicit retry."""
from datetime import date, datetime, timedelta, timezone
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .market import code_of, validate_bars, board_of
from .provider_guard import ProviderError
from .suspensions import announcement_evidence

CHINA = timezone(timedelta(hours=8))
BASE = 'https://free-api.tickflow.org'


def symbol_of(code):
    market, number = code_of(code).split('.')
    return number+'.'+market.upper()


def decode_bars(code, payload, start, end):
    keys = ['timestamp','open','high','low','close','volume']
    if not isinstance(payload,dict) or any(not isinstance(payload.get(k),list) for k in keys):
        raise ValueError(f'TickFlow {code} 缺少日 K 列')
    if len({len(payload[k]) for k in keys}) != 1:
        raise ValueError(f'TickFlow {code} 日 K 列长度不一致')
    days = [datetime.fromtimestamp(t/1000,CHINA).date().isoformat() for t in payload['timestamp']]
    frame = pd.DataFrame({k:payload[k] for k in keys[1:]})
    frame.insert(0,'date',days)
    # The validated CN stock contract reports lots, while our immutable bars use shares.
    frame['volume'] = pd.to_numeric(frame.volume,errors='raise') * 100
    if frame.empty:
        raise ValueError(f'TickFlow {code} 未返回行情，不假定停牌')
    frame = validate_bars(frame)
    frame = frame[(frame.date >= start)&(frame.date <= end)].reset_index(drop=True)
    if frame.empty:
        raise ValueError(f'TickFlow {code} 请求区间无行情，不推进覆盖')
    suspended, evidence = announcement_evidence(code,start,end)
    if set(frame.date).intersection(suspended):
        raise ValueError(f'TickFlow {code} 返回日 K 与已核验整日停牌公告冲突，未保存')
    frame.attrs.update(returned_dates=frame.date.tolist(),source='tickflow',adjustment='前复权',volume_unit='shares',
                       suspended_dates=suspended,suspension_evidence=evidence)
    return frame


class TickFlowHTTP:
    """Runs only in the killable worker process; HTTP diagnostics survive IPC."""
    def __init__(self):
        self.last_request = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __enter__(self): return self
    def __exit__(self,*args): pass

    def request(self,path,params=None):
        time.sleep(max(0,1.5-(time.monotonic()-self.last_request)))  # <=40/min, sequential
        self.last_request = time.monotonic()
        started = time.monotonic()
        url = BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
        request = urllib.request.Request(url,headers={'User-Agent':'Aplus/1.1.4','Accept':'application/json'})
        try:
            with self.opener.open(request,timeout=15) as response:
                body = response.read(64*1024*1024+1)
            if len(body)>64*1024*1024:
                raise ValueError('批次响应过大，未保存')
            result = json.loads(body)
            if 'data' not in result:
                raise ValueError('响应没有 data，未保存')
            return result['data']
        except Exception as exc:
            code = str(exc.code) if isinstance(exc,urllib.error.HTTPError) else None
            error=ProviderError('tickflow',path,str(exc),code,round(time.monotonic()-started,3))
            if isinstance(exc,urllib.error.HTTPError):
                try:
                    error.retry_after=min(24*3600,max(0,float(exc.headers.get('Retry-After','0'))))
                except (ValueError,TypeError):
                    pass
            raise error from exc

    def batch(self,codes,start,end,adjust='forward'):
        if not 1<=len(codes)<=100:
            raise ValueError('TickFlow 每批限 1～100 只')
        first = datetime.combine(date.fromisoformat(start),datetime.min.time(),CHINA)
        last = datetime.combine(date.fromisoformat(end)+timedelta(days=1),datetime.min.time(),CHINA)
        return self.request('/v1/klines/batch',dict(symbols=','.join(symbol_of(c) for c in codes),
            period='1d',count=10000,adjust=adjust,start_time=int(first.timestamp()*1000),end_time=int(last.timestamp()*1000)))

    def instruments(self,exchange):
        return self.request('/v1/exchanges/'+exchange+'/instruments',dict(type='stock'))


from .provider_process import BaoStock


class TickFlow(BaoStock):
    """Reuse the process timeout/cancellation mechanism, not BaoStock sessions."""
    vendor = 'tickflow'
    query_timeout = 45

    def __init__(self):
        super().__init__()
        self.cache = {}

    def preload(self,codes,start,end,*,preserve=False):
        if not preserve:
            self.cache.clear()  # never accumulate whole-market history in RAM
        else:
            # A factor-change refresh of one stock must not evict the other
            # 99 stocks already fetched in this batch. Keep only one range/code.
            self.cache={key:value for key,value in self.cache.items() if key[0] not in codes}
        raw = self._query('batch',[codes,start,end])
        if not isinstance(raw,dict):
            raise ProviderError('tickflow','batch','日 K 响应不是按标的组织的对象，未保存')
        for code in codes:
            try:
                self.cache[(code,start,end)] = decode_bars(code,raw.get(symbol_of(code)),start,end)
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                self.cache[(code,start,end)] = exc

    def fetch(self,code,start,end):
        key = (code,start,end)
        if key not in self.cache:
            self.preload([code],start,end,preserve=True)
        result = self.cache[key]
        if isinstance(result,Exception):
            raise result
        return result.copy()

    def universe(self,day):
        rows=[]
        for exchange in ('SH','SZ','BJ'):
            for item in self._query('instruments',[exchange]):
                # Preserve explicit exchange identity (especially old BJ prefixes).
                code=exchange.lower()+'.'+item['symbol'].split('.')[0]
                if board_of(code) is None:
                    continue
                listing=(item.get('ext') or {}).get('listing_date','')
                if listing:
                    date.fromisoformat(listing)  # malformed metadata must not narrow the universe
                # Retain unknown entries for per-stock validation, never silently
                # exclude a security or abort all downloads because one date is absent.
                if not listing or listing<=day:
                    rows.append(dict(code=code,code_name=item.get('name',''),tradeStatus='unknown',ipoDate=listing))
        return pd.DataFrame(rows)

    def fetch_unadjusted(self,code,start,end):
        raw=self._query('batch',[[code],start,end,'none'])
        frame=decode_bars(code,raw.get(symbol_of(code)),start,end)
        frame.attrs['adjustment']='不复权'
        return frame
