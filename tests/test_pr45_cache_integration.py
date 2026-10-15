"""Real archived V36.3 -> approved V36.4, same isolated dataset/date.

No Git history/network dependency, patched strategy source or formal data.
Only the structural pattern input is a synthetic SETUP_READY scenario; both
actual frozen MTR implementations, adapters and Service cache paths execute.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.test_workbench import wait_for, store, frame
from workbench.store import ROOT, Store, digest, dumps, now
from workbench.strategies import catalog, scan_engine_version
from workbench.scan_cache import calculation_versions, compatible_engines, cache_key, PR45_QUALITY_GATE
from workbench.service import Service
from gui.data import load_candidates
from gui.candidate_tabs import collapse_candidate_rows


FIXTURES = Path(__file__).parent/'fixtures/pr47_v363'
ARCHIVED = {
    'core/strategies/mtr_strategy.py':'68720522848c2fffc4bff505de77716a5256d48a88161a8d03bd19f7415e406a',
    'core/strategies/mtr_structural_v35.py':'9705da38307f6b68f6bcc1a9b5672765721074ef0a24977c963a8c3bdfd9f31e',
    'frozen_manifest.json':'8efd4dcc9d2e3e83d90ab48bb33a1b0d78cd188b337f6e99277517b1d26e3217',
    'workbench/strategies.py':'a3ebd4ba33a23ab2c8a4327171b6986c2db4dd871723011b7295376d7f82d3c9',
}
IDS = ['MTR_MASTER','STRATEGY_3K','STRATEGY_GAP_H2']


def pattern_engine(with_signal):
    class Pattern:
        def find_swing_points(self, _frame): return []
        def match_mtr_pattern(self, data, _swings, index):
            if index != len(data)-1: return None
            signal = dict(idx=index,high=float(data.iloc[-1].high),quality=90.,close_position=1.) if with_signal else None
            return dict(stage='SETUP_READY',score=80.,signal_bar=signal,points={})
    return Pattern()


def set_pattern(monkeypatch, with_signal):
    from core.strategies.mtr_strategy import MTRStrategy
    original = MTRStrategy.__init__
    def initialize(self,*args,**kwargs):
        original(self,*args,**kwargs)
        self.structural_engine = pattern_engine(with_signal)
    monkeypatch.setattr(MTRStrategy,'__init__',initialize)


BASELINE = '''
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.append(sys.argv[3])
from tests.test_pr45_cache_integration import pattern_engine
from tests.test_premarket_resume import seeded
from tests.test_workbench import wait_for
from core.strategies.mtr_strategy import MTRStrategy
from workbench.store import Store
from workbench.strategies import catalog,scan_engine_version
from workbench.scan_cache import calculation_versions
from workbench.service import Service
assert MTRStrategy.get_metadata()['strategy_revision']=='V36.3'
with_signal=bool(int(sys.argv[2]))
original=MTRStrategy.__init__
def initialize(self,*args,**kwargs):
    original(self,*args,**kwargs)
    self.structural_engine=pattern_engine(with_signal)
MTRStrategy.__init__=initialize
count=160
close=np.linspace(20.,10.,count)
data=pd.DataFrame(dict(date=pd.bdate_range('2026-01-01',periods=count).strftime('%Y-%m-%d'),
    open=close+.1,high=close+.5,low=close-.5,close=close,volume=1000.))
if with_signal:
    data.loc[count-1,'open']=9.9
    data.loc[count-1,'high']=10.
store=Store(sys.argv[1])
source=sys.argv[4]
start,day,ids=seeded(store,data,source)
keys=['MTR_MASTER','STRATEGY_3K','STRATEGY_GAP_H2']
entries=catalog(store)
service=Service(store)
try:
    spec=dict(source=source,boards=['沪深主板'],datasets=ids,strategies=keys,timeframe='daily',asof=day)
    row=wait_for(store,service.submit('scan',spec))
    assert row['status']=='completed',row
    report=json.loads(row['result'])
    assert report['calculated']==3 and report['reused']==0,report
    assert len(store.rows("SELECT id FROM observations WHERE strategy='MTR_MASTER'"))==1
    print(json.dumps(dict(spec=spec,versions=calculation_versions([entries[k] for k in keys]),
                         engine=scan_engine_version(include_frozen=False),release=entries[keys[0]].version)))
finally:
    service.pool.shutdown()
'''


def old_scan(tmp_path,source,with_signal):
    code = tmp_path/'historical-code'
    for folder in ('core','config','workbench'):
        shutil.copytree(ROOT/folder,code/folder,ignore=shutil.ignore_patterns('__pycache__'))
    for path,expected in ARCHIVED.items():
        text = (FIXTURES/(path.replace('/','__')+'.txt')).read_text(encoding='utf-8')
        content = text.replace('\n','\r\n').encode() if path.startswith('core/') else text.encode()
        assert digest(content) == expected, path
        (code/path).write_bytes(content)
    runtime = tmp_path/'isolated-runtime'
    env = dict(os.environ,A_WORKBENCH_HOME=str(runtime),PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable,'-c',BASELINE,str(runtime),str(int(with_signal)),str(ROOT),source],
        cwd=code,env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert result.returncode == 0, result.stdout+result.stderr
    return Store(runtime),json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('source',['baostock','tickflow','tencent'])
@pytest.mark.parametrize('with_signal',[False,True])
def test_v363_actual_cache_recalculates_mtr_only_and_preserves_history(tmp_path,monkeypatch,source,with_signal):
    store,baseline = old_scan(tmp_path,source,with_signal)
    spec = baseline['spec']; did = spec['datasets'][0]; day = spec['asof']
    before = store.rows('SELECT * FROM observations')
    old_mtr = next(r for r in before if r['strategy']=='MTR_MASTER')
    old_cache = store.rows('SELECT * FROM scan_cache')
    entries = catalog(store)
    assert entries['MTR_MASTER'].cls.get_metadata()['strategy_revision'] == 'V36.4'
    versions = calculation_versions([entries[k] for k in IDS])
    assert versions['MTR_MASTER'] != baseline['versions']['MTR_MASTER']
    assert all(versions[k] == baseline['versions'][k] for k in IDS[1:])
    assert scan_engine_version(include_frozen=False) != baseline['engine']  # PR45 output guard remains bound.
    compatible = compatible_engines([entries[k] for k in IDS])
    assert compatible == {k:baseline['engine'] for k in IDS[1:]}
    # No new complete judgment yet: archived MTR history is NOT called no-signal.
    assert load_candidates(store,'daily',source,tuple(day.split('-')))['MTR_MASTER'][0]['observation_id']==old_mtr['id']
    set_pattern(monkeypatch,with_signal)
    from workbench import service as module
    real_signal = module.signal_at_end
    calls = []
    def observed(strategy,*args,**kwargs):
        calls.append(strategy.id)
        return real_signal(strategy,*args,**kwargs)
    monkeypatch.setattr(module,'signal_at_end',observed)
    prices = store.rows('SELECT * FROM datasets')
    service = Service(store)
    try:
        row = wait_for(store,service.submit('scan',spec))
        report = json.loads(row['result'])
        assert row['status']=='completed',row['result']
        assert calls == ['MTR_MASTER']
        assert report['calculated']==1 and report['reused']==2
        assert not report.get('stage_reused')
        grouped = load_candidates(store,'daily',source,tuple(day.split('-')))
        if with_signal:
            assert len(grouped['MTR_MASTER']) == 1
            current = grouped['MTR_MASTER'][0]
            assert current['observation_id'] != old_mtr['id']
            assert collapse_candidate_rows(grouped['MTR_MASTER'])[0]['repeat_count'] == 1
            payload = json.loads(store.rows('SELECT payload FROM observations WHERE id=?',(current['observation_id'],))[0]['payload'])
            assert payload['signal_quality_rule']['strategy_revision']=='V36.4'
        else:
            assert not grouped.get('MTR_MASTER')
            key = cache_key(did,'MTR_MASTER',versions['MTR_MASTER'],'daily',day,scan_engine_version(False))
            assert store.rows('SELECT signal FROM scan_cache WHERE key=?',(key,))[0]['signal']=='null'
        for record in before:
            assert store.rows('SELECT * FROM observations WHERE id=?',(record['id'],))[0] == record
        for answer in old_cache:
            assert store.rows('SELECT * FROM scan_cache WHERE key=?',(answer['key'],))[0] == answer
        assert store.rows('SELECT * FROM datasets') == prices
        calls.clear()
        again = wait_for(store,service.submit('scan',spec))
        assert again['status']=='completed' and not calls
        assert json.loads(again['result'])['stage_reused']
    finally:
        service.pool.shutdown()


def test_pr45_bridge_does_not_accept_plugins_or_opted_in_quality_metadata(tmp_path):
    st = Store(tmp_path/'runtime')
    entries = catalog(st)
    assert 'MTR_MASTER' not in compatible_engines(entries.values())
    three = entries['STRATEGY_3K']
    plugin = replace(three,id='plugin',file=str(tmp_path/'plugin.py'))
    assert not compatible_engines([plugin])
    class OptedIn:
        @classmethod
        def get_metadata(cls): return {'signal_close_position_column':'quality'}
    assert not compatible_engines([replace(three,cls=OptedIn)])


def test_unknown_guard_edit_disables_bridge_and_shared_changes_invalidate_old_alias(monkeypatch,tmp_path):
    from workbench import strategies as adapters
    st = Store(tmp_path/'runtime'); entries = [catalog(st)['STRATEGY_3K']]
    original = compatible_engines(entries)
    real_source = __import__('inspect').getsource
    with monkeypatch.context() as patch:
        patch.setattr('workbench.scan_cache.inspect.getsource',lambda func:real_source(func).replace(
            'quality < quality_min','quality <= quality_min'))
        assert not compatible_engines(entries)
    with monkeypatch.context() as patch:
        patch.setattr(adapters,'_number',lambda value:0.)
        assert compatible_engines(entries) != original


def test_current_output_gate_stays_in_current_engine_fingerprint():
    from workbench import strategies as adapters
    import inspect
    source = inspect.getsource(adapters.calculate_prepared)
    assert source.count(PR45_QUALITY_GATE) == 1
    old = digest(adapters._scan_engine_semantics(source.replace(PR45_QUALITY_GATE,'')))
    assert old != scan_engine_version(include_frozen=False)


@pytest.mark.parametrize('source',['baostock','tickflow','tencent'])
@pytest.mark.parametrize('latest_signal',[False,True])
def test_latest_compatible_snapshot_wins_over_current_engine_older_snapshot(store,frame,source,latest_signal):
    from workbench.market import save_dataset
    from workbench.sources import ADJUSTMENTS
    day = frame.date.iloc[-1]
    earlier = save_dataset(store,'sh.600000',frame,source,ADJUSTMENTS[source])
    changed = frame.copy(); changed['volume'] += 10
    latest = save_dataset(store,'sh.600000',changed,source,ADJUSTMENTS[source])
    entry = catalog(store)['STRATEGY_3K']
    version = calculation_versions([entry])[entry.id]
    compatible = compatible_engines([entry])[entry.id]
    old_answer = dict(asof=day,setup_date=day,entry=20.,stop=19.,target=22.)
    new_answer = dict(old_answer,entry=21.) if latest_signal else None
    for did,engine,answer in [(earlier,scan_engine_version(False),old_answer if not latest_signal else None),
                              (latest,compatible,new_answer)]:
        key = cache_key(did,entry.id,version,'daily',day,engine)
        store.execute('INSERT INTO scan_cache VALUES(?,?,?)',(key,dumps(answer),now()))
    for identity,did,answer in [('earlier',earlier,old_answer),('latest',latest,new_answer)]:
        if answer:
            store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (identity,'job','sh.600000',entry.id,entry.version,'daily',day,day,did,dumps(answer),now()))
    rows = load_candidates(store,'daily',source,tuple(day.split('-'))).get(entry.id,[])
    assert len(rows) == int(latest_signal)
    if latest_signal:
        assert rows[0]['observation_id']=='latest'
    assert len(store.rows('SELECT * FROM observations')) == 1+int(latest_signal)
