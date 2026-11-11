"""Real interpreter shutdown: nonblocking close does not detach pool workers."""
import json
from pathlib import Path
import subprocess
import sys
import time


CHILD = r'''
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from workbench.shape_tasks import ShapeTasks
from workbench.store import Store

store = Store(Path(sys.argv[1]) / 'isolated.sqlite')
service = SimpleNamespace(store=store, lock=threading.Lock(), cancel_flags={},
                          _code_update_reserved=False)
runner = ShapeTasks(service)
started = threading.Event()
def blocked(*args):
    started.set()
    threading.Event().wait(2.5)  # Ignores cancellation; bounded self-release.
runner._run = blocked
identifier = runner.submit([], '2026-11-10', 'test')
assert started.wait(5)
queued = runner.pool.submit(lambda: 'must not run')
begin = time.monotonic()
remaining = runner.close(wait=False)
marker = Path(sys.argv[1], 'end.tmp')
marker.write_text(json.dumps(dict(
    remaining=remaining, identifier=identifier, close_seconds=time.monotonic()-begin,
    end=time.monotonic(), queued_cancelled=queued.cancelled())))
marker.rename(Path(sys.argv[1], 'end.json'))
# End of script: concurrent.futures now joins the still-blocked worker.
'''


def test_nonblocking_close_reports_worker_but_interpreter_waits(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', CHILD, str(tmp_path)],
                             cwd=Path(__file__).resolve().parents[1],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        marker = tmp_path / 'end.json'
        deadline = time.monotonic() + 15
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), 'child did not reach end of script after close'
        report = json.loads(marker.read_text())
        assert report['close_seconds'] < 1, report
        assert report['queued_cancelled'], report
        # The script ended, but the interpreter must still wait for its worker.
        time.sleep(.3)
        assert child.poll() is None, 'running worker was silently detached'
        out, err = child.communicate(timeout=10)
        linger = time.monotonic() - report['end']
        print(f"close={report['close_seconds']:.4f}s; process linger={linger:.3f}s; "
              f"remaining={report['remaining']}")
        assert child.returncode == 0, out + err
        assert linger >= 1, linger
        assert report['remaining'] == [report['identifier']], report
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_cooperative_close_waits_and_reports_no_running_tasks(tmp_path):
    import threading
    from types import SimpleNamespace
    import pytest
    from workbench.shape_tasks import ShapeTasks
    from workbench.store import Store

    store = Store(tmp_path / 'cooperative.sqlite')
    service = SimpleNamespace(store=store, lock=threading.Lock(), cancel_flags={},
                              _code_update_reserved=False)
    runner = ShapeTasks(service)
    started = threading.Event()
    stopped = threading.Event()

    def cooperative(identifier, event, *args):
        started.set()
        if event.wait(5):
            stopped.set()

    runner._run = cooperative
    try:
        runner.submit([], '2026-11-10', 'test')
        assert started.wait(5)
        assert runner.close() == ()
        assert stopped.is_set()
        assert runner.close(wait=False) == ()
        with pytest.raises(ValueError, match='正在退出'):
            runner.submit([], '2026-11-10', 'test')
    finally:
        runner.close()


def test_window_destroy_registers_and_logs_running_shapes(monkeypatch, caplog):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from gui import main_window

    window = object.__new__(main_window.AplusMainWindow)
    window._exit_poll_id = None
    window._watchdog = None
    runner = SimpleNamespace(close=Mock(return_value=('shape:blocked',)))
    window.service = SimpleNamespace(_shape_tasks=runner)
    destroyed = Mock()
    monkeypatch.setattr(main_window.ttk.Window, 'destroy', destroyed)
    window.destroy()
    runner.close.assert_called_once_with(wait=False)
    assert window._unfinished_shape_tasks == ('shape:blocked',)
    assert 'shape:blocked' in caplog.text
    assert '进程退出须等待任务收尾' in caplog.text
    destroyed.assert_called_once()
