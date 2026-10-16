import json
import os
import threading
import time

import pytest

from tests.test_workbench import store, frame, wait_for
from workbench.market import load_dataset, save_dataset
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.scan_reads import ScanReads
from workbench.service import Service


def multi_spec():
    return dict(sources=['baostock','tickflow'], boards=['沪深主板'],
                strategies=['STRATEGY_3K'], timeframe='daily', start='2024-01-01')


@pytest.mark.parametrize('checkpoint_delay', [0, 3.5])
def test_sources_download_independently_and_fast_source_scans_before_slow_finishes(store, monkeypatch, checkpoint_delay):
    slow_started, fast_scanned, release_slow = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def sync(self, job, spec):
        calls.append(('sync', spec['source'], spec['end']))
        if spec['source'] == 'baostock':
            slow_started.set()
            assert release_slow.wait(20), '测试未释放慢源'
        else:
            assert slow_started.wait(20)
        self.progress(job, 1, 1, '已更新')
        return dict(source=spec['source'], success=1)
    def scan(self, job, spec):
        calls.append(('scan', spec['source'], spec['asof']))
        from workbench.sources import set_market_source, market_source
        set_market_source(self.store, 'tencent')
        assert market_source(self.store)=='tencent'
        self.progress(job, 1, 1, '已扫描')
        if spec['source'] != 'baostock' and not fast_scanned.is_set():
            # Assert ordering, not a three-second CI disk/scheduling budget.
            # A sequential implementation cannot reach this point until the
            # test releases the slow vendor, and will still fail deterministically.
            assert slow_started.is_set() and not release_slow.is_set()
            time.sleep(checkpoint_delay)
            assert not release_slow.is_set()
            fast_scanned.set()
        return dict(asof=spec['asof'], success=1, signals=1)
    monkeypatch.setattr(Service, '_sync', sync)
    monkeypatch.setattr(Service, '_scan', scan)
    service = Service(store)
    try:
        job = service.submit('premarket', multi_spec())
        assert fast_scanned.wait(20), '快源扫描被尚未释放的慢源阻塞'
        release_slow.set()
        row = wait_for(store, job)
        assert row['status']=='completed', row['result']
        result = json.loads(row['result'])
        assert set(result['sources']) == {'baostock','tickflow'}
        assert len({day for _, _, day in calls}) == 1
        assert len(store.rows("SELECT id FROM jobs WHERE kind IN ('sync','scan')"))==4
        assert store.rows("SELECT value FROM meta WHERE key='daily_market_source'")[0]['value']=='tencent'
        assert all(r['progress']==r['total']==1 for r in store.rows(
            "SELECT progress,total FROM jobs WHERE kind IN ('sync','scan')"))
    finally:
        release_slow.set()
        service.pool.shutdown()


def test_display_source_switch_still_blocks_unrelated_active_jobs(store):
    from workbench.sources import set_market_source
    from workbench.store import dumps
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('parent','premarket','running','now',dumps(multi_spec())))
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('child','sync','running','now',dumps(dict(source='baostock',premarket_parent='parent'))))
    set_market_source(store,'tickflow')
    store.execute('UPDATE jobs SET spec=? WHERE id=?',('{}','child'))
    with pytest.raises(ValueError,match='等待'):
        set_market_source(store,'tencent')
    store.execute("DELETE FROM jobs WHERE id='parent'")
    with pytest.raises(ValueError,match='等待'):
        set_market_source(store,'tencent')


def test_one_provider_cooldown_does_not_stop_other_sources_or_scan_its_old_cache(store, monkeypatch):
    scans = []
    def sync(self, job, spec):
        if spec['source']=='baostock':
            raise ProviderError('baostock','fetch','限制访问','429')
        return dict(success=1)
    def scan(self, job, spec):
        scans.append(spec['source'])
        return dict(asof=spec['asof'], success=1)
    monkeypatch.setattr(Service, '_sync', sync)
    monkeypatch.setattr(Service, '_scan', scan)
    service = Service(store)
    try:
        row = wait_for(store, service.submit('premarket', multi_spec()))
        result = json.loads(row['result'])
        assert row['status']=='partial'
        assert set(scans)=={'tickflow'}
        assert result['sources']['baostock']['sync_status']=='failed'
        assert ProviderGuard(store,'baostock').state()['last_error']['error_code']=='429'
        assert not ProviderGuard(store,'tickflow').state()
    finally:
        service.pool.shutdown()


def test_cancel_multisource_keeps_child_receipts_and_prevents_scanning(store, monkeypatch):
    entered = threading.Event()
    def sync(self, job, spec):
        entered.set()
        self.cancel_flags[job].wait(3)
        self.check_stop(job)
    monkeypatch.setattr(Service, '_sync', sync)
    monkeypatch.setattr(Service, '_scan', lambda *_a, **_k: pytest.fail('取消后扫描'))
    service = Service(store)
    try:
        job = service.submit('premarket', multi_spec())
        assert entered.wait(3)
        service.cancel(job)
        row = wait_for(store, job)
        assert row['status']=='cancelled'
        assert not store.rows("SELECT id FROM jobs WHERE status IN ('running','queued')")
    finally:
        service.pool.shutdown()


