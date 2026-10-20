"""Closed-period alert cursors, monitor/editor concurrency and native fallback."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from gui.chart_bridge import ChartBridge, workspace_boot
from workbench.chart_workspace import evaluate_alert
from workbench.store import Store


def frame(dates, closes=None):
    closes = closes or [9.5] * len(dates)
    return pd.DataFrame(dict(date=dates, open=closes, high=[p+.2 for p in closes],
                             low=[p-.2 for p in closes], close=closes, volume=100))


def boot_for(store, data):
    from workbench.market import save_dataset
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    return workspace_boot(store, dict(frame=data, record={'source': 'test', 'adjustment': 'qfq'},
                                     code='sz.000001', timeframe='daily'))


@pytest.mark.parametrize('timeframe,dates,now,expected,next_date,next_now', [
    ('weekly', ['2026-01-05', '2026-01-09', '2026-01-12', '2026-01-14'],
     '2026-01-14T20:00:00+08:00', '2026-01-09', '2026-01-16', '2026-01-16T20:00:00+08:00'),
    ('monthly', ['2025-12-01', '2025-12-31', '2026-01-05', '2026-01-14'],
     '2026-01-14T20:00:00+08:00', '2025-12-31', '2026-01-30', '2026-02-01T20:00:00+08:00'),
    ('daily', ['2026-01-12', '2026-01-13', '2026-01-14'],
     '2026-01-14T18:59:59+08:00', '2026-01-13', '2026-01-14', '2026-01-14T19:00:00+08:00'),
])
def test_first_period_that_closes_after_creation_can_trigger(
        tmp_path, monkeypatch, timeframe, dates, now, expected, next_date, next_now):
    store = Store(tmp_path)
    data = frame(dates)
    boot = boot_for(store, data)
    bridge = ChartBridge(store, boot, clock=lambda: now)
    alert = bridge.create_price_alert(10, timeframe=timeframe)['alert']
    assert alert['cursor'] == expected
    assert alert['cursor'] != boot['series'][timeframe][-1]['date']
    assert not alert['events']
    waiting, event = evaluate_alert(alert, pd.DataFrame(boot['series'][timeframe]), now=now)
    assert event is None and waiting['cursor'] == expected
    extended = pd.concat([data.loc[data.date.lt(next_date)], frame([next_date], [10.2])],
                         ignore_index=True)
    closed = pd.DataFrame(boot_for(store, extended)['series'][timeframe])
    triggered, event = evaluate_alert(alert, closed, now=next_now)
    assert event is not None and event['timeframe'] == timeframe
    assert event['bar'] == closed.date.iloc[-1]
    assert triggered['cursor'] == event['bar']
    again, duplicate = evaluate_alert(triggered, closed, now=next_now)
    assert duplicate is None and len(again['events']) == 1
    # The application's real monitor must see and trigger this same first close.
    from gui.chart_alerts import ChartAlertMonitor
    from workbench.market import save_dataset
    save_dataset(store, 'sz.000001', extended, 'test', 'qfq')
    monkeypatch.setattr('workbench.market.completed_date', lambda: next_now[:10])
    monitor = ChartAlertMonitor(Mock(), store, clock=lambda: next_now)
    monitored = monitor._frame(boot['identity'], alert)
    _, event = evaluate_alert(alert, monitored, now=next_now)
    assert event is not None and event['bar'] == closed.date.iloc[-1]


@pytest.mark.parametrize('moment,expected', [
    ('2026-01-16T18:59:59+08:00', '2026-01-09'),
    ('2026-01-16T19:00:00+08:00', '2026-01-16'),
])
def test_weekly_cursor_obeys_shanghai_close_boundary(tmp_path, moment, expected):
    store = Store(tmp_path)
    boot = boot_for(store, frame(['2026-01-09', '2026-01-16']))
    result = ChartBridge(store, boot, clock=lambda: moment).create_price_alert(10, timeframe='weekly')
    assert result['alert']['cursor'] == expected


def test_no_completed_period_rejects_alert_without_seeding_a_future_cursor(tmp_path):
    store = Store(tmp_path)
    boot = boot_for(store, frame(['2026-01-14']))
    bridge = ChartBridge(store, boot, clock=lambda: '2026-01-14T20:00:00+08:00')
    with pytest.raises(ValueError, match='尚无已完成'):
        bridge.create_price_alert(10, timeframe='monthly')
    assert store.chart_document(boot['identity'])['revision'] == 0


def setup_open_editor(tmp_path):
    store = Store(tmp_path)
    initial = frame(['2026-01-05', '2026-01-06'])
    boot = boot_for(store, initial)
    ChartBridge(store, boot, clock=lambda: '2026-01-06T20:00:00+08:00').create_price_alert(10)
    boot = boot_for(store, initial)
    bridge = ChartBridge(store, boot)
    state = dict(objects=[dict(id='t', type='trend', a=dict(index=0, value=9),
                               b=dict(index=1, value=10), createdAtIndex=1)], timeframe='daily')
    return store, boot, bridge, state


def test_real_monitor_keeps_polling_while_web_editor_saves_without_losing_alert_progress(tmp_path, monkeypatch):
    from gui.chart_alerts import ChartAlertMonitor
    import gui.chart_alerts as alerts_module

    store, boot, bridge, state = setup_open_editor(tmp_path)
    root = Mock(_active_chart_workspaces=set())
    root.winfo_exists.return_value = True
    monitor = ChartAlertMonitor(root, store)
    monkeypatch.setattr(monitor, '_frame', lambda *_: frame(
        ['2026-01-05', '2026-01-06', '2026-01-07'], [9.5, 9.5, 10.2]))
    monkeypatch.setattr(alerts_module, 'datetime', SimpleNamespace(now=lambda *_:
                        pd.Timestamp('2026-01-07T20:00:00+08:00')))
    warning = Mock()
    monkeypatch.setattr(alerts_module.messagebox, 'showwarning', warning)
    monitor.poll()
    monitored = store.chart_document(boot['identity'])
    assert monitored['revision'] == 2
    assert monitored['alerts'][0]['cursor'] == '2026-01-07'
    assert len(monitored['alerts'][0]['events']) == 1
    warning.assert_called_once()
    result = bridge.save_state(state, 1)
    saved = store.chart_document(boot['identity'])
    assert saved['revision'] == 3 and saved['drawings'][0]['id'] == 't'
    assert saved['alerts'] == monitored['alerts'] == result['alerts']
    assert monitor.last_error is None
    root.after.assert_called_once()


@pytest.mark.parametrize('external_edit', ['alert', 'drawing', 'setting'])
def test_atomic_save_race_merges_only_alert_changes(tmp_path, monkeypatch, external_edit):
    store, boot, bridge, state = setup_open_editor(tmp_path)
    real_save = store.save_chart_document
    raced = []

    def save_racing(key, value, revision):
        if not raced:
            other = store.chart_document(key)
            if external_edit == 'alert':
                other['alerts'][0].update(cursor='2026-01-07', status='已检查 2026-01-07')
            elif external_edit == 'drawing':
                other['drawings'] = [dict(id='other', kind='text',
                    points=[['2026-01-06', 9]], text='另一窗口', width=2)]
            else:
                other['settings']['ema_length'] = 50
            real_save(key, other, revision)
            raced.append(deepcopy(other))
        return real_save(key, value, revision)

    monkeypatch.setattr(store, 'save_chart_document', save_racing)
    if external_edit == 'alert':
        result = bridge.save_state(state, 1)
        saved = store.chart_document(boot['identity'])
        assert saved['revision'] == result['revision'] == 3
        assert saved['alerts'] == raced[0]['alerts']
        assert saved['drawings'][0]['id'] == 't'
    else:
        with pytest.raises(ValueError, match='另一图表窗口'):
            bridge.save_state(state, 1)
        saved = store.chart_document(boot['identity'])
        assert saved['revision'] == 2
        assert saved['drawings'] == raced[0]['drawings']
        assert saved['settings'] == raced[0]['settings']
        assert bridge.revision == 1


def test_new_alert_creation_cannot_bypass_another_editors_changes(tmp_path):
    store, boot, bridge, _ = setup_open_editor(tmp_path)
    other = store.chart_document(boot['identity'])
    other['settings']['ema_length'] = 60
    store.save_chart_document(boot['identity'], other, 1)
    with pytest.raises(ValueError, match='另一图表窗口'):
        bridge.create_price_alert(11)
    saved = store.chart_document(boot['identity'])
    assert len(saved['alerts']) == 1 and saved['settings']['ema_length'] == 60


def test_continuous_monitor_races_stop_without_overwriting_progress(tmp_path, monkeypatch):
    store, boot, bridge, state = setup_open_editor(tmp_path)
    real_save = store.save_chart_document
    attempts = []

    def always_racing(key, value, revision):
        attempts.append(revision)
        progress = store.chart_document(key)
        progress['alerts'][0]['status'] = '后台更新 ' + str(len(attempts))
        real_save(key, progress, revision)
        return real_save(key, value, revision)

    monkeypatch.setattr(store, 'save_chart_document', always_racing)
    with pytest.raises(ValueError, match='持续更新'):
        bridge.save_state(state, 1)
    assert len(attempts) == 3 and bridge.revision == 1
    saved = store.chart_document(boot['identity'])
    assert saved['drawings'] == [] and saved['alerts'][0]['status'] == '后台更新 3'


def test_native_fallback_reads_web_text_and_ruler_without_changing_saved_objects(tmp_path, monkeypatch):
    import tkinter as tk
    from PIL import Image
    from ttkbootstrap.publisher import Channel, Publisher
    import gui.chart_workspace as workspace
    from workbench.chart_workspace import POINT_COUNTS

    store = Store(tmp_path)
    data = frame(pd.bdate_range('2026-01-05', periods=15).strftime('%Y-%m-%d').tolist())
    context = dict(frame=data, record={'source': 'test', 'adjustment': 'qfq'},
                   code='sz.000001', timeframe='daily', context_kind='watch')
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot)
    bridge.save_state(dict(timeframe='daily', objects=[
        dict(id='note', type='text', a=dict(index=1, value=10), text='my note'),
        dict(id='range', type='ruler', a=dict(index=2, value=9), b=dict(index=5, value=11)),
        dict(id='legacy-path', type='path', a=dict(index=3,value=10),b=dict(index=8,value=11),
             points=[dict(index=3,value=10),dict(index=6,value=12),dict(index=8,value=11)]),
        dict(id='arrow-path', type='path', a=dict(index=4,value=10),b=dict(index=9,value=11),
             points=[dict(index=4,value=10),dict(index=7,value=12),dict(index=9,value=11)],
             path_version=1,startStyle='arrow',endStyle='arrow'),
        dict(id='legacy-arrow',type='arrow',a=dict(index=3,value=10),b=dict(index=8,value=12)),
        dict(id='marker-arrow',type='arrow',a=dict(index=4,value=10),b=dict(index=9,value=12),
             arrow_marker_version=1,fontSize=16),
    ]))
    before = store.chart_document(boot['identity'])
    assert set(POINT_COUNTS) <= set(workspace.DRAWING_LABELS)
    monkeypatch.delenv('APLUS_TK_CHART_FALLBACK', raising=False)
    monkeypatch.setattr('gui.chart_shell.launch_chart_workspace', Mock(side_effect=RuntimeError('WebView失败')))
    monkeypatch.setattr(workspace.messagebox, 'showwarning', Mock())
    monkeypatch.setattr(workspace.ChartWorkspaceDialog, 'schedule_render', lambda self: None)
    subscriptions = [sub for channel in Channel for sub in Publisher.get_subscribers(channel)]
    root = tk.Tk()
    root.withdraw()
    dialog = None
    try:
        dialog = workspace.open_chart_workspace(root, store, **context)
        assert isinstance(dialog, workspace.ChartWorkspaceDialog)
        assert dialog.tree.item('d:note')['text'] == '文字'
        assert dialog.tree.item('d:range')['text'] == '价格范围'
        dialog._info = dict(price_x=(0,500))
        dialog._xy = lambda point: (30+data.date.tolist().index(point[0])*25, 300-point[1]*20)
        for obj in before['drawings']:
            dialog._draw_object(obj, selected=True)
            assert dialog.canvas.find_withtag('obj:'+obj['id'])
            if obj['kind']=='path':
                line=next(item for item in dialog.canvas.find_withtag('obj:'+obj['id'])
                          if dialog.canvas.type(item)=='line')
                assert dialog.canvas.itemcget(line,'arrow') == ('both' if obj['id']=='arrow-path' else 'none')
            if obj['kind']=='arrow':
                shapes=[dialog.canvas.type(item) for item in dialog.canvas.find_withtag('obj:'+obj['id'])]
                assert ('polygon' if obj['id']=='marker-arrow' else 'line') in shapes
        texts = [dialog.canvas.itemcget(item, 'text') for item in dialog.canvas.find_all()
                 if dialog.canvas.type(item) == 'text']
        assert 'my note' in texts and '+2.000 (+22.22%)' in texts
        dialog._image = Image.new('RGB', (500,300))
        snapshot = dialog._snapshot()
        assert snapshot.getbbox() is not None
        assert store.chart_document(boot['identity']) == before
    finally:
        if dialog is not None:
            dialog.close()
        root.destroy()
        # ttkbootstrap keeps theme callbacks for destroyed legacy Comboboxes.
        # Restore only this test's subscriber changes, preserving earlier owners.
        for channel in Channel:
            for sub in list(Publisher.get_subscribers(channel)):
                Publisher.unsubscribe(sub.name)
        for sub in subscriptions:
            Publisher.subscribe(sub.name, sub.func, sub.channel)
    assert store.chart_document(boot['identity']) == before
