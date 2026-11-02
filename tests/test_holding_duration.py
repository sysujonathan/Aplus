"""Derived exchange-session counts; never infer dates or write the ledger."""
import json

import pytest

from workbench.store import Store
from workbench.trading import holding_trading_days, management_report
from tests.test_closed_record_corrections import closed, business, CODE


@pytest.mark.parametrize('first,last,expected',[
    ('2026-10-27','2026-10-27',1),
    ('2026-10-22','2026-10-27',4),
    ('2026-10-30','2026-11-02',2),
    ('2026-09-30','2026-10-08',2),
    ('2026-09-20','2026-09-21',None),  # Sunday is not an exchange make-up day.
    ('2025-01-03','2025-01-06',None),  # No verified calendar for this year.
    ('2026-10-28','2026-10-27',None),
    ('invalid','2026-10-27',None),
])
def test_exchange_sessions_include_trade_endpoints_and_exclude_holidays(first,last,expected):
    assert holding_trading_days(first,last)==expected


def test_verified_calendar_and_exact_fills_drive_counts_without_any_prices(tmp_path):
    store=Store(tmp_path)
    aid,pid,row,_=closed(store,bought='2025-01-03',sold='2025-01-06')
    store.save_closed_trade(aid,'sz.001234','合成汇总','2025-01-06',8,10,1)
    report=management_report(store,aid)
    assert all(r['holding_trading_days'] is None for r in report['closed'])
    (store.root/'trading_calendar.json').write_text(json.dumps(dict(
        start='2025-01-03',end='2025-01-06',trading_days=['2025-01-03','2025-01-06'])),encoding='utf-8')
    before=business(store)
    report=management_report(store,aid)
    linked=next(r for r in report['closed'] if r['id']==row['id'])
    summary=next(r for r in report['closed'] if not r['position_id'])
    assert linked['holding_days']==3 and linked['holding_trading_days']==2
    assert summary['holding_days']==8 and summary['holding_trading_days'] is None
    assert business(store)==before
    assert not store.rows('SELECT * FROM datasets')


def test_correcting_close_date_recomputes_derived_sessions_without_schema_change(tmp_path):
    store=Store(tmp_path);aid,_,row,fills=closed(store,bought='2026-10-22')
    assert management_report(store,aid)['closed'][0]['holding_trading_days']==4
    changes=[dict(r,trade_date='2026-10-28') if r['side']=='SELL' else dict(r) for r in fills]
    store.correct_closed_position(aid,row['id'],changes,expected_fills=fills)
    actual=management_report(store,aid)['closed'][0]
    assert actual['holding_days']==6 and actual['holding_trading_days']==5
    assert 'holding_trading_days' not in store.rows('SELECT * FROM closed_trades')[0]


@pytest.mark.native_process
def test_duration_uses_one_explained_column_and_sorts_natural_days_numerically(tmp_path):
    import ttkbootstrap as ttk
    from gui.trade_management import TradeManagementFrame
    store=Store(tmp_path);aid,_,_,_=closed(store)
    store.save_closed_trade(aid,'sz.001234','合成汇总','2026-10-27',8,10,1)
    root=ttk.Window(themename='darkly')
    try:
        page=TradeManagementFrame(root,store);page.pack(fill='both',expand=True)
        root.update()
        tree=page.closed_tree
        assert tree.heading('days','text')=='持有天数（自然/交易）'
        assert len(tree['columns'])==7
        values={tree.set(i,'code'):tree.set(i,'days') for i in tree.get_children('')}
        assert values=={CODE.split('.')[1]:'0/1','001234':'8/—'}
        tree.delete(*tree.get_children(''))
        for days in ('2/1','10/8','8/—'):
            tree.insert('', 'end', values=(1,'001234','合成','2026-10-27',days,'0','0'))
        page._sort_tree(tree,'days','closed')
        assert [tree.set(i,'days') for i in tree.get_children('')]==['10/8','8/—','2/1']
    finally:
        root.destroy()
