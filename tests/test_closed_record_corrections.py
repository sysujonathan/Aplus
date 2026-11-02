"""Disposable ledgers: no market universe required to inspect/correct trades."""
import copy
import json
import threading
from unittest.mock import Mock

import pandas as pd
import pytest

from workbench.store import Store
from workbench.daily_returns import calculate_daily_pnl, historical_daily_returns
from workbench.closed_chart import load_closed_chart
from workbench.market import save_dataset
from workbench.watch_history import fetch_history

CODE='sz.159570'
DAY='2026-10-27'


def closed(store, *, bought=DAY, sold=DAY, code=CODE, account=None):
    account=account or store.save_account('隔离测试',10000,accounting_mode='history')
    pid=store.save_position(account,code,'合成标的',10,9,12,
                            [dict(date=bought,price=10,hands=2,fees=2)])
    store.sell_position(pid,[dict(date=sold,price=11,hands=2)],3)
    row=store.rows('SELECT * FROM closed_trades WHERE position_id=?',(pid,))[0]
    fills=store.rows('SELECT * FROM position_fills WHERE position_id=? ORDER BY trade_date,created,id',(pid,))
    return account,pid,row,fills


def business(store):
    return {table:store.rows('SELECT * FROM '+table+' ORDER BY rowid') for table in (
        'positions','position_fills','closed_trades','accounts','plans','plan_history','cash_flows','meta')}


def test_t0_closed_same_day_requires_only_actual_cash_flows_and_fees(tmp_path):
    store=Store(tmp_path);aid,_,_,fills=closed(store)
    keyed=[dict(r,code=CODE) for r in fills]
    assert calculate_daily_pnl(keyed,{},DAY)==195
    assert calculate_daily_pnl(keyed,{CODE:dict(price=float('nan'),previous_close=float('nan'))},DAY)==195
    assert historical_daily_returns(store,aid)==[dict(date=DAY,pnl=195,source='local',status='recorded')]
    assert not store.rows('SELECT * FROM datasets')
    assert calculate_daily_pnl(keyed,{},'2026-10-28')==0


def test_overnight_inventory_still_needs_real_prices_and_explains_missing(tmp_path):
    store=Store(tmp_path);aid,_,_,_=closed(store,bought='2026-10-26')
    gaps={}
    assert historical_daily_returns(store,aid,missing=gaps)==[]
    assert CODE in gaps['2026-10-26'] and CODE in gaps[DAY]


def test_calendar_reports_all_missing_holdings_and_rejects_broken_chart_cache(tmp_path):
    store=Store(tmp_path);aid,_,_,fills=closed(store,bought='2026-10-26')
    _,_,_,other=closed(store,bought='2026-10-26',code='sz.159518',account=aid)
    gaps={}
    assert calculate_daily_pnl([dict(r,code=CODE) for r in fills]+
                              [dict(r,code='sz.159518') for r in other],{},DAY,missing=gaps) is None
    assert set(gaps)=={CODE,'sz.159518'}
    store.execute('INSERT INTO meta(key,value) VALUES(?,?)',('watch_chart:baostock:'+CODE,'{}'))
    assert historical_daily_returns(store,aid)==[]
    assert historical_daily_returns(store,aid)==[]  # unknown never saved as zero


def chart_cache(store):
    frame=pd.DataFrame(dict(date=['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30'],
        open=[10.]*5,high=[12.]*5,low=[9.]*5,close=[10.,10.5,10.7,10.8,11.],volume=[100.]*5))
    provider=Mock()
    provider.__enter__=Mock(return_value=provider);provider.__exit__=Mock(return_value=False)
    provider.fetch_unadjusted=Mock(return_value=frame)
    return fetch_history(store,CODE,'baostock',threading.Event(),provider_factory=lambda:provider,end='2026-10-30')


