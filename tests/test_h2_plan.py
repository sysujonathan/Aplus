"""Next-session GAP H2 plans are causal, anchored and independent of trades."""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock, patch

from workbench.h2_plan import display_plan, plan_at_end, project_plan
from workbench.prices import offset_tick, tick_size
from workbench.strategies import calculate, catalog, signal_at_end, verify_frozen
from workbench.store import Store
from gui.chart_panel import ChartPanel, render_chart


@pytest.fixture
def h2_bars():
    count = 130
    frame = pd.DataFrame({
        'date': pd.bdate_range('2025-01-01', periods=count).strftime('%Y-%m-%d'),
        'open': 9., 'high': 10., 'low': 6., 'close': 9., 'volume': 1000.,
    })
    # One real detector BO -> lower high/low -> H1 -> H2, MM = 14.
    for i, (high, low) in enumerate([(12., 10.8), (11.8, 10.6),
                                    (11.9, 10.7), (11.6, 10.5)], 126):
        frame.loc[i, ['open', 'high', 'low', 'close']] = [low + .1, high, low, high - .1]
    return frame


@pytest.fixture
def h2_spec(tmp_path):
    return catalog(Store(tmp_path / 'isolated'))['STRATEGY_GAP_H2']


def append_bar(frame, high, low):
    day = (pd.Timestamp(frame.date.iloc[-1]) + pd.offsets.BDay()).strftime('%Y-%m-%d')
    return pd.concat([frame, pd.DataFrame([{
        'date': day, 'open': low + .1, 'high': high, 'low': low,
        'close': high - .1, 'volume': 1000.,
    }])], ignore_index=True)


def anchored_plan(spec, bars, day):
    instance, calculated = calculate(spec, bars)
    return plan_at_end(instance, calculated, 'sh.600000', day)


def test_daily_entry_sl1_and_shared_risk_basis(h2_spec, h2_bars):
    first = signal_at_end(h2_spec, h2_bars)
    assert first is not None
    assert first['h2_setup_date'] == h2_bars.date.iloc[-1]
    assert first['pending_state'] == 'PENDING'
    assert first['gap_floor'] == 10
    assert first['entry_plan_price'] == 11.61
    assert first['sl1_plan_price'] == 10.49
    assert first['target'] == first['mm_target'] == 14
    assert first['stop'] != first['gap_floor']
    second_bars = append_bar(h2_bars, 11.4, 10.3)
    second = signal_at_end(h2_spec, second_bars)
    assert second['setup_date'] == first['setup_date']
    assert second['entry_plan_price'] == 11.41
    assert second['sl1_plan_price'] == 10.29
    assert second['sl1_reference_date'] == second_bars.date.iloc[-1]
    assert second['initial_risk_pct'] == pytest.approx((11.41 - 10.29) / 11.41 * 100)
    assert second['mm_r_multiple'] == pytest.approx((14 - 11.41) / (11.41 - 10.29))
    assert second['rating'] is None


@pytest.mark.parametrize('high,low,state,reason', [
    (11.61, 10.5, 'TRIGGERED', 'previous_plan_price_reached'),
    (11.60, 10.5, 'PENDING', ''),
    (11.60, 10., 'INVALID', 'gap_floor_broken'),
    (14., 10.5, 'INVALID', 'target_already_reached'),
    (12., 9.9, 'INVALID', 'gap_floor_broken'),
])
def test_lifecycle_and_conservative_same_bar_order(h2_spec, h2_bars, high, low, state, reason):
    day = h2_bars.date.iloc[-1]
    bars = append_bar(h2_bars, high, low)
    plan = anchored_plan(h2_spec, bars, day)
    assert plan['pending_state'] == state
    assert plan['state_reason'] == reason
    if state != 'PENDING':
        assert signal_at_end(h2_spec, bars) is None
        assert plan['entry'] is None and plan['stop'] is None
        assert plan['initial_risk_pct'] is None


def test_timeout_30_bars_and_cannot_revive(h2_spec, h2_bars):
    day = h2_bars.date.iloc[-1]
    bars = h2_bars
    for _ in range(30):
        bars = append_bar(bars, 11.6, 10.5)
    assert anchored_plan(h2_spec, bars, day)['pending_state'] == 'PENDING'
    bars = append_bar(bars, 11.6, 10.5)
    assert anchored_plan(h2_spec, bars, day)['pending_state'] == 'EXPIRED'
    bars = append_bar(bars, 11.6, 10.5)
    assert signal_at_end(h2_spec, bars) is None


def test_sl1_excludes_breakout_and_includes_first_leg(h2_spec, h2_bars):
    instance, calculated = calculate(h2_spec, h2_bars)
    calculated.loc[126, 'low'] = 10.1  # BO low must not become SL1.
    calculated.loc[127, 'low'] = 10.2  # Earlier first-leg low must remain eligible.
    plan = plan_at_end(instance, calculated, 'sh.600000')
    assert plan['sl1_reference_low'] == 10.2
    assert plan['sl1_reference_date'] == h2_bars.date.iloc[127]
    assert plan['sl1_plan_price'] == 10.19


