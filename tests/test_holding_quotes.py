import gc
import os
import threading
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from workbench.holding_quotes import (CHINA, QuotePoller, parse_quotes, fetch_quotes,
    session_status, select_quotes, saved_quotes, snapshot_reference, value_holdings)
from workbench.holding_chart import load_holding_chart
from workbench.market import save_dataset
from workbench.store import Store
from workbench.trading import management_report

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=CHINA)
CALENDAR = {"start": "2026-09-01", "end": "2026-10-10", "trading_days": ["2026-09-30"]}
CODE = "sh.600000"


@pytest.fixture(autouse=True)
def collect_tk():
    # ttkbootstrap caches Style at class level across separate Tk interpreters.
    # These tests create isolated windows; production has one persistent root.
    from ttkbootstrap import Style
    import tkinter as tk
    if tk._default_root is None:
        Style.instance = None
    gc.collect()
    yield
    if tk._default_root is None:
        Style.instance = None
    gc.collect()


@pytest.fixture(scope="module")
def quote_window():
    import ttkbootstrap as ttk
    ttk.Style.instance = None
    root = ttk.Window(themename="darkly")
    root.withdraw()
    yield root
    root.destroy()
    ttk.Style.instance = None
    gc.collect()


def quote(price=11, previous=10.5, moment=NOW):
    return {"price": price, "previous_close": previous, "date": moment.date().isoformat(),
            "quote_time": moment.isoformat(), "source": "sina"}


def payload(price="11", previous="10.5", stamp="2026-09-30,10:00:00", symbol="sh600000"):
    fields = ["测试", "10", previous, price] + ["0"] * 26 + stamp.split(",") + ["00"]
    return f'var hq_str_{symbol}="{",".join(fields)}";'


def bars():
    return pd.DataFrame({"date": ["2026-09-28", "2026-09-29", "2026-09-30"],
        "open": [10., 10., 10.], "high": [12., 12., 12.], "low": [9., 9., 9.],
        "close": [10., 10.5, 11.], "volume": [1000.] * 3})


def ledger(tmp_path, mode="history"):
    store = Store(tmp_path)
    aid = store.save_account("测试", 10000, accounting_mode=mode)
    pid = store.save_position(aid, CODE, "测试", 10, 9, 12,
        [{"date": "2026-09-29", "price": 10, "hands": 2, "fees": 2}], tp2=13, tp3=14)
    return store, aid, pid


def report(store, aid):
    return management_report(store, aid, price_map={CODE: quote()})


def test_opening_capital_display_retains_independent_reverse_calculation(tmp_path):
    from types import SimpleNamespace
    from gui.trade_management import TradeManagementFrame
    store,aid,_=ledger(tmp_path)
    store.execute('UPDATE accounts SET current_total_assets=10200 WHERE id=?',(aid,))
    from workbench.account_reconciliation import capture_reference
    store.save_account_reconciliation(aid, capture_reference(store,aid,10200,'2026-09-30',
        {CODE:{**quote(11), 'adjusted':False}}))
    page=SimpleNamespace(_show_fund_values=True,return_calendar=Mock(),fund_total=Mock(),
        fund_position=Mock(),funds_reconciliation=Mock(),fund_metrics={
            k:(Mock(),Mock()) for k in ('market_value','floating_pnl','daily_pnl',
                                     'withdrawable_cash','available_cash','asset_pnl')})
    for price,implied in ((11,10002),(12,10002),(9,10002)):
        page._report=management_report(store,aid,{CODE:quote(price)})
        TradeManagementFrame._fill_funds(page,update_calendar=False)
        assert page.funds_reconciliation.set.call_args.args==(f'初始资金（推算） {implied:,.2f} 元',)
    store.save_closed_trade(aid,CODE,'测试','2026-09-28',2,100,1)
    store.save_cash_flow(aid,'2026-09-28','利息',50)
    page._report=management_report(store,aid,{CODE:quote(11)})
    TradeManagementFrame._fill_funds(page,update_calendar=False)
    assert page.funds_reconciliation.set.call_args.args==('初始资金（推算） 9,852.00 元',)
    historical_initial=page._report['summary']['implied_initial_equity']
    live=value_holdings(page._report,{CODE:quote(15)},fills(store,aid),NOW)
    assert live['summary']['implied_initial_equity']==historical_initial
    assert live['summary']['floating_pnl'] != page._report['summary']['floating_pnl']
    page._show_fund_values=False
    TradeManagementFrame._fill_funds(page,update_calendar=False)
    assert page.funds_reconciliation.set.call_args.args==('初始资金（推算） •••••• 元',)
    page._show_fund_values=True
    store.execute('UPDATE accounts SET current_total_assets=0 WHERE id=?',(aid,))
    page._report=management_report(store,aid,{CODE:quote(11)})
    TradeManagementFrame._fill_funds(page,update_calendar=False)
    assert page.funds_reconciliation.set.call_args.args==('初始资金（推算） —',)
    assert store.rows('SELECT initial_equity FROM accounts WHERE id=?',(aid,))[0]['initial_equity']==10000


