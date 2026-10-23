from contextlib import contextmanager
from dataclasses import replace
import json
import threading

import numpy as np
import pandas as pd
import pytest

from tests.test_workbench import frame, store, wait_for
from workbench.market import save_dataset
from workbench.mtr_acceleration import (
    APPROVAL_ENV,
    CANARY_RATE_ENV,
    MODE_ENV,
    MTRFastRejector,
    assess_latest_bar,
    current_binding_id,
)
from workbench.scan_cache import cache_key, calculation_versions
from workbench.service import Service
from workbench.store import digest, dumps
from workbench.strategies import catalog, prepare_indicators, scan_engine_version, signal_at_end


def generated_bars(seed=7, rows=180):
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.18, rows))
    opening = close + rng.normal(0, 0.12, rows)
    high = np.maximum(opening, close) + rng.uniform(0.04, 0.35, rows)
    low = np.minimum(opening, close) - rng.uniform(0.04, 0.35, rows)
    for index in range(126, rows, 11):
        opening[index] = close[index] - 0.15
        high[index] = close[index]
        low[index] = close[index] - 0.4
    return pd.DataFrame({
        'date': pd.bdate_range('2025-01-02', periods=rows).strftime('%Y-%m-%d'),
        'open': opening,
        'high': high,
        'low': low,
        'close': close,
        'volume': rng.integers(10_000, 1_000_000, rows).astype(float),
    })


def reject_frame(source):
    result = source.copy()
    result.loc[result.index[-1], 'open'] = result.close.iloc[-1] - 0.1
    result.loc[result.index[-1], 'high'] = result.close.iloc[-1] + 0.3
    result.loc[result.index[-1], 'low'] = result.close.iloc[-1] - 0.4
    return result


def passing_frame(source):
    result = source.copy()
    result.loc[result.index[-1], 'open'] = result.close.iloc[-1] - 0.1
    result.loc[result.index[-1], 'high'] = result.close.iloc[-1]
    result.loc[result.index[-1], 'low'] = result.close.iloc[-1] - 0.4
    return result


def controller(store, monkeypatch, mode, *, rate=None, spec=None):
    monkeypatch.setenv(MODE_ENV, mode)
    if rate is not None:
        monkeypatch.setenv(CANARY_RATE_ENV, str(rate))
    if mode in {'CANARY', 'ACCELERATED'}:
        monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
    return MTRFastRejector(store, 'mtr-test-job', spec or catalog(store)['MTR_MASTER'])


def test_gate_matches_frozen_latest_signal_bar_boundaries(frame):
    rejected = reject_frame(frame)
    assessment = assess_latest_bar(rejected, prepare_indicators(rejected))
    assert assessment.safe and assessment.reject

    exact = passing_frame(frame)
    assessment = assess_latest_bar(exact, prepare_indicators(exact))
    assert assessment.safe and not assessment.reject

    tolerance = exact.copy()
    tolerance.loc[tolerance.index[-1], 'high'] += 5e-10
    assert not assess_latest_bar(tolerance, prepare_indicators(tolerance)).reject

    outside = exact.copy()
    outside.loc[outside.index[-1], 'high'] += 5e-8
    assert assess_latest_bar(outside, prepare_indicators(outside)).reject


@pytest.mark.parametrize('mutation', ['bad_date', 'bad_price', 'changed_prepared', 'bad_atr'])
def test_untrusted_input_never_fast_rejects(frame, mutation):
    raw = reject_frame(frame)
    prepared = prepare_indicators(raw)
    if mutation == 'bad_date':
        prepared.loc[prepared.index[-1], 'date'] = None
    elif mutation == 'bad_price':
        raw.loc[raw.index[-1], 'high'] = raw.close.iloc[-1] - 1
    elif mutation == 'changed_prepared':
        prepared.loc[prepared.index[-1], 'close'] += 1
    else:
        prepared.loc[prepared.index[-1], 'atr'] = np.nan
    assessment = assess_latest_bar(raw, prepared)
    assert not assessment.safe and not assessment.reject


