from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.test_workbench import frame, store, wait_for
from workbench.gap_family_acceleration import (
    CONFIGS, GapFamilyFastRejector, _store_contract_sha, assess_latest_bar,
    current_binding_id,
)
from workbench.market import save_dataset
from workbench.service import Service
from workbench.strategies import catalog, prepare, prepare_indicators, signal_at_end


IDS = tuple(CONFIGS)


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
    previous = result.iloc[-2]
    close = float(previous['close']) - 0.2
    result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
        close + 0.1, max(close + 0.2, previous['high'] - 0.1),
        min(close - 0.2, previous['low'] - 0.1), close,
    ]
    return result


def passing_frame(strategy_id, source):
    result = source.copy()
    previous = result.iloc[-2]
    if strategy_id == 'STRATEGY_GAP_PINBAR':
        ema = float(prepare_indicators(result.iloc[:-1])['ema20'].iloc[-1])
        result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
            ema + 0.05, ema + 0.8, ema - 0.8, ema + 0.4,
        ]
    elif strategy_id == 'STRATEGY_AWIL':
        ema = float(prepare_indicators(result.iloc[:-1])['ema20'].iloc[-1])
        high = max(float(previous['high']) + 0.8, ema + 1.2)
        result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
            high - 0.3, high, max(ema + 0.05, high - 0.8), high - 0.005,
        ]
    else:
        high = float(previous['high']) + 0.8
        low = float(previous['low']) + 0.1
        close = max(float(previous['close']) + 0.3, low + 0.4)
        result.loc[result.index[-1], ['open', 'high', 'low', 'close']] = [
            close - 0.2, max(high, close + 0.1), low, close,
        ]
    return result


def controller(store, monkeypatch, strategy_id, mode, rate=None):
    config = CONFIGS[strategy_id]
    monkeypatch.setenv(config.mode_env, mode)
    if rate is not None:
        monkeypatch.setenv(config.rate_env, str(rate))
    if mode in {'CANARY', 'ACCELERATED'}:
        monkeypatch.setenv(config.approval_env, current_binding_id(strategy_id))
    return GapFamilyFastRejector(store, 'gap-family-test', catalog(store)[strategy_id])


@pytest.mark.parametrize('strategy_id', IDS)
def test_each_gate_rejects_impossible_latest_bar_and_passes_necessary_shape(
        frame, strategy_id):
    rejected = reject_frame(frame)
    assessment = assess_latest_bar(strategy_id, rejected, prepare_indicators(rejected))
    assert assessment.safe and assessment.reject
    passing = passing_frame(strategy_id, frame)
    assessment = assess_latest_bar(strategy_id, passing, prepare_indicators(passing))
    assert assessment.safe and not assessment.reject


@pytest.mark.parametrize('strategy_id', IDS)
@pytest.mark.parametrize('mutation', [
    'date', 'ohlcv', 'text_atr', 'infinite_ema', 'stale_close_loc', 'text_bullish',
])
def test_untrusted_common_input_always_falls_back(frame, strategy_id, mutation):
    raw = reject_frame(frame)
    prepared = prepare_indicators(raw)
    if mutation == 'date':
        prepared.loc[prepared.index[-1], 'date'] = None
    elif mutation == 'ohlcv':
        prepared.loc[prepared.index[-1], 'close'] += 1
    elif mutation == 'text_atr':
        prepared.loc[prepared.index[-1], 'atr'] = 'broken'
    elif mutation == 'infinite_ema':
        prepared.loc[prepared.index[-1], 'ema20'] = np.inf
    elif mutation == 'stale_close_loc':
        prepared.loc[prepared.index[-1], 'close_loc'] = 0.123456
    else:
        prepared['is_bullish'] = prepared['is_bullish'].astype(object)
        prepared.loc[prepared.index[-1], 'is_bullish'] = 'True'
    assessment = assess_latest_bar(strategy_id, raw, prepared)
    assert not assessment.safe and not assessment.reject


@pytest.mark.parametrize('strategy_id', IDS)
def test_corrupt_indicator_preserves_original_exception_without_fuse(
        store, frame, monkeypatch, strategy_id):
    fast = controller(store, monkeypatch, strategy_id, 'ACCELERATED', rate=0)
    raw = reject_frame(frame)
    prepared = prepare_indicators(raw)
    prepared.loc[prepared.index[-1], 'atr'] = 'broken'
    with pytest.raises(TypeError, match='冻结策略指标异常'):
        fast.run(lambda: (_ for _ in ()).throw(TypeError('冻结策略指标异常')),
                 raw, prepared, 'corrupt')
    assert not fast.fused and fast.mode == 'ACCELERATED'


