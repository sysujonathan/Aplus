"""JSON boundary between the local TradingView-style page and Aplus' Store."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

import pandas as pd

from workbench.chart_workspace import create_alert, identity, validate_document


def _native(value):
    """Return JSON-safe values without leaking pandas/numpy implementation types."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _native(value.item())
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    return str(value)


def _bars(frame):
    required = ("date", "open", "high", "low", "close", "volume")
    missing = [key for key in required if key not in frame]
    if missing:
        raise ValueError("图表行情缺少字段：" + "、".join(missing))
    rows = []
    for row in frame.loc[:, required].itertuples(index=False):
        rows.append({
            "date": str(row.date), "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close), "volume": float(row.volume),
        })
    if not rows:
        raise ValueError("图表没有可显示的真实行情")
    return rows


def _resample(frame, rule):
    value = frame.loc[:, ("date", "open", "high", "low", "close", "volume")].copy()
    value.index = pd.to_datetime(value.pop("date"))
    output = value.resample(rule).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna().reset_index()
    output["date"] = output["date"].dt.strftime("%Y-%m-%d")
    return output


def _nearest_index(dates, day):
    target = pd.Timestamp(day)
    return min(range(len(dates)), key=lambda index: abs(dates[index] - target))


def _front_point(point, dates, timeframe="daily"):
    index = _nearest_index(dates, point[0])
    rule = {"weekly": "W-FRI", "monthly": "M"}.get(timeframe)
    if rule:
        period = pd.Timestamp(point[0]).to_period(rule)
        index = next((i for i, date in enumerate(dates) if date.to_period(rule) == period), index)
    return {"index": index, "value": float(point[1]),
            "date": str(point[0])}


def drawing_to_frontend(obj, dates, timeframe="daily"):
    points = obj["points"]
    value = deepcopy(obj.get("web") or {})
    value.update(id=obj["id"], type=obj["kind"], color=obj.get("color", "#2962ff"),
                 width=int(obj.get("width", 2)), visible=bool(obj.get("visible", True)),
                 locked=bool(obj.get("locked", False)), text=obj.get("text", ""))
    kind = obj["kind"]
    if kind == "path":
        value["points"] = [_front_point(point, dates, timeframe) for point in points]
        value["a"], value["b"] = value["points"][0], value["points"][-1]
    elif kind in ("long", "short"):
        value["a"] = _front_point(points[0], dates, timeframe)
        value["b"] = _front_point([points[3][0], points[2][1]], dates, timeframe)
        value["stop"] = float(points[1][1])
    else:
        value["a"] = _front_point(points[0], dates, timeframe)
        if len(points) > 1:
            value["b"] = _front_point(points[1], dates, timeframe)
        if kind == "channel":
            value["c"] = _front_point(points[2], dates, timeframe)
    if kind == "fib" and "fibLevels" not in value:
        colors = value.get("color", "#4caf50")
        value["fibLevels"] = [
            {"value": float(level), "enabled": True, "color": colors}
            for level in obj.get("levels", [0, .5, 1])
        ]
    created = obj.get("created_at")
    if created:
        value["createdAtDate"] = str(created)
        try:
            value["createdAtIndex"] = _front_point([created, 1], dates, timeframe)["index"]
        except (ValueError, TypeError):
            value["createdAtIndex"] = len(dates) - 1
    return value


def _store_point(point, rows):
    index = max(0, min(len(rows) - 1, int(round(float(point.get("index", 0))))))
    price = float(point.get("value"))
    if not math.isfinite(price) or price <= 0:
        raise ValueError("图形价格必须为有限正数")
    day = point.get("date") or rows[index]["date"]
    if pd.isna(pd.Timestamp(day)):
        raise ValueError("图形日期无效")
    return [str(day), price]


def drawing_from_frontend(value, rows):
    kind = str(value.get("type") or "")
    a = _store_point(value.get("a") or {}, rows)
    b = _store_point(value.get("b") or value.get("a") or {}, rows)
    if kind == "path":
        points = [_store_point(point, rows) for point in value.get("points") or []]
    elif kind in ("long", "short"):
        stop = float(value.get("stop"))
        edge_date = b[0]
        points = [a, [a[0], stop], [edge_date, b[1]], [edge_date, a[1]]]
    elif kind == "channel":
        offset = max(.000001, abs(b[1] - a[1]) * .34)
        third = _store_point(value["c"], rows) if value.get("c") else [a[0], a[1] + offset]
        points = [a, b, third]
    elif kind in ("ray", "up", "down", "text"):
        points = [a]
    else:
        points = [a, b]
    created_index = max(0, min(len(rows) - 1, int(value.get("createdAtIndex", len(rows) - 1))))
    levels = [float(item["value"]) for item in value.get("fibLevels", []) if item.get("enabled")]
    return {
        "id": str(value.get("id") or ""), "kind": kind, "points": points,
        "created_at": str(value.get("createdAtDate") or rows[created_index]["date"]),
        "color": str(value.get("color") or "#2962ff"),
        "text": str(value.get("text") or ""), "visible": bool(value.get("visible", True)),
        "locked": bool(value.get("locked", False)), "width": int(value.get("width", 2)),
        "levels": levels or [0, .5, 1], "risk_budget": float(value.get("risk_budget", 1000)),
        "web": _native(value),
    }


