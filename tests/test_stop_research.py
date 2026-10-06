from copy import deepcopy
from dataclasses import replace
import json

import pandas as pd
import pytest

from tests.test_h2_plan import h2_bars, h2_spec, append_bar
from tests.test_workbench import wait_for
from workbench.backtest import Assumptions
from workbench.h2_replay import H2Settings, MODEL as BASE_MODEL, run_replay
from workbench.h2_replay_service import load_receipt
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store
from workbench.stop_research import AnchorSettings, MODEL, candidate_anchor, compare_trade, replay_fixed_stop, summarize
from workbench.stop_research_service import load_experiment
from workbench.strategies import verify_frozen
from gui.stop_research_chart import render_comparison

ZERO=Assumptions(commission_bps=0,sell_tax_bps=0,slippage_bps=0)


def example(h2_bars,h2_spec,ending='rescue'):
    bars=h2_bars.copy();bars.attrs=h2_bars.attrs.copy()
    bars.loc[123:125,'low']=[5.,6.,7.]
    bars=append_bar(bars,11.8,10.8) # same original fill
    if ending=='rescue':
        bars=append_bar(bars,12.,10.)
        bars=append_bar(bars,16.,11.)
    elif ending=='loss':
        bars=append_bar(bars,12.,10.)
        bars=append_bar(bars,11.,4.8)
    elif ending=='mm':
        bars=append_bar(bars,16.,11.)
    elif ending=='open':
        bars=append_bar(bars,12.,10.)
    rec=run_replay(h2_spec,bars,bars.date.iloc[124],bars.date.iloc[-1],costs=ZERO)[0]
    rec['dataset_id']='sample';rec['study_end']=bars.date.iloc[-1]
    return bars,rec


@pytest.mark.parametrize('ending,group',[('rescue','宽止损救回'),('loss','两者止损'),('mm','两者到 MM'),('open','仍持有／其他退出')])
def test_paired_outcomes_keep_fill_and_mm_and_common_r(h2_bars,h2_spec,ending,group):
    bars,rec=example(h2_bars,h2_spec,ending)
    before=deepcopy(rec);pair=compare_trade(bars,rec,AnchorSettings(),ZERO)
    assert pair['comparable'] and pair['group']==group
    assert rec==before
    assert pair['candidate']['entry']==rec['entry']
    assert pair['candidate']['target']==rec['target']
    if ending=='rescue':
        wide=pair['candidate']
        assert wide['common_r']>wide['r_multiple']
        assert pair['delta_common_r']>0
    elif ending=='loss':assert pair['delta_common_r']<0
    elif ending=='mm':assert pair['delta_common_r']==pytest.approx(0)
    else:
        assert 'delta_common_r' not in pair
        assert summarize([pair])['paired_closed']==0


def test_anchor_is_causal_not_a_future_pivot_and_exclusions_are_visible(h2_bars,h2_spec):
    bars,rec=example(h2_bars,h2_spec)
    a=candidate_anchor(bars,rec['structure'],rec['code'])
    future=bars.copy();future.loc[future.date>rec['structure']['bo_date'],'low']=.01
    assert candidate_anchor(future,rec['structure'],rec['code'])==a
    assert a['date']==bars.date.iloc[123] and a['stop']==4.99
    limited=candidate_anchor(bars,rec['structure'],rec['code'],AnchorSettings(max_bars=2))
    assert not limited['valid']
    excluded=compare_trade(bars,dict(rec,filled=False),AnchorSettings(),ZERO)
    assert excluded['group']=='无法对照' and summarize([excluded])['excluded']==1
    changed=dict(rec,exit_date='1900-01-01')
    assert not compare_trade(bars,changed,AnchorSettings(),ZERO)['comparable']


def test_ticks_t1_dual_touch_and_holding_period_match_original(h2_bars,h2_spec):
    bars,rec=example(h2_bars,h2_spec,'mm')
    fi=int(bars.index[bars.date==rec['entry_date']][0])
    # Both prices hit on the next day; conservative order is stop.
    dual=bars.copy();dual.loc[fi+1,'low']=4.8
    assert '止损' in replay_fixed_stop(dual,rec,4.99,ZERO)['reason']
    t1=dual.copy();t1.loc[fi,'low']=4.8
    assert replay_fixed_stop(t1,rec,4.99,ZERO)['reason']=='T+1 延迟止损'
    holding=bars.copy();holding.loc[fi+1,['high','low','open','close']]=[12,11,11.2,11.5]
    assert replay_fixed_stop(holding,rec,4.99,replace(ZERO,holding_bars=1))['reason']=='持有期限退出'
    stock=candidate_anchor(bars,rec['structure'],rec['code'])
    etf=bars.copy();etf.attrs['instrument_type']='etf'
    fund=candidate_anchor(etf,rec['structure'],'sh.510300')
    assert stock['low']-stock['stop']==pytest.approx(.01)
    assert fund['low']-fund['stop']==pytest.approx(.001)


