from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from gui.chart_renderer import render_chart
from gui.chart_workspace import resample_daily
from gui.chart_bridge import ChartBridge, workspace_boot
from gui.chart_bridge import drawing_to_frontend
from gui.chart_shell import ChartCloseGuard
from workbench.chart_workspace import (
    History, aggregate_minutes, bar_completed, create_alert, document, evaluate_alert, identity,
    make_drawing, minute_frame, risk_readout, session_counts,
)
from workbench.store import Store


def bars(dates=None, closes=None):
    if dates is None:
        periods = len(closes) if closes is not None else 20
        dates = pd.bdate_range('2026-01-05', periods=periods).strftime('%Y-%m-%d').tolist()
    closes = closes or [10+i*.1 for i in range(len(dates))]
    return pd.DataFrame({'date':dates, 'open':closes, 'high':[x+.2 for x in closes],
                         'low':[x-.2 for x in closes], 'close':closes,
                         'volume':[1000]*len(dates)})


def test_drawing_undo_never_rewinds_alert_cursor():
    frame = bars(closes=[9, 9.5])
    value = document()
    value['alerts'] = [create_alert(name='测试', code='sz.000001', source='s', adjustment='qfq',
                                    timeframe='daily', target='price', condition='cross_up',
                                    value=10, frame=frame)]
    history = History(value)
    changed = deepcopy(history.value)
    changed['drawings'].append(make_drawing('trend', [['2026-01-05',9],['2026-01-06',10]],
        created_at='2026-01-06', settings=changed['settings']))
    assert history.commit(changed)
    history.value['alerts'][0]['cursor'] = '2026-01-09'
    assert history.undo()
    assert history.value['drawings'] == []
    assert history.value['alerts'][0]['cursor'] == '2026-01-09'


def test_closed_bar_alert_is_forward_only_deduplicated_and_replay_safe():
    frame = bars(dates=['2026-01-05','2026-01-06'], closes=[9,9.5])
    alert = create_alert(name='上穿10', code='sz.000001', source='s', adjustment='qfq',
                         timeframe='daily', target='price', condition='cross_up', value=10,
                         frame=frame, frequency='once')
    extended = pd.concat([frame, bars(dates=['2026-01-07'], closes=[10.2])], ignore_index=True)
    updated, event = evaluate_alert(alert, extended, now='2026-01-07T20:00:00+08:00')
    assert event['bar'] == '2026-01-07' and not updated['enabled']
    again, duplicate = evaluate_alert(updated, extended, now='2026-01-07T21:00:00+08:00')
    assert duplicate is None and len(again['events']) == 1
    replay_alert = deepcopy(alert); replay_alert['cursor'] = '2026-01-06'
    replayed, event = evaluate_alert(replay_alert, extended, now='2026-01-07T20:00:00+08:00', replay=True)
    assert event is None and replayed['cursor'] == '2026-01-06'


def test_intraday_alert_waits_for_close_and_checks_latest_completed_prefix():
    frame = bars(dates=['2026-10-12 14:45:00', '2026-10-12 14:50:00'], closes=[9, 9.5])
    alert = create_alert(name='上穿10', code='sz.000001', source='s', adjustment='qfq',
                         timeframe='5m', target='price', condition='cross_up', value=10,
                         frame=frame)
    alert['cursor'] = '2026-10-12 14:45:00'
    extended = pd.concat([frame, bars(dates=['2026-10-12 14:55:00'], closes=[10.2])],
                         ignore_index=True)
    waiting, event = evaluate_alert(alert, extended, now='2026-10-12T14:59:59+08:00')
    assert event is None and waiting['cursor'] == '2026-10-12 14:50:00'
    assert bar_completed('2026-10-12 14:55:00', '5m', '2026-10-12T14:59:59+08:00') is False
    triggered, event = evaluate_alert(waiting, extended, now='2026-10-12T15:00:00+08:00')
    assert event['bar'] == '2026-10-12 14:55:00'
    assert triggered['cursor'] == '2026-10-12 14:55:00'
    assert bar_completed('2026-10-12', 'daily', '2026-10-12T18:59:59+08:00') is False
    assert bar_completed('2026-10-12', 'daily', '2026-10-12T19:00:00+08:00') is True


