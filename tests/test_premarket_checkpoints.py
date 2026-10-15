"""Concurrent synthetic checkpoints; no network or installed runtime access."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
import threading
import time

import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_premarket_flow import multi_spec
from workbench.market import save_dataset
from workbench.premarket_store import PremarketStore
from workbench.provider_guard import ProviderGuard
from workbench.service import Service
from workbench.store import Store


def workers(store):
    result = [PremarketStore(store.root) for _ in range(3)]
    for item in result:
        item._files_lock = store._files_lock
    return result


def test_three_sources_and_receipts_serialize_short_connections_only(store, frame, monkeypatch):
    stores = workers(store)
    original = Store.connect
    counts = dict(active=0, peak=0)
    guard = threading.Lock()
    @contextmanager
    def measured(self):
        if getattr(self._atomic_local,'connection',None) is not None:
            with original(self) as db:
                yield db
            return
        with original(self) as db:
            with guard:
                counts['active'] += 1
                counts['peak'] = max(counts['active'],counts['peak'])
            try:
                time.sleep(.001)  # Deterministically expose overlapping connections.
                yield db
            finally:
                with guard: counts['active'] -= 1
    monkeypatch.setattr(Store,'connect',measured)
    def save(index):
        st = stores[index]
        for offset in range(8):
            with st.atomic_write():
                did = save_dataset(st,f'sh.{600000+offset}',frame,
                                   ('baostock','tickflow','tencent')[index],'fixture')
                st.execute('INSERT INTO meta VALUES(?,?)', (f'unit:{index}:{offset}',did))
            st.execute('INSERT INTO meta VALUES(?,?)', (f'receipt:{index}:{offset}','saved'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(save,range(3)))
    assert counts == dict(active=0,peak=1)
    assert len(store.rows('SELECT id FROM datasets')) == 24
    assert len(store.rows("SELECT key FROM meta WHERE key LIKE 'unit:%'")) == 24
    assert len(store.rows("SELECT key FROM meta WHERE key LIKE 'receipt:%'")) == 24
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'] == '8'


def test_file_lock_precedes_db_writer_so_other_source_cannot_invert_locks(store):
    first, second, _ = workers(store)
    attempted, wrote = threading.Event(), threading.Event()
    def other_source():
        attempted.set()
        with second.connect() as db:
            db.execute("INSERT INTO meta VALUES('second-writer','ok')")
            wrote.set()
            with second._files_lock:
                assert second.root.exists()
        second.write_artifact('fixture/second.txt',b'second')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.atomic_write():
            future = pool.submit(other_source)
            assert attempted.wait(2)
            # The second source must not hold SQLite's writer lock while
            # waiting for the first source's artifact lock.
            assert not wrote.wait(.1)
            first.execute("INSERT INTO meta VALUES('first-writer','ok')")
            first.write_artifact('fixture/first.txt',b'first')
        future.result(timeout=3)
    assert wrote.is_set()
    assert len(store.rows("SELECT key FROM meta WHERE key IN ('first-writer','second-writer')")) == 2


def test_failed_checkpoint_rolls_back_and_releases_next_source(store, frame):
    first, second, _ = workers(store)
    with pytest.raises(ValueError,match='fixture failure'):
        with first.atomic_write():
            save_dataset(first,'sh.600000',frame,'tickflow','fixture')
            first.execute("INSERT INTO meta VALUES('rolled-back','x')")
            raise ValueError('fixture failure')
    assert not store.rows("SELECT key FROM meta WHERE key='rolled-back'")
    assert not store.rows('SELECT id FROM datasets')
    with ThreadPoolExecutor(max_workers=1) as pool:
        did = pool.submit(save_dataset,second,'sh.600000',frame,'tencent','fixture').result(timeout=3)
    assert store.rows('SELECT source FROM datasets WHERE id=?',(did,))[0]['source'] == 'tencent'


def test_local_failure_keeps_source_progress_diagnostics_and_other_sources(store, monkeypatch):
    def sync(self, job, spec):
        self.progress(job,23,100,'保存进度')
        if spec['source'] == 'tickflow':
            raise sqlite3.OperationalError('database is locked')
        return dict(success=23)
    monkeypatch.setattr(Service,'_sync',sync)
    monkeypatch.setattr(Service,'_scan',lambda *_a,**_k:dict(success=1))
    service = Service(store)
    try:
        row = wait_for(store,service.submit('premarket',multi_spec()))
        report = json.loads(row['result'])
        item = report['sources']['tickflow']
        assert row['status'] == 'partial'
        assert (item['phase'],item['progress'],item['total']) == ('失败',23,100)
        assert item['sync_result']['errors'][0]['error'] == 'database is locked'
        assert all(report['sources'][s]['scan_status']=='completed' for s in ('baostock','tencent'))
        event = store.rows('SELECT detail FROM events WHERE job_id=? AND action=?',
                           (item['sync_job'],'一键盘前子任务异常'))
        assert 'OperationalError' in json.loads(event[0]['detail'])['traceback']
        assert not ProviderGuard(store,'tickflow').state()  # Local IO is not vendor abuse.
    finally:
        service.pool.shutdown()
