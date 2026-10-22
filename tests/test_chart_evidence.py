"""Strategy-specific vectors retain geometry and the original signal prefix."""
from copy import deepcopy
import json
from unittest.mock import patch

import pandas as pd
import pytest
from matplotlib.patches import Rectangle

from core.strategy_registry import StrategyRegistry
from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
from gui.chart_evidence import strategy_evidence
from gui.theme import ANNOTATION, STOP, TARGET


def bars():
    return pd.DataFrame(dict(date=pd.bdate_range('2026-01-05', periods=40).strftime('%Y-%m-%d'),
        open=[10.+i*.1 for i in range(40)], close=[10.1+i*.1 for i in range(40)],
        high=[10.3+i*.1 for i in range(40)], low=[9.9+i*.1 for i in range(40)], volume=100))


def test_annotation_vectors_keep_original_dates_prices_and_prefix():
    frame = bars()
    anchor = frame.date.iloc[20]
    class Strategy:
        def get_signal_info(self, source):
            assert source.date.iloc[-1] == anchor
            return {}
        def annotate_chart(self, ax, source, kind, **kwargs):
            assert len(source) == 21 and kwargs['anchor_signal_date'] == anchor
            ax.annotate('original signal', xy=(20, 12), xytext=(17, 13), arrowprops=dict(arrowstyle='->'))
            ax.axhline(11.5, linestyle='--')
            ax.add_patch(Rectangle((10, 10), 5, 1, fill=False))
    before = frame.copy(deep=True)
    output = strategy_evidence(dict(frame=frame, payload=dict(asof=anchor),
        strategy=Strategy(), strategy_type='CUSTOM', metadata={}))
    label = next(item for item in output['items'] if item['kind'] == 'label')
    assert label['a'] == dict(date=anchor, value=12., offset=0.)
    assert label['b']['date'] == frame.date.iloc[17]
    assert label['leader'] and label['color'].lower() == ANNOTATION.lower()
    rect = next(item for item in output['items'] if item['kind'] == 'rectangle')
    assert rect['a']['date'] == frame.date.iloc[10] and rect['b']['date'] == frame.date.iloc[15]
    assert rect['fill'] is None and rect['a']['value'] == 10 and rect['b']['value'] == 11
    assert next(item for item in output['items'] if item['kind'] == 'hline')['value'] == 11.5
    assert output['availableDate'] == anchor
    pd.testing.assert_frame_equal(frame, before)
    json.dumps(output, allow_nan=False)


@pytest.mark.parametrize('kind,column,labels', [
    ('MTR_MASTER', 'signal_mtr', {'2. Sell Climax\n(抛售高潮)', '3. First Leg\n(一腿突破)', '4. SIGNAL (HL)\n(次低点确认)'}),
    ('STRATEGY_AWIL', 'signal_awil', {'H2 Signal\n(AWIL入场)'}),
    ('STRATEGY_3K', 'signal_3k', set()),
])
def test_real_strategy_annotation_methods_are_not_replaced_with_generic_star(kind, column, labels):
    frame = bars()
    frame[column] = False
    frame.loc[20, column] = True
    frame.loc[35, column] = True  # later opportunity must not replace the selected original signal.
    frame['signal_3k_gap_test'] = False
    frame['is_sw_h_geometric'] = False
    frame['sl_awil'] = 10.
    frame['tp_awil'] = 15.
    frame['awil_swing_high'] = 13.
    strategy = StrategyRegistry.get_strategy(kind)
    output = strategy_evidence(dict(frame=frame, payload=dict(asof=frame.date.iloc[20]),
        strategy=strategy, strategy_type=kind, metadata=strategy.get_metadata()))
    assert labels <= {item['text'] for item in output['items'] if item['kind'] == 'label'}
    if kind == 'STRATEGY_3K':
        marker, = [item for item in output['items'] if item['kind'] == 'marker']
        assert marker['marker'] == '^' and marker['text'] == '3K 信号'
    for item in output['items']:
        if item.get('a'):
            assert item['a']['date'] <= frame.date.iloc[20]
    json.dumps(output, allow_nan=False)


def test_mtr_trend_omits_non_finite_points_from_web_evidence():
    frame = bars()
    frame['signal_mtr'] = False
    frame.loc[20, 'signal_mtr'] = True
    frame['is_sw_h_geometric'] = False
    frame['geometric_trendline'] = float('nan')
    frame.loc[12:20, 'geometric_trendline'] = [10.2 + index * .1 for index in range(9)]
    strategy = StrategyRegistry.get_strategy('MTR_MASTER')
    output = strategy_evidence(dict(frame=frame, payload=dict(asof=frame.date.iloc[20]),
        strategy=strategy, strategy_type='MTR_MASTER', metadata=strategy.get_metadata()))
    trend = next(item for item in output['items']
                 if item['kind'] == 'line' and item['color'] == '#8B949E')
    assert len(trend['points']) == 9
    assert all(point is not None for point in trend['points'])
    json.dumps(output, allow_nan=False)


def test_gap_web_evidence_matches_desktop_label_and_archived_price_fallback():
    frame = bars()
    frame['is_breakout_gp'] = False
    frame['signal_gap_pinbar'] = False
    frame['gap_pinbar_prior_low'] = float('nan')
    frame['gap_pinbar_floor_exact'] = float('nan')
    frame['gap_pinbar_top_exact'] = float('nan')
    anchor = frame.date.iloc[20]
    payload = dict(asof=anchor, setup_date=anchor, entry=12.2, stop=11.8, target=13.0)
    strategy = GapPinbarStrategy()
    output = strategy_evidence(dict(frame=frame, payload=payload, strategy=strategy,
        strategy_type='STRATEGY_GAP_PINBAR', metadata=strategy.get_metadata()))
    assert '原信号结构待核对（保留归档计划价格）' in output['facts']
    levels = {(item['text'], item['value'], item['color']) for item in output['items']}
    assert levels == {('SL', 11.8, STOP), ('TP1', 13.0, TARGET)}
    assert output['availableDate'] == anchor
    json.dumps(output, allow_nan=False)


