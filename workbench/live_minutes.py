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
PERIODS = {'m1': 1, 'm5': 5, 'm15': 15, 'm30': 30, 'm60': 60}
INTERVAL = 8          # seconds between reads while the session is open
MAX_INTERVAL = 60     # backoff ceiling after repeated provider failures
MAX_BARS = 640        # provider ceiling: about thirteen A-share sessions of 48 bars
DEFAULT_BARS = 320    # ~6.7 sessions: enough context to read structure intraday
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 8
URL = ('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline'
       '?param={symbol},{period},,{count}')
HEADERS = {'Referer': 'https://gu.qq.com/', 'User-Agent': 'Mozilla/5.0'}
STAMP = re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$')

# Exchange session grid, in minutes after midnight.
MORNING_OPEN, MORNING_CLOSE = 9 * 60 + 30, 11 * 60 + 30
AFTERNOON_OPEN, AFTERNOON_CLOSE = 13 * 60, 15 * 60
AUCTION = MORNING_OPEN      # 09:30 opening call auction prints as its own 1-minute bar


def session_closes(minutes=5):
    """Every valid bar-closing minute of one A-share session."""
    minutes = int(minutes)
    closes = set()
    for start, end in ((MORNING_OPEN, MORNING_CLOSE), (AFTERNOON_OPEN, AFTERNOON_CLOSE)):
        step = start + minutes
        while step <= end:
            closes.add(step)
            step += minutes
    if minutes == 1:
        # The auction is a standalone 09:30 print at one-minute resolution;
        # every coarser period merges it into its first bar instead.
        closes.add(AUCTION)
    return closes


def bar_close_of(minute_of_day, minutes):
    """Closing minute of the bar that contains ``minute_of_day``."""
    minutes = int(minutes)
    if minutes == 1:
        return int(minute_of_day)
    start = MORNING_OPEN if minute_of_day <= MORNING_CLOSE else AFTERNOON_OPEN
    if minute_of_day == start:
        return start + minutes     # the auction joins the first bar
    return start + math.ceil((minute_of_day - start) / minutes) * minutes



class MinuteSourceError(ValueError):
    """No verifiable 5-minute series; the caller must keep the previous frame."""


# A dedicated direct opener: a stale proxy environment variable must not decide
# whether the trader can see the session. Global proxy config stays untouched.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_minute_bars(text, code, moment, *, period='m5', count=DEFAULT_BARS):
    """Validate every row; Tencent labels each bar with its closing time."""
    code = code_of(code)
    minutes = PERIODS.get(str(period))
    if minutes is None:
        raise MinuteSourceError(f'不支持的分钟周期：{period}')
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise MinuteSourceError('腾讯分钟行情不是有效 JSON') from exc
    if not isinstance(payload, dict) or payload.get('code') not in (0, '0'):
        reason = payload.get('msg') if isinstance(payload, dict) else None
        raise MinuteSourceError('腾讯分钟行情返回错误状态' + (f'：{reason}' if reason else ''))
    block = (payload.get('data') or {}).get(code.replace('.', '')) or {}
    raw = block.get(str(period)) or []
    if not isinstance(raw, list) or len(raw) < 2:
        raise MinuteSourceError('腾讯未返回该标的的 5 分钟 K 线'
                                if minutes == 5 else f'腾讯未返回该标的的 {minutes} 分钟 K 线')
    if len(raw) > count:
        raise MinuteSourceError('腾讯返回的分钟 K 线超过请求数量')
    closes = session_closes(minutes)
    rows, previous = [], None
    last = len(raw) - 1
    for index, item in enumerate(raw):
        try:
            matched = STAMP.match(str(item[0]))
            opened, closed, high, low = (float(item[i]) for i in (1, 2, 3, 4))
            volume = float(item[5])
        except (IndexError, TypeError, ValueError) as exc:
            raise MinuteSourceError('腾讯分钟 K 线字段不完整') from exc
        if not matched:
            raise MinuteSourceError('腾讯分钟 K 线时间格式无效')
        stamp = datetime(*map(int, matched.groups()), tzinfo=CHINA)
        if stamp.hour * 60 + stamp.minute not in closes:
            raise MinuteSourceError(f'腾讯分钟 K 线不在 {minutes} 分钟收盘边界')
        # Tencent publishes the bar that is still forming, stamped with its own
        # closing minute -- five minutes ahead for m5, one minute for m1. Only
        # the final row may sit ahead of the local clock, and never by more than
        # one period, so a stale or fabricated earlier row is still rejected.
        # The extra minute is clock skew: the afternoon's first hour closes at
        # 14:00, and a local clock still reading 12:59:55 must not throw away
        # eight months of history over five seconds.
        limit = timedelta(minutes=minutes, seconds=60) if index == last else timedelta(0)
        if stamp > moment + limit:
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


