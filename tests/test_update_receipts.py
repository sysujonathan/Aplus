import json
import threading
from unittest.mock import Mock

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_tickflow_scan_readiness import bars, scope
from workbench.market import save_dataset, load_dataset
from workbench.sources import save_coverage, coverage_state, directory_file
from workbench.history_quality import describe_history, save_quality
from workbench.tickflow_integrity import integrity_summary
from workbench.tencent_service import retain_unconfirmed_members
from workbench.readiness import save_directory, audit_scope
from workbench.service import Service
from workbench.sync import sync_stock
from gui.toolbar import ToolBar


def test_receipt_distinguishes_scan_history_from_today_and_recommends_scan():
    gaps=([dict(category='input_gap')]*324+[dict(category='stale_tail')]*10+
          [dict(category='missing_dataset')]*2+[dict(category='insufficient_bars')]*16)
    view=dict(asof='2026-10-09',scan=dict(ready=2846,suspended=1,gaps=gaps,scan_allowed=True),
              retry_codes=list(range(334)),scan_repair_codes=['a','b'])
    message=integrity_summary(view)
    for fragment in ('可直接扫描 2846','暂不扫描 352','历史缺日 324','最新行情落后 10',
                     '缺整份行情 2','历史根数不足 16','不是都缺当天行情','不必等全部',
                     '可补拉 2','已补拉无改善 334'):
        assert fragment in message
    view['scan']['scan_allowed']=False
    assert '暂不能扫描' in integrity_summary(view)


def test_tencent_minor_catalog_omission_preserves_scope_and_resolves_on_retry(store,monkeypatch):
    data=bars(); codes=['sh.600000','sh.600001']; first,last=scope(store,data,codes)
    save_directory(store,pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*2)),first,source='tencent')
    calls=[]; returned=['sh.600000']
    class Provider:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def universe(self,*args):return pd.DataFrame(dict(code=returned,tradeStatus=['unknown']*len(returned)))
        def fetch(self,code,*args):calls.append(code); return data.copy()
    monkeypatch.setattr('workbench.tencent_service.Tencent',Provider)
    monkeypatch.setattr('workbench.tencent_service.local_public_catalog',lambda *args:(None,None))
    service=Service(store)
    spec=dict(source='tencent',boards=['沪深主板'],start=first,end=last)
    try:
        row=wait_for(store,service.submit('sync',spec)); result=json.loads(row['result'])
        assert row['status']=='partial' and calls==['sh.600000']
        assert result['scan_readiness']['expected']==2 and result['scan_readiness']['ready']==1
        assert result['scan_readiness']['gaps'][0]['category']=='directory_pending'
        assert result['integrity']['scan_repair_codes']==[]
        assert set(pd.read_csv(directory_file(store,'tencent')).code)==set(codes)
        # Even a perfectly current old snapshot cannot certify an absent identity.
        save_dataset(store,'sh.600001',data,'tencent','不复权')
        assert audit_scope(store,['沪深主板'],last,source='tencent')['ready']==1
        returned.append('sh.600001'); calls.clear()
        row=wait_for(store,service.submit('sync',spec))
        assert row['status']=='completed',row['message']
        assert json.loads(row['result'])['directory_pending']==[]
        assert not coverage_state(store,'sh.600000','tickflow')
    finally:service.pool.shutdown()


def test_large_or_invalid_tencent_catalog_still_stops(store):
    codes=[f'sh.{600000+i}' for i in range(10)]
    old=pd.DataFrame(dict(code=codes,tradeStatus=['unknown']*10))
    save_directory(store,old,'2026-10-08',source='tencent')
    content=directory_file(store,'tencent').read_bytes()
    with pytest.raises(ValueError,match='大量缺失'):
        retain_unconfirmed_members(store,old.iloc[:2],set(),'2026-10-09')
    with pytest.raises(ValueError,match='身份重复'):
        retain_unconfirmed_members(store,pd.concat([old,old]),set(),'2026-10-09')
    assert directory_file(store,'tencent').read_bytes()==content


