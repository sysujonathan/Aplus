"""Live 5-minute transport: verified rows only, and never Store history."""
import json
from datetime import datetime

import pandas as pd
import pytest

from gui.chart_bridge import workspace_boot
from workbench.live_minutes import (CHINA, DEFAULT_BARS, INTERVAL, MAX_INTERVAL,
    IntradaySource, MinutePoller, MinuteSourceError, aggregate_minute_bars, coarse_series,
    fetch_minute_bars, merge_live_tail, minute_rows, parse_minute_bars)
from workbench.store import Store

NOW = datetime(2026, 10, 26, 14, 30, tzinfo=CHINA)
CODE = "sz.002912"


def payload(rows, *, status=0, symbol="sz002912", period="m5"):
    return json.dumps({"code": status, "msg": "", "data": {symbol: {period: rows}}})


def row(stamp="202610261425", open_="25.00", close="25.10", high="25.20", low="24.90", volume="100"):
    return [stamp, open_, close, high, low, volume, {}, "10.0"]


def daily_frame():
    return pd.DataFrame({
        "date": ["2026-10-23", "2026-10-24"], "open": [24.0, 24.6], "high": [25.0, 25.4],
        "low": [23.8, 24.5], "close": [24.6, 25.2], "volume": [1e6, 1.1e6]})


def minute_frame():
    return pd.DataFrame({
        "date": ["2026-10-26 14:20", "2026-10-26 14:25", "2026-10-26 14:30"],
        "open": [25.0, 25.1, 25.2], "high": [25.1, 25.2, 25.3], "low": [24.9, 25.0, 25.1],
        "close": [25.1, 25.2, 25.25], "volume": [1e5, 1.1e5, 1.2e5]})


def context(store, **extra):
    return {"frame": daily_frame(), "record": {"source": "tencent", "adjustment": "不复权"},
            "code": CODE, "name": "中新赛克", "timeframe": "daily", **extra}


# ---- parsing -------------------------------------------------------------

def test_parse_keeps_verified_rows_and_scales_lots_to_shares():
    frame = parse_minute_bars(payload([row("202610261420"), row("202610261425")]), CODE, NOW)
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert frame.iloc[-1]["date"] == "2026-10-26 14:25"
    # Main-board Tencent volume is lots; the chart compares shares with daily bars.
    assert frame.iloc[-1]["volume"] == pytest.approx(10000.)


def test_star_board_volume_is_not_rescaled():
    frame = parse_minute_bars(payload([row("202610261420"), row("202610261425")], symbol="sh688111"),
                              "sh.688111", NOW)
    assert frame.iloc[-1]["volume"] == pytest.approx(100.)


@pytest.mark.parametrize("text", [
    "not json",
    payload([row("202610261420"), row("202610261425")], status=1),
    payload([row("202610261420")]),
    payload([]),
    payload([row("202610261420"), row("202610261427")]),                    # not on a 5-minute edge
    payload([row("202610261420"), row("202610261420")]),                    # not strictly increasing
    payload([row("202610261420"), row("202610261425"), row("202610261420")]),
    payload([row("202610261420"), row("202610261431")]),                    # beyond the local clock
    payload([row("202610261420"), row("202610261425", high="25.00")]),      # high below close
    payload([row("202610261420"), row("202610261425", low="25.30")]),       # low above close
    payload([row("202610261420"), row("202610261425", open_="0")]),
    payload([row("202610261420"), row("202610261425", volume="-1")]),
])
def test_parse_rejects_unusable_payloads(text):
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(text, CODE, NOW)


def test_parse_rejects_more_rows_than_requested():
    rows = [row("2026102614%02d" % minute) for minute in (20, 25, 30)]
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload(rows), CODE, NOW, count=2)


def test_parse_rejects_payload_for_another_symbol():
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload([row("202610261420"), row("202610261425")], symbol="sh600000"),
                          CODE, NOW)


# ---- request -------------------------------------------------------------

