"""Account daily P&L, distinct from a closed trade's lifetime P&L.

Broker calendar observations are authoritative. Local calculations use saved
historical prices and confirmed fills; summary-only historical trades cannot
reconstruct the missing daily holdings.
"""
from __future__ import annotations

import math
import re
from datetime import date

def calculate_daily_pnl(fills, quotes, asof):
    """Opening inventory movement + same-day trade cash/stock changes - fees.

    None means incomplete inputs, never zero. Selling all stock still contributes
    the move from yesterday's close to the sale price.
    """
    books = {}
    for fill in fills:
        day = str(fill["trade_date"])[:10]
        if day > asof:
            continue
        code = fill["code"]
        book = books.setdefault(code, {"opening": 0, "trades": []})
        qty = int(fill["quantity"])
        direction = 1 if fill["side"] == "BUY" else -1
        if day < asof:
            book["opening"] += direction * qty
        else:
            book["trades"].append(fill)
    total = 0.0
    for code, book in books.items():
        opening, trades = book["opening"], book["trades"]
        if opening < 0:
            return None
        if not opening and not trades:
            continue
        quote = quotes.get(code, {})
        ending = opening + sum(
            int(t["quantity"]) * (1 if t["side"] == "BUY" else -1) for t in trades
        )
        if ending < 0 or quote.get("date", "")[:10] != asof:
            return None
        close, previous = quote.get("price"), quote.get("previous_close")
        if (ending and (close is None or not math.isfinite(float(close)))) or (
            opening and (previous is None or not math.isfinite(float(previous)))
        ):
            return None
        total += ending * float(close or 0) - opening * float(previous or 0)
        for trade in trades:
            value = float(trade["price"]) * int(trade["quantity"])
            total += value if trade["side"] == "SELL" else -value
            total -= float(trade.get("fees") or 0)
    return round(total, 2)


def historical_daily_returns(store, account_id):
    """Revalue confirmed historical inventory with saved daily market prices.

    No quantities are inferred from rounded percentages/holding-days summaries.
    A missing quote for any held stock makes that entire day unavailable.
    Local forward-adjusted histories are estimates; broker records take priority.
    """
    from .market import load_dataset

    fills = store.rows(
        "SELECT f.*,p.code FROM position_fills f JOIN positions p ON p.id=f.position_id "
        "WHERE p.account_id=? ORDER BY f.trade_date,f.created,f.id", (account_id,),
    )
    if not fills:
        return []
    first_day = min(f["trade_date"][:10] for f in fills)
    manual = store.rows("SELECT MAX(close_date) AS last_day FROM closed_trades "
                        "WHERE account_id=? AND position_id IS NULL", (account_id,))[0]["last_day"]
    by_day = {}
    for code in sorted({f["code"] for f in fills}):
        snapshots = store.rows(
            "SELECT id FROM datasets WHERE code=? AND source='baostock' AND timeframe='daily' "
            "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code,),
        )
        if not snapshots:
            continue
        try:
            frame, _record = load_dataset(store, snapshots[0]["id"])
        except (ValueError, OSError):
            continue
        previous = None
        for bar in frame.itertuples(index=False):
            day = str(bar.date)[:10]
            if day >= first_day and (not manual or day > manual[:10]):
                by_day.setdefault(day, {})[code] = {
                    "price": float(bar.close), "previous_close": previous, "date": day,
                }
            previous = float(bar.close)
    flows = store.list_cash_flows(account_id)
    rows = []
    for day, quotes in sorted(by_day.items()):
        pnl = calculate_daily_pnl(fills, quotes, day)
        if pnl is None:
            continue
        for flow in flows:
            if flow["flow_date"][:10] == day and not any(
                word in flow["category"] for word in ("转入", "转出", "入金", "出金", "转账")
            ):
                pnl += float(flow["amount"])
        rows.append({"date": day, "pnl": round(pnl, 2), "source": "local", "status": "recorded"})
    return rows


def parse_calendar_text(text):
    """Editable import: ISO date followed by P&L or explicit 休市."""
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[\s,，]+(.+)", line)
        if not match:
            raise ValueError(f"第 {line_number} 行请填写：日期 盈亏（或休市）")
        day, amount = match.groups()
        date.fromisoformat(day)
        amount = amount.strip().replace(",", "").replace("−", "-")
        if amount == "休市":
            rows.append({"date": day, "pnl": None, "status": "closed", "source": "broker"})
        else:
            value = float(amount)
            if not math.isfinite(value):
                raise ValueError(f"第 {line_number} 行金额必须是有效数字")
            rows.append({"date": day, "pnl": round(value, 2), "status": "recorded", "source": "broker"})
    if not rows:
        raise ValueError("请至少填写一天或粘贴一张券商日历截图")
    by_day = {}
    for row in rows:
        if row["date"] in by_day and row != by_day[row["date"]]:
            raise ValueError(f"{row['date']} 存在冲突金额，请核对")
        by_day[row["date"]] = row
    return sorted(by_day.values(), key=lambda r: r["date"])


def parse_calendar_tokens(tokens):
    """Read the five-column Ping An monthly calendar by cell coordinates."""
    month = None
    headers = {}
    for token in tokens:
        text = token.text.replace(" ", "")
        match = re.search(r"(20\d{2})年(\d{1,2})月", text)
        if match:
            month = (int(match[1]), int(match[2]))
        for index, label in enumerate(("周一", "周二", "周三", "周四", "周五")):
            if text == label:
                headers[index] = token
    if month is None or len(headers) != 5:
        raise ValueError("未识别出年份、月份或周一至周五表头，请使用完整月日历截图")
    year, month_number = month
    dates, amounts = [], []
    for token in tokens:
        text = token.text.replace(" ", "").replace(",", "").replace("−", "-")
        column = min(headers, key=lambda c: abs(headers[c].x - token.x))
        if token.y <= headers[column].y:
            continue
        if re.fullmatch(r"\d{1,2}", text) and 1 <= int(text) <= 31:
            try:
                day = date(year, month_number, int(text))
            except ValueError:
                continue
            if day.weekday() == column:
                dates.append((token, column, day))
        elif text == "休市" or re.fullmatch(r"[+\-]?\d+\.\d{2}", text):
            amounts.append((token, column, text))
    rows = []
    for token, column, day in dates:
        next_y = min((t.y for t, c, _ in dates if c == column and t.y > token.y), default=float("inf"))
        candidates = [(t, v) for t, c, v in amounts if c == column and token.y < t.y < next_y]
        if len(candidates) != 1:
            raise ValueError(f"{day.isoformat()} 金额识别不完整，请使用清晰的完整截图")
        rows.append(f"{day.isoformat()} {candidates[0][1]}")
    return parse_calendar_text("\n".join(rows))


def recognize_calendar_screenshots(images):
    from .closed_import import MAX_SCREENSHOTS, recognize_screenshot

    if not 1 <= len(images) <= MAX_SCREENSHOTS:
        raise ValueError(f"一次最多 {MAX_SCREENSHOTS} 张截图")
    rows = []
    for image in images:
        rows.extend(parse_calendar_tokens(recognize_screenshot(image)))
    return parse_calendar_text("\n".join(
        f"{r['date']} {r['pnl'] if r['status'] == 'recorded' else '休市'}" for r in rows
    ))
