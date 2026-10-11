import json
from pathlib import Path

import pytest

from gui.data import StockNameLookup
from workbench.closed_import import OcrToken, parse_broker_tokens
from workbench.store import Store, dumps, now
from workbench.trading import (
    account_report,
    history_reconciliation,
    management_report,
    proposed_quantity,
)


def make_plan(store, state="计划交易", pending_state="PENDING"):
    store.execute(
        "INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("market", "sh.600000", "daily", "baostock", "前复权", "2026-01-01",
         "2026-09-30", 180, "market/missing.csv", "hash", now()),
    )
    payload = {
        "entry": 10.0, "stop": 9.0, "target": 12.0,
        "pending_state": pending_state, "plan_version": "h2-next-session-v1",
    }
    store.execute(
        "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("obs", "scan", "sh.600000", "STRATEGY_GAP_H2", "1.0", "daily",
         "2026-09-30", "2026-09-30", "market", dumps(payload), now()),
    )
    return store.save_plan("obs", state, 10.0, 9.0, 12.0, 1000, "次日计划")


def test_v4_upgrade_preserves_plans_and_adds_manual_ledger(tmp_path):
    store = Store(tmp_path)
    plan_id = make_plan(store)
    store.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    reopened = Store(tmp_path)
    assert reopened.rows("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "8"
    assert reopened.rows("SELECT id FROM plans")[0]["id"] == plan_id
    assert reopened.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'")
    assert reopened.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='executions'")
    assert reopened.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'")
    assert reopened.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='closed_trades'")
    assert reopened.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='cash_flows'")


def test_signal_trigger_does_not_create_position_or_execution(tmp_path):
    store = Store(tmp_path)
    make_plan(store, pending_state="TRIGGERED")
    account_id = store.save_account("主账户", 100000)
    report = account_report(store, account_id, price_map={})
    assert report["positions"] == []
    assert report["executions"] == []
    assert store.rows("SELECT state FROM plans")[0]["state"] == "计划交易"


def test_manual_buy_freezes_plan_and_partial_then_full_sell(tmp_path):
    store = Store(tmp_path)
    plan_id = make_plan(store)
    account_id = store.save_account("主账户", 100000, risk_limit_pct=3, per_trade_risk_pct=1)
    buy_id = store.record_execution(
        account_id, "sh.600000", "BUY", "2026-10-01 09:31", 10.0, 1000,
        fee=5.0, reason="券商成交确认", plan_id=plan_id,
    )
    assert store.rows("SELECT state FROM plans WHERE id=?", (plan_id,))[0]["state"] == "已手工入场"
    before = json.loads(store.rows("SELECT plan_snapshot FROM executions WHERE id=?", (buy_id,))[0]["plan_snapshot"])
    assert before["stop"] == 9.0 and before["strategy"] == "STRATEGY_GAP_H2"

    # Later plan edits do not rewrite the snapshot attached to the fill.
    store.save_plan("obs", "已手工入场", 10.5, 9.5, 12.5, 1000, "调整观察")
    frozen = json.loads(store.rows("SELECT plan_snapshot FROM executions WHERE id=?", (buy_id,))[0]["plan_snapshot"])
    assert frozen["entry"] == 10.0 and frozen["stop"] == 9.0

    store.record_execution(
        account_id, "sh.600000", "SELL", "2026-10-02 10:00", 11.0, 400,
        fee=3.0, reason="部分止盈", plan_id=plan_id,
    )
    assert store.rows("SELECT state FROM plans WHERE id=?", (plan_id,))[0]["state"] == "已手工入场"
    partial = account_report(store, account_id, price_map={"sh.600000": {"price": 11.0, "date": "2026-10-02"}})
    assert partial["positions"][0]["quantity"] == 600
    assert partial["summary"]["realized"] == pytest.approx(395.0)
    assert partial["positions"][0]["risk_amount"] == pytest.approx(603.0)

    with pytest.raises(ValueError, match="超过当前实际持仓"):
        store.record_execution(account_id, "sh.600000", "SELL", "2026-10-02 10:01", 11, 601)

    store.record_execution(
        account_id, "sh.600000", "SELL", "2026-10-02 14:50", 9.5, 600,
        fee=3.0, reason="退出", plan_id=plan_id,
    )
    final = account_report(store, account_id, price_map={})
    assert final["positions"] == []
    assert final["summary"]["realized"] == pytest.approx(89.0)
    assert store.rows("SELECT state FROM plans WHERE id=?", (plan_id,))[0]["state"] == "已手工退出"


def test_risk_math_never_counts_stop_above_cost_as_new_loss(tmp_path):
    store = Store(tmp_path)
    plan_id = make_plan(store)
    account_id = store.save_account("主账户", 100000)
    store.record_execution(account_id, "sh.600000", "BUY", "2026-10-01", 10.0, 1000,
                           plan_id=plan_id)
    execution = store.rows("SELECT * FROM executions")[0]
    snapshot = json.loads(execution["plan_snapshot"])
    snapshot["stop"] = 10.5
    store.execute("UPDATE executions SET plan_snapshot=? WHERE id=?", (dumps(snapshot), execution["id"]))
    report = account_report(store, account_id, price_map={"sh.600000": {"price": 11, "date": "2026-10-02"}})
    assert report["positions"][0]["risk_amount"] == 0
    assert proposed_quantity(report["account"], 10, 9, existing_risk=0) == 1000


def test_desktop_replaces_other_tools_with_single_trade_management_page():
    source = (Path(__file__).parents[1] / "gui" / "main_window.py").read_text(encoding="utf-8")
    page = (Path(__file__).parents[1] / "gui" / "trade_management.py").read_text(encoding="utf-8")
    assert '("other", "交易管理")' in source
    assert 'TradeManagementFrame(self.page_host, self.store)' in source
    assert '其他工具' not in source
    assert 'self.rowconfigure(1, weight=1, minsize=210)' in page
    assert 'self.rowconfigure(2, weight=3, minsize=630)' in page
    for title in ("持仓管理", "资金账户", "已清仓", "盈亏日历"):
        assert title in page
    assert "拟建仓速算" in page
    assert 'style="PositionSell.primary.Outline.TButton"' in page
    assert 'anchor=tk.CENTER' in page
    assert 'padding=(4, 0, 4, 2)' in page
    assert '"市值(元)"' not in page
    assert '"盈亏(元)"' not in page
    assert 'rowheight=68' in page
    assert 'label="粘贴截图（最多 6 张）"' in page
    assert 'text="＋ 增加五行"' in page
    assert 'command=lambda: self._add_rows(5)' in page
    assert 'text="＋ 增加一行"' not in page
    assert "0 笔 · 默认按最近清仓日期排列" not in page
    assert page.count('state="readonly"') >= 3
    assert 'ttk.Label(header, text="交易管理"' not in page
    assert 'label="新增账户"' in page
    assert '"资金账号"' in page
    assert 'text="👁 隐藏"' in page
    assert 'command=self.reload_data' in page
    assert 'initial = summary["implied_initial_equity"]' in page
    assert 'self.funds_reconciliation.set("初始资金（推算） —")' in page
    assert 'f"持仓 {summary[\'floating_pnl\']:+,.2f}＋资金调整' not in page
    assert 'text="税费合计"' in page
    assert 'value="上月"' in page and 'value="下月"' in page
    assert '"上年" if yearly else "上月"' in page
    assert 'while len(weeks) < 6' in page
    assert 'for column, widget in enumerate(widgets):' in page
    assert "tree._measure_font.measure(str(tree.set(iid, column)))" in page


def test_stock_name_lookup_resolves_code_chinese_and_pinyin_initials():
    lookup = StockNameLookup({
        "sz.000703": "恒逸石化",
        "sh.600000": "浦发银行",
    })
    assert lookup.resolve("000703") == ("sz.000703", "恒逸石化")
    assert lookup.resolve("恒逸石化") == ("sz.000703", "恒逸石化")
    assert lookup.resolve("hysh") == ("sz.000703", "恒逸石化")


def test_snapshot_account_batches_partial_sale_and_auto_close(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account(
        "平安证券", 100000, accounting_mode="snapshot", current_total_assets=100000
    )
    position_id = store.save_position(
        account_id, "sh.600000", "浦发银行", 11, 9, 14,
        [{"date": "2026-09-28", "price": 10.0, "hands": 1, "fees": 3.0},
         {"date": "2026-09-29", "price": 12.0, "hands": 1, "fees": 2.0}],
        tp2=15, tp3=16,
    )
    report = management_report(
        store, account_id, {"sh.600000": {"price": 13.0, "date": "2026-09-30"}}
    )
    holding = report["positions"][0]
    assert holding["quantity"] == 200
    assert holding["diluted_cost"] == pytest.approx(11.025)
    assert holding["market_value"] == pytest.approx(2600)
    assert holding["floating_pnl"] == pytest.approx(395)
    assert report["summary"]["available_cash"] == pytest.approx(97400)

    store.sell_position(
        position_id, [{"date": "2026-09-30", "price": 14.0, "hands": 1}], 5.0
    )
    partial = management_report(
        store, account_id, {"sh.600000": {"price": 13.0, "date": "2026-09-30"}}
    )["positions"][0]
    assert partial["quantity"] == 100
    assert partial["diluted_cost"] == pytest.approx(8.10)
    assert partial["floating_pnl"] == pytest.approx(490)
    assert partial["risk_amount"] == 0

    with pytest.raises(ValueError, match="超过当前持仓"):
        store.sell_position(position_id, [{"date": "2026-10-01", "price": 13, "hands": 2}], 0)
    store.sell_position(position_id, [{"date": "2026-10-01", "price": 13, "hands": 1}], 0)
    final = management_report(store, account_id, {})
    assert final["positions"] == []
    assert len(final["closed"]) == 1
    assert final["closed"][0]["pnl"] == pytest.approx(490)
    assert final["closed"][0]["close_date"] == "2026-10-01"


def test_history_mode_manual_close_reconciliation_edit_and_delete(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account(
        "历史账户", 100000, accounting_mode="history", current_total_assets=100550
    )
    from workbench.account_reconciliation import capture_reference
    store.save_account_reconciliation(account_id, capture_reference(store,account_id,100550,'2026-09-30'))
    closed_id = store.save_closed_trade(
        account_id, "sz.000001", "平安银行", "2026-09-30", 20, 500, 5.0
    )
    report = management_report(store, account_id, {})
    assert report["summary"]["historical_equity"] == pytest.approx(100500)
    assert report["summary"]["reconciliation"] == pytest.approx(50)
    assert report["summary"]["implied_initial_equity"] == pytest.approx(100050)
    store.save_closed_trade(
        account_id, "sz.000001", "平安银行", "2026-09-30", 21, 550, 5.5,
        closed_id=closed_id,
    )
    edited = management_report(store, account_id, {})
    assert edited["summary"]["closed_pnl"] == 550
    assert edited["summary"]["historical_equity"] == pytest.approx(100550)
    assert edited["summary"]["implied_initial_equity"] == pytest.approx(100000)
    assert edited["summary"]["reconciliation"] == pytest.approx(0)
    store.delete_closed_trade(closed_id)
    assert management_report(store, account_id, {})["closed"] == []


def test_history_reconciliation_keeps_manual_initial_and_reverse_value_independent():
    result = history_reconciliation(
        50000,
        broker_total_assets=42261.94,
        closed_pnl=-7000,
        floating_pnl=-738.06,
    )

    assert result["system_total_assets"] == pytest.approx(42261.94)
    assert result["implied_initial_equity"] == pytest.approx(50000)
    assert result["reconciliation"] == pytest.approx(0)


def test_cash_adjustments_are_part_of_reconciliation_and_can_be_managed(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account(
        "历史账户", 50000, accounting_mode="history", current_total_assets=50001.53
    )
    from workbench.account_reconciliation import capture_reference
    store.save_account_reconciliation(account_id, capture_reference(store,account_id,50001.53,'2026-09-30'))
    flow_id = store.save_cash_flow(account_id, "2026-09-21", "利息归本", 1.53, "季度结息")
    report = management_report(store, account_id, {})
    assert report["summary"]["cash_adjustments"] == pytest.approx(1.53)
    assert report["summary"]["historical_equity"] == pytest.approx(50001.53)
    assert report["summary"]["reconciliation"] == pytest.approx(0)
    store.save_cash_flow(account_id, "2026-09-21", "其他支出", 0.53, flow_id=flow_id)
    assert management_report(store, account_id, {})["summary"]["cash_adjustments"] == pytest.approx(-0.53)
    store.delete_cash_flow(flow_id)
    assert store.list_cash_flows(account_id) == []


def test_account_number_custom_adjustment_and_daily_pnl(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account(
        "主账户", 50000, accounting_mode="history", broker_account_no="309812342552"
    )
    assert store.list_accounts()[0]["broker_account_no"] == "309812342552"
    store.save_cash_flow(account_id, "2026-10-01", "手续费返还", 2.18, "自定义类别")
    position_id = store.save_position(
        account_id, "sz.000001", "平安银行", 10, 9, 12,
        [{"date": "2026-10-01", "price": 10, "hands": 1, "fees": 0}],
    )
    report = management_report(
        store, account_id,
        {"sz.000001": {"price": 10.5, "previous_close": 10.2, "date": "2026-10-02"}},
    )
    assert report["summary"]["daily_pnl"] == pytest.approx(30)
    assert report["summary"]["cash_adjustments"] == pytest.approx(2.18)
    assert report["positions"][0]["id"] == position_id


def test_broker_screenshot_tokens_parse_and_overlap_deduplicate():
    tokens = [
        OcrToken("华阳股份", 50, 100),
        OcrToken("20260929清仓", 50, 145),
        OcrToken("9", 460, 102),
        OcrToken("-752.11", 680, 102),
        OcrToken("-4.61%", 890, 102),
        OcrToken("华阳股份", 50, 300),
        OcrToken("20260929清仓", 50, 345),
        OcrToken("9", 460, 302),
        OcrToken("-752.11", 680, 302),
        OcrToken("-4.61%", 890, 302),
        OcrToken("202609清仓次数10，清仓盈利-5,224.85", 50, 40),
    ]
    records, summaries, warnings = parse_broker_tokens(tokens)
    assert warnings == []
    assert records == [{
        "code": "", "name": "华阳股份", "close_date": "2026-09-29",
        "holding_days": 9, "pnl": -752.11, "return_pct": -4.61,
        "notes": "截图识别导入",
    }]
    assert summaries == [{"month": "202609", "count": 10, "pnl": -5224.85}]


def test_history_account_ui_explains_two_way_reconciliation():
    source = (Path(__file__).resolve().parents[1] / "gui" / "trade_management.py").read_text(
        encoding="utf-8"
    )
    for text in (
        "系统账面总资产",
        "券商资产快照",
        "反推开户资金",
        "采用反推值",
        "不会自动覆盖初始资金",
    ):
        assert text in source


def test_batch_closed_trade_import_is_atomic_and_keeps_recent_first(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account("历史账户", 100000, accounting_mode="history")
    store.save_closed_trades_batch(account_id, [
        {"code": "sh.600000", "name": "浦发银行", "close_date": "2026-09-29",
         "holding_days": 8, "pnl": 120.0, "return_pct": 2.1},
        {"code": "sz.000001", "name": "平安银行", "close_date": "2026-09-30",
         "holding_days": 12, "pnl": -30.0, "return_pct": -0.5},
    ])
    closed = management_report(store, account_id, {})["closed"]
    assert [row["code"] for row in closed] == ["sz.000001", "sh.600000"]
    assert sum(row["pnl"] for row in closed) == pytest.approx(90)

    with pytest.raises(ValueError, match="第 2 行"):
        store.save_closed_trades_batch(account_id, [
            {"code": "sh.600001", "name": "邯郸钢铁", "close_date": "2026-10-01",
             "holding_days": 2, "pnl": 10, "return_pct": 1},
            {"code": "", "name": "", "close_date": "2026-10-01",
             "holding_days": 2, "pnl": 10, "return_pct": 1},
        ])
    assert len(management_report(store, account_id, {})["closed"]) == 2


def test_position_can_be_edited_and_deleted_without_touching_market_data(tmp_path):
    store = Store(tmp_path)
    account_id = store.save_account("快照账户", 80000)
    position_id = store.save_position(
        account_id, "sh.600000", "浦发银行", 10, 9, 12,
        [{"date": "2026-09-29", "price": 10, "hands": 1}],
    )
    store.save_position(
        account_id, "sh.600000", "浦发银行", 10.2, 9.2, 12.5,
        [{"date": "2026-09-29", "price": 10, "hands": 1},
         {"date": "2026-09-30", "price": 10.4, "hands": 1}],
        position_id=position_id,
    )
    report = management_report(
        store, account_id, {"sh.600000": {"price": 10.5, "date": "2026-09-30"}}
    )
    assert report["positions"][0]["quantity"] == 200
    assert report["positions"][0]["stop"] == 9.2
    store.delete_position(position_id)
    assert management_report(store, account_id, {})["positions"] == []