def test_history_account_dialog_uses_dated_check_not_live_floating(tmp_path, quote_window):
    from gui.trade_management import _AccountDialog
    from workbench.account_reconciliation import capture_reference, reconcile_reference
    store,aid,_=ledger(tmp_path)
    store.execute('UPDATE accounts SET current_total_assets=10200 WHERE id=?',(aid,))
    ref=capture_reference(store,aid,10200,'2026-09-30',{CODE:{**quote(11),'adjusted':False}})
    store.save_account_reconciliation(aid,ref)
    base=management_report(store,aid,{CODE:quote(99)})
    def builder(day,broker):
        candidate={**ref,'broker_total_assets':broker}
        assert day=='2026-09-30'
        check=reconcile_reference(store,{**base['account'],'current_total_assets':broker},candidate)
        return candidate,check['reconciliation_components']
    dialog=_AccountDialog(quote_window,base['account'],summary=base['summary'],
        check_builder=builder,check_dates=['2026-09-30'])
    try:
        assert dialog.reverse_initial.get()=='10002.00'
        assert float(dialog.system_total.get())==pytest.approx(27798)
        dialog.initial.set('12345')
        assert dialog.reverse_initial.get()=='10002.00'
        dialog.total.set('10300')
        assert dialog.reverse_initial.get()=='10102.00'
        dialog._ok()
        assert dialog.confirmed
        assert dialog.check_reference['broker_total_assets']==10300
        assert dialog.values['initial_equity']==12345
    finally:
        if dialog.top.winfo_exists():dialog.top.destroy()


def fills(store, aid):
    return store.rows("SELECT f.*,p.code FROM position_fills f JOIN positions p ON p.id=f.position_id "
                      "WHERE p.account_id=?", (aid,))


def database(store):
    tables = store.rows("SELECT name FROM sqlite_master WHERE type='table'")
    return {t["name"]: store.rows(f'SELECT * FROM "{t["name"]}"') for t in tables}


def test_quote_parser_identity_and_actual_time():
    result = parse_quotes(payload() + payload(symbol="sz000001"), [CODE], NOW)
    assert result == {CODE: quote()}
    assert not parse_quotes(payload(symbol="sz600000"), [CODE], NOW)
    assert parse_quotes(payload(stamp="2026-09-29,15:00:00"), [CODE], NOW)[CODE]["date"] == "2026-09-29"


@pytest.mark.parametrize("change", [dict(price="0"), dict(price="nan"), dict(previous="inf"),
    dict(previous="0"), dict(stamp="invalid,xx"), dict(stamp="2026-09-30,10:02:00")])
def test_invalid_quote_is_not_cost_or_zero(change):
    assert not parse_quotes(payload(**change), [CODE], NOW)


def test_fetch_direct_opener_not_global_proxy_and_unsupported_code():
    response = Mock()
    response.read.return_value = payload().encode("gbk")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    before = dict(os.environ)
    with patch("workbench.holding_quotes.urllib.request.build_opener", return_value=opener), \
         patch("workbench.holding_quotes.china_now", return_value=NOW):
        quotes, failed = fetch_quotes([CODE, "bj.920000"])
    assert quotes == {CODE: quote()} and failed == {"bj.920000"}
    assert opener.open.call_args.kwargs["timeout"] == 8
    assert opener.open.call_args.args[0].full_url.endswith("sh600000")
    assert dict(os.environ) == before


@pytest.mark.parametrize("clock,expected", [("09:29", "非交易时段"), ("09:30", "交易中"),
    ("11:30", "交易中"), ("11:31", "非交易时段"), ("12:59", "非交易时段"),
    ("13:00", "交易中"), ("15:00", "交易中"), ("15:01", "非交易时段")])
def test_sessions(clock, expected):
    assert session_status(datetime.fromisoformat("2026-09-30T" + clock).replace(tzinfo=CHINA), CALENDAR) == expected


def test_published_schedule_fills_expired_calendar_but_never_guesses_unknown_year():
    assert session_status(NOW + timedelta(days=1), CALENDAR) == "休市"
    for calendar in (None, [], {}, {**CALENDAR, "end": "2026-09-29"}):
        assert session_status(NOW, calendar) == "交易中"
        assert "待更新" in session_status(NOW.replace(year=2027, day=29), calendar)


def test_stale_failed_and_previous_day_quotes_are_explicit():
    old = quote(moment=NOW - timedelta(seconds=46))
    assert select_quotes({}, {CODE: old}, [CODE], set(), NOW, CALENDAR)[CODE]["state"] == "行情滞后"
    assert select_quotes({}, {CODE: old}, [CODE], {CODE}, NOW, CALENDAR)[CODE]["state"] == "更新失败"
    history = {CODE: {"price": 10, "date": "2026-09-29", "source": "history"}}
    assert select_quotes(history, {CODE: quote(moment=NOW-timedelta(days=1))}, [CODE], set(), NOW, CALENDAR)[CODE]["state"] == "历史收盘"
    assert not select_quotes({}, {}, [CODE], {CODE}, NOW, CALENDAR)


