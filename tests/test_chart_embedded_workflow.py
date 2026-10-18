"""Real Store boundaries for embedded switches and forward-only alert edits."""
from copy import deepcopy

import pandas as pd
import pytest

from gui.chart_bridge import ChartBridge, workspace_boot
from gui.chart_embed import ChartSessionBridge
from workbench.market import save_dataset
from workbench.store import Store


def setup(tmp_path):
    store = Store(tmp_path)
    frame = pd.DataFrame(dict(date=['2026-10-09', '2026-10-14', '2026-10-15'],
        open=[9., 9., 11.], high=[9.1, 9.1, 11.1], low=[8.9, 8.9, 10.9],
        close=[9., 9., 11.], volume=100))
    save_dataset(store, 'sz.000001', frame, 'test', 'qfq')
    context = dict(frame=frame, record=dict(source='test', adjustment='qfq'),
        code='sz.000001', timeframe='daily', context_kind='candidate')
    return store, frame, workspace_boot(store, context)


def test_switch_reads_document_after_outgoing_flush_not_selection_time(tmp_path):
    store, _, boot = setup(tmp_path)
    pages = []
    session = ChartSessionBridge(store, boot, pages.append)
    stale = deepcopy(boot)
    session.pending = dict(token=2, boot=stale)
    state = dict(timeframe='daily', objects=[dict(id='drawn', type='trend',
        a=dict(index=0, value=9), b=dict(index=2, value=11), width=2)],
        indicators=dict(ema_length=30), view=dict(count=204), toolbar={})
    session.save_state(state, 0)
    assert session.activate_context() == {'token': 2}
    assert len(pages[-1]['drawings']) == 1
    assert pages[-1]['drawings'][0]['id'] == 'drawn'
    assert pages[-1]['revision'] == 1
    assert pages[-1]['settings']['ema_length'] == 30
    assert session.bootstrap()['state']['view']['count'] == 204
    assert stale['drawings'] == []


def test_manual_watch_display_period_keeps_daily_coordinates_and_saved_analysis(tmp_path):
    from gui.chart_bridge import refresh_workspace_boot
    store, frame, _ = setup(tmp_path)
    context = dict(frame=frame, code='sz.000001', timeframe='daily', display_timeframe='weekly',
                   record={'source':'test','adjustment':'qfq'}, context_kind='watch')
    boot = workspace_boot(store, context)
    assert boot['timeframe'] == 'daily' and boot['state']['timeframe'] == 'weekly'
    assert len(boot['series']['daily']) == len(frame)
    assert refresh_workspace_boot(store, boot)['state']['timeframe'] == 'weekly'
    ChartBridge(store, boot).save_state(dict(timeframe='monthly',objects=[]))
    assert workspace_boot(store, context)['state']['timeframe'] == 'monthly'
    assert refresh_workspace_boot(store, boot)['state']['timeframe'] == 'monthly'


def test_indicator_and_visibility_options_survive_detached_and_embedded_reopen(tmp_path):
    store, frame, boot = setup(tmp_path)
    bridge = ChartBridge(store, boot)
    options = dict(ema_source='hlc3', ema_length=17, ema_smoothing='vwma',
        ema_smooth_length=5, indicators_visible=False, strategy_visible=False,
        lock_drawings=True, hide_scope='all', sidebar_open=True)
    bridge.save_state(dict(objects=[],timeframe='daily',indicators=options))
    context = dict(frame=frame, record=dict(source='test',adjustment='qfq'),code='sz.000001',timeframe='daily')
    reopened = workspace_boot(store, context)
    assert reopened['state']['indicators'] == options
    assert reopened['settings']['ema_source'] == 'hlc3'
    session = ChartSessionBridge(store, reopened, lambda _: None)
    assert session.bootstrap()['state']['indicators'] == options


def test_layout_requests_accept_only_supported_native_counts(tmp_path):
    store, _, boot = setup(tmp_path)
    session = ChartSessionBridge(store, boot, lambda _: None)
    requested = []
    with pytest.raises(ValueError):
        session.request_layout(4)
    session._request_layout = requested.append
    for count in (1,4,6,9):
        assert session.request_layout(count) == {'ok': True}
    for count in (True,'4',2,16,None):
        with pytest.raises(ValueError):
            session.request_layout(count)
    assert requested == [1,4,6,9]


