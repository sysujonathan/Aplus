"""Actual fill date choices must not depend on downloading current prices."""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from workbench.store import Store, now
from workbench.sources import set_market_source, calendar_file
from workbench.trading import HISTORY_SESSIONS, trading_dates


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
@pytest.mark.parametrize('cache', ['empty', 'stale', 'broken'])
def test_today_is_selectable_without_current_price_files(tmp_path, monkeypatch, source, cache):
    store=Store(tmp_path/'ledger')
    set_market_source(store, source)
    if cache!='empty':
        store.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('old', 'sz.002259', 'daily', source, '前复权', '2026-09-29', '2026-09-30',
             2, 'market/old.csv', 'invalid-hash', now()))
        if cache=='broken':
            store.write_artifact('market/old.csv', b'broken price file')
    before=store.rows('SELECT * FROM datasets')
    monkeypatch.setattr('workbench.market.load_dataset', lambda *a, **kw:pytest.fail('No price loading'))
    query=store.rows
    def no_price_query(sql, args=()):
        assert 'datasets' not in sql, 'Date selector must not query price inventories'
        return query(sql, args)
    monkeypatch.setattr(store, 'rows', no_price_query)
    choices=trading_dates(store, 'sz.002259', today=date(2026,10,9))
    assert choices[:2]==['2026-10-09', '2026-10-08']
    # The window is ten years, but this store has no verified calendar file and
    # only 2026's published schedule exists, so older years stay unguessed. The
    # list still reaches the first 2026 session instead of stopping at 180.
    assert choices==sorted(set(choices), reverse=True)
    assert choices[-1]=='2026-01-05'
    assert all(day.startswith('2026') for day in choices)
    assert not any('2026-10-01'<=day<='2026-10-07' for day in choices)
    assert query('SELECT * FROM datasets')==before


def test_last_year_and_ten_year_ago_fills_stay_selectable(tmp_path):
    """A position opened years ago must be recordable, not only recent 180 sessions."""
    store = Store(tmp_path/'ledger')
    today = date(2026, 10, 9)
    first, last = date(2015, 1, 5), date(2026, 12, 31)
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    store.write_artifact('trading_calendar.json', json.dumps(dict(
        start=first.isoformat(), end=last.isoformat(), trading_days=days)).encode())
    choices = trading_dates(store, today=today)
    assert choices == sorted(set(choices), reverse=True)
    assert len(choices) == HISTORY_SESSIONS
    assert '2025-09-29' in choices   # bought last year
    assert '2021-09-29' in choices   # bought five years ago
    assert '2016-10-10' in choices   # bought ten years ago
    assert len(choices) < len(days), 'the window is bounded, not the whole calendar'


def test_typed_prefix_jumps_and_never_invents_a_date():
    from gui.trade_management import date_jump_target
    dates = ['2026-10-27', '2026-09-30', '2025-09-29', '2016-10-10']
    assert date_jump_target(dates, '2') == '2026-10-27'
    assert date_jump_target(dates, '2025') == '2025-09-29'
    assert date_jump_target(dates, '20250929') == '2025-09-29'
    assert date_jump_target(dates, '2016-10') == '2016-10-10'
    # A prefix outside the verified calendar stays unselected instead of guessed.
    assert date_jump_target(dates, '2030') is None
    assert date_jump_target(dates, '') is None
    assert date_jump_target(dates, '--') is None


@pytest.mark.parametrize('today, latest', [
    (date(2026,10,7), '2026-09-30'),
    (date(2026,10,8), '2026-10-08'),
    (date(2026,10,10), '2026-10-09'),
    (date(2026,10,11), '2026-10-09'),
])
def test_holidays_weekends_and_future_are_not_offered(tmp_path, today, latest):
    choices=trading_dates(Store(tmp_path/'ledger'), limit=10, today=today)
    assert choices[0]==latest and len(choices)==10
    assert all(date.fromisoformat(day)<=today for day in choices)
    assert all(date.fromisoformat(day).weekday()<5 for day in choices)


@pytest.mark.parametrize('instant, zone, expected', [
    ('2026-10-08T15:59:00+00:00', 'UTC', '2026-10-08'),
    ('2026-10-08T16:01:00+00:00', 'UTC', '2026-10-09'),
    ('2026-10-08T17:30:00-07:00', 'America/Los_Angeles', '2026-10-09'),
    ('2026-10-09T17:30:00-07:00', 'America/Los_Angeles', '2026-10-09'),
])
def test_default_today_uses_exchange_timezone_without_prices(tmp_path, monkeypatch, instant, zone, expected):
    from zoneinfo import ZoneInfo
    current = datetime.fromisoformat(instant).astimezone(ZoneInfo(zone))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # A no-argument now() would return the foreign computer's date.
            assert str(tz) == 'Asia/Shanghai'
            return current.astimezone(tz)

    monkeypatch.setattr('workbench.trading.datetime', Clock)
    monkeypatch.setattr('workbench.market.load_dataset', lambda *a, **kw: pytest.fail('No price loading'))
    store = Store(tmp_path/'ledger')
    assert trading_dates(store, limit=2)[0] == expected  # production default, no today=
    assert not store.rows('SELECT * FROM datasets')
    # Explicit dates stay injectable, independently of the current clock.
    assert trading_dates(store, limit=1, today=date(2026,10,8)) == ['2026-10-08']


