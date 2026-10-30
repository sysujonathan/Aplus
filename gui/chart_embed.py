"""A persistent WebView child in a Tk chart slot, with acknowledged switches.

IPC files are disposable session transport. Store remains the only owner of
drawings/settings/alerts. A selection cannot replace an unsaved document.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .chart_bridge import ChartBridge, refresh_workspace_boot, workspace_boot


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.writing')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        # Windows surfaces both a reader holding the previous notification and a
        # concurrent writer holding the file being replaced as PermissionError
        # (WinError 5 or 32). A fixed 50 ms budget is not enough once several
        # sessions write at once, so back off instead of losing the refresh.
        delay = .005
        for attempt in range(12):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, .05)
    finally:
        temporary.unlink(missing_ok=True)


def read_notification(path):
    """A transient Windows sharing conflict must not end the IPC reader.

    These files are replaceable notifications, not saved analysis. Keep the
    last acknowledged page and retry on the next tick if a writer, antivirus,
    or replacement briefly makes a notification unavailable.
    """
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def adopt_parent_dpi(parent):
    """Match DPI before WinForms creates any HWND; avoid cross-process reset."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    user.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    context = user.GetWindowDpiAwarenessContext(int(parent))
    if not context:
        raise RuntimeError('无法核对主图的显示缩放')
    if not user.SetProcessDpiAwarenessContext(context):
        raise ctypes.WinError(ctypes.get_last_error())


class ChartSessionBridge:
    """The JS bridge object lives as long as the embedded browser process."""
    def __init__(self, store, boot, materialize):
        self.store, self.materialize = store, materialize
        self.current = ChartBridge(store, refresh_workspace_boot(store, boot)) if boot else None
        self.active_token = 0
        self.pending = None
        self.busy = False
        self.failed_token = None
        self.close_requested = False
        self.close_token = None
        self._lock = threading.RLock()
        self._close_window = self._cancel_close = None

    def bootstrap(self):
        if not self.current:
            return None
        boot = self.current.bootstrap()
        boot['sessionToken'] = self.active_token
        return boot

    def _check_token(self, session_token):
        if self.active_token and session_token != self.active_token:
            raise ValueError('图表页面已切换，旧页面修改未写入当前标的')

    def save_state(self, state, expected_revision=None, session_token=None):
        with self._lock:
            self._check_token(session_token)
            if self.current is None:
                raise ValueError('尚未选择真实行情')
            return self.current.save_state(state, expected_revision)

    def create_price_alert(self, value, condition='cross_up', name='', target='price',
                           timeframe=None, ema_length=20, frequency='bar_close', expires=None,
                           upper=None, lower=None, bars=1, session_token=None):
        with self._lock:
            self._check_token(session_token)
            if self.current is None:
                raise ValueError('尚未选择真实行情')
            return self.current.create_price_alert(value, condition, name, target, timeframe,
                                                    ema_length, frequency, expires,
                                                    upper, lower, bars)

    def activate_context(self):
        with self._lock:
            request = deepcopy(self.pending)
            if not request:
                raise ValueError('切换请求已失效')
            boot = request.get('boot')
            boot = refresh_workspace_boot(self.store, boot) if boot else None
            if boot:
                boot['sessionToken'] = request['token']
            self.materialize(boot)
            self.current = ChartBridge(self.store, boot) if boot else None
            self.active_token = request['token']
            self.failed_token = None
            return {'token': self.active_token}

    def update_alert(self, alert_id, changes, session_token=None):
        with self._lock:
            self._check_token(session_token)
            if self.current is None:
                raise ValueError('尚未选择真实行情')
            return self.current.update_alert(alert_id, changes)

    def delete_alert(self, alert_id, session_token=None):
        with self._lock:
            self._check_token(session_token)
            if self.current is None:
                raise ValueError('尚未选择真实行情')
            return self.current.delete_alert(alert_id)

    def read_alerts(self, session_token=None):
        with self._lock:
            self._check_token(session_token)
            return self.current.read_alerts() if self.current else {'alerts': []}

    def notify_active(self):
        callback = getattr(self, '_notify_active', None)
        if callback:
            callback(self.active_token)
        return True

    def cancel_context_switch(self, error=''):
        self.failed_token = (self.pending or {}).get('token')
        self.busy = False

    def retry_context_switch(self):
        self.failed_token = None

    def finish_close(self):
        if self._close_window:
            self._close_window()

    def copy_snapshot(self, data_url):
        if not getattr(self, '_copy_snapshot', None):
            raise RuntimeError('快照剪贴板尚未就绪')
        return self._copy_snapshot(data_url)

    def request_layout(self, count, session_token=None):
        self._check_token(session_token)
        if type(count) is not int or count not in (1, 4, 6, 9):
            raise ValueError('只支持单图、4图、6图和9图')
        callback = getattr(self, '_request_layout', None)
        if callback is None:
            raise ValueError('请在主工作区切换多图布局')
        callback(count)
        return {'ok': True}

    def cancel_close(self):
        if self._cancel_close:
            self._cancel_close()


