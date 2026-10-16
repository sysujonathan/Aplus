"""Synthetic source facts/providers only; no network or formal data writes."""
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_desk import basic
from workbench.market import save_dataset, load_dataset
from workbench.readiness import save_directory, audit_scope
from workbench.sources import source_file, save_coverage, ADJUSTMENTS
from workbench.store import dumps, digest, now
from workbench.service import Service
from workbench.premarket_resume import certified_scope, continue_sync, price_repair_codes
from workbench.sync import sync_stock, repair_intervals
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.scan_cache import calculation_versions, cache_key
from workbench.strategies import catalog, scan_engine_version
from gui.candidate_tabs import candidate_repeat_counts, collapse_candidate_rows
from gui.data import load_candidates


def seeded(store, frame, source='baostock', holes=(), codes=('sh.600000',), status='1'):
    day=frame.date.iloc[-1]; start=frame.date.iloc[0]
    job='scope-'+source
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  (job,'sync','completed',now(),dumps(dict(source=source,boards=['沪深主板'],start=start,end=day)), '{}'))
    calendar=dict(start=start,end=day,trading_days=frame.date.tolist())
    store.write_artifact(str(source_file(store,'trading_calendar.json',source).relative_to(store.root)),dumps(calendar).encode(),job)
    directory=pd.DataFrame(dict(code=list(codes),code_name=['Test']*len(codes),tradeStatus=[status]*len(codes)))
    save_directory(store,directory,day,job,source=source,
                   basics=basic(list(codes)) if source=='baostock' else None)
    ids=[]
    for code in codes:
        bars=frame.drop(index=list(holes)).reset_index(drop=True)
        did=save_dataset(store,code,bars,source,ADJUSTMENTS[source],job)
        save_coverage(store,code,did,start,day,source)
        ids.append(did)
    return start,day,ids


def proxy(store, raw):
    return SimpleNamespace(store=store,_sync_once=raw,check_stop=lambda *_:None,
                           progress=lambda *_:None)


@pytest.mark.parametrize('source',['baostock','tickflow'])
def test_warm_complete_prices_use_zero_provider_requests_and_keep_data(store,frame,source):
    start,day,ids=seeded(store,frame,source)
    assert certified_scope(store,source,day)
    snapshots=store.rows('SELECT * FROM datasets')
    service=proxy(store,lambda *_:pytest.fail('已齐行情不应重新进入供应商'))
    result=continue_sync(service,'resume',dict(source=source,boards=['沪深主板'],start=start,end=day,scan_timeframe='daily'))
    assert result['skipped']==1 and result['connections']==0
    assert result['scan_readiness']['eligible_ids']==ids
    assert store.rows('SELECT * FROM datasets')==snapshots


@pytest.mark.parametrize('source',['baostock','tickflow'])
def test_plain_interior_hole_is_repaired_once_then_scanned_without_manual_steps(store,frame,source,monkeypatch):
    start,day,ids=seeded(store,frame,source,holes=(100,))
    calls=[]
    def raw(self,job,spec):
        calls.append(spec)
        assert spec['repair_codes']==['sh.600000'] and spec['repair_scope']=='scan' and not spec['force']
        class Provider:
            def fetch(self,code,a,b):
                return frame.loc[frame.date.between(a,b)].copy()
        did,outcome=sync_stock(self.store,lambda:Provider(),'sh.600000',start,day,job,
                               source=source,repair_timeframe='daily')
        audit=audit_scope(self.store,['沪深主板'],day,source=source,timeframe='daily',verify_files=True)
        return dict(success=1,datasets=[did],errors=audit['gaps'],scan_readiness=audit)
    scans=[]
    def scan(self,job,spec):
        audit=audit_scope(self.store,['沪深主板'],day,source=source,timeframe='daily',verify_files=True)
        assert audit['complete']
        scans.append(spec['source'])
        return dict(success=1,signals=0,asof=day)
    monkeypatch.setattr(Service,'_sync_once',raw)
    monkeypatch.setattr(Service,'_scan',scan)
    service=Service(store)
    try:
        spec=dict(sources=[source],boards=['沪深主板'],strategies=['STRATEGY_3K'],
                  timeframe='daily',start=start,end=day)
        first=wait_for(store,service.submit('premarket',spec))
        assert first['status']=='completed' and len(calls)==1 and scans==[source]
        assert len(store.rows('SELECT * FROM datasets'))==2  # Old snapshot intact.
        second=wait_for(store,service.submit('premarket',spec))
        assert second['status']=='completed' and len(calls)==1  # No repeated pull.
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('source',['baostock','tickflow'])
def test_unchanged_repair_is_not_a_loop_or_a_permission_to_scan(store,frame,source):
    start,day,ids=seeded(store,frame,source,holes=(100,))
    calls=[]
    def raw(_job,spec):
        calls.append(spec)
        audit=audit_scope(store,['沪深主板'],day,source=source,timeframe='daily',verify_files=True)
        return dict(success=1,datasets=ids,errors=audit['gaps'],scan_readiness=audit)
    service=proxy(store,raw)
    spec=dict(source=source,boards=['沪深主板'],start=start,end=day,scan_timeframe='daily')
    first=continue_sync(service,'a',spec)
    second=continue_sync(service,'b',spec)
    assert len(calls)==1 and not second['scan_readiness']['scan_allowed']
    assert second['resume']['unchanged']==['sh.600000']
    assert len(store.rows('SELECT * FROM datasets'))==1


