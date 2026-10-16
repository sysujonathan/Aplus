"""Coordinate short local checkpoints, never serialize vendor downloads.

Only the in-process premarket workers use this adapter. Schema, SQL, commit /
rollback and artifact ownership remain in Store. Writers share the existing
artifact -> DB lock order; independent SQL reads are read-only. Network and
strategy calculations stay outside writer connection scopes. Read audit rows
are bounded to 100 before commit and flushed at every normal/error stage exit.
"""
from contextlib import contextmanager
import sqlite3
import threading
import time

from .store import Store, dumps, now


class PremarketStore(Store):
    def __init__(self, root=None):
        self._metrics = dict(write_connections=0, write_lock_wait_seconds=0., read_queries=0,
                             read_seconds=0., buffered_read_events=0)
        self._metrics_lock = threading.Lock()
        self._read_events = []
        super().__init__(root)

    def metrics(self):
        with self._metrics_lock:
            return dict(self._metrics)

    @contextmanager
    def connect(self):
        if getattr(self._atomic_local, 'connection', None) is not None:
            with super().connect() as db:
                yield db
            return
        started = time.monotonic()
        with self._files_lock:
            with self._metrics_lock:
                self._metrics['write_lock_wait_seconds'] += time.monotonic() - started
                self._metrics['write_connections'] += 1
            with super().connect() as db:
                yield db

    def rows(self, sql, args=()):
        # Read-your-writes inside a checkpoint; outside it do not queue a read
        # behind another source's file serialization. SQLite supplies committed
        # snapshots; mode=ro makes accidental mutation fail closed.
        active = getattr(self._atomic_local, 'connection', None)
        if active is not None:
            return [dict(r) for r in active.execute(sql, args)]
        started = time.monotonic()
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args)]
        finally:
            db.close()
            with self._metrics_lock:
                self._metrics['read_queries'] += 1
                self._metrics['read_seconds'] += time.monotonic() - started

    def event(self, job, action, path='', **detail):
        if action != '读取文件' or getattr(self._atomic_local, 'connection', None) is not None:
            return super().event(job, action, path, **detail)
        # Preserve every original audit row, timestamp and path, but commit
        # bounded groups rather than one transaction per read-only file access.
        self._read_events.append((now(), job, action, str(path), dumps(detail)))
        with self._metrics_lock:
            self._metrics['buffered_read_events'] += 1
        if len(self._read_events) >= 100:
            self.flush_reads()

    def flush_reads(self):
        if not self._read_events:
            return
        with self.connect() as db:
            db.executemany('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)', self._read_events)
        self._read_events.clear()
