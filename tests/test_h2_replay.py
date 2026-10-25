import json
from dataclasses import replace
from unittest.mock import patch

import pandas as pd
import pytest

from workbench.backtest import Assumptions
from workbench.h2_plan import _structure, project_plan
from workbench.h2_replay import (H2Settings, LEGACY_MODEL, MODEL, ResearchSpec,
                                 boundary_samples, replay_setup, run_replay,
                                 summarize_replay)
from workbench.h2_replay_service import (ITERATION_BASELINE_PURPOSE, available_snapshots,
                                         load_receipt)
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store, dumps
from workbench.strategies import calculate, catalog, verify_frozen
from gui.afterhours import list_prices
from gui.h2_replay_chart import replay_view, render_replay
from tests.test_h2_plan import h2_bars, h2_spec, append_bar
from tests.test_workbench import wait_for


ZERO = Assumptions(commission_bps=0, sell_tax_bps=0, slippage_bps=0)


def study(spec, frame, settings=None, costs=None):
    return run_replay(spec, frame, frame.date.iloc[124], frame.date.iloc[-1], settings, costs or ZERO)


def test_default_projection_matches_formal_and_research_settings_do_not_mutate(h2_spec, h2_bars):
    before = verify_frozen()
    _, calc = calculate(h2_spec, h2_bars)
    structure = _structure(calc, len(calc) - 1, 60)
    formal = project_plan(h2_bars, structure)
    r = study(h2_spec, h2_bars)[0]
    assert r['latest_plan'] == formal
    assert r['status'] == 'pending' and r['events'][0]['date'] == h2_bars.date.iloc[-1]
    instance = ResearchSpec(h2_spec, H2Settings(lookback=20, max_pullback=10)).instance()
    assert instance.LOOKBACK_WINDOW == 20
    assert h2_spec.instance().LOOKBACK_WINDOW == 60
    assert verify_frozen() == before


def test_full_tick_equal_high_not_trigger_and_next_plan_updates(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 11.6, 10.4)
    r = study(h2_spec, bars)[0]
    assert not r['triggered'] and r['latest_plan']['entry'] == 11.61
    assert r['latest_plan']['stop'] == 9.99
    assert r['latest_plan']['sl1_plan_price'] == 10.39
    assert r['latest_plan']['target'] == 14.85
    assert len(r['events']) == 2
    bars = append_bar(bars, 11.61, 10.6)
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['triggered'] and r['entry'] == 11.61
    assert r['stop'] == 9.99 and r['target'] == 14.85 and r['status'] == 'open'


def test_stop_target_and_costs_use_fixed_fill_risk(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 11.8, 10.8)
    bars = append_bar(bars, 15, 11)
    r = study(h2_spec, bars)[0]
    assert r['status'] == 'closed' and r['reason'] == '2R 止盈'
    assert r['exit'] == 14.85
    assert r['r_multiple'] == pytest.approx(2)
    costly = study(h2_spec, bars, costs=Assumptions())[0]
    assert costly['r_multiple'] < r['r_multiple']
    summary = summarize_replay([r])
    assert summary['closed_trades'] == 1 and summary['win_rate'] == 1
    assert summary['boundaries']['profit'] == [r['id']]


def test_gap_fill_does_not_recalculate_frozen_two_r_target(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 12.5, 10.8)
    bars.loc[len(bars) - 1, ['open', 'close']] = [12.0, 12.1]
    bars = append_bar(bars, 15.0, 11.5)
    r = study(h2_spec, bars)[0]
    assert r['planned_entry'] == 11.61 and r['entry'] == 12.0
    assert r['stop'] == 9.99 and r['target'] == 14.85
    assert r['reason'] == '2R 止盈'
    assert r['r_multiple'] == pytest.approx((14.85 - 12.0) / (12.0 - 9.99))


def test_post_fill_double_touch_is_conservatively_stopped(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 11.8, 10.8)
    bars = append_bar(bars, 15.0, 9.8)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.0, 12.0]
    r = study(h2_spec, bars)[0]
    assert r['reason'] == 'SL 止损（同日双触碰优先止损）'
    assert r['exit'] == 9.99