@pytest.mark.parametrize('strategy_id', IDS)
def test_shadow_preserves_payload_and_difference_fuses_only_that_strategy(
        store, frame, monkeypatch, strategy_id):
    fast = controller(store, monkeypatch, strategy_id, 'SHADOW')
    payload = {'asof': '2024-08-09', 'setup_date': '2024-08-09',
               'entry': 10.2, 'stop': 9.1, 'target': 12.4,
               'score': 80, 'rating': {'score': 80}, 'warning': ''}
    raw = reject_frame(frame)
    result = fast.run(lambda: payload, raw, prepare_indicators(raw), strategy_id)
    assert result.signal is payload and fast.fused and fast.mode == 'OFF'
    for other_id in IDS:
        if other_id == strategy_id:
            continue
        other = controller(store, monkeypatch, other_id, 'SHADOW')
        assert other.mode == 'SHADOW' and not other.fused


@pytest.mark.parametrize('strategy_id', IDS)
@pytest.mark.parametrize('mode', ['CANARY', 'ACCELERATED'])
@pytest.mark.parametrize('rate', [0, 0.37, 1])
def test_canary_and_accelerated_have_opposite_deterministic_split(
        store, frame, monkeypatch, strategy_id, mode, rate):
    fast = controller(store, monkeypatch, strategy_id, mode, rate)
    raw, prepared = reject_frame(frame), None
    prepared = prepare_indicators(raw)
    keys = [f'{strategy_id}-{index}' for index in range(80)]
    sampled = sum(fast._sampled(key) for key in keys)
    calls = 0

    def original():
        nonlocal calls
        calls += 1
        return None

    for key in keys:
        fast.run(original, raw, prepared, key)
    if rate == 0:
        assert sampled == 0
    elif rate == 1:
        assert sampled == len(keys)
    else:
        assert 0 < sampled < len(keys)
    assert calls == (len(keys) - sampled if mode == 'CANARY' else sampled)


@pytest.mark.parametrize('strategy_id', IDS)
@pytest.mark.parametrize('mode', ['SHADOW', 'CANARY', 'ACCELERATED'])
def test_windows_ci_runtime_exercises_requested_mode(
        store, monkeypatch, strategy_id, mode):
    config = CONFIGS[strategy_id]
    monkeypatch.setattr('workbench.gap_family_acceleration._runtime',
                        lambda: ('Windows', '3.12', '2.5.3', '2.3.3'))
    monkeypatch.setenv(config.mode_env, mode)
    if mode in {'CANARY', 'ACCELERATED'}:
        monkeypatch.setenv(config.approval_env, current_binding_id(strategy_id))
        monkeypatch.setenv(config.rate_env, '0.5')
    assert GapFamilyFastRejector(store, 'windows-ci', catalog(store)[strategy_id]).mode == mode


def test_each_strategy_has_independent_binding_cache_and_fuse_identity(
        store, monkeypatch):
    controllers = []
    for strategy_id in IDS:
        controllers.append(controller(store, monkeypatch, strategy_id, 'SHADOW'))
    assert len({item.binding_id for item in controllers}) == 3
    assert len({item.cache_engine('base') for item in controllers}) == 3
    assert len({item._fuse_key for item in controllers}) == 3


@pytest.mark.parametrize('strategy_id', IDS)
def test_version_runtime_approval_and_dynamic_rules_safely_fall_back(
        store, monkeypatch, strategy_id):
    spec = catalog(store)[strategy_id]
    config = CONFIGS[strategy_id]
    monkeypatch.setenv(config.mode_env, 'SHADOW')
    assert GapFamilyFastRejector(store, 'changed', replace(spec, version='changed')).mode == 'OFF'
    monkeypatch.setattr('workbench.gap_family_acceleration._runtime',
                        lambda: ('Other', '0', '0', '0'))
    assert GapFamilyFastRejector(store, 'runtime', spec).mode == 'OFF'


def test_structural_dynamic_rules_are_not_silently_accepted(store, monkeypatch):
    target = (Path(__file__).resolve().parents[1] / 'config/gap_optimized_rules.json').resolve()
    original_exists = Path.exists
    original_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'exists', lambda path: True if path.resolve() == target
                        else original_exists(path))
    monkeypatch.setattr(Path, 'read_bytes', lambda path: b'{}' if path.resolve() == target
                        else original_read_bytes(path))
    config = CONFIGS['STRATEGY_STRUCTURAL_GAP']
    monkeypatch.setenv(config.mode_env, 'SHADOW')
    fast = GapFamilyFastRejector(store, 'rules', catalog(store)[config.id])
    assert fast.mode == 'OFF'