def test_shadow_preserves_complete_payload_and_records_match(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'SHADOW')
    payload = {
        'asof': '2024-08-09', 'setup_date': '2024-08-08',
        'entry': 10.2, 'stop': 9.1, 'target': 12.4,
        'score': 88, 'rating': {'score': 88}, 'warning': '',
    }
    raw = reject_frame(frame)
    result = fast.run(lambda: payload, raw, prepare_indicators(raw), 'one')
    assert result.signal is payload
    assert fast.fused and fast.mode == 'OFF'
    evidence = json.loads(store.rows(
        "SELECT value FROM meta WHERE key LIKE 'mtr_fast_reject:fuse:%'")[0]['value'])
    assert evidence['original'] == payload
    assert store.rows("SELECT * FROM events WHERE action='MTR快速拒绝熔断'")


def test_shadow_zero_difference_and_gate_pass_both_execute_original(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'SHADOW')
    calls = []
    rejected = reject_frame(frame)
    result = fast.run(lambda: calls.append('reject') or None,
                      rejected, prepare_indicators(rejected), 'reject')
    assert result.signal is None and result.event_detail['matched'] is True
    accepted = passing_frame(frame)
    result = fast.run(lambda: calls.append('pass') or {'complete': 'payload'},
                      accepted, prepare_indicators(accepted), 'pass')
    assert result.signal == {'complete': 'payload'}
    assert calls == ['reject', 'pass'] and not fast.fused


def test_accelerated_rejects_only_unsampled_safe_input(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'ACCELERATED', rate=0)
    assert fast.mode == 'ACCELERATED'
    raw = reject_frame(frame)
    result = fast.run(lambda: pytest.fail('安全拒绝不应执行完整 MTR'),
                      raw, prepare_indicators(raw), 'not-sampled')
    assert result.signal is None and result.event_action == 'MTR快速拒绝'

    calls = []
    accepted = passing_frame(frame)
    assert fast.run(lambda: calls.append(1) or None,
                    accepted, prepare_indicators(accepted), 'pass').signal is None
    assert calls == [1]


def test_canary_is_deterministic_and_business_exception_is_preserved(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'CANARY', rate=1)
    raw = reject_frame(frame)
    with pytest.raises(ValueError, match='冻结策略真实异常'):
        fast.run(lambda: (_ for _ in ()).throw(ValueError('冻结策略真实异常')),
                 raw, prepare_indicators(raw), 'always-sampled')
    assert fast.fused and fast.mode == 'OFF'
    evidence = json.loads(store.rows(
        "SELECT value FROM meta WHERE key LIKE 'mtr_fast_reject:fuse:%'")[0]['value'])
    assert evidence['exception'] == {'type': 'ValueError', 'message': '冻结策略真实异常'}


def test_version_runtime_and_approval_changes_fall_back_to_original(store, monkeypatch):
    spec = catalog(store)['MTR_MASTER']
    monkeypatch.setenv(MODE_ENV, 'SHADOW')
    assert MTRFastRejector(store, 'job', replace(spec, version='changed')).mode == 'OFF'

    monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
    monkeypatch.delenv(APPROVAL_ENV, raising=False)
    assert MTRFastRejector(store, 'job', spec).mode == 'OFF'

    monkeypatch.setattr('workbench.mtr_acceleration._runtime', lambda: ('Other', '0', '0', '0'))
    assert MTRFastRejector(store, 'job', spec).mode == 'OFF'


def test_current_frozen_mtr_generated_legal_prefixes_have_zero_difference(store, monkeypatch):
    spec = catalog(store)['MTR_MASTER']
    fast = controller(store, monkeypatch, 'SHADOW', spec=spec)
    compared = rejected = 0
    for seed in range(3):
        bars = generated_bars(seed)
        for end in range(125, 181, 11):
            current = bars.iloc[:end].reset_index(drop=True)
            prepared = prepare_indicators(current)
            original = signal_at_end(spec, current, prepared=prepared)
            assessment = assess_latest_bar(current, prepared)
            result = fast.run(lambda original=original: original,
                              current, prepared, f'{seed}-{end}')
            assert result.signal == original
            if assessment.safe and assessment.reject:
                rejected += 1
                assert original is None
            compared += 1
    assert compared == 18 and rejected >= 12 and not fast.fused


