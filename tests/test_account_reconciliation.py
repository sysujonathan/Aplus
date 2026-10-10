from datetime import datetime

import pandas as pd
import pytest

from workbench.account_reconciliation import capture_reference, reconcile_account, validate_reference
from workbench.holding_quotes import value_holdings, CHINA
from workbench.market import save_dataset
from workbench.sources import set_market_source
from workbench.store import Store
from workbench.trading import management_report

CODE='sz.000001'
DAY='2026-09-30'


def setup(tmp_path):
    store=Store(tmp_path)
    aid=store.save_account('独立对账',73000,accounting_mode='history',current_total_assets=73298)
    pid=store.save_position(aid,CODE,'测试',10,9,12,[dict(date='2026-09-29',price=10,hands=2,fees=2)])
    closed=store.save_closed_trade(aid,CODE,'测试',DAY,2,100,1)
    ref=capture_reference(store,aid,73298,DAY,{CODE:dict(price=11,date=DAY,adjusted=False,source='confirmed')})
    store.save_account_reconciliation(aid,ref)
    return store,aid,pid,closed,ref


@pytest.mark.parametrize('source',['baostock','tickflow','tencent'])
@pytest.mark.parametrize('price',[6,11,50])
def test_price_source_restart_and_account_initial_do_not_drive_reverse(tmp_path,source,price):
    store,aid,pid,_,ref=setup(tmp_path)
    set_market_source(store,source)
    store=Store(tmp_path)
    report=management_report(store,aid,{CODE:dict(price=price,date='2026-10-09')})
    assert report['summary']['implied_initial_equity']==pytest.approx(73000)
    assert report['summary']['reconciliation_asof']==DAY
    assert report['summary']['floating_pnl']==pytest.approx(price*200-2002)
    before=report['summary']['implied_initial_equity']
    store.execute('UPDATE accounts SET initial_equity=99999 WHERE id=?',(aid,))
    changed=management_report(store,aid,{CODE:dict(price=price)})
    assert changed['summary']['implied_initial_equity']==before
    assert changed['summary']['reconciliation']==pytest.approx(73000-99999)
    live=value_holdings(changed,{CODE:dict(price=100,date='2026-10-09',source='history')},[],datetime(2026,10,10,tzinfo=CHINA))
    assert live['summary']['implied_initial_equity']==before


def test_backdated_close_cash_fee_errors_still_change_reverse(tmp_path):
    store,aid,pid,closed,_=setup(tmp_path)
    store.save_closed_trade(aid,CODE,'测试',DAY,2,150,1,closed_id=closed)
    assert management_report(store,aid,{})['summary']['implied_initial_equity']==72950
    flow=store.save_cash_flow(aid,DAY,'其他收入',20)
    assert management_report(store,aid,{})['summary']['implied_initial_equity']==72930
    store.execute('UPDATE position_fills SET fees=12 WHERE position_id=?',(pid,))
    assert management_report(store,aid,{})['summary']['implied_initial_equity']==72940
    store.delete_cash_flow(flow)
    assert management_report(store,aid,{})['summary']['implied_initial_equity']==72960


def test_post_snapshot_trades_and_flows_do_not_contaminate_old_check(tmp_path):
    store,aid,pid,_,_=setup(tmp_path)
    store.sell_position(pid,[dict(date='2026-10-09',price=12,hands=2)],2)
    store.save_cash_flow(aid,'2026-10-09','转入',1000)
    store.save_closed_trade(aid,'sz.000002','别的股票','2026-10-09',2,900,1)
    result=management_report(store,aid,{})
    assert not result['positions']
    assert result['summary']['implied_initial_equity']==73000
    assert result['summary']['cash_adjustments']==1000