@pytest.mark.parametrize('open_price,high,low,expected_reason,expected_exit', [
    (9.50, 10.0, 9.0, '跳空止损', 9.50),
    (15.50, 16.0, 15.0, '2R 止盈', 14.85),
])
def test_post_fill_gap_exit_keeps_existing_conservative_prices(
        h2_spec, h2_bars, open_price, high, low, expected_reason, expected_exit):
    bars = append_bar(h2_bars, 11.8, 10.8)
    bars = append_bar(bars, high, low)
    bars.loc[len(bars) - 1, ['open', 'close']] = [open_price, open_price]
    r = study(h2_spec, bars)[0]
    assert r['reason'] == expected_reason and r['exit'] == expected_exit


def test_t1_fill_day_then_blocked_day_and_next_tradable_stop(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 12, 9.8)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 10.2]
    bars = append_bar(bars, 10.2, 10.2)
    bars = append_bar(bars, 10.0, 9.4)
    bars.loc[len(bars) - 1, 'open'] = 9.7
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['status'] == 'closed'
    assert r['reason'] == 'T+1 延迟止损'
    assert r['exit'] == 9.7 and r['holding_bars'] == 2
    assert not any(e['kind'] == 'exit' and e['date'] == r['entry_date'] for e in r['events'])
    assert any(e['kind'] == 't1' for e in r['events']) and any(e['kind'] == 'blocked' for e in r['events'])


def test_legacy_v1_baseline_preserves_original_t1_sl1_path(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 12, 10.3)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 10.6]
    bars = append_bar(bars, 10.2, 10.2)
    bars = append_bar(bars, 10.1, 9.4)
    bars.loc[len(bars) - 1, 'open'] = 9.7
    r = run_replay(h2_spec, bars, bars.date.iloc[124], bars.date.iloc[-1],
                   costs=ZERO, execution_model=LEGACY_MODEL)[0]
    assert r['execution_model'] == LEGACY_MODEL
    assert r['pricing_basis'] == 'sl1-mm-v1'
    assert r['filled'] and r['status'] == 'closed'
    assert r['reason'] == 'T+1 延迟止损'
    assert r['stop'] == 10.49 and r['target'] == 14
    assert r['exit'] == 9.7
    assert any(e['kind'] == 't1' and 'SL1' in e['text'] for e in r['events'])


def test_open_fill_floor_only_does_not_cancel_position(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 12, 9.995)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 10.2]
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['status'] == 'open'
    assert not any(e['kind'] == 't1' for e in r['events'])
    assert any('跌破 Floor 不倒推取消成交' in e['text'] for e in r['events'])


def test_open_fill_then_intraday_mm_does_not_cancel_known_fill(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 14.1, 10.7)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 13.8]
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['status'] == 'open'
    assert r['entry_date'] == bars.date.iloc[-1]


def test_intraday_entry_and_floor_remain_conservatively_unfilled(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 12, 9.8)
    bars.loc[len(bars) - 1, ['open', 'close']] = [10.8, 11.0]
    r = study(h2_spec, bars)[0]
    assert r['triggered'] and not r['filled'] and r['status'] == 'unfilled'
    assert '先后无法确认' in r['reason']


def test_open_at_mm_invalidates_before_entry(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 14.2, 10.7)
    bars.loc[len(bars) - 1, ['open', 'close']] = [14.0, 13.8]
    r = study(h2_spec, bars)[0]
    assert r['triggered'] and not r['filled'] and r['status'] == 'mm_first'
    assert r['reason'] == '开盘已先达到 MM，原机会失效'


def test_open_at_two_r_before_later_intraday_mm_is_unfilled_not_mm_first(h2_spec, h2_bars):
    _, calculated = calculate(h2_spec, h2_bars)
    structure = dict(_structure(calculated, len(calculated) - 1, 60), gap_floor=10.8)
    bars = append_bar(h2_bars, 14.1, 11.0)
    bars.loc[len(bars) - 1, ['open', 'close']] = [13.3, 13.8]

    r = replay_setup(bars, len(h2_bars) - 1, structure, H2Settings(), ZERO)

    assert r['trigger_plan']['target'] == 13.25
    assert r['trigger_plan']['mm_target'] == 14
    assert r['triggered'] and not r['filled']
    assert r['status'] == 'unfilled'
    assert r['reason'] == '开盘已达到计划 2R，不追价成交'