def test_non_h2_gap_web_evidence_does_not_add_h2_label():
    frame = bars()
    anchor = frame.date.iloc[20]
    payload = dict(asof=anchor, setup_date=anchor, stop=11.8, target=13.0)

    class Strategy:
        def get_signal_info(self, source):
            return {}

        def annotate_chart(self, ax, source, kind, **kwargs):
            ax.text(20, 12, 'H2')
            ax.text(20, 11.9, 'Gap')

    with patch('gui.chart_evidence.gap_annotation_frame', return_value=frame.iloc[:21].copy()):
        output = strategy_evidence(dict(frame=frame, payload=payload, strategy=Strategy(),
            strategy_type='STRATEGY_GAP_PINBAR', metadata={}))
    labels = {item['text'] for item in output['items'] if item['kind'] == 'label'}
    assert 'Gap' in labels and 'H2' not in labels


def test_line_collections_preserve_gap_segment_geometry_without_fake_markers():
    frame = bars()
    before = frame.copy(deep=True)
    class Strategy:
        def get_signal_info(self, source):
            return {}
        def annotate_chart(self, ax, source, kind, **kwargs):
            ax.hlines([11.,12.], [5,9], [20,20], colors=['red','blue'],
                      linestyles=[':', '--'], linewidths=[.9,1.2], alpha=.5)
            ax.vlines(10, 10.5, 12.5, color='green')
    output = strategy_evidence(dict(frame=frame, payload={'asof':frame.date.iloc[20]},
        strategy=Strategy(),strategy_type='CUSTOM',metadata={}))
    lines = [item for item in output['items'] if item['kind']=='line']
    assert len(lines)==3 and not any(item['kind']=='marker' for item in output['items'])
    assert lines[0]['points']==[dict(date=frame.date.iloc[5],value=11.,offset=0.),
                               dict(date=frame.date.iloc[20],value=11.,offset=0.)]
    assert lines[1]['points'][0]['date']==frame.date.iloc[9]
    assert lines[2]['points'][0]['date']==lines[2]['points'][1]['date']==frame.date.iloc[10]
    assert lines[0]['alpha']==.5 and lines[0]['width']==.9
    assert isinstance(lines[0]['dash'],list) and lines[0]['dash']!=lines[1]['dash']
    pd.testing.assert_frame_equal(frame,before)
    json.dumps(output,allow_nan=False)


def test_unknown_scatter_still_requires_explicit_strategy_marker_contract():
    class Strategy:
        def get_signal_info(self, source): return {}
        def annotate_chart(self, ax, source, kind, **kwargs): ax.scatter([1],[11])
    with pytest.raises(ValueError,match='散点'):
        strategy_evidence(dict(frame=bars(),strategy=Strategy(),strategy_type='CUSTOM',metadata={}))


@pytest.mark.parametrize('kind,prefix,breakout,signal', [
    ('STRATEGY_STRUCTURAL_GAP','struct_gap','is_breakout','signal_struct_gap_confirm'),
    ('STRATEGY_GAP_PINBAR','gap_pinbar','is_breakout_gp','signal_gap_pinbar'),
])
def test_real_gap_strategy_with_prior_open_gap_loads_all_chart_vectors(kind,prefix,breakout,signal):
    frame=bars()
    columns=['open','high','low','close']
    frame.loc[5:,columns]+=1
    frame.loc[20:,columns]+=1
    frame[breakout]=False
    frame.loc[[5,20],breakout]=True
    frame[signal]=False
    frame.loc[30,signal]=True
    floor,prior,top=13.1,9.9,13.9
    for suffix,value in [('_floor_exact',floor),('_prior_low',prior),('_top_exact',top)]:
        frame[prefix+suffix]=float('nan')
        frame.loc[30,prefix+suffix]=value
    target=floor+top-prior if prefix=='struct_gap' else 2*floor-prior
    payload=dict(asof=frame.date.iloc[30],setup_date=frame.date.iloc[30],
                 entry=float(frame.high.iloc[30]),stop=floor,target=target)
    strategy=StrategyRegistry.get_strategy(kind)
    before=frame.copy(deep=True)
    output=strategy_evidence(dict(frame=frame,payload=payload,strategy=strategy,
        strategy_type=kind,metadata=strategy.get_metadata()))
    prior_gap=[item for item in output['items'] if item['kind']=='line'
               and item['points'][0]['date']==frame.date.iloc[5]]
    assert len(prior_gap)==1
    assert prior_gap[0]['points']==[dict(date=frame.date.iloc[5],value=float(frame.high.iloc[3]),offset=0.),
                                   dict(date=frame.date.iloc[30],value=float(frame.high.iloc[3]),offset=0.)]
    assert any(item['kind']=='rectangle' for item in output['items'])
    assert not any(item['kind']=='marker' for item in output['items'])
    pd.testing.assert_frame_equal(frame,before)
    json.dumps(output,allow_nan=False)


def test_failed_embedded_context_releases_cover_only_through_save_acknowledgement():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from gui.chart_panel import ChartPanel
    host=Mock()
    host.closed.return_value=False
    panel=SimpleNamespace(readout_frame=Mock(),readout=Mock(),_embedded=host,
                          _image=object(),_workspace_context=None)
    ChartPanel._embedded_error(panel,'unsupported annotation')
    host.request_close.assert_called_once_with()
    panel.readout_frame.grid.assert_called_once_with()
    assert 'unsupported annotation' in panel.readout.set.call_args.args[0]
