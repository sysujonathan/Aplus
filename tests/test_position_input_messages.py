"""Missing executions are explained without Python conversion errors."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import trade_management as ui


def variable(value):
    return SimpleNamespace(get=lambda: value)


def make_dialog(monkeypatch):
    dialog = object.__new__(ui._PositionDialog)
    for field, value in (("name", "测试股票"), ("entry_price", "10"),
                         ("stop", "9"), ("tp1", "12"), ("tp2", ""), ("tp3", "")):
        setattr(dialog, field, variable(value))
    dialog.plan_id = "plan"
    dialog.observation_id = "observation"
    dialog.top = Mock()
    dialog.confirmed = False
    dialog.values = None
    dialog.batches = SimpleNamespace(rows=[{
        key: variable(value) for key, value in
        (("date", "2026-10-09"), ("price", "10.1"), ("hands", "2"), ("fees", ""))
    }])
    monkeypatch.setattr(ui._IdentityDialog, "_resolve_name", lambda self: "sh.600000")
    popup = Mock()
    monkeypatch.setattr(ui.messagebox, "showerror", popup)
    return dialog, popup


@pytest.mark.parametrize("field", ["date", "price", "hands"])
def test_missing_actual_buy_names_required_fields_and_keeps_dialog(monkeypatch, field):
    dialog, popup = make_dialog(monkeypatch)
    dialog.batches.rows[0][field] = variable(" ")
    dialog._ok()
    assert not dialog.confirmed and dialog.values is None
    dialog.top.destroy.assert_not_called()
    message = popup.call_args.args[1]
    assert "下方第1笔实际买入" in message
    assert "日期、成交价格和手数" in message
    assert "交易计划不能代替实际成交" in message
    assert "float" not in message


@pytest.mark.parametrize("field,text,label", [
    ("entry_price", "", "买点 Entry"), ("entry_price", "abc", "买点 Entry"),
    ("stop", "", "止损 SL1"), ("tp1", "", "TP1 / MM"),
    ("tp2", "abc", "TP2"), ("tp3", "abc", "TP3"),
    ("price", "abc", "成交价格"), ("hands", "1.5", "手数"),
    ("fees", "abc", "税费"), ("price", "nan", "成交价格"),
    ("fees", "inf", "税费"),
])
def test_bad_numeric_input_names_field_without_technical_error(monkeypatch, field, text, label):
    dialog, popup = make_dialog(monkeypatch)
    if field in ("price", "hands", "fees"):
        dialog.batches.rows[0][field] = variable(text)
    else:
        setattr(dialog, field, variable(text))
    dialog._ok()
    assert not dialog.confirmed and dialog.values is None
    dialog.top.destroy.assert_not_called()
    assert label in popup.call_args.args[1]
    assert "float" not in popup.call_args.args[1]


def test_incomplete_second_buy_is_not_silently_ignored(monkeypatch):
    dialog, popup = make_dialog(monkeypatch)
    dialog.batches.rows.append(dict(dialog.batches.rows[0], hands=variable("")))
    dialog._ok()
    assert "第2笔" in popup.call_args.args[1]
    assert not dialog.confirmed


def test_no_buy_rows_requires_actual_execution(monkeypatch):
    dialog, popup = make_dialog(monkeypatch)
    dialog.batches.rows = []
    dialog._ok()
    assert "至少一笔实际买入" in popup.call_args.args[1]
    assert not dialog.confirmed


def test_valid_buy_preserves_payload_and_optional_fields(monkeypatch):
    dialog, popup = make_dialog(monkeypatch)
    dialog._ok()
    popup.assert_not_called()
    assert dialog.confirmed
    dialog.top.destroy.assert_called_once()
    assert dialog.values == {
        "code": "sh.600000", "name": "测试股票", "entry": 10.0,
        "stop": 9.0, "tp1": 12.0, "tp2": None, "tp3": None,
        "buy_batches": [{"date": "2026-10-09", "price": 10.1, "hands": 2, "fees": 0}],
        "plan_id": "plan", "observation_id": "observation",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows native holding form")
def test_native_plan_only_submit_then_complete_actual_buy(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", "from tests.test_position_input_messages import check_native; "
         "import sys; check_native(sys.argv[1])", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def check_native(runtime):
    import ttkbootstrap as ttk
    from workbench.store import Store
    store = Store(Path(runtime))
    account = store.save_account("测试", 50000)
    root = ttk.Window(themename="darkly")
    root.withdraw()
    original_popup = ui.messagebox.showerror
    popup = Mock()
    ui.messagebox.showerror = popup
    try:
        dialog = ui._PositionDialog(root, store, {"sh.600000": "浦发银行"})
        dialog.code.set("600000")
        dialog.name.set("浦发银行")
        dialog.entry_price.set("10")
        dialog.stop.set("9")
        dialog.tp1.set("12")
        root.update_idletasks()
        # Invoke the real confirmation button, not only a validation helper.
        buttons = dialog.form.grid_slaves(row=6)[0].winfo_children()
        confirm = next(button for button in buttons if button.cget("text") == "确认")
        confirm.invoke()
        assert not dialog.confirmed and dialog.top.winfo_exists()
        assert "实际买入" in popup.call_args.args[1]
        assert "float" not in popup.call_args.args[1]
        assert not store.rows("SELECT * FROM positions")
        assert not store.rows("SELECT * FROM position_fills")
        row = dialog.batches.rows[0]
        row["price"].set("10.1")
        row["hands"].set("2")
        row["fees"].set("3")
        confirm.invoke()
        assert dialog.confirmed and popup.call_count == 1
        store.save_position(account, **dialog.values)
        execution = store.rows("SELECT * FROM position_fills")[0]
        assert execution["quantity"] == 200 and execution["fees"] == 3
    finally:
        ui.messagebox.showerror = original_popup
        root.destroy()
