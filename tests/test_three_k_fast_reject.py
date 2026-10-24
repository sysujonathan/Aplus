from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest

from tests.test_workbench import frame, store, wait_for
from workbench.market import save_dataset
from workbench.scan_cache import cache_key, calculation_versions
from workbench.service import Service
from workbench.strategies import catalog, prepare_indicators, scan_engine_version, signal_at_end
from workbench.three_k_acceleration import (
    APPROVAL_ENV,
    CANARY_RATE_ENV,
    MODE_ENV,
    ThreeKFastRejector,
    assess_latest_bar,
    current_binding_id,
)


def generated_bars(seed=7, rows=180):
    rng = np.random.default_rng(seed)
    close = 20 + np.cumsum(rng.normal(0, 0.18, rows))
    opening = close + rng.normal(0, 0.12, rows)
    high = np.maximum(opening, close) + rng.uniform(0.04, 0.35, rows)
    low = np.minimum(opening, close) - rng.uniform(0.04, 0.35, rows)
    return pd.DataFrame({
        'date': pd.bdate_range('2025-01-02', periods=rows).strftime('%Y-%m-%d'),
        'open': opening, 'high': high, 'low': low, 'close': close,
        'volume': rng.integers(10_000, 1_000_000, rows).astype(float),
    })


def reject_frame(source):
    result = source.copy()
    previous_high = float(result.high.iloc[-2])
    result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
        previous_high - 0.2, previous_high + 0.1,
        previous_high - 0.3, previous_high,
    ]
    return result


def passing_frame(source):
    result = source.copy()
    close = float(result.high.iloc[-2]) + 1.0
    result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
        close - 0.5, close + 0.2, close - 0.7, close,
    ]
    return result


def controller(store, monkeypatch, mode, *, rate=None, spec=None):
    monkeypatch.setenv(MODE_ENV, mode)
    if rate is not None:
        monkeypatch.setenv(CANARY_RATE_ENV, str(rate))
    if mode in {'CANARY', 'ACCELERATED'}:
        monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
    return ThreeKFastRejector(
        store, 'three-k-test-job', spec or catalog(store)['STRATEGY_3K'])


def test_gate_matches_frozen_latest_confirmation_necessity(frame):
    rejected = reject_frame(frame)
    assessment = assess_latest_bar(rejected, prepare_indicators(rejected))
    assert assessment.safe and assessment.reject

    passing = passing_frame(frame)
    assessment = assess_latest_bar(passing, prepare_indicators(passing))
    assert assessment.safe and not assessment.reject

    bearish = passing.copy()
    bearish.loc[bearish.index[-1], 'open'] = bearish.close.iloc[-1] + 0.1
    bearish.loc[bearish.index[-1], 'high'] = bearish.open.iloc[-1] + 0.1
    assert assess_latest_bar(bearish, prepare_indicators(bearish)).reject


@pytest.mark.parametrize('mutation', ['bad_date', 'bad_price', 'changed_prepared', 'bad_bullish'])
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
        prepared.loc[prepared.index[-1], 'is_bullish'] = False
    assessment = assess_latest_bar(raw, prepared)
    assert not assessment.safe and not assessment.reject


def test_shadow_preserves_payload_and_trips_fuse_on_difference(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'SHADOW')
    payload = {'asof': '2024-08-09', 'setup_date': '2024-08-09',
               'entry': 10.2, 'stop': 9.1, 'target': 12.4,
               'score': None, 'rating': {'score': 88}, 'warning': ''}
    raw = reject_frame(frame)
    result = fast.run(lambda: payload, raw, prepare_indicators(raw), 'one')
    assert result.signal is payload and fast.fused and fast.mode == 'OFF'
    evidence = json.loads(store.rows(
        "SELECT value FROM meta WHERE key LIKE 'three_k_fast_reject:fuse:%'")[0]['value'])
    assert evidence['original'] == payload


def test_shadow_reject_and_gate_pass_both_preserve_original(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'SHADOW')
    calls = []
    rejected = reject_frame(frame)
    result = fast.run(lambda: calls.append('reject') or None,
                      rejected, prepare_indicators(rejected), 'reject')
    assert result.signal is None and result.event_detail['matched'] is True
    accepted = passing_frame(frame)
    payload = {'complete': 'payload'}
    result = fast.run(lambda: calls.append('pass') or payload,
                      accepted, prepare_indicators(accepted), 'pass')
    assert result.signal is payload and calls == ['reject', 'pass'] and not fast.fused


def test_accelerated_skips_only_unsampled_safe_rejection(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'ACCELERATED', rate=0)
    rejected = reject_frame(frame)
    result = fast.run(lambda: pytest.fail('安全拒绝不应执行完整3K'),
                      rejected, prepare_indicators(rejected), 'not-sampled')
    assert result.signal is None and result.event_action == '3K快速拒绝'
    calls = []
    accepted = passing_frame(frame)
    fast.run(lambda: calls.append(1) or None,
             accepted, prepare_indicators(accepted), 'pass')
    assert calls == [1]


def test_sampled_business_exception_is_preserved_and_fused(store, frame, monkeypatch):
    fast = controller(store, monkeypatch, 'CANARY', rate=1)
    raw = reject_frame(frame)
    with pytest.raises(ValueError, match='冻结3K真实异常'):
        fast.run(lambda: (_ for _ in ()).throw(ValueError('冻结3K真实异常')),
                 raw, prepare_indicators(raw), 'always-sampled')
    assert fast.fused and fast.mode == 'OFF'


