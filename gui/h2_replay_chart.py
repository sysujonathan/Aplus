"""Historical event charts. No future bars or events unless explicitly requested."""
from workbench.market import load_dataset
from .chart_renderer import render_chart


def replay_view(frame, record, event_index, posthoc=False):
    events = record['events'][:event_index + 1]
    selected = events[-1]
    cutoff = selected['date']
    visible = frame.copy() if posthoc else frame[frame.date <= cutoff].copy()
    plans = [event['plan'] for event in events if 'plan' in event]
    payload = dict(plans[-1] if plans else record['structure'])
    fills = [event for event in events if event['kind'] == 'fill']
    if fills:
        fill = fills[0]
        payload.update(pending_state='PENDING', entry=fill['price'], stop=fill['stop'],
                       entry_plan_price=fill['price'], sl1_plan_price=fill['stop'],
                       target=fill['target'], replay_caption='历史成交计划（非当前待挂价）')
        risk = fill['price'] - fill['stop']
        payload.update(initial_risk_pct=risk / fill['price'] * 100,
                       mm_r_multiple=(fill['target'] - fill['price']) / risk)
    else:
        payload['replay_caption'] = '当时的次日计划' if payload.get('pending_state') == 'PENDING' else '当时机会已结束'
    marks = []
    for event in events:
        event = dict(event)
        if event['kind'] == 'trigger':
            event['price'] = event['plan']['entry']
        marks.append(event)
    return visible, payload, marks


def render_replay(store, record, event_index, posthoc=False, *, pixel_size=None):
    frame, _ = load_dataset(store, record['dataset_id'])
    # A replay can never expose snapshot bars after the requested study end.
    if record.get('study_end'):
        frame = frame[frame.date <= record['study_end']]
    frame, payload, events = replay_view(frame, record, event_index, posthoc)
    anchors = [record['structure'].get(key) for key in
               ('bo_date', 'h1_date', 'h2_setup_date', 'gap_floor_date', 'mm_low_date')]
    earliest = min(day for day in anchors if day)
    span = int((frame.date >= earliest).sum()) + 10
    caption = '完整走势（事后查看）' if posthoc else '仅显示当时已完成行情'
    return render_chart(frame, payload, f'{record["code"]} · {events[-1]["date"]} · {caption}',
                        {'signal_column': 'signal_gap_h2'}, replay_events=events,
                        view_bars=max(120, span), pixel_size=pixel_size)