@pytest.mark.parametrize('kind',['halt','short','identity','invalid','scope','outside'])
def test_only_ordinary_input_price_gaps_are_automatically_requested(store,frame,kind):
    start,day,ids=seeded(store,frame)
    categories={'halt':'current_halt','short':'insufficient_bars','identity':'directory_pending',
                'invalid':'invalid_file','scope':'input_gap','outside':'history_gap'}
    audit=dict(scope_valid=kind!='scope',expected_day=day,gaps=[dict(code='sh.600000',category=categories[kind])])
    assert price_repair_codes(store,'baostock',audit)[0]==[]


@pytest.mark.parametrize('changed',['directory','calendar','date','no_proof'])
def test_scope_reuse_requires_same_day_and_original_unchanged_write_proofs(store,frame,changed):
    start,day,ids=seeded(store,frame)
    if changed=='directory':
        (store.root/'universe.csv').write_bytes(b'bad directory')
    elif changed=='calendar':
        path=store.root/'trading_calendar.json'
        value=json.loads(path.read_text()); value['trading_days'].pop(50)
        path.write_text(json.dumps(value))
    elif changed=='date':
        store.execute("UPDATE meta SET value='2024-01-01' WHERE key='universe_date'")
    else:
        store.execute('DELETE FROM events')
    assert not certified_scope(store,'baostock',day)
    calls=[]
    result=continue_sync(proxy(store,lambda *_:calls.append(True) or {'success':0}), 'new',
                         dict(source='baostock',boards=['沪深主板'],start=start,end=day))
    assert calls==[True] and not result.get('scope_reused')


def test_unknown_failure_and_cooldown_do_not_trigger_second_download(store,frame):
    start,day,ids=seeded(store,frame,holes=(100,))
    calls=[]
    # Full-history explicitly retains original preflight; then its network
    # error cannot be followed by automatic targeted retries.
    service=proxy(store,lambda *_:calls.append(True) or dict(cooldown={'until':9999999999},success=0))
    continue_sync(service,'job',dict(source='baostock',boards=['沪深主板'],start=start,end=day,full_history=True))
    assert calls==[True]


def test_covered_sync_can_be_used_offline_during_cooldown_without_accessing_vendor(store,frame,monkeypatch):
    start,day,ids=seeded(store,frame)
    ProviderGuard(store,'baostock').failure(ProviderError('baostock','fetch','限制访问','429'))
    monkeypatch.setattr(Service,'_sync_once',lambda *_:pytest.fail('冷却期间不能建立连接'))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('sync',dict(source='baostock',boards=['沪深主板'],start=start,end=day,resume=True)))
        assert row['status']=='completed' and json.loads(row['result'])['connections']==0
    finally: service.pool.shutdown()


def test_selective_fallback_repair_preserves_long_history_and_uses_two_price_anchors(store,frame):
    start,day,ids=seeded(store,frame,'tencent',holes=(100,))
    calls=[]
    class Provider:
        def fetch(self,code,a,b):
            calls.append((a,b)); return frame.loc[frame.date.between(a,b)].copy()
    did,_=sync_stock(store,lambda:Provider(),'sh.600000',start,day,source='tencent',repair_timeframe='daily')
    bars,_=load_dataset(store,did)
    assert calls==[(frame.date.iloc[99],frame.date.iloc[101])]
    assert bars.date.tolist()==frame.date.tolist()