def fetch_minute_bars(code, moment=None, *, period='m5', count=DEFAULT_BARS, timeout=TIMEOUT,
                      opener=None):
    """One bounded request; every failure surfaces instead of returning stale bars."""
    code = code_of(code)
    period = str(period)
    if period not in PERIODS:
        raise MinuteSourceError(f'不支持的分钟周期：{period}')
    count = max(2, min(int(count), MAX_BARS))
    moment = moment or datetime.now(CHINA)
    request = urllib.request.Request(
        URL.format(symbol=code.replace('.', ''), period=period, count=count), headers=HEADERS)
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
    return parse_minute_bars(text, code, moment, period=period, count=count)


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


# ---- composition ---------------------------------------------------------

def _has_auction(value, day):
    return bool(((value['_day'] == day) & (value['_slot'] == AUCTION)).any())


def aggregate_minute_bars(frame, minutes):
    """Collapse one-minute rows into a coarser closing-labelled unit.

    A window truncated by the provider's row ceiling is dropped, because its
    open would belong to a later minute than the real bar.  The newest window
    is kept while it is still forming: that is what a live chart draws, and it
    is the difference between a 10:03 reading and an empty morning.
    """
    minutes = int(minutes)
    if minutes not in PERIODS.values() or minutes == 1:
        raise ValueError('分钟聚合仅支持 5、15、30、60 分钟')
    value = frame.copy()
    stamp = pd.to_datetime(value['date'])
    value['_slot'] = [bar_close_of(int(t.hour) * 60 + int(t.minute), minutes) for t in stamp]
    value['_day'] = stamp.dt.strftime('%Y-%m-%d')
    groups = list(value.groupby(['_day', '_slot'], sort=True))
    if not groups:
        raise ValueError('分钟行情为空')
    (newest_day, newest_slot) = groups[-1][0]
    rows = []
    for (day, slot), group in groups:
        # m1 can omit the 13:00 print included by native m5: keep native OHLC.
        if slot == AFTERNOON_OPEN + minutes:
            continue
        # 09:35 carries the opening call auction on top of five minutes.
        expected = minutes + 1 if (slot == MORNING_OPEN + minutes and _has_auction(value, day)) \
            else minutes
        if len(group) < expected and (day, slot) != (newest_day, newest_slot):
            continue          # truncated head or a hole: never repaired
        first = pd.Timestamp(group.date.iloc[0])
        required_start = AUCTION if expected == minutes + 1 else slot - minutes + 1
        stamps = pd.to_datetime(group.date)
        if (first.hour * 60 + first.minute != required_start
                or not stamps.diff().iloc[1:].eq(timedelta(minutes=1)).all()):
            continue          # even a forming tail needs its real open
        # The slot is the bar's own close, not the minute that happened to arrive
        # last: a window that is still forming must be dated where it will end,
        # or it stops replacing the skeleton bar it belongs to and shows up as a
        # second candle with a timestamp off the five-minute grid.
        rows.append(dict(date=f'{day} {slot // 60:02d}:{slot % 60:02d}',
                         open=float(group.open.iloc[0]),
                         high=float(group.high.max()), low=float(group.low.min()),
                         close=float(group.close.iloc[-1]), volume=float(group.volume.sum())))
    if not rows:
        raise ValueError('没有完整的 5 分钟窗口；不会用缺失分钟线拼接')
    return pd.DataFrame(rows)


