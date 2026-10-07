"""Read-only chart references for closed trades; never reconstruct ledger fills."""
from __future__ import annotations

from datetime import date
import math

from .market import code_of, load_dataset


class _ReadOnlySnapshots:
    """Keep load_dataset validation, without its Store read-audit write."""
    def __init__(self, store):
        self.root, self.rows = store.root, store.rows

    def read_artifact(self, relative, job=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("不允许越过运行目录读取文件")
        return path.read_bytes()


def closed_chart_events(frame, row, fills=(), *, adjustment=""):
    """Use confirmed fills, or approximate inclusive trading-bar holding days.

    Summary-only records contain no quantity or execution price. Their marks
    deliberately use closing prices, not a fabricated return/price inversion.
    """
    dates = frame.date.astype(str).str[:10].tolist()
    closes = dict(zip(dates, frame.close.astype(float)))
    close_date = date.fromisoformat(str(row["close_date"])).isoformat()
    warnings, events = [], []
    if fills:
        unadjusted = adjustment in ("不复权", "none", "3")
        if not unadjusted:
            warnings.append("复权行情：箭头定位当日收盘，列表保留原始成交价；两者不可直接比较。")
        for fill in fills:
            day = str(fill["trade_date"])[:10]
            if fill["side"] not in ("BUY", "SELL"):
                raise ValueError("原始成交方向无效，请核对记录。")
            price = float(fill["price"])
            if not math.isfinite(price) or price <= 0:
                raise ValueError("原始成交价格无效，请核对记录。")
            buy = fill["side"] == "BUY"
            located = day in closes
            if not located:
                warnings.append(f"{day} 缺少该标的 K 线；该笔只列成交，不移动到其他日期。")
            events.append({
                "date": day, "kind": "fill" if buy else "exit",
                "label": "买入" if buy else "卖出", "estimated": False,
                "price": (price if unadjusted else closes[day]) if located else None,
                "execution_price": price, "quantity": fill["quantity"],
                "fees": fill["fees"],
            })
        return events, warnings
    if row.get("position_id"):
        raise ValueError("原始成交链路缺失，不能用推算替代；请核对持仓记录。")
    days = int(row["holding_days"])
    if days < 0:
        raise ValueError("持仓天数不能为负数。")
    if close_date not in closes:
        raise ValueError("清仓日没有该标的 K 线，请先更新行情或核对日期；不会移动到其他日期。")
    end = dates.index(close_date)
    start = end - max(days - 1, 0)
    if start < 0:
        raise ValueError("历史行情不足以倒推建仓日，请补充更早的行情。")
    for day, buy in ((dates[start], True), (close_date, False)):
        events.append({
            "date": day, "kind": "fill" if buy else "exit",
            "label": "推算买入" if buy else "推算卖出", "estimated": True,
            "price": closes[day], "execution_price": None, "quantity": None, "fees": None,
        })
    warnings.append("按持仓天数倒数 K 线（含买卖两端），以当日收盘价定位；不是成交价。"
                    "停牌、不同计日口径或分批交易可能使建仓日偏差；不反推股数，不写回账本。")
    return events, warnings


def load_closed_chart(store, row):
    """Only read immutable daily snapshots and this record's own position fills."""
    code = code_of(row["code"])
    close_date = date.fromisoformat(str(row["close_date"])).isoformat()
    fills = []
    if row.get("position_id"):
        positions = store.rows("SELECT * FROM positions WHERE id=?", (row["position_id"],))
        if not positions or positions[0]["code"] != code or positions[0]["account_id"] != row.get("account_id"):
            raise ValueError("清仓与原持仓关联不一致，请核对记录。")
        fills = store.rows("SELECT * FROM position_fills WHERE position_id=? "
                           "ORDER BY trade_date,created,id", (row["position_id"],))
        if not fills or not any(f["side"] == "BUY" for f in fills) or not any(f["side"] == "SELL" for f in fills):
            raise ValueError("原始买卖链路不完整，请核对持仓记录。")
    first = min((f["trade_date"] for f in fills), default=close_date)
    snapshots = store.rows(
        "SELECT * FROM datasets WHERE code=? AND timeframe='daily' AND start<=? AND end>=? "
        "ORDER BY CASE WHEN adjustment='不复权' THEN 0 ELSE 1 END,end DESC,created DESC,rowid DESC",
        (code, first, close_date),
    )
    if not snapshots:
        raise ValueError("本地没有覆盖清仓日期的日线行情，请先在盘前任务更新该标的行情。")
    # Prefer a snapshot with enough history, but never silently skip a failed
    # integrity check. Both engine and chart retain the source snapshot identity.
    for snapshot in snapshots:
        frame, record = load_dataset(_ReadOnlySnapshots(store), snapshot["id"])
        if fills or (frame.date.le(close_date).sum() >= max(int(row["holding_days"]), 1)):
            break
    events, warnings = closed_chart_events(frame, row, fills, adjustment=record["adjustment"])
    return {"frame": frame, "dataset": record, "events": events, "warnings": warnings,
            "code": code, "name": row.get("name", ""), "estimated": not bool(fills)}
