import copy
import json
from threading import Event

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars
from workbench.history_quality import describe_history, save_quality, quality_for, require_research_history
from workbench.market import save_dataset, DirectBaoStock
from workbench.readiness import audit_scope, save_directory
from workbench.sources import SOURCES, source_file
from workbench.store import dumps
from workbench.trading_status import (validate_status_rows, save_status_rows, status_facts,
                                     halt_evidence, refresh_status_index)
from workbench.status_review import verify_status_targets

CODE = 'sh.600000'


def status(day, value='0', volume=''):
    return dict(code=CODE, date=day, tradestatus=value, volume=volume)


def market(store, data, source, codes=(CODE,)):
    first, last = data.date.iloc[0], data.date.iloc[-1]
    calendar = dict(start=first,end=last,trading_days=data.date.tolist())
    store.write_artifact(str(source_file(store,'trading_calendar.json',source).relative_to(store.root)),dumps(calendar).encode())
    save_directory(store,pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*len(codes))),last,source=source)
    return first,last


@pytest.mark.parametrize('source', list(SOURCES))
def test_three_sources_share_only_dated_halt_facts_and_keep_prices_independent(store, source):
    full=bars(); first,last=market(store,full,source)
    hole=full.date.iloc[200]; data=full.drop(index=200)
    did=save_dataset(store,CODE,data,source,'不复权' if source=='tencent' else '前复权')
    q=describe_history(store,CODE,data,first,last,source=source);save_quality(store,did,q)
    audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily')
    assert not audit['scan_allowed'] and audit['gaps'][0]['category']=='input_gap'
    before=store.rows('SELECT * FROM datasets')
    assert save_status_rows(store,CODE,hole,hole,[status(hole)])
    # Old quality cannot grant eligibility after evidence changes until recertified.
    pending=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily',history_cache_only=True)
    assert not pending['scan_allowed']
    audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily',verify_files=True)
    assert audit['complete'] and audit['ready']==1 and audit['scan_allowed']
    assert store.rows('SELECT * FROM datasets')==before
    assert not store.rows('SELECT * FROM sync_coverage')
    assert not store.rows("SELECT key FROM meta WHERE key LIKE 'sync_coverage:%'")
    updated=quality_for(store,before[0]);assert hole in updated['suspended_dates'] and not updated['missing_dates']
    require_research_history(store,before[0],last)


@pytest.mark.parametrize('source', list(SOURCES))
def test_one_halt_does_not_excuse_another_unknown_gap_or_traded_day(store,source):
    full=bars();first,last=market(store,full,source)
    h1,h2=full.date.iloc[200],full.date.iloc[201]
    data=full.drop(index=[200,201]);did=save_dataset(store,CODE,data,source,'前复权')
    save_status_rows(store,CODE,h1,h2,[status(h1),status(h2,'1','10')])
    audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily')
    assert audit['gaps'][0]['dates']==[h2] and not audit['scan_allowed']
    if source!='baostock':
        with pytest.raises(ValueError,match='未认证缺口'):
            require_research_history(store,store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0],last)


@pytest.mark.parametrize('source', list(SOURCES))
def test_current_halt_excluded_and_short_daily_or_weekly_never_waived(store,source):
    full=bars();first,last=market(store,full,source)
    save_dataset(store,CODE,full.iloc[:-1],source,'前复权')
    save_status_rows(store,CODE,last,last,[status(last)])
    audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily')
    assert audit['suspended']==1 and audit['ready']==0 and not audit['eligible_ids']
    assert audit['complete']  # No tradable stock, not a failed network request.
    earlier=full.date.iloc[-2]
    save_directory(store,pd.DataFrame(dict(code=[CODE],tradeStatus=['unknown'])),earlier,source=source)
    weekly=audit_scope(store,['沪深主板'],earlier,source=source,timeframe='weekly')
    assert not weekly['scan_allowed'] and weekly['gaps'][0]['category']=='insufficient_bars'


@pytest.mark.parametrize('change', ['identity','duplicate','status','date','positive_volume','nan'])
def test_bad_status_never_becomes_halt(change):
    row=status('2026-10-09'); rows=[row]
    if change=='identity':row['code']='sz.000001'
    if change=='duplicate':rows.append(dict(row))
    if change=='status':row['tradestatus']='unknown'
    if change=='date':row['date']='2026-10-10'
    if change=='positive_volume':row['volume']='1'
    if change=='nan':row['volume']='nan'
    with pytest.raises(ValueError):validate_status_rows(CODE,'2026-10-09','2026-10-09',rows)


def test_vendor_empty_or_zero_volume_is_not_itself_halt_proof(store):
    dates=['2026-10-08','2026-10-09']
    save_status_rows(store,CODE,*dates,[status(dates[0],'1','0')])
    assert halt_evidence(store,CODE,*dates)==([],[])
    assert status_facts(store,CODE)[0]=={dates[0]:'1'}
    assert not save_status_rows(store,CODE,dates[1],dates[1],[])
    assert dates[1] not in status_facts(store,CODE)[0]


