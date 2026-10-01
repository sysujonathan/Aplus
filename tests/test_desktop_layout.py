from gui.candidate_tabs import candidate_repeat_counts, short_strategy_label
from gui.toolbar import format_board_scope


def test_candidate_labels_fit_horizontal_strategy_bar():
    assert short_strategy_label("MTR Master") == "MTR"
    assert short_strategy_label("GAP PINBAR") == "GAP PB"
    assert short_strategy_label("STRATEGY_GAP_H2") == "GAP H2"


def test_repeated_candidates_are_counted_within_current_filter():
    rows = [
        {"code": "sz.002357"},
        {"code": "sz.002102"},
        {"code": "sz.002357"},
    ]
    counts = candidate_repeat_counts(rows)
    assert counts["sz.002357"] == 2
    assert counts["sz.002102"] == 1


def test_board_scope_label_is_compact_but_unambiguous():
    assert format_board_scope(["沪深主板", "创业板"]) == "范围：主板+创业"
    assert format_board_scope(["沪深主板", "创业板", "科创板", "北交所"]) == "范围：全市场"
    assert format_board_scope([]) == "范围：未选择"
