"""JSON boundary between the local TradingView-style page and Aplus' Store."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import threading

import pandas as pd

from workbench.chart_workspace import create_alert, identity, validate_document


def _edit_fields(document):
    """Only alert progression may be merged over an open editor's revision."""
    return deepcopy({key: value for key, value in document.items()
                     if key not in ("revision", "alerts")})


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


def _alert_expiry(value):
    """datetime-local controls use the market timezone, never the host timezone."""
    if not value:
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('提醒有效期无效')
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize('Asia/Shanghai')
    return stamp.isoformat()


def _resample(frame, rule):
    value = frame.loc[:, ("date", "open", "high", "low", "close", "volume")].copy()
    value.index = pd.to_datetime(value.pop("date"))
    output = value.resample(rule).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna().reset_index()
    output["date"] = output["date"].dt.strftime("%Y-%m-%d")
    return output


def _coordinate_dates(rows, timeframe):
    """Extend the time axis, never the price series, into verified sessions.

    Week/month labels are calendar periods. Daily labels require the published
    exchange calendar; unpublished years deliberately remain unavailable.
    """
    from datetime import timedelta
    from workbench.exchange_calendar import is_trading_day
    first, last = pd.Timestamp(rows[0]['date']), pd.Timestamp(rows[-1]['date'])
    if timeframe in ('weekly', 'monthly'):
        rule = 'W-FRI' if timeframe == 'weekly' else 'ME'
        return pd.date_range(first - pd.DateOffset(years=5), last + pd.DateOffset(years=5),
                             freq=rule).strftime('%Y-%m-%d').tolist()
    before, after = [], []
    for direction, output, edge in ((-1, before, first.date()), (1, after, last.date())):
        for offset in range(1, 367):
            day = edge + timedelta(days=offset * direction)
            opened = is_trading_day(day)
            if opened is None:
                break
            if opened:
                output.append(day.isoformat())
    return list(reversed(before)) + [row['date'] for row in rows] + after


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
        "indicators": deepcopy(saved.get("indicators") or {}),
        "templates": deepcopy(saved.get("templates") or {}),
        "replayDate": saved.get("replayDate"),
        "timeframe": saved.get("timeframe") or context.get("display_timeframe") or context.get("timeframe") or "daily",
    }
    payload = _native(context.get("payload") or {})
    context_kind = str(context.get("context_kind") or "candidate")
    title = {"candidate": "策略匹配", "watch": "自选标的",
             "holding": "当前持仓", "closed": "已清仓"}.get(context_kind, "图表分析")
    latest = rows[-1]
    from workbench.prices import tick_size
    try:
        price_step = tick_size(str(context['code']), latest['close'],
                               instrument_type=context.get('instrument_type'))
    except ValueError:
        price_step = None  # Unknown instruments cannot borrow a stock tick.
    series = {timeframe: rows}
    if timeframe == "daily":
        series["weekly"] = _bars(_resample(frame, "W-FRI"))
        series["monthly"] = _bars(_resample(frame, "ME"))
    from .chart_evidence import strategy_evidence
    vectors = strategy_evidence(context)
    from workbench.chart_workspace import bar_close_time
    close_dates = {unit: {row['date']: str(bar_close_time(row['date'], unit).date()) for row in data}
                   for unit, data in series.items()}
    return {
        "production": True, "identity": key, "revision": int(document["revision"]),
        "bars": rows, "drawings": state["objects"], "state": state,
        "settings": _native(settings), "alerts": _native(document["alerts"]),
        "editBaseline": _native(_edit_fields(document)),
        "symbol": str(context["code"]), "displaySymbol": str(context["code"]).split(".")[-1],
        "name": str(context.get("name") or context["code"]), "source": source,
        "adjustment": adjustment, "timeframe": timeframe, "series": series,
        "displayTimeframe": str(context.get('display_timeframe') or timeframe),
        "seriesCloseDates": close_dates,
        "axisDates": {unit: _coordinate_dates(data, unit) for unit, data in series.items()},
        "contextKind": context_kind, "contextTitle": title,
        "strategyType": str(context.get("strategy_type") or ""), "evidence": payload,
        "strategyEvidence": _native(vectors),
        "holdingLevels": _native(context.get("holding_levels") or ()),
        "tradeEvents": _native(context.get("replay_events") or ()),
        "latest": latest,
        "priceStep": price_step,
    }


