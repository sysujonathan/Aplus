"""Shared candle renderer with a research-only candidate stop overlay."""
from workbench.market import load_dataset
from .h2_replay_chart import replay_view
from .chart_renderer import render_chart


def render_comparison(store, row, cutoff, posthoc=False, *, size=None, viewport=(None,0,1.0)):
    if cutoff < row['setup_date']:
        raise ValueError('不能在 H2 成立之前展示此研究计划')
    frame, _ = load_dataset(store,row['dataset_id'])
    if row.get('study_end'):
        frame=frame[frame.date<=row['study_end']].copy()
    original=row['baseline']
    indices=[i for i,e in enumerate(original['events']) if e['date']<=cutoff]
    index=indices[-1] if indices else 0
    # Payload uses only original events visible at the selected close.
    _,payload,events=replay_view(frame,original,index,False)
    visible=frame.copy() if posthoc else frame[frame.date<=cutoff].copy()
    overlay=[]
    anchor=row.get('anchor',{})
    if anchor.get('valid') and cutoff>=row['setup_date']:
        overlay=[dict(date=anchor['date'],price=anchor['stop'],anchor_price=anchor['low'],
                      label='研究 C 止损',color='#C2ADFF')]
    bars,offset,scale=viewport
    title=f"{row['code']} · {cutoff} · " + ('完整走势（事后）' if posthoc else '当时可见行情')
    return render_chart(visible,payload,title,{'signal_column':'signal_gap_h2'},
                        replay_events=events,research_overlay=overlay,size=size,
                        view_bars=bars or 90,view_offset=offset,price_scale=scale,code=row['code'])