def test_atomic_metadata_rollback_and_thread_isolation(store):
    with pytest.raises(ValueError,match='failure'):
        with store.atomic_write():
            store.execute("INSERT INTO meta VALUES('test-rollback','x')")
            store.event(None,'rollback test')
            assert store.rows("SELECT value FROM meta WHERE key='test-rollback'")
            observed=[]
            thread=threading.Thread(target=lambda:observed.extend(store.rows("SELECT value FROM meta WHERE key='test-rollback'")))
            thread.start();thread.join(5);assert not thread.is_alive() and not observed
            raise ValueError('failure')
    assert not store.rows("SELECT value FROM meta WHERE key='test-rollback'")
    assert not store.rows("SELECT seq FROM events WHERE action='rollback test'")
    with store.atomic_write():store.execute("INSERT INTO meta VALUES('test-rollback','ok')")
    assert store.rows("SELECT value FROM meta WHERE key='test-rollback'")[0]['value']=='ok'


def test_tickflow_write_unit_preserves_snapshot_and_rollback_never_advances_coverage(store,monkeypatch):
    data=bars();first,last=scope(store,data,['sh.600000'])
    old=data.iloc[:-1];old_end=old.date.iloc[-1]
    did=save_dataset(store,'sh.600000',old,'tickflow','前复权')
    save_coverage(store,'sh.600000',did,first,old_end,'tickflow')
    save_quality(store,did,describe_history(store,'sh.600000',old,first,old_end))
    class Provider:
        def fetch(self,*args):
            assert getattr(store._atomic_local,'connection',None) is None
            return data.iloc[-2:].copy()
    original=store.event
    def fail(job,action,*args,**kwargs):
        if action=='保存同步进度':raise ValueError('simulated write failure')
        return original(job,action,*args,**kwargs)
    monkeypatch.setattr(store,'event',fail)
    with pytest.raises(ValueError,match='simulated'):
        sync_stock(store,lambda:Provider(),'sh.600000',first,last,source='tickflow',batch_writes=True)
    assert coverage_state(store,'sh.600000','tickflow')[0]['dataset_id']==did
    assert len(store.rows("SELECT id FROM datasets WHERE source='tickflow'"))==1
    monkeypatch.setattr(store,'event',original)
    new,action=sync_stock(store,lambda:Provider(),'sh.600000',first,last,source='tickflow',batch_writes=True)
    assert action=='updated' and len(load_dataset(store,new)[0])==len(data)
    assert len(load_dataset(store,did)[0])==len(old)


def test_repair_timings_add_same_source_day_scope_only(monkeypatch):
    toolbar=Mock();toolbar._sync_totals={};toolbar._job_started_at=None
    toolbar._auto_scan_after_sync=False;toolbar._auto_started_at=None;toolbar._on_job_finished=None
    toolbar.service.submit.return_value='job-test'
    clock=[0.0];monkeypatch.setattr('gui.toolbar.time.monotonic',lambda:clock[0])
    spec=dict(source='tickflow',boards=['沪深主板'],end='2026-10-09')
    ToolBar._submit_job(toolbar,'sync','更新行情',spec)
    clock[0]=731;ToolBar._finish_job(toolbar,'partial',result_json='{}')
    assert toolbar._sync_elapsed==731
    ToolBar._submit_job(toolbar,'sync','更新行情',dict(spec,repair_codes=['sh.600000']))
    clock[0]=809;ToolBar._finish_job(toolbar,'partial',result_json='{}')
    assert toolbar._sync_elapsed==809
    toolbar._timing_var=Mock();toolbar._job_kind='更新行情'
    ToolBar._refresh_timing_status(toolbar,running_elapsed=1)
    assert '12分12秒' in toolbar._timing_var.set.call_args.args[0]  # base 731 + running 1
    for change in ({'source':'tencent'},{'end':'2026-10-10'},{'boards':['创业板']}):
        ToolBar._submit_job(toolbar,'sync','更新行情',dict(spec,repair_codes=['sh.600000'],**change))
        assert toolbar._job_sync_base==0
    ToolBar._submit_job(toolbar,'sync','更新行情',spec)
    assert toolbar._job_sync_base==0  # fresh normal update starts a new chain
