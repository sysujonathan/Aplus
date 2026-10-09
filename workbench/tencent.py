"""Tencent raw daily history, isolated transport and a public securities catalog.

Prices never come from the catalog endpoint. No BaoStock/TickFlow connection,
factor, history, coverage or cooldown is used by this provider.
"""
from datetime import date, timedelta
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .market import code_of, board_of, validate_bars
from .provider_guard import ProviderError
from .provider_process import BaoStock
from .tencent_quotes import volume_scale

HISTORY_URL = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'
DIRECTORY_URL = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.'
PAGE_BARS = 640
DIRECTORY_PAGE = 100


def decode_history(code, payload, start, end):
    if board_of(code) is None:
        raise ValueError('腾讯历史路线仅支持 A 股，不猜测指数或基金单位')
    symbol = code_of(code).replace('.', '')
    if not isinstance(payload, dict) or payload.get('code') != 0:
        raise ValueError('腾讯历史响应状态无效')
    body = payload.get('data', {}).get(symbol)
    if not isinstance(body, dict) or not isinstance(body.get('day'), list):
        raise ValueError('腾讯未返回所请求股票的不复权 day 序列，不替换为其他复权口径')
    rows = body['day']
    if not rows or len(rows) > PAGE_BARS or any(not isinstance(r, list) or len(r) < 6 for r in rows):
        raise ValueError('腾讯历史日 K 为空、截断或超过请求上限')
    # STAR reports shares; other A-share boards report lots. Indices unsupported.
    frame = pd.DataFrame([dict(date=r[0], open=r[1], close=r[2], high=r[3], low=r[4],
                               volume=float(r[5])*volume_scale(code)) for r in rows])
    frame = validate_bars(frame)
    if (frame.date > end).any():
        raise ValueError('腾讯返回请求结束日之后的行情，未保存')
    frame.attrs.update(source='tencent', adjustment='不复权', volume_unit='shares',
                       returned_dates=frame.date.tolist())
    return frame


class TencentHTTP:
    """Bounded sequential HTTP, run only in a killable worker process."""
    def __init__(self):
        self.last_request = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def request(self, url, params, operation):
        time.sleep(max(0, .5-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        started = time.monotonic()
        target = url+'?'+urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(target, headers={'User-Agent':'Aplus/1.1.4',
                'Referer':'https://gu.qq.com/' if 'qq.com' in url else 'https://finance.sina.com.cn/'})
            with self.opener.open(req, timeout=8) as response:
                raw = response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:
                raise ValueError('腾讯路线响应超过上限')
            return json.loads(raw.decode('utf-8'))
        except Exception as exc:
            code = str(exc.code) if isinstance(exc, urllib.error.HTTPError) else None
            raise ProviderError('tencent', operation, str(exc), code, round(time.monotonic()-started,3)) from exc

    def history_page(self, code, end):
        symbol = code_of(code).replace('.', '')
        return self.request(HISTORY_URL, {'param':f'{symbol},day,,{end},{PAGE_BARS},'}, 'history_page')

    def directory_count(self):
        value = self.request(DIRECTORY_URL+'getHQNodeStockCount', {'node':'hs_a'}, 'directory_count')
        if not str(value).isdigit() or not 1 <= int(value) <= 15000:
            raise ValueError('公共证券目录总数无效')
        return int(value)

    def directory_page(self, page):
        value = self.request(DIRECTORY_URL+'getHQNodeData',
            {'node':'hs_a', 'page':page, 'num':DIRECTORY_PAGE, 'sort':'symbol', 'asc':1}, 'directory_page')
        if not isinstance(value,list) or len(value)>DIRECTORY_PAGE:
            raise ValueError('公共证券目录分页无效')
        # Catalog only. Discard all Sina quote/volume fields at the network boundary.
        result=[]
        for item in value:
            symbol=item.get('symbol','')
            if not re.fullmatch(r'(?:sh|sz|bj)\d{6}',symbol) or symbol[2:] != item.get('code'):
                raise ValueError('公共证券目录市场身份不一致')
            code=symbol[:2]+'.'+symbol[2:]
            # Preserve the complete verified catalog, including new code prefixes.
            # Board selection excludes unsupported identities with a visible receipt;
            # one new security must not invalidate all existing board memberships.
            result.append(dict(code=code,code_name=item.get('name',''),tradeStatus='unknown'))
        return result


class Tencent(BaoStock):
    vendor = 'tencent'
    query_timeout = 15

    def fetch(self, code, start, end):
        date.fromisoformat(start); date.fromisoformat(end)
        if start>end: raise ValueError('腾讯请求区间无效')
        chunks=[]; right=end
        # Each IPC operation is separately cancellable; never one ten-year wait.
        for _ in range(32):
            raw=self._query('history_page',[code,right])
            frame=decode_history(code,raw,start,right)
            if chunks:
                # Endpoint must move backwards; overlapping or repeated pages
                # cannot be deduplicated to hide a ignored date parameter.
                if frame.date.max()>=chunks[-1].date.min():
                    raise ValueError('腾讯历史分页未向前推进，未保存截断历史')
            chunks.append(frame)
            first=frame.date.min()
            if first<=start or len(frame)<PAGE_BARS:
                break
            right=(date.fromisoformat(first)-timedelta(days=1)).isoformat()
        else:
            raise ValueError('腾讯历史分页超过安全上限，未登记完整覆盖')
        merged=validate_bars(pd.concat(chunks,ignore_index=True))
        merged=merged[(merged.date>=start)&(merged.date<=end)].reset_index(drop=True)
        if merged.empty:
            raise ValueError('腾讯请求区间无真实日 K，不能假定停牌')
        merged.attrs.update(source='tencent', adjustment='不复权', volume_unit='shares',
                            returned_dates=merged.date.tolist())
        return merged

    def fetch_unadjusted(self, code, start, end):
        return self.fetch(code,start,end)

    def universe(self, day):
        total=self._query('directory_count',[])
        rows=[]
        for page in range(1,(total+DIRECTORY_PAGE-1)//DIRECTORY_PAGE+1):
            received=self._query('directory_page',[page])
            expected=min(DIRECTORY_PAGE,total-len(rows))
            if len(received)!=expected:
                raise ValueError('公共证券目录分页数量不齐，未替换原目录')
            rows.extend(received)
            self.on_wait('directory',len(rows))
        if self._query('directory_count',[]) != total:
            raise ValueError('公共证券目录抓取期间总数变化，请稍后重新核验')
        directory=pd.DataFrame(rows)
        if len(directory)!=total or directory.code.duplicated().any():
            raise ValueError('公共证券目录重复或不完整，未替换原目录')
        directory.attrs['catalog_source']='sina_public_securities_only'
        return directory
