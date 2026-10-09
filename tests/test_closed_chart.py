import gc
import os
from unittest.mock import patch

import pandas as pd
import pytest

from workbench.closed_chart import closed_chart_events, load_closed_chart
from workbench.market import save_dataset
from workbench.store import Store


@pytest.fixture(autouse=True)
def collect_tk_on_main_thread():
    yield
    gc.collect()


def bars(dates=None):
    dates = dates or ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30"]
    return pd.DataFrame({"date": dates, "open": 10., "high": 11., "low": 9.,
                         "close": 10.5, "volume": 1000.})


def summary(**changes):
    return {"code": "sh.600000", "name": "测试股票", "close_date": "2026-09-29",
            "holding_days": 4, "pnl": -100, "return_pct": -2, **changes}


def test_estimate_uses_inclusive_actual_bars_and_never_invents_price_or_quantity():
    frame = bars()
    before = frame.copy(deep=True)
    events, warnings = closed_chart_events(frame, summary())
    assert [e["date"] for e in events] == ["2026-09-23", "2026-09-29"]
    assert [e["label"] for e in events] == ["推算买入", "推算卖出"]
    assert all(e["price"] == 10.5 and e["execution_price"] is None and e["quantity"] is None for e in events)
    assert "不是成交价" in warnings[0]
    # P&L/return cannot determine executions: different numbers do not move marks.
    assert events == closed_chart_events(frame, summary(pnl=999, return_pct=12))[0]
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("days", [0, 1])
def test_single_day_estimate(days):
    events, _ = closed_chart_events(bars(), summary(holding_days=days))
    assert all(e["date"] == "2026-09-29" for e in events)


@pytest.mark.parametrize("changes,message", [
    ({"holding_days": -1}, "负数"), ({"holding_days": 20}, "不足"),
    ({"close_date": "2026-09-25"}, "没有"), ({"position_id": "missing"}, "链路缺失"),
])
def test_missing_history_or_invalid_dates_do_not_shift_markers(changes, message):
    with pytest.raises(ValueError, match=message):
        closed_chart_events(bars(), summary(**changes))


def ledger(store):
    account = store.save_account("测试账户", 10000)
    position = store.save_position(account, "sh.600000", "测试股票", 10, 9, 12,
        [{"date": "2026-09-23", "price": 10, "hands": 1, "fees": 1},
         {"date": "2026-09-24", "price": 10.2, "hands": 1, "fees": 1}])
    store.sell_position(position, [{"date": "2026-09-28", "price": 10.6, "hands": 1}], 2)
    store.sell_position(position, [{"date": "2026-09-29", "price": 10.7, "hands": 1}], 2)
    return store.rows("SELECT * FROM closed_trades")[0]


def database_snapshot(store):
    tables = store.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return {t["name"]: store.rows(f'SELECT * FROM "{t["name"]}"') for t in tables}


def test_real_fills_include_every_batch_and_do_not_write_even_audit(tmp_path):
    store = Store(tmp_path)
    row = ledger(store)
    save_dataset(store, row["code"], bars(), "baostock", "不复权")
    before = database_snapshot(store)
    data = load_closed_chart(store, row)
    assert not data["estimated"] and not data["warnings"]
    assert [e["price"] for e in data["events"]] == [10, 10.2, 10.6, 10.7]
    assert [e["label"] for e in data["events"]] == ["买入", "买入", "卖出", "卖出"]
    assert [e["quantity"] for e in data["events"]] == [100]*4
    assert before == database_snapshot(store)


def test_adjusted_chart_never_uses_raw_fill_price_as_adjusted_coordinate(tmp_path):
    store = Store(tmp_path)
    row = ledger(store)
    save_dataset(store, row["code"], bars(), "baostock", "前复权")
    data = load_closed_chart(store, row)
    assert all(e["price"] == 10.5 for e in data["events"])
    assert data["events"][0]["execution_price"] == 10
    assert "复权行情" in data["warnings"][0]


def test_missing_fill_bar_keeps_real_date_and_missing_price(tmp_path):
    store = Store(tmp_path)
    row = ledger(store)
    frame = bars().query("date != '2026-09-24'").reset_index(drop=True)
    save_dataset(store, row["code"], frame, "baostock", "不复权")
    data = load_closed_chart(store, row)
    assert data["events"][1]["date"] == "2026-09-24"
    assert data["events"][1]["price"] is None
    assert "不移动" in data["warnings"][0]


