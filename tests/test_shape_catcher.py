"""Actual local snapshot -> durable dated research -> existing watchlist."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from PIL import Image,ImageDraw

from workbench.store import Store,digest
from workbench.market import save_dataset
from workbench.shape_matching import classic,compare,keywords,drawing_path,TEMPLATES
from workbench.shape_scan import scan,FrameCache
from workbench.shape_image import candle_contour


def frame(key='w_bottom',end='2026-10-20',count=180):
    closes=np.r_[np.linspace(20,18,count-60),18+np.asarray(classic(key))*5]
    return pd.DataFrame(dict(date=pd.bdate_range(end=end,periods=count).strftime('%Y-%m-%d'),
        open=closes*.999,high=closes*1.01,low=closes*.99,close=closes,volume=np.full(count,1000.)))


def template(store,key='w_bottom',**changes):
    return store.save_shape_template(dict(name=TEMPLATES[key][0],kind='classic',path=classic(key),
        origin={'classic_key':key},settings={'threshold':0,'topn':10},**changes))


def test_six_templates_and_explicit_text_mapping():
    assert len(TEMPLATES)==6
    assert keywords('向下三推后双底')==['w_bottom']
    assert keywords('完全不认识的描述')==[]
    for key in TEMPLATES:
        values=np.asarray(classic(key))
        assert compare(15+values*3,values)['score']==100
        assert compare(np.ones(60),values)['score']==0
    with pytest.raises(ValueError):drawing_path([(10,1),(2,3),(20,5)])
    assert len(drawing_path([(0,100),(50,10),(100,90)]))==120


def test_no_schema_or_formal_signal_writes_and_history_survives_upgrade(tmp_path):
    store=Store(tmp_path)
    tables=store.rows("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
    raw=frame();save_dataset(store,'600000',raw,'baostock','qfq')
    t=template(store)
    first=scan(store,[t],'2026-10-20','baostock')
    second=scan(store,[t],'2026-10-19','baostock')
    assert first['status']=='completed' and first['mode']=='observed'
    assert second['mode']=='replay'
    assert second['matches'][0]['end']=='2026-10-19'
    assert Store(tmp_path).shape_run(first['id'])['matches']==first['matches']
    assert store.rows('SELECT * FROM observations')==store.rows('SELECT * FROM plans')==[]
    assert tables==store.rows("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
    assert len(store.shape_candidates(('2026','10','20'),'baostock'))==1
    assert len(store.shape_runs())==2


def test_prefix_never_reads_future_prices_and_source_isolated(tmp_path):
    store=Store(tmp_path);raw=frame()
    save_dataset(store,'600000',raw,'baostock','qfq')
    save_dataset(store,'000001',frame('top_reversal'),'tickflow','qfq')
    t=template(store)
    before=scan(store,[t],'2026-10-01','baostock')
    raw.loc[raw.date>'2026-10-01',['open','high','low','close']]*=4
    save_dataset(store,'600000',raw,'baostock','qfq')
    after=scan(store,[t],'2026-10-01','baostock')
    assert before['matches'][0]['score']==after['matches'][0]['score']
    assert {m['code'] for m in after['matches']}=={'sh.600000'}
    assert after['matches'][0]['dataset_id']!=before['matches'][0]['dataset_id']
    assert store.shape_candidates(('2026','10','01'),'tickflow')==[]
    with pytest.raises(ValueError,match='只到'):scan(store,[t],'2026-10-21','baostock')


def test_checksum_failure_and_stale_stock_not_reported_as_no_signal(tmp_path):
    store=Store(tmp_path);t=template(store)
    identifier=save_dataset(store,'600000',frame(),'baostock','qfq')
    save_dataset(store,'000001',frame(end='2026-10-19'),'baostock','qfq')
    rec=store.rows('SELECT * FROM datasets WHERE id=?',(identifier,))[0]
    (store.root/rec['path']).write_bytes(b'altered')
    report=scan(store,[t],'2026-10-20','baostock')
    assert report['status']=='partial' and len(report['errors'])==1
    assert len(report['skipped'])==1 and not report['matches']
    assert 'SHA' in report['errors'][0]['reason']


def test_multiple_templates_and_watches_keep_all_evidence_and_existing_notes(tmp_path):
    store=Store(tmp_path);save_dataset(store,'600000',frame(),'baostock','qfq')
    a,b=template(store),template(store,'top_reversal')
    report=scan(store,[a,b],'2026-10-20','baostock')
    assert len(report['matches'])==2
    store.add_manual_watch('600000','浦发银行','原备注')
    for match in report['matches']:store.watch_shape_match(report['id'],match['id'])
    watches=store.rows('SELECT * FROM watchlist')
    assert len(watches)==1 and watches[0]['notes']=='原备注'
    evidence=json.loads(store.rows("SELECT value FROM meta WHERE key='shape_watch:sh.600000'")[0]['value'])
    assert len(evidence)==2
    assert not store.rows('SELECT * FROM plans')


def test_case_template_reconstructs_exact_real_prefix_and_retains_versions(tmp_path):
    store=Store(tmp_path)
    identifier=save_dataset(store,'600000',frame(),'baostock','qfq')
    record=store.rows('SELECT * FROM datasets WHERE id=?',(identifier,))[0]
    value=dict(kind='case',name='向下三推后双底',path=[1,2,3],origin=dict(case_id='shared:real',
        dataset_id=identifier,sha256=record['sha256'],start='2026-07-01',cutoff='2026-08-03'))
    original=store.save_shape_template(value)
    assert original['origin']['cutoff']=='2026-08-03'
    assert len(original['path'])>3
    changed=store.save_shape_template(dict(original,name='案例模板第二版'),original['revision'])
    assert changed['version']!=original['version']
    assert len(store.rows("SELECT * FROM meta WHERE key LIKE 'shape_template:%'"))==2
    with pytest.raises(ValueError,match='另一窗口'):
        store.save_shape_template(dict(original,name='stale'),original['revision'])
    with pytest.raises(ValueError):
        store.save_shape_template(dict(value,origin=dict(value['origin'],sha256='wrong')))


def test_cancel_receipt_is_resumable_and_daily_inputs_reuse(tmp_path):
    store=Store(tmp_path)
    for code in ('600000','600001','600002'):save_dataset(store,code,frame(),'baostock','qfq')
    t=template(store);cache=FrameCache()
    counter=[0]
    def cancel():
        counter[0]+=1
        if counter[0]>1:raise InterruptedError()
    partial=scan(store,[t],'2026-10-20','baostock',cancel=cancel,cache=cache)
    assert partial['status']=='cancelled' and len(partial['inputs'])==1
    final=scan(store,[t],'2026-10-20','baostock',checkpoint=partial['id'],cache=cache)
    assert final['status']=='completed' and len(final['matches'])==3
    assert final['timings']['score_reused']==1 and final['timings']['parsed_reused']>=1
    assert not store.shape_candidates(('2026','10','20'),'tickflow')


def test_screenshot_requires_regular_bodies_and_explicit_confirmation():
    image=Image.new('RGB',(600,180),(15,20,28));draw=ImageDraw.Draw(image)
    for i in range(30):
        y=int(80+30*np.sin(i/5))
        draw.rectangle((10+i*19,y,15+i*19,y+12),fill='white' if i%2 else (50,95,220))
    result=candle_contour(image)
    assert result['candles']==30 and result['requires_confirmation'] is True
    assert 'OHLC' in result['message']
    with pytest.raises(ValueError):candle_contour(Image.new('RGB',(600,180),'black'))


def test_background_tracking_yields_to_business_job_and_never_uses_remote(tmp_path):
    from workbench.service import Service
    store=Store(tmp_path);save_dataset(store,'600000',frame(),'baostock','qfq')
    template(store,tracking=True)
    service=Service(store);runner=service.shape_tasks()
    busy=threading.Event()
    with service.lock:service.cancel_flags['formal-scan']=busy
    runner.tracked()
    time.sleep(.15)
    assert not store.shape_runs()
    with service.lock:service.cancel_flags.pop('formal-scan')
    deadline=time.monotonic()+10
    while runner.active and time.monotonic()<deadline:time.sleep(.02)
    assert runner.status()['state']=='completed'
    assert len(store.shape_runs())==1
    assert not store.rows('SELECT * FROM observations')
    runner.close();service.pool.shutdown()


def test_pa_case_entry_only_exports_identity_not_images_or_credentials(tmp_path):
    from gui.growth_bridge import GrowthBridge
    store=Store(tmp_path)
    buffer=__import__('io').BytesIO();Image.new('RGB',(120,90),'white').save(buffer,format='PNG')
    case=store.pa_save_case([dict(data=buffer.getvalue(),name='case.png')],dict(title='双底',symbol='600000'))
    bridge=GrowthBridge(store);bridge._find_similar=Mock()
    # Use the guarded public boundary just like the real page.
    result=bridge.find_similar(case['id'])
    assert result.get('ok',True) is True
    exported=bridge._find_similar.call_args.args[0]
    assert set(exported)=={'id','title','symbol','timeframe'}
