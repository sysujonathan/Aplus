from dataclasses import replace
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from workbench.backtest import Assumptions
from workbench.h2_prefilter import candidate_days
from workbench.h2_replay import H2Settings, MODEL, run_replay
from workbench.h2_replay_cache import cache_key, engine_version, read_cache, write_cache
from workbench.h2_replay_service import load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store, dumps
from tests.test_h2_plan import h2_bars, h2_spec, append_bar
from tests.test_workbench import wait_for


def long_bars():
    count = 460
    # Several positive BO/H1/H2 structures on both sides of 300-bar truncation.
    bars = pd.DataFrame(dict(date=pd.bdate_range('2024-01-01', periods=count).strftime('%Y-%m-%d'),
                             open=9., high=10., low=6., close=9., volume=1000.))
    for bo in (126, 240, 295, 315, 425):
        for j, (high, low) in enumerate([(12, 10.8), (11.8, 10.6), (11.9, 10.7), (11.6, 10.5),
                                       (11.5, 10.4), (11.8, 10.7), (14.2, 11)]):
            bars.loc[bo + j, ['open', 'high', 'low', 'close']] = [low + .1, high, low, high - .1]
    bars.attrs['code'] = 'sh.600000'
    return bars


@pytest.mark.parametrize('settings', [H2Settings(), H2Settings(lookback=5, max_pullback=120),
                                     H2Settings(lookback=120, max_pullback=120),
                                     H2Settings(lookback=120, min_pullback=10, max_pullback=40)])
def test_prefilter_reference_complete_events_equal_across_300_bars(h2_spec, settings):
    frame = long_bars()
    start, end = frame.date.iloc[124], frame.date.iloc[-1]
    reference = run_replay(h2_spec, frame, start, end, settings, prefilter=False)
    optimized = run_replay(h2_spec, frame, start, end, settings)
    assert dumps(reference) == dumps(optimized)
    if settings == H2Settings():
        assert len(reference) >= 2


def test_raw_filter_never_changes_past_flags_on_future_append():
    bars = long_bars()
    first, first_bo = candidate_days(bars.iloc[:330], H2Settings())
    whole, whole_bo = candidate_days(bars, H2Settings())
    np.testing.assert_array_equal(first, whole[:330])
    np.testing.assert_array_equal(first_bo, whole_bo[:330])


def test_random_prefix_detector_reference_equality(h2_spec):
    rng = np.random.default_rng(73)
    close = 20 * np.exp(np.cumsum(rng.normal(.002, .025, 350)))
    opening = close * (1 + rng.normal(0, .004, len(close)))
    bars = pd.DataFrame(dict(date=pd.bdate_range('2024-01-01', periods=350).strftime('%Y-%m-%d'),
                            open=opening, high=np.maximum(opening, close) * 1.01,
                            low=np.minimum(opening, close) * .99, close=close, volume=10000.))
    bars.attrs['code'] = 'sh.600000'
    for settings in [H2Settings(), H2Settings(lookback=5, max_pullback=120)]:
        start, end = bars.date.iloc[250], bars.date.iloc[-1]
        assert dumps(run_replay(h2_spec, bars, start, end, settings)) == dumps(
            run_replay(h2_spec, bars, start, end, settings, prefilter=False))


def test_cache_key_all_inputs_and_bad_cache_not_used(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path / 'cache')
    did = save_dataset(store, 'sh.600000', h2_bars, 'csv', '测试')
    snapshot = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
    settings, costs = H2Settings(), Assumptions()
    version = engine_version()
    def key(snap=snapshot, start='2025-01-01', end='2025-12-31', cfg=settings, price=costs, engine=version):
        return cache_key(snap, h2_spec, start, end, cfg, price, engine)
    original = key()
    assert all(value != original for value in [
        key(start='2025-01-02'), key(end='2025-12-30'),
        key(cfg=replace(settings, wait_bars=20)), key(price=replace(costs, slippage_bps=0)),
        key(engine='different'), key(snap=dict(snapshot, sha256='different'))])
    assert read_cache(store, original) is None
    write_cache(store, original, [])
    assert read_cache(store, original) == []  # Zero-opportunity results are reusable too.
    path = store.root / f'research-cache/h2/{original}.json'
    damaged = json.loads(path.read_text(encoding='utf-8'))
    damaged['records'] = [{}]
    path.write_text(dumps(damaged), encoding='utf-8')
    assert read_cache(store, original) is None


def test_repeat_job_reuses_complete_records_but_source_tamper_fails(tmp_path, h2_bars):
    store = Store(tmp_path / 'reuse')
    bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 14.1, 11)
    did = save_dataset(store, 'sh.600000', bars, 'csv', '测试')
    service = Service(store)
    spec = dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                datasets=[did], start=bars.date.iloc[124], end=bars.date.iloc[-1])
    try:
        job = service.submit('backtest', spec)
        assert wait_for(store, job)['status'] == 'completed'
        _, first, original = load_receipt(store, job)
        assert first['performance']['calculated'] == 1 and first['performance']['reused'] == 0
        with patch('workbench.h2_replay_service.run_replay', side_effect=AssertionError('must reuse')):
            job = service.submit('backtest', spec)
            assert wait_for(store, job)['status'] == 'completed'
        _, second, reused = load_receipt(store, job)
        assert second['performance']['reused'] == 1 and dumps(original) == dumps(reused)
        dataset = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
        (store.root / dataset['path']).write_bytes(b'corrupt')
        job = service.submit('backtest', spec)
        assert wait_for(store, job)['status'] == 'partial'
        _, failed, rows = load_receipt(store, job)
        assert failed['errors'] and failed['performance']['reused'] == 0 and not rows
        for table in ('observations', 'plans', 'positions', 'position_fills', 'closed_trades'):
            assert not store.rows(f'SELECT * FROM {table}')
    finally:
        service.pool.shutdown()


def test_stopped_job_reuses_only_finished_symbols_on_retry(tmp_path, h2_bars):
    store = Store(tmp_path / 'retry')
    ids = [save_dataset(store, code, h2_bars, 'csv', '合成工程测试')
           for code in ('sh.600000', 'sh.600012')]
    service = Service(store)
    spec = dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                datasets=ids, start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1])
    calls = []
    def replay(strategy, frame, start, end, settings, costs, progress, cancelled):
        calls.append(frame.attrs['code'])
        if len(calls) == 2:
            next(iter(service.cancel_flags.values())).set()
            raise InterruptedError('停止')
        return run_replay(strategy, frame, start, end, settings, costs)
    try:
        with patch('workbench.h2_replay_service.run_replay', side_effect=replay):
            first_job = service.submit('backtest', spec)
            assert wait_for(store, first_job)['status'] == 'cancelled'
            second_job = service.submit('backtest', spec)
            assert wait_for(store, second_job)['status'] == 'completed'
        _, stopped, partial = load_receipt(store, first_job)
        _, finished, records = load_receipt(store, second_job)
        assert not stopped['complete'] and stopped['processed_datasets'] == 1
        assert len(partial) == 1 and len(records) == 2
        assert calls == ['sh.600000', 'sh.600012', 'sh.600012']
        assert finished['performance']['reused'] == 1 and finished['performance']['calculated'] == 1
    finally:
        service.pool.shutdown()
