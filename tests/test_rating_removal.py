"""Retired rating fields cannot change prices, displays or snapshot consumers."""
import json
from pathlib import Path

import pytest

from core.strategy_registry import StrategyRegistry
from workbench import snapshot
from workbench.strategies import StrategySpec, signal_at_end
from tests.test_h2_plan import h2_bars
from tests.test_mtr_fast_reject import generated_bars
from tests.test_premarket_snapshot import assert_no_retired_fields


def test_old_backtest_settings_ignore_retired_filter_without_exporting_it():
    from dataclasses import asdict
    from workbench.backtest import Assumptions
    costs = Assumptions.from_saved({'min_score': 88, 'min_rr': 2, 'wait_bars': 7})
    costs.validate()
    assert costs.min_rr == 2 and costs.wait_bars == 7
    assert 'min_score' not in asdict(costs)
    with pytest.raises(TypeError):
        Assumptions.from_saved({'unknown_setting': 1})


def test_fixed_signals_match_pre_removal_prices(h2_bars):
    samples = {'h2-fixture': h2_bars,
               'mtr-1-126': generated_bars(1).iloc[:127].copy(),
               'mtr-2-159': generated_bars(2).iloc[:160].copy()}
    expected = json.loads((Path(__file__).parent / 'fixtures/rating_removal_signals.json').read_text(encoding="utf-8"))
    actual = []
    for code, frame in samples.items():
        for key, item in sorted(StrategyRegistry._strategies.items()):
            # Low-level detector contract; production catalog still verifies frozen bytes.
            spec = StrategySpec(key, key, item['class'], {}, 'fixture', ['daily'], 'research', '')
            signal = signal_at_end(spec, frame)
            if signal:
                assert_no_retired_fields(signal)
                actual.append(dict(code=code, strategy=key,
                                   **{k: signal[k] for k in ('entry', 'stop', 'target')}))
    assert actual == expected


def test_snapshot_export_hit_drops_legacy_rating(tmp_path):
    row = dict(id='o1', code='sh.600000', strategy='fixture', version='v1',
               timeframe='daily', setup_date='2026-11-10',
               payload=json.dumps(dict(entry=10, stop=9, target=12,
                                       rating={'score': 88}, score=88, mm_target=13)))
    hit = snapshot._hit(row, {})
    path = tmp_path / 'snapshot.json'
    path.write_text(json.dumps({'hits': [hit]}))
    assert_no_retired_fields(json.loads(path.read_text(encoding="utf-8")))
    assert 'rating' not in path.read_text(encoding="utf-8")
    assert '"score"' not in path.read_text(encoding="utf-8")
    assert hit['entry'] == 10 and hit['plan']['mm_target'] == 13


def test_historical_snapshot_verifies_original_then_ignores_rating(tmp_path, monkeypatch):
    # Synthetic anchors isolate the loader contract, not production frozen validation.
    monkeypatch.setattr(snapshot, 'scan_engine_version', lambda: 'fixture-engine')
    monkeypatch.setattr(snapshot, 'verify_frozen', lambda: 'fixture-frozen')
    data = dict(kind=snapshot.SNAPSHOT_KIND, schema_version=1, asof='2026-11-10',
                generated_at='2026-11-10T21:00:00+08:00', source='fixture',
                coverage={}, engine_version='fixture-engine', frozen_digest='fixture-frozen',
                observation_ids=['o1'], timeframe='daily', boards=[], strategies=['fixture'],
                charts={}, chart_errors={'o1': 'fixture: no image'},
                hits=[dict(observation_id='o1', code='sh.600000', strategy='fixture', version='v1',
                           setup_date='2026-11-10', asof='2026-11-10', timeframe='daily',
                           entry=10, stop=9, target=12, rating={'score': 99}, score=99,
                           plan={'entry': 10, 'setup_evaluation': {'rating': {'score': 88}, 'score': 88}})])
    data['sha256'] = snapshot.snapshot_digest(data)
    path = tmp_path / 'historical.json'
    path.write_text(json.dumps(data))
    loaded = snapshot.load_snapshot(path, today='2026-11-11')
    assert_no_retired_fields(loaded)
    assert loaded['hits'][0]['entry'] == 10
    assert loaded['hits'][0]['plan']['setup_evaluation'] == {}
    assert json.loads(path.read_text(encoding="utf-8")) == data
    data['hits'][0]['score'] = 77  # Even ignored data must pass the ORIGINAL SHA.
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='SHA'):
        snapshot.load_snapshot(path, today='2026-11-11')


def test_write_snapshot_fixture_has_no_retired_fields(tmp_path, monkeypatch):
    from workbench.store import Store
    from workbench import strategies
    from tests.test_premarket_snapshot import _seed, ASOF
    # Export integration with synthetic version anchors and isolated fixture records.
    # This does not assert that the unchanged production frozen manifest accepts edits.
    monkeypatch.setattr(strategies, 'verify_frozen', lambda: 'fixture-frozen')
    monkeypatch.setattr(snapshot, 'verify_frozen', lambda: 'fixture-frozen')
    store = Store(tmp_path / 'fixture-store')
    _seed(store)
    payload = json.loads(store.rows("SELECT payload FROM observations WHERE id='obs-1'")[0]['payload'])
    payload.update(rating={'score': 82}, score=82)
    store.execute("UPDATE observations SET payload=? WHERE id='obs-1'", (json.dumps(payload),))
    monkeypatch.setattr(snapshot, '_package_charts', lambda _store, data, _out:
                        ({}, {hit['observation_id']: 'fixture: no image' for hit in data['hits']}))
    out = snapshot.write_snapshot(store, ASOF, tmp_path / 'export')
    text = (out / 'snapshot.json').read_text(encoding="utf-8")
    assert 'rating' not in text and '"score"' not in text
    assert_no_retired_fields(json.loads(text))
    assert_no_retired_fields(snapshot.load_snapshot(out / 'snapshot.json'))
