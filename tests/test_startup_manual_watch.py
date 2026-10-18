"""Startup stays responsive; manual watches never become scan/trade evidence."""
import json
import os
import threading
import time
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from workbench.store import Store
from workbench.market import save_dataset
from workbench.provider_guard import ProviderError, ProviderGuard
from workbench.watch_history import chart_day, chart_source, fetch_history, local_history


def native_case(name, tmp_path):
    """Production owns one Tk root; native lifecycle probes get one process."""
    if os.environ.get('APLUS_NATIVE_WATCH_CHILD') == name:
        return False
    result = subprocess.run([sys.executable, '-m', 'pytest', '-xq',
                             str(Path(__file__).resolve()) + '::' + name,
                             '--basetemp=' + str(tmp_path / 'native-child')],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=60,
                            env=dict(os.environ, APLUS_NATIVE_WATCH_CHILD=name, PYTHONIOENCODING='utf-8'))
    assert result.returncode == 0, result.stdout + result.stderr
    return True


def bars(end='2026-10-16'):
    return pd.DataFrame(dict(date=['2026-10-15', end], open=[10., 10.], high=[11., 11.],
                             low=[9., 9.], close=[10., 10.5], volume=[100., 200.]))


def business(store):
    return {t: store.rows('SELECT * FROM ' + t) for t in
            ['observations', 'plans', 'plan_history', 'accounts', 'positions', 'position_fills',
             'closed_trades', 'cash_flows', 'datasets', 'sync_coverage', 'scan_cache']}


class Provider:
    def __init__(self, error=None, cancel=None):
        self.calls = []
        self.error, self.cancel = error, cancel

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetch_unadjusted(self, code, start, end):
        self.calls.append((code, start, end))
        if self.error:
            raise self.error
        if self.cancel:
            self.cancel.set()
        return bars(end)


def test_add_manual_watch_has_no_fake_signal_plan_or_scope_change(tmp_path):
    store = Store(tmp_path)
    before = business(store)
    schema = store.rows("SELECT value FROM meta WHERE key='schema_version'")
    assert store.add_manual_watch('159570', '创新药ETF', '外部发现') == 'sz.159570'
    row = store.rows('SELECT * FROM watchlist')[0]
    assert row['observation_id'] == '' and row['notes'] == '外部发现' and row['active'] == 1
    assert store.rows("SELECT value FROM meta WHERE key='watch_name:sz.159570'")[0]['value'] == '创新药ETF'
    store.add_manual_watch('sz.159570')
    assert len(store.rows('SELECT * FROM watchlist')) == 1
    assert store.rows('SELECT notes FROM watchlist')[0]['notes'] == '外部发现'
    assert business(store) == before
    assert store.rows("SELECT value FROM meta WHERE key='schema_version'") == schema


def test_manual_readd_preserves_strategy_anchor_and_notes(tmp_path):
    store = Store(tmp_path)
    store.execute('INSERT INTO watchlist VALUES(?,?,?,?,?,?)', ('sz.000001', 'archived-anchor', '原理由', 0, 'x', 'x'))
    store.add_manual_watch('000001', '平安银行', '不得覆盖')
    row = store.rows('SELECT * FROM watchlist')[0]
    assert row['observation_id'] == 'archived-anchor' and row['notes'] == '原理由' and row['active'] == 1


@pytest.mark.parametrize('code,name,notes', [('bad', '', ''), ('000001', 'x'*81, ''),
                                          ('000001', '', 'x'*501), ('000001', 'a\nb', '')])
def test_bad_manual_input_does_not_write(tmp_path, code, name, notes):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.add_manual_watch(code, name, notes)
    assert store.rows('SELECT * FROM watchlist') == []


