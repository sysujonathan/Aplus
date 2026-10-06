import pandas as pd
import pytest
import gc

from workbench.closed_import import OcrToken
from workbench.daily_returns import (
    calculate_daily_pnl, historical_daily_returns, parse_calendar_text, parse_calendar_tokens,
)
from workbench.market import save_dataset
from workbench.store import Store


@pytest.fixture(autouse=True)
def collect_destroyed_calendar_on_main_thread():
    yield
    # Destroyed widgets retain callback/variable cycles. Collect them here,
    # before later service tests can trigger collection on a worker thread.
    gc.collect()


def fill(day, side, price, quantity=100, fees=0):
    return {"code": "sh.600000", "trade_date": day, "side": side,
            "price": price, "quantity": quantity, "fees": fees}


def quote(day, close, previous):
    return {"sh.600000": {"date": day, "price": close, "previous_close": previous}}


def test_buy_day_uses_fill_price_and_fees_not_previous_close():
    assert calculate_daily_pnl([fill("2026-09-01", "BUY", 10, fees=1)],
                               quote("2026-09-01", 10.4, 9), "2026-09-01") == 39


def test_overnight_and_partial_sale_include_remaining_stock_and_fees():
    fills = [fill("2026-09-01", "BUY", 10),
             fill("2026-09-02", "SELL", 10.7, quantity=50, fees=2)]
    # 50 remaining * (10.6 - 10.4) + 50 sold * (10.7 - 10.4) - 2.
    assert calculate_daily_pnl(fills, quote("2026-09-02", 10.6, 10.4), "2026-09-02") == 23


def test_full_exit_keeps_daily_profit_without_double_counting_lifetime_pnl():
    fills = [fill("2026-09-01", "BUY", 10, fees=1),
             fill("2026-09-03", "SELL", 10.7, fees=2)]
    assert calculate_daily_pnl(fills, quote("2026-09-03", 10.9, 10.6), "2026-09-03") == 8
    assert calculate_daily_pnl(fills, quote("2026-09-02", 10.6, 10.4), "2026-09-02") == 20


@pytest.mark.parametrize("quotes", [{}, quote("2026-09-01", 10.2, 10), quote("2026-09-02", 10.2, None)])
def test_missing_or_stale_quotes_are_unknown_not_zero(quotes):
    assert calculate_daily_pnl([fill("2026-09-01", "BUY", 10)], quotes, "2026-09-02") is None


def make_history(store):
    account = store.save_account("测试账户", 10000, accounting_mode="history")
    position = store.save_position(account, "sh.600000", "测试股票", 10, 9, 12,
                                   [{"date": "2026-09-01", "price": 10, "hands": 1, "fees": 1}])
    frame = pd.DataFrame({"date": ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"],
                          "open": [9, 10, 10.4, 10.6], "close": [9, 10.4, 10.6, 10.9],
                          "high": [10, 11, 11, 11], "low": [8, 9, 10, 10], "volume": [100]*4})
    save_dataset(store, "sh.600000", frame, "baostock", "不复权")
    return account, position


def test_historical_prices_distribute_lifetime_pnl_and_capital_transfer_is_not_profit(tmp_path):
    store = Store(tmp_path)
    account, position = make_history(store)
    store.sell_position(position, [{"date": "2026-09-03", "price": 10.7, "hands": 1}], 2)
    store.save_cash_flow(account, "2026-09-02", "转入", 500)
    store.save_cash_flow(account, "2026-09-02", "利息归本", 0.50)
    rows = historical_daily_returns(store, account)
    assert [r["pnl"] for r in rows] == [39, 20.5, 8]
    assert sum(r["pnl"] for r in rows) == pytest.approx(67.5)
    assert store.rows("SELECT pnl FROM closed_trades")[0]["pnl"] == pytest.approx(67)


def test_summary_only_trades_do_not_become_fake_daily_returns(tmp_path):
    store = Store(tmp_path)
    account, _ = make_history(store)
    store.save_closed_trade(account, "sz.000001", "另一只股票", "2026-09-02", 5, -200, -4)
    assert [r["date"] for r in historical_daily_returns(store, account)] == ["2026-09-03"]


