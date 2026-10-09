import os
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gui.candidate_tabs import (
    candidate_repeat_counts,
    collapse_candidate_rows,
    short_strategy_label,
    strategy_window,
)
from gui.toolbar import (
    ToolBar,
    format_board_scope,
    format_data_chain_status,
    format_elapsed,
    format_header_data_status,
    format_scope_readiness,
    format_strategy_scope,
    format_task_timings,
    sync_start_date,
)
from gui.chart_panel import layout_shape, page_start_for
from gui.chart_items import ChartItem, chart_items
from gui.main_window import AplusMainWindow, right_list_width
from gui.tree_scroll import identity_column_widths, numeric_stock_code, wheel_scroll_units
from launch_dashboard import acquire_single_instance, release_single_instance


ROOT = Path(__file__).resolve().parents[1]


class _ValueVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_candidate_labels_fit_horizontal_strategy_bar():
    assert short_strategy_label("MTR Master") == "MTR"
    assert short_strategy_label("GAP PINBAR") == "GPb"
    assert short_strategy_label("STRATEGY_GAP_H1") == "GH1"
    assert short_strategy_label("STRATEGY_GAP_H2") == "GH2"


def test_strategy_bar_shows_all_six_groups_without_paging():
    keys = ["MTR", "3K", "GH1", "GPb", "GH2", "AIL"]
    assert strategy_window(keys, 0) == (keys, 0)
    assert strategy_window(keys, 4) == (keys, 0)


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


def test_strategy_scope_label_distinguishes_all_custom_and_empty():
    available = ["MTR_MASTER", "STRATEGY_3K", "STRATEGY_GAP_H2"]
    assert format_strategy_scope(available, available) == "策略：全部"
    assert format_strategy_scope(available[:2], available) == "策略：2/3"
    assert format_strategy_scope([], available) == "策略：未选择"


def test_selected_strategies_follow_registry_order():
    toolbar = object.__new__(ToolBar)
    toolbar._available_strategies = ["MTR_MASTER", "STRATEGY_3K", "STRATEGY_GAP_H2"]
    toolbar.strategy_vars = {
        "MTR_MASTER": _ValueVar(True),
        "STRATEGY_3K": _ValueVar(False),
        "STRATEGY_GAP_H2": _ValueVar(True),
    }

    assert ToolBar._selected_strategies(toolbar) == ["MTR_MASTER", "STRATEGY_GAP_H2"]


def test_completed_auto_market_update_chains_strategy_scan_once():
    toolbar = Mock()
    toolbar._job_id = "job-sync"
    toolbar._job_kind = "更新行情"
    toolbar._auto_scan_after_sync = True
    toolbar._auto_chain_cancelled = False
    toolbar._on_job_finished = None
    toolbar.data_chain_summary.return_value = "行情最新 2026-10-03 ✓"

    ToolBar._finish_job(toolbar, "completed", result_json="{}")

    toolbar.after_idle.assert_called_once_with(toolbar._start_auto_scan)
    assert toolbar._job_id is None


def test_incomplete_market_update_never_chains_strategy_scan():
    for status in ("partial", "failed", "cancelled"):
        toolbar = Mock()
        toolbar._job_id = "job-sync"
        toolbar._job_kind = "更新行情"
        toolbar._auto_scan_after_sync = True
        toolbar._auto_chain_cancelled = False
        toolbar._on_job_finished = None

        ToolBar._finish_job(toolbar, status, message="未完整完成", result_json="{}")

        toolbar.after_idle.assert_not_called()
        assert toolbar._auto_scan_after_sync is False


def test_partial_tickflow_auto_update_chains_only_explicitly_ready_scope():
    import json
    for allowed in (False, True):
        toolbar = Mock()
        toolbar._job_id = 'job-sync'
        toolbar._job_kind = '更新行情'
        toolbar._auto_scan_after_sync = True
        toolbar._auto_chain_cancelled = False
        toolbar._on_job_finished = None
        ToolBar._finish_job(toolbar, 'partial', result_json=json.dumps(
            dict(source='tickflow', scan_readiness=dict(scan_allowed=allowed))))
        if allowed:
            toolbar.after_idle.assert_called_once_with(toolbar._start_auto_scan)
        else:
            toolbar.after_idle.assert_not_called()