def test_version_runtime_and_approval_changes_fall_back(store, monkeypatch):
    spec = catalog(store)['STRATEGY_3K']
    monkeypatch.setenv(MODE_ENV, 'SHADOW')
    assert ThreeKFastRejector(store, 'job', replace(spec, version='changed')).mode == 'OFF'

    monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
    monkeypatch.delenv(APPROVAL_ENV, raising=False)
    assert ThreeKFastRejector(store, 'job', spec).mode == 'OFF'

    monkeypatch.setattr('workbench.three_k_acceleration._runtime',
                        lambda: ('Other', '0', '0', '0'))
    assert ThreeKFastRejector(store, 'job', spec).mode == 'OFF'


@pytest.mark.parametrize('mode', ['SHADOW', 'CANARY', 'ACCELERATED'])
def test_windows_ci_runtime_exercises_requested_mode(store, monkeypatch, mode):
    spec = catalog(store)['STRATEGY_3K']
    monkeypatch.setattr('workbench.three_k_acceleration._runtime',
                        lambda: ('Windows', '3.12', '2.5.3', '2.3.3'))
    monkeypatch.setenv(MODE_ENV, mode)
    if mode in {'CANARY', 'ACCELERATED'}:
        monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
        monkeypatch.setenv(CANARY_RATE_ENV, '0.5')
    assert ThreeKFastRejector(store, 'windows-ci', spec).mode == mode


def test_current_frozen_three_k_generated_prefixes_have_zero_difference(store, monkeypatch):
    spec = catalog(store)['STRATEGY_3K']
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
    assert compared == 18 and rejected >= 10 and not fast.fused


def test_off_cache_identity_unchanged_and_shadow_isolated(store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = dict(datasets=[dataset], strategies=['STRATEGY_3K'], timeframe='daily',
                asof=frame.date.iloc[-1])
    calls = []
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda *_args, **_kwargs: calls.append(1) or None)
    service = Service(store)
    try:
        monkeypatch.setenv(MODE_ENV, 'OFF')
        off = wait_for(store, service.submit('scan', spec))
        assert off['status'] == 'completed' and calls == [1]
        entry = catalog(store)['STRATEGY_3K']
        version = calculation_versions([entry])[entry.id]
        original_key = cache_key(dataset, entry.id, version, 'daily', frame.date.iloc[-1],
                                 scan_engine_version(include_frozen=False))
        assert store.rows('SELECT signal FROM scan_cache WHERE key=?', (original_key,)) == [
            {'signal': 'null'}]

        monkeypatch.setenv(MODE_ENV, 'SHADOW')
        shadow = wait_for(store, service.submit('scan', spec))
        assert shadow['status'] == 'completed' and calls == [1, 1]
        assert len(store.rows('SELECT key FROM scan_cache')) == 2

        calls.clear()
        monkeypatch.setenv(MODE_ENV, 'OFF')
        again = wait_for(store, service.submit('scan', spec))
        assert again['status'] == 'completed' and not calls
        assert json.loads(again['result'])['stage_reused']
    finally:
        service.pool.shutdown()


def test_accelerated_service_keeps_public_preparation_and_skips_only_three_k(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = dict(datasets=[dataset], strategies=['STRATEGY_3K'], timeframe='daily',
                asof=frame.date.iloc[-1], force=True)
    calls = {'prepare': 0, 'three_k': 0}
    from workbench import service as service_module
    real_prepare = service_module.prepare_indicators

    def prepared(data):
        calls['prepare'] += 1
        return real_prepare(data)

    monkeypatch.setattr(service_module, 'prepare_indicators', prepared)
    monkeypatch.setattr(service_module, 'signal_at_end',
                        lambda *_a, **_k: calls.__setitem__('three_k', calls['three_k'] + 1))
    monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
    monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
    monkeypatch.setenv(CANARY_RATE_ENV, '0')
    service = Service(store)
    try:
        row = wait_for(store, service.submit('scan', spec))
        report = json.loads(row['result'])
        assert row['status'] == 'completed' and report['success'] == report['no_signal'] == 1
        assert calls == {'prepare': 1, 'three_k': 0}
        assert store.rows("SELECT * FROM events WHERE action='3K快速拒绝'")
        assert store.rows("SELECT * FROM events WHERE action='3K灰度门禁判断'")
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('source', ['baostock', 'tickflow'])
def test_two_sources_shadow_then_accelerated_keep_formal_result(
        store, frame, monkeypatch, source):
    from tests.test_premarket_resume import seeded

    current = reject_frame(frame)
    _start, day, datasets = seeded(store, current, source)
    spec = dict(source=source, boards=['沪深主板'], datasets=datasets,
                strategies=['STRATEGY_3K'], timeframe='daily', asof=day, force=True)
    service = Service(store)
    try:
        monkeypatch.setenv(MODE_ENV, 'SHADOW')
        shadow = wait_for(store, service.submit('scan', spec))
        shadow_report = json.loads(shadow['result'])
        monkeypatch.setenv(MODE_ENV, 'ACCELERATED')
        monkeypatch.setenv(APPROVAL_ENV, current_binding_id())
        monkeypatch.setenv(CANARY_RATE_ENV, '0')
        accelerated = wait_for(store, service.submit('scan', spec))
        accelerated_report = json.loads(accelerated['result'])
        assert shadow['status'] == accelerated['status'] == 'completed'
        for key in ('success', 'signals', 'no_signal', 'errors', 'observation_ids'):
            assert accelerated_report[key] == shadow_report[key]
    finally:
        service.pool.shutdown()