def test_chart_only_etf_history_values_overnight_days_and_opens_closed_chart(tmp_path):
    store=Store(tmp_path);aid,_,row,_=closed(store,sold='2026-10-30')
    chart_cache(store)
    before=business(store)
    result=historical_daily_returns(store,aid)
    assert [r['date'] for r in result]==['2026-10-27','2026-10-28','2026-10-29','2026-10-30']
    assert [r['pnl'] for r in result]==[98,40,20,37]
    assert sum(r['pnl'] for r in result)==pytest.approx(row['pnl'])
    data=load_closed_chart(store,row)
    assert data['dataset']['id'].startswith('watch-') and not data['estimated']
    assert len(data['events'])==2 and data['dataset']['adjustment']=='不复权'
    assert business(store)==before and store.rows('SELECT * FROM datasets')==[]


def test_t0_no_quote_does_not_block_other_stock_days_27_to_30(tmp_path):
    store=Store(tmp_path);aid,_,_,_=closed(store)
    store.save_position(aid,'sh.600000','合成股票',10,9,12,
                        [dict(date='2026-10-26',price=10,hands=1,fees=0)])
    frame=pd.DataFrame(dict(date=['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30'],
        open=[10.]*5,high=[12.]*5,low=[9.]*5,close=[10.,10.5,10.7,10.8,11.],volume=[100.]*5))
    save_dataset(store,'sh.600000',frame,'baostock','不复权')
    gaps={};result={r['date']:r['pnl'] for r in historical_daily_returns(store,aid,missing=gaps)}
    assert result=={'2026-10-26':0,'2026-10-27':245,'2026-10-28':20,'2026-10-29':10,'2026-10-30':20}
    assert not gaps


def test_fee_correction_preserves_closed_identity_and_broker_calendar(tmp_path):
    store=Store(tmp_path);aid,pid,row,fills=closed(store)
    same_code=store.save_position(aid,CODE,'另一次独立持仓',10,9,12,[dict(date='2026-10-28',price=10,hands=1)])
    store.save_daily_returns(aid,[dict(date=DAY,pnl=999,source='broker'),
                                  dict(date='2026-10-26',pnl=42,source='local')])
    updated=copy.deepcopy(fills)
    next(r for r in updated if r['side']=='SELL')['fees']=8
    before_open=store.rows('SELECT * FROM positions WHERE id=?',(same_code,))
    store.correct_closed_position(aid,row['id'],updated,expected_fills=fills)
    after=store.rows('SELECT * FROM closed_trades WHERE id=?',(row['id'],))[0]
    assert after['pnl']==190 and after['return_pct']==pytest.approx(190/2002*100)
    assert after['position_id']==pid and after['created']==row['created'] and after['holding_days']==0
    assert store.rows('SELECT status FROM positions WHERE id=?',(pid,))[0]['status']=='CLOSED'
    assert store.rows('SELECT * FROM positions WHERE id=?',(same_code,))==before_open
    assert {r['id'] for r in store.rows('SELECT * FROM position_fills WHERE position_id=?',(pid,))}=={r['id'] for r in fills}
    assert store.list_daily_returns(aid)==[dict(date=DAY,pnl=999,source='broker',status='recorded')]
    assert {r['date']:r['pnl'] for r in historical_daily_returns(store,aid)}[DAY]==190
    audit=json.loads(store.rows("SELECT detail FROM events WHERE action='修正清仓原始成交'")[0]['detail'])
    assert next(r for r in audit['before'] if r['side']=='SELL')['fees']==3
    assert next(r for r in audit['after'] if r['side']=='SELL')['fees']==8
    before=business(store)
    with pytest.raises(ValueError,match='已变化'):
        store.correct_closed_position(aid,row['id'],updated,expected_fills=fills)
    assert business(store)==before


@pytest.mark.parametrize('change', ['negative_fee','nan','bool','overflow','quantity','date','direction','missing','duplicate','account','early_sale'])
def test_invalid_correction_is_atomic_and_cannot_reopen_or_cross_accounts(tmp_path,change):
    store=Store(tmp_path);aid,_,row,fills=closed(store)
    changes=copy.deepcopy(fills);sell=next(r for r in changes if r['side']=='SELL')
    if change=='negative_fee':sell['fees']=-1
    elif change=='nan':sell['price']=float('nan')
    elif change=='bool':sell['fees']=True
    elif change=='overflow':sell['price']=1e308
    elif change=='quantity':sell['quantity']=100
    elif change=='date':sell['trade_date']='2026-02-30'
    elif change=='direction':sell['side']='BUY'
    elif change=='missing':changes.pop()
    elif change=='duplicate':changes=[changes[0],changes[0]]
    elif change=='early_sale':sell['trade_date']='2026-10-26'
    elif change=='account':aid=store.save_account('其他账户',1000)
    before=business(store)
    with pytest.raises(ValueError):store.correct_closed_position(aid,row['id'],changes,expected_fills=fills)
    assert business(store)==before