def test_replay_period_close_dates_use_exchange_last_session_not_weekend(tmp_path):
    store = Store(tmp_path)
    frame = pd.DataFrame(dict(date=pd.bdate_range('2026-01-05','2026-02-06').strftime('%Y-%m-%d'),
        open=10,high=11,low=9,close=10,volume=100))
    boot = workspace_boot(store, dict(frame=frame,record=dict(source='test',adjustment='qfq'),
        code='sz.000001',timeframe='daily'))
    assert boot['seriesCloseDates']['monthly']['2026-01-31'] == '2026-01-30'
    assert boot['seriesCloseDates']['weekly']['2026-01-16'] == '2026-01-16'


def test_blank_time_axis_contains_verified_sessions_without_synthetic_prices(tmp_path):
    _, _, boot = setup(tmp_path)
    rows = boot['series']['daily']
    assert '2026-10-19' in boot['axisDates']['daily']
    assert '2026-10-17' not in boot['axisDates']['daily']
    assert '2026-10-01' not in boot['axisDates']['daily']
    assert not any(day.startswith('2027') for day in boot['axisDates']['daily'])
    assert rows == boot['bars']
    assert rows[-1]['date'] < '2026-10-19'


def test_future_drawing_templates_and_replay_bookmark_survive_store_reopen(tmp_path):
    store, frame, boot = setup(tmp_path)
    bridge = ChartBridge(store, boot)
    templates = {'channel': {'channelLevels': [{'value': 0, 'enabled': True},
                                              {'value': 1.25, 'enabled': True}]}}
    bridge.save_state(dict(timeframe='daily', objects=[dict(id='future',type='trend',
        a=dict(index=0,date=frame.iloc[0].date,value=10.123456789),
        b=dict(index=len(frame)+3,date='2026-10-22',value=11.234567891),width=2)],
        templates=templates,replayDate='2026-10-13'))
    reopened = workspace_boot(store, dict(frame=frame,record=dict(source='test',adjustment='qfq'),
        code='sz.000001',timeframe='daily'))
    assert reopened['drawings'][0]['b']['date'] == '2026-10-22'
    assert reopened['drawings'][0]['b']['value'] == 11.234567891
    assert reopened['state']['templates'] == templates
    assert reopened['state']['replayDate'] == '2026-10-13'


def test_monitor_status_reads_never_advance_an_editors_revision_or_write(tmp_path):
    store, _, boot = setup(tmp_path)
    bridge = ChartBridge(store, boot, clock=lambda:'2026-10-16T20:00:00+08:00')
    bridge.create_price_alert(10)
    current = store.chart_document(boot['identity'])
    current['alerts'][0]['events'] = [{'bar':'2026-10-16','price':11}]
    current['alerts'][0]['status'] = '已触发'
    store.save_chart_document(boot['identity'],current,current['revision'])
    before = store.chart_document(boot['identity'])
    assert bridge.read_alerts()['alerts'] == before['alerts']
    assert bridge.revision == 1
    assert store.chart_document(boot['identity']) == before
    saved = bridge.save_state(dict(timeframe='daily',objects=[]),1)
    assert saved['alerts'][0]['events'] == before['alerts'][0]['events']


def test_native_chart_navigation_commands_are_scoped_to_the_display_request(tmp_path):
    import json
    from unittest.mock import Mock
    from gui.chart_embed import EmbeddedChartHost
    host = EmbeddedChartHost.__new__(EmbeddedChartHost)
    host.process = Mock()
    host.process.poll.return_value = None
    host.closing = False
    host.token = 7
    host.navigation = tmp_path/'own-chart.navigation.json'
    assert host.navigate('2026-10-15')
    command = json.loads(host.navigation.read_text(encoding='utf-8'))
    assert command['date'] == '2026-10-15' and command['token'] == 7
    assert host.navigate()
    reset = json.loads(host.navigation.read_text(encoding='utf-8'))
    assert reset['date'] is None and reset['id'] != command['id']
    with pytest.raises(ValueError):
        host.navigate('invalid')
    host.closing = True
    assert not host.navigate('2026-10-15')


def test_switch_materialization_failure_preserves_active_bridge(tmp_path):
    store, _, boot = setup(tmp_path)
    def fail(_):
        raise OSError('page unavailable')
    session = ChartSessionBridge(store, boot, fail)
    original = session.current
    session.pending = dict(token=3, boot=boot)
    with pytest.raises(OSError):
        session.activate_context()
    assert session.current is original and session.active_token == 0


