"""Desktop historical charts must honor the selected observation and timeframe."""
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from gui.chart_panel import ChartPanel, render_chart
from gui.theme import CHART_BG
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
    corner = image.convert("RGB").getpixel((0, 0))
    expected = tuple(int(CHART_BG[i:i + 2], 16) for i in (1, 3, 5))
    assert max(abs(a - b) for a, b in zip(corner, expected)) <= 2
    pd.testing.assert_frame_equal(frame, before)


def test_watch_chart_uses_latest_market_but_keeps_anchor_payload(tmp_path):
    store = Store(tmp_path / "isolated-watch")
    count = 950
    close = 20 + np.sin(np.arange(count) / 10)
    latest = pd.DataFrame({
        "date": pd.bdate_range("2022-01-01", periods=count).strftime("%Y-%m-%d"),
        "open": close - .1, "high": close + .5, "low": close - .5,
        "close": close, "volume": 1000.,
    })
    anchor_cutoff = latest.date.iloc[850]
    market_cutoff = latest.date.iloc[-1]
    anchor = latest.iloc[:851].copy()
    strategy = "STRATEGY_GAP_H2"
    spec = catalog(store)[strategy]
    payload = {"entry": 18.5, "stop": 17.2, "target": 21.0}
    fake_store = Mock()
    fake_store.rows.return_value = [{
        "code": "sh.600000", "strategy": strategy, "timeframe": "daily",
        "asof": anchor_cutoff, "setup_date": anchor_cutoff,
    }]
    panel = Mock()
    with patch("gui.data.load_observation_candles", return_value=(anchor, {}, payload)), \
         patch("workbench.market.load_dataset", return_value=(latest, {"end": market_cutoff})), \
         patch("gui.data.code_names", return_value={"sh.600000": "浦发银行"}), \
         patch("workbench.strategies.catalog", return_value={strategy: spec}), \
         patch("gui.chart_panel.render_chart", return_value=Mock()) as render:
        ChartPanel.show_observation(
            panel,
            fake_store,
            "anchor-observation",
            market_dataset_id="latest-market",
            mode="watch",
            market_asof=market_cutoff,
            anchor_asof=anchor_cutoff,
        )

    displayed, rendered_payload = render.call_args.args[:2]
    assert displayed.date.max() == market_cutoff
    assert rendered_payload == {
        **payload,
        "asof": anchor_cutoff,
        "setup_date": anchor_cutoff,
    }
    title = panel._title_var.set.call_args_list[-1].args[0]
    assert f"行情 {market_cutoff}" in title
    assert f"信号 {anchor_cutoff}" in title
    assert "计划线来自信号日" in title


def test_each_chart_slot_opens_its_own_tradingview_symbol():
    panel = Mock(_code="sz.002011", _tf="daily")
    with patch("gui.tv.tv_link", return_value="https://example.test/chart") as link, \
         patch("webbrowser.open") as open_browser:
        ChartPanel._open_tv(panel)
    link.assert_called_once_with("sz.002011", "daily")
    open_browser.assert_called_once_with("https://example.test/chart")