def test_wrong_account_linkage_and_summary_of_same_code_not_using_another_position(tmp_path):
    store = Store(tmp_path)
    row = ledger(store)
    save_dataset(store, row["code"], bars(), "baostock", "不复权")
    with pytest.raises(ValueError, match="关联不一致"):
        load_closed_chart(store, {**row, "account_id": "other"})
    assert load_closed_chart(store, summary())["estimated"]


def test_snapshot_selection_supports_csv_etf_history_and_checks_integrity(tmp_path):
    store = Store(tmp_path)
    save_dataset(store, "159570", bars().iloc[3:], "csv", "前复权")
    long = save_dataset(store, "159570", bars(), "csv", "前复权")
    data = load_closed_chart(store, summary(code="159570"))
    assert data["dataset"]["id"] == long and data["code"] == "sz.159570"
    (store.root / data["dataset"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验"):
        load_closed_chart(store, summary(code="159570"))
    with pytest.raises(ValueError, match="本地没有"):
        load_closed_chart(store, summary(code="600001"))


def test_renderer_reuses_existing_chart_navigation_and_does_not_mutate(tmp_path):
    from gui.closed_trade_chart import render_closed_chart
    store = Store(tmp_path)
    save_dataset(store, "600000", bars(), "csv", "前复权")
    data = load_closed_chart(store, summary())
    before = database_snapshot(store)
    from matplotlib.axes import Axes
    with patch.object(Axes, "annotate", autospec=True, wraps=Axes.annotate) as annotate:
        image = render_closed_chart(data, size=(1000, 550))
    labels = [call.args[1] for call in annotate.call_args_list]
    assert "推算买入" in labels and "推算卖出" in labels
    assert image.size == (1000, 550)
    assert image.info["replay_view"]["total"] == len(bars())
    assert before == database_snapshot(store)


def test_same_day_batches_group_chart_labels_but_preserve_individual_records():
    from gui.closed_trade_chart import render_closed_chart
    from unittest.mock import Mock
    events = [{"date": "2026-09-23", "kind": "fill", "label": "买入", "price": 10, "quantity": 100},
              {"date": "2026-09-23", "kind": "fill", "label": "买入", "price": 11, "quantity": 200}]
    data = {"frame": bars(), "code": "sh.600000", "name": "测试", "events": events}
    with patch("gui.closed_trade_chart.render_chart", return_value=Mock()) as render:
        render_closed_chart(data)
    marks = render.call_args.kwargs["replay_events"]
    assert len(marks) == 1 and marks[0]["label"] == "买入（2笔）"
    assert marks[0]["price"] == pytest.approx(32/3)
    assert len(data["events"]) == 2 and data["events"][0]["label"] == "买入"


def test_closed_marker_text_and_offsets_grow_with_window_without_moving_trade():
    from gui.closed_trade_chart import render_closed_chart, closed_label_scale
    from matplotlib.axes import Axes
    data = {"frame": bars(), "code": "sh.600000", "name": "测试", "events":
            closed_chart_events(bars(), summary())[0]}
    original = Axes.annotate
    calls = []
    def capture(axis, *args, **kwargs):
        calls.append(kwargs)
        return original(axis, *args, **kwargs)
    with patch.object(Axes, "annotate", new=capture):
        small = render_closed_chart(data, size=(1100, 550))
        small_marks = list(calls)
        calls.clear()
        large = render_closed_chart(data, size=(2200, 1100))
        large_marks = list(calls)
    assert small.size == (1100, 550) and large.size == (2200, 1100)
    assert len(small_marks) == len(large_marks) == 2
    ratio = closed_label_scale((2200, 1100)) / closed_label_scale((1100, 550))
    for small_mark, large_mark in zip(small_marks, large_marks):
        assert large_mark["fontsize"] > small_mark["fontsize"]
        assert large_mark["fontsize"] == pytest.approx(small_mark["fontsize"] * ratio)
        assert large_mark["xy"] == small_mark["xy"]
        assert large_mark["xytext"] == pytest.approx(tuple(v * ratio for v in small_mark["xytext"]))
    assert closed_label_scale((720, 300)) == closed_label_scale(None) == 1.25
    assert closed_label_scale((8000, 6000)) == 2.75


def test_research_chart_default_marker_style_is_unchanged():
    from gui.chart_renderer import render_chart
    from matplotlib.axes import Axes
    events = [{"date": "2026-09-23", "price": 10.5, "kind": "fill", "label": "Fill"}]
    original = Axes.annotate
    calls = []
    def capture(axis, *args, **kwargs):
        calls.append(kwargs)
        return original(axis, *args, **kwargs)
    with patch.object(Axes, "annotate", new=capture):
        default = render_chart(bars(), {}, "", {}, replay_events=events, size=(1100, 550))
        explicit = render_chart(bars(), {}, "", {}, replay_events=events, size=(1100, 550),
                                replay_label_scale=1.0)
    assert all(call["fontsize"] == 8 for call in calls)
    assert default.tobytes() == explicit.tobytes()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows Tk viewer")
def test_edit_popup_chart_and_automatic_archive_double_click_are_read_only(tmp_path):
    # Production has one persistent Tk root. Repeated in-process Tcl lifetimes
    # in the full suite can fail to load auto.tcl despite the file being present.
    # Run every existing interaction/style/read-only assertion in a fresh process.
    import subprocess
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-c',
        'from pathlib import Path; from tests.test_closed_chart import check_native_closed_viewer; '
        f'check_native_closed_viewer(Path({str(tmp_path)!r}))'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def check_native_closed_viewer(tmp_path):
    import ttkbootstrap as ttk
    from gui.trade_management import _ClosedDialog, TradeManagementFrame
    from gui.closed_trade_chart import ClosedTradeChartDialog
    store = Store(tmp_path)
    save_dataset(store, "600000", bars(), "csv", "前复权")
    before = database_snapshot(store)
    root = ttk.Window(themename="darkly")
    try:
        dialog = _ClosedDialog(root, store, {}, summary())
        assert dialog.chart_button.cget("text") == "查看 K 线买卖点"
        # Simulate closing the viewer inside the modal wait, then keep editing.
        def close_chart(_top):
            viewer = dialog.top.winfo_children()[-1]
            viewer.update()
            viewer.destroy()
        with patch.object(dialog.top, "wait_window", side_effect=close_chart):
            dialog.chart_button.invoke()
        assert not dialog.confirmed and dialog.top.grab_current() is dialog.top
        assert database_snapshot(store) == before
        dialog.top.destroy()
        chart = ClosedTradeChartDialog(root, store, summary())
        root.update()
        assert tuple(map(int, chart.top.resizable())) == (1, 1)
        assert not chart.top.transient()
        # Verify the real Windows title-bar style, not just Tk's resize flag.
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        hwnd = user32.GetParent(ctypes.c_void_p(chart.top.winfo_id()))
        style = user32.GetWindowLongW(ctypes.c_void_p(hwnd), -16)
        assert style & 0x00010000  # WS_MAXIMIZEBOX
        assert style & 0x00040000  # WS_THICKFRAME
        chart.draw()
        assert chart._photo is not None
        chart.top.state("zoomed")
        root.update()
        assert chart.top.state() == "zoomed"
        chart.draw()
        assert chart._photo.width() == chart.chart.winfo_width()
        assert chart._photo.height() == chart.chart.winfo_height()
        chart.top.state("normal")
        chart.top.geometry("1000x650")
        root.update()
        chart.draw()
        assert chart._photo.width() == chart.chart.winfo_width()
        assert chart._photo.height() == chart.chart.winfo_height()
        assert chart.chart.bind("<MouseWheel>") and chart.chart.bind("<B1-Motion>")
        chart.events_tree.selection_set("0")
        chart._locate()
        chart.navigation.offset = 99
        chart.reset_view()
        assert chart.navigation.viewport == chart._initial_viewport
        chart.close()
        root.update()
        assert database_snapshot(store) == before
        from unittest.mock import Mock
        page = Mock()
        row = {**summary(), "position_id": "real"}
        page._selected_closed.return_value = row
        TradeManagementFrame._edit_closed(page)
        page._view_closed_chart.assert_called_once_with(row)
        page._open_closed_dialog.assert_not_called()
    finally:
        root.destroy()
