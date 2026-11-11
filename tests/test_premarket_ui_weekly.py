"""Isolated regressions for daily -> weekly preparation and nonblocking selection."""
import json
import threading
import time

import pytest

from tests.test_workbench import store, frame, wait_for
from tests.test_premarket_resume import seeded
from tests.test_tickflow_scan_readiness import bars
from workbench.market import save_dataset
from workbench.premarket_resume import ordinary_update_codes
from workbench.sources import save_coverage, ADJUSTMENTS


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
def test_period_switch_requests_missing_prefix_but_not_pre_ipo_dates(store, source):
    data = bars(1800)
    codes = ('sh.600000', 'sh.600001', 'sh.600002')
    start, day, _ = seeded(store, data, source, codes=codes)
    short = data.iloc[-540:].reset_index(drop=True)
    for code in codes[:2]:
        did = save_dataset(store, code, short, source, ADJUSTMENTS[source])
        # Second stock already received the older request (young listing).
        save_coverage(store, code, did, short.date.iloc[0] if code == codes[0] else start, day, source)
    assert ordinary_update_codes(store, source, ['沪深主板'], day) == []
    assert ordinary_update_codes(store, source, ['沪深主板'], day, start=start) == [codes[0]]
    # Returning to the daily window does not request the same old prefix.
    assert ordinary_update_codes(store, source, ['沪深主板'], day, start=short.date.iloc[0]) == []


def test_weekly_resume_extends_daily_history_once_and_then_reuses_it(store, monkeypatch):
    from workbench.service import Service
    from workbench.readiness import audit_scope
    from workbench.sources import source_file
    data = bars(1800)
    short = data.iloc[-540:].reset_index(drop=True)
    _, day, _ = seeded(store, short, 'tickflow')
    start = data.date.iloc[0]
    # A verified long calendar/directory does not imply long prices per stock.
    store.write_artifact(str(source_file(store, 'trading_calendar.json', 'tickflow').relative_to(store.root)),
                         json.dumps(dict(start=start, end=day, trading_days=data.date.tolist())).encode(),
                         'scope-tickflow')
    before = audit_scope(store, ['沪深主板'], day, source='tickflow', timeframe='weekly')
    assert before['ready'] == 0 and before['gaps'][0]['category'] == 'insufficient_bars'
    calls = []

    class Provider:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def universe(self, *_): pytest.fail('same-day directory should be reused')
        def preload(self, group, a, b): calls.append((group, a, b))
        def fetch(self, code, a, b): return data.loc[data.date.between(a, b)].copy()

    monkeypatch.setattr('workbench.tickflow.TickFlow', Provider)
    service = Service(store)
    spec = dict(source='tickflow', boards=['沪深主板'], start=start, end=day,
                scan_timeframe='weekly', resume=True)
    try:
        first = wait_for(store, service.submit('sync', spec))
        assert first['status'] == 'completed', first['result']
        result = json.loads(first['result'])
        assert result['scan_readiness']['ready'] == 1
        assert calls == [(['sh.600000'], start, day)]
        second = wait_for(store, service.submit('sync', spec))
        assert second['status'] == 'completed', second['result']
        assert len(calls) == 1
        assert json.loads(second['result'])['connections'] == 0
    finally:
        service.pool.shutdown()


@pytest.mark.native_process
@pytest.mark.skipif(__import__('os').name != 'nt', reason='native Tk responsiveness')
def test_candidate_switch_and_context_menu_remain_responsive_during_blocked_read(store, frame, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    import ttkbootstrap as ttk
    from gui.chart_panel import ChartPanel
    from gui.candidate_tabs import CandidateTabs
    from gui.chart_embed import EmbeddedChartHost

    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    for oid in ('first', 'last'):
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (oid, 'synthetic', 'sh.600000', 'STRATEGY_3K', 'fixture', 'daily',
                       frame.date.iloc[-1], frame.date.iloc[-1], did, '{}', 'now'))
    root = ttk.Window()
    root.title('Aplus 隔离测试：切股与右键响应')
    panel = ChartPanel(root, store=store, interactive=True)
    panel.pack()
    monkeypatch.setattr(EmbeddedChartHost, 'available', staticmethod(lambda: True))
    shown, menus, heartbeats = [], [], []
    monkeypatch.setattr(panel, '_show_interactive', lambda **kw: shown.append(
        (panel._observation_id, kw['prepared_boot'])))
    listing = CandidateTabs(root, lambda code, oid: panel.show_observation(store, oid),
                            on_context=lambda *args: menus.append(args))
    listing.pack()
    for oid in ('first', 'last'):
        listing.tree.insert('', 'end', iid=oid, values=(1, '600000', '隔离样本'))
        listing.tree._obs[oid] = oid
        listing.tree._codes[oid] = 'sh.600000'
    root.update()
    # SQLite writes may temporarily exclude readers. The GUI must keep pumping
    # timers and open the context menu before that real write lock is released.
    lock = sqlite3.connect(store.path)
    lock.execute('BEGIN EXCLUSIVE')
    try:
        listing.tree.selection_set('first')
        root.after(10, lambda: heartbeats.append(True))
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert heartbeats and not shown
        listing.tree.identify_row = lambda y: 'last'
        listing._context(SimpleNamespace(y=1))
        root.update()
        assert menus and panel._observation_id == 'last'
        lock.rollback()
        deadline = time.monotonic() + 10
        while not shown and time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert [oid for oid, _ in shown] == ['last']
        assert shown[0][1]['symbol'] == 'sh.600000'
        assert panel._observation_poll is None
    finally:
        lock.close()
        root.destroy()