def merge_live_tail(skeleton, minutes_frame, *, minutes=5):
    """Overlay bars built from verified one-minute rows onto the skeleton.

    The provider caps every request at 640 rows, so one-minute data reaches
    about two sessions while five-minute data reaches thirteen.  Keeping both
    and letting the fine one win on the newest session preserves context and
    freshness at once; a failed fine read simply leaves the skeleton alone.
    """
    if minutes_frame is None or skeleton is None or len(minutes_frame) < 2:
        return skeleton
    try:
        derived = aggregate_minute_bars(minutes_frame, minutes)
    except ValueError:
        return skeleton
    if derived is None or derived.empty:
        return skeleton
    # Compare as timestamps, not strings: a skeleton row written as "...14:30:00"
    # and a derived row written as "...14:30" are the same bar, and a missed
    # match would show up as two candles for one close.
    stamped = pd.to_datetime(skeleton['date'])
    keep = skeleton[~stamped.isin(pd.to_datetime(derived['date']))]
    merged = pd.concat([keep, derived], ignore_index=True)
    return merged.sort_values('date').reset_index(drop=True)


def halfday_from_hourly(history, *, per_half=2):
    """Merge the provider's native 60-minute bars into one bar per half session.

    The half-day unit is two bars a day -- morning 09:30-11:30 closes at 11:30,
    afternoon 13:00-15:00 closes at 15:00 -- so the eight months of hourly
    history has to be halved before it can stand in for a 4-hour series.  A half
    session that does not hold both of its hours is dropped rather than
    averaged into something the exchange never printed; the live minutes fill
    the newest half anyway.
    """
    if history is None or len(history) == 0:
        return history
    value = history.copy()
    stamp = pd.to_datetime(value['date'])
    value['_day'] = stamp.dt.strftime('%Y-%m-%d')
    value['_half'] = [0 if int(t.hour) * 60 + int(t.minute) <= MORNING_CLOSE else 1
                      for t in stamp]
    rows = []
    for (_day, _half), group in value.groupby(['_day', '_half'], sort=True):
        if len(group) < per_half:
            continue
        rows.append(dict(date=str(group.date.iloc[-1]),
                         open=float(group.open.iloc[0]),
                         high=float(group.high.max()), low=float(group.low.min()),
                         close=float(group.close.iloc[-1]),
                         volume=float(group.volume.sum())))
    if not rows:
        return None
    return pd.DataFrame(rows)


def coarse_series(history, live, minutes):
    """Replace the newest bar of a coarse unit with one built from live minutes.

    ``history`` is the provider's own 60-minute series (about eight months) and
    ``live`` is the five-minute frame being polled.  The half-day unit needs that
    history halved first: keeping hourly bars would leave 10:30 and 14:00 in a
    series that is only supposed to hold 11:30 and 15:00.  Only the bars that
    the live frame can speak for are replaced, so a coarse bar is never dropped
    for lack of history that the provider simply does not serve any more.
    """
    from .chart_workspace import aggregate_minutes
    if int(minutes) == 240:
        history = halfday_from_hourly(history)
    if live is None or len(live) < 2:
        return history
    try:
        derived = aggregate_minutes(live, int(minutes), allow_open_tail=True,
                                    closing_labelled=True)
    except ValueError:
        return history
    if derived is None or derived.empty:
        return history
    if history is None or history.empty:
        return derived
    # Timestamps, not strings, so "...11:30:00" and "...11:30" are one bar.
    stamped = pd.to_datetime(history['date'])
    keep = history[~stamped.isin(pd.to_datetime(derived['date']))]
    merged = pd.concat([keep, derived], ignore_index=True)
    return merged.sort_values('date').reset_index(drop=True)


