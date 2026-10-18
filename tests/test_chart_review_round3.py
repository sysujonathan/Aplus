"""Historical display snapshots must never backfill a newly created alert."""
from copy import deepcopy
from unittest.mock import Mock

import pandas as pd
import pytest

from gui.chart_alerts import ChartAlertMonitor, load_alert_frame
from gui.chart_bridge import ChartBridge, workspace_boot
from workbench.chart_workspace import bar_completed, evaluate_alert
from workbench.market import save_dataset
from workbench.store import Store


def bars(days, prices=None):
    prices = prices or [9.] * len(days)
    return pd.DataFrame(dict(date=days, open=prices, high=[p+.1 for p in prices],
                             low=[p-.1 for p in prices], close=prices, volume=100))


def setup(store, display):
    return workspace_boot(store, dict(frame=display, record=dict(source='test', adjustment='qfq'),
        code='sz.000001', timeframe='daily', context_kind='candidate'))


@pytest.mark.parametrize('timeframe', ['daily', 'weekly', 'monthly'])
def test_historical_candidate_uses_official_snapshot_and_never_fires_existing_close(tmp_path, timeframe):
    store = Store(tmp_path)
    display = bars(['2026-07-31', '2026-08-31'])
    official = bars(['2026-07-31', '2026-08-31', '2026-09-30', '2026-10-09', '2026-10-15'],
                    [9, 9, 11, 11, 11])
    save_dataset(store, 'sz.000001', official, 'test', 'qfq')
    boot = setup(store, display)
    now = '2026-10-15T20:00:00+08:00'
    alert = ChartBridge(store, boot, clock=lambda: now).create_price_alert(
        10, condition='above', timeframe=timeframe)['alert']
    assert alert['active_after'] == now
    assert alert['cursor'] > display.date.iloc[-1]
    monitor = ChartAlertMonitor(Mock(), store, clock=lambda: now)
    current = monitor._frame(boot['identity'], alert)
    updated, event = evaluate_alert(alert, current, now=now)
    assert event is None and updated['events'] == []


def test_late_arriving_pre_creation_daily_close_is_skipped_but_future_cross_triggers(tmp_path):
    store = Store(tmp_path)
    stale = bars(['2026-09-29', '2026-09-30'])
    save_dataset(store, 'sz.000001', stale, 'test', 'qfq')
    boot = setup(store, stale)
    now = '2026-10-15T20:00:00+08:00'
    alert = ChartBridge(store, boot, clock=lambda: now).create_price_alert(10)['alert']
    # The official source itself was stale at creation. A later import must not backfill.
    late = bars(['2026-09-29', '2026-09-30', '2026-10-14', '2026-10-15'], [9,9,9,11])
    save_dataset(store, 'sz.000001', late, 'test', 'qfq')
    current = load_alert_frame(store, boot['identity'], alert, now)
    alert, event = evaluate_alert(alert, current, now=now)
    assert event is None and alert['cursor'] == '2026-10-15'
    future = pd.concat([late, bars(['2026-10-16', '2026-10-19'], [9,11])], ignore_index=True)
    save_dataset(store, 'sz.000001', future, 'test', 'qfq')
    later = '2026-10-19T20:00:00+08:00'
    current = load_alert_frame(store, boot['identity'], alert, later)
    advanced, event = evaluate_alert(alert, current, now=later)
    assert event['bar'] == '2026-10-19' and len(advanced['events']) == 1


def test_thursday_only_snapshot_cannot_seed_friday_weekly_close(tmp_path):
    store = Store(tmp_path)
    stale = bars(['2026-10-09', '2026-10-12', '2026-10-15'])
    save_dataset(store, 'sz.000001', stale, 'test', 'qfq')
    boot = setup(store, stale)
    now = '2026-10-16T20:00:00+08:00'
    alert = ChartBridge(store, boot, clock=lambda: now).create_price_alert(
        10, timeframe='weekly')['alert']
    assert alert['cursor'] == '2026-10-09'
    assert load_alert_frame(store, boot['identity'], alert, now).date.tolist() == ['2026-10-09']
    # Friday data delivered late is already before the creation watermark.
    complete = pd.concat([stale, bars(['2026-10-16'], [11])], ignore_index=True)
    save_dataset(store, 'sz.000001', complete, 'test', 'qfq')
    alert, event = evaluate_alert(alert, load_alert_frame(store, boot['identity'], alert, now), now=now)
    assert event is None and alert['cursor'] == '2026-10-16'
    complete = pd.concat([complete, bars(['2026-10-23', '2026-10-30'], [9,11])], ignore_index=True)
    save_dataset(store, 'sz.000001', complete, 'test', 'qfq')
    later = '2026-10-30T20:00:00+08:00'
    _, event = evaluate_alert(alert, load_alert_frame(store, boot['identity'], alert, later), now=later)
    assert event['bar'] == '2026-10-30'


def test_no_matching_official_source_rejects_without_writes(tmp_path):
    store = Store(tmp_path)
    data = bars(['2026-10-14', '2026-10-15'])
    boot = setup(store, data)
    bridge = ChartBridge(store, boot)
    for source, adjustment in [('other', 'qfq'), ('test', 'hfq')]:
        save_dataset(store, 'sz.000001', data, source, adjustment)
        with pytest.raises(ValueError, match='正式行情'):
            bridge.create_price_alert(10)
    assert store.chart_document(boot['identity'])['revision'] == 0