@pytest.mark.parametrize('mode', ['one_price', 'ambiguous_sl', 'floor', 'mm'])
def test_trigger_is_not_fill(h2_spec, h2_bars, mode):
    high, low = {'one_price': (11.7, 11.7), 'ambiguous_sl': (11.8, 9.9),
                 'floor': (11.8, 10.0), 'mm': (14.1, 10.7)}[mode]
    bars = append_bar(h2_bars, high, low)
    if mode == 'one_price':
        bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 11.7]
    r = study(h2_spec, bars)[0]
    assert r['triggered'] and not r['filled'] and r['status'] == ('mm_first' if mode == 'mm' else 'unfilled')
    assert list_prices(r) == (None, None, None)
    report = summarize_replay([r])
    assert report['triggered'] == report['unfilled'] == 1
    assert report['win_rate'] is None and report['mean_r'] is None


def test_wait_expiry_filters_and_suspended_signal_price_observation(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 11.5, 10.5)
    bars = append_bar(bars, 11.5, 10.5)
    expired = study(h2_spec, bars, H2Settings(wait_bars=1))[0]
    assert expired['status'] == 'expired' and not expired['triggered']
    filtered = study(h2_spec, bars, H2Settings(max_risk_pct=1))[0]
    assert filtered['status'] == 'filtered'
    assert summarize_replay([filtered])['opportunities'] == 0
    assert study(h2_spec, bars, H2Settings(min_mm_r=100))[0]['status'] == 'filtered'


def test_future_append_cannot_change_past_events_or_shape(h2_spec, h2_bars):
    prefix = append_bar(h2_bars, 11.5, 10.4)
    old = study(h2_spec, prefix)[0]
    future = append_bar(prefix, 11.8, 10.8)
    future = append_bar(future, 15, 11)
    new = study(h2_spec, future)[0]
    assert old['structure'] == new['structure'] and old['id'] == new['id']
    assert new['events'][:len(old['events'])] == old['events']
    frame, payload, events = replay_view(future, new, 0)
    assert frame.date.max() == new['setup_date'] and len(events) == 1
    assert 'entry_date' not in payload
    full, _, events = replay_view(future, new, 0, True)
    assert full.date.max() == future.date.max() and len(events) == 1


def test_replay_h2_marks_actual_trigger_after_fill_without_future_leak(h2_spec, h2_bars):
    import matplotlib.pyplot as plt
    from gui.h2_chart import draw_h2
    bars = append_bar(h2_bars, 11.8, 10.8)
    record = study(h2_spec, bars)[0]
    trigger = next(e for e in record['events'] if e['kind'] == 'trigger')
    assert trigger['date'] != record['setup_date']
    for index in (0, len(record['events'])-1):
        visible, payload, _ = replay_view(bars, record, index, posthoc=True)
        fig, ax = plt.subplots()
        try:
            ax.set_xlim(-1, len(visible))
            ax.set_ylim(8, 15)
            draw_h2(ax, visible, payload)
            marks = [text for text in ax.texts if text.get_text() == 'H2']
            assert bool(marks) == (index > 0)
            if marks:
                dates = visible.date.tolist()
                position = dates.index(trigger['date'])
                assert marks[0].xy == (position, float(visible.iloc[position].high))
                assert marks[0].arrow_patch.get_linestyle() == '--'
                assert marks[0].arrow_patch.shrinkB >= 5
        finally:
            plt.close(fig)


def test_boundaries_signs_ties_and_open_exclusion():
    records = [dict(id=str(i), code='sh.600000', setup_date=f'2025-01-{i + 1:02}',
                    status='closed', r_multiple=value) for i, value in enumerate([2, 2, 1, 0, -1, -2])]
    records.append(dict(id='open', status='open', r_multiple=100))
    assert boundary_samples(records) == {'profit': ['0', '1', '2'], 'loss': ['5', '4']}


@pytest.mark.parametrize('settings', [H2Settings(wait_bars=0), H2Settings(lookback=121),
                                     H2Settings(min_pullback=20, max_pullback=10),
                                     H2Settings(max_risk_pct=float('nan'))])
