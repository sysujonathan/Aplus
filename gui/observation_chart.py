"""Read-only candidate preparation. No Tk objects are accessed by the worker."""
import threading

_preview_lock = threading.Lock()


def load_shape_chart(store, item, options, *, check=lambda: None):
    from .background_status import display_store
    from workbench.market import load_dataset

    check()
    view = display_store(store)
    shape = item.shape
    frame, record = load_dataset(view, shape['dataset_id'])
    if record['code'] != item.code or record['source'] != item.source or record['timeframe'] != 'daily':
        raise ValueError('形态图格与行情身份不一致')
    if shape.get('sha256') and record['sha256'] != shape['sha256']:
        raise ValueError('形态候选快照身份不一致')
    frame = frame[frame.date <= shape['end']].copy()
    if frame.empty:
        raise ValueError('匹配截止日之前没有行情')
    context = dict(frame=frame, record=record, code=item.code, name=item.name,
                   timeframe='daily', payload={}, metadata={}, strategy_type='', context_kind='shape',
                   shape=dict(label=shape.get('label'), score=shape.get('score'),
                              start=shape.get('start'), end=shape['end']))
    check()
    image = boot = None
    if options['interactive']:
        from .chart_bridge import workspace_boot
        boot = workspace_boot(view, {**context, 'embedded': True,
                                    **({'workspace': options['workspace']} if options.get('workspace') else {})})
    else:
        from .chart_renderer import render_chart
        bars = frame.tail(300).copy()
        bars['ema20'] = frame.close.ewm(span=20, adjust=False).mean().tail(300)
        with _preview_lock:
            check()
            image = render_chart(bars, {}, '', {}, code=item.code)
    check()
    title = (f"{item.code} {item.name} · {shape.get('label','形态模板')}"
             f" · 相似 {shape.get('score',100):.1f} · 截至 {shape['end']} · 研究")
    return dict(context=context, image=image, title=title, boot=boot)


def load_observation_chart(store, request, options, *, check=lambda: None):
    from .background_status import display_store
    from .chart_panel import _observation_calculation, render_chart
    from .data import code_names
    from workbench.h2_plan import display_plan, is_h2, STATE_LABELS

    check()
    view = display_store(store)
    check()
    observation = view.rows('SELECT * FROM observations WHERE id=?', (request['observation_id'],))[0]
    observation = {**observation, 'id': request['observation_id']}
    mode = request['mode']
    record, payload, bars, instance, calculated, spec, _ = _observation_calculation(
        view, observation, request['market_dataset_id'] if mode == 'watch' else None,
        request['market_asof'])
    check()
    payload = dict(payload or {})
    for key in ('asof', 'setup_date'):
        if observation.get(key):
            payload.setdefault(key, observation[key])
    code, timeframe = observation['code'], observation['timeframe']
    h2 = spec.id == 'STRATEGY_GAP_H2' and is_h2(instance.get_metadata())
    if h2:
        payload = display_plan(spec, bars, payload, code,
                               observation.get('setup_date') or observation['asof'], mode)
    check()
    name = code_names(view).get(code, '')
    period = '周K' if timeframe == 'weekly' else '日K'
    chart_identity = f'{code}  {name}'.rstrip()
    title = f'{chart_identity}  ·  {spec.name} · {period}'
    if mode == 'watch':
        current_day = str(bars.date.iloc[-1])[:10]
        signal_day = str(request['anchor_asof'] or observation['asof'])[:10]
        if h2:
            signal_day = payload.get('h2_setup_date', signal_day)
            title += f' · 行情 {current_day} · 机会起始 {signal_day}'
            title += (f" · 明日计划基于{payload['plan_asof']}收盘"
                      if payload.get('pending_state') == 'PENDING'
                      else f" · {STATE_LABELS[payload['pending_state']]}")
        else:
            title += f' · 行情 {current_day} · 信号 {signal_day} · 计划线来自信号日'
    context = dict(frame=calculated, record={**(record or {}), 'timeframe': timeframe},
                   code=code, name=name, timeframe=timeframe, payload=payload,
                   metadata=instance.get_metadata(), strategy=instance,
                   strategy_type=observation['strategy'], instrument_type=bars.attrs.get('instrument_type'),
                   title='', context_kind=mode)
    check()
    image = boot = None
    if options['interactive']:
        from .chart_bridge import workspace_boot
        embedded = {**context, 'embedded': True}
        if options.get('workspace'):
            embedded['workspace'] = options['workspace']
        boot = workspace_boot(view, embedded)
    else:
        # Matplotlib's shared state must not race between fallback preview workers.
        with _preview_lock:
            check()
            image = render_chart(calculated, payload, '', instance.get_metadata(),
                                 strategy=instance, strategy_type=observation['strategy'], code=code,
                                 instrument_type=bars.attrs.get('instrument_type'))
    check()
    return dict(context=context, image=image, title=title, boot=boot)