def test_poller_no_overlap_and_invalidated_late_result():
    entered, release = threading.Event(), threading.Event()
    def slow(codes):
        entered.set()
        assert release.wait(3)
        return {CODE: quote()}, set()
    poller = QuotePoller(slow)
    assert poller.start([CODE]) and entered.wait(2)
    assert not poller.start([CODE])
    poller.invalidate()
    assert not poller.start([CODE])
    release.set()
    result = poller.results.get(timeout=2)
    poller.results.put(result)
    assert poller.take() is None and not poller.busy
    for i in range(4):
        poller.results.put((poller.generation, {}, {CODE}))
        assert poller.take() == ({}, {CODE})
    assert poller.delay == 120
    poller.results.put((poller.generation, {}, set()))
    poller.take()
    assert poller.delay == 15


def test_history_cash_unchanged_with_quotes_partial_sale_and_closed_pnl_not_double_counted(tmp_path):
    store, aid, pid = ledger(tmp_path)
    store.save_closed_trade(aid, "600001", "补录", "2026-09-28", 1, -100, -1)
    base, tx = report(store, aid), fills(store, aid)
    before = database(store)
    for price in (9, 11, 12):
        overlay = value_holdings(base, {CODE: quote(price)}, tx, NOW)
        assert overlay["summary"]["available_cash"] == pytest.approx(7898)
        assert overlay["summary"]["total_assets"] == pytest.approx(7898 + 200*price)
        assert overlay["summary"]["daily_pnl"] == pytest.approx((price-10.5)*200)
    assert database(store) == before
    store.sell_position(pid, [{"date": "2026-09-30", "price": 11, "hands": 1}], 2)
    partial = value_holdings(report(store, aid), {CODE: quote()}, fills(store, aid), NOW)
    assert partial["summary"]["available_cash"] == pytest.approx(8996)
    assert partial["summary"]["daily_pnl"] == pytest.approx(98)
    store.sell_position(pid, [{"date": "2026-09-30", "price": 11, "hands": 1}], 2)
    closed = value_holdings(report(store, aid), {CODE: quote()}, fills(store, aid), NOW)
    assert closed["summary"]["available_cash"] == pytest.approx(10094)
    assert closed["summary"]["daily_pnl"] == pytest.approx(96)


def test_snapshot_cash_reference_persists_and_moves_only_for_ledger_changes(tmp_path):
    store, aid, pid = ledger(tmp_path, "snapshot")
    base, tx = report(store, aid), fills(store, aid)
    ref = store.holding_cash_reference(aid, snapshot_reference(base, tx))
    assert ref["cash"] == 7800
    assert Store(tmp_path).holding_cash_reference(aid, {**ref, "cash": 1}) == ref
    for price in (8, 12):
        assert value_holdings(base, {CODE: quote(price)}, tx, NOW, ref)["summary"]["available_cash"] == 7800
    store.save_cash_flow(aid, "2026-09-30", "利息", 1.53)
    store.sell_position(pid, [{"date": "2026-09-30", "price": 11, "hands": 1}], 2)
    result = value_holdings(report(store, aid), {CODE: quote()}, fills(store, aid), NOW, ref)
    assert result["summary"]["available_cash"] == pytest.approx(8899.53)
    assert result["summary"]["daily_pnl"] == pytest.approx(99.53)
    new_ref = {**ref, "cash": 500}
    store.save_account("测试", 10000, account_id=aid, snapshot_reference=new_ref)
    assert store.holding_cash_reference(aid) == new_ref
    assert not store.holding_auto_quotes()
    store.holding_auto_quotes(True)
    assert Store(tmp_path).holding_auto_quotes()
    with pytest.raises(ValueError):
        store.holding_cash_reference(aid, {**ref, "cash": float("nan")})


def test_missing_prices_never_substitute_cost_and_adjusted_history_is_not_daily_pnl(tmp_path):
    store, aid, _pid = ledger(tmp_path)
    base, tx = report(store, aid), fills(store, aid)
    result = value_holdings(base, {}, tx, NOW)
    assert result["summary"]["available_cash"] == 7998
    assert result["summary"]["total_assets"] is None
    assert result["summary"]["daily_pnl"] is None
    assert result["positions"][0]["current_price"] is None
    assert result["positions"][0]["floating_pnl"] is None
    adjusted = value_holdings(base, {CODE: {**quote(), "adjusted": True}}, tx, NOW)
    assert adjusted["summary"]["daily_pnl"] is None
    assert value_holdings(base, {CODE: quote()}, tx, NOW, None)["summary"]["total_assets"] is not None
    store.save_account("测试", 10000, account_id=aid)
    assert value_holdings(report(store, aid), {CODE: quote()}, tx, NOW)["summary"]["available_cash"] is None


