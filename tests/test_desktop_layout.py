import os
import uuid

import pytest

from gui.candidate_tabs import (
    candidate_repeat_counts,
    collapse_candidate_rows,
    short_strategy_label,
)
from gui.toolbar import format_board_scope
from launch_dashboard import acquire_single_instance, release_single_instance


def test_candidate_labels_fit_horizontal_strategy_bar():
    assert short_strategy_label("MTR Master") == "MTR"
    assert short_strategy_label("GAP PINBAR") == "G-PB"
    assert short_strategy_label("STRATEGY_GAP_H2") == "G-H2"


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


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex")
def test_desktop_launcher_rejects_second_instance_without_opening_backend():
    name = "Local\\AplusTest-" + uuid.uuid4().hex
    first = acquire_single_instance(notify=False, name=name)
    try:
        assert first
        assert acquire_single_instance(notify=False, name=name) is None
    finally:
        release_single_instance(first)
