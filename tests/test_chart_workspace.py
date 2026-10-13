from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from gui.chart_renderer import render_chart
from gui.chart_workspace import resample_daily
from workbench.chart_workspace import (
    History, aggregate_minutes, create_alert, document, evaluate_alert, identity,
    make_drawing, minute_frame, session_counts,
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