def test_manual_transfer_changes_cash_but_not_day_profit_and_today_buy_uses_execution(tmp_path):
    store = Store(tmp_path)
    aid = store.save_account("测试", 10000, accounting_mode="history")
    store.save_position(aid, CODE, "测试", 10, 9, 12,
                        [{"date": "2026-09-30", "price": 10, "hands": 1, "fees": 5}])
    store.save_cash_flow(aid, "2026-09-30", "转入", 1000)
    store.save_cash_flow(aid, "2026-09-30", "利息", 1.53)
    result = value_holdings(report(store, aid), {CODE: quote()}, fills(store, aid), NOW)
    assert result["summary"]["available_cash"] == pytest.approx(9996.53)
    assert result["summary"]["daily_pnl"] == pytest.approx(96.53)


def test_saved_history_reads_are_verified_and_do_not_audit_write(tmp_path):
    store, aid, _pid = ledger(tmp_path)
    saved = save_dataset(store, CODE, bars(), "baostock", "不复权")
    before = database(store)
    quotes = saved_quotes(store, [CODE, "sz.000001"])
    assert quotes[CODE]["price"] == 11 and not quotes[CODE]["adjusted"]
    assert before == database(store)
    record = store.rows("SELECT * FROM datasets WHERE id=?", (saved,))[0]
    (store.root / record["path"]).write_bytes(b"tampered")
    assert saved_quotes(store, [CODE]) == {}


def test_holding_chart_one_cost_line_all_buy_arrows_plan_levels_read_only(tmp_path):
    store, aid, pid = ledger(tmp_path)
    store.sell_position(pid, [{"date": "2026-09-30", "price": 11, "hands": 1}], 2)
    save_dataset(store, CODE, bars(), "baostock", "不复权")
    row = report(store, aid)["positions"][0]
    before = database(store)
    data = load_holding_chart(store, row)
    assert [e["label"] for e in data["events"]] == ["买入", "卖出"]
    assert data["events"][0]["price"] == 10
    assert [l["label"] for l in data["levels"]] == ["摊薄成本（含税费）", "止损 SL1", "止盈 TP1", "止盈 TP2", "止盈 TP3"]
    assert data["levels"][0]["price"] == pytest.approx(9.04)
    assert database(store) == before
    with pytest.raises(ValueError, match="变化"):
        load_holding_chart(store, {**row, "account_id": "other"})


def test_holding_adjusted_axis_only_converts_with_same_date_raw_price(tmp_path):
    store, aid, _pid = ledger(tmp_path)
    adjusted = bars()
    for key in ("open", "high", "low", "close"):
        adjusted[key] /= 2
    save_dataset(store, CODE, adjusted, "baostock", "前复权")
    row = report(store, aid)["positions"][0]
    data = load_holding_chart(store, row)
    assert all(l["price"] is None for l in data["levels"])
    assert data["reference_needed"]
    wrong = load_holding_chart(store, row, quote=quote(moment=NOW-timedelta(days=1)))
    assert all(l["price"] is None for l in wrong["levels"])
    intraday = load_holding_chart(store, row, quote=quote())
    assert intraday["reference_needed"]  # an intraday price is NOT the daily close
    data = load_holding_chart(store, row, quote=quote(moment=NOW.replace(hour=15)))
    assert data["levels"][0]["price"] == pytest.approx(5.005)
    assert data["events"][0]["execution_price"] == 10
    assert data["events"][0]["price"] == 5.25