def test_real_five_minute_import_counts_sessions_and_never_bridges_lunch():
    times = [*pd.date_range('2026-10-12 09:30',periods=24,freq='5min'),
             *pd.date_range('2026-10-12 13:00',periods=24,freq='5min')]
    source = pd.DataFrame({'时间':times,'开盘':10.,'最高':10.2,'最低':9.9,
                           '收盘':10.1,'成交量':100})
    frame = minute_frame(BytesIO(source.to_csv(index=False).encode('utf-8')), 5)
    assert session_counts(frame) == list(range(1,49))
    hourly = aggregate_minutes(frame, 60)
    assert hourly.date.tolist() == ['2026-10-12 10:25:00','2026-10-12 11:25:00',
                                    '2026-10-12 13:55:00','2026-10-12 14:55:00']
    four_hour = aggregate_minutes(frame, 240)
    assert len(four_hour) == 1 and four_hour.iloc[0].volume == 4800


def test_minute_import_rejects_daily_data_and_duplicate_bars():
    value = bars(dates=['2026-01-05','2026-01-05'])
    with pytest.raises(ValueError, match='唯一'):
        minute_frame(BytesIO(value.to_csv(index=False).encode()), 5)


@pytest.mark.parametrize('dates, message', [
    (['2026-10-12 10:00:00', '2026-10-12 10:20:00'], '缺失或跳跃'),
    (['2026-10-11 09:30:00', '2026-10-11 09:35:00'], '不是交易日'),
    (['2026-10-01 09:30:00', '2026-10-01 09:35:00'], '不是交易日'),
    (['1900-01-01 09:30:00', '1900-01-01 09:35:00'], '有效历史范围'),
    (['09:30', '09:35'], '明确交易日期'),
])
def test_minute_import_rejects_session_gaps_and_invalid_dates(dates, message):
    source = pd.DataFrame({'date': dates, 'open': 10, 'high': 10.2, 'low': 9.9,
                           'close': 10.1, 'volume': 100})
    with pytest.raises(ValueError, match=message):
        minute_frame(BytesIO(source.to_csv(index=False).encode()), 5)


def test_risk_readout_is_explicitly_analysis_only():
    obj = make_drawing('long', [['2026-10-12', 10]], created_at='2026-10-12',
                       settings={'fib_levels': [0, .5, 1], 'risk_budget': 1000})
    text, _, quantity = risk_readout(obj)
    assert quantity == 2000
    assert '分析模拟数量 2000 股' in text and '非交易建议' in text


def test_chart_document_is_optimistic_and_does_not_write_trade_tables(tmp_path):
    store = Store(tmp_path)
    key = identity('sz.000001','test','qfq')
    value = store.chart_document(key)
    assert store.save_chart_document(key, value, 0) == 1
    with pytest.raises(ValueError, match='另一图表窗口'):
        store.save_chart_document(key, value, 0)
    assert store.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == 0
    assert store.rows('SELECT COUNT(*) AS n FROM plans')[0]['n'] == 0


def test_imported_series_is_immutable_verified_runtime_artifact(tmp_path):
    store = Store(tmp_path)
    key = identity('sz.000001','test','qfq')
    frame = minute_frame(BytesIO(('date,open,high,low,close,volume\n'
        '2026-10-12 09:30:00,10,11,9,10.5,100\n'
        '2026-10-12 09:35:00,10.5,11,10,10.8,120\n').encode()),5)
    record = store.save_chart_series(key,'5m',frame)
    loaded, loaded_record = store.load_chart_series(key,'5m')
    assert loaded.date.tolist() == frame.date.tolist() and loaded_record['sha256'] == record['sha256']
    (tmp_path/record['path']).write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='校验失败'):
        store.load_chart_series(key,'5m')


def test_custom_chart_style_can_hide_volume_and_reports_ema_coordinates():
    frame = bars()
    image = render_chart(frame, {}, '', {}, size=(900,500), code='sz.000001',
        display_options={'ema':True,'ema_length':5,'ema_source':'close','ema_offset':0,
                         'ema_color':'#f0a202','volume':False,'up':'#ffffff','down':'#2962ff'})
    info = image.info['replay_view']
    assert len(info['ema_values']) == min(120,len(frame))
    assert info['price_bounds'][0] < min(frame.low) and info['price_bounds'][1] > max(frame.high)


def test_weekly_and_monthly_views_are_derived_only_from_real_daily_bars():
    frame = bars(pd.bdate_range('2026-01-05',periods=30).strftime('%Y-%m-%d').tolist())
    weekly = resample_daily(frame,'weekly'); monthly = resample_daily(frame,'monthly')
    assert weekly.volume.sum() == frame.volume.sum() == monthly.volume.sum()
    assert weekly.iloc[0].open == frame.iloc[0].open