def test_invalid_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        settings.validate()


def test_cancellation(h2_spec, h2_bars):
    with pytest.raises(InterruptedError):
        run_replay(h2_spec, h2_bars, h2_bars.date.iloc[124], h2_bars.date.iloc[-1], cancelled=lambda: True)


def test_causal_prefilter_matches_daily_frozen_detector(h2_spec, h2_bars):
    bars = h2_bars
    for high, low in [(11.5, 10.4), (11.8, 10.7), (11.4, 10.3), (12, 10.8), (11.7, 10.6)]:
        bars = append_bar(bars, high, low)
    expected = []
    for i in range(124, len(bars)):
        _, calculated = calculate(h2_spec, bars.iloc[:i + 1])
        if bool(calculated.iloc[-1].get('signal_gap_h2', False)):
            expected.append(str(calculated.date.iloc[-1]))
    assert [r['setup_date'] for r in study(h2_spec, bars)] == expected


def test_service_receipt_replay_and_database_isolation(tmp_path, h2_bars):
    store = Store(tmp_path / 'replay')
    schema_before = store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']
    bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 15, 11)
    did = save_dataset(store, 'sh.600000', bars, 'csv', '测试合成行情，非真实验收')
    demo = save_dataset(store, 'sz.000001', bars, 'demo', '合成')
    assert [r['id'] for r in available_snapshots(store)[0]] == [did]
    service = Service(store)
    try:
        job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                    datasets=[did], start=bars.date.iloc[124], end=bars.date.iloc[-1]))
        row = wait_for(store, job)
        assert row['status'] == 'completed', row
        _, report, records = load_receipt(store, job)
        assert report['filled'] == report['closed_trades'] == 1
        assert report['datasets'][0]['sha256'] and report['engine_version']
        assert not report['real_data']  # Synthetic test CSV is never real-data acceptance.
        assert records[0]['events']
        for table in ('observations', 'plans', 'plan_history', 'watchlist', 'accounts',
                      'executions', 'positions', 'position_fills', 'closed_trades'):
            assert store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'] == 0
        image = render_replay(store, records[0], len(records[0]['events']) - 1)
        assert image.width > 800 and image.height > 300
        native = render_replay(store, records[0], 0, size=(900, 550))
        assert native.size == (900, 550)
        info = native.info['replay_view']
        assert info['price_x'][0] == pytest.approx(8)
        assert native.width-info['price_x'][1] == pytest.approx(64)
        assert info['price_y'][0] == pytest.approx(30)
        assert native.height-info['plot_y'][1] == pytest.approx(48)
        assert (info['price_y'][1]-info['price_y'][0])/(info['plot_y'][1]-info['price_y'][1]) == pytest.approx(7)
        assert max(r['date'] for r in info['candles']) <= records[0]['events'][0]['date']
        zoomed = render_replay(store, records[0], 0, size=(900, 550), viewport=(30, 20, .5))
        assert zoomed.info['replay_view']['bars'] == 30
        assert zoomed.info['replay_view']['offset'] == 20
        assert len(zoomed.info['replay_view']['candles']) == 30
        xs = zoomed.info['replay_view']['candle_x']
        assert zoomed.info['replay_view']['price_x'][1]-xs[-1] < 2*(xs[1]-xs[0])
        assert zoomed.info['replay_view']['candles'][0]['date'] == bars.date.iloc[zoomed.info['replay_view']['total']-50]
        assert zoomed.info['replay_view']['total'] == int((bars.date <= records[0]['events'][0]['date']).sum())
        assert native.tobytes() != zoomed.tobytes()
        assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'] == schema_before
    finally:
        service.pool.shutdown()


def test_unscoped_legacy_replay_request_fails_instead_of_becoming_generic_study(tmp_path):
    store = Store(tmp_path / 'legacy-route')
    service = Service(store)
    try:
        job = service.submit('backtest', {'execution_model': LEGACY_MODEL})
        row = wait_for(store, job)
        assert row['status'] == 'failed'
        assert 'v1' in row['message'] and '策略迭代' in row['message']
        assert json.loads(row['result'])['stop_reason'] == row['message']
        assert not list((store.root / 'research').glob(f'{job}/*'))
    finally:
        service.pool.shutdown()


