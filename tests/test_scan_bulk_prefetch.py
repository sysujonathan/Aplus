import json
import sqlite3

from tests.test_workbench import frame, store, wait_for
from workbench.market import save_dataset
from workbench.scan_cache import cache_key, calculation_versions
from workbench.service import Service
from workbench.store import dumps, now
from workbench.strategies import catalog, scan_engine_version


def scan_spec(dataset_ids, strategies=None):
    return dict(datasets=dataset_ids, strategies=strategies or ['STRATEGY_3K'],
                timeframe='daily', asof='2024-12-31')


def test_scan_prefetches_metadata_and_raw_cache_once_without_reordering(store, frame, monkeypatch):
    codes = ['sh.600000', 'sz.000001', 'sz.300750']
    dataset_ids = [save_dataset(store, code, frame, 'csv', '前复权') for code in codes]
    queries = {'datasets': 0, 'cache': 0}
    original_rows = store.rows

    def counted(sql, args=()):
        if 'FROM datasets WHERE id IN' in sql:
            queries['datasets'] += 1
        if 'FROM scan_cache WHERE key IN' in sql:
            queries['cache'] += 1
        return original_rows(sql, args)

    calls = []
    monkeypatch.setattr(store, 'rows', counted)
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda strategy, data, **kwargs: calls.append((data.attrs['code'], strategy.id)))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', scan_spec(
            dataset_ids, ['STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP'])))
    finally:
        service.pool.shutdown()

    report = json.loads(row['result'])
    assert row['status'] == 'completed'
    assert report['success'] == 6 and report['calculated'] == 6 and report['reused'] == 0
    assert calls == [(code, strategy) for code in codes
                     for strategy in ['STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP']]
    assert queries == {'datasets': 1, 'cache': 1}


def test_duplicate_dataset_reuses_judgment_committed_earlier_in_same_scan(store, frame, monkeypatch):
    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    calls = []
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda *args, **kwargs: calls.append(1))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', scan_spec([dataset_id, dataset_id])))
    finally:
        service.pool.shutdown()

    report = json.loads(row['result'])
    assert row['status'] == 'completed'
    assert report['datasets'] == [dataset_id, dataset_id]
    assert report['success'] == 2 and report['calculated'] == 1 and report['reused'] == 1
    assert calls == [1]


def test_missing_metadata_and_broken_cached_json_keep_stock_strategy_error_order(store, frame, monkeypatch):
    first = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    second = save_dataset(store, 'sz.000001', frame, 'csv', '前复权')
    strategies = [catalog(store)[key] for key in ['STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP']]
    versions = calculation_versions(strategies)
    engine = scan_engine_version(include_frozen=False)
    broken = cache_key(first, strategies[0].id, versions[strategies[0].id],
                       'daily', '2024-12-31', engine)
    store.execute('INSERT INTO scan_cache VALUES(?,?,?)', (broken, '{broken json', now()))
    calls = []
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda strategy, data, **kwargs: calls.append((data.attrs['code'], strategy.id)))
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', scan_spec(
            ['missing-dataset', first, second], [strategy.id for strategy in strategies])))
    finally:
        service.pool.shutdown()

    report = json.loads(row['result'])
    assert row['status'] == 'partial'
    assert [(item['code'], item['strategy']) for item in report['errors']] == [
        ('missing-dataset', 'STRATEGY_3K'),
        ('missing-dataset', 'STRATEGY_STRUCTURAL_GAP'),
        ('sh.600000', 'STRATEGY_3K'),
    ]
    assert '行情快照不存在' in report['errors'][0]['error']
    assert 'Expecting property name' in report['errors'][2]['error']
    assert calls == [
        ('sh.600000', 'STRATEGY_STRUCTURAL_GAP'),
        ('sz.000001', 'STRATEGY_3K'),
        ('sz.000001', 'STRATEGY_STRUCTURAL_GAP'),
    ]


def test_cache_prefetch_database_lock_remains_a_failed_job(store, frame, monkeypatch):
    dataset_id = save_dataset(store, 'sh.600000', frame, 'csv', '前复权')
    original_rows = store.rows

    def locked(sql, args=()):
        if 'FROM scan_cache WHERE key IN' in sql:
            raise sqlite3.OperationalError('database is locked')
        return original_rows(sql, args)

    monkeypatch.setattr(store, 'rows', locked)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', scan_spec([dataset_id])))
    finally:
        service.pool.shutdown()

    assert row['status'] == 'failed'
    assert row['message'] == 'database is locked'
    assert json.loads(row['result'])['errors'][0]['error'] == 'database is locked'


def test_metadata_batch_lock_is_reported_on_current_stock_then_legacy_reads_resume(store, frame, monkeypatch):
    dataset_ids = [save_dataset(store, code, frame, 'csv', '前复权')
                   for code in ['sh.600000', 'sz.000001']]
    original_rows = store.rows
    failed = False

    def locked_once(sql, args=()):
        nonlocal failed
        if not failed and 'FROM datasets WHERE id IN' in sql:
            failed = True
            raise sqlite3.OperationalError('database is locked')
        return original_rows(sql, args)

    monkeypatch.setattr(store, 'rows', locked_once)
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *args, **kwargs: None)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', scan_spec(dataset_ids)))
    finally:
        service.pool.shutdown()

    report = json.loads(row['result'])
    assert row['status'] == 'partial'
    assert [(item['code'], item['error']) for item in report['errors']] == [
        (dataset_ids[0], 'database is locked')]
    assert report['success'] == 1 and report['calculated'] == 1