def test_chart_cache_is_separate_reusable_and_verified(tmp_path):
    store = Store(tmp_path)
    before = business(store)
    provider = Provider()
    frame, record = fetch_history(store, '513500', 'baostock', threading.Event(),
                                  provider_factory=lambda: provider, end='2026-10-16')
    assert len(provider.calls) == 1 and provider.calls[0][0] == 'sh.513500'
    assert record['source'] == 'baostock' and record['adjustment'] == '不复权'
    pd.testing.assert_frame_equal(local_history(store, '513500')[0], frame)
    assert business(store) == before
    # New process / Store object still sees the separate chart-only cache.
    assert local_history(Store(tmp_path), '513500')[1] == record
    (tmp_path / record['path']).write_bytes(b'corrupt')
    assert local_history(store, '513500') is None
    with pytest.raises(ValueError, match='拒绝覆盖'):
        fetch_history(store, '513500', 'baostock', threading.Event(),
                      provider_factory=Provider, end='2026-10-16')


def test_valid_local_market_is_read_only_and_source_specific(tmp_path):
    store = Store(tmp_path)
    save_dataset(store, 'sz.000001', bars(), 'baostock', '前复权')
    other = bars().copy()
    other.loc[:, ['open', 'high', 'low', 'close']] *= 2
    save_dataset(store, 'sz.000001', other, 'tickflow', '前复权')
    before = (tmp_path / 'workbench.sqlite3').read_bytes()
    assert local_history(store, '000001', 'baostock')[0].close.iloc[-1] == 10.5
    assert local_history(store, '000001', 'tickflow')[0].close.iloc[-1] == 21
    assert (tmp_path / 'workbench.sqlite3').read_bytes() == before


def test_chart_only_etf_alert_uses_verified_cache_and_only_future_closes(tmp_path):
    from gui.chart_bridge import ChartBridge, workspace_boot
    from gui.chart_alerts import load_alert_frame, ChartAlertMonitor
    from workbench.chart_workspace import evaluate_alert
    store = Store(tmp_path)
    before = business(store)
    provider = Provider()
    # Historical chart contents are deliberately older than the creation date.
    stale = bars('2026-10-16')
    frame, record = fetch_history(store, '513500', 'baostock', threading.Event(),
        provider_factory=lambda: provider, end='2026-10-16')
    boot = workspace_boot(store, dict(frame=frame, record=record, code='sh.513500',
                                     timeframe='daily', context_kind='watch'))
    moment = '2026-10-19T20:00:00+08:00'
    alert = ChartBridge(store, boot, clock=lambda: moment).create_price_alert(10.7)['alert']
    assert alert['cursor'] == '2026-10-16' and alert['active_after'] == moment
    # A newly delivered close from before creation must not notify.
    late = pd.concat([stale, stale.iloc[[-1]].assign(date='2026-10-19', close=10.9)], ignore_index=True)
    provider.fetch_unadjusted = Mock(return_value=late)
    fetch_history(store, '513500', 'baostock', threading.Event(),
                  provider_factory=lambda: provider, end='2026-10-19')
    alert, event = evaluate_alert(alert, load_alert_frame(store, boot['identity'], alert, moment), now=moment)
    assert event is None and alert['cursor'] == '2026-10-19'
    future = pd.concat([late, stale.iloc[[-1]].assign(date='2026-10-20', close=10.5),
                       stale.iloc[[-1]].assign(date='2026-10-21', close=10.9)], ignore_index=True)
    provider.fetch_unadjusted = Mock(return_value=future)
    fetch_history(store, '513500', 'baostock', threading.Event(),
                  provider_factory=lambda: provider, end='2026-10-21')
    later = '2026-10-21T20:00:00+08:00'
    current = ChartAlertMonitor(Mock(), store, clock=lambda: later)._frame(boot['identity'], alert)
    _, event = evaluate_alert(alert, current, now=later)
    assert event['bar'] == '2026-10-21'
    assert business(store) == before  # cache/analysis never enter scanning or trade tables