def test_display_uses_latest_plan_and_preserves_original_h2(h2_spec, h2_bars):
    payload = signal_at_end(h2_spec, h2_bars)
    latest = append_bar(h2_bars, 11.4, 10.3)
    before = dict(payload)
    plan = display_plan(h2_spec, latest, payload, 'sh.600000', payload['setup_date'], 'watch')
    assert plan['entry'] == 11.41
    assert plan['stop'] == 10.29
    assert plan['plan_asof'] == latest.date.iloc[-1]
    assert plan['h2_setup_date'] == payload['setup_date']
    assert plan['bo_date'] == payload['bo_date']
    assert payload == before
    # A historical candidate remains an immutable as-of plan.
    assert display_plan(h2_spec, latest, payload, 'sh.600000', payload['setup_date']) == payload


def test_watch_does_not_replace_old_setup_with_new_signal(h2_spec, h2_bars):
    payload = signal_at_end(h2_spec, h2_bars)
    latest = append_bar(h2_bars, 11.7, 10.4)
    latest = append_bar(latest, 11.5, 10.3)
    plan = display_plan(h2_spec, latest, payload, 'sh.600000', payload['setup_date'], 'watch')
    assert plan['h2_setup_date'] == payload['setup_date']
    assert plan['pending_state'] == 'TRIGGERED'
    assert plan['entry'] is None


def test_imported_reminder_resolves_setup_at_original_scan_close(h2_spec, h2_bars):
    reminder = append_bar(h2_bars, 11.4, 10.3)
    old_payload = {'entry': 11.4, 'stop': 10, 'target': 14, 'legacy_import': True}
    # Import had stored the reminder's scan day, rather than the raw H2 day.
    day = reminder.date.iloc[-1]
    plan = display_plan(h2_spec, reminder, old_payload, 'sh.600000', day)
    assert plan['h2_setup_date'] == h2_bars.date.iloc[-1]
    assert plan['entry'] == 11.41
    latest = append_bar(reminder, 11.6, 10.3)
    watched = display_plan(h2_spec, latest, old_payload, 'sh.600000', day, 'watch')
    assert watched['h2_setup_date'] == plan['h2_setup_date']
    assert watched['pending_state'] == 'TRIGGERED'


def test_watch_anchor_survives_300_bar_window(h2_spec, h2_bars):
    payload = signal_at_end(h2_spec, h2_bars)
    latest = h2_bars
    for _ in range(305):
        latest = append_bar(latest, 11.6, 10.5)
    plan = display_plan(h2_spec, latest, payload, 'sh.600000', payload['setup_date'], 'watch')
    assert plan['h2_setup_date'] == payload['setup_date']
    assert plan['pending_state'] == 'EXPIRED'


def test_prior_open_gaps_count_uses_current_survival(h2_spec, h2_bars):
    plan = signal_at_end(h2_spec, h2_bars)
    structure = dict(plan, prior_gap_structures=[
        {'date': h2_bars.date.iloc[125], 'floor': 10.2},
        {'date': h2_bars.date.iloc[126], 'floor': 10.1},
    ])
    assert project_plan(h2_bars, structure)['prior_open_gap_count'] == 2
    latest = append_bar(h2_bars, 11.4, 10.15)
    updated = project_plan(latest, structure)
    assert updated['prior_open_gap_count'] == 1
    assert updated['pending_state'] == 'PENDING'


def test_future_bars_do_not_change_asof_plan(h2_spec, h2_bars):
    latest = append_bar(h2_bars, 11.4, 10.3)
    before = signal_at_end(h2_spec, latest)
    future = append_bar(latest, 100, 1)
    assert signal_at_end(h2_spec, future.iloc[:-1]) == before


def test_missing_anchor_hides_old_prices(h2_spec, h2_bars):
    payload = signal_at_end(h2_spec, h2_bars)
    plan = display_plan(h2_spec, h2_bars.tail(2), payload, 'sh.600000', payload['setup_date'], 'watch')
    assert plan['pending_state'] == 'UNAVAILABLE'
    assert plan['entry'] is None and plan['stop'] is None


def test_frozen_and_fixed_price_research_stay_unchanged(h2_spec, h2_bars):
    legacy = signal_at_end(h2_spec, h2_bars, with_rating=False, plan_prices=False)
    assert legacy['entry'] == 11.6
    assert legacy['stop'] == 10
    assert legacy['target'] == pytest.approx(14.8)  # Frozen active first target = 2R.
    assert 'plan_kind' not in legacy
    assert len(verify_frozen()) == 64


@pytest.mark.parametrize('code', ['600000', 'sz.000001', 'sh.688001', 'bj.920001'])
def test_tick_is_central_and_decimal_safe(code):
    assert tick_size(code, 47.50) == .01
    assert offset_tick(code, 47.5, 1) == 47.51
    assert offset_tick(code, 45.9, -1) == 45.89


def test_unconfigured_instrument_does_not_get_stock_tick():
    with pytest.raises(ValueError, match='tick'):
        tick_size('sh.510300', 4)