def test_holding_renderer_default_research_pixels_unchanged_and_scaled_level_labels(tmp_path):
    from gui.closed_trade_chart import render_closed_chart
    from gui.chart_renderer import render_chart
    from matplotlib.axes import Axes
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "不复权")
    data = load_holding_chart(store, report(store, aid)["positions"][0])
    before = database(store)
    original, calls = Axes.text, []
    def capture(axis, *args, **kwargs):
        if "摊薄成本" in str(args[2]) or "止盈" in str(args[2]):
            calls.append((args, kwargs))
        return original(axis, *args, **kwargs)
    with patch.object(Axes, "text", new=capture):
        small = render_closed_chart(data, size=(1100, 550))
        first = list(calls)
        calls.clear()
        large = render_closed_chart(data, size=(2200, 1100))
    small.save(tmp_path / "holding-small.png")
    large.save(tmp_path / "holding-large.png")
    assert first and calls and calls[0][1]["fontsize"] > first[0][1]["fontsize"]
    assert len(first) == 4  # one cost and all three targets, even outside bar range
    assert first[0][0][1] == calls[0][0][1]
    assert before == database(store)
    with patch.object(Axes, "annotate", autospec=True) as annotate:
        render_closed_chart(data, size=(1100, 550))
    buy = next(call for call in annotate.call_args_list if call.args[1] == "买入")
    assert buy.kwargs["arrowprops"]["arrowstyle"] == "->"
    assert buy.kwargs["xy"] == (1, 10)
    default = render_chart(bars(), {}, "", {}, size=(900, 500))
    explicit = render_chart(bars(), {}, "", {}, size=(900, 500), holding_levels=())
    assert default.tobytes() == explicit.tobytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_gui_live_overlay_keeps_rows_selection_calendar_and_ledger(tmp_path, quote_window):
    import ttkbootstrap as ttk
    from gui.trade_management import TradeManagementFrame, _PositionDialog
    from gui.closed_trade_chart import HoldingTradeChartDialog
    store, aid, pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "不复权")
    root = quote_window
    try:
        with patch("gui.trade_management.china_now", return_value=NOW):
            page = TradeManagementFrame(root, store)
            page.pack(fill="both", expand=True)
            root.update()
            assert not page.auto_quote_var.get()
            ids = page.position_tree.get_children()
            page.position_tree.selection_set(ids[0])
            scroll = page.position_tree.yview()
            before = database(store)
            with patch.object(page.return_calendar, "set_rows", side_effect=AssertionError("calendar mutation")):
                page._live_quotes = {CODE: quote(12)}
                page._apply_quote_view(NOW, CALENDAR)
            assert page.position_tree.get_children() == ids
            assert page.position_tree.selection() == (ids[0],)
            assert page.position_tree.yview() == scroll
            assert "12.00" in page.position_tree.item(ids[0], "values")[4]
            assert database(store) == before
            # Disabling rejects a response already queued by the previous generation.
            page._quote_poller.results.put((page._quote_poller.generation, {CODE: quote(13)}, set()))
            page._toggle_auto_quotes()
            page._quote_tick()
            assert page._live_quotes[CODE]["price"] == 12
            editor = _PositionDialog(page, store, {}, page._selected_position())
            labels = [str(w.cget("text")) for w in editor.form.winfo_children() if "text" in w.keys()]
            assert "查看持仓图" in labels
            with patch.object(editor, "_autofill_plan") as autofill:
                editor._resolve_code()
                autofill.assert_not_called()
            assert float(editor.stop.get()) == 9 and float(editor.tp1.get()) == 12
            editor.top.destroy()
            chart = HoldingTradeChartDialog(root, store, page._selected_position())
            root.update()
            assert tuple(map(int, chart.top.resizable())) == (1, 1)
            assert len([l for l in chart.data["levels"] if "成本" in l["label"]]) == 1
            chart.top.destroy()
            page.destroy()
    finally:
        for child in root.winfo_children():
            child.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_snapshot_account_editor_keeps_cash_when_revalued(tmp_path, quote_window):
    import ttkbootstrap as ttk
    from gui.trade_management import _AccountDialog, _sort_value
    store, aid, _pid = ledger(tmp_path, "snapshot")
    base, tx = report(store, aid), fills(store, aid)
    live = value_holdings(base, {CODE: quote(12)}, tx, NOW, snapshot_reference(base, tx))
    root = quote_window
    try:
        dialog = _AccountDialog(root, live["account"], market_value=live["summary"]["market_value"],
                                summary=live["summary"])
        assert float(dialog.available.get()) == 7800
        assert float(dialog.initial.get()) == 10200
        dialog.top.destroy()
        assert _sort_value("9.50 · 历史收盘") < _sort_value("12.00 · 更新失败")
    finally:
        for child in root.winfo_children():
            child.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_gui_background_fetch_responsive_and_auto_pauses_outside_verified_session(tmp_path, quote_window):
    import ttkbootstrap as ttk
    from gui.trade_management import TradeManagementFrame
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "不复权")
    entered, release = threading.Event(), threading.Event()
    def fetch(codes):
        entered.set()
        release.wait(3)
        return {CODE: quote(13)}, set()
    root = quote_window
    try:
        with patch("gui.trade_management.china_now", return_value=NOW):
            page = TradeManagementFrame(root, store)
            page.after_cancel(page._quote_timer)
            page._quote_poller = QuotePoller(fetch)
            with patch.object(page, "_quote_calendar", return_value=CALENDAR):
                page.auto_quote_var.set(True)
                page._toggle_auto_quotes()
                assert entered.wait(2) and page._quote_poller.busy
                idle = Mock()
                page.after_idle(idle)
                root.update()
                idle.assert_called_once()
                page.auto_quote_var.set(False)
                page._toggle_auto_quotes()
                release.set()
                response = page._quote_poller.results.get(timeout=2)
                page._quote_poller.results.put(response)
                page._quote_tick()
                assert CODE not in page._live_quotes
                assert not store.holding_auto_quotes()
                page.auto_quote_var.set(True)
                with patch.object(page, "_start_quotes") as start:
                    with patch("gui.trade_management.china_now", return_value=NOW.replace(hour=12)):
                        page._quote_tick()
                    start.assert_not_called()
                    with patch.object(page, "_quote_calendar", return_value=None):
                        with patch("gui.trade_management.china_now", return_value=NOW.replace(year=2027, day=29)):
                            page._quote_tick()
                    start.assert_not_called()
                assert "日历待更新" in page.asof_var.get()
            page.destroy()
    finally:
        release.set()
        for child in root.winfo_children():
            child.destroy()


