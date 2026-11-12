"""Startup data must never turn one slow read into a blocked Tk callback."""
import threading
import time

import pytest

from workbench.store import Store


def spin(window, predicate, timeout=3):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        window.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'Background result was not delivered'


@pytest.mark.native_process
def test_main_window_watch_refresh_does_not_wait_for_slow_read(tmp_path, monkeypatch):
    from gui.main_window import AplusMainWindow
    store = Store(tmp_path/'runtime')
    window = AplusMainWindow(store=store, enable_tray=False)
    entered, release = threading.Event(), threading.Event()
    original = Store.rows
    def slow(self, sql, args=()):
        if 'FROM watchlist w' in sql:
            entered.set(); release.wait(1)
        return original(self, sql, args)
    # The read-only worker and original Store must both see the injected delay.
    from gui.startup_reads import ReadSession as ReadView
    original_view = ReadView.rows
    def slow_view(self, sql, args=()):
        if 'FROM watchlist w' in sql:
            entered.set(); release.wait(1)
        return original_view(self, sql, args)
    monkeypatch.setattr(Store, 'rows', slow)
    monkeypatch.setattr(ReadView, 'rows', slow_view)
    try:
        start = time.perf_counter(); window.watch.reload(); elapsed = time.perf_counter()-start
        assert elapsed < .3, f'Watch reload occupied Tk for {elapsed:.3f}s'
        assert entered.wait(1)
    finally:
        release.set(); window.destroy()


@pytest.mark.native_process
def test_main_window_candidate_refresh_does_not_wait_for_pipeline(tmp_path, monkeypatch):
    from gui.main_window import AplusMainWindow
    import gui.data as data
    window = AplusMainWindow(store=Store(tmp_path/'runtime'), enable_tray=False)
    spin(window,lambda:not window.toolbar._dates_pending)
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set(); release.wait(1)
        return {}
    monkeypatch.setattr(data, 'load_candidates', slow)
    window.candidates._pipeline_cache=None  # Exercise a cold pipeline, not a valid PR128 hit.
    try:
        start = time.perf_counter(); window._load_strategies(); elapsed = time.perf_counter()-start
        assert elapsed < .3, f'Candidate pipeline occupied Tk for {elapsed:.3f}s'
        assert entered.wait(1)
    finally:
        release.set(); window.destroy()


@pytest.mark.native_process
def test_watch_read_failure_keeps_existing_rows_and_reports_failure(tmp_path, monkeypatch):
    from gui.main_window import AplusMainWindow
    from gui.startup_reads import ReadSession as ReadView
    store = Store(tmp_path/'runtime'); store.add_manual_watch('600000','测试证券')
    window = AplusMainWindow(store=store, enable_tray=False)
    try:
        window.watch.reload()
        spin(window, lambda: bool(window.watch.rows()))
        before = window.watch.rows()
        original = Store.rows
        def broken(self, sql, args=()):
            if 'FROM watchlist w' in sql:
                raise OSError('injected read failure')
            return original(self, sql, args)
        original_view = ReadView.rows
        def broken_view(self, sql, args=()):
            if 'FROM watchlist w' in sql:
                raise OSError('injected read failure')
            return original_view(self, sql, args)
        monkeypatch.setattr(Store,'rows',broken);monkeypatch.setattr(ReadView,'rows',broken_view)
        window.watch.reload()
        spin(window,lambda:'失败' in window.watch.hint.get())
        assert window.watch.rows() == before
    finally:
        window.destroy()


@pytest.mark.native_process
def test_sqlite_exclusive_lock_is_visible_then_retry_recovers(tmp_path):
    import sqlite3
    import ttkbootstrap as ttk
    from gui.watch_panel import WatchPanel
    store=Store(tmp_path/'runtime');store.add_manual_watch('600000','隔离测试')
    # Exercise the real background list/SQLite/Tk pipeline in isolation. The
    # full workbench also starts a separate 3-second chart-alert timer, whose
    # modal lock warning can capture this test's deliberately exclusive lock.
    window=ttk.Window(themename='darkly')
    window.watch=WatchPanel(window,store=store,background_reads=True)
    window.watch.pack()
    db=sqlite3.connect(store.path)
    try:
        window.watch.reload();spin(window,lambda:bool(window.watch.rows()))
        before=window.watch.rows()
        db.execute('BEGIN EXCLUSIVE')
        window.watch.reload()
        ticks=[]
        window.after(100,lambda:ticks.append(True))
        spin(window,lambda:'失败' in window.watch.hint.get())
        assert ticks and window.watch.rows()==before
        assert 'locked' in window.watch.hint.get()
        db.rollback();window.watch.reload()
        spin(window,lambda:not window.watch._loading)
        assert '失败' not in window.watch.hint.get()
        assert window.watch.rows()==before
    finally:db.close();window.destroy()