def test_stop_cancels_auto_scan_handoff_even_when_market_job_just_completed():
    toolbar = Mock()
    toolbar._job_id = "job-sync"
    toolbar._job_kind = "更新行情"
    toolbar._auto_scan_after_sync = True
    toolbar._auto_chain_cancelled = False
    toolbar._on_job_finished = None
    toolbar.data_chain_summary.return_value = "行情最新 2026-10-03 ✓"

    ToolBar._on_stop(toolbar)

    toolbar.service.cancel.assert_called_once_with("job-sync")
    assert toolbar._auto_chain_cancelled is True
    assert toolbar._auto_scan_after_sync is False

    ToolBar._finish_job(toolbar, "completed", result_json="{}")
    toolbar.after_idle.assert_not_called()


def test_queued_auto_scan_handoff_rechecks_stop_request():
    toolbar = Mock()
    toolbar._auto_chain_cancelled = True
    toolbar._auto_scan_after_sync = True
    toolbar._auto_started_at = 100.0
    toolbar._auto_sync_elapsed = 3.0

    ToolBar._start_auto_scan(toolbar)

    toolbar._on_scan.assert_not_called()
    assert toolbar._auto_scan_after_sync is False
    assert toolbar._auto_started_at is None
    assert toolbar._auto_sync_elapsed is None
    assert "未继续策略扫描" in toolbar.set_status.call_args.args[0]