def test_shallow_pause_is_an_explicit_variant_not_body_color(h2_bars,h2_spec):
    bars,rec=example(h2_bars,h2_spec)
    bars.loc[125,'low']=float(bars.loc[124,'low'])-.02
    strict=candidate_anchor(bars,rec['structure'],rec['code'],AnchorSettings(pause_ticks=0))
    tolerant=candidate_anchor(bars,rec['structure'],rec['code'],AnchorSettings(pause_ticks=2))
    assert strict['date']!=tolerant['date']
    with pytest.raises(ValueError):AnchorSettings(max_bars=True).validate()


def test_service_saved_history_chart_cutoff_and_no_formal_state(tmp_path,h2_bars,h2_spec):
    frozen=verify_frozen();bars,_=example(h2_bars,h2_spec)
    store=Store(tmp_path/'isolated')
    schema=store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']
    did=save_dataset(store,'sh.600000',bars,'csv','合成工程测试，非真实验收')
    service=Service(store)
    try:
        source=service.submit('backtest',dict(execution_model=BASE_MODEL,strategy='STRATEGY_GAP_H2',timeframe='daily',datasets=[did],start=bars.date.iloc[124],end=bars.date.iloc[-1],assumptions=dict(commission_bps=0,sell_tax_bps=0,slippage_bps=0)))
        assert wait_for(store,source)['status']=='completed'
        baseline=load_receipt(store,source)[2]
        job=service.submit('backtest',dict(execution_model=MODEL,source_job=source))
        assert wait_for(store,job)['status']=='completed'
        _,report,pairs=load_experiment(store,job)
        assert report['groups']['宽止损救回']==1 and not report['real_data']
        assert load_receipt(store,source)[2]==baseline
        image=render_comparison(store,pairs[0],pairs[0]['setup_date'],size=(900,550))
        assert image.size==(900,550)
        assert max(c['date'] for c in image.info['replay_view']['candles'])==pairs[0]['setup_date']
        with pytest.raises(ValueError):render_comparison(store,pairs[0],'1900-01-01')
        full=render_comparison(store,pairs[0],pairs[0]['setup_date'],True,size=(900,550))
        assert full.info['replay_view']['total']>image.info['replay_view']['total']
        assert len(store.backtest_history(MODEL))==1 and len(store.backtest_history(BASE_MODEL))==1
        store.rename_backtest(job,'止损实验');store.set_backtest_deleted(job)
        assert not store.backtest_history(MODEL)
        store.set_backtest_deleted(job,False)
        assert store.backtest_history(MODEL)[0]['display']['name']=='止损实验'
        for table in ('observations','plans','plan_history','watchlist','accounts','executions','positions','position_fills','closed_trades'):
            assert store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n']==0
        assert store.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value']==schema
        assert verify_frozen()==frozen
    finally:service.pool.shutdown()


@pytest.mark.parametrize('posthoc', [False, True])
def test_candidate_only_exit_day_draws_both_paths_without_future_events(tmp_path, h2_bars, h2_spec, monkeypatch, posthoc):
    from matplotlib.axes import Axes
    bars, original = example(h2_bars, h2_spec, 'rescue')
    store = Store(tmp_path/'chart')
    original['dataset_id'] = save_dataset(store, original['code'], bars, 'csv', '合成工程测试')
    pair = compare_trade(bars, original, AnchorSettings(), ZERO)
    before = deepcopy(pair)
    baseline_day, candidate_day = original['exit_date'], pair['candidate']['exit_date']
    assert baseline_day < candidate_day
    marks = []
    annotate = Axes.annotate
    def capture(self, text, *args, **kwargs):
        if text.startswith(('SL1 ·', 'C ·')):
            marks.append((text, kwargs['xy'], kwargs['xytext'], kwargs['arrowprops']))
        return annotate(self, text, *args, **kwargs)
    monkeypatch.setattr(Axes, 'annotate', capture)
    image = render_comparison(store, pair, candidate_day, posthoc, size=(1200, 700))
    candles = image.info['replay_view']['candles']
    baseline_exit = next(m for m in marks if m[0].startswith('SL1 ·') and '止损' in m[0])
    candidate_exit = next(m for m in marks if m[0] == 'C · MM 退出')
    assert candles[baseline_exit[1][0]]['date'] == baseline_day
    assert candles[candidate_exit[1][0]]['date'] == candidate_day
    assert baseline_exit[1][1] == original['exit']
    assert candidate_exit[1][1] == pair['candidate']['exit']
    assert candidate_exit[3]['linestyle'] == '--'
    assert baseline_exit[3]['linestyle'] == '-'
    marks.clear()
    early = render_comparison(store, pair, baseline_day, posthoc, size=(1200, 700))
    assert any(m[0] == 'C · 持仓' for m in marks)
    assert not any(m[0] == 'C · MM 退出' for m in marks)
    if not posthoc:
        assert max(c['date'] for c in early.info['replay_view']['candles']) == baseline_day
    # On the shared fill day, identical prices must have separate label positions.
    marks.clear()
    render_comparison(store, pair, original['entry_date'], posthoc, size=(1200, 700))
    fills = [m for m in marks if m[0].endswith('模拟成交')]
    assert len(fills) == 2 and fills[0][2] != fills[1][2]
    assert pair == before
