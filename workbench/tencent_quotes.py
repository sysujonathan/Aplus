"""Bounded Tencent unadjusted snapshots, NOT certified historical daily bars.

No Store, history stitching, adjustment factors, scan coverage or ledger writes.
The public web endpoint has no contractual availability/batch-size guarantee.
"""
from datetime import datetime, timedelta, timezone
import math
import re
import time
import urllib.request

from .market import code_of

CHINA = timezone(timedelta(hours=8))
BATCH_SIZE = 100
MAX_BODY = 1024 * 1024


def volume_scale(code):
    """Tencent A-share STAR volume is shares; other stock boards use lots."""
    return 1 if code_of(code).startswith('sh.688') else 100


def parse_tencent_quotes(text, codes, moment):
    records = {}
    duplicates = set()
    for symbol, raw in re.findall(r'v_((?:sh|sz|bj)\d{6})\s*=\s*"([^"\r\n]*)"', text):
        if symbol in records:
            duplicates.add(symbol)
        records[symbol] = raw.split('~')
    result = {}
    for code in codes:
        code = code_of(code)
        symbol = code.replace('.', '')
        fields = records.get(symbol, [])
        try:
            if symbol in duplicates or fields[2] != code.split('.')[1]:
                continue
            price, previous, opened = map(float, (fields[3], fields[4], fields[5]))
            high, low, hands = map(float, (fields[33], fields[34], fields[6]))
            trade = fields[35].split('/')  # price / total hands / amount in yuan
            amount = float(trade[2])
            stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
            values = (price, previous, opened, high, low, hands, amount)
            if not all(math.isfinite(v) for v in values):
                continue
            if min(price, previous, opened, high, low) <= 0 or min(hands, amount) < 0:
                continue
            if high < max(opened, price, low) or low > min(opened, price, high):
                continue
            if not math.isclose(float(trade[0]), price) or not math.isclose(float(trade[1]), hands):
                continue
            if stamp > moment + timedelta(seconds=60):
                continue
            result[code] = dict(price=price, previous_close=previous, date=stamp.date().isoformat(),
                quote_time=stamp.isoformat(), source='tencent', adjustment='不复权',
                open=opened, high=high, low=low, volume=hands*volume_scale(code), amount=amount,
                volume_unit='shares', amount_unit='yuan', kind='snapshot')
        except (IndexError, ValueError, TypeError, OverflowError):
            continue
    return result


def fetch_tencent_quotes(codes, *, moment=None, opener=None, cancel_event=None):
    codes = sorted({code_of(c) for c in codes})
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    quotes, errors = {}, {}
    for offset in range(0, len(codes), BATCH_SIZE):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError('腾讯快照请求已停止')
        if offset:
            if cancel_event:
                if cancel_event.wait(.5):
                    raise InterruptedError('腾讯快照请求已停止')
            else:
                time.sleep(.5)
        batch = codes[offset:offset+BATCH_SIZE]
        request = urllib.request.Request('https://qt.gtimg.cn/q='+','.join(c.replace('.', '') for c in batch),
            headers={'Referer':'https://gu.qq.com/', 'User-Agent':'Mozilla/5.0'})
        try:
            with opener.open(request, timeout=8) as response:
                body = response.read(MAX_BODY+1)
            if len(body) > MAX_BODY:
                raise ValueError('腾讯快照响应过大')
            parsed = parse_tencent_quotes(body.decode('gbk'), batch, moment or datetime.now(CHINA))
            quotes.update(parsed)
            errors.update({c:'腾讯未返回有效身份、量价或时间字段' for c in batch if c not in parsed})
        except Exception as exc:
            errors.update({c:f'{type(exc).__name__}: {exc}' for c in codes[offset:]})
            break  # No retry storm and no repeated failing whole-market batches.
    return quotes, errors