@pytest.mark.parametrize("day", ["2026-01-01", "2026-01-02", "2026-02-16", "2026-02-23",
    "2026-04-06", "2026-05-05", "2026-06-19", "2026-09-25", "2026-10-01",
    "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-10"])
def test_published_exchange_holidays_and_makeup_weekends_closed(day):
    from workbench.exchange_calendar import is_trading_day
    assert is_trading_day(day) is False
    assert session_status(datetime.fromisoformat(day + "T10:00").replace(tzinfo=CHINA), None) == "休市"


def test_calendar_coverage_validation_and_local_overrides():
    from workbench.exchange_calendar import is_trading_day
    old = {**CALENDAR, "end": "2026-10-02"}
    assert is_trading_day("2026-10-08", old) is True
    assert is_trading_day("2027-10-08", old) is None
    assert is_trading_day("2027-10-09", old) is False
    # A verified local calendar can express a special closure in any year.
    local = {"start": "2027-10-01", "end": "2027-10-10", "trading_days": ["2027-10-07"]}
    assert is_trading_day("2027-10-07", local) is True
    assert is_trading_day("2027-10-08", local) is False
    for days in (["bad-date"], [["2026-10-08"]], ["2026-10-08"] * 2, ["2025-01-01"]):
        assert is_trading_day("2026-10-08", {**old, "end": "2026-12-31", "trading_days": days}) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_auto_polling_resumes_after_holiday_without_manual_refresh(tmp_path, quote_window):
    from gui.trade_management import TradeManagementFrame
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "不复权")
    # Same calendar cutoff as the user's installation, independent of bar data.
    store.write_artifact("trading_calendar.json", b'{"start":"1990-12-19","end":"2026-10-02",'
                         b'"trading_days":["2026-09-30"]}')
    store.holding_auto_quotes(True)
    clock = datetime(2026, 10, 8, 11, 19, tzinfo=CHINA)
    now = Mock(return_value=clock)
    monotonic = Mock(return_value=100.)
    calls = []
    def fetch(codes):
        calls.append(tuple(codes))
        return {CODE: quote(12 + len(calls), moment=now())}, set()
    root = quote_window
    with patch("gui.trade_management.china_now", now), \
         patch("gui.trade_management.time.monotonic", monotonic):
        page = TradeManagementFrame(root, store)
        page.after_cancel(page._quote_timer)
        page._quote_poller = QuotePoller(fetch)
        before = database(store)
        try:
            def consume():
                response = page._quote_poller.results.get(timeout=2)
                page._quote_poller.results.put(response)
                page._quote_tick()
                page.after_cancel(page._quote_timer)
            page._quote_tick()
            page.after_cancel(page._quote_timer)
            consume()
            assert len(calls) == 1 and "11:19:00" in page.asof_var.get()
            assert "自动更新（15秒）" in page.asof_var.get()
            assert page._report["positions"][0]["current_price"] == 13
            page._quote_tick()
            page.after_cancel(page._quote_timer)
            assert len(calls) == 1  # no early/overlapping request
            monotonic.return_value = 115.
            now.return_value = clock.replace(second=15)
            page._quote_tick()
            page.after_cancel(page._quote_timer)
            consume()
            assert len(calls) == 2 and "11:19:15" in page.asof_var.get()
            assert page._report["positions"][0]["current_price"] == 14
            for paused in (clock.replace(hour=12), clock.replace(day=7), clock.replace(hour=15, minute=1)):
                now.return_value = paused
                monotonic.return_value += 200
                page._quote_tick()
                page.after_cancel(page._quote_timer)
                assert len(calls) == 2
                assert "自动暂停" in page.asof_var.get()
            assert database(store) == before
        finally:
            page._quote_timer = None
            page.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_page_entry_hides_account_calendar_before_raise_and_quotes_do_not_reveal(tmp_path, quote_window):
    from types import SimpleNamespace
    from gui.trade_management import TradeManagementFrame
    from gui.main_window import AplusMainWindow
    from gui.theme import TEXT
    from workbench.daily_returns import parse_calendar_text
    store, aid, _pid = ledger(tmp_path)
    store.save_daily_returns(aid, parse_calendar_text("2026-10-08 -24.00"))
    root = quote_window
    with patch("gui.trade_management.china_now", return_value=datetime(2026, 10, 8, 11, tzinfo=CHINA)):
        page = TradeManagementFrame(root, store)
        try:
            assert not page._show_fund_values and page.return_calendar.hidden
            assert page.fund_total.get() == "••••••" and "显示" in page.eye_button.cget("text")
            page._toggle_fund_values()
            assert page._show_fund_values and not page.return_calendar.hidden
            assert page.fund_total.get() != "••••••"
            # Exercise the real section routing, asserting masking before tkraise.
            main = SimpleNamespace(trade_management=page,
                _pages={"premarket": Mock(), "other": page},
                _section_buttons={"premarket": Mock(), "other": Mock()})
            AplusMainWindow._switch_section(main, "premarket")
            with patch.object(page, "tkraise", side_effect=lambda: (
                pytest.fail("Private values were raised") if page._show_fund_values else None)):
                AplusMainWindow._switch_section(main, "other")
            assert page.current_account_id() == aid and page.return_calendar.hidden
            assert page.fund_total.get() == "••••••"
            cells = {c.winfo_children()[0].cget("text"): c for c in page.return_calendar.grid.winfo_children()
                     if c.winfo_children()}
            for day in ("01", "02", "05", "06", "07"):
                assert cells[day].winfo_children()[1].cget("text") == "休市"
            assert cells["08"].winfo_children()[1].cget("text") == "••••••"
            page._live_quotes = {CODE: quote(13, moment=datetime(2026, 10, 8, 11, tzinfo=CHINA))}
            page._apply_quote_view()
            assert page.fund_total.get() == "••••••" and page.return_calendar.hidden
            assert all(str(label.cget("foreground")) == TEXT for _, label in page.fund_metrics.values())
            page._toggle_fund_values()
            assert not page.return_calendar.hidden and page.fund_total.get() != "••••••"
            before = database(store)
            page.return_calendar.set_rows([])
            page.return_calendar.anchor = datetime(2026, 10, 1).date()
            page.return_calendar.render()
            cells = {c.winfo_children()[0].cget("text"): c for c in page.return_calendar.grid.winfo_children()
                     if c.winfo_children()}
            assert cells["08"].winfo_children()[1].cget("text") == "待补数据"
            assert "0 天" in page.return_calendar.summary.get()
            assert database(store) == before  # no fake zero-return holiday records
        finally:
            page.destroy()