def test_off_cache_identity_is_unchanged_and_shadow_cache_is_isolated(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = dict(datasets=[dataset], strategies=['MTR_MASTER'], timeframe='daily',
                asof=frame.date.iloc[-1])
    calls = []
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda *_args, **_kwargs: calls.append(1) or None)
    service = Service(store)
    try:
        monkeypatch.setenv(MODE_ENV, 'OFF')
        off = wait_for(store, service.submit('scan', spec))
        assert off['status'] == 'completed' and calls == [1]
        entry = catalog(store)['MTR_MASTER']
        version = calculation_versions([entry])[entry.id]
        original_key = cache_key(dataset, entry.id, version, 'daily', frame.date.iloc[-1],
                                 scan_engine_version(include_frozen=False))
        assert store.rows('SELECT signal FROM scan_cache WHERE key=?', (original_key,)) == [
            {'signal': 'null'}]

        monkeypatch.setenv(MODE_ENV, 'SHADOW')
        shadow = wait_for(store, service.submit('scan', spec))
        report = json.loads(shadow['result'])
        assert shadow['status'] == 'completed' and report['calculated'] == 1
        assert calls == [1, 1]
        assert len(store.rows('SELECT * FROM scan_cache')) == 2

        calls.clear()
        monkeypatch.setenv(MODE_ENV, 'OFF')
        again = wait_for(store, service.submit('scan', spec))
        assert again['status'] == 'completed' and not calls
        assert json.loads(again['result'])['stage_reused']
    finally:
        service.pool.shutdown()


