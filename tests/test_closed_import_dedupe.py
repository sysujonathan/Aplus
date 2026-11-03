"""Regression tests for single/multiple screenshot OCR deduplication.

No broker data or OCR model is required: sample records are synthetic.
"""
import sys
from types import SimpleNamespace

from workbench import closed_import


def _record(*, name="华阳股份", date="2026-09-29", days=9, pnl=-752.11, pct=-4.61):
    return {
        "code": "",
        "name": name,
        "close_date": date,
        "holding_days": days,
        "pnl": pnl,
        "return_pct": pct,
        "notes": "截图识别导入",
    }


def test_complete_row_wins_over_incomplete_overlap_and_exact_duplicates():
    partial = _record(days="", pnl="", pct="")
    complete = _record()
    other_trade = _record(pnl=-700)
    other_day = _record(date="2026-09-30", days="", pnl="", pct="")
    records = [partial, complete.copy(), complete.copy(), other_trade, other_day]

    assert closed_import._dedupe_records(records) == [
        complete, other_trade, other_day
    ]


def test_fingerprint_keeps_existing_two_decimal_rounding():
    records = [_record(pnl=1.001, pct=2.001), _record(pnl=1.004, pct=2.004)]
    assert closed_import._dedupe_records(records) == [records[0]]
    assert closed_import._dedupe_records([]) == []


def test_multi_screenshot_reuses_same_rule_and_keeps_warnings(monkeypatch):
    first, second = object(), object()
    complete = _record()
    partial = _record(days="", pnl="", pct="")
    monkeypatch.setitem(
        sys.modules, "rapidocr_onnxruntime",
        SimpleNamespace(RapidOCR=lambda: object()),
    )
    monkeypatch.setattr(
        closed_import, "recognize_screenshot", lambda image, engine=None: image
    )

    def fake_parse(image):
        if image is first:
            return [partial, complete], [], ["一条记录待补全"]
        assert image is second
        return [complete.copy()], [{"month": "202609", "count": 1, "pnl": -752.11}], []

    monkeypatch.setattr(closed_import, "parse_broker_tokens", fake_parse)
    records, summaries, warnings = closed_import.recognize_screenshots([first, second])

    assert records == [complete]
    assert summaries == [{"month": "202609", "count": 1, "pnl": -752.11}]
    assert warnings == ["第 1 张：一条记录待补全"]


def test_numeric_ocr_zero_substitution_stays_numeric_only():
    assert closed_import._number("1O.5") == 10.5
    assert closed_import._number("-4.O% ", percent=True) == -4.0
