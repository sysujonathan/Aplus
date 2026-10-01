import os
import uuid
from unittest.mock import Mock

import pytest

from gui.candidate_tabs import (
    candidate_repeat_counts,
    collapse_candidate_rows,
    short_strategy_label,
    strategy_window,
)
from gui.toolbar import ToolBar, format_board_scope
from gui.chart_panel import layout_shape, page_start_for
from gui.chart_items import ChartItem, chart_items
from gui.data import REALTIME_MARKET_SOURCE
from gui.main_window import AplusMainWindow, side_panel_widths
from launch_dashboard import acquire_single_instance, release_single_instance


def test_candidate_labels_fit_horizontal_strategy_bar():
    assert short_strategy_label("MTR Master") == "MTR"
    assert short_strategy_label("GAP PINBAR") == "GPb"
    assert short_strategy_label("STRATEGY_GAP_H1") == "GH1"
    assert short_strategy_label("STRATEGY_GAP_H2") == "GH2"


def test_strategy_bar_shows_four_groups_and_keeps_last_window_full():
    keys = ["MTR", "3K", "GH1", "GPb", "GH2", "AIL"]
    assert strategy_window(keys, 0) == (["MTR", "3K", "GH1", "GPb"], 0)
    assert strategy_window(keys, 4) == (["GH1", "GPb", "GH2", "AIL"], 2)
    assert strategy_window(keys, -4) == (["MTR", "3K", "GH1", "GPb"], 0)


def test_repeated_candidates_are_counted_within_current_filter():
    rows = [
        {"code": "sz.002357", "observation_id": "new"},
        {"code": "sz.002102", "observation_id": "only"},
        {"code": "sz.002357", "observation_id": "old"},
    ]
    counts = candidate_repeat_counts(rows)
    assert counts["sz.002357"] == 2
    assert counts["sz.002102"] == 1
    collapsed = collapse_candidate_rows(rows)
    assert [row["code"] for row in collapsed] == ["sz.002357", "sz.002102"]
    assert collapsed[0]["observation_id"] == "new"
    assert collapsed[0]["repeat_count"] == 2


def test_board_scope_label_is_compact_but_unambiguous():
    assert format_board_scope(["沪深主板", "创业板"]) == "范围：主板+创业"
    assert format_board_scope(["沪深主板", "创业板", "科创板", "北交所"]) == "范围：全市场"
    assert format_board_scope([]) == "范围：未选择"


def test_multichart_layouts_and_candidate_pages_are_stable():
    assert layout_shape(1) == (1, 1)
    assert layout_shape(4) == (2, 2)
    assert layout_shape(6) == (2, 3)
    assert layout_shape(9) == (3, 3)
    assert page_start_for(0, 4) == 0
    assert page_start_for(3, 4) == 0
    assert page_start_for(4, 4) == 4
    assert page_start_for(17, 9) == 9
    assert page_start_for(17, 1) == 17


def test_single_chart_toolbar_option_and_compact_sidebars():
    toolbar = Mock()
    toolbar.layout_var.get.return_value = "1×1"
    ToolBar._fire_layout(toolbar)
    toolbar._on_chart_layout.assert_called_once_with(1)

    toolbar.layout_var.get.return_value = "1✖1"
    ToolBar._fire_layout(toolbar)
    assert toolbar._on_chart_layout.call_args.args == (1,)

    toolbar._chart_source_label = "关注"
    ToolBar.set_chart_page_status(toolbar, 10, 10, 53)
    toolbar.chart_page_var.set.assert_called_once_with("关注 10 / 53")

    narrow = side_panel_widths(1280)
    wide = side_panel_widths(2880)
    assert narrow == (238, 222)
    assert wide == (260, 242)
    assert sum(wide) < 520


def test_watch_selection_switches_chart_paging_to_watchlist():
    window = Mock(store=object())
    window.watch.rows.return_value = [
        {"code": "sz.003006", "observation_id": "legacy-observation"},
        {"code": "sz.002912", "observation_id": "next-observation"},
    ]
    window._chart_mode = "candidates"
    AplusMainWindow.on_watch_selected(window, "sz.003006", "legacy-observation")
    window.chart.set_items.assert_called_once_with(
        chart_items(window.watch.rows.return_value, "watch"), selected_index=0
    )
    assert window._chart_mode == "watch"
    window.toolbar.set_chart_source.assert_called_once_with("关注")
    window._status_text.set.assert_called_once_with(
        "关注浏览：sz.003006（左右键切换关注列表）"
    )


def test_candidate_selection_exits_watch_paging():
    window = Mock(store=object())
    window._chart_mode = "watch"
    rows = [
        {"code": "sh.600017", "observation_id": "first"},
        {"code": "sh.600026", "observation_id": "selected"},
    ]
    window.candidates.rows.return_value = rows
    AplusMainWindow.on_stock_selected(window, "sh.600026", "selected")
    window.chart.set_items.assert_called_once_with(
        chart_items(rows, "candidate"), selected_index=1
    )
    assert window._chart_mode == "candidates"
    window.toolbar.set_chart_source.assert_called_once_with("策略")


def test_toolbar_starts_from_latest_aplus_signal_date_only():
    toolbar = Mock()
    toolbar.store.rows.return_value = [{"day": "2026-09-30"}]
    toolbar._tf_var.get.return_value = "daily"

    ToolBar._select_latest_date(toolbar)

    query, args = toolbar.store.rows.call_args.args
    assert "d.source=?" in query
    assert args == (REALTIME_MARKET_SOURCE, "daily")
    toolbar.year_var.set.assert_called_once_with("2026")
    toolbar.month_var.set.assert_called_once_with("09")
    toolbar.day_var.set.assert_called_once_with("30")


def test_chart_items_keep_mode_and_source_explicit():
    items = chart_items(
        [{
            "code": "sz.003006",
            "name": "百亚股份",
            "observation_id": "legacy-observation",
            "source": "legacy-engine-a",
            "strategy": "MTR_MASTER",
            "timeframe": "daily",
        }],
        "watch",
    )
    assert items == [
        ChartItem(
            code="sz.003006",
            name="百亚股份",
            observation_id="legacy-observation",
            source="legacy-engine-a",
            mode="watch",
            strategy="MTR_MASTER",
            timeframe="daily",
        )
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex")
def test_desktop_launcher_rejects_second_instance_without_opening_backend():
    name = "Local\\AplusTest-" + uuid.uuid4().hex
    first = acquire_single_instance(notify=False, name=name)
    try:
        assert first
        assert acquire_single_instance(notify=False, name=name) is None
    finally:
        release_single_instance(first)