def test_chart_cache_alert_identity_and_hash_are_not_bypassed(tmp_path):
    from gui.chart_alerts import load_alert_frame
    store = Store(tmp_path)
    _, record = fetch_history(store, '513500', 'baostock', threading.Event(),
                             provider_factory=Provider, end='2026-10-16')
    spec = dict(code='sh.513500', source='baostock', adjustment='不复权', timeframe='weekly')
    now = '2026-10-16T20:00:00+08:00'
    assert load_alert_frame(store, '', spec, now).date.tolist() == ['2026-10-16']
    for mismatch in [dict(source='tickflow'), dict(adjustment='前复权'), dict(code='sh.513501')]:
        assert load_alert_frame(store, '', dict(spec, **mismatch), now) is None
    (store.root / record['path']).write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='校验失败'):
        load_alert_frame(store, '', spec, now)


def test_chart_cache_cannot_certify_missing_friday_and_never_splices_prices(tmp_path):
    from gui.chart_alerts import load_alert_frame
    store = Store(tmp_path)
    provider = Provider()
    provider.fetch_unadjusted = Mock(return_value=bars('2026-10-15').iloc[:1])
    cached, _ = fetch_history(store, '513500', 'baostock', threading.Event(),
                             provider_factory=lambda: provider, end='2026-10-16')
    spec = dict(code='sh.513500', source='baostock', adjustment='不复权', timeframe='weekly')
    assert load_alert_frame(store, '', spec, '2026-10-16T20:00:00+08:00').empty
    official = bars().assign(open=20., high=22., low=18., close=21.)
    save_dataset(store, 'sh.513500', official, 'baostock', '不复权')
    result = load_alert_frame(store, '', dict(spec, timeframe='daily'), '2026-10-16T20:00:00+08:00')
    assert result.close.tolist() == [21., 21.]
    assert len(cached) == 1  # the older cache was not combined with the scan series


def test_same_tail_chart_cache_preserves_query_watermark_and_does_not_refetch(tmp_path, monkeypatch):
    from gui.watch_chart import load_chart
    from PIL import Image
    store = Store(tmp_path)
    stale = bars('2026-10-16')
    save_dataset(store, 'sh.513500', stale, 'baostock', '前复权')
    provider = Provider()
    provider.fetch_unadjusted = Mock(return_value=stale)
    fetch_history(store, '513500', 'baostock', threading.Event(),
                  provider_factory=lambda: provider, end='2026-10-19')
    assert local_history(store, '513500')[1]['checked_through'] == '2026-10-19'
    monkeypatch.setattr('gui.watch_chart.chart_day', lambda: '2026-10-19')
    monkeypatch.setattr('gui.watch_chart.render_chart', lambda *_a, **_k: Image.new('RGB', (100, 100)))
    reply = load_chart(store, None, 'sh.513500', 'baostock', 'daily', 'local', lambda: None)
    assert reply['refresh'] is False and reply['record']['end'] == '2026-10-16'


def test_etf_routes_to_supported_provider_and_retired_source_is_not_reenabled(tmp_path):
    store = Store(tmp_path)
    store.execute("INSERT INTO meta VALUES('daily_market_source','tickflow')")
    assert chart_source(store, '159570') == chart_source(store, '513500') == 'baostock'
    assert chart_source(store, '000001') == 'tickflow'
    store.execute("UPDATE meta SET value='tencent' WHERE key='daily_market_source'")
    assert chart_source(store, '000001') == 'baostock'


@pytest.mark.parametrize('cancel_after', [False, True])
def test_tickflow_fresh_chart_session_respects_gap_under_connection_lease(tmp_path, monkeypatch, cancel_after):
    store = Store(tmp_path)
    store.execute("INSERT INTO meta VALUES('daily_market_source','tickflow')")
    sequence = []
    event = threading.Event()
    class TickProvider(Provider):
        def __enter__(self):
            sequence.append('lease')
            return self
        def fetch_unadjusted(self, *args):
            sequence.append('fetch')
            return super().fetch_unadjusted(*args)
        def __exit__(self, *_):
            sequence.append('release')
    def gap(cancelled):
        assert not cancelled.is_set()  # Cleanup gap must survive query cancellation.
        sequence.append('gap')
    monkeypatch.setattr('workbench.watch_history._request_gap', gap)
    def fetch():
        return fetch_history(store, '000001', 'tickflow', event,
                             provider_factory=lambda: TickProvider(cancel=event if cancel_after else None),
                             end='2026-10-16')
    if cancel_after:
        with pytest.raises(InterruptedError):
            fetch()
        assert local_history(store, '000001') is None
    else:
        fetch()
    assert sequence == ['lease', 'gap', 'fetch', 'gap', 'release']