def test_monthly_alert_checks_last_trading_day_without_waiting_for_calendar_month_end(tmp_path):
    store = Store(tmp_path)
    data = bars(['2025-12-31', '2026-01-29'], [9,9])
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    boot = setup(store, data)
    now = '2026-01-30T18:59:59+08:00'
    alert = ChartBridge(store, boot, clock=lambda: now).create_price_alert(10, timeframe='monthly')['alert']
    data = pd.concat([data, bars(['2026-01-30'], [11])], ignore_index=True)
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    assert not bar_completed('2026-01-31', 'monthly', now)
    close = '2026-01-30T19:00:00+08:00'
    assert bar_completed('2026-01-31', 'monthly', close)
    monitor = ChartAlertMonitor(Mock(), store, clock=lambda: close)
    current = monitor._frame(boot['identity'], alert)
    updated, event = evaluate_alert(alert, current, now=close)
    assert event['bar'] == '2026-01-31' and updated['cursor'] == '2026-01-31'
    # A missing last trading day cannot masquerade as the final month close.
    other = Store(tmp_path / 'incomplete')
    save_dataset(other, 'sz.000001', data.iloc[:-1], 'test', 'qfq')
    assert load_alert_frame(other, boot['identity'], alert, close).date.tolist() == ['2025-12-31']


def test_watermark_survives_store_roundtrip_and_alert_only_rebase(tmp_path):
    store = Store(tmp_path)
    data = bars(['2026-10-14', '2026-10-15'])
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    boot = setup(store, data)
    bridge = ChartBridge(store, boot, clock=lambda: '2026-10-15T20:00:00+08:00')
    alert = bridge.create_price_alert(10)['alert']
    other = deepcopy(store.chart_document(boot['identity']))
    other['alerts'][0]['status'] = 'background'
    store.save_chart_document(boot['identity'], other, 1)
    bridge.save_state(dict(objects=[], timeframe='daily'), 1)
    assert store.chart_document(boot['identity'])['alerts'][0]['active_after'] == alert['active_after']


def test_real_poll_skips_late_history_without_notification_and_persists_watermark(tmp_path, monkeypatch):
    store = Store(tmp_path)
    stale = bars(['2026-09-29', '2026-09-30'])
    save_dataset(store, 'sz.000001', stale, 'test', 'qfq')
    boot = setup(store, stale)
    moment = '2026-10-15T20:00:00+08:00'
    alert = ChartBridge(store, boot, clock=lambda: moment).create_price_alert(10)['alert']
    late = pd.concat([stale, bars(['2026-10-14', '2026-10-15'], [9,11])], ignore_index=True)
    save_dataset(store, 'sz.000001', late, 'test', 'qfq')
    root = Mock(_active_chart_workspaces=set())
    warning = Mock()
    monkeypatch.setattr('gui.chart_alerts.messagebox.showwarning', warning)
    monitor = ChartAlertMonitor(root, store, clock=lambda: moment)
    monitor.poll()
    saved = store.chart_document(boot['identity'])['alerts'][0]
    assert saved['cursor'] == '2026-10-15' and saved['events'] == []
    assert saved['active_after'] == alert['active_after']
    assert monitor.last_error is None
    warning.assert_not_called()
    root.bell.assert_not_called()


def test_creation_verifies_official_artifact_bytes(tmp_path):
    store = Store(tmp_path)
    data = bars(['2026-10-14', '2026-10-15'])
    snapshot = save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    record = store.rows('SELECT * FROM datasets WHERE id=?', (snapshot,))[0]
    # Isolated fixture only: simulate a corrupted official snapshot.
    (store.root / record['path']).write_bytes(b'corrupted')
    boot = setup(store, data)
    with pytest.raises(ValueError):
        ChartBridge(store, boot).create_price_alert(10)
    assert store.chart_document(boot['identity'])['revision'] == 0


def test_holiday_week_closes_on_verified_last_trading_day(tmp_path):
    store = Store(tmp_path)
    data = bars(['2026-06-12', '2026-06-18'])
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    spec = dict(code='sz.000001', source='test', adjustment='qfq', timeframe='weekly')
    boot = setup(store, data)
    assert not bar_completed('2026-06-19', 'weekly', '2026-06-18T18:59:59+08:00')
    assert load_alert_frame(store, boot['identity'], spec, '2026-06-18T19:00:00+08:00').date.tolist() == [
        '2026-06-12', '2026-06-19']


def test_compatibility_alert_cannot_notify_when_progress_save_conflicts(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    import gui.chart_workspace as workspace
    store = Store(tmp_path)
    data = bars(['2026-10-14','2026-10-15'])
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    boot = setup(store, data)
    ChartBridge(store, boot, clock=lambda:'2026-10-15T20:00:00+08:00').create_price_alert(10)
    before = deepcopy(store.chart_document(boot['identity']))
    data = pd.concat([data, bars(['2026-10-16'],[11])], ignore_index=True)
    save_dataset(store, 'sz.000001', data, 'test', 'qfq')
    owner = SimpleNamespace(replay_index=None, history=SimpleNamespace(value=deepcopy(before)),
        store=store, identity=boot['identity'], code='sz.000001', source='test', adjustment='qfq',
        top=Mock(), _save=Mock(return_value=False), refresh_tree=Mock())
    monkeypatch.setattr(workspace, 'datetime', Mock(now=lambda *_:datetime.fromisoformat('2026-10-16T20:00:00+08:00')))
    warning = Mock()
    monkeypatch.setattr(workspace.messagebox, 'showwarning', warning)
    workspace.ChartWorkspaceDialog.check_alerts(owner, silent=True)
    owner._save.assert_called_once()
    assert owner.history.value == before
    assert store.chart_document(boot['identity']) == before
    warning.assert_not_called()
    owner.top.bell.assert_not_called()
