"""Desktop historical charts must honor the selected observation and timeframe."""
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from gui.chart_panel import ChartPanel, render_chart
from workbench.strategies import prepare, calculate, catalog
from workbench.store import Store


@pytest.mark.parametrize('timeframe', ['daily', 'weekly'])
def test_chart_uses_observation_cutoff_and_timeframe(tmp_path, timeframe):
    store = Store(tmp_path / 'isolated')
    count = 950
    close = 20 + np.sin(np.arange(count) / 10)
    frame = pd.DataFrame({'date': pd.bdate_range('2022-01-01', periods=count).strftime('%Y-%m-%d'),
                          'open': close - .1, 'high': close + .5, 'low': close - .5,
                          'close': close, 'volume': 1000.})
    cutoff = frame.date.iloc[850]
    strategy = 'STRATEGY_GAP_H2'
    spec = catalog(store)[strategy]
    expected = prepare(frame, timeframe, cutoff)
    _, expected = calculate(spec, expected)
    fake_store = Mock()
    fake_store.rows.return_value = [{'code': 'sh.600000', 'strategy': strategy,
                                    'timeframe': timeframe, 'asof': cutoff}]
    panel = Mock()
    with patch('gui.data.load_observation_candles', return_value=(frame, {}, {})), \
         patch('gui.data.code_names', return_value={}), \
         patch('workbench.strategies.catalog', return_value={strategy: spec}), \
         patch('gui.chart_panel.render_chart', return_value=Mock()) as render:
        ChartPanel.show_observation(panel, fake_store, 'observation')
    render.assert_called_once()
    displayed = render.call_args.args[0]
    pd.testing.assert_frame_equal(displayed, expected)
    assert displayed.date.max() <= cutoff
    if timeframe == 'weekly':
        assert (pd.to_datetime(displayed.date).dt.dayofweek == 4).all()


def test_chart_without_signal_or_price_levels_renders_without_mutation():
    frame = pd.DataFrame({'date': pd.bdate_range('2026-01-01', periods=10).strftime('%Y-%m-%d'),
                          'open': 10., 'high': 11., 'low': 9., 'close': 10.5, 'volume': 1000.})
    before = frame.copy(deep=True)
    image = render_chart(frame, {}, 'Chart', {})
    assert image.width > 500 and image.height > 300
    pd.testing.assert_frame_equal(frame, before)
