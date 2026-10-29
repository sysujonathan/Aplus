"""MTR presentation contracts: immutable anchors, selected opportunity, no fill inference."""
from copy import deepcopy
import json
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
import pandas as pd
import pytest

from gui.mtr_chart import mtr_evidence, draw_mtr, ENTRY, SL, TP
from gui.chart_evidence import strategy_evidence
from gui.chart_renderer import render_chart


def case():
    df = pd.DataFrame(dict(date=pd.bdate_range('2026-01-05', periods=80).strftime('%Y-%m-%d'),
        open=10., high=12., low=8., close=11., volume=1000., signal_mtr=False))
    df.loc[[15, 55, 70], 'signal_mtr'] = True
    for name, i, price in [('H0',0,12.),('L1',30,8.),('H1',40,12.),('TL',50,8.)]:
        df.loc[55, f'mtr_{name}_idx'] = i
        df.loc[55, f'mtr_{name}_price'] = price
    df.loc[55,['entry_price','sl_price','tp1_price']]=[12.,7.99,20.02]
    return df, dict(setup_date=df.date.iloc[55],asof=df.date.iloc[60],entry=12.1,stop=7.9,target=20.5)


def test_selected_signal_uses_original_fields_even_after_later_signal_and_viewport_slice():
    frame,payload=case();before=frame.copy(deep=True);archive=deepcopy(payload)
    # Adversarial extrema must never become H0/L1 or H1.
    frame.loc[10,['high','low']]=[99.,1.]
    output=strategy_evidence(dict(frame=frame,payload=payload,strategy_type='MTR_MASTER'))
    labels={i['text']:i['a'] for i in output['items'] if i['kind']=='label'}
    assert set(labels)=={'H0','L1','H1','TL','S'}
    assert labels['H0']==dict(date=frame.date.iloc[0],value=12.)
    assert labels['L1']==dict(date=frame.date.iloc[30],value=8.)
    assert labels['S']['date']==frame.date.iloc[55]
    assert output['availableDate']==frame.date.iloc[60]
    assert [(i['text'],i['a']['value'],i['color']) for i in output['items'] if i['kind']=='level']==[
        ('Entry',12.1,ENTRY),('SL',7.9,SL),('TP1',20.5,TP)]
    history=[i for i in output['items'] if i.get('text')=='历史 MTR 信号']
    assert len(history)==1 and history[0]['a']['date']==frame.date.iloc[15] and history[0]['alpha']==.45
    sliced=mtr_evidence(frame.iloc[25:],payload)
    assert next(i for i in sliced['items'] if i.get('text')=='H1')['a']==labels['H1']
    assert not any(i.get('text')=='H0' for i in sliced['items'])
    frame.loc[10,['high','low']]=before.loc[10,['high','low']]
    pd.testing.assert_frame_equal(frame,before);assert payload==archive
    json.dumps(output,allow_nan=False)


@pytest.mark.parametrize('bad', [float('nan'),999,-1,5.5,70])
def test_missing_out_of_range_fractional_or_future_nodes_are_not_fabricated(bad):
    df,payload=case();df.loc[55,'mtr_TL_idx']=bad
    output=mtr_evidence(df,payload)
    assert not any(i.get('text')=='TL' for i in output['items'])
    assert any('TL' in fact and '待核对' in fact for fact in output['facts'])
    assert not any(i.get('text') in {'H2','Trig','Fill'} for i in output['items'])


def test_missing_selected_signal_keeps_archive_prices_but_does_not_choose_another_signal():
    df,payload=case();payload['setup_date']=df.date.iloc[54]
    output=mtr_evidence(df,payload)
    assert not any(i['kind']=='label' for i in output['items'])
    assert {i['text'] for i in output['items'] if i['kind']=='level'}=={'Entry参考','SL参考','TP1参考'}
    assert any('原信号未确认' in s for s in output['facts'])
    # Explicitly missing archive prices cannot be substituted with today's output.
    payload['entry']=None
    assert not any(i.get('text','').startswith('Entry') for i in mtr_evidence(df,payload)['items'])


def test_equal_levels_share_geometry_and_price_zoom_does_not_pull_invisible_nodes_to_edges():
    df,payload=case();payload.update(entry=12.,stop=12.,target=12.)
    evidence=mtr_evidence(df,payload)
    levels=[i for i in evidence['items'] if i['kind']=='level']
    assert len(levels)==1 and levels[0]['text']=='Entry/SL/TP1' and levels[0]['color']==SL
    fig=Figure();ax=fig.subplots();ax.set_ylim(9,11);ax.set_xlim(0,90)
    assert draw_mtr(ax,df,evidence)==[]
    assert any('在窗口外' in t.get_text() for t in ax.texts)


@pytest.mark.parametrize('width,height',[(640,480),(960,640),(1920,1080)])
def test_static_layout_remeasures_bounds_and_keeps_data_anchors_on_resize_and_export(width,height):
    df,payload=case();evidence=mtr_evidence(df,payload);original=deepcopy(evidence)
    fig=Figure(figsize=(width/100,height/100));ax=fig.subplots()
    visible=df.iloc[25:61];ax.set_xlim(-.5,len(visible)+10);ax.set_ylim(7,22)
    labels=draw_mtr(ax,visible,evidence)
    for dpi in (100,150,200):
        fig.set_dpi(dpi);fig.canvas.draw()
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        canvas=FigureCanvasAgg(fig);canvas.draw();renderer=canvas.get_renderer();bounds=ax.get_window_extent(renderer)
        boxes=[]
        for label in labels:
            box=label.get_bbox_patch().get_window_extent(renderer)
            assert bounds.contains(box.x0,box.y0) and bounds.contains(box.x1,box.y1)
            assert not any(box.overlaps(other) for other in boxes)
            boxes.append(box)
        assert evidence==original


def test_static_and_interactive_routes_consume_the_same_mtr_model_without_legacy_annotation():
    df,payload=case()
    class Frozen:
        def annotate_chart(self,*args,**kwargs):raise AssertionError('Legacy approximation must not run')
        def get_signal_info(self,*args):return {}
    expected=mtr_evidence(df,payload)
    with patch('gui.mtr_chart.draw_mtr',wraps=draw_mtr) as draw:
        image=render_chart(df,payload,'',{'signal_column':'signal_mtr'},strategy=Frozen(),
            strategy_type='MTR_MASTER',size=(960,640))
    assert draw.call_args.args[2]==expected
    assert abs(image.width-960)<=1 and abs(image.height-640)<=1  # Matplotlib float-to-pixel rounding.