def test_tickflow_batch_subranges_use_existing_same_source_cache_without_network(frame,monkeypatch):
    from workbench.tickflow import TickFlow
    provider=TickFlow(); start,end=frame.date.iloc[20],frame.date.iloc[110]
    provider.cache[('sh.600000',start,end)]=frame.loc[frame.date.between(start,end)].copy()
    monkeypatch.setattr(provider,'preload',lambda *_a,**_k:pytest.fail('子区间重复拉取'))
    result=provider.fetch('sh.600000',frame.date.iloc[99],frame.date.iloc[101])
    assert result.date.tolist()==frame.date.iloc[99:102].tolist()
    result.loc[:,'close']=999
    assert provider.cache[('sh.600000',start,end)].close.max()<999


def test_new_no_signal_cache_suppresses_an_old_hit_but_retains_archived_observation(store,frame,monkeypatch):
    did=save_dataset(store,'sh.600000',frame,'baostock','前复权')
    day=frame.date.iloc[-1]; entry=catalog(store)['STRATEGY_3K']
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  ('old','job','sh.600000',entry.id,'old-version','daily',day,day,did,'{}',now()))
    versions=calculation_versions([entry]); engine=scan_engine_version(False)
    key=cache_key(did,entry.id,versions[entry.id],'daily',day,engine)
    store.execute('INSERT INTO scan_cache VALUES(?,?,?)',(key,'null',now()))
    assert load_candidates(store,'daily','baostock',tuple(day.split('-')))=={}
    assert len(store.rows('SELECT * FROM observations'))==1


def test_unrelated_release_change_does_not_hide_identical_finished_hit(store,frame):
    did=save_dataset(store,'sh.600000',frame,'baostock','前复权'); day=frame.date.iloc[-1]
    entry=catalog(store)['STRATEGY_3K']; signal=dict(asof=day,entry=22.)
    store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  ('old','job','sh.600000',entry.id,'earlier-release','daily',day,day,did,dumps(signal),now()))
    key=cache_key(did,entry.id,calculation_versions([entry])[entry.id],'daily',day,scan_engine_version(False))
    store.execute('INSERT INTO scan_cache VALUES(?,?,?)',(key,dumps(signal),now()))
    assert load_candidates(store,'daily','baostock',tuple(day.split('-')))[entry.id][0]['observation_id']=='old'


@pytest.mark.parametrize('source',['tickflow'])
def test_reused_prices_refresh_gap_receipt_for_current_board_without_download(store,frame,source):
    from workbench.gap_review import review_receipt
    start,day,_=seeded(store,frame,source,codes=('sh.600000','sz.300001'))
    service=proxy(store,lambda *_:pytest.fail('切换已齐范围不应重下载'))
    for board in ('沪深主板','创业板'):
        result=continue_sync(service,'resume',dict(source=source,boards=[board],start=start,end=day,
                                                  scan_timeframe='daily'))
        receipt=review_receipt(store,source,[board],day,'daily')
        assert result['connections']==0 and receipt['scan']['ready']==1
        assert receipt['boards']==[board] and receipt['periods']['daily']['complete']


def test_same_day_reruns_and_different_revisions_are_never_yellow():
    rows=[dict(code='sh.600000',date='2026-10-14',observation_id=str(i)) for i in range(3)]
    assert candidate_repeat_counts(rows)['sh.600000']==1
    assert collapse_candidate_rows(rows)[0]['repeat_count']==1
    rows.append(dict(code='sh.600000',date='2026-10-13',observation_id='earlier'))
    assert candidate_repeat_counts(rows)['sh.600000']==2
    assert candidate_repeat_counts([dict(code='sh.600000')])['sh.600000']==1