def test_conflicting_actual_bar_or_status_cannot_pass(store):
    data=bars();first,last=market(store,data,'tickflow');hole=data.date.iloc[200]
    save_status_rows(store,CODE,hole,hole,[status(hole)])
    with pytest.raises(ValueError,match='停牌公告冲突'):
        describe_history(store,CODE,data,first,last)
    with pytest.raises(ValueError,match='证据冲突'):
        save_status_rows(store,CODE,hole,hole,[status(hole,'1','100')])
    assert status_facts(store,CODE)[0][hole]=='0'


def test_tampered_evidence_is_rechecked_before_scan_even_after_cached_quality(store):
    full=bars();first,last=market(store,full,'tickflow');hole=full.date.iloc[200];data=full.drop(index=200)
    did=save_dataset(store,CODE,data,'tickflow','前复权')
    save_status_rows(store,CODE,hole,hole,[status(hole)])
    save_quality(store,did,describe_history(store,CODE,data,first,last))
    ref=store._trading_status_index[CODE][0];store.write_artifact(ref['path'],b'changed')
    audit=audit_scope(store,['沪深主板'],last,source='tickflow',timeframe='daily',verify_files=True)
    assert not audit['scan_allowed'] and '证据校验失败' in audit['gaps'][0]['error']


def test_explicit_status_query_excludes_prices_and_binds_identity():
    from unittest.mock import Mock,patch
    provider=DirectBaoStock();provider.bs=Mock()
    raw=pd.DataFrame([status('2026-10-09')])
    with patch.object(provider,'collect',return_value=raw):
        out=provider.trading_status(CODE,'2026-10-09','2026-10-09')
    assert provider.bs.query_history_k_data_plus.call_args.args==(CODE,'date,code,tradestatus,volume')
    assert set(out.columns)=={'date','code','tradestatus','volume'}


def test_verification_queries_once_then_only_new_dates_and_unknown_is_explicit_retry(store):
    calls=[]
    class Vendor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def trading_status(self,code,a,b):
            calls.append((code,a,b))
            rows=[] if a=='2026-10-08' else [status(a)]
            return pd.DataFrame(rows,columns=['date','code','tradestatus','volume'])
    first=verify_status_targets(store,{CODE:['2026-10-08']},Vendor)
    assert len(first['unknown'])==1 and not first['complete']
    verify_status_targets(store,{CODE:['2026-10-08']},Vendor)
    assert len(calls)==1  # Missing vendor status isn't polled indefinitely.
    second=verify_status_targets(store,{CODE:['2026-10-08','2026-10-09']},Vendor)
    assert calls[-1][1:] == ('2026-10-09','2026-10-09') and len(second['confirmed_halt'])==1
    verify_status_targets(store,{CODE:['2026-10-09']},lambda:pytest.fail('known fact opened network'))
    verify_status_targets(store,{CODE:['2026-10-08']},Vendor,retry=True)
    assert len(calls)==3 and not store.rows('SELECT * FROM datasets')


def test_network_failure_preserves_proof_and_stops_without_reconnect(store):
    from workbench.provider_guard import ProviderError
    calls=[]
    class Vendor:
        def __enter__(self):return self
        def __exit__(self,*args):calls.append('closed')
        def trading_status(self,*args):
            calls.append(args)
            raise ProviderError('baostock','trading_status','黑名单','10001011')
    report=verify_status_targets(store,{CODE:['2026-10-09'],'sh.600001':['2026-10-09']},Vendor)
    assert len(report['errors'])==1 and len(report['remaining'])==2 and len(calls)==2
    assert not report['complete'] and not status_facts(store,CODE)[0]
    with pytest.raises(ValueError,match='暂停请求'):
        verify_status_targets(store,{CODE:['2026-10-09']},lambda:pytest.fail('cooldown bypassed'))


def test_real_service_status_job_updates_fallback_receipt_without_new_prices(store,monkeypatch):
    from workbench.service import Service
    full=bars();first,last=market(store,full,'tickflow');hole=full.date.iloc[200]
    data=full.drop(index=200);did=save_dataset(store,CODE,data,'tickflow','前复权')
    save_quality(store,did,describe_history(store,CODE,data,first,last))
    calls=[]
    class Vendor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def trading_status(self,*args):calls.append(args);return pd.DataFrame([status(hole)])
    monkeypatch.setattr('workbench.market.BaoStock',Vendor)
    before=store.rows('SELECT * FROM datasets'); service=Service(store)
    try:
        job=wait_for(store,service.submit('verify_status',dict(source='tickflow',boards=['沪深主板'],
            asof=last,timeframe='daily',codes=[CODE])))
        report=json.loads(job['result'])
        assert job['status']=='completed',job['message']
        assert len(calls)==1 and report['coverage']['ready']==1 and report['price_downloads']==0
        receipt=json.loads(store.rows("SELECT value FROM meta WHERE key='tickflow_integrity'")[0]['value'])
        assert receipt['scan']['ready']==1 and receipt['scan']['gaps']==[]
        assert store.rows('SELECT * FROM datasets')==before and not store.rows('SELECT * FROM observations')
        assert not store.rows('SELECT * FROM plans')
    finally:service.pool.shutdown()