def test_missing_legacy_baseline_does_not_guess_date_or_echo_initial(tmp_path):
    store=Store(tmp_path)
    aid=store.save_account('旧账户',12345,accounting_mode='history',current_total_assets=15000)
    report=management_report(store,aid,{})
    assert report['summary']['implied_initial_equity'] is None
    assert '未确认' in report['summary']['reconciliation_reason']
    assert not store.rows("SELECT * FROM meta WHERE key LIKE 'account_reconciliation:%'")


def test_raw_price_capture_is_exact_day_and_remains_independent_of_cache(tmp_path):
    store,aid,_,_,_=setup(tmp_path)
    frame=pd.DataFrame(dict(date=[DAY,'2026-10-09'],open=[11,12],high=[11,12],low=[11,12],close=[11,12],volume=[100,100]))
    ds=save_dataset(store,CODE,frame,'tencent','不复权')
    ref=capture_reference(store,aid,73298,DAY)
    assert ref['prices'][CODE]['dataset_id']==ds
    assert ref['prices'][CODE]['price']==11
    store.save_account_reconciliation(aid,ref)
    (store.root/store.rows('SELECT path FROM datasets WHERE id=?',(ds,))[0]['path']).unlink()
    assert management_report(Store(tmp_path),aid,{})['summary']['implied_initial_equity']==73000


def test_adjusted_only_or_wrong_day_cannot_build_broker_check(tmp_path):
    store,aid,_,_,_=setup(tmp_path)
    for changes in ({'adjusted':True},{'date':'2026-10-09'},{'price':float('nan')},{'price':True}):
        quote=dict(price=11,date=DAY,adjusted=False);quote.update(changes)
        with pytest.raises(ValueError):capture_reference(store,aid,73298,DAY,{CODE:quote})
    with pytest.raises(ValueError,match='缺少'):capture_reference(store,aid,73298,DAY)


def test_risk_only_account_save_preserves_baseline_changed_broker_invalidates(tmp_path):
    store,aid,_,_,_=setup(tmp_path)
    store.save_account('改风险',73000,account_id=aid,accounting_mode='history',current_total_assets=73298)
    assert management_report(store,aid,{})['summary']['implied_initial_equity']==73000
    store.save_account('新券商资产',73000,account_id=aid,accounting_mode='history',current_total_assets=74000)
    assert management_report(store,aid,{})['summary']['implied_initial_equity'] is None


def test_reference_write_never_changes_any_business_table(tmp_path):
    store,aid,_,_,ref=setup(tmp_path)
    tables=['accounts','positions','position_fills','closed_trades','cash_flows','datasets','observations','plans']
    before={t:store.rows('SELECT * FROM '+t) for t in tables}
    store.save_account_reconciliation(aid,ref)
    assert before=={t:store.rows('SELECT * FROM '+t) for t in tables}
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']=='8'


def test_unpriced_backdated_stock_is_pending_not_silently_latest(tmp_path):
    store,aid,_,_,_=setup(tmp_path)
    store.save_position(aid,'sz.000002','补录',10,9,12,[dict(date=DAY,price=10,hands=1,fees=0)])
    result=management_report(store,aid,{'sz.000002':dict(price=99,date='2026-10-09')})
    assert result['summary']['implied_initial_equity'] is None
    assert '同日估值' in result['summary']['reconciliation_reason']


def test_only_reverse_and_its_metadata_change_other_account_metrics_equal(tmp_path):
    store,aid,_,_,_=setup(tmp_path)
    price_map={CODE:dict(price=15,date='2026-10-09')}
    with_ref=management_report(store,aid,price_map)
    store.execute("DELETE FROM meta WHERE key=?",('account_reconciliation:'+aid,))
    without=management_report(store,aid,price_map)
    assert with_ref['positions']==without['positions']
    ignore={'implied_initial_equity','reconciliation','reconciliation_asof','reconciliation_reason','reconciliation_reference','reconciliation_components'}
    assert {k:v for k,v in with_ref['summary'].items() if k not in ignore}=={k:v for k,v in without['summary'].items() if k not in ignore}