def test_alert_edit_disable_reenable_delete_keep_forward_watermark_and_events(tmp_path):
    store, _, boot = setup(tmp_path)
    clock = ['2026-10-15T20:00:00+08:00']
    bridge = ChartBridge(store, boot, clock=lambda: clock[0])
    created = bridge.create_price_alert(10, expires='2026-11-01T09:00')['alert']
    assert created['expires'] == '2026-11-01T09:00:00+08:00'
    document = store.chart_document(boot['identity'])
    document['alerts'][0]['events'] = [{'bar': '2026-10-15', 'price': 11}]
    store.save_chart_document(boot['identity'], document, document['revision'])
    renamed = bridge.update_alert(created['id'], {'name': 'renamed'})['alerts'][0]
    assert renamed['events'] == document['alerts'][0]['events']
    assert renamed['active_after'] == clock[0]
    bridge.update_alert(created['id'], {'enabled': False})
    clock[0] = '2026-10-16T20:00:00+08:00'
    resumed = bridge.update_alert(created['id'], {'enabled': True})['alerts'][0]
    assert resumed['active_after'] == clock[0]
    assert resumed['events'] == renamed['events']
    clock[0] = '2026-10-19T20:00:00+08:00'
    edited = bridge.update_alert(created['id'], {'value': 12, 'timeframe': 'weekly'})['alerts'][0]
    assert edited['active_after'] == clock[0]
    assert edited['cursor'] == '2026-10-09'  # Friday Oct 16 is absent, cannot seed it.
    assert edited['timeframe'] == 'weekly' and edited['value'] == 12
    bridge.delete_alert(created['id'])
    assert store.chart_document(boot['identity'])['alerts'] == []


@pytest.mark.parametrize('changes', [{'cursor': '2026-10-19'}, {'enabled': 'false'},
    {'value': float('nan')}, {'target': 'order'}, {'timeframe': '5s'}])
def test_invalid_alert_mutations_do_not_write(tmp_path, changes):
    store, _, boot = setup(tmp_path)
    bridge = ChartBridge(store, boot, clock=lambda: '2026-10-15T20:00:00+08:00')
    alert = bridge.create_price_alert(10)['alert']
    before = store.chart_document(boot['identity'])
    with pytest.raises((ValueError, TypeError)):
        bridge.update_alert(alert['id'], changes)
    assert store.chart_document(boot['identity']) == before


def test_slot_asset_urls_change_with_content_but_not_symbol(tmp_path, monkeypatch):
    """A reused WebView must not load a mixture of old and current scripts."""
    import re
    import shutil
    import gui.chart_shell as shell
    assets = tmp_path / 'assets'
    shutil.copytree(shell.ASSETS, assets)
    monkeypatch.setattr(shell, 'ASSETS', assets)
    _, _, boot = setup(tmp_path / 'runtime')
    session = tmp_path / 'slot.json'
    page, _ = shell._materialize_page(session, boot)
    first = re.findall(r'(?:src|href)="([^"?]+\.(?:js|css)\?build=[a-f0-9]+)"',
                       page.read_text(encoding='utf-8'))
    assert len(first) == 10
    page, _ = shell._materialize_page(session, {**boot, 'name': 'another symbol'})
    assert all(url in page.read_text(encoding='utf-8') for url in first)
    script = assets / 'chart-indicators.js'
    script.write_text(script.read_text(encoding='utf-8') + '\n// new build\n', encoding='utf-8')
    page, _ = shell._materialize_page(session, boot)
    assert all(url not in page.read_text(encoding='utf-8') for url in first)


@pytest.mark.parametrize('writer', ['embedded', 'shell'])
def test_concurrent_session_notifications_never_share_a_staging_file(tmp_path, writer):
    from concurrent.futures import ThreadPoolExecutor
    import json
    from gui.chart_embed import atomic_json
    from gui.chart_shell import _write_json
    write = atomic_json if writer == 'embedded' else _write_json
    target = tmp_path / 'own-session.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: write(target, dict(token=n, body='x'*1000)), range(40)))
    result = json.loads(target.read_text(encoding='utf-8'))
    assert result['token'] in range(40) and result['body'] == 'x'*1000
    assert list(tmp_path.iterdir()) == [target]