class NativeChild:
    """Windows-supported child parenting; never enumerates unrelated windows."""
    def __init__(self, child, parent):
        self.child, self.parent = int(child), int(parent)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user.SetParent.restype = wintypes.HWND
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        if not self.user.IsWindow(self.parent):
            raise RuntimeError('主图容器已经关闭')
        style = self.user.GetWindowLongW(self.child, -16)
        style = (style & ~0x80CF0000) | 0x40000000  # remove popup/caption/frame, add WS_CHILD
        self.user.SetWindowLongW(self.child, -16, style)
        ctypes.set_last_error(0)
        old = self.user.SetParent(self.child, self.parent)
        if not old and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if self.user.GetParent(self.child) != self.parent:
            raise RuntimeError('交互图表没有嵌入主图容器')
        self.size = None
        self.fit()

    def fit(self):
        if not self.user.IsWindow(self.parent) or not self.user.IsWindow(self.child):
            return False
        rect = wintypes.RECT()
        if not self.user.GetClientRect(self.parent, ctypes.byref(rect)):
            return False
        size = (max(1, rect.right), max(1, rect.bottom))
        if size != self.size:
            if not self.user.SetWindowPos(self.child, None, 0, 0, *size, 0x0034):
                raise ctypes.WinError(ctypes.get_last_error())
            self.size = size
        return True