def test_reference_conversion_uses_saved_risk_and_each_fill_date_without_writes(tmp_path):
    from workbench.holding_chart import apply_holding_reference
    store, aid, _pid = ledger(tmp_path)
    raw = bars()
    adjusted = raw.copy()
    for key in ("open", "high", "low", "close"):
        adjusted[key] *= pd.Series([.5, .5, .25])
    save_dataset(store, CODE, adjusted, "baostock", "前复权")
    before = database(store)
    data = load_holding_chart(store, report(store, aid)["positions"][0])
    resolved = apply_holding_reference(data, raw)
    assert [l["price"] for l in resolved["levels"]] == pytest.approx([2.5025, 2.25, 3., 3.25, 3.5])
    assert [l["value"] for l in resolved["levels"]] == pytest.approx([10.01, 9, 12, 13, 14])
    assert resolved["events"][0]["price"] == 5  # actual buy 10, NOT adjusted close 5.25
    assert resolved["events"][0]["execution_price"] == 10
    assert resolved["events"][0]["date"] == "2026-09-29"
    assert data["events"][0]["price"] == 5.25 and data["reference_needed"]
    assert all(l["price"] is None for l in data["levels"])
    assert not resolved["reference_needed"]
    assert data["frame"].ema20.tolist() == pytest.approx(adjusted.close.ewm(span=20, adjust=False).mean())
    assert database(store) == before
    partial = apply_holding_reference(data, raw.tail(1))
    assert partial["events"][0]["price"] == 5.25
    assert any("部分成交日" in w for w in partial["warnings"])


@pytest.mark.parametrize("fault", ["missing_end", "future", "zero", "nan"])
def test_invalid_raw_reference_never_fabricates_lines(tmp_path, fault):
    from workbench.holding_chart import apply_holding_reference
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "前复权")
    data = load_holding_chart(store, report(store, aid)["positions"][0])
    raw = bars()
    if fault == "missing_end":
        raw = raw.iloc[:-1]
    elif fault == "future":
        raw = pd.concat([raw, raw.tail(1).assign(date="2026-10-01")], ignore_index=True)
    else:
        raw.loc[2, "close"] = 0 if fault == "zero" else float("nan")
    with pytest.raises(ValueError):
        apply_holding_reference(data, raw)
    assert data["reference_needed"] and all(l["price"] is None for l in data["levels"])


def test_latest_holding_bars_not_replaced_by_older_unadjusted_snapshot(tmp_path):
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars().iloc[:-1], "baostock", "不复权")
    latest = save_dataset(store, CODE, bars(), "baostock", "前复权")
    data = load_holding_chart(store, report(store, aid)["positions"][0])
    assert data["dataset"]["id"] == latest and data["close_date"] == "2026-09-30"
    assert data["reference_needed"]