class Capture:
    """Minimal stand-in for urlopen that records the one request it receives."""

    def __init__(self, body=b"", error=None):
        self.body, self.error, self.request, self.timeout = body, error, None, None

    def __call__(self, request, timeout=None):
        self.request, self.timeout = request, timeout
        if self.error:
            raise self.error
        return _Response(self.body)


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, size=None):
        return self.body[:size] if size else self.body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_fetch_requests_five_minute_bars_with_bounded_timeout():
    capture = Capture(payload([row("202610261420"), row("202610261425")]).encode())
    frame = fetch_minute_bars("002912", NOW, count=60, opener=capture)
    assert "param=sz002912,m5,,60" in capture.request.full_url
    assert capture.request.headers.get("Referer") == "https://gu.qq.com/"
    assert 0 < capture.timeout <= 10
    assert len(frame) == 2


def test_fetch_surfaces_transport_and_size_failures():
    with pytest.raises(MinuteSourceError):
        fetch_minute_bars(CODE, NOW, opener=Capture(error=TimeoutError("timeout")))
    with pytest.raises(MinuteSourceError):
        fetch_minute_bars(CODE, NOW, opener=Capture(b"x" * (5 * 1024 * 1024)))


# ---- poller --------------------------------------------------------------

def test_poller_delivers_a_frame_then_backs_off_after_failure():
    def broken(code, moment=None):
        raise MinuteSourceError("供应商拒绝")

    poller = MinutePoller(fetch=broken)
    assert poller.start(CODE)
    assert not poller.start(CODE)          # one worker at a time
    frame, error = poller.take()
    assert frame is None and "供应商拒绝" in error
    assert poller.delay == INTERVAL * 2
    for _ in range(6):
        poller.start(CODE)
        poller.take()
    assert poller.delay == MAX_INTERVAL


def test_poller_drops_a_superseded_symbol_result():
    import threading
    import time
    gate = threading.Event()

    def blocking(code, moment=None):
        if code == "sz.002912":
            gate.wait(3)                    # the abandoned request stays in flight
        return minute_frame()

    def settled(poller):
        for _ in range(300):
            result = poller.take()
            if result is not None:
                return result
            time.sleep(.01)
        return None

    poller = MinutePoller(fetch=blocking)
    poller.start("sz.002912")
    poller.invalidate()                     # the trader switched symbols
    poller.start("sz.000001")
    frame, error = settled(poller)
    assert frame is not None and error is None
    gate.set()                              # the stale worker answers afterwards
    time.sleep(.15)
    assert poller.take() is None            # and its rows are never delivered


# ---- rows ----------------------------------------------------------------

def test_minute_rows_rejects_incomplete_or_unpriced_frames():
    with pytest.raises(ValueError):
        minute_rows(pd.DataFrame({"date": ["2026-10-26 14:30"], "open": [1.]}))
    with pytest.raises(ValueError):
        minute_rows(minute_frame().assign(close=0.))


def test_minute_rows_keep_market_strings_and_floats():
    rows = minute_rows(minute_frame())
    assert rows[-1]["date"] == "2026-10-26 14:30"
    assert all(isinstance(rows[-1][key], float) for key in ("open", "high", "low", "close", "volume"))


# ---- chart boot ----------------------------------------------------------

def test_workspace_boot_offers_intraday_unit_without_writing_history(tmp_path):
    store = Store(tmp_path)
    before = store.rows("SELECT COUNT(*) n FROM datasets")[0]["n"]
    boot = workspace_boot(store, context(store, display_timeframe="m5",
                                        minute_series=minute_frame()))
    assert store.rows("SELECT COUNT(*) n FROM datasets")[0]["n"] == before
    assert boot["series"]["m5"][-1]["date"] == "2026-10-26 14:30"
    # No invented future minutes: the axis ends at the last received bar.
    assert boot["axisDates"]["m5"] == []
    # Tencent labels a bar with its closing minute, so the watermark is the bar.
    assert boot["seriesCloseDates"]["m5"]["2026-10-26 14:30"] == "2026-10-26 14:30"
    assert boot["startTimeframe"] == "m5"
    assert boot["live"]["source"] == "腾讯 5 分钟"