def test_request_gap_is_full_duration_and_cancellable_without_real_wait():
    from workbench.watch_history import _request_gap
    current = [0.]
    waits = []
    def wait(seconds):
        waits.append(seconds)
        current[0] += seconds
    _request_gap(threading.Event(), clock=lambda: current[0], wait=wait)
    assert current[0] >= 1.5 and all(seconds <= .1 for seconds in waits)
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        _request_gap(event)


def test_chart_cancellation_supports_original_provider_event_protocol(tmp_path, monkeypatch):
    from gui.watch_chart import _Cancellation, load_chart
    from workbench.provider_process import BaoStock
    from PIL import Image
    stopped = threading.Event()
    def check():
        if stopped.is_set():
            raise InterruptedError('selection changed')
    cancellation = _Cancellation(check)
    assert cancellation.wait(0) is False
    stopped.set()
    assert cancellation.wait(1) is True
    stopped.clear()
    cancellation.set()
    assert cancellation.wait(1) is True
    requests = []
    class OfflineAdapter(BaoStock):
        # Keep the real fetch_unadjusted -> _query -> cancel_event.wait path.
        # Only subprocess transport is replaced; there is no network or lease.
        def _start(self):
            self.process = object()
        def _stop(self):
            self.process = None
        def _request(self, operation, args, timeout, cancellable=True):
            requests.append(operation)
            return bars().to_dict('records') if operation == 'fetch' else None
    store = Store(tmp_path)
    before = business(store)
    monkeypatch.setattr('workbench.market.BaoStock', OfflineAdapter)
    monkeypatch.setattr('workbench.watch_history.chart_day', lambda: '2026-10-16')
    monkeypatch.setattr('gui.watch_chart.render_chart', lambda *_a, **_k: Image.new('RGB', (100, 100)))
    result = load_chart(store, None, 'sh.513500', 'baostock', 'daily', 'fetch', check)
    assert requests == ['fetch', 'logout'] and result['record']['end'] == '2026-10-16'
    assert business(store) == before


def test_cooldown_checked_before_provider_construction(tmp_path):
    store = Store(tmp_path)
    ProviderGuard(store, 'baostock', clock=lambda: 10**12).failure(ProviderError('baostock', 'fetch', 'blocked', '429'))
    factory = Mock()
    with pytest.raises(ValueError, match='暂停请求'):
        fetch_history(store, '513500', 'baostock', threading.Event(), provider_factory=factory, end='2026-10-16')
    factory.assert_not_called()


@pytest.mark.parametrize('error', [ProviderError('baostock', 'fetch', '黑名单', '10001011'),
    ProviderError('baostock', 'fetch', 'rate limit', '429'), ProviderError('baostock', 'fetch', 'refused', '403'),
    TimeoutError('network timeout')])
def test_restriction_and_timeout_record_guard_without_retry_or_signal_writes(tmp_path, error):
    store = Store(tmp_path)
    before = business(store)
    provider = Provider(error)
    with pytest.raises(type(error)):
        fetch_history(store, '513500', 'baostock', threading.Event(),
                      provider_factory=lambda: provider, end='2026-10-16')
    assert len(provider.calls) == 1 and ProviderGuard(store, 'baostock').state()['until'] > time.time()
    assert local_history(store, '513500') is None and business(store) == before


