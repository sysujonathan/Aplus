"""Bounded, cancellable display refreshes. Workers never access Tk widgets."""
import json
from pathlib import Path
import queue
import sqlite3
import threading


class LatestRefresh:
    def __init__(self, load):
        self.load = load
        self.results = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.token = 0
        self.pending = None
        self.active = None
        self.running = self.closed = False

    def submit(self, value):
        with self.lock:
            if self.closed:
                return None
            self.token += 1
            if self.active:
                self.active.set()
            self.pending = (self.token, value, threading.Event())
            if not self.running:
                self.running = True
                threading.Thread(target=self._run, daemon=True, name='display-refresh').start()
            return self.token

    def _run(self):
        while True:
            with self.lock:
                if self.closed or self.pending is None:
                    self.running = False
                    return
                token, value, cancelled = self.pending
                self.pending = None
                self.active = cancelled
            def check():
                if cancelled.is_set():
                    raise InterruptedError('显示请求已被替代')
            result, error = None, None
            try:
                check()
                result = self.load(value, check)
                check()
            except Exception as exc:
                error = exc
            with self.lock:
                self.active = None
                if not self.closed and token == self.token:
                    try:
                        self.results.get_nowait()
                    except queue.Empty:
                        pass
                    self.results.put_nowait((token, value, result, error))

    def poll(self):
        latest = None
        while True:
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                return latest
            if result[0] == self.token and not self.closed:
                latest = result

    def close(self):
        with self.lock:
            self.closed = True
            self.pending = None
            if self.active:
                self.active.set()


class ReadView:
    """Existing data only: no Store initialization, migration or writable DB."""
    def __init__(self, root, source):
        self.root = Path(root)
        self.path = self.root/'workbench.sqlite3'
        self.source = source

    def rows(self, sql, args=()):
        if sql == "SELECT value FROM meta WHERE key='daily_market_source'":
            return [{'value': self.source}]
        db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute(sql, args)]
        finally:
            db.close()

    def gap_review_decisions(self, source):
        rows = self.rows('SELECT value FROM meta WHERE key=?', ('gap_review:'+source,))
        return json.loads(rows[0]['value']) if rows else {}

    def read_artifact(self, relative, job=None):
        path = (self.root/relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('不允许越过运行目录读取文件')
        return path.read_bytes()  # Display reads never compete to write audit events.


def display_store(store):
    from workbench.store import Store
    from workbench.sources import market_source
    return ReadView(store.root, market_source(store)) if isinstance(store, Store) else store


def load_job(root, job, check):
    """Bounded read-only progress lookup. No Tk, Store init or write events."""
    check()
    rows = ReadView(root, None).rows(
        'SELECT status,progress,total,message,result FROM jobs WHERE id=?', (job,))
    check()
    return rows[0] if rows else None


def load_status(root, context, check):
    from .data import latest_market_date, latest_scan_date
    from workbench.readiness import audit_scope
    from workbench.gap_review import pending_count
    source, boards, day, timeframe = context
    view = ReadView(root, source)
    check()
    market, signal = latest_market_date(view, timeframe), latest_scan_date(view, timeframe)
    audit = audit_scope(view, list(boards), day, source=source, timeframe=timeframe,
                        history_cache_only=True, check_stop=check)
    check()
    count = pending_count(view, audit, timeframe) if audit.get('scope_valid') else 0
    return dict(market=market, signal=signal, audit=audit, pending=count)