class EmbeddedChartHost:
    """Tk-side controller. Starting WebView never blocks Tk's first paint."""
    def __init__(self, parent, store, on_error=None, on_layout=None, on_ready=None, on_active=None):
        self.parent, self.store, self.on_error = parent, store, on_error
        self.on_layout = on_layout
        self.on_ready = on_ready
        self.on_active = on_active
        self._ready_token = None
        self.process = None
        self.token = 0
        self.session = store.root / 'chart_sessions' / f'embedded-{uuid.uuid4().hex}.json'
        self.control = self.session.with_suffix('.control.json')
        self.status = self.session.with_suffix('.status.json')
        self.actions = self.session.with_suffix('.actions.json')
        self.navigation = self.session.with_suffix('.navigation.json')
        self.live = self.session.with_suffix('.live.json')
        self.focus = self.session.with_suffix('.focus.json')
        self._last_focus = None
        self._last_action = None
        self.closing = False
        self.error = None
        self._timer = None
        self._started = None
        self._queued_context = None

    @staticmethod
    def available():
        from .chart_shell import webview_available
        return os.name == 'nt' and webview_available() and os.environ.get('APLUS_TK_CHART_FALLBACK') != '1'

    def show(self, context):
        if self.closing and not self.closed():
            self._queued_context = context
            return
        boot = workspace_boot(self.store, context) if context else None
        if boot:
            boot['embedded'] = True
            boot['layoutSupported'] = self.on_layout is not None
        self.token += 1
        request = dict(token=self.token, boot=boot)
        atomic_json(self.control, request)
        self._started = time.monotonic()
        if self.closed():
            self.closing = False
            self.error = None
            self.status.unlink(missing_ok=True)
            atomic_json(self.session, dict(runtime_root=str(self.store.root), boot=boot,
                parent_hwnd=int(self.parent.winfo_id()), token=self.token))
            from workbench.process_entry import chart_command
            self.process = subprocess.Popen(chart_command(self.session),
                cwd=Path(__file__).resolve().parents[1], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._started = time.monotonic()
            self._timer = self.parent.after(100, self._poll)
        elif self._timer is None:
            self._timer = self.parent.after(100, self._poll)

    def navigate(self, day=None):
        """A UI-only command scoped to this exact displayed symbol request."""
        if self.closed() or self.closing:
            return False
        if day is not None:
            from datetime import date
            day = date.fromisoformat(str(day)).isoformat()
        atomic_json(self.navigation, dict(id=uuid.uuid4().hex, token=self.token, date=day))
        return True

    def push_live(self, payload):
        """Hand new intraday bars to the open page without reloading it.

        The page keeps its own viewport and scroll position; the transport only
        replaces the tail series. Nothing here can write Store history.
        """
        if self.closed() or self.closing or not payload:
            return False
        atomic_json(self.live, dict(id=uuid.uuid4().hex, token=self.token, **payload))
        return True

    def _poll(self):
        self._timer = None
        if not self.parent.winfo_exists():
            return
        try:
            focus = read_notification(self.focus)
            if focus is not None:
                if focus.get('id') != self._last_focus:
                    self._last_focus = focus.get('id')
                    if focus.get('token') == self.token and self.on_active and not self.closing:
                        self.on_active()
            action = read_notification(self.actions)
            if action is not None:
                if action.get('id') != self._last_action:
                    self._last_action = action.get('id')
                    if self.on_layout and action.get('layout') in (1, 4, 6, 9) and not self.closing:
                        self.on_layout(action['layout'])
            result = read_notification(self.status)
            if result is not None:
                owns_page = result.get('token') == self.token
                if result.get('error') and (owns_page or result.get('scope') == 'process'):
                    raise RuntimeError(result['error'])
                if result.get('ready') and owns_page:
                    self._started = None
                    if result.get('token') == self.token and self._ready_token != self.token:
                        self._ready_token = self.token
                        if self.on_ready:
                            self.on_ready()
                if result.get('closeCancelled') and owns_page:
                    self.closing = False
            if self.process.poll() is not None and not self.closing:
                raise RuntimeError('交互图表进程已退出；已保存的分析对象仍在本机')
            if self._started and time.monotonic() - self._started > 30:
                raise RuntimeError('交互图表启动超时，请重开图表；未删除任何分析对象')
        except Exception as exc:
            error = str(exc)
            changed = error != self.error
            self.error = error
            if self.on_error and changed:
                self.on_error(self.error)
            if not self.closed():
                self._timer = self.parent.after(250, self._poll)
            return
        if self.closed() and self.closing and self._queued_context is not None:
            context, self._queued_context = self._queued_context, None
            self.cleanup()
            self.closing = False
            self.show(context)
            return
        if self.closed() and self.closing:
            self.cleanup()
            return
        self.error = None
        if not self.closed():
            self._timer = self.parent.after(100, self._poll)

    def request_close(self):
        self._queued_context = None
        if not self.closing:
            self.token += 1
        self.closing = True
        if self.process is not None and self.process.poll() is None:
            atomic_json(self.control, dict(token=self.token, close=True, id=uuid.uuid4().hex))

    def closed(self):
        return self.process is None or self.process.poll() is not None

    def cleanup(self):
        if self._timer:
            self.parent.after_cancel(self._timer)
            self._timer = None
        if self.closed():
            for path in (self.control, self.status, self.actions, self.navigation,
                         self.live, self.focus, self.session):
                path.unlink(missing_ok=True)


def run_embedded_transport(window, bridge, session, parent_hwnd, native=None):
    """Only this chart's native handle is used; keep a separate UI event loop."""
    native = native or NativeChild(int(window.native.Handle.ToInt64()), parent_hwnd)
    control = session.with_suffix('.control.json')
    bridge._request_layout = lambda count: atomic_json(session.with_suffix('.actions.json'),
        dict(id=uuid.uuid4().hex, layout=count))
    bridge._notify_active = lambda token: atomic_json(session.with_suffix('.focus.json'),
        dict(id=uuid.uuid4().hex, token=token))
    last_mtime = None
    last_navigation = None
    last_live = None
    while native.fit():
        try:
            modified = control.stat().st_mtime_ns
        except (FileNotFoundError, PermissionError):
            modified = None
        if modified is not None and modified != last_mtime:
            request = read_notification(control)
            if request is not None:
                last_mtime = modified
                if request.get('close'):
                    bridge.pending = None
                    bridge.close_requested = True
                    bridge.close_token = request.get('token')
                    window.destroy()  # ChartCloseGuard waits for the last Store save
                else:
                    bridge.pending = request
        pending = bridge.pending or {}
        if (pending.get('token') != bridge.active_token and pending
            and pending.get('token') != bridge.failed_token and not bridge.busy
            and window.events.loaded.is_set()):
            bridge.busy = True
            installed = window.evaluate_js(
                "typeof window.aplusSwitchContext === 'function' ? (window.aplusSwitchContext(), true) : false")
            if not installed:
                bridge.busy = False
        navigation = session.with_suffix('.navigation.json')
        if not bridge.busy and window.events.loaded.is_set():
            request = read_notification(navigation)
            if request is not None and request.get('id') != last_navigation and request.get('token') == bridge.active_token:
                accepted = window.evaluate_js(
                    "typeof window.aplusNavigateChart === 'function' && window.aplusNavigateChart("
                    + json.dumps(request, ensure_ascii=False) + ")")
                if accepted:
                    last_navigation = request['id']
        live_path = session.with_suffix('.live.json')
        if not bridge.busy and window.events.loaded.is_set():
            request = read_notification(live_path)
            if request is not None and request.get('id') != last_live:
                if request.get('token') == bridge.active_token:
                    # A page that cannot accept intraday rows keeps its current
                    # frame; the next tick retries after the page reloads.
                    accepted = window.evaluate_js(
                        "typeof window.aplusApplyLiveBars === 'function' && window.aplusApplyLiveBars("
                        + json.dumps(request, ensure_ascii=False) + ")")
                    if accepted:
                        last_live = request['id']
        time.sleep(.1)


def run_embedded_close_recovery(window, bridge, session):
    """Keep ordinary save/close available after the chart transport has failed.

    This deliberately does not resume market updates or switch pages. Destroy
    still passes through ChartCloseGuard and the page's save acknowledgement;
    no worker is terminated and no unsaved document is silently discarded.
    """
    control = session.with_suffix('.control.json')
    last_close = None
    while not window.events.closed.is_set():
        request = read_notification(control)
        if request and request.get('close'):
            identity = (request.get('token'), request.get('id'))
            if identity != last_close:
                last_close = identity
                bridge.pending = None
                bridge.close_requested = True
                bridge.close_token = request.get('token')
                window.destroy()
        time.sleep(.1)