def test_raw_reference_provider_is_bounded_cancelable_and_never_changes_default_query(tmp_path):
    from workbench.holding_chart import fetch_holding_reference
    from workbench.provider_process import BaoStock
    from workbench.market import DirectBaoStock
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "前复权")
    data = load_holding_chart(store, report(store, aid)["positions"][0])
    cancel = threading.Event()
    provider = Mock()
    provider.__enter__ = Mock(return_value=provider)
    provider.__exit__ = Mock(return_value=False)
    provider.fetch_unadjusted.return_value = bars()
    with patch("workbench.holding_chart.BaoStock", return_value=provider):
        assert len(fetch_holding_reference(data, cancel)) == 3
    assert provider.cancel_event is cancel
    assert (provider.login_timeout, provider.query_timeout) == (8, 12)
    provider.fetch_unadjusted.assert_called_once_with(CODE, "2026-09-29", "2026-09-30")
    process = BaoStock()
    with patch.object(process, "_query") as query:
        process.fetch(CODE, "2026-09-29", "2026-09-30")
        assert query.call_args.args == ("fetch", [CODE, "2026-09-29", "2026-09-30"])
        process.fetch_unadjusted(CODE, "2026-09-29", "2026-09-30")
        assert query.call_args.args == ("fetch", [CODE, "2026-09-29", "2026-09-30", "3"])
    direct = DirectBaoStock()
    direct.bs = Mock()
    frame = bars().loc[lambda d:d.date.between('2026-09-29','2026-09-30')].assign(tradestatus="1", code=CODE, adjustflag="3")
    with patch.object(direct, "collect", return_value=frame):
        direct.fetch(CODE, "2026-09-29", "2026-09-30")
        assert direct.bs.query_history_k_data_plus.call_args.kwargs["adjustflag"] == "2"
        assert direct.bs.query_history_k_data_plus.call_args.args[1] == "date,open,high,low,close,volume,tradestatus,code"
        direct.fetch(CODE, "2026-09-29", "2026-09-30", "3")
        assert direct.bs.query_history_k_data_plus.call_args.kwargs["adjustflag"] == "3"
    for invalid in (frame.assign(code="sz.000001"), frame.assign(adjustflag="2")):
        with patch.object(direct, "collect", return_value=invalid), pytest.raises(ValueError):
            direct.fetch(CODE, "2026-09-29", "2026-09-30", "3")


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
@pytest.mark.parametrize("failed", [False, True])
def test_holding_reference_background_keeps_gui_responsive_and_fails_visibly(tmp_path, quote_window, failed):
    from gui.closed_trade_chart import HoldingTradeChartDialog
    store, aid, _pid = ledger(tmp_path)
    adjusted = bars()
    for key in ("open", "high", "low", "close"):
        adjusted[key] /= 2
    save_dataset(store, CODE, adjusted, "baostock", "前复权")
    before = database(store)
    entered, release = threading.Event(), threading.Event()
    def slow(data, cancel):
        entered.set()
        release.wait(3)
        if failed:
            raise TimeoutError("测试超时")
        return bars()
    root = quote_window
    with patch("workbench.holding_chart.fetch_holding_reference", new=slow):
        chart = HoldingTradeChartDialog(root, store, report(store, aid)["positions"][0])
        try:
            assert entered.wait(2) and chart.data["reference_needed"]
            idle = Mock()
            chart.top.after_idle(idle)
            root.update()
            idle.assert_called_once()
            release.set()
            response = chart._reference_results.get(timeout=2)
            chart._reference_results.put(response)
            chart.top.after_cancel(chart._reference_after)
            chart._reference_after = None
            chart._poll_reference()
            assert chart.top.title().startswith("持仓图")
            if failed:
                assert all(l["price"] is None for l in chart.data["levels"])
                assert "核验失败" in chart.note.cget("text") and "正在后台" not in chart.note.cget("text")
            else:
                assert [l["price"] for l in chart.data["levels"]] == pytest.approx([5.005, 4.5, 6, 6.5, 7])
                assert chart.data["events"][0]["price"] == 5
                assert "已核验" in chart.note.cget("text")
            assert database(store) == before
        finally:
            release.set()
            chart.close()
            assert chart._reference_cancel.is_set() and chart._reference_after is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Tk integration")
def test_closing_holding_chart_cancels_pending_reference_without_late_gui_write(tmp_path, quote_window):
    from gui.closed_trade_chart import HoldingTradeChartDialog
    store, aid, _pid = ledger(tmp_path)
    save_dataset(store, CODE, bars(), "baostock", "前复权")
    entered, release = threading.Event(), threading.Event()
    def slow(data, cancel):
        entered.set()
        release.wait(3)
        assert cancel.is_set()
        return bars()
    with patch("workbench.holding_chart.fetch_holding_reference", new=slow):
        chart = HoldingTradeChartDialog(quote_window, store, report(store, aid)["positions"][0])
        assert entered.wait(2)
        chart.close()
        assert chart._reference_cancel.is_set() and chart._reference_after is None
        release.set()
        chart._reference_results.get(timeout=2)
        quote_window.update()