@pytest.mark.parametrize('target',['closed_trades','events'])
def test_write_failure_rolls_back_fills_archive_and_cached_calendar(tmp_path,target):
    import sqlite3
    store=Store(tmp_path);aid,_,row,fills=closed(store)
    store.save_daily_returns(aid,[dict(date=DAY,pnl=195,source='local')])
    operation='UPDATE' if target=='closed_trades' else 'INSERT'
    store.execute(f"CREATE TRIGGER fail_archive BEFORE {operation} ON {target} BEGIN SELECT RAISE(ABORT,'injected'); END")
    updates=copy.deepcopy(fills);updates[0]['fees']=10
    before=business(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.correct_closed_position(aid,row['id'],updates,expected_fills=fills)
    assert business(store)==before


def test_linked_archive_summary_cannot_be_overwritten_independently(tmp_path):
    store=Store(tmp_path);aid,_,row,_=closed(store)
    before=business(store)
    with pytest.raises(ValueError,match='原始成交'):
        store.save_closed_trade(aid,CODE,'错误替换',DAY,0,999,99,closed_id=row['id'])
    assert business(store)==before


def test_user_chart_request_fetches_one_etf_without_joining_scan_universe(tmp_path,monkeypatch):
    from gui import closed_trade_chart as view
    store=Store(tmp_path);_,_,row,_=closed(store)
    calls=[]
    def fetch(target,code,source,cancel):
        calls.append((code,source))
        return chart_cache(target)
    monkeypatch.setattr('workbench.watch_history.fetch_history',fetch)
    monkeypatch.setattr(view,'_fetch_chart_data_dialog',lambda parent,fetch,**kw:fetch(threading.Event()))
    dialog=Mock();monkeypatch.setattr(view,'ClosedTradeChartDialog',dialog)
    view.open_closed_chart_dialog(None,store,row)
    assert calls==[(CODE,'baostock')]
    assert dialog.call_args.kwargs['data']['code']==CODE
    assert not store.rows('SELECT * FROM datasets')


@pytest.mark.native_process
def test_native_editor_without_quotes_retains_inputs_on_error_and_only_saves_on_confirm(tmp_path,monkeypatch):
    import ttkbootstrap as ttk
    from gui.trade_management import _ClosedFillsDialog
    from workbench.trading import management_report
    store=Store(tmp_path);aid,_,row,_=closed(store)
    root=ttk.Window(themename='darkly')
    errors=Mock();monkeypatch.setattr('gui.trade_management.messagebox.showerror',errors)
    try:
        before=business(store)
        dialog=_ClosedFillsDialog(root,store,row);root.update()
        assert dialog.top.winfo_exists() and dialog.top.grab_current() is dialog.top
        assert business(store)==before and not store.rows('SELECT * FROM datasets')
        sale=next(e for f,e in zip(dialog.original,dialog.entries) if f['side']=='SELL')
        sale['fees'].set('8');sale['quantity'].set('100')
        dialog._ok()
        assert errors.called and not dialog.confirmed and dialog.top.winfo_exists()
        assert sale['fees'].get()=='8' and business(store)==before
        monkeypatch.setattr('gui.closed_trade_chart.open_closed_chart_dialog',lambda *a:None)
        dialog._view_chart()
        assert dialog.top.grab_current() is dialog.top and business(store)==before
        sale['quantity'].set('200');dialog._ok();root.update()
        assert dialog.confirmed and not dialog.top.winfo_exists()
        assert management_report(store,aid,{})['summary']['total_assets']==10190
        after=business(store)
        cancel=_ClosedFillsDialog(root,store,store.rows('SELECT * FROM closed_trades')[0])
        cancel.entries[0]['fees'].set('999');cancel.top.destroy();root.update()
        assert business(store)==after
    finally:
        root.destroy()