class HourlyHistory:
    """One provider 60-minute read per symbol, kept off the Tk thread.

    The 60-minute series is the only source of an eight-month intraday context;
    it grows by four bars a session, so it is refetched once an hour rather than
    on every poll.  Without that refresh a session left open past thirteen
    trading days would slowly lose the dates the five-minute skeleton can no
    longer reach.  Results are handed back through a queue that the caller
    drains on its own timer, which keeps every network call out of the UI
    thread.
    """

    REFRESH_SECONDS = 3600
    RETRY_SECONDS = 60

    def __init__(self, fetch=fetch_minute_bars, clock=time.monotonic):
        self.fetch = fetch
        self.clock = clock
        self.frames = {}
        self.pending = set()
        self.results = queue.Queue()
        self._at = {}
        self._failed_at = {}

    def request(self, code):
        code = code_of(code)
        if code in self.pending:
            return False
        now = self.clock()
        settled = self._at.get(code)
        if code in self.frames and settled is not None \
                and now - settled < self.REFRESH_SECONDS:
            return False
        failed = self._failed_at.get(code)
        if failed is not None and now - failed < self.RETRY_SECONDS:
            return False
        self.pending.add(code)
        threading.Thread(target=self._work, args=(code,), daemon=True,
                         name='hourly-history').start()
        return True

    def _work(self, code):
        try:
            frame = self.fetch(code, period='m60', count=MAX_BARS)
        except Exception:
            frame = None
        self.results.put((code, frame))

    def take(self, *, successful_only=False):
        """Codes whose read settled since the last drain, failed ones included."""
        landed = []
        while True:
            try:
                code, frame = self.results.get_nowait()
            except queue.Empty:
                return landed
            self.pending.discard(code)
            if frame is not None and len(frame):
                landed.append(code)
                self.frames[code] = frame
                self._at[code] = self.clock()
                self._failed_at.pop(code, None)
            else:
                if not successful_only:
                    landed.append(code)
                # Keep whatever was cached: eight months of context is not worth
                # losing to one failed read.  Back off briefly instead of
                # hammering the provider on every poll.
                self._failed_at[code] = self.clock()

    def history(self, code):
        return self.frames.get(code_of(code))


class IntradaySource:
    """One-minute tail over a five-minute skeleton, cached per symbol and session.

    Only the one-minute request repeats while the market is open; the skeleton
    is re-read every few minutes because it is the only source of a long
    five-minute context.  A blocked one-minute read degrades to the skeleton
    instead of blanking a chart the trader is reading.
    """

    SKELETON_SECONDS = 900

    def __init__(self, fetch=fetch_minute_bars, clock=lambda: datetime.now(CHINA)):
        self.fetch = fetch
        self.clock = clock
        self._skeleton = {}
        self._at = {}

    def skeleton(self, code, moment=None):
        moment = moment or self.clock()
        code = code_of(code)
        held, at = self._skeleton.get(code), self._at.get(code)
        fresh = held is not None and (moment - at).total_seconds() <= self.SKELETON_SECONDS
        if fresh:
            return held
        try:
            held = self.fetch(code, moment, period='m5', count=MAX_BARS)
        except Exception:
            return self._skeleton.get(code)
        self._skeleton[code] = held
        self._at[code] = moment
        return held

    def __call__(self, code, moment=None):
        moment = moment or self.clock()
        skeleton = self.skeleton(code, moment)
        try:
            tail = self.fetch(code, moment, period='m1', count=MAX_BARS)
        except Exception:
            if skeleton is not None and len(skeleton) >= 2:
                return skeleton
            raise
        merged = merge_live_tail(skeleton, tail)
        if merged is None and tail is not None:
            # The skeleton is blocked but the tail is not: a short five-minute
            # series built from one-minute rows still beats an empty chart.
            try:
                merged = aggregate_minute_bars(tail, 5)
            except ValueError:
                merged = None
        if merged is None or len(merged) < 2:
            raise MinuteSourceError('分钟行情为空')
        return merged

    def forget(self, code):
        code = code_of(code)
        self._skeleton.pop(code, None)
        self._at.pop(code, None)



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
