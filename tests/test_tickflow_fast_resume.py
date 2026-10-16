"""Isolated performance/safety regressions; no vendor or installed runtime IO."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_premarket_resume import seeded, proxy
from workbench.market import save_dataset, load_dataset
from workbench.premarket import resume_summary
from workbench.premarket_resume import continue_sync, ordinary_update_codes
from workbench.premarket_store import PremarketStore
from workbench.provider_guard import ProviderGuard, ProviderError
from workbench.service import Service
from workbench.sources import save_coverage, set_market_source, update_selection, UPDATE_SOURCES
from workbench.store import dumps


def tail_fixture(store, frame, source='tickflow'):
    codes = ('sh.600000', 'sh.600001', 'sh.600002')
    start, day, _ = seeded(store, frame, source, codes=codes)
    # Latest-dataset ordering deliberately prefers end date: start with a
    # genuine interrupted prior run rather than hiding a newer saved snapshot.
    store.execute('DELETE FROM datasets WHERE code IN (?,?)', codes[:2])
    for code in codes[:2]:
        did = save_dataset(store, code, frame.iloc[:-1], source, '前复权')
        save_coverage(store, code, did, start, frame.date.iloc[-2], source)
    return start, day, codes


@pytest.mark.parametrize('timeframe', ['daily', 'weekly'])
def test_interrupted_tickflow_tail_uses_one_ordinary_batch_without_gap_planning(store, frame, monkeypatch, timeframe):
    start, day, codes = tail_fixture(store, frame)
    preloads, planning_reads = [], []
    import workbench.sync as sync
    original = sync.local_history
    def counted(*args, **kwargs):
        if not preloads: planning_reads.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(sync, 'local_history', counted)
    class Provider:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def universe(self, *_): pytest.fail('同日目录应复用')
        def preload(self, group, a, b): preloads.append((list(group), a, b))
        def fetch(self, code, a, b): return frame.loc[frame.date.between(a, b)].copy()
    monkeypatch.setattr('workbench.tickflow.TickFlow', Provider)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('sync', dict(source='tickflow', boards=['沪深主板'],
             start=start, end=day, scan_timeframe=timeframe, resume=True)))
        result = json.loads(row['result'])
        assert row['status'] == ('completed' if timeframe == 'daily' else 'partial'), row['result']
        assert preloads == [(list(codes[:2]), frame.date.iloc[-2], day)]
        assert planning_reads == []  # Previously one full CSV/gap analysis per stock.
        assert result['resume']['ordinary_increment']
        assert not result['resume']['repair_codes']
        assert result['requested'] == 2 and result['updated'] == 2
        assert result['coverage']['complete']
        assert result['scan_readiness']['complete'] == (timeframe == 'daily')
        assert len(store.rows('SELECT id FROM datasets')) == 5
    finally:
        service.pool.shutdown()


def test_ordinary_routing_does_not_hide_interior_holes_or_trust_corrupt_finished_file(store, frame):
    start, day, codes = tail_fixture(store, frame)
    did = save_dataset(store, codes[2], frame.drop(index=100), 'tickflow', '前复权')
    save_coverage(store, codes[2], did, start, day, 'tickflow')
    calls = []
    def raw(job, spec):
        calls.append(spec)
        from workbench.readiness import audit_scope
        if len(calls) == 1:
            assert spec['increment_codes'] == list(codes[:2]) and not spec.get('repair_codes')
        else:
            assert spec['repair_codes'] == [codes[2]] and spec['repair_scope'] == 'scan'
        return dict(success=0, errors=[], datasets=[], scan_readiness=audit_scope(
            store, ['沪深主板'], day, source='tickflow', timeframe='daily', verify_files=True))
    result = continue_sync(proxy(store, raw), 'test', dict(source='tickflow', boards=['沪深主板'], start=start, end=day))
    assert len(calls) == 2 and not result['scan_readiness']['complete']
    # Marking a job successful must never excuse a changed price file.
    path = store.rows('SELECT path FROM datasets WHERE id=?', (did,))[0]['path']
    (store.root/path).write_bytes(b'broken fixture')
    calls.clear()
    continue_sync(proxy(store, raw), 'test', dict(source='tickflow', boards=['沪深主板'], start=start, end=day))
    assert len(calls) == 1  # Invalid files do not become automatic price repairs.


def test_tail_update_in_cooldown_audits_locally_and_never_connects(store, frame):
    start, day, codes = tail_fixture(store, frame)
    ProviderGuard(store, 'tickflow').failure(ProviderError('tickflow', 'batch', '限制访问', '429'))
    result = continue_sync(proxy(store, lambda *_: pytest.fail('冷却期间联网')), 'test',
        dict(source='tickflow', boards=['沪深主板'], start=start, end=day))
    assert result['connections'] == 0 and result['resume']['waiting_for_cooldown']
    assert result['scan_readiness']['ready'] == 1


def test_tail_network_failure_is_not_followed_by_automatic_repair(store, frame):
    start, day, _ = tail_fixture(store, frame)
    calls = []
    def raw(*_):
        calls.append(1)
        from workbench.readiness import audit_scope
        return dict(success=0, remaining=2, cooldown={'until':9999999999},
                    scan_readiness=audit_scope(store, ['沪深主板'], day, source='tickflow', timeframe='daily'))
    result = continue_sync(proxy(store, raw), 'test',
        dict(source='tickflow', boards=['沪深主板'], start=start, end=day))
    assert calls == [1] and not result['resume']['repair_codes']


def test_missing_snapshot_is_ordinary_but_unchanged_tail_and_known_halt_are_not(store, frame):
    from workbench.repair_outcomes import before_repair, remember_repairs
    start, day, codes = tail_fixture(store, frame)
    before = before_repair(store, 'tickflow', codes[:1])
    remember_repairs(store, 'tickflow', day, before, [before[codes[0]]])
    assert ordinary_update_codes(store, 'tickflow', ['沪深主板'], day) == [codes[1]]
    store.execute('DELETE FROM datasets WHERE code=?', (codes[1],))
    assert ordinary_update_codes(store, 'tickflow', ['沪深主板'], day) == [codes[1]]


def test_read_only_queries_do_not_wait_for_another_sources_file_lock(store):
    first, second = PremarketStore(store.root), PremarketStore(store.root)
    first._files_lock = second._files_lock = store._files_lock
    store.execute("INSERT INTO meta VALUES('committed','old')")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.atomic_write():
            first.execute("UPDATE meta SET value='new' WHERE key='committed'")
            assert first.rows("SELECT value FROM meta WHERE key='committed'")[0]['value'] == 'new'
            assert pool.submit(second.rows, "SELECT value FROM meta WHERE key='committed'").result(timeout=2)[0]['value'] == 'old'
        assert second.rows("SELECT value FROM meta WHERE key='committed'")[0]['value'] == 'new'
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        second.rows("DELETE FROM meta WHERE key='committed' RETURNING key")
    assert second.metrics()['read_queries'] == 3


def test_file_read_audit_is_batched_bounded_and_keeps_original_details(store):
    worker = PremarketStore(store.root)
    path = store.write_artifact('fixture/input.txt', b'real fixture')
    before = worker.metrics()['write_connections']
    for _ in range(205):
        assert worker.read_artifact('fixture/input.txt', 'job') == b'real fixture'
    assert len(worker._read_events) == 5
    assert worker.metrics()['write_connections'] - before == 2
    worker.flush_reads()
    events = store.rows("SELECT path,detail FROM events WHERE job_id='job' AND action='读取文件'")
    assert len(events) == 205 and all(e['path'] == str(path) for e in events)
    assert all(json.loads(e['detail']) == {'bytes':12} for e in events)
    assert worker.metrics()['write_connections'] - before == 3


def test_writer_metrics_count_physical_connection_not_nested_transaction_calls(store):
    worker = PremarketStore(store.root)
    before = worker.metrics()['write_connections']
    with worker.atomic_write():
        worker.execute("INSERT INTO meta VALUES('unit:first','1')")
        worker.execute("INSERT INTO meta VALUES('unit:second','2')")
        assert len(worker.rows("SELECT key FROM meta WHERE key LIKE 'unit:%'")) == 2
    assert worker.metrics()['write_connections'] - before == 1


def test_cancelled_stage_flushes_pending_reads_and_preserves_progress(store, monkeypatch):
    store.write_artifact('fixture/input', b'input')
    def sync(self, job, spec):
        self.store.read_artifact('fixture/input', job)
        self.progress(job, 23, 100, 'saved')
        raise InterruptedError('stopped')
    monkeypatch.setattr(Service, '_sync', sync)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('premarket', dict(sources=['tickflow'],boards=['沪深主板'],
            strategies=['STRATEGY_3K'],timeframe='daily',start='2024-01-01')))
        report = json.loads(row['result'])
        child = report['sources']['tickflow']['sync_job']
        assert report['sources']['tickflow']['sync_status'] == 'cancelled'
        assert store.rows('SELECT progress FROM jobs WHERE id=?', (child,))[0]['progress'] == 23
        assert len(store.rows("SELECT seq FROM events WHERE job_id=? AND action='读取文件'", (child,))) == 1
    finally:
        service.pool.shutdown()


def test_parent_resume_progress_size_is_independent_of_stock_list_length():
    small = resume_summary(dict(reused=200, repair_codes=['sh.600000'], unchanged=[]))
    large = resume_summary(dict(reused=200, repair_codes=['sh.'+str(600000+i) for i in range(3199)], unchanged=['x']*3199))
    assert len(dumps(large)) < 150 and large['repair_count'] == large['unchanged_count'] == 3199
    assert set(large) == set(small) and not any(isinstance(v, list) for v in large.values())


@pytest.mark.parametrize('kind,spec', [
    ('sync', {'source':'tencent'}), ('sync', {'source':'tencent', 'resume':True}),
    ('sync', {'source':'tencent', 'repair_codes':['sh.600000']}),
    ('premarket', dict(sources=['baostock','tencent'],boards=['沪深主板'], strategies=['STRATEGY_3K'],timeframe='daily')),
])
def test_tencent_historical_download_rejected_before_jobs_or_connections(store, frame, monkeypatch, kind, spec):
    did = save_dataset(store, 'sh.600000', frame, 'tencent', '不复权')
    before = store.rows('SELECT * FROM datasets')
    monkeypatch.setattr('workbench.tencent_service.sync_tencent', lambda *_: pytest.fail('退休源仍联网'))
    service = Service(store)
    try:
        with pytest.raises(ValueError, match='腾讯历史行情已停用'):
            service.submit(kind, spec)
        with pytest.raises(ValueError, match='腾讯历史行情已停用'):
            service._sync_once('not-created', {'source':'tencent'})
        assert not store.rows('SELECT id FROM jobs')
        assert store.rows('SELECT * FROM datasets') == before
        set_market_source(store, 'tencent')
        bars, _ = load_dataset(store, did)
        assert len(bars) == len(frame)
        assert UPDATE_SOURCES == ('baostock', 'tickflow')
    finally:
        service.pool.shutdown()


def test_saved_three_source_choices_drop_only_retired_download_without_rewriting_preference(store):
    store.save_premarket_sources(['baostock','tickflow','tencent'])
    assert update_selection(store.premarket_sources(), 'tencent') == ['baostock','tickflow']
    assert store.premarket_sources() == ['baostock','tickflow','tencent']
    assert update_selection(['tencent'], 'tencent') == ['baostock']