def test_accelerated_service_keeps_public_preparation_and_skips_only_mtr(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = dict(datasets=[dataset], strategies=['MTR_MASTER'], timeframe='daily',
                asof=frame.date.iloc[-1], force=True)
    calls = {'prepare': 0, 'mtr': 0}
    from workbench import service as service_module
    real_prepare = service_module.prepare_indicators

    def prepared(data):
        calls['prepare'] += 1
        return real_prepare(data)

    monkeypatch.setattr(service_module, 'prepare_indicators', prepared)
    monkeypatch.setattr(service_module, 'signal_at_end',
                        lambda *_args, **_kwargs: calls.__setitem__('mtr', calls['mtr'] + 1))
    monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
    monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
    monkeypatch.setenv(CANARY_RATE_ENV, '0')
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', spec))
        report = json.loads(row['result'])
        assert row['status'] == 'completed' and report['success'] == report['no_signal'] == 1
        assert calls == {'prepare': 1, 'mtr': 0}
        assert store.rows("SELECT * FROM events WHERE action='MTR快速拒绝'")
        assert store.rows("SELECT * FROM events WHERE action='MTR灰度门禁判断'")
        assert not store.rows("SELECT * FROM events WHERE action='调用策略'")
    finally:
        service.pool.shutdown()


def test_signal_on_earlier_bar_is_not_a_latest_formal_candidate(store, frame, monkeypatch):
    from core.strategies.mtr_strategy import MTRStrategy

    class Structural:
        def find_swing_points(self, _data):
            return []

        def match_mtr_pattern(self, data, _swings, index):
            if index != len(data) - 1:
                return None
            previous = len(data) - 2
            return {
                'stage': 'SETUP_READY', 'score': 80.0, 'points': {},
                'signal_bar': {'idx': previous, 'high': float(data.high.iloc[previous]),
                               'quality': 90.0, 'close_position': 1.0},
            }

    original = MTRStrategy.__init__

    def initialize(self):
        original(self)
        self.structural_engine = Structural()

    monkeypatch.setattr(MTRStrategy, '__init__', initialize)
    current = reject_frame(frame)
    assert signal_at_end(catalog(store)['MTR_MASTER'], current) is None


def test_binding_id_and_sample_key_are_stable(store, monkeypatch):
    one = current_binding_id()
    two = current_binding_id()
    assert one == two and len(one) == 64
    fast = controller(store, monkeypatch, 'CANARY', rate=0.5)
    assert fast._sampled('same-stock') == fast._sampled('same-stock')
    assert fast.cache_engine('engine') == fast.cache_engine('engine')
    assert fast.cache_engine('engine') != 'engine'
    assert digest(dumps([one, 'CANARY', 0.5]).encode()) != one


@pytest.mark.parametrize('source', ['baostock', 'tickflow'])
def test_baostock_and_tickflow_shadow_then_accelerated_keep_formal_result(
        store, frame, monkeypatch, source):
    from tests.test_premarket_resume import seeded

    current = reject_frame(frame)
    _start, day, datasets = seeded(store, current, source)
    spec = dict(source=source, boards=['沪深主板'], datasets=datasets,
                strategies=['MTR_MASTER'], timeframe='daily', asof=day, force=True)
    service = Service(store)
    try:
        monkeypatch.setenv(MODE_ENV, 'SHADOW')
        shadow = wait_for(store, service.submit('scan', spec))
        shadow_report = json.loads(shadow['result'])
        assert shadow['status'] == 'completed'

        monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
        monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
        monkeypatch.setenv(CANARY_RATE_ENV, '0')
        accelerated = wait_for(store, service.submit('scan', spec))
        accelerated_report = json.loads(accelerated['result'])
        assert accelerated['status'] == 'completed'
        for key in ('success', 'signals', 'no_signal', 'errors', 'observation_ids'):
            assert accelerated_report[key] == shadow_report[key]
    finally:
        service.pool.shutdown()


class HardCrash(BaseException):
    pass


def test_accelerated_cache_survives_committed_crash_and_resumes_once(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    job = 'mtr-accelerated-crash'
    spec = dict(datasets=[dataset], strategies=['MTR_MASTER'], timeframe='daily',
                asof=frame.date.iloc[-1], force=True)
    monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
    monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
    monkeypatch.setenv(CANARY_RATE_ENV, '0')
    service = Service(store)
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  (job, 'scan', 'running', '2026-10-23T00:00:00+08:00', dumps(spec)))
    service.cancel_flags[job] = threading.Event()

    def crash(point):
        if point == 'after_stock_commit':
            raise HardCrash(point)

    monkeypatch.setattr('workbench.service._scan_crash_probe', crash)
    try:
        with pytest.raises(HardCrash, match='after_stock_commit'):
            service._scan(job, spec)
    finally:
        service.pool.shutdown()

    assert len(store.rows('SELECT key FROM scan_cache')) == 1
    monkeypatch.setattr('workbench.service._scan_crash_probe', lambda _point: None)
    restarted = Service(store)
    try:
        interrupted = store.rows('SELECT * FROM jobs WHERE id=?', (job,))[0]
        assert interrupted['status'] == 'interrupted'
        recovered = json.loads(interrupted['result'])
        assert recovered['success'] == recovered['no_signal'] == recovered['calculated'] == 1

        resume_spec = dict(spec)
        resume_spec.pop('force')
        resumed = wait_for(store, restarted.submit('scan', resume_spec))
        report = json.loads(resumed['result'])
        assert resumed['status'] == 'completed'
        assert report['success'] == report['reused'] == report['no_signal'] == 1
        assert report['calculated'] == 0
        assert len(store.rows('SELECT key FROM scan_cache')) == 1
    finally:
        restarted.pool.shutdown()


def test_fuse_evidence_falls_back_to_stock_transaction_when_direct_write_fails(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = dict(datasets=[dataset], strategies=['MTR_MASTER'], timeframe='daily',
                asof=frame.date.iloc[-1], force=True)
    payload = {
        'asof': frame.date.iloc[-1], 'setup_date': frame.date.iloc[-1],
        'entry': 20.0, 'stop': 19.0, 'target': 22.0, 'score': 80,
        'rating': None, 'close': 20.0, 'warning': '',
    }
    monkeypatch.setenv(MODE_ENV, 'SHADOW')
    monkeypatch.setattr('workbench.service.signal_at_end', lambda *_a, **_k: payload)
    original_trip = MTRFastRejector._trip_fuse

    def transient_failure(self, *args, **kwargs):
        real_connect = self.store.connect

        @contextmanager
        def unavailable():
            raise OSError('diagnostic database temporarily unavailable')
            yield

        self.store.connect = unavailable
        try:
            return original_trip(self, *args, **kwargs)
        finally:
            self.store.connect = real_connect

    monkeypatch.setattr(MTRFastRejector, '_trip_fuse', transient_failure)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', spec))
        report = json.loads(row['result'])
        assert row['status'] == 'completed' and report['signals'] == 1
        assert not report.get('checkpoints') and not store.rows('SELECT * FROM scan_cache')
        events = store.rows("SELECT detail FROM events WHERE action='MTR快速拒绝熔断'")
        assert len(events) == 1
        detail = json.loads(events[0]['detail'])
        assert detail['persistence_error'].startswith('OSError:')
        assert detail['original'] == payload
    finally:
        service.pool.shutdown()