def test_broker_priority_account_isolation_reopen_and_atomic_validation(tmp_path):
    store = Store(tmp_path)
    account = store.save_account("账户甲", 10000)
    other = store.save_account("账户乙", 20000)
    rows = parse_calendar_text("2026-09-01 -10.25\n2026-09-02 0.00\n2026-09-03 休市")
    store.save_daily_returns(account, rows)
    store.save_daily_returns(account, [{"date": "2026-09-01", "pnl": 50,
                                       "source": "local", "status": "recorded"}])
    assert Store(tmp_path).list_daily_returns(account) == rows
    assert store.list_daily_returns(other) == []
    before = store.rows("SELECT * FROM accounts")
    with pytest.raises(ValueError):
        store.save_daily_returns(account, [rows[0], {"date": "wrong", "pnl": 1}])
    assert store.list_daily_returns(account) == rows
    assert store.rows("SELECT * FROM accounts") == before
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "8"


def test_calendar_parser_preserves_zero_holiday_and_rejects_overlapping_conflicts():
    assert len(parse_calendar_text("2026-09-01 -2.10\n2026-09-01 -2.10")) == 1
    for text in ("2026-09-01 1\n2026-09-01 2", "2026-09-01 nan", "2026-02-30 1"):
        with pytest.raises(ValueError):
            parse_calendar_text(text)


def test_removed_or_corrected_fills_do_not_leave_stale_local_returns(tmp_path):
    store = Store(tmp_path)
    account, position = make_history(store)
    store.save_daily_returns(account, historical_daily_returns(store, account), replace_local=True)
    broker = parse_calendar_text("2026-09-02 12.34")
    store.save_daily_returns(account, broker)
    store.delete_position(position)
    store.save_daily_returns(account, historical_daily_returns(store, account), replace_local=True)
    assert store.list_daily_returns(account) == broker


def test_spatial_ocr_keeps_dates_distinct_from_amounts_and_holiday():
    tokens = [OcrToken("2026年9月", 500, 40)]
    tokens += [OcrToken(label, 100 + i*200, 100)
               for i, label in enumerate(("周一", "周二", "周三", "周四", "周五"))]
    tokens += [OcrToken("01", 300, 200), OcrToken("-12.34", 300, 270),
               OcrToken("02", 500, 200), OcrToken("0.00", 500, 270),
               OcrToken("25", 900, 600), OcrToken("休市", 900, 670)]
    rows = parse_calendar_tokens(tokens)
    assert [r["pnl"] for r in rows] == [-12.34, 0, None]
    assert rows[-1]["date"] == "2026-09-25"


def test_calendar_date_color_and_grid_height_are_independent_of_returns(tmp_path):
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.trade_management import _ReturnCalendar
    from gui.theme import TEXT, UP, DOWN

    root = ttk.Window(themename="darkly")
    root.withdraw()
    try:
        parent = ttk.Frame(root)
        parent.pack(fill=tk.BOTH, expand=True)
        view = _ReturnCalendar(parent)
        view.set_rows(parse_calendar_text("2026-09-01 -12.34\n2026-09-02 45.67\n2026-09-03 0.00\n2026-09-25 休市"))
        labels = [label for cell in view.grid.winfo_children() if isinstance(cell, ttk.Frame)
                  for label in cell.winfo_children()]
        assert all(str(label.cget("foreground")) == TEXT for label in labels
                   if str(label.cget("text")) in ("01", "02", "03", "25"))
        assert next(str(l.cget("foreground")) for l in labels if l.cget("text") == "-12.34") == DOWN
        assert next(str(l.cget("foreground")) for l in labels if l.cget("text") == "+45.67") == UP
        assert any(l.cget("text") == "休市" for l in labels)
        assert len([c for c in view.grid.winfo_children() if isinstance(c, ttk.Frame)]) == 30
        view.set_hidden(True)
        labels = [l for c in view.grid.winfo_children() if isinstance(c, ttk.Frame) for l in c.winfo_children()]
        assert all(l.cget("text") != "-12.34" for l in labels)
        view.scale.set("年")
        assert len(view.grid.winfo_children()) == 12
        view.scale.set("月")
        view.shift(-1)
        assert len([c for c in view.grid.winfo_children() if isinstance(c, ttk.Frame)]) == 30
        root.update_idletasks()
    finally:
        root.destroy()
