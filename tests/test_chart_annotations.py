"""策略 K 线标注契约：策略之间不能再被压成同一种星号。"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
import pandas as pd

from core.strategy_registry import StrategyRegistry
from core.strategies.base import BaseStrategy
from gui.chart_annotations import (
    annotation_frame,
    info_panel_lines,
    marker_specs,
    owns_risk_lines,
    restyle_strategy_annotations,
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