@pytest.mark.native_process
def test_latest_candidate_request_wins_and_restored_layout_is_not_selected(tmp_path,monkeypatch):
    import tkinter as tk
    from gui.match_list import MatchList
    import gui.startup_reads as reads
    first=threading.Event();release=threading.Event()
    def prepare(root,context,check):
        if context[1]==('2026','11','10'):
            first.set();release.wait(1);check()
        return dict(rows={},all_rows=[],templates={},error=None,cache=None,
                    source='tickflow' if context[1][-1]=='11' else 'baostock')
    monkeypatch.setattr(reads,'match_rows',prepare)
    window=tk.Tk();panel=MatchList(window,lambda *_:None);panel._background_reads=True
    store=Store(tmp_path/'runtime')
    try:
        panel.load_from_store(store,asof_filter=('2026','11','10'))
        assert first.wait(1)
        panel.load_from_store(store,asof_filter=('2026','11','11'))
        release.set();spin(window,lambda:not panel._loading)
        assert panel._source=='tickflow'
        # A first automatic selection must not overwrite a saved chart layout.
        panel.on_rows_changed=lambda _:False
        panel._choose(None)
        assert panel.consume_refresh() is True
    finally:release.set();window.destroy()


@pytest.mark.native_process
def test_date_read_failure_is_not_infinite_loading_and_click_retries(tmp_path,monkeypatch):
    from gui.main_window import AplusMainWindow
    import gui.startup_reads as reads
    def broken(*args,**kwargs):raise OSError('injected dates failure')
    monkeypatch.setattr(reads,'date_options',broken)
    window=AplusMainWindow(store=Store(tmp_path/'runtime'),enable_tray=False)
    try:
        spin(window,lambda:bool(window.toolbar._dates_error))
        assert '日期读取失败' in window.candidates.summary.get()
        monkeypatch.setattr(window.toolbar._dates_reader,'load',lambda *a,**k:dict(dates=[],latest=None))
        window.toolbar._retry_dates()
        spin(window,lambda:not window.toolbar._dates_pending)
        assert window.toolbar._dates_error is None
        assert window.toolbar._dates_retry_bind is None
    finally:window.destroy()


@pytest.mark.native_process
def test_pending_date_inventory_preserves_new_scan_day_and_plain_refresh_is_silent(tmp_path,monkeypatch):
    from unittest.mock import Mock
    from gui.main_window import AplusMainWindow
    window=AplusMainWindow(store=Store(tmp_path/'runtime'),enable_tray=False)
    try:
        spin(window,lambda:not window.toolbar._dates_pending)
        toolbar=window.toolbar;notify=Mock();toolbar.on_dates_ready=notify
        monkeypatch.setattr(toolbar._dates_reader,'load',lambda *a,**k:dict(
            dates=['2026-11-11','2026-11-10'],latest='2026-11-11'))
        toolbar._load_date_options()
        spin(window,lambda:not toolbar._dates_pending)
        notify.assert_not_called()  # Metadata refresh must not replace a user's chart.
        toolbar._load_date_options()
        assert toolbar._select_date('2026-11-11') is True
        assert toolbar.selected_date()==('2026','11','11')
        spin(window,lambda:not toolbar._dates_pending)
        assert toolbar.selected_date()==('2026','11','11')
        notify.assert_called_once()
        notify.reset_mock()
        toolbar._load_date_options()
        assert toolbar._select_date('2026-11-12') is True
        spin(window,lambda:not toolbar._dates_pending)
        assert toolbar._dates_error and '2026-11-12' in toolbar._dates_error
        assert toolbar.selected_date()==('2026','11','11')
        notify.assert_not_called()
    finally:window.destroy()
