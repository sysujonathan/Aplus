"""Run the production PA page in the existing desktop WebView environment."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

from .chart_embed import atomic_json, read_notification

ASSETS = Path(__file__).resolve().parent / 'pa_growth'


class GrowthCloseGuard:
    WAIT_SECONDS = 10.0
    POLL_SECONDS = .05
    BUSY_SCRIPT = "typeof window.aplusGrowthBusy === 'function' && window.aplusGrowthBusy()"
    STATE_SCRIPT = """(() => {
        const busy = typeof window.aplusGrowthBusy === 'function' && window.aplusGrowthBusy();
        return {busy, safe: !busy && (typeof window.aplusGrowthCanClose !== 'function' || window.aplusGrowthCanClose())};
    })()"""

    def __init__(self, window, bridge, status):
        self.window, self.bridge, self.status = window, bridge, status
        self.allowed = False
        self.running = False
        self._lock = threading.Lock()

    def request(self):
        with self._lock:
            if self.allowed:
                return True
            if not self.running:
                self.running = True
                threading.Thread(target=self._finish, daemon=True).start()
        return False

    def _finish(self):
        try:
            deadline = time.monotonic() + self.WAIT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('图片或保存操作仍在处理中，请稍后再关闭')
                # A committed Save may still await its JS promise callback.
                # Never offer to discard it, and let queued API calls acquire
                # the bridge lock while the page acknowledges their results.
                if self.window.evaluate_js(self.BUSY_SCRIPT):
                    time.sleep(min(self.POLL_SECONDS, remaining))
                    continue
                if not self.bridge._lock.acquire(timeout=remaining):
                    raise TimeoutError('保存尚未完成，请稍后再关闭')
                try:
                    # Recheck both flags in one JS turn after acquiring the
                    # lock; a new queued Save/staging call must finish first.
                    state = self.window.evaluate_js(self.STATE_SCRIPT)
                    if not isinstance(state, dict):
                        raise RuntimeError('页面尚未确认关闭状态，请稍后重试')
                    if state.get('busy'):
                        continue
                    if not state.get('safe') and not self.window.create_confirmation_dialog(
                        '尚有未保存内容', '图片或文字尚未保存。确定离开并放弃这些修改？'):
                        atomic_json(self.status, {'ready': True, 'closeCancelled': True, 'id': time.time_ns()})
                        return
                    self.bridge._closing = True
                    self.allowed = True
                    break
                finally:
                    self.bridge._lock.release()
            self.window.destroy()
        except Exception as exc:
            self.allowed = False
            self.bridge._closing = False
            atomic_json(self.status, {'ready': True, 'closeCancelled': True,
                                     'error': '关闭确认失败：' + str(exc), 'id': time.time_ns()})
        finally:
            self.running = False


def run_session(session_path):
    session = Path(session_path).resolve()
    status = session.with_suffix('.status.json')
    payload = json.loads(session.read_text(encoding='utf-8'))
    try:
        parent = payload.get('parent_hwnd')
        if parent:
            from .chart_embed import adopt_parent_dpi
            adopt_parent_dpi(parent)
        import webview
        from workbench.store import Store
        from .growth_bridge import GrowthBridge
        bridge = GrowthBridge(Store(payload['runtime_root']), payload.get('context'))
        window = webview.create_window('Aplus · PA 成长空间', (ASSETS / 'index.html').as_uri(),
            js_api=bridge, width=1100, height=800, min_size=(700, 500),
            frameless=bool(parent), hidden=bool(parent), easy_drag=False, background_color='#101923')
        bridge._window = window
        bridge._return_chart = lambda: atomic_json(session.with_suffix('.actions.json'),
                                                  {'return_chart': True, 'id': time.time_ns()})
        guard = GrowthCloseGuard(window, bridge, status)
        window.events.closing += guard.request

        def transport():
            try:
                if not window.events.loaded.wait(30):
                    raise TimeoutError('成长空间加载超时，请重试')
                native = None
                if parent:
                    from .chart_embed import NativeChild
                    native = NativeChild(int(window.native.Handle.ToInt64()), parent)
                    window.show()
                atomic_json(status, {'ready': True})
                last = None
                while not guard.allowed:
                    if native and not native.fit():
                        guard.request()
                        return
                    request = read_notification(session.with_suffix('.control.json'))
                    if request and request.get('id') != last:
                        last = request.get('id')
                        if request.get('close'):
                            guard.request()
                    time.sleep(.12)
            except Exception as exc:
                atomic_json(status, {'ready': False, 'error': str(exc)})
                # A failed resize/transport must still accept a normal close;
                # no force-kill may discard staged images or an in-flight Save.
                last = None
                while not guard.allowed:
                    request = read_notification(session.with_suffix('.control.json'))
                    if request and request.get('id') != last:
                        last = request.get('id')
                        if request.get('close'):
                            guard.request()
                    time.sleep(.2)

        webview.start(transport, gui='edgechromium', debug=False, private_mode=True,
                      storage_path=str(session.with_suffix('.webview')))
    except Exception as exc:
        atomic_json(status, {'ready': False, 'error': str(exc)})
        raise


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit('usage: python -m gui.growth_shell <session.json>')
    run_session(args[0])


if __name__ == '__main__':
    main()
