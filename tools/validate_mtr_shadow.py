"""Read-only real-market MTR SHADOW validation and isolated service benchmark.

The source Aplus store is opened with SQLite read-only/immutable flags. Selected
market files are SHA-256 checked, then copied into temporary stores. Nothing in
the source runtime is changed, and no network provider is contacted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc

import pandas as pd


sys.path.insert(0, str(Path.cwd()))

from workbench import service as service_module  # noqa: E402
from workbench.history_quality import quality_for, save_quality  # noqa: E402
from workbench.market import validate_bars  # noqa: E402
from workbench.mtr_acceleration import (  # noqa: E402
    APPROVAL_ENV,
    CANARY_RATE_ENV,
    EXPECTED_ADAPTER_SHA,
    EXPECTED_CALCULATION,
    EXPECTED_RELEASE,
    MODE_ENV,
    MTRFastRejector,
    SUPPORTED_RUNTIMES,
    _runtime,
    assess_latest_bar,
    current_binding_id,
)
from workbench.scan_cache import calculation_versions  # noqa: E402
from workbench.service import Service  # noqa: E402
from workbench.store import ROOT, Store, digest, dumps  # noqa: E402
from workbench.strategies import (  # noqa: E402
    catalog,
    prepare_indicators,
    signal_at_end,
    verify_frozen,
)


STRATEGIES = [
    'MTR_MASTER',
    'STRATEGY_3K',
    'STRATEGY_STRUCTURAL_GAP',
    'STRATEGY_GAP_PINBAR',
    'STRATEGY_GAP_H2',
    'STRATEGY_AWIL',
]


def readonly_records(source_home, source):
    source_home = Path(source_home).resolve()
    database = source_home / 'workbench.sqlite3'
    if not database.is_file():
        raise ValueError(f'源数据仓缺少 workbench.sqlite3：{source_home}')
    uri = database.as_uri() + '?mode=ro&immutable=1'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        schema = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        records = [dict(row) for row in db.execute(
            '''SELECT d.* FROM datasets d JOIN (
                   SELECT code,MAX(rowid) AS rid FROM datasets
                   WHERE source=? AND timeframe='daily' GROUP BY code
               ) latest ON d.rowid=latest.rid
               WHERE d.rows>=125 ORDER BY d.code''', (source,))]
        quality_rows = db.execute(
            "SELECT COUNT(*) FROM meta WHERE key LIKE 'market_quality:%'").fetchone()[0]
    if not records:
        raise ValueError(f'源数据仓没有可验证的 {source} 日线')
    return source_home, schema[0] if schema else None, records, quality_rows


def spaced(items, count):
    count = min(max(1, count), len(items))
    if count == 1:
        return [items[-1]]
    indexes = sorted({round(index * (len(items) - 1) / (count - 1))
                      for index in range(count)})
    return [items[index] for index in indexes]


def load_snapshots(source_home, records, count):
    snapshots = []
    for record in spaced(records, count):
        path = (source_home / record['path']).resolve()
        if not path.is_relative_to(source_home):
            raise ValueError(f"行情路径越界：{record['path']}")
        content = path.read_bytes()
        if digest(content) != record['sha256']:
            raise ValueError(f"{record['code']} 行情 SHA-256 不一致")
        frame = validate_bars(pd.read_csv(io.BytesIO(content)))
        if len(frame) != int(record['rows']):
            raise ValueError(f"{record['code']} 行数与登记不一致")
        snapshots.append({'record': record, 'content': content, 'frame': frame})
    return snapshots


def historical_dates(source_home, snapshots, count):
    calendar_path = source_home / 'trading_calendar.json'
    if not calendar_path.is_file():
        raise ValueError('源数据仓缺少交易日历')
    calendar = json.loads(calendar_path.read_text(encoding='utf-8'))
    final_day = min(snapshot['record']['end'] for snapshot in snapshots)
    days = [day for day in calendar['trading_days'] if day <= final_day]
    recent = days[-241:] if len(days) > 241 else days
    return spaced(recent, count), calendar


@contextmanager
def acceleration_mode(name, rate='0'):
    names = [MODE_ENV, APPROVAL_ENV, CANARY_RATE_ENV]
    before = {key: os.environ.get(key) for key in names}
    try:
        os.environ[MODE_ENV] = name
        if name in {'CANARY', 'ACCELERATED'}:
            os.environ[APPROVAL_ENV] = current_binding_id()
            os.environ[CANARY_RATE_ENV] = str(rate)
        else:
            os.environ.pop(APPROVAL_ENV, None)
            os.environ.pop(CANARY_RATE_ENV, None)
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def captured(call):
    try:
        return {'kind': 'result', 'payload': call()}
    except Exception as exc:  # Business exceptions are part of the comparison.
        return {'kind': 'exception', 'type': type(exc).__name__, 'message': str(exc)}


def canonical(value):
    return dumps(value)


def opportunity_id(dataset_id, spec, signal):
    if signal is None:
        return None
    identity = digest(
        f"{dataset_id}|{spec.id}|{spec.version}|daily|{signal['asof']}".encode())
    if signal.get('plan_version'):
        identity = digest((identity + signal['plan_version']).encode())
    return identity


def shadow_validation(snapshots, dates):
    with tempfile.TemporaryDirectory(prefix='aplus-mtr-shadow-real-') as directory:
        store = Store(directory)
        spec = catalog(store)['MTR_MASTER']
        with acceleration_mode('SHADOW'):
            fast = MTRFastRejector(store, 'real-shadow-validation', spec)
        if fast.mode != 'SHADOW':
            events = store.rows(
                "SELECT detail FROM events WHERE action='MTR快速拒绝回退原版'")
            raise RuntimeError(f'SHADOW 未生效：{events}')

        counts = Counter()
        audit_bytes = 0
        differences = []
        examples = []
        baseline_seconds = 0.0
        shadow_seconds = 0.0
        for snapshot in snapshots:
            frame = snapshot['frame']
            record = snapshot['record']
            for day in dates:
                prefix = frame.loc[frame.date <= day].reset_index(drop=True)
                if len(prefix) < 125:
                    counts['insufficient_prefix'] += 1
                    continue
                prefix.attrs['code'] = record['code']
                prepared = prepare_indicators(prefix)
                gate = assess_latest_bar(prefix, prepared)
                counts['safe_reject' if gate.safe and gate.reject else
                       'safe_pass' if gate.safe else 'unsafe_fallback'] += 1

                started = time.perf_counter()
                baseline = captured(lambda: signal_at_end(spec, prefix, prepared=prepared))
                baseline_seconds += time.perf_counter() - started
                started = time.perf_counter()
                result_box = {}

                def run_shadow():
                    result = fast.run(
                        lambda: signal_at_end(spec, prefix, prepared=prepared),
                        prefix, prepared, f"{record['id']}|daily|{day}")
                    result_box['result'] = result
                    return result.signal

                shadow = captured(run_shadow)
                shadow_seconds += time.perf_counter() - started
                counts['comparisons'] += 1
                if baseline['kind'] == 'result' and baseline['payload'] is not None:
                    counts['signals'] += 1
                    if len(examples) < 12:
                        examples.append({
                            'code': record['code'], 'date': day,
                            'setup_date': baseline['payload'].get('setup_date'),
                            'entry': baseline['payload'].get('entry'),
                            'stop': baseline['payload'].get('stop'),
                            'target': baseline['payload'].get('target'),
                            'score': baseline['payload'].get('score'),
                        })
                if baseline['kind'] == 'exception':
                    counts['business_exceptions'] += 1
                if 'result' in result_box and result_box['result'].event_action:
                    counts[result_box['result'].event_action] += 1
                    audit_bytes += len(canonical(result_box['result'].event_detail).encode())

                baseline_identity = (opportunity_id(record['id'], spec, baseline['payload'])
                                     if baseline['kind'] == 'result' else None)
                shadow_identity = (opportunity_id(record['id'], spec, shadow['payload'])
                                   if shadow['kind'] == 'result' else None)
                if canonical(baseline) != canonical(shadow) or baseline_identity != shadow_identity:
                    differences.append({
                        'code': record['code'], 'date': day,
                        'gate': gate.__dict__, 'baseline': baseline, 'shadow': shadow,
                        'baseline_identity': baseline_identity,
                        'shadow_identity': shadow_identity,
                    })
                    if len(differences) >= 20:
                        break
                if fast.fused:
                    break
            if differences or fast.fused:
                break

        event_counts = {row['action']: row['n'] for row in store.rows(
            'SELECT action,COUNT(*) n FROM events GROUP BY action ORDER BY action')}
        return {
            'requested_mode': 'SHADOW', 'actual_mode_at_start': 'SHADOW',
            'actual_mode_at_end': fast.mode, 'binding': fast.binding_id,
            'stocks': len(snapshots), 'dates': dates, **dict(counts),
            'payload_and_identity_differences': len(differences),
            'difference_examples': differences,
            'signal_examples': examples,
            'baseline_seconds': round(baseline_seconds, 6),
            'shadow_seconds': round(shadow_seconds, 6),
            'shadow_overhead_seconds': round(shadow_seconds - baseline_seconds, 6),
            'shadow_overhead_pct': round(
                (shadow_seconds / baseline_seconds - 1) * 100, 2) if baseline_seconds else None,
            'returned_audit_detail_bytes': audit_bytes,
            'persistent_controller_events': event_counts,
            'fused': fast.fused,
        }


def copy_snapshots(store, snapshots):
    rows = []
    for snapshot in snapshots:
        record = snapshot['record']
        target = (store.root / record['path']).resolve()
        if not target.is_relative_to(store.root):
            raise ValueError('隔离行情路径越界')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(snapshot['content'])
        rows.append(tuple(record[key] for key in (
            'id', 'code', 'timeframe', 'source', 'adjustment', 'start', 'end',
            'rows', 'path', 'sha256', 'created')))
    with store.connect() as db:
        db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
    return [row[0] for row in rows]


class Profiler:
    def __init__(self):
        self.lock = threading.Lock()
        self.seconds = defaultdict(float)
        self.counts = Counter()

    def add(self, key, elapsed, count_key=None):
        with self.lock:
            self.seconds[key] += elapsed
            self.counts[count_key or key] += 1

    def snapshot(self):
        with self.lock:
            return {
                'seconds': {key: round(value, 6) for key, value in self.seconds.items()},
                'counts': dict(self.counts),
            }


@contextmanager
def service_profile(store, profiler):
    original_load = service_module.load_dataset
    original_prepare = service_module.prepare
    original_indicators = service_module.prepare_indicators
    original_signal = service_module.signal_at_end
    original_connect = store.connect

    def timed(key, call):
        started = time.perf_counter()
        try:
            return call()
        finally:
            profiler.add(key, time.perf_counter() - started)

    service_module.load_dataset = lambda *a, **k: timed(
        'file_read_sha_parse', lambda: original_load(*a, **k))
    service_module.prepare = lambda *a, **k: timed(
        'window_prepare', lambda: original_prepare(*a, **k))
    service_module.prepare_indicators = lambda *a, **k: timed(
        'public_indicators', lambda: original_indicators(*a, **k))

    def signal(spec, *args, **kwargs):
        return timed('strategy:' + spec.id, lambda: original_signal(spec, *args, **kwargs))

    service_module.signal_at_end = signal

    @contextmanager
    def connect():
        outer = getattr(store._atomic_local, 'connection', None) is None
        started = time.perf_counter()
        try:
            with original_connect() as db:
                yield db
        finally:
            profiler.add('database_context', time.perf_counter() - started,
                         'durable_transactions' if outer else 'nested_database_contexts')

    store.connect = connect
    try:
        yield
    finally:
        store.connect = original_connect
        service_module.load_dataset = original_load
        service_module.prepare = original_prepare
        service_module.prepare_indicators = original_indicators
        service_module.signal_at_end = original_signal


def wait_for(store, job):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        row = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        if row['status'] not in {'queued', 'running'}:
            return row
        time.sleep(0.02)
    raise TimeoutError('隔离扫描超时')


def business_snapshot(store, report):
    observations = store.rows(
        'SELECT code,strategy,version,timeframe,asof,setup_date,dataset_id,payload '
        'FROM observations ORDER BY code,strategy,asof')
    return {
        'success': report['success'], 'signals': report['signals'],
        'no_signal': report['no_signal'], 'errors': report['errors'],
        'observation_ids': report['observation_ids'], 'observations': observations,
    }


def service_once(mode, snapshots):
    with tempfile.TemporaryDirectory(prefix=f'aplus-mtr-{mode.lower()}-') as directory:
        store = Store(directory)
        dataset_ids = copy_snapshots(store, snapshots)
        asof = min(snapshot['record']['end'] for snapshot in snapshots)
        spec = dict(datasets=dataset_ids, strategies=STRATEGIES, timeframe='daily',
                    asof=asof, force=True)
        service = Service(store)
        cold_profile = Profiler()
        cached_profile = Profiler()
        try:
            with acceleration_mode(mode), service_profile(store, cold_profile):
                tracemalloc.start()
                started = time.perf_counter()
                cold_row = wait_for(store, service.submit('scan', spec))
                cold_seconds = time.perf_counter() - started
                _current, cold_peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            cold_report = json.loads(cold_row['result'])
            if cold_row['status'] != 'completed':
                raise RuntimeError((mode, cold_row['status'], cold_report))

            cached_spec = dict(spec)
            cached_spec.pop('force')
            with acceleration_mode(mode), service_profile(store, cached_profile):
                tracemalloc.start()
                started = time.perf_counter()
                cached_row = wait_for(store, service.submit('scan', cached_spec))
                cached_seconds = time.perf_counter() - started
                _current, cached_peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            cached_report = json.loads(cached_row['result'])
            events = store.rows(
                'SELECT action,COUNT(*) n,SUM(LENGTH(detail)) bytes FROM events '
                'GROUP BY action ORDER BY action')
            business = business_snapshot(store, cold_report)
            return {
                'business': business,
                'business_digest': digest(canonical(business).encode()),
                'cold_seconds': round(cold_seconds, 6),
                'cold_peak_python_bytes': cold_peak,
                'cold_profile': cold_profile.snapshot(),
                'cached_seconds': round(cached_seconds, 6),
                'cached_peak_python_bytes': cached_peak,
                'cached_profile': cached_profile.snapshot(),
                'cached_stage_reused': bool(cached_report.get('stage_reused')),
                'cache_rows': store.rows('SELECT COUNT(*) n FROM scan_cache')[0]['n'],
                'observation_rows': len(business['observations']),
                'event_rows': sum(row['n'] for row in events),
                'event_detail_bytes': sum(row['bytes'] or 0 for row in events),
                'event_counts': {row['action']: row['n'] for row in events},
                'database_bytes': store.path.stat().st_size,
                'terminal_receipt_bytes': len((cold_row['result'] or '').encode()),
            }
        finally:
            service.pool.shutdown(wait=True)


def median_summary(rows):
    numeric = ['cold_seconds', 'cold_peak_python_bytes', 'cached_seconds',
               'cached_peak_python_bytes', 'cache_rows', 'observation_rows',
               'event_rows', 'event_detail_bytes', 'database_bytes',
               'terminal_receipt_bytes']
    return {key: round(statistics.median(row[key] for row in rows), 6)
            for key in numeric}


def service_benchmark(snapshots, rounds):
    samples = {mode: [] for mode in ('OFF', 'SHADOW', 'ACCELERATED')}
    business = {}
    orders = [
        ('OFF', 'SHADOW', 'ACCELERATED'),
        ('ACCELERATED', 'OFF', 'SHADOW'),
        ('SHADOW', 'ACCELERATED', 'OFF'),
    ]
    for index in range(rounds):
        for mode in orders[index % len(orders)]:
            row = service_once(mode, snapshots)
            business.setdefault(mode, row.pop('business'))
            samples[mode].append(row)
    digests = {mode: rows[0]['business_digest'] for mode, rows in samples.items()}
    summary = {mode: median_summary(rows) for mode, rows in samples.items()}
    off = summary['OFF']['cold_seconds']
    for mode in ('SHADOW', 'ACCELERATED'):
        summary[mode]['cold_change_vs_off_pct'] = round(
            (summary[mode]['cold_seconds'] / off - 1) * 100, 2)
    return {
        'stocks': len(snapshots), 'rounds': rounds,
        'interleaved_order': True, 'business_digests': digests,
        'business_equal': len(set(digests.values())) == 1,
        'signals': {mode: business[mode]['signals'] for mode in business},
        'errors': {mode: business[mode]['errors'] for mode in business},
        'summary': summary,
        'round_details': samples,
    }


def quality_benchmark(source_home, snapshots, calendar):
    with tempfile.TemporaryDirectory(prefix='aplus-quality-real-') as directory:
        store = Store(directory)
        copy_snapshots(store, snapshots)
        (store.root / 'trading_calendar.json').write_bytes(
            (source_home / 'trading_calendar.json').read_bytes())
        records = store.rows('SELECT * FROM datasets ORDER BY code')
        day = min(record['end'] for record in records)

        started = time.perf_counter()
        calculated = [quality_for(store, record, day, calendar) for record in records]
        missing_seconds = time.perf_counter() - started
        started = time.perf_counter()
        for record, quality in zip(records, calculated):
            save_quality(store, record['id'], quality)
        persist_seconds = time.perf_counter() - started

        if hasattr(store, '_history_quality_cache'):
            del store._history_quality_cache
        started = time.perf_counter()
        valid = [quality_for(store, record, day, calendar) for record in records]
        valid_seconds = time.perf_counter() - started

        for record, quality in zip(records, valid):
            stale = dict(quality, evidence_version='stale')
            save_quality(store, record['id'], stale)
        if hasattr(store, '_history_quality_cache'):
            del store._history_quality_cache
        started = time.perf_counter()
        invalid = [quality_for(store, record, day, calendar) for record in records]
        invalid_seconds = time.perf_counter() - started
        return {
            'stocks': len(records), 'rows_median': statistics.median(r['rows'] for r in records),
            'missing_cache_seconds': round(missing_seconds, 6),
            'persist_seconds': round(persist_seconds, 6),
            'valid_cache_seconds': round(valid_seconds, 6),
            'invalid_cache_seconds': round(invalid_seconds, 6),
            'valid_equals_missing': canonical(valid) == canonical(calculated),
            'invalid_recalculation_equals_missing': canonical(invalid) == canonical(calculated),
            'missing_dates_total': sum(len(q['missing_dates']) for q in calculated),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-home', required=True)
    parser.add_argument('--source', default='baostock')
    parser.add_argument('--shadow-stocks', type=int, default=300)
    parser.add_argument('--shadow-dates', type=int, default=5)
    parser.add_argument('--performance-stocks', type=int, default=60)
    parser.add_argument('--quality-stocks', type=int, default=60)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    source_home, schema, records, source_quality_rows = readonly_records(
        args.source_home, args.source)
    needed = max(args.shadow_stocks, args.performance_stocks, args.quality_stocks)
    all_snapshots = load_snapshots(source_home, records, needed)
    shadow_snapshots = spaced(all_snapshots, args.shadow_stocks)
    performance_snapshots = spaced(all_snapshots, args.performance_stocks)
    quality_snapshots = spaced(all_snapshots, args.quality_stocks)
    dates, calendar = historical_dates(source_home, shadow_snapshots, args.shadow_dates)

    with tempfile.TemporaryDirectory(prefix='aplus-binding-check-') as directory:
        binding_store = Store(directory)
        mtr = catalog(binding_store)['MTR_MASTER']
        calculation = calculation_versions([mtr])[mtr.id]
    environment = {
        'platform_python_numpy_pandas': _runtime(),
        'runtime_supported': _runtime() in SUPPORTED_RUNTIMES,
        'binding': current_binding_id(),
        'main_sha': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'frozen_manifest': verify_frozen(),
        'mtr_release_matches': mtr.version == EXPECTED_RELEASE,
        'calculation_matches': calculation == EXPECTED_CALCULATION,
        'adapter_matches': digest((ROOT / 'workbench/strategies.py').read_bytes()) == EXPECTED_ADAPTER_SHA,
    }
    output = {
        'kind': 'mtr-pre-activation-validation-v1',
        'source': {
            'kind': args.source, 'schema': schema, 'read_only': True,
            'available_eligible_snapshots': len(records),
            'source_quality_cache_rows': source_quality_rows,
            'rows_min': min(record['rows'] for record in records),
            'rows_median': statistics.median(record['rows'] for record in records),
            'rows_max': max(record['rows'] for record in records),
            'network_used': False, 'formal_runtime_modified': False,
        },
        'environment': environment,
        'shadow': shadow_validation(shadow_snapshots, dates),
        'service_baseline': service_benchmark(performance_snapshots, args.rounds),
        'quality_cache': quality_benchmark(source_home, quality_snapshots, calendar),
        'coverage_limits': {
            'tickflow_real_snapshots': False,
            'about_2800_row_real_snapshots': False,
            'one_click_premarket_network_stage': False,
        },
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + '\n', encoding='utf-8')
    if not args.quiet:
        print(encoded)
    if (not environment['runtime_supported'] or not environment['mtr_release_matches']
            or not environment['calculation_matches'] or not environment['adapter_matches']
            or output['shadow']['payload_and_identity_differences']
            or output['shadow']['fused'] or not output['service_baseline']['business_equal']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