@pytest.mark.parametrize('high,low,state', [(11.4, 10.3, 'PENDING'), (11.7, 10.4, 'TRIGGERED')])
def test_watch_panel_passes_updated_plan_and_title(h2_spec, h2_bars, high, low, state):
    payload = signal_at_end(h2_spec, h2_bars)
    latest = append_bar(h2_bars, high, low)
    store, panel = Mock(), Mock()
    store.rows.return_value = [{'code': 'sh.600000', 'strategy': h2_spec.id,
                                'timeframe': 'daily', 'asof': h2_bars.date.iloc[-1],
                                'setup_date': payload['setup_date']}]
    with patch('gui.data.load_observation_candles', return_value=(h2_bars, {}, payload)), \
         patch('workbench.market.load_dataset', return_value=(latest, {'end': latest.date.iloc[-1]})), \
         patch('gui.data.code_names', return_value={}), \
         patch('workbench.strategies.catalog', return_value={h2_spec.id: h2_spec}), \
         patch('gui.chart_panel.render_chart', return_value=Mock()) as render:
        ChartPanel.show_observation(panel, store, 'anchor', market_dataset_id='new', mode='watch')
    shown_frame, shown_plan = render.call_args.args[:2]
    assert shown_frame.date.iloc[-1] == latest.date.iloc[-1]
    assert shown_plan['pending_state'] == state
    assert shown_plan['h2_setup_date'] == payload['setup_date']
    title = panel._title_var.set.call_args_list[-1].args[0]
    assert f"H2信号 {payload['setup_date']}" in title
    assert '计划线来自信号日' not in title
    if state == 'PENDING':
        assert '明日计划基于' in title
        assert shown_plan['entry'] == 11.41
    else:
        assert '市场已触发计划价' in title
        assert shown_plan['entry'] is None


def test_chart_annotations_point_to_candle_centers(h2_spec, h2_bars):
    from matplotlib.axes import Axes
    latest = append_bar(h2_bars, 11.4, 10.3)
    plan = signal_at_end(h2_spec, latest)
    instance, calculated = calculate(h2_spec, latest)
    before = calculated.copy(deep=True)
    real_annotate = Axes.annotate
    annotations = []

    def capture(ax, label, *args, **kwargs):
        annotations.append((label, kwargs['xy']))
        return real_annotate(ax, label, *args, **kwargs)

    with patch.object(Axes, 'annotate', capture):
        image = render_chart(calculated, plan, '', instance.get_metadata())
    dates = calculated.tail(120).date.tolist()
    by_label = dict(annotations)
    assert by_label['Entry'] == (len(dates) - 1, 11.41)
    assert by_label['SL1'] == (dates.index(plan['sl1_reference_date']), 10.29)
    assert by_label['H2'][0] == dates.index(plan['h2_setup_date'])
    assert all(float(x).is_integer() for _, (x, _) in annotations)
    assert image.width > 500
    pd.testing.assert_frame_equal(calculated, before)


def test_scan_archives_new_plan_without_replacing_legacy_or_duplicating_manual_plan(tmp_path, h2_spec, h2_bars):
    import json
    from workbench.market import save_dataset
    from workbench.service import Service
    from workbench.store import digest, dumps, now
    from tests.test_workbench import wait_for
    store = Store(tmp_path / 'scan-plan')
    did = save_dataset(store, 'sh.600000', h2_bars, 'csv', '前复权')
    day = h2_bars.date.iloc[-1]
    legacy = signal_at_end(h2_spec, h2_bars, with_rating=False, plan_prices=False)
    oid = digest(f'{did}|{h2_spec.id}|{h2_spec.version}|daily|{day}'.encode())
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (oid, 'previous-scan', 'sh.600000', h2_spec.id, h2_spec.version,
                   'daily', day, day, did, dumps(legacy), now()))
    pid = store.save_plan(oid, '观察', 11.6, 10, 14.8, 100, '测试人工计划')
    service = Service(store)
    settings = dict(datasets=[did], strategies=[h2_spec.id], timeframe='daily', asof=day)
    try:
        first = wait_for(store, service.submit('scan', settings))
        assert first['status'] == 'completed'
        report = json.loads(first['result'])
        assert report['signals'] == 1
        new_id = report['observation_ids'][0]
        assert new_id != oid
        rows = store.rows('SELECT * FROM observations WHERE id=?', (new_id,))
        payload = json.loads(rows[0]['payload'])
        assert payload['entry'] == 11.61 and payload['stop'] == 10.49
        assert payload['target'] == 14
        assert payload['plan_version']
        assert json.loads(store.rows('SELECT payload FROM observations WHERE id=?', (oid,))[0]['payload']) == legacy
        second = wait_for(store, service.submit('scan', settings))
        assert json.loads(second['result'])['reused'] == 1
        assert len(store.rows('SELECT * FROM plans')) == 1
        assert store.save_plan(new_id, '观察', 11.61, 10.49, 14, 100, '测试更新') == pid
        assert len(store.rows('SELECT * FROM plans')) == 1
    finally:
        service.pool.shutdown()
