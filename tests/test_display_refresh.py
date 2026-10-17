import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gui.background_status import LatestRefresh, ReadView, load_status
from tests.test_workbench import store, frame


def await_reply(reader):
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        result = reader.poll()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError('显示后台未返回')


def test_latest_refresh_serializes_and_discards_superseded_work():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def load(value, check):
        calls.append(value)
        if value == 'first':
            entered.set()
            assert release.wait(2)
        check()
        return value
    reader = LatestRefresh(load)
    try:
        reader.submit('first'); assert entered.wait(1)
        reader.submit('second'); reader.submit('last'); release.set()
        token, value, result, error = await_reply(reader)
        assert value == result == 'last' and error is None
        assert calls == ['first', 'last'] and reader.poll() is None
    finally:
        release.set(); reader.close()


def test_latest_refresh_reports_errors_and_can_retry():
    def load(value, check):
        if not value: raise ValueError('读取失败')
        return value
    reader = LatestRefresh(load)
    try:
        reader.submit(False)
        assert isinstance(await_reply(reader)[3], ValueError)
        reader.submit('retry'); assert await_reply(reader)[2] == 'retry'
    finally: reader.close()


def test_close_cancels_active_read_without_waiting_or_delivering_a_callback():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def load(value, check):
        entered.set(); release.wait(2); finished.set(); check()
    reader = LatestRefresh(load)
    reader.submit('read'); assert entered.wait(1)
    reader.close()
    assert not release.is_set() and not finished.is_set()
    release.set(); assert finished.wait(1)
    assert reader.submit('ignored') is None and reader.poll() is None


def test_display_view_pins_source_and_cannot_write_or_initialize_database(store):
    before = store.path.read_bytes()
    view = ReadView(store.root, 'tickflow')
    assert view.rows("SELECT value FROM meta WHERE key='daily_market_source'") == [{'value':'tickflow'}]
    with pytest.raises(sqlite3.OperationalError):
        view.rows("UPDATE meta SET value='changed' WHERE key='schema_version'")
    assert store.path.read_bytes() == before
    missing = ReadView(store.root/'missing', 'tickflow')
    with pytest.raises(sqlite3.OperationalError): missing.rows('SELECT * FROM meta')
    assert not missing.root.exists()


def test_background_status_does_not_change_records_or_certify_missing_scope(store):
    before = store.path.read_bytes()
    context = ('tickflow', ('沪深主板',), '2026-10-14', 'daily')
    result = load_status(store.root, context, lambda: None)
    assert result['audit']['source'] == 'tickflow'
    assert not result['audit']['scan_allowed'] and result['audit']['ready'] == 0
    assert store.path.read_bytes() == before


def test_display_candles_do_not_write_read_events_but_still_check_snapshot_hash(store, frame):
    from gui.background_status import display_store
    from workbench.market import save_dataset, load_dataset
    did = save_dataset(store, 'sh.600000', frame, 'baostock', '前复权')
    before = store.path.read_bytes()
    view = display_store(store)
    data, record = load_dataset(view, did)
    assert not data.empty and store.path.read_bytes() == before
    path = store.root/record['path']
    path.write_bytes(path.read_bytes()+b'changed')  # Isolated test only.
    with pytest.raises(ValueError, match='校验不一致'):
        load_dataset(view, did)
    with pytest.raises(ValueError, match='越过'):
        view.read_artifact('../outside')