def test_scan_parse_reuse_remains_bounded_copied_and_hash_checked(store, frame, monkeypatch):
    did = save_dataset(store,'sh.600000',frame,'csv','不复权')
    store._scan_reads = ScanReads(limit=10**7)
    first, record = load_dataset(store,did)
    first.iloc[0, first.columns.get_loc('close')] = 999
    monkeypatch.setattr('workbench.market.pd.read_csv', lambda *_a, **_k: pytest.fail('重复解析'))
    second, _ = load_dataset(store,did)
    assert second.iloc[0].close != 999
    (store.root/record['path']).write_bytes(b'tampered fixture')
    with pytest.raises(ValueError, match='校验'):
        load_dataset(store,did)
    small = ScanReads(limit=1)
    small.put(did,frame)
    assert not small.frames and small.bytes==0


def test_long_backup_filename_uses_short_atomic_temp_path(tmp_path):
    from workbench.store import Store
    root = tmp_path / ('x' * max(1,135-len(str(tmp_path))-1))
    store = Store(root)
    relative = 'quarantine/tickflow/' + 'a'*64 + '-' + 'b'*16 + '.bin'
    path = store.write_artifact(relative,b'damaged fixture')
    assert path.read_bytes()==b'damaged fixture'
    store.write_artifact(relative,b'replacement fixture')
    assert path.read_bytes()==b'replacement fixture'
    assert not list(path.parent.glob('*.tmp'))


def test_warm_scan_skips_parse_but_still_rejects_tampered_files(store, frame, monkeypatch):
    did = save_dataset(store,'sh.600000',frame,'csv','不复权')
    spec = dict(datasets=[did], strategies=['STRATEGY_3K'], timeframe='daily', asof=frame.date.iloc[-1])
    service = Service(store)
    try:
        first = wait_for(store,service.submit('scan',spec))
        assert first['status']=='completed'
        monkeypatch.setattr('workbench.service.load_dataset',lambda *_a, **_k: pytest.fail('热缓存重复解析'))
        second = wait_for(store,service.submit('scan',spec))
        assert second['status']=='completed'
        assert json.loads(second['result'])['reused']==1
        record=store.rows('SELECT path FROM datasets WHERE id=?',(did,))[0]
        (store.root/record['path']).write_bytes(b'tampered fixture')
        third=wait_for(store,service.submit('scan',spec))
        assert third['status']=='partial' and '校验' in third['result']
    finally:
        service.pool.shutdown()


def test_cold_daily_weekly_audit_and_scan_parse_same_snapshot_once(store, monkeypatch):
    import io
    from tests.test_tickflow_scan_readiness import bars, scope, snapshot
    data=bars(900)
    _, last=scope(store,data,['sh.600000'])
    did=snapshot(store,'sh.600000',data,data.date.iloc[0],last)
    store.execute('DELETE FROM meta WHERE key=?',('market_quality:'+did,))
    import pandas as pd
    original=pd.read_csv
    calls=[]
    def counted(source,*args,**kwargs):
        if isinstance(source,io.BytesIO): calls.append(1)
        return original(source,*args,**kwargs)
    monkeypatch.setattr('workbench.market.pd.read_csv',counted)
    monkeypatch.setattr('workbench.service.signal_at_end',lambda *_a,**_k: None)
    service=Service(store)
    try:
        row=wait_for(store,service.submit('scan',dict(source='tickflow',boards=['沪深主板'],
            datasets=[did],strategies=['STRATEGY_3K','STRATEGY_STRUCTURAL_GAP'],
            timeframes=['daily','weekly'],asof=last)))
        assert row['status']=='completed',row['result']
        assert len(calls)==1
        assert service.store._scan_reads is None
    finally:
        service.pool.shutdown()


@pytest.mark.skipif(os.name!='nt', reason='Windows Tk layout')
@pytest.mark.parametrize('scale',[1.0,1.5,2.0])
def test_split_button_keeps_original_width_and_source_choices_persist(store, scale):
    # Production has one root. Verify each DPI in its own Tcl lifetime rather
    # than repeatedly recreating Windows interpreters in the full pytest run.
    import subprocess
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-c',
        'from tests.test_premarket_flow import check_split_button; '
        f'check_split_button({str(store.root)!r}, {scale!r})'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert result.returncode==0, result.stdout+result.stderr


def check_split_button(path, scale):
    import ttkbootstrap as ttk
    from workbench.store import Store
    from gui.toolbar import ToolBar
    store=Store(path)
    root=ttk.Window(themename='darkly')
    root.tk.call('tk','scaling',scale)
    try:
        root.geometry('1800x300')
        toolbar=ToolBar(root,store=store)
        toolbar.pack(fill='x')
        root.update_idletasks()
        assert toolbar._auto_host.winfo_width()==toolbar._auto_original_width
        assert toolbar.btn_auto.cget('text')=='一键'
        assert toolbar.btn_auto_menu.cget('text')==''
        assert toolbar.btn_auto.winfo_width() >= toolbar.btn_auto.winfo_reqwidth()
        assert toolbar.btn_auto_menu.winfo_width() >= toolbar.btn_auto_menu.winfo_reqwidth()
        assert toolbar.btn_auto.winfo_x()+toolbar.btn_auto.winfo_width() <= toolbar.btn_auto_menu.winfo_x()
        toolbar.auto_source_vars['tickflow'].set(True)
        toolbar._save_auto_sources()
        assert store.premarket_sources()==['baostock','tickflow']
        for v in toolbar.auto_source_vars.values(): v.set(False)
        toolbar._save_auto_sources()
        assert any(v.get() for v in toolbar.auto_source_vars.values())
    finally:
        root.destroy()