def test_production_web_workspace_uses_real_context_and_persists_objects(tmp_path):
    store = Store(tmp_path)
    frame = bars(pd.bdate_range('2026-01-05', periods=40).strftime('%Y-%m-%d').tolist())
    boot = workspace_boot(store, dict(
        frame=frame, record={'source': 'baostock', 'adjustment': '前复权'},
        code='sz.000001', name='平安银行', timeframe='daily', context_kind='watch',
        strategy_type='STRATEGY_GAP_H2',
        payload={'gap_floor': 10.2, 'gap_top': 10.5, 'bo_date': frame.date.iloc[20],
                 'h2_setup_date': frame.date.iloc[30], 'entry_plan_price': 14.2,
                 'sl1_plan_price': 12.8, 'mm_target': 16.4},
    ))
    assert boot['production'] and boot['contextTitle'] == '自选标的'
    assert boot['bars'][-1]['date'] == frame.date.iloc[-1]
    assert set(boot['series']) == {'daily', 'weekly', 'monthly'}
    assert sum(row['volume'] for row in boot['series']['weekly']) == sum(frame.volume)
    assert boot['drawings'] == []

    bridge = ChartBridge(store, boot)
    state = {'objects': [{
        'id': 'trend-web-1', 'type': 'trend',
        'a': {'index': 5, 'value': 10.1}, 'b': {'index': 15, 'value': 12.3},
        'color': '#2962ff', 'width': 2, 'visible': True, 'locked': False,
        'createdAtIndex': 15,
    }], 'toolbar': {'left': '410px', 'top': '58px'},
        'view': {'count': 32, 'offset': 4, 'priceZoom': 1.2}, 'timeframe': 'daily'}
    saved = bridge.save_state(state, 0)
    assert saved['revision'] == 1
    reopened = workspace_boot(store, dict(
        frame=frame, record={'source': 'baostock', 'adjustment': '前复权'},
        code='sz.000001', name='平安银行', timeframe='daily', context_kind='watch'))
    assert reopened['drawings'][0]['a'] == {'index': 5, 'value': 10.1, 'date': frame.date.iloc[5]}
    assert reopened['state']['toolbar']['left'] == '410px'
    assert reopened['state']['view']['count'] == 32
    assert store.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == 0
    assert store.rows('SELECT COUNT(*) AS n FROM positions')[0]['n'] == 0


def test_web_workspace_alert_is_forward_only_store_alert(tmp_path):
    store = Store(tmp_path)
    frame = bars()
    from workbench.market import save_dataset
    save_dataset(store, 'sz.000001', frame, 'baostock', '前复权')
    boot = workspace_boot(store, dict(
        frame=frame, record={'source': 'baostock', 'adjustment': '前复权'},
        code='sz.000001', name='平安银行', timeframe='daily'))
    result = ChartBridge(store, boot).create_price_alert(12.5, name='收盘突破')
    assert result['alert']['cursor'] == frame.date.iloc[-1]
    saved = store.chart_document(boot['identity'])
    assert saved['alerts'][0]['name'] == '收盘突破'
    assert saved['alerts'][0]['events'] == []


@pytest.mark.parametrize('timeframe', ['weekly', 'monthly'])
def test_aggregate_view_save_preserves_original_drawing_coordinates(tmp_path, timeframe):
    store = Store(tmp_path)
    frame = bars(pd.bdate_range('2026-01-05', periods=45).strftime('%Y-%m-%d').tolist())
    context = dict(frame=frame, record={'source': 's'}, code='sz.000001', timeframe='daily')
    boot = workspace_boot(store, context)
    value = store.chart_document(boot['identity'])
    # Every supported point shape carries canonical coordinates, including path vertices.
    for kind, points in [
        ('trend', [['2026-01-06', 10.123456789], ['2026-01-12', 11.234567891]]),
        ('path', [['2026-01-06', 10.1], ['2026-01-07', 11.2], ['2026-01-08', 12.3]]),
        ('long', [['2026-01-06', 10.1]]),
        ('channel', [['2026-01-06', 10], ['2026-01-14', 12], ['2026-01-07', 11.35]]),
    ]:
        value['drawings'].append(make_drawing(kind, points, created_at='2026-01-14',
                                              settings=value['settings']))
    store.save_chart_document(boot['identity'], value, 0)
    original = store.chart_document(boot['identity'])['drawings']
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    dates = [pd.Timestamp(row['date']) for row in boot['series'][timeframe]]
    objects = [drawing_to_frontend(obj, dates, timeframe) for obj in original]
    if timeframe == 'weekly':
        assert objects[0]['b']['index'] == 1  # Monday belongs to this Friday, not last Friday.
    bridge.save_state({'timeframe': timeframe, 'objects': objects}, 1)
    aggregate_saved = store.chart_document(boot['identity'])['drawings']
    assert [(o['points'], o['created_at']) for o in aggregate_saved] == [
        (o['points'], o['created_at']) for o in original]
    daily_objects = workspace_boot(store, context)['drawings']
    bridge.save_state({'timeframe': 'daily', 'objects': daily_objects}, 2)
    assert [o['points'] for o in store.chart_document(boot['identity'])['drawings']] == [
        o['points'] for o in original]