def test_repair_explanation_names_remaining_vendor_and_keeps_cooldowns_independent(store):
    from gui.toolbar import ToolBar
    from workbench.provider_guard import ProviderGuard, ProviderError
    from workbench.sources import set_market_source
    set_market_source(store, 'tickflow')
    bar = SimpleNamespace(store=store, _source_pending=False, _job_id='parent', _job_kind='一键盘前',
        _premarket_progress={'baostock':{'phase':'更新'}, 'tickflow':{'phase':'部分完成'}})
    reason = ToolBar._repair_block_reason(bar)
    assert 'BaoStock 更新' in reason and '全部结束' in reason
    assert 'TickFlow 部分完成' not in reason
    bar._job_id = None
    ProviderGuard(store,'tickflow').failure(ProviderError('tickflow','batch','network timeout'))
    assert 'TickFlow正在访问冷却' in ToolBar._repair_block_reason(bar)
    assert ToolBar._repair_block_reason(bar, 'verify') == ''  # Status uses BaoStock, not TickFlow.


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Tk responsiveness')
def test_source_switch_keeps_tk_alive_during_db_write_wait_and_slow_audit(tmp_path):
    result = subprocess.run([sys.executable,'-c',
        'from tests.test_display_refresh import check_native_refresh; check_native_refresh()',str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout+result.stderr


def check_native_refresh():
    import ttkbootstrap as ttk
    from gui.toolbar import ToolBar
    from workbench.store import Store, dumps
    from workbench.sources import market_source
    from tests.test_premarket_flow import multi_spec
    st = Store(Path(sys.argv[1])/'ui')
    root = ttk.Window(themename='darkly'); root.withdraw()
    entered, release = threading.Event(), threading.Event()
    calls, ticks, dates = [], [], []
    service = SimpleNamespace(cancel=Mock())
    bar = ToolBar(root, store=st, service=service, on_date_change=lambda *args: dates.append(market_source(st)))
    bar.pack(fill='x'); root.update()
    # Initial status is now asynchronous too. Finish it before isolating the
    # subsequent source-switch requests; don't count it as an extra switch.
    initial_deadline = time.monotonic()+3
    while bar._status_snapshot is None and time.monotonic()<initial_deadline:
        root.update(); time.sleep(.01)
    assert bar._status_snapshot is not None
    spec = dumps(multi_spec())
    st.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',('parent','premarket','running','now',spec))
    bar._job_id, bar._job_kind = 'parent','一键盘前'
    st.execute('UPDATE jobs SET result=? WHERE id=?', (dumps({'sources':{'baostock':{'phase':'更新'}}}),'parent'))
    bar._poll_job()
    deadline = time.monotonic()+3
    while not bar._premarket_progress and time.monotonic()<deadline:
        root.update(); time.sleep(.01)
    assert bar._premarket_progress == {'baostock':{'phase':'更新'}}
    writer = sqlite3.connect(st.path)
    writer.execute('BEGIN IMMEDIATE')
    def slow_status(_root, context, check):
        calls.append(context[0])
        if context[0] == 'tickflow':
            entered.set()
            while not release.wait(.01): check()
        check()
        return dict(market='2026-10-14',signal='2026-10-14',pending=0,
            audit=dict(source=context[0],expected=1,ready=1,gaps=[],scope_valid=True,scan_allowed=True))
    def pump(condition, timeout=3):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            root.update(); time.sleep(.005)
            if condition(): return
        raise AssertionError('界面刷新超时')
    def tick():
        ticks.append(time.monotonic()); root.after(10,tick)
    root.after(10,tick)
    try:
        # Startup already constructed the reader with its loader binding.
        # Inject the slow audit into that same reader, keeping the real queue,
        # writer wait, cancellation and Tk polling behavior under test.
        with patch('gui.background_status.load_status',side_effect=slow_status), \
             patch.object(bar._status_reader, 'load',
                          side_effect=lambda context, check: slow_status(st.root, context, check)):
            bar.source_var.set('TickFlow')
            bar._source_changed()
            assert bar._source_pending and writer.in_transaction
            pump(lambda:len(ticks)>=8)
            assert bar._source_pending and not dates
            writer.rollback(); writer.close(); writer = None
            pump(lambda:entered.is_set() and dates)
            assert dates == ['tickflow'] and '后台核对' in bar._header_data_var.get()
            before = len(ticks); pump(lambda:len(ticks)>=before+8)
            bar.btn_stop.configure(state='normal'); bar.btn_stop.invoke()
            service.cancel.assert_called_once_with('parent')
            assert bar._auto_chain_cancelled
            # A later source wins even if the old audit was slow.
            bar.source_var.set('腾讯'); bar._source_changed()
            pump(lambda: bar._status_snapshot is not None and bar._status_snapshot[0][0]=='tencent')
            assert bar._header_data_var.get().startswith('腾讯')
            assert dates == ['tickflow','tencent'] and calls == ['tickflow','tencent']
            assert st.rows("SELECT spec FROM jobs WHERE id='parent'")[0]['spec'] == spec
            assert st.rows('SELECT count(*) n FROM datasets')[0]['n'] == 0
    finally:
        release.set()
        if writer: writer.rollback(); writer.close()
        root.destroy()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native gap controls')
def test_gap_window_disables_network_actions_before_click_and_recovers_when_idle(tmp_path):
    result = subprocess.run([sys.executable,'-c',
        'from tests.test_display_refresh import check_native_blocked; check_native_blocked()',str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr


def check_native_blocked():
    import ttkbootstrap as ttk
    from gui.tickflow_integrity import show_integrity
    from tests.test_gap_review import receipt
    root = ttk.Window(themename='darkly'); root.withdraw()
    report = receipt(); report.update(periods={'daily':report['scan']}, excluded=report['scan']['gaps'],
        unknown_history=[], confirmed_history=[], request_failures=[], outside_input_history=[])
    reason = ['BaoStock还在更新；三源任务全部结束后才能补拉。']
    calls = []
    window = show_integrity(root,report,lambda *args:calls.append(args),
                            repair_blocked=lambda action:reason[0])
    def descendants(w):
        for child in w.winfo_children():
            yield child; yield from descendants(child)
    try:
        root.update()
        tree = next(w for w in descendants(window) if isinstance(w,ttk.Treeview))
        tree.selection_set('group:history'); root.update()
        repair = next(w for w in descendants(window) if isinstance(w,ttk.Button) and str(w.cget('text')).startswith('继续补拉'))
        assert repair.instate(['disabled'])
        assert any(isinstance(w,ttk.Label) and 'BaoStock还在更新' in str(w.cget('text')) for w in descendants(window))
        repair.invoke(); assert not calls
        reason[0] = ''
        end = time.monotonic()+2
        while repair.instate(['disabled']) and time.monotonic()<end:
            root.update(); time.sleep(.01)
        assert not repair.instate(['disabled'])
        # Protection is rechecked at click, not just when the button was drawn.
        reason[0] = '新任务已开始'
        with patch('gui.tickflow_integrity.messagebox.showerror') as error:
            repair.invoke(); root.update()
            assert not calls and error.call_count == 0 and window.winfo_exists()
    finally: root.destroy()