@pytest.mark.parametrize('source',['baostock','tickflow','tencent'])
@pytest.mark.parametrize('timeframe',['daily','weekly'])
def test_screenshot_single_day_two_versions_are_one_hit_without_deleting_history(store,frame,source,timeframe):
    day = frame.date.iloc[-1]
    for version in ('earlier-rule','current-rule'):
        did = save_dataset(store,'sz.002428',frame,source,ADJUSTMENTS[source],timeframe=timeframe)
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (version,'job','sz.002428','MTR_MASTER',version,timeframe,day,day,did,'{}',version))
    grouped = load_candidates(store,timeframe,source,tuple(day.split('-')))
    rows = grouped['MTR_MASTER']
    assert len(rows) == 1
    assert collapse_candidate_rows(rows)[0]['repeat_count'] == 1
    assert len(store.rows('SELECT id FROM observations')) == 2


def test_current_list_one_result_per_day_keeps_database_history(store,frame):
    did=save_dataset(store,'sh.600000',frame,'baostock','前复权'); day=frame.date.iloc[-1]
    for i in range(3):
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (str(i),'job','sh.600000','MTR_MASTER','v'+str(i),'daily',day,day,did,'{}',str(i)))
    rows=load_candidates(store,'daily','baostock',tuple(day.split('-')))['MTR_MASTER']
    assert len(rows)==1 and rows[0]['observation_id']=='2'
    assert len(store.rows('SELECT * FROM observations'))==3


def test_legacy_finished_no_signal_judgment_is_migrated_without_recalculation(store,frame,monkeypatch):
    did=save_dataset(store,'sh.600000',frame,'csv','不复权'); day=frame.date.iloc[-1]
    entry=catalog(store)['STRATEGY_3K']
    old_key=cache_key(did,entry.id,entry.version,'daily',day,scan_engine_version())
    store.execute('INSERT INTO scan_cache VALUES(?,?,?)',(old_key,'null',now()))
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *_a,**_k:pytest.fail('已完成无命中也须复用'))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('scan',dict(datasets=[did],strategies=[entry.id],timeframe='daily',asof=day)))
        result=json.loads(row['result'])
        assert result['reused']==1 and result['calculated']==0 and result['no_signal']==1
        assert len(store.rows('SELECT * FROM scan_cache'))==2
    finally: service.pool.shutdown()


def test_mtr_only_iteration_invalidates_mtr_not_unrelated_detectors(tmp_path):
    root=tmp_path/'synthetic-code'; root.mkdir()
    content={'core/calculator.py':'x=1\n','core/strategies/base.py':'class Base: pass\n',
             'core/strategies/mtr_structural.py':'x=1\n',
             'core/strategies/mtr_strategy.py':'from .base import Base\nfrom .mtr_structural import x\n',
             'core/strategies/gap_h2_strategy.py':'from .base import Base\n'}
    def write():
        files=[]
        for path,text in content.items():
            target=root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(text.encode())
            files.append(dict(path=path,sha256=digest(text.encode())))
        (root/'frozen_manifest.json').write_text(json.dumps(dict(files=files)))
    write()
    entries=[SimpleNamespace(id=id,params={},version='release-v1',file=str(root/path)) for id,path in
             [('MTR','core/strategies/mtr_strategy.py'),('GH2','core/strategies/gap_h2_strategy.py')]]
    first=calculation_versions(entries,root)
    content['core/strategies/mtr_structural.py']='x=2\n'; write()
    for entry in entries: entry.version='release-v2'
    second=calculation_versions(entries,root)
    assert second['MTR']!=first['MTR'] and second['GH2']==first['GH2']
    content['core/calculator.py']='x=2\n'; write()
    third=calculation_versions(entries,root)
    assert all(third[id]!=second[id] for id in ('MTR','GH2'))


@pytest.mark.parametrize('missing',['none','answer','observation'])
def test_completed_stage_requires_intact_cache_and_observations(store,frame,monkeypatch,missing):
    did=save_dataset(store,'sh.600000',frame,'csv','不复权'); day=frame.date.iloc[-1]
    calls=[]
    signal=dict(asof=day,setup_date=day,entry=22.,stop=20.,target=26.)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *_a,**_k:calls.append(True) or signal)
    service=Service(store)
    try:
        spec=dict(datasets=[did],strategies=['STRATEGY_3K'],timeframe='daily',asof=day)
        first=wait_for(store,service.submit('scan',spec))
        assert first['status']=='completed' and calls==[True]
        if missing=='answer': store.execute('DELETE FROM scan_cache')
        if missing=='observation': store.execute('DELETE FROM observations')
        second=wait_for(store,service.submit('scan',spec)); result=json.loads(second['result'])
        assert second['status']=='completed' and len(store.rows('SELECT * FROM observations'))==1
        assert result.get('stage_reused',False)==(missing=='none')
        assert len(calls)==(2 if missing=='answer' else 1)
    finally: service.pool.shutdown()


