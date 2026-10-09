"""Ephemeral holding quotes and valuation. Never feed strategies or daily history."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import math
import queue
import re
import threading
import urllib.request

from .daily_returns import calculate_daily_pnl
from .market import code_of, load_dataset
from .closed_chart import _ReadOnlySnapshots
from .exchange_calendar import is_trading_day
from .sources import market_source

CHINA = timezone(timedelta(hours=8))
INTERVAL = 15


def china_now():
    return datetime.now(CHINA)


def session_status(moment, calendar):
    trading = is_trading_day(moment.date(), calendar)
    if trading is None:
        return "交易日历待更新（可手动刷新）"
    if not trading:
        return "休市"
    clock = moment.time().replace(tzinfo=None)
    if time(9, 30) <= clock <= time(11, 30) or time(13) <= clock <= time(15):
        return "交易中"
    return "非交易时段"


def parse_quotes(text, codes, moment):
    """Validate identity, positive finite prices and the provider's own timestamp."""
    result = {}
    lines = dict(re.findall(r'var\s+hq_str_([a-z]+\d{6})\s*=\s*"([^"\r\n]*)"', text))
    for code in codes:
        normalized = code_of(code)
        fields = lines.get(normalized.replace(".", ""), "").split(",")
        try:
            price, previous = float(fields[3]), float(fields[2])
            stamp = datetime.fromisoformat(fields[30] + " " + fields[31]).replace(tzinfo=CHINA)
            if not all(math.isfinite(v) and v > 0 for v in (price, previous)):
                continue
            if stamp > moment + timedelta(seconds=60):
                continue
            result[normalized] = {"price": price, "previous_close": previous,
                                  "date": stamp.date().isoformat(),
                                  "quote_time": stamp.isoformat(), "source": "sina"}
        except (IndexError, ValueError):
            continue
    return result


class QuoteFailures(set):
    """Backwards-compatible failure set with ephemeral, readable diagnostics."""
    def __init__(self,codes=(),details=None):
        super().__init__(codes)
        self.details=details or {}


def fetch_quotes(codes):
    codes = sorted({code_of(c) for c in codes})
    supported = [c for c in codes if c.startswith(("sh.", "sz."))]
    quotes = {}
    details={}
    # A dedicated direct opener: never mutate global proxy environment/config.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for start in range(0, len(supported), 80):
        batch = supported[start:start + 80]
        request = urllib.request.Request(
            "https://hq.sinajs.cn/list=" + ",".join(c.replace(".", "") for c in batch),
            headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"},
        )
        try:
            with opener.open(request, timeout=8) as response:
                payload = response.read(1024 * 1024).decode("gbk")
            quotes.update(parse_quotes(payload, batch, china_now()))
        except Exception as exc:
            details.update({code:f'{type(exc).__name__}: {exc}' for code in batch})
            continue
    failed=set(codes)-quotes.keys()
    for code in failed:
        details.setdefault(code,'该标的未返回有效报价' if code in supported else '新浪接口不支持该市场')
    return quotes, QuoteFailures(failed,details)


def fetch_holding_quotes(codes):
    """Tencent first; Sina only for missing/stale snapshots. History stays separate."""
    from .tencent_quotes import fetch_tencent_quotes
    codes = sorted({code_of(c) for c in codes})
    quotes, details = fetch_tencent_quotes(codes)
    quotes = dict(quotes)
    moment = china_now()
    trading = session_status(moment, None) == '交易中'
    retry = [c for c in codes if c not in quotes or (trading and
        (moment-datetime.fromisoformat(quotes[c]['quote_time'])).total_seconds() > 45)]
    if retry:
        fallback, failed = fetch_quotes(retry)
        for code, quote in fallback.items():
            if code not in quotes or quote['quote_time'] > quotes[code]['quote_time']:
                quotes[code] = quote
        for code in retry:
            if code not in quotes:
                details[code] = details.get(code,'腾讯无有效报价')+'；新浪：'+failed.details.get(code,'无有效报价')
    failed = set(codes)-quotes.keys()
    return quotes, QuoteFailures(failed,{c:details.get(c,'无有效报价') for c in failed})


class QuotePoller:
    """One daemon worker; invalidation rejects late results without killing threads."""
    def __init__(self, fetch=fetch_holding_quotes):
        self.fetch = fetch
        self.results = queue.Queue()
        self.busy = False
        self.generation = 0
        self.failures = 0

    def invalidate(self):
        self.generation += 1
        self.failures = 0

    def start(self, codes):
        if self.busy or not codes:
            return False
        generation, codes = self.generation, tuple(sorted(set(codes)))
        self.busy = True
        def work():
            try:
                quotes, failed = self.fetch(codes)
            except Exception as exc:
                quotes, failed = {}, QuoteFailures(codes,{code:f'{type(exc).__name__}: {exc}' for code in codes})
            self.results.put((generation, quotes, failed))
        threading.Thread(target=work, daemon=True, name="holding-quotes").start()
        return True

    def take(self):
        try:
            generation, quotes, failed = self.results.get_nowait()
        except queue.Empty:
            return None
        self.busy = False
        if generation != self.generation:
            return None
        self.failures = min(self.failures + 1, 4) if failed else 0
        return quotes, failed

    @property
    def delay(self):
        return min(INTERVAL * 2 ** self.failures, 120)