@pytest.mark.parametrize('cancel_before', [True, False])
def test_cancel_does_not_commit_history(tmp_path, cancel_before):
    store = Store(tmp_path)
    event = threading.Event()
    if cancel_before:
        event.set()
    factory = Mock(return_value=Provider(cancel=event))
    with pytest.raises(InterruptedError):
        fetch_history(store, '513500', 'baostock', event, provider_factory=factory, end='2026-10-16')
    assert local_history(store, '513500') is None
    assert not store.rows("SELECT * FROM meta WHERE key LIKE 'provider_guard:%'")
    if cancel_before:
        factory.assert_not_called()


def test_calendar_avoids_repeated_weekend_and_holiday_requests(monkeypatch):
    monkeypatch.setattr('workbench.watch_history.completed_date', lambda: '2026-10-18')
    assert chart_day() == '2026-10-16'
    monkeypatch.setattr('workbench.watch_history.completed_date', lambda: '2026-10-05')
    assert chart_day() == '2026-09-30'


def test_queued_tiles_recheck_guard_after_first_tile_is_restricted(tmp_path):
    store = Store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    class Restricted(Provider):
        def fetch_unadjusted(self, *_):
            entered.set()
            assert release.wait(3)
            raise ProviderError('baostock', 'fetch', '黑名单', '10001011')
    second = Mock(return_value=Provider())
    def request(code, factory):
        try:
            fetch_history(store, code, 'baostock', threading.Event(), provider_factory=factory, end='2026-10-16')
        except Exception as exc:
            errors.append(exc)
    first = threading.Thread(target=request, args=('513500', Restricted))
    queued = threading.Thread(target=request, args=('159570', second))
    first.start()
    assert entered.wait(2)
    queued.start()
    try:
        time.sleep(.1)
        second.assert_not_called()
    finally:
        release.set()
        first.join(4)
        queued.join(4)
    assert not first.is_alive() and not queued.is_alive()
    assert len(errors) == 2 and any(isinstance(exc, ValueError) for exc in errors)
    second.assert_not_called()


def test_added_store_api_remains_updater_compatible():
    from pathlib import Path
    import ast
    from workbench.version_update import compatible_store_extension
    root = Path(__file__).resolve().parents[1]
    new = (root / 'workbench/store.py').read_text(encoding='utf-8')
    tree = ast.parse(new)
    store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Store')
    store.body = [n for n in store.body if not (isinstance(n, ast.FunctionDef) and n.name == 'add_manual_watch')]
    old = ast.unparse(tree)  # Also works with GitHub's shallow checkouts.
    assert compatible_store_extension(old, new)


def test_chart_read_blocks_upgrade_and_cleans_up_on_error(tmp_path):
    from workbench.service import Service
    service = Service(Store(tmp_path))
    try:
        with pytest.raises(RuntimeError):
            with service.chart_access(threading.Event()):
                with pytest.raises(ValueError, match='尚未结束'):
                    service.reserve_code_update()
                raise RuntimeError('cancelled chart')
        service.reserve_code_update()
        with pytest.raises(ValueError, match='软件更新'):
            with service.chart_access(threading.Event()):
                pass
    finally:
        service.pool.shutdown(wait=True)