def test_prepared_boot_does_not_reenter_database_on_gui_thread(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gui.chart_embed import EmbeddedChartHost
    import gui.chart_embed as embed
    from unittest.mock import Mock

    host = EmbeddedChartHost.__new__(EmbeddedChartHost)
    host.closing = False
    host.workspace = None
    host.on_layout = None
    host.token = 2
    host.control = tmp_path / 'slot.control.json'
    host._timer = 'already-polling'
    host.closed = lambda: False
    host.store = Mock()
    monkeypatch.setattr(embed, 'workspace_boot', lambda *_: pytest.fail('GUI repeated DB/plot work'))
    context, boot = {'code': 'sh.600000'}, {'identity': 'fixture', 'symbol': 'sh.600000'}
    host.show(context, prepared_boot=boot)
    assert json.loads(host.control.read_text())['boot']['identity'] == 'fixture'
    assert host.token == 3 and host._history_identity == 'fixture'
    host.closing = True
    host.show(context, prepared_boot=boot)
    assert host._queued_boot is boot and host._queued_context is context


def test_invalidating_a_busy_selection_keeps_no_late_result():
    from gui.background_status import LatestRefresh
    started, release = threading.Event(), threading.Event()
    def load(value, check):
        started.set()
        release.wait(2)
        check()
        return value
    reader = LatestRefresh(load)
    try:
        reader.submit('old')
        assert started.wait(1)
        reader.invalidate()  # Placeholder, manual watch or hidden layout tile.
        release.set()
        deadline = time.monotonic() + 2
        while reader.running and time.monotonic() < deadline:
            time.sleep(.005)
        assert not reader.running and reader.poll() is None
    finally:
        release.set()
        reader.close()


@pytest.mark.native_process
@pytest.mark.skipif(__import__('os').name != 'nt', reason='native Tk autosave')
@pytest.mark.parametrize('conflict', [False, True])
def test_layout_autosave_waits_off_thread_and_keeps_revision_contract(store, monkeypatch, conflict):
    import ttkbootstrap as ttk
    from gui.chart_panel import ChartGrid
    from workbench.workspace_layouts import new_layout
    from gui.workspace_controls import WorkspaceControls
    root = ttk.Window()
    root.title('Aplus 隔离测试：布局后台保存')
    grid = ChartGrid(root, store=store, layout_count=1)
    grid.pack()
    controls = grid.controls = WorkspaceControls(grid)
    started, release = threading.Event(), threading.Event()
    original = store.save_ui_preferences
    gui_thread = threading.get_ident()
    calls = []
    def save(*args, **kwargs):
        assert threading.get_ident() != gui_thread
        calls.append(args)
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'save_ui_preferences', save)
    beats = []
    try:
        controls._save_background()
        assert started.wait(1)
        root.after(10, lambda: beats.append(True))
        controls.current['name'] = 'latest view'
        controls._save_background()  # Coalesce while the first commit is pending.
        deadline = time.monotonic() + .15
        while time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert beats and len(calls) == 1
        assert controls.save() is False  # Explicit writes cannot race the revision.
        if conflict:
            external = {'layouts':[new_layout('other window')], 'active':'external'}
            original('chart_layouts', external, 0)
        release.set()
        deadline = time.monotonic() + 4
        while controls._autosave_pending and time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert not controls._autosave_pending
        saved = store.load_ui_preferences('chart_layouts')
        if conflict:
            assert saved['data'] == external
            assert controls.caption.get().startswith('布局未保存')
            assert len(calls) == 1
        else:
            assert controls.revision == saved['revision'] == 2
            assert saved['data']['layouts'][0]['name'] == 'latest view'
            assert len(calls) == 2 and calls[1][2] == 1
    finally:
        release.set()
        root.destroy()


