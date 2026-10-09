"""Actual fill date choices must not depend on downloading current prices."""
import json
import os
from datetime import date
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from workbench.store import Store, now
from workbench.sources import set_market_source, calendar_file
from workbench.trading import trading_dates


@pytest.mark.parametrize('source', ['baostock', 'tickflow'])
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
    assert len(choices)==180 and choices==sorted(set(choices), reverse=True)
    assert not any('2026-10-01'<=day<='2026-10-07' for day in choices)
    assert query('SELECT * FROM datasets')==before


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
        finally:
            root.destroy()
