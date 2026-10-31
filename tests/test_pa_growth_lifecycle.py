"""Close and recovery behavior without native windows or a business database."""
import base64
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from gui.growth_bridge import GrowthBridge
from gui.growth_shell import GrowthCloseGuard
from gui.growth_space import GrowthHost, GrowthSpacePage
from workbench.store import Store


def staged_bridge(tmp_path):
    store = Store(tmp_path/'isolated')
    bridge = GrowthBridge(store)
    output = io.BytesIO()
    Image.new('RGB', (120, 80), 'navy').save(output, 'PNG')
    staged = bridge.stage_images([dict(name='test.png',
        data_url='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode())])
    assert staged['ok'], staged
    return store, bridge, staged['pending'][0]['token']


def detached_host(tmp_path, *, alive=True):
    """Only the subprocess and Tk timer are mocked; use real IPC files."""
    parent, process = Mock(), Mock()
    parent.winfo_exists.return_value = True
    parent.winfo_id.return_value = 123
    parent.after.return_value = 'timer'
    process.poll.return_value = None if alive else 1
    with patch('gui.growth_space.subprocess.Popen', return_value=process):
        host = GrowthHost(parent, SimpleNamespace(root=tmp_path))
    return host


def test_close_waits_for_durable_save_before_destroying_page(tmp_path, monkeypatch):
    store, bridge, token = staged_bridge(tmp_path)
    saving, release, committed, destroyed = (threading.Event() for _ in range(4))
    original = store.pa_save_case
    def slow_write(*args, **kwargs):
        saving.set()
        assert release.wait(5), 'test never released the simulated disk write'
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'pa_save_case', slow_write)
    outcome = []
    def save():
        outcome.append(bridge.save_capture([token], dict(data_kind='test')))
        committed.set()
    worker = threading.Thread(target=save)
    worker.start()
    assert saving.wait(5)
    window = Mock()
    evaluated = threading.Event()
    def can_close(_script):
        if _script == GrowthCloseGuard.BUSY_SCRIPT:
            return False
        evaluated.set()
        # The bridge lock is released only after the Store commit and pending
        # removal, even if the saving thread has not yet signalled its return.
        assert token not in bridge._pending
        assert len(Store(store.root).pa_search()) == 1
        return dict(busy=False, safe=True)
    window.evaluate_js.side_effect = can_close
    window.destroy.side_effect = destroyed.set
    guard = GrowthCloseGuard(window, bridge, tmp_path/'status.json')
    try:
        assert guard.request() is False
        assert not evaluated.wait(.1), 'close bypassed the active save lock'
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert committed.is_set() and outcome[0]['ok']
    assert destroyed.wait(5)
    assert guard.allowed and bridge._closing
    assert guard.request() is True


def test_close_cancellation_keeps_staged_image_and_allows_save(tmp_path):
    store, bridge, token = staged_bridge(tmp_path)
    window = Mock()
    window.evaluate_js.side_effect = lambda script: False if script == GrowthCloseGuard.BUSY_SCRIPT else dict(busy=False, safe=False)
    window.create_confirmation_dialog.return_value = False
    status = tmp_path/'close.json'
    guard = GrowthCloseGuard(window, bridge, status)
    guard._finish()
    assert json.loads(status.read_text(encoding='utf-8'))['closeCancelled'] is True
    assert not guard.allowed and not guard.running
    assert not getattr(bridge, '_closing', False)
    window.destroy.assert_not_called()
    assert token in bridge._pending
    result = bridge.save_capture([token], dict(data_kind='test'))
    assert result['ok'] and len(store.pa_search()) == 1


def test_destroy_error_preserves_page_and_can_retry_close(tmp_path):
    bridge = SimpleNamespace(_lock=threading.RLock(), _closing=False)
    window = Mock()
    window.evaluate_js.side_effect = lambda script: False if script == GrowthCloseGuard.BUSY_SCRIPT else dict(busy=False, safe=True)
    window.destroy.side_effect = RuntimeError('native close failed')
    status = tmp_path/'close.json'
    guard = GrowthCloseGuard(window, bridge, status)
    guard._finish()
    feedback = json.loads(status.read_text(encoding='utf-8'))
    assert feedback['closeCancelled'] and 'native close failed' in feedback['error']
    assert not guard.allowed and not guard.running and not bridge._closing
    window.destroy.side_effect = None
    guard._finish()
    assert guard.allowed and bridge._closing


def test_close_waits_until_js_consumes_the_committed_save_result(tmp_path):
    store, bridge, token = staged_bridge(tmp_path)
    assert bridge.save_capture([token], dict(data_kind='test'))['ok']
    acknowledged, checked, destroyed = (threading.Event() for _ in range(3))
    window = Mock()
    def evaluate(script):
        if script == GrowthCloseGuard.BUSY_SCRIPT:
            checked.set()
            return not acknowledged.is_set()
        return dict(busy=False, safe=True)
    window.evaluate_js.side_effect = evaluate
    window.destroy.side_effect = destroyed.set
    guard = GrowthCloseGuard(window, bridge, tmp_path/'close.json')
    try:
        guard.request()
        assert checked.wait(5)
        assert not destroyed.wait(.1)
        window.create_confirmation_dialog.assert_not_called()
        # Busy JS must not hold the API lock: a queued read can still finish.
        assert bridge.bootstrap()['ok']
        assert len(Store(store.root).pa_search()) == 1
    finally:
        acknowledged.set()
    assert destroyed.wait(5)
    window.create_confirmation_dialog.assert_not_called()


def test_queued_staging_finishes_before_any_discard_confirmation(tmp_path):
    _, bridge, _ = staged_bridge(tmp_path)
    # Start with no pending import. JS marks a new clipboard staging request
    # busy before its API call obtains the bridge lock.
    bridge._pending.clear()
    staging_done, busy_checked, confirmed = (threading.Event() for _ in range(3))
    window = Mock()
    def evaluate(script):
        if script == GrowthCloseGuard.BUSY_SCRIPT:
            busy_checked.set()
            return not staging_done.is_set()
        return dict(busy=False, safe=False)
    def cancel(*_args):
        assert staging_done.is_set()
        assert bridge._pending
        confirmed.set()
        return False
    window.evaluate_js.side_effect = evaluate
    window.create_confirmation_dialog.side_effect = cancel
    guard = GrowthCloseGuard(window, bridge, tmp_path/'close.json')
    guard.request()
    assert busy_checked.wait(5)
    output = io.BytesIO()
    Image.new('RGB', (120, 80), 'green').save(output, 'PNG')
    outcome = []
    def stage():
        outcome.append(bridge.stage_images([dict(name='test.png',
            data_url='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode())]))
    worker = threading.Thread(target=stage)
    worker.start()
    worker.join(timeout=5)
    try:
        assert not worker.is_alive(), 'close held the lock while the queued staging API needed it'
        assert outcome[0]['ok']
        window.create_confirmation_dialog.assert_not_called()
    finally:
        staging_done.set()
    assert confirmed.wait(5)
    window.destroy.assert_not_called()
    assert not guard.allowed and not getattr(bridge, '_closing', False)


def test_page_becoming_busy_after_first_check_is_rechecked_under_lock(tmp_path):
    bridge = SimpleNamespace(_lock=threading.RLock(), _closing=False)
    window = Mock()
    window.evaluate_js.side_effect = [False, dict(busy=True, safe=False),
                                      False, dict(busy=False, safe=True)]
    guard = GrowthCloseGuard(window, bridge, tmp_path/'close.json')
    guard._finish()
    assert guard.allowed
    window.destroy.assert_called_once_with()
    window.create_confirmation_dialog.assert_not_called()


def test_busy_timeout_keeps_page_open_without_offering_to_discard_inflight_work(tmp_path):
    bridge = SimpleNamespace(_lock=threading.RLock(), _closing=False)
    window = Mock()
    window.evaluate_js.return_value = True
    status = tmp_path/'close.json'
    guard = GrowthCloseGuard(window, bridge, status)
    guard.WAIT_SECONDS, guard.POLL_SECONDS = .06, .01
    guard._finish()
    feedback = json.loads(status.read_text(encoding='utf-8'))
    assert feedback['closeCancelled'] and '仍在处理中' in feedback['error']
    assert not guard.allowed and not guard.running and not bridge._closing
    window.destroy.assert_not_called()
    window.create_confirmation_dialog.assert_not_called()


def test_comparison_window_can_close_after_child_has_already_exited(tmp_path):
    host = detached_host(tmp_path, alive=False)
    host.on_closed = Mock()
    for path in (host.status, host.control, host.actions):
        path.write_text('{}', encoding='utf-8')
    host.request_close()
    host.on_closed.assert_called_once_with()
    host.parent.after_cancel.assert_called_once_with('timer')
    assert all(not path.exists() for path in (host.session, host.status, host.control, host.actions))
    host.process.terminate.assert_not_called()


def test_retry_waits_for_live_child_to_close_before_reopening(tmp_path):
    host = detached_host(tmp_path)
    page = SimpleNamespace(host=host, start=Mock())
    GrowthSpacePage.retry(page)
    assert host.closing and host.control.exists()
    page.start.assert_not_called()
    host.process.terminate.assert_not_called()
    host.process.poll.return_value = 0
    host._poll()
    page.start.assert_called_once_with()
    assert not host.control.exists()


def test_cancelled_retry_does_not_reopen_during_a_later_normal_exit(tmp_path):
    host = detached_host(tmp_path)
    page = SimpleNamespace(host=host, start=Mock())
    GrowthSpacePage.retry(page)
    host.status.write_text(json.dumps(dict(ready=True, closeCancelled=True, id='cancel-1')), encoding='utf-8')
    host._poll()
    assert not host.closing
    page.start.assert_not_called()
    host.request_close()
    host.process.poll.return_value = 0
    host._poll()
    page.start.assert_not_called()