def refresh_workspace_boot(store, boot):
    """Read the document after the previous chart's flush, not at selection time."""
    value = deepcopy(boot)
    document = store.chart_document(value['identity'])
    dates = [pd.Timestamp(row['date']) for row in value['bars']]
    value['drawings'] = [drawing_to_frontend(obj, dates, value['timeframe']) for obj in document['drawings']]
    saved = document['settings'].get('web_state') or {}
    value.update(revision=document['revision'], settings=_native(document['settings']),
                 alerts=_native(document['alerts']), editBaseline=_native(_edit_fields(document)))
    value['state'] = dict(objects=value['drawings'], toolbar=deepcopy(saved.get('toolbar') or {}),
        view=deepcopy(saved.get('view') or {}), timeframe=saved.get('timeframe') or value.get('displayTimeframe') or value['timeframe'],
        indicators=deepcopy(saved.get('indicators') or {}))
    value['state']['templates'] = deepcopy(saved.get('templates') or {})
    value['state']['replayDate'] = saved.get('replayDate')
    return value


class ChartBridge:
    """The exposed surface is intentionally small and never writes trade books."""

    def __init__(self, store, boot, *, clock=None):
        self.store = store
        self.boot = boot
        self.identity = boot["identity"]
        self.rows = boot["bars"]
        self.revision = int(boot["revision"])
        self._close_window = None
        self._cancel_close = None
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._baseline = deepcopy(boot.get("editBaseline"))
        self._write_lock = threading.RLock()

    def bootstrap(self):
        return deepcopy(self.boot)

    def read_alerts(self):
        """Observe monitor progress without advancing this editor's CAS baseline."""
        with self._write_lock:
            document = self.store.chart_document(self.identity)
            return {'alerts': _native(document['alerts'])}

    def save_state(self, state, expected_revision=None):
        rows = (self.boot.get("series") or {}).get(str(state.get("timeframe"))) or self.rows
        drawings = [drawing_from_frontend(item, rows) for item in (state.get("objects") or [])]

        def edit(document):
            document["drawings"] = drawings
            document["settings"]["web_state"] = {
                "toolbar": _native(state.get("toolbar") or {}),
                "view": _native(state.get("view") or {}),
                "timeframe": str(state.get("timeframe") or self.boot["timeframe"]),
                "indicators": _native(state.get('indicators') or {}),
                "templates": _native(state.get('templates') or {}),
                "replayDate": _native(state.get('replayDate')),
            }
            for key in ('ema', 'ema_length', 'ema_source', 'ema_offset', 'ema_color',
                        'bar_count', 'count_interval', 'count_color', 'count_size', 'magnet'):
                if key in (state.get('indicators') or {}):
                    document['settings'][key] = state['indicators'][key]
        return self._commit(edit, expected_revision)

    def _commit(self, edit, expected_revision=None):
        """Rebase alert-only writes, retaining Store's atomic revision check."""
        conflict = "另一图表窗口已保存修改，请重新打开后继续；本窗口修改仍保留"
        with self._write_lock:
            expected = self.revision if expected_revision is None else int(expected_revision)
            if expected != self.revision:
                raise ValueError(conflict)
            for _ in range(3):
                current = self.store.chart_document(self.identity)
                revision = int(current["revision"])
                if revision != expected and (
                    self._baseline is None or _edit_fields(current) != self._baseline
                ):
                    raise ValueError(conflict)
                document = deepcopy(current)
                edit(document)
                document = validate_document(document)
                try:
                    saved = self.store.save_chart_document(self.identity, document, revision)
                except ValueError:
                    # A monitor can advance again between read and CAS. A different
                    # editor is rejected on the next comparison, never overwritten.
                    if self.store.chart_document(self.identity)["revision"] == revision:
                        raise
                    continue
                self.revision = int(saved)
                self._baseline = _edit_fields(document)
                self.boot["revision"] = self.revision
                return {"ok": True, "revision": self.revision,
                        "alerts": _native(document["alerts"])}
            raise ValueError("提醒状态持续更新，请稍后重试保存；本窗口修改仍保留")

    def create_price_alert(self, value, condition="cross_up", name="", target="price",
                           timeframe=None, ema_length=20, frequency='bar_close', expires=None,
                           upper=None, lower=None, bars=1):
        timeframe = str(timeframe or self.boot["timeframe"])
        rows = (self.boot.get("series") or {}).get(timeframe)
        if not rows:
            raise ValueError("当前入口没有该周期的真实行情，无法创建提醒")
        moment = self._clock()
        from .chart_alerts import load_alert_frame
        frame = load_alert_frame(self.store, self.identity, dict(
            code=self.boot['symbol'], source=self.boot['source'],
            adjustment=self.boot['adjustment'], timeframe=timeframe), moment)
        if frame is None:
            raise ValueError('没有同来源、同复权的正式行情，请更新行情后创建提醒')
        if frame.empty:
            raise ValueError("该周期尚无已完成 K 线，暂不能创建收盘提醒")
        alert = create_alert(
            name=name, code=self.boot["symbol"], source=self.boot["source"],
            adjustment=self.boot["adjustment"], timeframe=timeframe,
            target=target, condition=condition, value=float(value), frame=frame,
            active_after=moment,
            ema_length=int(ema_length), frequency=frequency, expires=_alert_expiry(expires),
            upper=upper, lower=lower, bars=bars,
        )
        result = self._commit(lambda document: document["alerts"].append(alert))
        return {**result, "alert": _native(alert)}

    def update_alert(self, alert_id, changes):
        """Edit without replaying historical closes or losing monitor writes."""
        allowed = {'name', 'condition', 'target', 'value', 'ema_length',
                   'frequency', 'expires', 'enabled', 'timeframe', 'upper', 'lower', 'bars'}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError('提醒设置包含未知字段')
        patch = deepcopy(changes)
        if 'enabled' in patch and not isinstance(patch['enabled'], bool):
            raise ValueError('提醒启用状态无效')
        if 'expires' in patch:
            patch['expires'] = _alert_expiry(patch['expires'])

        def edit(document):
            current = next((item for item in document['alerts'] if item['id'] == alert_id), None)
            if current is None:
                raise ValueError('提醒已不存在，请重新打开对象树')
            semantic = {'condition', 'target', 'value', 'ema_length', 'timeframe',
                        'upper', 'lower', 'bars'}
            reseed = any(key in patch and patch[key] != current.get(key) for key in semantic)
            reseed |= patch.get('enabled') is True and not current.get('enabled', True)
            candidate = {**current, **patch}
            channel = candidate['condition'] in (
                'enter_channel', 'exit_channel', 'inside_channel', 'outside_channel')
            movement = candidate['condition'] in ('rise', 'fall', 'rise_pct', 'fall_pct')
            if not channel:
                candidate.pop('upper', None)
                candidate.pop('lower', None)
            if not movement:
                candidate.pop('bars', None)
            validate_document({**document, 'alerts': [candidate]})
            if reseed:
                from .chart_alerts import load_alert_frame
                moment = self._clock()
                frame = load_alert_frame(self.store, self.identity, candidate, moment)
                if frame is None or frame.empty:
                    raise ValueError('没有同来源、同复权的已收盘行情，无法更新提醒条件')
                seeded = create_alert(name=candidate['name'], code=candidate['code'],
                    source=candidate['source'], adjustment=candidate['adjustment'],
                    timeframe=candidate['timeframe'], target=candidate['target'],
                    condition=candidate['condition'], value=candidate['value'], frame=frame,
                    ema_length=int(candidate['ema_length']), frequency=candidate['frequency'],
                    expires=candidate.get('expires'), active_after=moment,
                    upper=candidate.get('upper'), lower=candidate.get('lower'),
                    bars=candidate.get('bars', 1))
                for key in ('cursor', 'active_after'):
                    candidate[key] = seeded[key]
            current.clear()
            current.update(candidate)
        return self._commit(edit)

    def delete_alert(self, alert_id):
        def edit(document):
            if not any(item['id'] == alert_id for item in document['alerts']):
                raise ValueError('提醒已不存在，请重新打开对象树')
            document['alerts'] = [item for item in document['alerts'] if item['id'] != alert_id]
        return self._commit(edit)

    def ping(self):
        return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

    def copy_snapshot(self, data_url):
        if not getattr(self, '_copy_snapshot', None):
            raise RuntimeError('快照剪贴板尚未就绪')
        return self._copy_snapshot(data_url)

    def finish_close(self):
        if self._close_window:
            self._close_window()

    def cancel_close(self):
        if self._cancel_close:
            self._cancel_close()
