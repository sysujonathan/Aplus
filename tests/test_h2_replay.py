import json
from dataclasses import replace
from unittest.mock import patch

import pandas as pd
import pytest

from workbench.backtest import Assumptions
from workbench.h2_plan import _structure, project_plan
from workbench.h2_replay import H2Settings, MODEL, ResearchSpec, boundary_samples, run_replay, summarize_replay
from workbench.h2_replay_service import available_snapshots, load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store
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
    assert r['latest_plan']['stop'] == 10.39
    assert len(r['events']) == 2
    bars = append_bar(bars, 11.61, 10.6)
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['triggered'] and r['entry'] == 11.61
    assert r['stop'] == 10.39 and r['status'] == 'open'


def test_stop_target_and_costs_use_fixed_fill_risk(h2_spec, h2_bars):
    bars = append_bar(h2_bars, 11.8, 10.8)
    bars = append_bar(bars, 14.2, 11)
    r = study(h2_spec, bars)[0]
    assert r['status'] == 'closed' and r['reason'] == 'MM 止盈'
    assert r['exit'] == 14
    assert r['r_multiple'] == pytest.approx((14 - 11.61) / (11.61 - 10.49))
    costly = study(h2_spec, bars, costs=Assumptions())[0]
    assert costly['r_multiple'] < r['r_multiple']
    summary = summarize_replay([r])
    assert summary['closed_trades'] == 1 and summary['win_rate'] == 1
    assert summary['boundaries']['profit'] == [r['id']]


def test_t1_stop_deferred_to_next_tradable_open(h2_spec, h2_bars):
    # Entry is observable at the open before SL1 gets hit that same day.
    bars = append_bar(h2_bars, 12, 10.4)
    bars.loc[len(bars) - 1, ['open', 'close']] = [11.7, 11.8]
    bars = append_bar(bars, 10.2, 10.2)
    bars = append_bar(bars, 10.3, 9.9)
    bars.loc[len(bars) - 1, 'open'] = 10.1
    r = study(h2_spec, bars)[0]
    assert r['filled'] and r['status'] == 'closed'
    assert r['reason'] == 'T+1 延迟止损'
    assert r['exit'] == 10.1 and r['holding_bars'] == 2
    assert any(e['kind'] == 't1' for e in r['events'])
    assert any(e['kind'] == 'blocked' for e in r['events'])


@pytest.mark.parametrize('mode', ['one_price', 'ambiguous_sl1', 'floor', 'mm'])
def test_trigger_is_not_fill(h2_spec, h2_bars, mode):
    high, low = {'one_price': (11.7, 11.7), 'ambiguous_sl1': (11.8, 10.4),
                 'floor': (11.8, 9.9), 'mm': (14.1, 10.7)}[mode]
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
    future = append_bar(future, 14.1, 11)
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
    bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 14.1, 11)
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


def test_cancelled_service_preserves_finished_symbols_only(tmp_path, h2_bars):
    store = Store(tmp_path / 'cancelled')
    ids = [save_dataset(store, code, h2_bars, 'csv', '合成工程测试')
           for code in ('sh.600000', 'sh.600012')]
    service = Service(store)
    calls = []
    def replay(spec, frame, start, end, settings, costs, progress, cancelled):
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
