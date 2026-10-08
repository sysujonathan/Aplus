"""Read-only holding chart, real fills and explicitly converted price levels."""
import math

from .closed_chart import _ReadOnlySnapshots, closed_chart_events
from .market import load_dataset
from .holding_quotes import net_invested


def load_holding_chart(store, row, *, quote=None):
    positions = store.rows("SELECT * FROM positions WHERE id=? AND account_id=? AND status='OPEN'",
                           (row["id"], row["account_id"]))
    if not positions or positions[0]["code"] != row["code"]:
        raise ValueError("持仓已变化，请刷新后重试。")
    position = positions[0]
    fills = store.rows("SELECT * FROM position_fills WHERE position_id=? ORDER BY trade_date,created,id",
                       (position["id"],))
    quantity = sum((1 if f["side"] == "BUY" else -1) * f["quantity"] for f in fills)
    if not fills or quantity <= 0:
        raise ValueError("没有有效的在持仓成交链路。")
    snapshots = store.rows(
        "SELECT * FROM datasets WHERE code=? AND timeframe='daily' "
        "ORDER BY CASE WHEN adjustment IN ('不复权','none','3') THEN 0 ELSE 1 END,"
        "end DESC,created DESC,rowid DESC LIMIT 1", (position["code"],))
    if not snapshots:
        raise ValueError("本地没有该标的日线，请先在盘前任务更新行情。")
    frame, record = load_dataset(_ReadOnlySnapshots(store), snapshots[0]["id"])
    if frame.empty:
        raise ValueError("该标的日线为空，请先更新行情。")
    end = str(frame.iloc[-1]["date"])[:10]
    events, warnings = closed_chart_events(frame, {"close_date": end}, fills,
                                          adjustment=record["adjustment"])
    cost = net_invested(fills) / quantity
    levels = [{"label": "摊薄成本（含税费）", "value": cost, "color": "#f6c85f", "style": "-"},
              {"label": "止损 SL1", "value": position["stop"], "color": "#8fc7ff", "style": "-."}]
    for key in ("tp1", "tp2", "tp3"):
        if position.get(key) is not None:
            levels.append({"label": "止盈 " + key.upper(), "value": position[key],
                           "color": "#ceadff", "style": "--"})
    factor = 1.0 if record["adjustment"] in ("不复权", "none", "3") else None
    # Only a verified same-date unadjusted price may anchor an adjusted axis.
    if factor is None and quote and not quote.get("adjusted") and quote.get("date") == end:
        raw = quote.get("price")
        if raw is not None and math.isfinite(float(raw)) and float(raw) > 0:
            factor = float(frame.iloc[-1]["close"]) / float(raw)
            warnings.append("水平线按末日同日不复权价格换算到复权坐标；文字与列表仍为原始金额。")
    if factor is None:
        warnings.append("缺少与图表末日对应的不复权价格，暂不画成本／止盈止损线，避免错位；请补充不复权日线或刷新同日行情。")
    for level in levels:
        level["price"] = level["value"] * factor if factor is not None else None
    warnings.append("当前成本与止盈止损为已保存持仓的参考水平，不代表历史每天的成本或自动下单。")
    return {"frame": frame, "dataset": record, "events": events, "warnings": warnings,
            "code": position["code"], "name": position["name"], "estimated": False,
            "holding": True, "levels": levels, "close_date": end}
