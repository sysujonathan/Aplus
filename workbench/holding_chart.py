"""Read-only holding chart, real fills and explicitly converted price levels."""
import math
from datetime import datetime

from .closed_chart import _ReadOnlySnapshots, closed_chart_events
from .market import BaoStock, load_dataset, validate_bars
from .holding_quotes import net_invested
from .sources import market_source


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
        "SELECT * FROM datasets WHERE code=? AND timeframe='daily' AND source IN (?,'csv','legacy-engine-a') "
        "ORDER BY CASE WHEN source=? THEN 0 ELSE 1 END,end DESC, CASE WHEN adjustment IN ('不复权','none','3') THEN 0 ELSE 1 END,"
        "created DESC,rowid DESC LIMIT 1", (position["code"],market_source(store),market_source(store)))
    if not snapshots:
        raise ValueError("本地没有该标的日线，请先在盘前任务更新行情。")
    frame, record = load_dataset(_ReadOnlySnapshots(store), snapshots[0]["id"])
    if frame.empty:
        raise ValueError("该标的日线为空，请先更新行情。")
    # Same EMA20 definition as the premarket base chart, no strategy rescan.
    frame["ema20"] = frame.close.ewm(span=20, adjust=False).mean()
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
    if factor is None and _is_close_reference(quote, end):
        raw = quote.get("price")
        if raw is not None and math.isfinite(float(raw)) and float(raw) > 0:
            factor = float(frame.iloc[-1]["close"]) / float(raw)
            warnings.append("水平线按末日同日不复权价格换算到复权坐标；文字与列表仍为原始金额。")
    if factor is None:
        warnings.append("缺少对应的不复权收盘价；正在后台核验水平线坐标。")
    for level in levels:
        level["price"] = level["value"] * factor if factor is not None else None
    warnings.append("当前成本与止盈止损为已保存持仓的参考水平，不代表历史每天的成本或自动下单。")
    return {"frame": frame, "dataset": record, "events": events, "warnings": warnings,
            "code": position["code"], "name": position["name"], "estimated": False,
            "holding": True, "levels": levels, "close_date": end,
            "reference_needed": factor is None}


def _is_close_reference(quote, end):
    if not quote or quote.get("adjusted") or quote.get("date") != end:
        return False
    if quote.get("source") == "history":
        return True
    try:
        stamp = datetime.fromisoformat(quote["quote_time"])
        return (quote.get("source") == "sina" and stamp.date().isoformat() == end
                and (stamp.hour, stamp.minute) >= (15, 0))
    except (KeyError, ValueError, TypeError):
        return False


def fetch_holding_reference(data, cancel_event):
    """Only download this security's raw reference prices; never touch Store."""
    end = data["close_date"]
    start = min((e["date"] for e in data["events"] if e["price"] is not None), default=end)
    if data.get('dataset',{}).get('source')=='tickflow':
        from .tickflow import TickFlow
        provider = TickFlow()
    else:
        provider = BaoStock()
    provider.cancel_event = cancel_event
    provider.login_timeout, provider.query_timeout = 8, 12
    with provider:
        raw = provider.fetch_unadjusted(data["code"], start, end)
    return validate_bars(raw)


def apply_holding_reference(data, raw):
    """Convert each reference using matching dates, leaving the ledger untouched."""
    raw = validate_bars(raw)
    end = data["close_date"]
    closes = dict(zip(raw.date, raw.close))
    if end not in closes:
        raise ValueError("未返回图表末日的不复权收盘价，暂不能核验水平线")
    if (raw.date > end).any():
        raise ValueError("不复权参考包含图表末日之后的行情")
    adjusted = dict(zip(data["frame"].date, data["frame"].close))
    factor = float(adjusted[end]) / float(closes[end])
    result = dict(data)
    result["levels"] = [{**level, "price": level["value"] * factor} for level in data["levels"]]
    result["events"] = [dict(event) for event in data["events"]]
    for event in result["events"]:
        if event["price"] is not None and event["date"] in closes:
            event["price"] = event["execution_price"] * adjusted[event["date"]] / closes[event["date"]]
    result["warnings"] = [w for w in data["warnings"] if not w.startswith((
        "缺少对应的不复权收盘价", "复权行情："))]
    result["warnings"].insert(0, "已核验不复权收盘价：水平线按图表末日换算，买点按各成交日换算；文字及列表保留原始价格。")
    if any(e["price"] is not None and e["date"] not in closes for e in result["events"]):
        result["warnings"].insert(1, "部分成交日缺少不复权参考，相关箭头仍按当日收盘定位。")
    result["reference_needed"] = False
    return result
