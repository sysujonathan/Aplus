"""Strategy-specific vectors retain geometry and the original signal prefix."""
from copy import deepcopy
import json

import pandas as pd
import pytest
from matplotlib.patches import Rectangle

from core.strategy_registry import StrategyRegistry
from gui.chart_evidence import strategy_evidence
from gui.theme import ANNOTATION


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