def test_support_changes_invalidate_all_old_bindings(store, monkeypatch):
    old = {strategy_id: current_binding_id(strategy_id) for strategy_id in IDS}
    monkeypatch.setattr('workbench.gap_family_acceleration._store_contract_sha',
                        lambda _source=None: 'changed-store-contract')
    for strategy_id in IDS:
        config = CONFIGS[strategy_id]
        assert current_binding_id(strategy_id) != old[strategy_id]
        monkeypatch.setenv(config.mode_env, 'ACCELERATED')
        monkeypatch.setenv(config.approval_env, old[strategy_id])
        assert GapFamilyFastRejector(
            store, 'support-change', catalog(store)[strategy_id]).mode == 'OFF'


def test_store_contract_ignores_unrelated_code_but_tracks_required_behavior():
    source = (Path(__file__).resolve().parents[1] / 'workbench/store.py').read_text(
        encoding='utf-8')
    baseline = _store_contract_sha(source)
    assert _store_contract_sha(source + '\n\ndef unrelated_helper():\n    return 1\n') == baseline
    changed = source.replace('sqlite3.connect(self.path, timeout=30)',
                             'sqlite3.connect(self.path, timeout=31)', 1)
    assert changed != source and _store_contract_sha(changed) != baseline


@pytest.mark.parametrize('strategy_id', IDS)
def test_generated_legal_prefixes_have_zero_business_difference(
        store, monkeypatch, strategy_id):
    spec = catalog(store)[strategy_id]
    fast = controller(store, monkeypatch, strategy_id, 'SHADOW')
    compared = rejected = 0
    for seed in range(3):
        bars = generated_bars(seed)
        for end in range(125, 181, 11):
            current = bars.iloc[:end].reset_index(drop=True)
            prepared = prepare_indicators(current)
            original = signal_at_end(spec, current, prepared=prepared)
            gate = assess_latest_bar(strategy_id, current, prepared)
            result = fast.run(lambda original=original: original, current, prepared,
                              f'{strategy_id}-{seed}-{end}')
            assert result.signal == original
            if gate.safe and gate.reject:
                rejected += 1
                assert original is None
            compared += 1
    assert compared == 18 and rejected > 0 and not fast.fused


@pytest.mark.parametrize('strategy_id', [
    'STRATEGY_GAP_PINBAR', 'STRATEGY_STRUCTURAL_GAP',
])
def test_weekly_path_generated_prefixes_have_zero_business_difference(
        store, monkeypatch, strategy_id):
    spec = catalog(store)[strategy_id]
    fast = controller(store, monkeypatch, strategy_id, 'SHADOW')
    compared = 0
    for seed in range(4):
        weekly = prepare(generated_bars(seed, 760), 'weekly', '2027-12-31')
        for end in (125, len(weekly)):
            current = weekly.iloc[:end].reset_index(drop=True)
            prepared = prepare_indicators(current)
            original = signal_at_end(spec, current, prepared=prepared)
            gate = assess_latest_bar(strategy_id, current, prepared)
            result = fast.run(lambda original=original: original, current, prepared,
                              f'weekly-{strategy_id}-{seed}-{end}')
            assert result.signal == original
            if gate.safe and gate.reject:
                assert original is None
            compared += 1
    assert compared == 8 and not fast.fused


def test_service_all_three_accelerated_isolated_and_business_equal(
        store, frame, monkeypatch):
    dataset = save_dataset(store, 'sh.600000', reject_frame(frame), 'csv', '前复权')
    spec = {'datasets': [dataset], 'strategies': list(IDS), 'timeframe': 'daily',
            'asof': frame.date.iloc[-1], 'force': True}
    service = Service(store)
    try:
        monkeypatch.setattr('workbench.service.signal_at_end',
                            lambda *_args, **_kwargs: None)
        for strategy_id, config in CONFIGS.items():
            monkeypatch.setenv(config.mode_env, 'ACCELERATED')
            monkeypatch.setenv(config.approval_env, current_binding_id(strategy_id))
            monkeypatch.setenv(config.rate_env, '0')
        row = wait_for(store, service.submit('scan', spec))
        assert row['status'] == 'completed'
        result = __import__('json').loads(row['result'])
        assert result['success'] == 3 and result['signals'] == 0 and not result['errors']
        actions = {item['action'] for item in store.rows(
            "SELECT action FROM events WHERE action LIKE '%快速拒绝' OR action LIKE '%灰度门禁判断'")}
        for config in CONFIGS.values():
            assert f'{config.label}快速拒绝' in actions
            assert f'{config.label}灰度门禁判断' in actions
    finally:
        service.pool.shutdown(wait=True)
