"""Default history dates limit fetching, not the existing 125..300-bar contract.

These calendars and provider replies are synthetic and stay in an isolated
Store. Explicit daily status receipts certify the long halt; absent prices
alone never become halt evidence. Service and the indicator adapter run as-is.
"""
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from gui.toolbar import sync_start_date
from tests.test_workbench import store, wait_for
from workbench.market import load_dataset, save_dataset
from workbench.readiness import save_directory
from workbench.service import Service
from workbench.sources import ADJUSTMENTS, SOURCES, save_coverage, set_market_source, source_file
from workbench.store import dumps
from workbench.sync import sync_stock
from workbench.trading_status import save_status_rows


CODE = 'sh.600000'
ASOF = '2026-10-09'
RECENT_START = '2024-10-09'
PRICE_FIELDS = ['date', 'open', 'high', 'low', 'close', 'volume']


def _bars(dates):
    positions = np.arange(len(dates), dtype=float)
    close = 30 + positions / 200 + np.sin(positions / 11)
    return pd.DataFrame(dict(date=list(dates), open=close - .1,
        high=close + .5, low=close - .5, close=close, volume=1000.))


def _recent_bars(count):
    trading_days = pd.bdate_range(RECENT_START, ASOF).strftime('%Y-%m-%d').tolist()
    # One bar before a long, fully documented halt, then resumed trading.
    actual_days = [trading_days[0], *trading_days[-(count - 1):]]
    return trading_days, _bars(actual_days)


class StatusProvider:
    """A dated mock response with prices and explicit status-only evidence."""

    def __init__(self, data, trading_days):
        self.data = data
        actual = set(data.date)
        self.status_rows = [dict(code=CODE, date=day,
            tradestatus='1' if day in actual else '0',
            volume='1000' if day in actual else '0') for day in trading_days]
        self.calls = []

    def fetch(self, code, start, end):
        assert code == CODE
        self.calls.append((start, end))
        result = self.data[self.data.date.between(start, end)].copy()
        statuses = [r for r in self.status_rows if start <= r['date'] <= end]
        result.attrs['returned_dates'] = [r['date'] for r in statuses]
        result.attrs['suspended_dates'] = [r['date'] for r in statuses if r['tradestatus'] == '0']
        result.attrs['trading_status_rows'] = statuses
        return result


def _market(store, source, data, trading_days, monkeypatch):
    monkeypatch.setattr('workbench.market.completed_date', lambda: ASOF)
    monkeypatch.setattr('workbench.service.completed_date', lambda: ASOF)
    set_market_source(store, source)
    calendar = dict(start=trading_days[0], end=ASOF, trading_days=trading_days)
    path = source_file(store, 'trading_calendar.json', source)
    store.write_artifact(str(path.relative_to(store.root)), dumps(calendar).encode())
    save_directory(store, pd.DataFrame(dict(code=[CODE], code_name=['模拟证券'],
        tradeStatus=['1'])), ASOF, source=source)
    provider = StatusProvider(data, trading_days)
    # The shared evidence is an explicit provider status reply, independently
    # saved for all sources; it carries no prices from another source.
    save_status_rows(store, CODE, trading_days[0], ASOF, provider.status_rows)
    return provider


def _scan(store, source, dataset, monkeypatch):
    calls = []

    def capture(strategy, data, *, prepared):
        calls.append((data.copy(), prepared.copy()))
        return None

    monkeypatch.setattr('workbench.service.signal_at_end', capture)
    service = Service(store)
    try:
        job = service.submit('scan', dict(source=source, boards=['沪深主板'],
            datasets=[dataset], strategies=['MTR_MASTER'], timeframes=['daily'], asof=ASOF))
        row = wait_for(store, job)
        return row, json.loads(row['result']), calls
    finally:
        service.pool.shutdown(wait=True)