def test_workspace_boot_without_intraday_bars_keeps_daily_only(tmp_path):
    store = Store(tmp_path)
    boot = workspace_boot(store, context(store))
    assert "m5" not in boot["series"]
    assert boot["startTimeframe"] == ""
    assert boot["live"] is None


def test_workspace_boot_ignores_an_unknown_start_unit(tmp_path):
    store = Store(tmp_path)
    boot = workspace_boot(store, context(store, display_timeframe="m90",
                                        minute_series=minute_frame()))
    assert boot["startTimeframe"] == ""
    assert "m5" in boot["series"]


def test_workspace_boot_offers_hourly_and_half_day_units(tmp_path):
    store = Store(tmp_path)
    hourly = pd.DataFrame({
        "date": ["2026-10-24 10:30", "2026-10-24 11:30"],
        "open": [24.0, 24.5], "high": [25.0, 25.5], "low": [23.9, 24.4],
        "close": [24.5, 25.2], "volume": [3e5, 4e5]})
    halfday = pd.DataFrame({
        "date": ["2026-10-24 11:30"], "open": [24.0], "high": [25.5],
        "low": [23.9], "close": [25.2], "volume": [7e5]})
    boot = workspace_boot(store, context(store, display_timeframe="m60",
                                        minute_series=minute_frame(),
                                        hour_series=hourly, halfday_series=halfday))
    assert boot["startTimeframe"] == "m60"
    assert [row["date"] for row in boot["series"]["m60"]] == ["2026-10-24 10:30", "2026-10-24 11:30"]
    assert [row["date"] for row in boot["series"]["m240"]] == ["2026-10-24 11:30"]
    # Closing-labelled transport: the watermark is the bar, not bar plus five.
    assert boot["seriesCloseDates"]["m60"]["2026-10-24 11:30"] == "2026-10-24 11:30"
    assert boot["axisDates"]["m60"] == [] and boot["axisDates"]["m240"] == []


# ---- period grid and the forming bar -------------------------------------

def test_parse_accepts_the_forming_bar_only_as_the_last_row():
    # NOW is 14:30; Tencent already publishes the bar stamped 14:35.
    frame = parse_minute_bars(payload([row("202610261425"), row("202610261435")]), CODE, NOW)
    assert frame.iloc[-1]["date"] == "2026-10-26 14:35"
    # Ten minutes ahead is a different session's bar, not a forming one.
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload([row("202610261425"), row("202610261440")]), CODE, NOW)
    # A row in front of the clock that is not the tail is never the forming bar.
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload([row("202610261435"), row("202610261440")]), CODE, NOW)


def test_parse_reads_one_minute_bars_on_their_own_grid():
    rows = [row("202610261426"), row("202610261427")]
    frame = parse_minute_bars(payload(rows, period="m1"), CODE, NOW, period="m1")
    assert frame.date.tolist() == ["2026-10-26 14:26", "2026-10-26 14:27"]
    # The same rows are not five-minute bars.
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload(rows), CODE, NOW)


@pytest.mark.parametrize("stamp", ["202610261200", "202610261235", "202610261505"])
def test_parse_rejects_one_minute_rows_outside_the_session(stamp):
    with pytest.raises(MinuteSourceError):
        parse_minute_bars(payload([row("202610261431"), row(stamp)], period="m1"),
                          CODE, NOW, period="m1")


def test_one_minute_rows_replace_the_skeleton_tail():
    one = parse_minute_bars(
        payload([row("2026102614%02d" % m) for m in range(26, 31)], period="m1"),
        CODE, NOW, period="m1")
    skeleton = pd.DataFrame({
        "date": ["2026-10-26 14:20", "2026-10-26 14:25", "2026-10-26 14:30"],
        "open": [25., 25., 25.], "high": [25.5, 25.5, 25.5],
        "low": [24.8, 24.8, 24.8], "close": [25., 25., 25.], "volume": [900., 900., 900.]})
    merged = merge_live_tail(skeleton, one)
    assert merged.date.tolist() == ["2026-10-26 14:20", "2026-10-26 14:25", "2026-10-26 14:30"]
    assert merged.iloc[-1]["volume"] == pytest.approx(5 * 10000.)
    assert merged.iloc[-1]["open"] == pytest.approx(25.)