def test_cancel_during_automatic_repair_keeps_prices_and_does_not_scan(store,frame,monkeypatch):
    start,day,_=seeded(store,frame,holes=(100,))
    def raw(self,job,spec):
        did=save_dataset(self.store,'sh.600000',frame,'baostock','前复权',job)
        save_coverage(self.store,'sh.600000',did,start,day,'baostock')
        self.cancel_flags[job].set()
        self.check_stop(job)
    monkeypatch.setattr(Service,'_sync_once',raw)
    monkeypatch.setattr(Service,'_scan',lambda *_:pytest.fail('取消后不能继续扫描'))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('premarket',dict(sources=['baostock'],boards=['沪深主板'],
             strategies=['STRATEGY_3K'],timeframe='daily',start=start,end=day)))
        assert row['status']=='cancelled' and len(store.rows('SELECT * FROM datasets'))==2
    finally: service.pool.shutdown()


def test_cold_network_failure_does_not_request_interior_hole_again(store,frame):
    start,day,_=seeded(store,frame,holes=(100,))
    # Force normal preflight but retain genuine scope/price audit facts.
    store.execute('DELETE FROM events')
    audit=audit_scope(store,['沪深主板'],day,source='baostock',timeframe='daily',verify_files=True)
    calls=[]
    def raw(*_):
        calls.append(True)
        return dict(success=0,scan_readiness=audit,errors=[dict(code='sh.600000',error='网络中断')])
    continue_sync(proxy(store,raw),'cold',dict(source='baostock',boards=['沪深主板'],start=start,end=day))
    assert calls==[True]


def test_cooldown_blocks_auto_repair_not_already_verified_stock_scan(store,frame,monkeypatch):
    start,day,_=seeded(store,frame,codes=('sh.600000','sh.600001'))
    did=save_dataset(store,'sh.600001',frame.drop(index=100),'baostock','前复权')
    save_coverage(store,'sh.600001',did,start,day,'baostock')
    ProviderGuard(store,'baostock').failure(ProviderError('baostock','fetch','限制访问','429'))
    monkeypatch.setattr(Service,'_sync_once',lambda *_:pytest.fail('冷却期间不能补拉'))
    seen=[]
    monkeypatch.setattr('workbench.service.signal_at_end',lambda _s,data,**_k:seen.append(data.attrs['code']))
    service=Service(store)
    try:
        row=wait_for(store,service.submit('premarket',dict(sources=['baostock'],boards=['沪深主板'],
             strategies=['STRATEGY_3K'],timeframe='daily',start=start,end=day)))
        result=json.loads(row['result'])
        assert row['status']=='partial' and seen==['sh.600000']
        assert result['sources']['baostock']['sync_result']['resume']['waiting_for_cooldown']
        assert len(store.rows('SELECT * FROM datasets'))==3
    finally: service.pool.shutdown()


def test_one_strategy_iteration_recalculates_only_affected_judgment(store,frame,monkeypatch):
    did=save_dataset(store,'sh.600000',frame,'csv','不复权'); day=frame.date.iloc[-1]
    entries=catalog(store); ids=['STRATEGY_3K','STRATEGY_GAP_H2']; calls=[]
    monkeypatch.setattr('workbench.service.catalog',lambda *_:entries)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda s,*_a,**_k:calls.append(s.id))
    service=Service(store)
    try:
        spec=dict(datasets=[did],strategies=ids,timeframe='daily',asof=day)
        first=wait_for(store,service.submit('scan',spec))
        assert first['status']=='completed' and set(calls)==set(ids)
        for id in ids:
            entries[id]=replace(entries[id],version='new-release')
        entries['STRATEGY_3K']=replace(entries['STRATEGY_3K'],params={'iteration':1})
        calls.clear()
        second=wait_for(store,service.submit('scan',spec)); result=json.loads(second['result'])
        assert second['status']=='completed' and calls==['STRATEGY_3K']
        assert result['reused']==1 and result['calculated']==1
        assert len(store.rows('SELECT * FROM datasets'))==1
    finally: service.pool.shutdown()