def test_source_calendar_can_be_old_or_invalid_without_blocking_today(tmp_path):
    store=Store(tmp_path/'ledger')
    set_market_source(store, 'tickflow')
    path=calendar_file(store, 'tickflow')
    relative=str(path.relative_to(store.root))
    for content in (json.dumps(dict(start='2026-09-29', end='2026-09-30',
                                   trading_days=['2026-09-29','2026-09-30'])).encode(), b'invalid json'):
        store.write_artifact(relative, content)
        assert trading_dates(store, today=date(2026,10,9))[0]=='2026-10-09'
        assert path.read_bytes()==content


def test_covered_calendar_is_authoritative_and_unpublished_days_not_guessed(tmp_path):
    store=Store(tmp_path/'ledger')
    store.write_artifact('trading_calendar.json', json.dumps(dict(start='2026-10-08',
        end='2026-10-09', trading_days=['2026-10-08'])).encode())
    assert trading_dates(store, today=date(2026,10,9))[0]=='2026-10-08'
    choices=trading_dates(store, today=date(2027,1,5))
    assert not any(day.startswith('2027') for day in choices)
    assert trading_dates(store, limit=0, today=date(2026,10,9))==[]


@pytest.mark.skipif(os.name!='nt', reason='Windows native Tk controls')
def test_native_readonly_selector_records_today_buy_and_sell_without_quotes():
    result=subprocess.run([sys.executable, '-c',
        'from tests.test_trade_date_choices import check_native; check_native()'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=40)
    assert result.returncode==0, result.stdout+result.stderr


def check_native():
    import tempfile
    import ttkbootstrap as ttk
    from gui.trade_management import _PositionDialog, _SellDialog
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'ledger')
        # A verified ten-year calendar, so the selector can offer older fills.
        first, last, days = date(2015,1,5), date(2026,12,31), []
        cursor = first
        while cursor <= last:
            if cursor.weekday() < 5:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        store.write_artifact('trading_calendar.json', json.dumps(dict(
            start=first.isoformat(), end=last.isoformat(), trading_days=days)).encode())
        aid=store.save_account('隔离测试账户', 50000)
        batch=dict(date='2026-09-29', price=4.84, hands=4, fees=5.03)
        pid=store.save_position(aid, 'sz.002259', '测试标的', 4.84, 4.55, 5.53, [batch])
        position=dict(id=pid, code='sz.002259', name='测试标的', entry=4.84, stop=4.55,
                      tp1=5.53, buy_batches=[batch])
        root=ttk.Window(themename='darkly'); root.withdraw()
        try:
            with patch('gui.trade_management.trading_dates', side_effect=lambda s,*a:
                       trading_dates(s,*a,today=date(2026,10,9))):
                dialog=_PositionDialog(root, store, {'sz.002259':'测试标的'}, position)
                dialog.batches.add()
                record=dialog.batches.rows[-1]
                selector=record['widgets'][0]
                assert str(selector.cget('state'))=='readonly'
                assert selector.cget('values')[0]=='2026-10-09'
                selector.set('2026-10-09')
                record['price'].set('5.15'); record['hands'].set('4'); record['fees'].set('5.03')
                dialog._ok()
                assert dialog.confirmed and dialog.values['buy_batches'][0]['date']=='2026-09-29'
                store.save_position(aid, **dialog.values, position_id=pid)
                selling=dict(position, quantity=800, diluted_cost=5.01, first_buy_date='2026-09-29')
                sell=_SellDialog(root, store, selling)
                record=sell.editor.rows[0]
                assert str(record['widgets'][0].cget('state'))=='readonly'
                assert '2026-10-09' in record['widgets'][0].cget('values')
                record['date'].set('2026-10-09'); record['price'].set('5.15'); record['hands'].set('4')
                sell.fees.set('5.03'); sell._ok()
                assert sell.confirmed
                store.sell_position(pid, sell.batches, sell.fees_total)
                fills=store.rows('SELECT * FROM position_fills ORDER BY trade_date,side')
                assert len(fills)==3
                assert {(f['trade_date'],f['side']) for f in fills}=={
                    ('2026-09-29','BUY'), ('2026-10-09','BUY'), ('2026-10-09','SELL')}
                assert not store.rows('SELECT * FROM datasets')
                # A fill from years ago must be recordable through the same
                # native readonly control, not just the most recent sessions.
                other=store.save_account('历史买入账户', 50000)
                older=_PositionDialog(root, store, {'sz.002259':'测试标的'}, {})
                record=older.batches.rows[0]
                selector=record['widgets'][0]
                offered=selector.cget('values')
                assert str(selector.cget('state'))=='readonly'
                assert '2025-09-29' in offered and '2016-10-10' in offered
                # Typing jumps instead of scrolling 2650 sessions. Key events need
                # OS focus, so this asserts the binding; the jump rule itself is
                # covered by test_typed_prefix_jumps_and_never_invents_a_date.
                assert selector.bind('<KeyPress>')
                record['date'].set('2025-09-29')
                record['price'].set('4.50'); record['hands'].set('10'); record['fees'].set('5.01')
                older.name.set('测试标的')
                older.entry_price.set('4.50'); older.stop.set('4.20'); older.tp1.set('5.10')
                older._ok()
                assert older.confirmed and older.values['buy_batches'][0]['date']=='2025-09-29'
                store.save_position(other, **older.values)
                assert '2025-09-29' in {row['trade_date'] for row in
                    store.rows('SELECT * FROM position_fills')}
        finally:
            root.destroy()
