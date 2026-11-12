"""Destroyed loading interpreters must never be finalized by a data worker."""
import gc
import os
from pathlib import Path
import subprocess
import sys
import threading
import weakref

import pytest


@pytest.mark.native_process
@pytest.mark.parametrize('automatic_gc', [True, False])
@pytest.mark.parametrize('outcome', ['ready', 'retry', 'close-error'])
def test_repeated_loaders_release_tcl_before_worker_collection(monkeypatch, automatic_gc, outcome):
    from gui import startup_loading as loading
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    references = []
    released_on = []
    owner_thread = threading.get_ident()
    original = loading.LoadingPanel.__init__
    def observe(panel, master):
        original(panel, master)
        def released(_):
            released_on.append(threading.get_ident())
        references.extend([weakref.ref(panel, released), weakref.ref(master, released)])
    monkeypatch.setattr(loading.LoadingPanel, '__init__', observe)
    original_status = loading.LoadingPanel.status
    def status(panel, text):
        original_status(panel, text)
        if '失败' in text:
            def act():
                label = '重试' if outcome == 'retry' else '关闭'
                next(w for w in panel.winfo_children()
                     if w.winfo_class() == 'Button' and w.cget('text') == label).invoke()
            panel.after(30, act)
    monkeypatch.setattr(loading.LoadingPanel, 'status', status)
    initially_enabled = gc.isenabled()
    try:
        (gc.enable if automatic_gc else gc.disable)()
        for phase in ('installed-preflight', 'workbench', 'reopen'):
            attempts = []
            def factory():
                attempts.append(1)
                if outcome != 'ready' and len(attempts) == 1:
                    raise OSError('injected startup read failure')
                return phase
            expected = None if outcome == 'close-error' else phase
            assert loading.initialize(factory, handoff=False) == expected
            assert gc.isenabled() is automatic_gc
            assert all(reference() is None for reference in references)
            assert set(released_on) == {owner_thread}
            # This deterministically crashes the unfixed loader with
            # Tcl_AsyncDelete / 0x80000003, even though the window was destroyed.
            collector = threading.Thread(target=gc.collect, name='test-data-worker-gc')
            collector.start()
            collector.join(timeout=5)
            assert not collector.is_alive()
            assert all(reference() is None for reference in references)
    finally:
        (gc.enable if initially_enabled else gc.disable)()


@pytest.mark.skipif(os.name != 'nt', reason='Windows Tk/WebView startup lifecycle')
@pytest.mark.parametrize('entry', ['plain-loader', 'normal', 'installed-update'])
def test_real_desktop_after_loader_survives_background_gc(tmp_path, entry):
    import json
    probe = Path(__file__).with_name('startup_tcl_cleanup_probe.py')
    process = subprocess.run([sys.executable, '-X', 'faulthandler', str(probe),
                              str(tmp_path), entry], cwd=probe.parents[1],
                             capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=75)
    assert process.returncode == 0, process.stdout + process.stderr
    if entry == 'plain-loader':
        assert 'Plain Tk interpreters released safely' in process.stdout
        return
    evidence = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert evidence['ok'] and evidence['chart_ready']
    assert evidence['collections'] >= 20 and evidence['heartbeats'] >= 10
    assert evidence['startup_ack'] is (entry == 'installed-update')