def test_business_task_cancels_chart_then_waits_for_safe_release_without_blocking_submit(tmp_path):
    from workbench.service import Service
    service = Service(Store(tmp_path))
    cancelled = threading.Event()
    started = threading.Event()
    service._universe = Mock(side_effect=lambda *_: (started.set() or {'success': 1}))
    try:
        with service.chart_access(cancelled):
            job = service.submit('universe', {})
            assert cancelled.is_set()
            assert not started.wait(.1)
            with pytest.raises(ValueError, match='任务正在进行'):
                with service.chart_access(threading.Event()):
                    pass
        assert started.wait(2)
    finally:
        service.pool.shutdown(wait=True)
    assert service.store.rows('SELECT status FROM jobs WHERE id=?', (job,))[0]['status'] == 'completed'
    assert service.cancel_flags == {}


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_first_screen_never_waits_for_slow_audit_and_is_maximized(tmp_path, monkeypatch, record_property):
    if native_case('test_first_screen_never_waits_for_slow_audit_and_is_maximized', tmp_path):
        return
    import tkinter as tk
    from gui.main_window import AplusMainWindow
    from gui import background_status
    started, release = threading.Event(), threading.Event()
    callers = []
    def slow(*_):
        callers.append(threading.get_ident())
        started.set()
        release.wait(8)
        raise ValueError('isolated slow display audit')
    monkeypatch.setattr(background_status, 'load_status', slow)
    monkeypatch.setattr('workbench.readiness.audit_scope', slow)
    store = Store(tmp_path)
    begin = time.monotonic()
    window = AplusMainWindow(store=store)
    try:
        record_property('constructor_seconds', round(time.monotonic() - begin, 3))
        assert threading.get_ident() not in callers
        assert set(window._pages) == {'premarket'}
        window.update()
        assert window.state() == 'zoomed' and window.winfo_ismapped()
        assert started.wait(1)
        assert '后台核对' in window.toolbar._header_data_var.get()
        changed = []
        window.after(0, lambda: changed.append(True))
        window.update()
        assert changed == [True] and not release.is_set()
        assert window.watch.add_button.cget('text') == '+'
        window._switch_section('other')
        assert 'other' in window._pages and 'afterhours' not in window._pages
    finally:
        release.set()
        deadline = time.monotonic() + 2
        reader = window.toolbar._status_reader
        while reader is not None and reader.running and time.monotonic() < deadline:
            time.sleep(.01)
        window.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_manual_dialog_add_and_chart_do_not_require_an_observation(tmp_path, monkeypatch):
    if native_case('test_manual_dialog_add_and_chart_do_not_require_an_observation', tmp_path):
        return
    import tkinter as tk
    from PIL import Image
    from gui.watch_panel import WatchPanel, ManualWatchDialog
    from gui.chart_panel import ChartGrid
    from gui.chart_items import chart_items
    store = Store(tmp_path)
    save_dataset(store, 'sz.000001', bars(), 'baostock', '前复权')
    monkeypatch.setattr('gui.watch_chart.render_chart', lambda *_a, **_k: Image.new('RGB', (500, 300)))
    monkeypatch.setattr('gui.watch_chart.chart_day', lambda: '2026-10-16')
    monkeypatch.setattr('gui.watch_chart.fetch_history', Mock(side_effect=AssertionError('no network for complete local chart')))
    root = tk.Tk()
    try:
        watch = WatchPanel(root, store)
        dialog = ManualWatchDialog(watch, store, watch._manual_added)
        dialog.query.set('000001')
        dialog.notes.set('外部发现')
        dialog._save()
        assert not dialog.winfo_exists()
        assert len(watch.rows()) == 1 and watch.rows()[0]['manual']
        items = chart_items(watch.rows(), 'watch')
        assert items[0].observation_id is None and items[0].manual
        grid = ChartGrid(root, store)
        grid.pack(fill='both', expand=True)
        grid.set_items(items)
        deadline = time.monotonic() + 3
        while grid._slots[0]._image is None and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        slot = grid._slots[0]
        assert slot._image is not None and '手动关注' in slot._title_var.get()
        assert slot._workspace_context['payload'] == {} and slot._workspace_context['strategy_type'] == ''
        assert store.rows('SELECT * FROM observations') == store.rows('SELECT * FROM plans') == []
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_compatibility_monitor_starts_checks_other_period_and_recovers_from_read_failure(tmp_path, monkeypatch):
    if native_case('test_compatibility_monitor_starts_checks_other_period_and_recovers_from_read_failure', tmp_path):
        return
    import tkinter as tk
    from datetime import datetime
    import gui.chart_workspace as workspace
    from gui.chart_bridge import ChartBridge, workspace_boot
    store = Store(tmp_path)
    initial = pd.DataFrame(dict(date=['2026-10-02','2026-10-09'], open=9., high=9.1,
                                low=8.9, close=9., volume=100))
    save_dataset(store, 'sz.000001', initial, 'baostock', '前复权')
    context = dict(frame=initial, record={'source':'baostock','adjustment':'前复权'},
                   code='sz.000001', timeframe='daily')
    boot = workspace_boot(store, context)
    bridge = ChartBridge(store, boot, clock=lambda:'2026-10-15T20:00:00+08:00')
    bridge.create_price_alert(10, timeframe='daily')
    bridge.create_price_alert(10, timeframe='weekly')
    current = pd.concat([initial, initial.iloc[[-1]].assign(date='2026-10-16',
                         open=11.,high=11.1,low=10.9,close=11.)], ignore_index=True)
    save_dataset(store, 'sz.000001', current, 'baostock', '前复权')
    monkeypatch.setattr(workspace, 'datetime', Mock(now=lambda *_:datetime.fromisoformat('2026-10-16T20:00:00+08:00')))
    monkeypatch.setattr(workspace.ChartWorkspaceDialog, 'schedule_render', lambda _self:None)
    warning = Mock()
    monkeypatch.setattr(workspace.messagebox, 'showwarning', warning)
    root = tk.Tk()
    dialog = None
    try:
        dialog = workspace.ChartWorkspaceDialog(root, store, **context)
        assert dialog._alert_after in root.tk.call('after','info')
        assert boot['identity'] in root._active_chart_workspaces
        # Run the actual scheduled callback rather than sleeping for its timer.
        dialog.top.after_cancel(dialog._alert_after)
        dialog._scheduled_alert_check()
        saved = store.chart_document(boot['identity'])
        assert dialog.timeframe == 'daily'
        assert {alert['timeframe'] for alert in saved['alerts'] if len(alert['events'])==1} == {'daily','weekly'}
        assert all(alert['cursor']=='2026-10-16' for alert in saved['alerts'])
        assert dialog._alert_after in root.tk.call('after','info')
        warning.assert_called_once()
        dialog.top.after_cancel(dialog._alert_after)
        monkeypatch.setattr(dialog, 'check_alerts', Mock(side_effect=OSError('坏的行情文件')))
        dialog._scheduled_alert_check()
        assert '坏的行情文件' in dialog.status.get()
        assert dialog._alert_after in root.tk.call('after','info')
        assert store.chart_document(boot['identity']) == saved
    finally:
        if dialog is not None:
            dialog.close()
        root.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_manual_watch_compatibility_fallback_accepts_display_period_and_saved_analysis(tmp_path, monkeypatch):
    if native_case('test_manual_watch_compatibility_fallback_accepts_display_period_and_saved_analysis', tmp_path):
        return
    import tkinter as tk
    import gui.chart_workspace as workspace
    from gui.chart_bridge import ChartBridge, workspace_boot
    store = Store(tmp_path)
    context = dict(frame=bars(), record={'source':'baostock','adjustment':'不复权'},
                   code='sh.513500', timeframe='daily', display_timeframe='weekly', context_kind='watch')
    monkeypatch.setenv('APLUS_TK_CHART_FALLBACK', '1')
    monkeypatch.setattr(workspace.ChartWorkspaceDialog, 'schedule_render', lambda _self: None)
    root = tk.Tk()
    try:
        dialog = workspace.open_chart_workspace(root, store, **context)
        assert dialog.base_timeframe == 'daily' and dialog.timeframe == 'weekly'
        assert dialog.base_frame.date.tolist() == bars().date.tolist()
        assert dialog.frame.date.tolist() == ['2026-10-16']
        dialog.close()
        boot = workspace_boot(store, context)
        ChartBridge(store, boot).save_state(dict(timeframe='monthly', objects=[]))
        before = store.chart_document(boot['identity'])
        dialog = workspace.open_chart_workspace(root, store, **context)
        assert dialog.base_timeframe == 'daily' and dialog.timeframe == 'monthly'
        assert store.chart_document(boot['identity']) == before
        dialog.close()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_manual_watch_enters_default_interactive_grid_without_fake_signal(tmp_path, monkeypatch):
    if native_case('test_manual_watch_enters_default_interactive_grid_without_fake_signal', tmp_path):
        return
    import tkinter as tk
    from PIL import Image
    from gui.chart_panel import ChartGrid
    from gui.chart_items import ChartItem
    from gui.chart_bridge import workspace_boot
    store = Store(tmp_path)
    save_dataset(store, 'sz.000001', bars(), 'baostock', '前复权')
    before = business(store)
    host = Mock()
    factory = Mock(return_value=host)
    factory.available.return_value = True
    monkeypatch.setattr('gui.chart_embed.EmbeddedChartHost', factory)
    monkeypatch.setattr('gui.watch_chart.render_chart', lambda *_a, **_k: Image.new('RGB', (500,300)))
    monkeypatch.setattr('gui.watch_chart.chart_day', lambda: '2026-10-16')
    monkeypatch.setattr('gui.watch_chart.fetch_history', Mock(side_effect=AssertionError('local chart needs no fetch')))
    root = tk.Tk()
    try:
        grid = ChartGrid(root, store, interactive=True, layout_count=1)
        grid.pack()
        grid.set_items([ChartItem('sz.000001','测试',None,'baostock','watch',timeframe='weekly',manual=True)])
        deadline = time.monotonic()+3
        while not host.show.call_count and time.monotonic()<deadline:
            root.update()
            time.sleep(.01)
        host.show.assert_called_once()
        context = host.show.call_args.args[0]
        assert context['payload'] == {} and context['strategy_type'] == ''
        boot = workspace_boot(store, context)
        assert boot['state']['timeframe'] == 'weekly'
        assert boot['timeframe'] == 'daily' and len(boot['series']['daily']) == len(bars())
        assert business(store) == before
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Native desktop')
def test_late_manual_chart_cannot_replace_new_selection(tmp_path, monkeypatch):
    if native_case('test_late_manual_chart_cannot_replace_new_selection', tmp_path):
        return
    import tkinter as tk
    from PIL import Image
    from gui.chart_items import ChartItem
    from gui.chart_panel import ChartGrid
    from gui import watch_chart
    store = Store(tmp_path)
    for code in ['sz.000001', 'sz.000002']:
        save_dataset(store, code, bars(), 'baostock', '前复权')
    monkeypatch.setattr(watch_chart, 'render_chart', lambda *_a, **_k: Image.new('RGB', (500, 300)))
    monkeypatch.setattr(watch_chart, 'chart_day', lambda: '2026-10-16')
    original = watch_chart.load_chart
    entered, release = threading.Event(), threading.Event()
    def blocked(store, service, code, source, timeframe, phase, check):
        if code == 'sz.000001':
            entered.set()
            release.wait(3)
        check()
        return original(store, service, code, source, timeframe, phase, check)
    monkeypatch.setattr(watch_chart, 'load_chart', blocked)
    root = tk.Tk()
    try:
        grid = ChartGrid(root, store)
        grid.pack()
        def item(code):
            return ChartItem(code, code, None, 'baostock', 'watch', timeframe='daily', manual=True)
        grid.set_items([item('sz.000001')])
        root.update()
        assert entered.wait(1)
        old = grid._slots[0]._manual_controller
        grid.set_items([item('sz.000002')])
        deadline = time.monotonic() + 3
        while grid._slots[0]._image is None and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert grid._slots[0]._code == 'sz.000002' and grid._slots[0]._image is not None
        release.set()
        while old.reader.running and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        root.update()
        assert grid._slots[0]._workspace_context['code'] == 'sz.000002'
    finally:
        release.set()
        root.destroy()