def test_right_sidebar_reserves_twice_the_height_for_watchlist():
    source = (ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "self.list_pane.rowconfigure(0, weight=1)" in source
    assert "self.list_pane.rowconfigure(2, weight=2)" in source


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


def test_single_chart_toolbar_option_and_wider_right_list_pane():
    toolbar = Mock()
    toolbar.layout_var.get.return_value = "1×1"
    ToolBar._fire_layout(toolbar)
    toolbar._on_chart_layout.assert_called_once_with(1)

    toolbar.layout_var.get.return_value = "1✖1"
    ToolBar._fire_layout(toolbar)
    assert toolbar._on_chart_layout.call_args.args == (1,)

    ToolBar.set_chart_page_status(toolbar, 10, 10, 53)
    toolbar.chart_page_var.set.assert_not_called()

    assert right_list_width(1280) == 330
    assert right_list_width(1600) == 360
    assert right_list_width(2880) == 390


def test_list_mousewheel_scrolls_only_the_hovered_tree():
    assert wheel_scroll_units(Mock(delta=120, num=None)) == -3
    assert wheel_scroll_units(Mock(delta=-120, num=None)) == 3
    assert wheel_scroll_units(Mock(delta=0, num=4)) == -3
    assert wheel_scroll_units(Mock(delta=0, num=5)) == 3


def test_identity_columns_fill_available_width_without_clipping_keys():
    assert identity_column_widths(314) == (44, 96, 174)
    assert identity_column_widths(344) == (45, 103, 196)
    assert sum(identity_column_widths(374)) == 374
    assert identity_column_widths(374)[2] > identity_column_widths(314)[2]


def test_list_codes_hide_exchange_prefix_without_changing_unknown_values():
    assert numeric_stock_code("sh.600000") == "600000"
    assert numeric_stock_code("sz.300001") == "300001"
    assert numeric_stock_code("bj.920001") == "920001"
    assert numeric_stock_code("600000") == "600000"
    assert numeric_stock_code("custom.code") == "custom.code"


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


def test_toolbar_starts_from_latest_local_signal_date():
    toolbar = Mock()
    toolbar._tf_var.get.return_value = "daily"

    with patch("gui.data.latest_candidate_date", return_value="2026-09-30") as latest:
        ToolBar._select_latest_date(toolbar)

    latest.assert_called_once_with(toolbar.store, "daily")
    toolbar.year_var.set.assert_called_once_with("2026")
    toolbar.month_var.set.assert_called_once_with("09")
    toolbar.day_var.set.assert_called_once_with("30")


def test_date_options_stay_on_real_dates_for_selected_month():
    toolbar = object.__new__(ToolBar)
    toolbar.store = object()
    toolbar._tf_var = _ValueVar("daily")
    toolbar.year_var = _ValueVar("2026")
    toolbar.month_var = _ValueVar("09")
    toolbar.day_var = _ValueVar("30")
    toolbar.year_combo = {}
    toolbar.month_combo = {}
    toolbar.day_combo = {}

    with patch(
        "gui.data.candidate_dates",
        return_value=[
            "2026-09-30",
            "2026-09-29",
            "2026-08-31",
            "2026-09-31",  # 防御脏数据：九月不存在 31 日。
            "2026-02-29",  # 2026 不是闰年。
        ],
    ):
        ToolBar._load_date_options(toolbar)

    assert toolbar.day_combo["values"] == ["全部", "29", "30"]
    assert toolbar.day_var.get() == "30"
    assert ToolBar._parse_asof("2026-09-31") is None

    # 即使年份选“全部”，月份为九月时也不能混入八月的 31 日。
    toolbar.year_var.set("全部")
    toolbar.month_var.set("09")
    ToolBar._refresh_days(toolbar)
    assert toolbar.day_combo["values"] == ["全部", "29", "30"]


def test_job_refresh_keeps_traders_selected_date():
    window = Mock(store=object())
    window.toolbar.selected_date.return_value = ("2026", "09", "29")
    window._tf_var.get.return_value = "daily"
    window._source_for_current_date.return_value = "legacy-engine-a"

    AplusMainWindow._on_job_finished(window, "更新行情", "completed")

    window.toolbar._load_date_options.assert_called_once_with()
    window.toolbar._select_latest_date.assert_not_called()
    assert window._cur_date == ("2026", "09", "29")
    window.candidates.load_from_store.assert_called_once_with(
        window.store,
        timeframe="daily",
        asof_filter=("2026", "09", "29"),
        source="legacy-engine-a",
    )


def test_data_chain_status_marks_market_ahead_of_scan():
    assert format_data_chain_status("2026-09-30", "2026-09-29") == (
        "行情最新 2026-09-30 ✓ · 信号最新 2026-09-29 ⚠ 待扫描"
    )
    readiness = "主板：可扫描 3188/应有 3197 · 停牌 9 · 缺口 0"
    assert format_data_chain_status("2026-09-30", "2026-09-30", readiness) == (
        "行情最新 2026-09-30 ✓ · 信号最新 2026-09-30 ✓ · " + readiness
    )
    assert format_data_chain_status(None, None) == "行情最新 无 — · 信号最新 无 —"


def test_scope_readiness_explains_selected_range_in_trader_terms():
    audit = {"expected": 3197, "ready": 3188, "suspended": 9, "gaps": []}
    assert format_scope_readiness(["沪深主板"], audit) == (
        "主板：可扫描 3188/应有 3197 · 停牌 9 · 缺口 0"
    )
    assert format_scope_readiness(["沪深主板", "创业板"], {}) == "主板+创业：范围待核验"


@pytest.mark.parametrize("seconds,label", [
    (8.4, "8秒"),
    (65, "1分05秒"),
    (3723, "1小时02分03秒"),
])
def test_elapsed_time_is_compact_and_readable(seconds, label):
    assert format_elapsed(seconds) == label


def test_header_keeps_versions_and_compact_scope_visible():
    assert format_header_data_status(
        "2026-09-30",
        "2026-09-29",
        ["沪深主板"],
        {"expected": 3197, "ready": 3188},
    ) == "行情最新 2026-09-30 ✓ · 信号最新 2026-09-29 ⚠待扫描 · 主板 3188/3197"


def test_task_timings_keep_sync_and_scan_separate_after_completion():
    assert format_task_timings(65, 38) == "行情用时 1分05秒 · 扫描用时 38秒"
    assert format_task_timings(65, None, "扫描策略", 7) == (
        "行情用时 1分05秒 · 扫描用时 进行中 7秒"
    )


def test_toolbar_status_panel_is_not_hidden_by_responsive_layout():
    toolbar = object.__new__(ToolBar)
    toolbar.header = Mock()
    toolbar.actions = Mock()
    toolbar.filters = Mock()
    toolbar.status_panel = Mock()
    toolbar.status_label = Mock()
    toolbar.timing_label = Mock()
    toolbar._status_required_width = Mock(return_value=500)
    toolbar.header.winfo_reqwidth.return_value = 100
    toolbar.actions.winfo_reqwidth.return_value = 700
    toolbar.filters.winfo_reqwidth.return_value = 400
    toolbar.status_panel.winfo_reqwidth.return_value = 500

    for width in (1600, 1280):
        ToolBar._responsive(
            toolbar, type("Event", (), {"widget": toolbar, "width": width})()
        )

    assert toolbar.status_panel.grid.call_count == 2
    toolbar.status_panel.grid_remove.assert_not_called()


def test_finished_job_writes_elapsed_time_to_bottom_status():
    toolbar = Mock()
    toolbar._job_id = "job-scan"
    toolbar._job_kind = "扫描策略"
    toolbar._job_started_at = 100.0
    toolbar._auto_started_at = None
    toolbar._auto_scan_after_sync = False
    toolbar._on_job_finished = None
    toolbar.data_chain_summary.return_value = "行情最新 2026-10-03 ✓"

    with patch("gui.toolbar.time.monotonic", return_value=165.0):
        ToolBar._finish_job(toolbar, "completed", result_json='{"signals": 2, "success": 6, "reused": 0}')

    assert "用时 1分05秒" in toolbar.set_status.call_args.args[0]
    assert toolbar._scan_elapsed == 65.0
    toolbar._refresh_timing_status.assert_called_once_with()


def test_first_market_update_builds_full_history_then_uses_incremental_window():
    empty = Mock()
    empty.rows.return_value = [{"count": 0, "latest_start": None}]
    assert sync_start_date(empty) == "2016-01-01"

    partial = Mock()
    partial.rows.return_value = [{"count": 5211, "latest_start": "2024-10-01"}]
    assert sync_start_date(partial) == "2016-01-01"

    existing = Mock()
    existing.rows.return_value = [{"count": 5211, "latest_start": "2016-01-01"}]
    with patch("gui.toolbar._two_years_ago", return_value="2024-10-01"):
        assert sync_start_date(existing) == "2024-10-01"


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


def test_watch_chart_item_carries_latest_market_and_original_signal_dates():
    item = chart_items(
        [{
            "code": "sz.003006",
            "name": "百亚股份",
            "observation_id": "anchor-observation",
            "source": "baostock",
            "strategy": "MTR_MASTER",
            "timeframe": "daily",
            "market_dataset_id": "latest-market",
            "market_asof": "2026-09-30",
            "anchor_asof": "2026-09-26",
        }],
        "watch",
    )[0]
    assert item.market_dataset_id == "latest-market"
    assert item.market_asof == "2026-09-30"
    assert item.anchor_asof == "2026-09-26"


def test_windows_launchers_forward_persisted_runtime_home():
    vbs = (ROOT / "启动A.vbs").read_text(encoding="utf-8")
    cmd = (ROOT / "启动A.cmd").read_text(encoding="utf-8")

    for launcher in (vbs, cmd):
        assert "A_WORKBENCH_HOME" in launcher
        assert "HKCU\\Environment" in launcher


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex")
def test_desktop_launcher_rejects_second_instance_without_opening_backend():
    name = "Local\\AplusTest-" + uuid.uuid4().hex
    first = acquire_single_instance(notify=False, name=name)
    try:
        assert first
        assert acquire_single_instance(notify=False, name=name) is None
    finally:
        release_single_instance(first)