def test_one_minute_window_truncated_at_the_head_is_never_repaired():
    # 14:09 arrives without 14:06-14:08, so that window cannot be reconstructed.
    stamps = ["2026102614%02d" % m for m in (9, *range(10, 26))]
    one = parse_minute_bars(payload([row(s) for s in stamps], period="m1"),
                            CODE, NOW, period="m1")
    derived = aggregate_minute_bars(one, 5)
    assert derived.date.tolist() == ["2026-10-26 14:15", "2026-10-26 14:20",
                                     "2026-10-26 14:25"]


def test_source_reads_the_skeleton_once_and_then_only_the_tail():
    calls = []
    skeleton = pd.DataFrame({
        "date": ["2026-10-26 14:20", "2026-10-26 14:25"],
        "open": [25., 25.], "high": [25.5, 25.5], "low": [24.8, 24.8],
        "close": [25., 25.], "volume": [900., 900.]})
    tail = pd.DataFrame({
        "date": ["2026-10-26 14:26", "2026-10-26 14:27", "2026-10-26 14:28",
                 "2026-10-26 14:29", "2026-10-26 14:30"],
        "open": [25.] * 5, "high": [25.2] * 5, "low": [24.9] * 5,
        "close": [25.1] * 5, "volume": [100.] * 5})

    def fetch(code, moment=None, *, period="m5", count=0):
        calls.append(period)
        return skeleton if period == "m5" else tail

    source = IntradaySource(fetch=fetch, clock=lambda: NOW)
    first = source(CODE, NOW)
    second = source(CODE, NOW)
    assert calls == ["m5", "m1", "m1"]
    assert first.equals(second)
    assert first.date.tolist() == ["2026-10-26 14:20", "2026-10-26 14:25", "2026-10-26 14:30"]


def test_source_keeps_the_skeleton_when_the_tail_request_fails():
    skeleton = pd.DataFrame({
        "date": ["2026-10-26 14:20", "2026-10-26 14:25"],
        "open": [25., 25.], "high": [25.5, 25.5], "low": [24.8, 24.8],
        "close": [25., 25.], "volume": [900., 900.]})

    def fetch(code, moment=None, *, period="m5", count=0):
        if period == "m1":
            raise MinuteSourceError("供应商拒绝")
        return skeleton

    source = IntradaySource(fetch=fetch, clock=lambda: NOW)
    assert source(CODE, NOW).equals(skeleton)


def test_coarse_series_keeps_history_and_replaces_only_the_live_tail():
    history = pd.DataFrame({
        "date": ["2026-10-23 14:00", "2026-10-23 15:00", "2026-10-26 10:30"],
        "open": [24., 24.5, 25.], "high": [25., 25.5, 25.5],
        "low": [23.9, 24.4, 24.8], "close": [24.5, 25.2, 25.], "volume": [3e5, 4e5, 9e5]})
    live = pd.DataFrame({
        "date": ["2026-10-26 10:35", "2026-10-26 10:40", "2026-10-26 10:45"],
        "open": [25., 25.1, 25.2], "high": [25.3, 25.4, 25.5],
        "low": [24.9, 25., 25.1], "close": [25.1, 25.2, 25.3], "volume": [100., 110., 120.]})
    merged = coarse_series(history, live, 60)
    # The provider's own 60-minute bars survive; the hour that is still forming
    # is placed at 11:30 where it will close, not at 10:45 where it stops today.
    assert merged.date.tolist() == ["2026-10-23 14:00", "2026-10-23 15:00",
                                    "2026-10-26 10:30", "2026-10-26 11:30"]
    assert merged.iloc[-1]["volume"] == pytest.approx(330.)
    # A half-day bar waits for its own slice, so the forming one is the morning.
    assert coarse_series(history.iloc[:1], live, 240).date.tolist() == [
        "2026-10-23 14:00", "2026-10-26 11:30"]