@pytest.mark.parametrize('source', list(SOURCES))
@pytest.mark.parametrize('count', [124, 125, 200, 299])
def test_two_year_initialization_scans_real_bars_without_filling_to_300(store, monkeypatch, source, count):
    trading_days, data = _recent_bars(count)
    provider = _market(store, source, data, trading_days, monkeypatch)
    start = sync_start_date(store, timeframe='daily')
    assert start == RECENT_START
    dataset, outcome = sync_stock(store, lambda: provider, CODE, start, ASOF, source=source)
    assert outcome == 'downloaded' and provider.calls == [(RECENT_START, ASOF)]
    saved, _ = load_dataset(store, dataset)
    pd.testing.assert_frame_equal(saved, data, check_exact=False)

    row, report, calls = _scan(store, source, dataset, monkeypatch)
    quality = json.loads(store.rows('SELECT value FROM meta WHERE key=?',
        ('market_quality:' + dataset,))[0]['value'])
    halted = set(trading_days) - set(data.date)
    assert set(quality['suspended_dates']) == halted and not quality['missing_dates']
    assert len(halted) > 200  # Explicitly certified simulated long halt.
    if count == 124:
        assert row['status'] == 'partial' and report['success'] == 0
        assert not calls and not report['coverage']['scan_allowed']
        assert report['errors'][0]['category'] == 'insufficient_bars'
        assert report['errors'][0]['window']['rows'] == 124
    else:
        assert row['status'] == 'completed', row['message']
        assert report['success'] == report['calculated'] == report['no_signal'] == 1
        assert report['coverage']['scan_allowed'] and report['coverage']['ready'] == 1
        assert len(calls) == 1
        raw, prepared = calls[0]
        assert len(raw) == len(prepared) == count
        pd.testing.assert_frame_equal(prepared[PRICE_FIELDS], saved, check_exact=False)
        assert {'ema20', 'ema60', 'atr', 'is_suspension_resume'} <= set(prepared.columns)
        assert prepared.is_suspension_resume.iloc[1]
    assert not store.rows('SELECT * FROM observations')
    assert not store.rows('SELECT * FROM plans')


@pytest.mark.parametrize('source', list(SOURCES))
def test_default_start_keeps_early_snapshot_and_prepares_full_300_bar_window(store, monkeypatch, source):
    recent_days, recent = _recent_bars(200)
    early_days = pd.bdate_range(end=(date.fromisoformat(RECENT_START) - timedelta(days=1)).isoformat(),
        periods=350).strftime('%Y-%m-%d').tolist()
    all_days = [*early_days, *recent_days]
    data = _bars([*early_days, *recent.date])
    provider = _market(store, source, data, all_days, monkeypatch)
    original = save_dataset(store, CODE, data, source, ADJUSTMENTS[source])
    save_coverage(store, CODE, original, early_days[0], ASOF, source)
    before, record = load_dataset(store, original)
    original_bytes = (store.root / record['path']).read_bytes()

    dataset, outcome = sync_stock(store, lambda: provider, CODE,
        sync_start_date(store, timeframe='daily'), ASOF, source=source)
    assert dataset == original and outcome == 'skipped' and not provider.calls
    assert (store.root / record['path']).read_bytes() == original_bytes
    retained, _ = load_dataset(store, dataset)
    pd.testing.assert_frame_equal(retained, before)
    assert len(retained) == 550 and retained.date.iloc[0] < RECENT_START

    row, report, calls = _scan(store, source, dataset, monkeypatch)
    assert row['status'] == 'completed', row['message']
    assert report['success'] == report['calculated'] == 1 and len(calls) == 1
    raw, prepared = calls[0]
    assert len(raw) == 550 and len(prepared) == 300
    assert (prepared.date < RECENT_START).sum() == 100
    assert (prepared.date >= RECENT_START).sum() == 200
    pd.testing.assert_frame_equal(prepared[PRICE_FIELDS],
        retained.iloc[-300:].reset_index(drop=True), check_exact=False)
    assert {'ema20', 'ema60', 'atr'} <= set(prepared.columns)
    assert not store.rows('SELECT * FROM observations')
    assert not store.rows('SELECT * FROM plans')
