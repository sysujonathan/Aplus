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