def workspace_boot(store, context):
    frame = context["frame"].reset_index(drop=True)
    rows = _bars(frame)
    source = str((context.get("record") or {}).get("source") or "unknown")
    adjustment = str((context.get("record") or {}).get("adjustment") or "unknown")
    key = identity(str(context["code"]), source, adjustment)
    document = store.chart_document(key)
    dates = [pd.Timestamp(row["date"]) for row in rows]
    settings = document["settings"]
    saved = settings.get("web_state") or {}
    timeframe = str(context.get("timeframe") or "daily")
    state = {
        "objects": [drawing_to_frontend(obj, dates, timeframe) for obj in document["drawings"]],
        "toolbar": deepcopy(saved.get("toolbar") or {}),
        "view": deepcopy(saved.get("view") or {}),
        "timeframe": saved.get("timeframe") or context.get("timeframe") or "daily",
    }
    payload = _native(context.get("payload") or {})
    context_kind = str(context.get("context_kind") or "candidate")
    title = {"candidate": "策略匹配", "watch": "自选标的",
             "holding": "当前持仓", "closed": "已清仓"}.get(context_kind, "图表分析")
    latest = rows[-1]
    series = {timeframe: rows}
    if timeframe == "daily":
        series["weekly"] = _bars(_resample(frame, "W-FRI"))
        series["monthly"] = _bars(_resample(frame, "ME"))
    return {
        "production": True, "identity": key, "revision": int(document["revision"]),
        "bars": rows, "drawings": state["objects"], "state": state,
        "settings": _native(settings), "alerts": _native(document["alerts"]),
        "symbol": str(context["code"]), "displaySymbol": str(context["code"]).split(".")[-1],
        "name": str(context.get("name") or context["code"]), "source": source,
        "adjustment": adjustment, "timeframe": timeframe, "series": series,
        "contextKind": context_kind, "contextTitle": title,
        "strategyType": str(context.get("strategy_type") or ""), "evidence": payload,
        "holdingLevels": _native(context.get("holding_levels") or ()),
        "tradeEvents": _native(context.get("replay_events") or ()),
        "latest": latest,
    }


class ChartBridge:
    """The exposed surface is intentionally small and never writes trade books."""

    def __init__(self, store, boot):
        self.store = store
        self.boot = boot
        self.identity = boot["identity"]
        self.rows = boot["bars"]
        self.revision = int(boot["revision"])
        self._close_window = None
        self._cancel_close = None

    def bootstrap(self):
        return deepcopy(self.boot)

    def save_state(self, state, expected_revision=None):
        expected = self.revision if expected_revision is None else int(expected_revision)
        current = self.store.chart_document(self.identity)
        document = deepcopy(current)
        rows = (self.boot.get("series") or {}).get(str(state.get("timeframe"))) or self.rows
        document["drawings"] = [drawing_from_frontend(item, rows)
                                for item in (state.get("objects") or [])]
        document["settings"]["web_state"] = {
            "toolbar": _native(state.get("toolbar") or {}),
            "view": _native(state.get("view") or {}),
            "timeframe": str(state.get("timeframe") or self.boot["timeframe"]),
        }
        document["revision"] = expected
        self.revision = self.store.save_chart_document(self.identity, document, expected)
        self.boot["revision"] = self.revision
        return {"ok": True, "revision": self.revision}

    def create_price_alert(self, value, condition="cross_up", name="", target="price",
                           timeframe=None):
        timeframe = str(timeframe or self.boot["timeframe"])
        rows = (self.boot.get("series") or {}).get(timeframe)
        if not rows:
            raise ValueError("当前入口没有该周期的真实行情，无法创建提醒")
        frame = pd.DataFrame(rows)
        alert = create_alert(
            name=name, code=self.boot["symbol"], source=self.boot["source"],
            adjustment=self.boot["adjustment"], timeframe=timeframe,
            target=target, condition=condition, value=float(value), frame=frame,
        )
        document = self.store.chart_document(self.identity)
        expected = int(document["revision"])
        document["alerts"].append(alert)
        document["revision"] = expected
        self.revision = self.store.save_chart_document(self.identity, document, expected)
        return {"ok": True, "revision": self.revision, "alert": _native(alert)}

    def ping(self):
        return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

    def finish_close(self):
        if self._close_window:
            self._close_window()

    def cancel_close(self):
        if self._cancel_close:
            self._cancel_close()
