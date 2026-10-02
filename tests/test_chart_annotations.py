"""策略 K 线标注契约：策略之间不能再被压成同一种星号。"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
import pandas as pd
import pytest

from core.strategy_registry import StrategyRegistry
from core.strategies.base import BaseStrategy
from gui.chart_annotations import (
    annotation_frame,
    info_panel_lines,
    marker_specs,
    owns_risk_lines,
    restyle_strategy_annotations,
    signal_context,
    trend_specs,
)
from gui.chart_panel import render_chart
from gui.theme import CONTROL_BG


def _bars(count=30):
    close = pd.Series([10 + index * .05 for index in range(count)])
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=count).strftime("%Y-%m-%d"),
        "open": close - .1,
        "high": close + .4,
        "low": close - .4,
        "close": close,
        "volume": 1000.0,
        "ema20": close - .15,
    })


def test_strategy_marker_contracts_are_distinct():
    mtr = marker_specs("MTR_MASTER", {"signal_column": "signal_mtr"})
    three_k = marker_specs("STRATEGY_3K", {"signal_column": "signal_3k"})

    assert [(item.column, item.marker) for item in mtr] == [
        ("is_sw_h_geometric", "v"),
        ("signal_mtr", "*"),
    ]
    assert [(item.column, item.marker) for item in three_k] == [
        ("signal_3k", "^"),
        ("signal_3k_gap_test", "*"),
    ]
    assert marker_specs("STRATEGY_GAP_PINBAR", {"signal_column": "signal_gap_pinbar"}) == []
    assert trend_specs("MTR_MASTER")[0].column == "geometric_trendline"
    assert owns_risk_lines("STRATEGY_GAP_H2")
    assert owns_risk_lines("STRATEGY_AWIL")
    assert not owns_risk_lines("MTR_MASTER")


def test_known_strategies_keep_a_dedicated_annotation_route():
    for strategy_type in (
        "MTR_MASTER",
        "STRATEGY_STRUCTURAL_GAP",
        "STRATEGY_GAP_PINBAR",
        "STRATEGY_GAP_H2",
        "STRATEGY_AWIL",
        "STRATEGY_MONTHLY_RANGE_BREAK",
    ):
        strategy = StrategyRegistry.get_strategy(strategy_type)
        assert type(strategy).annotate_chart is not BaseStrategy.annotate_chart, strategy_type


def test_render_chart_calls_strategy_annotation_with_integer_axis_and_anchor():
    captured = {}

    class Strategy:
        def get_signal_info(self, frame):
            return {
                "entry": 10.5,
                "sl": 9.8,
                "tp1": 11.9,
                "extra_info": {"sig_quality": .82},
                "rating": {
                    "score": 78,
                    "factors": [{"name": "趋势", "hit": True}],
                },
            }

        def annotate_chart(self, ax, frame, strategy_type, **kwargs):
            captured.update(
                frame=frame,
                strategy_type=strategy_type,
                kwargs=kwargs,
            )
            ax.annotate("专属标注", xy=(len(frame) - 1, frame.iloc[-1].low))
            return 2

    frame = _bars()
    payload = {
        "asof": frame.iloc[-1].date,
        "entry": 10.5,
        "stop": 9.8,
        "target": 11.9,
    }
    image = render_chart(
        frame,
        payload,
        "",
        {"signal_column": "custom_signal"},
        strategy=Strategy(),
        strategy_type="CUSTOM_TEST",
    )

    assert image.width > 500 and image.height > 300
    assert isinstance(captured["frame"].index, pd.RangeIndex)
    assert captured["strategy_type"] == "CUSTOM_TEST"
    assert captured["kwargs"]["anchor_signal_date"] == payload["asof"]
    assert captured["kwargs"]["sl_price"] == payload["stop"]
    assert captured["kwargs"]["tp1"] == payload["target"]


def test_info_panel_restores_plan_quality_rating_and_hit_factors():
    frame = _bars()
    frame["bars_since_breakout"] = 4
    payload = {
        "asof": frame.iloc[-1].date,
        "entry": 10,
        "stop": 9,
        "target": 12,
        "rating": {
            "score": 76,
            "factors": [
                {"name": "信号K质量", "hit": True},
                {"name": "趋势过滤", "hit": True},
                {"name": "未命中", "hit": False},
            ],
        },
    }
    lines = info_panel_lines(
        payload,
        {"extra_info": {"sig_quality": .75}},
        frame,
        open_gap_count=3,
    )

    assert "Entry 10.00 · SL 9.00 · TP1 12.00 · (2.00R)" in lines
    assert "Quality 0.75" in lines[1]
    assert "PB bars 4" in lines[1]
    assert "Rating 76" in lines[1]
    assert "前序开放缺口 3个" in lines[1]
    assert lines[2] == "命中：信号K质量 / 趋势过滤"


def test_gap_h2_h1_marker_is_rethemed_purple_in_gui_adapter():
    figure, axis = plt.subplots()
    annotation = axis.annotate(
        "H1",
        xy=(1, 1),
        xytext=(1, 2),
        color="green",
        arrowprops={"arrowstyle": "->", "color": "green"},
        bbox={"boxstyle": "round", "facecolor": "white"},
    )

    restyle_strategy_annotations(axis, "STRATEGY_GAP_H2")

    assert to_hex(annotation.get_color()).upper() == "#8E24AA"
    assert to_hex(annotation.arrow_patch.get_edgecolor()).upper() == "#8E24AA"
    assert to_hex(annotation.get_bbox_patch().get_facecolor()).upper() == CONTROL_BG
    plt.close(figure)


def test_annotation_frame_keeps_only_visible_bars_and_uses_integer_axis():
    frame = _bars(150)
    visible = annotation_frame(frame)
    assert len(visible) == 120
    assert isinstance(visible.index, pd.RangeIndex)
    assert visible.iloc[0].date == frame.iloc[-120].date


def test_signal_context_failure_keeps_diagnostic_traceback(caplog):
    class BrokenStrategy:
        def get_signal_info(self, frame):
            raise RuntimeError("标注信息计算失败")

    frame = _bars()
    anchor = frame.iloc[-1].date
    with caplog.at_level("WARNING", logger="gui.chart_annotations"):
        assert signal_context(BrokenStrategy(), frame, {"asof": anchor}) == {}
    record, = caplog.records
    assert "BrokenStrategy" in record.message and anchor in record.message
    assert record.exc_info[0] is RuntimeError
    assert "标注信息计算失败" in caplog.text


def test_missing_signal_context_is_not_a_calculation_failure(caplog):
    class NoSignal:
        def get_signal_info(self, frame):
            return None

    frame = _bars()
    assert signal_context(None, frame, {}) == {}
    assert signal_context(NoSignal(), frame, {}) == {}
    assert signal_context(NoSignal(), frame, {"asof": "2000-01-01"}) == {}
    assert not caplog.records


@pytest.mark.parametrize("anchor_key", ["asof", "setup_date"])
def test_watch_signal_context_never_receives_future_bars(anchor_key):
    frame = _bars()
    anchor = frame.iloc[-2].date
    frame.loc[frame.index[-1], ["high", "low", "close"]] = [1000., .01, 999.]
    before = frame.copy(deep=True)
    payload = {anchor_key: anchor}

    class Strategy:
        def get_signal_info(self, source):
            assert source.iloc[-1].date == anchor
            assert len(source) == len(frame) - 1
            return {"entry": float(source.iloc[-1].high)}

    info = signal_context(Strategy(), frame, payload)
    assert info["entry"] == before.iloc[-2].high
    pd.testing.assert_frame_equal(frame, before)
    assert payload == {anchor_key: anchor}


def test_watch_render_keeps_original_plan_and_quality_with_new_market(monkeypatch):
    import gui.chart_renderer as panel
    frame = _bars()
    anchor = frame.iloc[-2].date
    frame["sig_bar_quality"] = .75
    frame["bars_since_breakout"] = 4
    frame.loc[frame.index[-1], ["high", "low", "close"]] = [100., 1., 99.]
    frame.loc[frame.index[-1], ["sig_bar_quality", "bars_since_breakout"]] = [.01, 99]
    payload = {"asof": anchor, "setup_date": anchor, "entry": 10., "stop": 9., "target": 12.}
    captured = {}
    original = info_panel_lines

    class Strategy:
        def get_signal_info(self, source):
            captured["info_date"] = source.iloc[-1].date
            # Deliberately differ from the archived plan: display must prefer payload.
            return {"entry": 50., "sl": 40., "tp1": 70.}

        def annotate_chart(self, ax, visible, strategy_type, **kwargs):
            captured["market_date"] = visible.iloc[-1].date
            captured["kwargs"] = kwargs

    def collect_lines(*args, **kwargs):
        captured["lines"] = original(*args, **kwargs)
        return captured["lines"]

    monkeypatch.setattr(panel, "info_panel_lines", collect_lines)
    render_chart(frame, payload, "", {}, strategy=Strategy(), strategy_type="CUSTOM_TEST")
    assert captured["market_date"] == frame.iloc[-1].date
    assert captured["info_date"] == anchor
    assert captured["kwargs"]["anchor_signal_date"] == anchor
    assert captured["kwargs"]["sl_price"] == 9. and captured["kwargs"]["tp1"] == 12.
    assert captured["lines"][0] == "Entry 10.00 · SL 9.00 · TP1 12.00 · (2.00R)"
    assert "Quality 0.75" in captured["lines"][1]
    assert "PB bars 4" in captured["lines"][1]