@pytest.mark.parametrize('phase', ['local', 'fetch'])
def test_manual_watch_prepares_interactive_boot_without_png_on_worker(store, frame, monkeypatch, phase):
    import gui.watch_chart as watch
    from gui.background_status import LatestRefresh
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    record = store.rows('SELECT * FROM datasets WHERE id=?', (did,))[0]
    monkeypatch.setattr(watch, 'local_history', lambda *_: (frame, record))
    monkeypatch.setattr(watch, 'fetch_history', lambda *_: (frame, record))
    monkeypatch.setattr(watch, 'render_chart', lambda *_a, **_k: pytest.fail('unused PNG render'))
    thread = threading.get_ident()
    seen = []
    def load(value, check):
        seen.append(threading.get_ident())
        return watch.load_chart(store, None, 'sh.600000', 'tickflow', 'daily', value, check,
                                interactive=True, name='sample')
    reader = LatestRefresh(load)
    try:
        reader.submit(phase)
        deadline = time.monotonic() + 8
        reply = None
        while reply is None and time.monotonic() < deadline:
            reply = reader.poll(); time.sleep(.005)
        assert reply is not None and reply[3] is None
        data = reply[2]
        assert data['image'] is None and data['boot']['symbol'] == 'sh.600000'
        assert data['boot']['contextKind'] == 'watch' and seen[0] != thread
    finally:
        reader.close()


@pytest.mark.native_process
@pytest.mark.skipif(__import__('os').name != 'nt', reason='native Tk match source switching')
def test_match_source_tabs_and_shape_selection_discard_slow_previous_result(store, frame, monkeypatch):
    import ttkbootstrap as ttk
    from gui.match_list import MatchList
    from gui.chart_panel import ChartPanel
    from gui.chart_items import ChartItem
    from gui.chart_embed import EmbeddedChartHost
    import gui.observation_chart as preparation
    did = save_dataset(store, 'sh.600000', frame, 'tickflow', '前复权')
    shape = dict(dataset_id=did, start=str(frame.date.iloc[0]), end=str(frame.date.iloc[-1]), label='sample', score=100)
    item = ChartItem(code='sh.600000', name='sample', observation_id='shape-one', source='tickflow', mode='candidate', shape=shape)
    original = preparation.load_shape_chart
    started, release = threading.Event(), threading.Event()
    threads = []
    def load(*args, check):
        threads.append(threading.get_ident()); started.set()
        assert release.wait(4)
        check()
        return original(*args, check=check)
    monkeypatch.setattr(preparation, 'load_shape_chart', load)
    monkeypatch.setattr(EmbeddedChartHost, 'available', staticmethod(lambda: True))
    root = ttk.Window()
    panel = ChartPanel(root, store=store, interactive=True); panel.pack()
    shown, beats, menus = [], [], []
    monkeypatch.setattr(panel, '_show_interactive', lambda **kw: shown.append(kw['prepared_boot']))
    listing = MatchList(root, lambda code, oid: panel.show_shape(store, item) if oid else panel.show_placeholder(),
                        on_context=lambda *args: menus.append(args))
    listing.pack(); listing.build_tabs([('STRATEGY_3K', '3K')])
    listing._all_rows = [dict(kind='shape', strategy='template', observation_id='shape-one', code=item.code,
        name=item.name, label='sample', score=100, window=30, start=shape['start'], end=shape['end'],
        scan_mode='replay', mae=0, correlation=1)]
    try:
        listing.set_source('形态捕手'); root.update()
        assert started.wait(1)
        listing.set_source('量化策略')  # Clearing the old tab cancels its in-flight read.
        root.update()
        listing.set_source('形态捕手'); root.update()
        listing.tree.identify_row = lambda y: listing.tree.get_children()[0]
        listing._context(type('Event', (), {'y':1})())
        root.update()
        root.after(10, lambda: beats.append(True))
        deadline = time.monotonic() + .15
        while time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert beats and menus and not shown
        release.set()
        deadline = time.monotonic() + 8
        while not shown and time.monotonic() < deadline:
            root.update(); time.sleep(.005)
        assert len(shown) == 1 and shown[0]['contextKind'] == 'shape'
        assert shown[0]['strategyEvidence']['items'] == []
        assert all(t != threading.get_ident() for t in threads)
    finally:
        release.set(); root.destroy()
