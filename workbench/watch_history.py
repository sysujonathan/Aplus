"""Per-security chart history, separate from scan snapshots and trade records.

Local validated prices first. A single bounded vendor request may populate an
immutable chart-only cache; it never grants scan coverage or creates a signal.
"""
from datetime import date, timedelta
import io
import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager

import pandas as pd

from .market import code_of, completed_date, load_dataset, validate_bars
from .provider_guard import ProviderGuard, ProviderError
from .sources import market_source, UPDATE_SOURCES
from .store import digest, dumps

_CHART_LOCKS = {source: threading.Lock() for source in UPDATE_SOURCES}


@contextmanager
def _serial_request(source, cancelled):
    lock = _CHART_LOCKS[source]
    while not lock.acquire(timeout=.1):
        if cancelled.is_set():
            raise InterruptedError('已取消等待K图行情')
    try:
        yield
    finally:
        lock.release()


def _request_gap(cancelled, duration=1.5, *, clock=time.monotonic, wait=time.sleep):
    deadline = clock() + duration
    while clock() < deadline:
        if cancelled.is_set():
            raise InterruptedError('已取消等待K图请求间隔')
        wait(min(.1, max(0, deadline - clock())))


def chart_source(store, code):
    code = code_of(code)
    # The stock directory contract does not certify ETF support in TickFlow.
    # ETF charting uses the existing BaoStock unadjusted on-demand route.
    if code.startswith(('sh.5', 'sz.1')):
        return 'baostock'
    source = market_source(store)
    return source if source in UPDATE_SOURCES else 'baostock'


def chart_day():
    from .exchange_calendar import is_trading_day
    day = date.fromisoformat(completed_date())
    for _ in range(40):
        state = is_trading_day(day)
        if state is not False:
            return day.isoformat()  # An unknown year is never guessed closed.
        day -= timedelta(days=1)
    raise ValueError('近期交易日期无法确认')


class _ChartView:
    def __init__(self, store):
        self.root = store.root.resolve()

    def rows(self, sql, args=()):
        with closing(sqlite3.connect((self.root / 'workbench.sqlite3').as_uri() + '?mode=ro',
                                     uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def read_artifact(self, relative, job=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('K图文件越过数据目录')
        return path.read_bytes()


def cached_history(store, code, source, adjustment=None):
    """Read only a verified chart cache; never infer identity from its filename."""
    code = code_of(code)
    if adjustment is not None and adjustment != '不复权':
        return None
    view = _ChartView(store)
    saved = view.rows('SELECT value FROM meta WHERE key=?', ('watch_chart:' + source + ':' + code,))
    if not saved:
        return None
    record = json.loads(saved[0]['value'])
    if (record['source'] != source or record['code'] != code
            or record['adjustment'] != '不复权' or record['timeframe'] != 'daily'):
        raise ValueError('关注K图缓存身份不一致')
    prefix = f'watch_history/{source}/{code}/'
    sha = record['sha256']
    if (not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
            or record['path'] != prefix + sha + '.csv' or record['id'] != 'watch-' + sha):
        raise ValueError('关注K图缓存路径无效')
    raw = view.read_artifact(record['path'])
    if digest(raw) != sha:
        raise ValueError('关注K图缓存校验失败')
    frame = validate_bars(pd.read_csv(io.BytesIO(raw)))
    if frame.date.iloc[0] != record['start'] or frame.date.iloc[-1] != record['end']:
        raise ValueError('关注K图缓存日期不一致')
    return frame, record


def local_history(store, code, source=None):
    code = code_of(code)
    source = source or chart_source(store, code)
    view = _ChartView(store)
    candidates = []
    rows = view.rows("SELECT id FROM datasets WHERE code=? AND source=? AND timeframe='daily' "
                     "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code, source))
    if rows:
        try:
            candidates.append(load_dataset(view, rows[0]['id']))
        except (ValueError, OSError):
            pass  # A bad snapshot cannot be displayed; the chart may refetch.
    try:
        cached = cached_history(store, code, source)
        if cached:
            candidates.append(cached)
    except (ValueError, OSError, KeyError, TypeError):
        pass  # Display may refetch; alert callers instead surface validation failures.
    # A successful chart request can legitimately return the same final day
    # as an older scan snapshot. Keep its query watermark in that tie, or each
    # reopen would incorrectly schedule the same network request again.
    return max(candidates, key=lambda item: (item[1]['end'], item[1].get('checked_through', ''))) if candidates else None


def fetch_history(store, code, source, cancel_event, *, provider_factory=None, end=None):
    """One security, one attempt, existing global lease/guard, no source fallback."""
    code = code_of(code)
    if source not in UPDATE_SOURCES or source != chart_source(store, code):
        raise ValueError('关注K图来源已变化，请重新选择标的')
    # Multiple visible tiles may need history together. Queue locally instead
    # of opening parallel connections or turning LOCAL_BUSY into retry loops.
    with _serial_request(source, cancel_event):
        return _fetch_once(store, code, source, cancel_event, provider_factory, end)


def _fetch_once(store, code, source, cancel_event, provider_factory, end):
    end = end or chart_day()
    start = (date.fromisoformat(end) - timedelta(days=730)).isoformat()
    if cancel_event.is_set():
        raise InterruptedError('已取消获取K图')
    guard = ProviderGuard(store, source)
    guard.check()  # Must precede even constructing/opening the provider.
    if provider_factory is None:
        if source == 'tickflow':
            from .tickflow import TickFlow
            provider_factory = TickFlow
        else:
            from .market import BaoStock
            provider_factory = BaoStock
    provider = provider_factory()
    provider.cancel_event = cancel_event
    provider.login_timeout, provider.query_timeout = 8, 20
    try:
        with provider:
            if source == 'tickflow':
                # Each chart owns a fresh subprocess. Its HTTP timestamp starts
                # empty, so a full gap under the OS lease is needed even when
                # handing off from another session's last request.
                _request_gap(cancel_event)
            try:
                frame = validate_bars(provider.fetch_unadjusted(code, start, end))
            finally:
                if source == 'tickflow':
                    # A business task may cancel this chart and start a fresh
                    # HTTP session. Keep its first request separated as well.
                    # This bounded cleanup intentionally completes even after
                    # cancellation; Service waits for chart_access to release.
                    _request_gap(threading.Event())
    except (ProviderError, TimeoutError) as exc:
        if getattr(exc, 'code', None) != 'LOCAL_BUSY':
            guard.failure(exc)
        raise
    if cancel_event.is_set():
        raise InterruptedError('已取消获取K图')
    if frame.date.iloc[0] < start or frame.date.iloc[-1] > end:
        raise ValueError('关注K图日期越过请求范围')
    raw = frame.to_csv(index=False).encode('utf-8')
    sha = digest(raw)
    record = dict(id='watch-' + sha, code=code, source=source, adjustment='不复权',
                  timeframe='daily', start=frame.date.iloc[0], end=frame.date.iloc[-1],
                  sha256=sha, path=f'watch_history/{source}/{code}/{sha}.csv', checked_through=end)
    with store.atomic_write():
        existing = store.root / record['path']
        if existing.exists() and digest(existing.read_bytes()) != sha:
            raise ValueError('已有关注K图文件校验失败，拒绝覆盖')
        store.write_artifact(record['path'], raw)
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      ('watch_chart:' + source + ':' + code, dumps(record)))
    return frame, record