def test_baostock_download_keeps_explicit_status_without_inserting_flat_bars(store):
    from workbench.sync import sync_stock
    full=bars();first,last=market(store,full,'baostock');hole=full.date.iloc[200]
    class Vendor:
        def fetch(self,*args):
            out=full.drop(index=200).copy()
            out.attrs.update(returned_dates=full.date.tolist(),suspended_dates=[hole],trading_status_rows=[status(hole)])
            return out
    did,_=sync_stock(store,lambda:Vendor(),CODE,first,last)
    assert status_facts(store,CODE)[0][hole]=='0'
    record=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    assert record['rows']==399 and not quality_for(store,record)['missing_dates']
    assert audit_scope(store,['沪深主板'],last,source='baostock',timeframe='daily')['ready']==1


def test_readiness_is_not_granted_by_ui_ignore_or_three_equal_absences(store):
    from workbench.gap_review import classify_review,review_summary
    full=bars();hole=full.date.iloc[200];data=full.drop(index=200)
    for source in SOURCES:
        first,last=market(store,full,source)
        did=save_dataset(store,CODE,data,source,'前复权')
        save_quality(store,did,describe_history(store,CODE,data,first,last,source=source))
        audit=audit_scope(store,['沪深主板'],last,source=source,timeframe='daily')
        view=dict(source=source,asof=last,timeframe='daily',scan=audit)
        review=classify_review(view);store.set_gap_review_decisions(source,review['rows'],'ignored')
        review=classify_review(view,decisions=store.gap_review_decisions(source))
        assert review['pending']==0 and review['excluded']==1 and not review['scan_allowed']
        assert '不改变扫描资格' in review_summary(review)
    assert not status_facts(store,CODE)[0]


def test_status_receipt_migration_preserves_original_provenance_and_rejects_prices(store):
    import hashlib
    from workbench.trading_status import import_status_receipt
    payload=dict(version=1,source='baostock',code=CODE,start='2026-10-09',end='2026-10-09',
                 retrieved='2026-10-10T01:00:00+00:00',rows=[status('2026-10-09')])
    raw=dumps(payload).encode();sha=hashlib.sha256(raw).hexdigest()
    assert import_status_receipt(store,raw,sha)
    assert not import_status_receipt(store,raw,sha)
    facts,receipts=status_facts(store,CODE)
    assert facts=={'2026-10-09':'0'} and receipts[0]['verified']==payload['retrieved']
    assert not store.rows('SELECT * FROM datasets') and not store.rows('SELECT * FROM sync_coverage')
    with pytest.raises(ValueError,match='校验失败'):
        import_status_receipt(store,raw,'0'*64)
    payload['rows'][0]['close']='10'
    raw=dumps(payload).encode()
    with pytest.raises(ValueError,match='不能包含价格'):
        import_status_receipt(store,raw,hashlib.sha256(raw).hexdigest())


def test_status_review_rejects_invalid_middle_date_before_network(store):
    with pytest.raises(ValueError):
        verify_status_targets(store,{CODE:['2026-10-08','2026-10-08x','2026-10-09']},
                              lambda:pytest.fail('invalid dates must never connect'))


def test_old_quality_cache_is_not_saved_after_status_evidence_changes(store):
    from workbench.history_quality import save_cached_quality
    full=bars();first,last=market(store,full,'tickflow');hole=full.date.iloc[200]
    did=save_dataset(store,CODE,full.drop(index=200),'tickflow','前复权')
    record=store.rows('SELECT * FROM datasets WHERE id=?',(did,))[0]
    quality_for(store,record)
    save_status_rows(store,CODE,hole,hole,[status(hole)])
    assert save_cached_quality(store,[did],last)==0
    quality_for(store,record)
    assert save_cached_quality(store,[did],last)==1


def test_shared_status_facts_survive_restart_and_do_not_require_another_query(store):
    from workbench.store import Store
    from workbench.history_quality import save_cached_quality
    full=bars();hole=full.date.iloc[200];data=full.drop(index=200)
    records=[]
    for source in SOURCES:
        first,last=market(store,full,source)
        did=save_dataset(store,CODE,data,source,'不复权' if source=='tencent' else '前复权')
        records.append(did)
    save_status_rows(store,CODE,hole,hole,[status(hole)])
    fresh=Store(store.root)
    report=verify_status_targets(fresh,{CODE:[hole]},lambda:pytest.fail('restart queried known fact'))
    assert report['reused']==1 and report['queried']==0 and len(report['confirmed_halt'])==1
    for source in SOURCES:
        assert audit_scope(fresh,['沪深主板'],last,source=source,timeframe='daily',verify_files=True)['ready']==1
    assert save_cached_quality(fresh,records,last)==3
    reopened=Store(store.root)
    for source in SOURCES:
        assert audit_scope(reopened,['沪深主板'],last,source=source,timeframe='daily',history_cache_only=True)['ready']==1