def test_strategy_iteration_regenerates_authentic_v1_from_full_v2_scope(tmp_path, h2_bars):
    store = Store(tmp_path / 'legacy-baseline')
    bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 15, 11)
    did = save_dataset(store, 'sh.600000', bars, 'csv', '合成工程测试，非真实验收')
    service = Service(store)
    try:
        source = service.submit('backtest', dict(
            strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
            datasets=[did], start=bars.date.iloc[124], end=bars.date.iloc[-1],
            h2_settings={}, assumptions={}))
        assert wait_for(store, source)['status'] == 'completed'
        baseline = service.submit('backtest', dict(
            execution_model=LEGACY_MODEL, purpose=ITERATION_BASELINE_PURPOSE,
            baseline_source_job=source))
        assert wait_for(store, baseline)['status'] == 'completed'
        _, report, records = load_receipt(store, baseline)
        assert report['execution_model'] == LEGACY_MODEL
        assert report['purpose'] == ITERATION_BASELINE_PURPOSE
        assert report['baseline_source_job'] == source
        assert [d['id'] for d in report['datasets']] == [did]
        assert records[0]['pricing_basis'] == 'sl1-mm-v1'
        assert records[0]['reason'] == 'MM 止盈'
        assert records[0]['target'] == records[0]['structure']['mm_target']
        assert records[0]['stop'] == records[0]['trigger_plan']['sl1_plan_price']
        for table in ('observations', 'plans', 'plan_history', 'watchlist', 'accounts',
                      'executions', 'positions', 'position_fills', 'closed_trades'):
            assert not store.rows(f'SELECT * FROM {table}')
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_restart_interrupts_legacy_replay_without_changing_model(tmp_path, status):
    store = Store(tmp_path / status)
    spec = {'execution_model': LEGACY_MODEL, 'strategy': 'STRATEGY_GAP_H2'}
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('legacy', 'backtest', status, '2026-10-24T00:00:00+08:00', dumps(spec)))
    before_result = store.rows('SELECT result FROM jobs WHERE id=?', ('legacy',))[0]['result']
    service = Service(store)
    try:
        row = store.rows('SELECT * FROM jobs WHERE id=?', ('legacy',))[0]
        assert row['status'] == 'interrupted'
        assert json.loads(row['spec']) == spec
        assert row['result'] == before_result
        assert '可重新发起' in row['message']
    finally:
        service.pool.shutdown()


def test_cancelled_service_preserves_finished_symbols_only(tmp_path, h2_bars):
    store = Store(tmp_path / 'cancelled')
    ids = [save_dataset(store, code, h2_bars, 'csv', '合成工程测试')
           for code in ('sh.600000', 'sh.600012')]
    service = Service(store)
    calls = []
    def replay(spec, frame, start, end, settings, costs, progress, cancelled, **kwargs):
        calls.append(frame.attrs['code'])
        if len(calls) == 2:
            next(iter(service.cancel_flags.values())).set()
            raise InterruptedError('停止')
        return run_replay(spec, frame, start, end, settings, costs)
    try:
        with patch('workbench.h2_replay_service.run_replay', side_effect=replay):
            job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                    datasets=ids, start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1]))
            row = wait_for(store, job)
        assert row['status'] == 'cancelled'
        _, report, records = load_receipt(store, job)
        assert not report['complete'] and report['processed_datasets'] == 1
        assert len(records) == 1 and records[0]['dataset_id'] == ids[0]
        assert not store.rows('SELECT * FROM observations')
    finally:
        service.pool.shutdown()


def test_bad_snapshot_partial_and_demo_rejected(tmp_path, h2_bars):
    store = Store(tmp_path / 'partial')
    good = save_dataset(store, 'sh.600000', h2_bars, 'csv', '合成工程测试')
    demo = save_dataset(store, 'sh.600012', h2_bars, 'demo', '合成')
    service = Service(store)
    try:
        job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                    datasets=[good, demo], start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1]))
        row = wait_for(store, job)
        _, report, records = load_receipt(store, job)
        assert row['status'] == 'partial'
        assert report['errors'] and report['processed_datasets'] == 1
        assert not report['complete'] and len(records) == 1
    finally:
        service.pool.shutdown()
