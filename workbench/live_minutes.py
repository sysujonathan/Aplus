"""Live 5-minute bars for reading the session in progress.

The provider snapshot is ephemeral transport, not market history: it is never
written to Store datasets, never certified as a daily bar, and never fed to
frozen strategies or the trade ledger. A response that cannot be verified row
by row is rejected, because a repaired guess is worse than a missing update.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import queue
import re
import threading
import time
import urllib.request

import pandas as pd

from .market import code_of
from .tencent_quotes import volume_scale

CHINA = timezone(timedelta(hours=8))
INTERVAL = 8          # seconds between reads while the session is open
MAX_INTERVAL = 60     # backoff ceiling after repeated provider failures
MAX_BARS = 640        # provider ceiling: about thirteen A-share sessions of 48 bars
DEFAULT_BARS = 320    # ~6.7 sessions: enough context to read structure intraday
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 8
URL = ('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline'
       '?param={symbol},m5,,{count}')
HEADERS = {'Referer': 'https://gu.qq.com/', 'User-Agent': 'Mozilla/5.0'}
STAMP = re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$')


class MinuteSourceError(ValueError):
    """No verifiable 5-minute series; the caller must keep the previous frame."""


# A dedicated direct opener: a stale proxy environment variable must not decide
# whether the trader can see the session. Global proxy config stays untouched.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_minute_bars(text, code, moment, *, count=DEFAULT_BARS):
    """Validate every row; Tencent labels each bar with its closing time."""
    code = code_of(code)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise MinuteSourceError('腾讯分钟行情不是有效 JSON') from exc
    if not isinstance(payload, dict) or payload.get('code') not in (0, '0'):
        reason = payload.get('msg') if isinstance(payload, dict) else None
        raise MinuteSourceError('腾讯分钟行情返回错误状态' + (f'：{reason}' if reason else ''))
    block = (payload.get('data') or {}).get(code.replace('.', '')) or {}
    raw = block.get('m5') or []
    if not isinstance(raw, list) or len(raw) < 2:
        raise MinuteSourceError('腾讯未返回该标的的 5 分钟 K 线')
    if len(raw) > count:
        raise MinuteSourceError('腾讯返回的分钟 K 线超过请求数量')
    rows, previous = [], None
    for item in raw:
        try:
            matched = STAMP.match(str(item[0]))
            opened, closed, high, low = (float(item[i]) for i in (1, 2, 3, 4))
            volume = float(item[5])
        except (IndexError, TypeError, ValueError) as exc:
            raise MinuteSourceError('腾讯分钟 K 线字段不完整') from exc
        if not matched:
            raise MinuteSourceError('腾讯分钟 K 线时间格式无效')
        stamp = datetime(*map(int, matched.groups()), tzinfo=CHINA)
        if stamp.minute % 5:
            raise MinuteSourceError('腾讯分钟 K 线不在 5 分钟边界')
        if stamp > moment + timedelta(seconds=60):
            raise MinuteSourceError('腾讯返回了本机当前时间之后的分钟 K 线')
        if previous is not None and stamp <= previous:
            raise MinuteSourceError('腾讯分钟 K 线时间未严格递增')
        prices = (opened, closed, high, low)
        if not all(math.isfinite(value) and value > 0 for value in prices) or not math.isfinite(volume):
            raise MinuteSourceError('腾讯分钟 K 线含无效价格或成交量')
        if volume < 0 or high < max(opened, closed, low) or low > min(opened, closed, high):
            raise MinuteSourceError('腾讯分钟 K 线 OHLC 关系矛盾')
        previous = stamp
        rows.append(dict(date=stamp.strftime('%Y-%m-%d %H:%M'), open=opened, high=high,
                         low=low, close=closed, volume=volume * volume_scale(code)))
    return pd.DataFrame(rows)


def fetch_minute_bars(code, moment=None, *, count=DEFAULT_BARS, timeout=TIMEOUT, opener=None):
    """One bounded request; every failure surfaces instead of returning stale bars."""
    code = code_of(code)
    count = max(2, min(int(count), MAX_BARS))
    moment = moment or datetime.now(CHINA)
    request = urllib.request.Request(
        URL.format(symbol=code.replace('.', ''), count=count), headers=HEADERS)
    open_url = opener or OPENER.open
    try:
        with open_url(request, timeout=timeout) as response:
            body = response.read(MAX_BODY + 1)
    except Exception as exc:
        raise MinuteSourceError(f'腾讯分钟行情请求失败：{type(exc).__name__}: {exc}') from exc
    if len(body) > MAX_BODY:
        raise MinuteSourceError('腾讯分钟行情响应超过上限')
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise MinuteSourceError('腾讯分钟行情响应编码无效') from exc
    return parse_minute_bars(text, code, moment, count=count)


def minute_rows(frame):
    """Front-end rows; volume stays in shares so the axis matches daily charts."""
    required = ('date', 'open', 'high', 'low', 'close', 'volume')
    missing = [key for key in required if key not in frame]
    if missing:
        raise ValueError('分钟行情缺少字段：' + '、'.join(missing))
    rows = []
    for row in frame.loc[:, required].itertuples(index=False):
        values = [float(row.open), float(row.high), float(row.low), float(row.close)]
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError('分钟行情含无效价格')
        rows.append({'date': str(row.date), 'open': values[0], 'high': values[1],
                     'low': values[2], 'close': values[3], 'volume': float(row.volume)})
    if not rows:
        raise ValueError('分钟行情为空')
    return rows


class MinutePoller:
    """One daemon worker; a stale result never replaces a newer symbol."""

    def __init__(self, fetch=fetch_minute_bars, clock=lambda: datetime.now(CHINA)):
        self.fetch = fetch
        self.clock = clock
        self.results = queue.Queue()
        self.running = 0
        self.started_generation = -1
        self.generation = 0
        self.failures = 0
        self.code = None

    @property
    def busy(self):
        return self.running > 0

    def invalidate(self):
        self.generation += 1
        self.failures = 0

    def start(self, code, *, attempts=1):
        # A worker belonging to a superseded request never blocks the new one;
        # its late result is discarded by generation instead.
        if self.running and self.started_generation == self.generation:
            return False
        self.code = code_of(code)
        self.started_generation = generation = self.generation
        code, attempts = self.code, max(1, int(attempts))
        self.running += 1

        def work():
            moment, error = self.clock(), None
            for attempt in range(attempts):
                try:
                    self.results.put((generation, code, self.fetch(code, moment), None))
                    return
                except Exception as exc:
                    error = f'{type(exc).__name__}: {exc}'
                    if attempt + 1 < attempts:
                        time.sleep(.6)
            self.results.put((generation, code, None, error))

        threading.Thread(target=work, daemon=True, name='live-minutes').start()
        return True

    def take(self):
        """Newest settled result for the current symbol, or None while running."""
        latest = None
        while True:
            try:
                generation, code, frame, error = self.results.get_nowait()
            except queue.Empty:
                return latest
            self.running = max(0, self.running - 1)
            if generation != self.generation or code != self.code:
                continue
            self.failures = min(self.failures + 1, 4) if error else 0
            latest = (frame, error)

    @property
    def delay(self):
        return min(INTERVAL * 2 ** self.failures, MAX_INTERVAL)