@pytest.mark.parametrize('timeframe', ['daily', 'weekly', 'monthly'])
def test_web_alert_uses_selected_period_and_its_last_completed_bar(tmp_path, timeframe):
    store = Store(tmp_path)
    frame = bars(pd.bdate_range('2026-01-05', periods=20).strftime('%Y-%m-%d').tolist())
    from workbench.market import save_dataset
    save_dataset(store, 'sz.000001', frame, 's', 'unknown')
    boot = workspace_boot(store, dict(frame=frame, record={'source': 's'},
                                     code='sz.000001', timeframe='daily'))
    # Historical fixture: all three periods are closed at this explicit clock.
    result = ChartBridge(store, boot, clock=lambda: '2026-02-02T20:00:00+08:00').create_price_alert(
        12.5, timeframe=timeframe)
    saved = store.chart_document(boot['identity'])['alerts'][0]
    assert saved['timeframe'] == timeframe
    assert saved['cursor'] == boot['series'][timeframe][-1]['date']
    assert saved['events'] == result['alert']['events'] == []
    if timeframe == 'monthly':
        assert saved['cursor'] != frame.date.iloc[-1]


def test_web_alert_rejects_unavailable_period_without_writing(tmp_path):
    store = Store(tmp_path)
    boot = workspace_boot(store, dict(frame=bars(), record={'source': 's'},
                                     code='sz.000001', timeframe='daily'))
    with pytest.raises(ValueError, match='该周期的真实行情'):
        ChartBridge(store, boot).create_price_alert(12.5, timeframe='5m')
    assert store.chart_document(boot['identity'])['revision'] == 0


def test_chart_close_waits_for_frontend_ack_and_can_retry_failure(tmp_path):
    import threading

    class Window:
        def __init__(self):
            self.started = threading.Event()
            self.destroyed = False
            self.discard = False

        def evaluate_js(self, script):
            assert 'aplusFlushAndClose' in script
            self.started.set()
            return True

        def destroy(self):
            self.destroyed = True

        def create_confirmation_dialog(self, title, message):
            assert '放弃未保存' in message
            return self.discard

    store = Store(tmp_path)
    bridge = ChartBridge(store, workspace_boot(store, dict(
        frame=bars(), record={'source': 's'}, code='sz.000001', timeframe='daily')))
    window = Window()
    guard = ChartCloseGuard(window, bridge)
    assert guard.request() is False
    assert window.started.wait(2)
    assert guard.request() is False and not window.destroyed
    bridge.cancel_close()
    assert not guard.flushing
    assert guard.request() is False
    bridge.finish_close()
    assert window.destroyed and guard.request() is True
    # Discarding an unsaved edit requires an explicit native-dialog confirmation.
    other = Window()
    other.discard = True
    guard = ChartCloseGuard(other, bridge)
    bridge.cancel_close()
    assert other.destroyed and guard.request() is True


@pytest.mark.parametrize('kind,target,stop', [('long',14,8),('short',6,12)])
def test_native_position_analysis_matches_risk_lot_leverage_and_precision(kind,target,stop):
    from workbench.chart_workspace import document, risk_readout, validate_document
    value = document()
    obj = make_drawing(kind,[['2026-10-15',10]],created_at='2026-10-15',settings=value['settings'])
    obj['points'] = [['2026-10-15',10],['2026-10-15',stop],['2026-10-19',target],['2026-10-19',10]]
    obj['web'] = dict(account_size=5000,risk_value=10,risk_unit='percent',leverage=1,lot_size=1)
    text, ratio, quantity = risk_readout(obj)
    assert ratio == 2 and quantity == 250 and '非交易建议' in text
    obj['web']['leverage'] = .1
    assert risk_readout(obj)[2] == 50
    obj['web'].update(lot_size=3,qty_precision='2')
    assert risk_readout(obj)[2] == 16.66
    value['drawings'] = [obj]
    assert validate_document(value)['drawings'][0]['web']['lot_size'] == 3
    obj['web']['leverage'] = float('nan')
    with pytest.raises(ValueError,match='有限正数'):
        validate_document(value)
