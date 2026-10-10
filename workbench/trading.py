"""Manual trading ledger calculations.

Only rows in ``executions`` are treated as real fills.  Observations, plans and
H2 opportunity states remain research/trader prompts until the user records a
broker-confirmed transaction.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .sources import market_source


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def latest_prices(store, codes):
    """Read the latest saved daily close for the requested open positions."""
    from workbench.market import load_dataset

    result = {}
    for code in sorted(set(codes)):
        rows = store.rows(
            "SELECT * FROM datasets WHERE source=? AND timeframe='daily' "
            "AND code=? ORDER BY end DESC,created DESC,rowid DESC LIMIT 1",
            (market_source(store),code),
        )
        if not rows:
            continue
        try:
            frame, record = load_dataset(store, rows[0]["id"])
            if not frame.empty:
                result[code] = {
                    "price": _number(frame.iloc[-1]["close"]),
                    "previous_close": (
                        _number(frame.iloc[-2]["close"]) if len(frame) > 1 else None
                    ),
                    "date": str(frame.iloc[-1]["date"]),
                    "dataset_id": record["id"],
                }
        except Exception:
            # A broken/missing snapshot must not prevent the trader from seeing
            # the rest of the account. The UI marks this row as lacking a quote.
            continue
    return result


def _snapshot(row):
    try:
        value = json.loads(row.get("plan_snapshot") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def account_report(store, account_id, price_map=None):
    """Return positions, risk and realised performance from actual fills."""
    accounts = store.rows("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not accounts:
        raise ValueError("交易账户不存在")
    account = accounts[0]
    rows = store.list_executions(account_id)
    books = {}
    realised_events = []
    cash = _number(account["initial_equity"])

    for row in rows:
        code = row["code"]
        book = books.setdefault(code, {
            "code": code, "quantity": 0, "cost": 0.0, "realized": 0.0,
            "fees": 0.0, "stop": None, "target": None, "strategy": "",
            "plan_id": None, "last_trade_time": "",
        })
        qty = int(row["quantity"])
        price = _number(row["price"])
        fee = _number(row["fee"])
        book["fees"] += fee
        book["last_trade_time"] = row["trade_time"]
        snap = _snapshot(row)
        if row["side"] == "BUY":
            book["cost"] += price * qty + fee
            book["quantity"] += qty
            cash -= price * qty + fee
            if snap:
                book["stop"] = snap.get("stop")
                book["target"] = snap.get("target")
                book["strategy"] = snap.get("strategy") or ""
                book["plan_id"] = snap.get("plan_id")
        else:
            before = book["quantity"]
            average = book["cost"] / before if before else 0.0
            removed_cost = average * qty
            profit = price * qty - fee - removed_cost
            book["quantity"] -= qty
            book["cost"] -= removed_cost
            book["realized"] += profit
            cash += price * qty - fee
            realised_events.append({**row, "profit": profit, "average_cost": average})
            if book["quantity"] == 0:
                book["cost"] = 0.0

    open_codes = [code for code, book in books.items() if book["quantity"] > 0]
    price_map = latest_prices(store, open_codes) if price_map is None else price_map
    positions = []
    market_value = unrealized = portfolio_risk = 0.0
    quote_dates = []
    for code in open_codes:
        book = books[code]
        qty = book["quantity"]
        average = book["cost"] / qty
        quote = price_map.get(code, {})
        price = _number(quote.get("price"), average)
        has_quote = bool(quote)
        value = price * qty
        floating = value - book["cost"]
        stop = _number(book.get("stop"), 0.0)
        risk = max(average - stop, 0.0) * qty if stop > 0 else 0.0
        market_value += value
        unrealized += floating
        portfolio_risk += risk
        if quote.get("date"):
            quote_dates.append(str(quote["date"]))
        positions.append({
            **book,
            "average_cost": average,
            "price": price,
            "has_quote": has_quote,
            "quote_date": quote.get("date", ""),
            "market_value": value,
            "unrealized": floating,
            "risk_amount": risk,
            "price_stop_pct": ((average - stop) / average * 100) if average and stop else None,
        })

    realised = sum(_number(book["realized"]) for book in books.values())
    equity = cash + market_value
    risk_pct = portfolio_risk / equity * 100 if equity > 0 else 0.0
    utilization_pct = market_value / equity * 100 if equity > 0 else 0.0
    wins = [e for e in realised_events if e["profit"] > 0]
    losses = [e for e in realised_events if e["profit"] < 0]
    gross_profit = sum(e["profit"] for e in wins)
    gross_loss = -sum(e["profit"] for e in losses)
    summary = {
        "initial_equity": _number(account["initial_equity"]),
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "realized": realised,
        "unrealized": unrealized,
        "portfolio_risk": portfolio_risk,
        "risk_pct": risk_pct,
        "utilization_pct": utilization_pct,
        "risk_limit_pct": _number(account["risk_limit_pct"]),
        "remaining_risk": max(equity * _number(account["risk_limit_pct"]) / 100 - portfolio_risk, 0.0),
        "closed_fills": len(realised_events),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(realised_events) * 100 if realised_events else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "quote_date": max(quote_dates) if quote_dates else "",
    }
    return {
        "account": account,
        "positions": sorted(positions, key=lambda item: item["market_value"], reverse=True),
        "executions": list(reversed(rows)),
        "realised_events": list(reversed(realised_events)),
        "summary": summary,
    }


def proposed_quantity(account, entry, stop, existing_risk=0.0):
    """Size a prospective long position without treating it as a holding."""
    entry = _number(entry)
    stop = _number(stop)
    equity = _number(account.get("initial_equity"))
    if not (0 < stop < entry and equity > 0):
        return 0
    per_trade = equity * _number(account.get("per_trade_risk_pct"), 1.0) / 100
    remaining = max(equity * _number(account.get("risk_limit_pct"), 3.0) / 100 - existing_risk, 0.0)
    by_risk = int(min(per_trade, remaining) / (entry - stop))
    by_value = int(equity * _number(account.get("max_position_pct"), 30.0) / 100 / entry)
    # A-share opening orders normally use board lots. Do not invent one lot if
    # the risk budget cannot afford it.
    return max(min(by_risk, by_value) // 100 * 100, 0)


def history_reconciliation(initial_equity, broker_total_assets=None,
                           closed_pnl=0.0, floating_pnl=0.0,
                           cash_adjustments=0.0):
    """双向计算历史建账资产；券商快照只校验，不覆盖用户确认的初始资金。"""
    initial = _number(initial_equity)
    closed = _number(closed_pnl)
    floating = _number(floating_pnl)
    cash = _number(cash_adjustments)
    system_total = initial + closed + floating + cash
    broker = None
    if broker_total_assets not in (None, ""):
        broker = _number(broker_total_assets)
    return {
        "system_total_assets": system_total,
        "broker_total_assets": broker,
        "implied_initial_equity": (
            broker - closed - floating - cash if broker is not None else None
        ),
        "reconciliation": broker - system_total if broker is not None else None,
        "cash_adjustments": cash,
    }


def management_report(store, account_id, price_map=None):
    """Build the broker-style current-position and closed-trade dashboard."""
    accounts = store.rows("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not accounts:
        raise ValueError("交易账户不存在")
    account = accounts[0]
    positions = store.rows(
        "SELECT * FROM positions WHERE account_id=? AND status='OPEN' ORDER BY updated DESC",
        (account_id,),
    )
    fills = store.rows(
        "SELECT f.* FROM position_fills f JOIN positions p ON p.id=f.position_id "
        "WHERE p.account_id=? ORDER BY f.trade_date,f.created,f.id",
        (account_id,),
    )
    by_position = {}
    for fill in fills:
        by_position.setdefault(fill["position_id"], []).append(fill)
    codes = [row["code"] for row in positions]
    price_map = latest_prices(store, codes) if price_map is None else price_map
    open_rows = []
    market_value = floating_total = risk_total = daily_pnl_total = 0.0
    daily_quote_count = 0
    quote_dates = []
    for position in positions:
        tx = by_position.get(position["id"], [])
        buys = [row for row in tx if row["side"] == "BUY"]
        sells = [row for row in tx if row["side"] == "SELL"]
        bought = sum(int(row["quantity"]) for row in buys)
        sold = sum(int(row["quantity"]) for row in sells)
        quantity = bought - sold
        if quantity <= 0:
            continue
        buy_cost = sum(_number(row["price"]) * row["quantity"] + _number(row["fees"]) for row in buys)
        sell_value = sum(_number(row["price"]) * row["quantity"] - _number(row["fees"]) for row in sells)
        net_invested = buy_cost - sell_value
        diluted_cost = net_invested / quantity
        quote = price_map.get(position["code"], {})
        current = _number(quote.get("price"), diluted_cost)
        previous_close = quote.get("previous_close")
        daily_pnl = None
        if previous_close not in (None, ""):
            daily_pnl = (current - _number(previous_close)) * quantity
            daily_pnl_total += daily_pnl
            daily_quote_count += 1
        value = current * quantity
        floating = value - net_invested
        stop = _number(position["stop"])
        risk_amount = max(diluted_cost - stop, 0.0) * quantity
        market_value += value
        floating_total += floating
        risk_total += risk_amount
        if quote.get("date"):
            quote_dates.append(str(quote["date"]))
        open_rows.append({
            **position,
            "buy_batches": [
                {"date": row["trade_date"], "price": row["price"],
                 "hands": int(row["quantity"]) // 100, "fees": _number(row["fees"])}
                for row in buys
            ],
            "quantity": quantity,
            "diluted_cost": diluted_cost,
            "current_price": current,
            "has_quote": bool(quote),
            "market_value": value,
            "floating_pnl": floating,
            "daily_pnl": daily_pnl,
            "distance_stop_pct": (current - stop) / current * 100 if current else 0.0,
            "trade_risk_pct": max(diluted_cost - stop, 0.0) / diluted_cost * 100 if diluted_cost > 0 else 0.0,
            "risk_amount": risk_amount,
            "first_buy_date": min((row["trade_date"] for row in buys), default=""),
        })
    closed = store.rows(
        "SELECT * FROM closed_trades WHERE account_id=? ORDER BY close_date DESC,created DESC,id DESC",
        (account_id,),
    )
    closed_pnl = sum(_number(row["pnl"]) for row in closed)
    cash_flows = store.list_cash_flows(account_id)
    cash_adjustments = sum(_number(row["amount"]) for row in cash_flows)
    snapshot = _number(account.get("current_total_assets"), 0.0)
    history = history_reconciliation(
        account["initial_equity"],
        snapshot if snapshot > 0 else None,
        closed_pnl,
        floating_total,
        cash_adjustments,
    )
    from .account_reconciliation import reconcile_account
    independent_check = reconcile_account(store, account)
    historical_equity = history["system_total_assets"]
    if account.get("accounting_mode") == "snapshot":
        total_assets = snapshot or historical_equity
    else:
        total_assets = historical_equity
    available_cash = total_assets - market_value
    for row in open_rows:
        row["allocation_pct"] = row["market_value"] / total_assets * 100 if total_assets else 0.0
        row["account_risk_pct"] = row["risk_amount"] / total_assets * 100 if total_assets else 0.0
    wins = [row for row in closed if _number(row["pnl"]) > 0]
    losses = [row for row in closed if _number(row["pnl"]) < 0]
    return {
        "account": account,
        "positions": open_rows,
        "closed": closed,
        "cash_flows": cash_flows,
        "summary": {
            "total_assets": total_assets,
            "market_value": market_value,
            "available_cash": available_cash,
            "withdrawable_cash": available_cash,
            "position_pct": market_value / total_assets * 100 if total_assets else 0.0,
            "floating_pnl": floating_total,
            "daily_pnl": daily_pnl_total if daily_quote_count else None,
            "asset_pnl": closed_pnl + floating_total,
            "closed_pnl": closed_pnl,
            "cash_adjustments": cash_adjustments,
            "risk_amount": risk_total,
            "risk_pct": risk_total / total_assets * 100 if total_assets else 0.0,
            "quote_date": max(quote_dates) if quote_dates else "",
            "historical_equity": historical_equity,
            "broker_total_assets": snapshot or None,
            **independent_check,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) * 100 if closed else 0.0,
        },
    }


def trading_dates(store, code=None, limit=180, today=None):
    """Ledger dates through today, independent of prices and individual bars.

    ``code`` remains accepted for existing callers; a stock's stale/missing
    price file must never decide which dates a real fill can be recorded on.
    Covered public calendars take precedence over published annual schedules.
    """
    from .exchange_calendar import is_trading_day
    from .sources import calendar_file

    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if limit <= 0:
        return []
    try:
        calendar = json.loads(calendar_file(store, market_source(store)).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        calendar = None
    dates = []
    # Bounded work even when an unpublished year has no verified calendar.
    # Unknown days are not assumed open; no price files or network are read.
    for offset in range(max(366, limit * 3)):
        candidate = today - timedelta(days=offset)
        if is_trading_day(candidate, calendar) is True:
            dates.append(candidate.isoformat())
            if len(dates) == limit:
                break
    return dates
