"""旧关注的缺口结构必须来自原计划证据，不能被后来突破或估算价格替换。"""
from unittest.mock import patch

import pandas as pd
import pytest

from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
from gui.chart_annotations import gap_annotation_frame
from gui.chart_renderer import render_chart


def bars():
    frame = pd.DataFrame({
        'date': pd.bdate_range('2026-01-01', periods=110).strftime('%Y-%m-%d'),
        'open': 5.4, 'high': 5.8, 'low': 5.2, 'close': 5.5,
        'volume': 1000., 'ema20': 5.4,
        'is_breakout_gp': False, 'signal_gap_pinbar': False,
        'gap_pinbar_prior_low': float('nan'),
        'gap_pinbar_floor_exact': float('nan'),
        'gap_pinbar_top_exact': float('nan'),
    })
    frame.loc[40, 'high'] = 5.88
    frame.loc[50, 'low'] = 4.92
    frame.loc[70:109, ['open','high','low','close']] = [6.1,6.3,6.02,6.2]
    frame.loc[70, ['is_breakout_gp','low']] = [True,5.90]
    frame.loc[80, ['is_breakout_gp','high','low']] = [True,6.35,6.15]
    return frame


def plan(frame):
    return dict(entry=6.22, stop=5.88, target=6.84,
                asof=frame.date.iloc[99], setup_date=frame.date.iloc[99])


def test_archived_gap_recovers_verified_original_breakout_not_latest():
    frame = bars()
    before = frame.copy(deep=True)
    payload = plan(frame)
    restored = gap_annotation_frame(frame, payload, 'STRATEGY_GAP_PINBAR')
    assert restored.loc[99, 'gap_pinbar_floor_exact'] == pytest.approx(5.88)
    assert restored.loc[99, 'gap_pinbar_prior_low'] == pytest.approx(4.92)
    assert restored.loc[99, 'gap_pinbar_top_exact'] == pytest.approx(5.90)
    assert restored.loc[70, 'is_breakout_gp']
    assert not restored.loc[80, 'is_breakout_gp']
    pd.testing.assert_frame_equal(frame, before)
    assert payload == plan(frame)


@pytest.mark.parametrize('change', ['unmatched_plan','closed_gap','missing_breakout'])
def test_unverifiable_structure_is_not_replaced_with_estimated_prices(change):
    frame = bars()
    payload = plan(frame)
    if change == 'unmatched_plan':
        payload['target'] = 8.0
    elif change == 'closed_gap':
        frame.loc[90,'low'] = 5.0
    else:
        frame['is_breakout_gp'] = False
    assert gap_annotation_frame(frame, payload, 'STRATEGY_GAP_PINBAR') is None


def test_rendered_pinbar_marks_real_floor_and_mm_low_without_h2_label():
    frame = bars()
    captured = {}
    original = GapPinbarStrategy.annotate_chart

    def capture(axis, visible, strategy_type, **kwargs):
        captured['axis'] = axis
        return original(axis, visible, strategy_type, **kwargs)

    with patch.object(GapPinbarStrategy, 'annotate_chart', side_effect=capture):
        render_chart(frame, plan(frame), '', GapPinbarStrategy.get_metadata(),
                     strategy=GapPinbarStrategy(), strategy_type='STRATEGY_GAP_PINBAR',
                     view_bars=90, view_offset=5)
    axis = captured['axis']
    labels = {text.get_text():text for text in axis.texts}
    assert labels['MM low'].xy[1] == pytest.approx(4.92)
    assert labels['Gap'].get_position()[1] == pytest.approx(5.89)
    assert 'H2' not in labels
    # The visible window begins at index 15. Original signal index 99 maps to 84.
    assert labels['Entry'].xy[0] == pytest.approx(84.5)


def test_outside_visible_anchor_never_falls_back_to_newer_gap():
    frame = bars()
    with patch.object(GapPinbarStrategy, 'annotate_chart') as annotate:
        render_chart(frame, plan(frame), '', GapPinbarStrategy.get_metadata(),
                     strategy=GapPinbarStrategy(), strategy_type='STRATEGY_GAP_PINBAR',
                     view_bars=5)
    annotate.assert_not_called()