def saved_quotes(store, codes):
    """Read verified, unadjusted history where possible, without audit writes."""
    prices = {}
    reader = _ReadOnlySnapshots(store)
    for code in sorted(set(codes)):
        rows = store.rows("SELECT * FROM datasets WHERE timeframe='daily' AND code=? AND source=? "
                          "ORDER BY end DESC, CASE WHEN adjustment IN ('不复权','none','3') "
                          "THEN 0 ELSE 1 END,created DESC,rowid DESC LIMIT 1", (code,market_source(store)))
        if not rows:
            continue
        try:
            frame, record = load_dataset(reader, rows[0]["id"])
            if frame.empty:
                continue
            unadjusted = record["adjustment"] in ("不复权", "none", "3")
            prices[code] = {"price": float(frame.iloc[-1]["close"]),
                            "previous_close": float(frame.iloc[-2]["close"]) if len(frame) > 1 else None,
                            "date": str(frame.iloc[-1]["date"])[:10], "source": "history",
                            "adjusted": not unadjusted}
        except (OSError, ValueError, KeyError):
            continue
    return prices


def select_quotes(history, live, codes, failed, moment, calendar):
    quotes = {}
    trading = session_status(moment, calendar) == "交易中"
    for code in codes:
        quote = live.get(code)
        saved=history.get(code)
        stamp=None
        try:
            if quote:
                stamp=datetime.fromisoformat(quote['quote_time'])
                if stamp.tzinfo is None or stamp>moment+timedelta(seconds=60) or quote['date']!=stamp.date().isoformat():
                    stamp=None
        except (KeyError,ValueError,TypeError):
            stamp=None
        # Midnight/holidays do not invalidate a completed session's quote.
        # A verified daily close outranks an older same-day intraday quote.
        usable=stamp is not None and quote['date']<=moment.date().isoformat()
        if usable and saved:
            usable=quote['date']>=saved['date'] and not (
                quote['date']==saved['date'] and stamp.date()<moment.date() and stamp.time().replace(tzinfo=None)<time(15))
        if usable:
            state = "更新失败" if code in failed else (
                "行情滞后" if trading and (moment - stamp).total_seconds() > 45 else "最新报价")
            quotes[code] = {**quote, "state": state}
        elif code in history:
            quotes[code] = {**history[code], "state": "复权历史收盘" if history[code].get("adjusted") else "历史收盘"}
    return quotes


def net_invested(fills):
    return sum((1 if f["side"] == "BUY" else -1) * float(f["price"]) * int(f["quantity"])
               + float(f.get("fees") or 0) for f in fills)


def snapshot_reference(report, fills):
    if not all(row["has_quote"] for row in report["positions"]):
        return None
    return {"cash": report["summary"]["available_cash"], "invested": net_invested(fills),
            "adjustments": report["summary"]["cash_adjustments"]}


def value_holdings(base, quotes, fills, moment, reference=None):
    """Pure display overlay. Cash depends on ledger/reference, never share prices."""
    report = deepcopy(base)
    s = report["summary"]
    rows = report["positions"]
    value = floating = 0.0
    complete = True
    for row in rows:
        quote = quotes.get(row["code"], {})
        price = quote.get("price")
        row["has_quote"] = price is not None
        row["quote_state"] = quote.get("state", "缺少行情")
        row["quote_label"] = (quote.get("quote_time", "")[11:19]
                              if quote.get("source") in ("sina", "tencent") else quote.get("date", ""))
        row["current_price"] = price
        if price is None:
            complete = False
            row.update(market_value=None, floating_pnl=None, distance_stop_pct=None)
            continue
        row["market_value"] = price * row["quantity"]
        row["floating_pnl"] = row["market_value"] - row["diluted_cost"] * row["quantity"]
        row["distance_stop_pct"] = (price - row["stop"]) / price * 100
        value += row["market_value"]
        floating += row["floating_pnl"]
    if report["account"].get("accounting_mode") == "history":
        cash = (report["account"]["initial_equity"] + s["closed_pnl"]
                + s["cash_adjustments"] - sum(r["diluted_cost"] * r["quantity"] for r in rows))
    elif reference is not None:
        cash = reference["cash"] - (net_invested(fills) - reference["invested"]) + (
            s["cash_adjustments"] - reference["adjustments"])
    else:
        cash = None
    total = cash + value if complete and cash is not None else None
    s.update(market_value=value if complete else None, floating_pnl=floating if complete else None,
             total_assets=total, available_cash=cash, withdrawable_cash=cash,
             position_pct=value / total * 100 if total and complete else None,
             risk_pct=s["risk_amount"] / total * 100 if total else None,
             asset_pnl=s["closed_pnl"] + floating if complete else None)
    # Broker snapshot may belong to another time: do not reverse opening cash
    # from a changing live valuation. Retain its historical reconciliation only.
    if not complete:
        s["implied_initial_equity"] = None
    for row in rows:
        row["allocation_pct"] = row["market_value"] / total * 100 if total and row["has_quote"] else None
        row["account_risk_pct"] = row["risk_amount"] / total * 100 if total else None
    # A live quote is unadjusted. Do not mix it with forward-adjusted yesterday.
    daily_quotes = {c: q for c, q in quotes.items() if not q.get("adjusted")}
    quote_days={q['date'] for q in daily_quotes.values() if q.get('date')}
    asof=moment.date().isoformat()
    if len(quote_days)==1 and moment.time().replace(tzinfo=None)<time(9,30):
        asof=next(iter(quote_days))
    elif len(quote_days)==1 and is_trading_day(moment.date()) is False:
        asof=next(iter(quote_days))
    s["daily_pnl"] = calculate_daily_pnl(fills, daily_quotes, asof)
    if s["daily_pnl"] is not None:
        s["daily_pnl"] += sum(float(f["amount"]) for f in report["cash_flows"]
                              if f["flow_date"][:10] == asof
                              and not any(w in f["category"] for w in ("转入", "转出", "入金", "出金", "转账")))
    return report
