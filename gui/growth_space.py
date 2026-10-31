"""Lazy native PA page and non-modal chart comparison, without chart reloads."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
import tkinter as tk
import uuid

import ttkbootstrap as ttk

from .chart_embed import atomic_json, read_notification
from .theme import APP_BG, MUTED


class GrowthHost:
    def __init__(self, parent, store, context=None, on_error=None, on_closed=None, on_return=None,
                 on_compare=None):
        self.parent, self.store = parent, store
        self.on_error, self.on_closed = on_error, on_closed
        self.on_return = on_return
        self.on_compare = on_compare
        self.process = None
        self.closing = False
        self.session = store.root / 'pa_sessions' / (uuid.uuid4().hex + '.json')
        self.status = self.session.with_suffix('.status.json')
        self.control = self.session.with_suffix('.control.json')
        self.actions = self.session.with_suffix('.actions.json')
        self._last_action = None
        self._started = time.monotonic()
        self._timer = None
        self._error = None
        self._last_cancel = None
        self._restart_after_close = False
        parent.update_idletasks()
        atomic_json(self.session, dict(runtime_root=str(store.root),
                    parent_hwnd=int(parent.winfo_id()) if os.name == 'nt' else None, context=context or {}))
        from workbench.process_entry import growth_command
        self.process = subprocess.Popen(growth_command(self.session),
            cwd=Path(__file__).resolve().parents[1], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._timer = parent.after(150, self._poll)

    def _poll(self):
        self._timer = None
        if not self.parent.winfo_exists():
            return
        result = read_notification(self.status) or {}
        action = read_notification(self.actions) or {}
        if action.get('id') and action['id'] != self._last_action:
            self._last_action = action['id']
            if action.get('return_chart') and self.on_return:
                self.on_return()
            if action.get('compare_case') and self.on_compare:
                self.on_compare(action['compare_case'])
        if result.get('closeCancelled') and result.get('id') != self._last_cancel:
            self._last_cancel = result.get('id')
            self.closing = False
            self._restart_after_close = False
        if result.get('ready'):
            self._started = None
        error = result.get('error')
        if self.closed():
            if self.closing:
                self.cleanup()
                restart = self._restart_after_close
                self._restart_after_close = False
                if restart and self.on_restart:
                    self.on_restart()
                    return
                if self.on_closed:
                    self.on_closed()
                return
            error = error or '成长空间已关闭，已保存的案例仍在。'
        elif self._started and time.monotonic() - self._started > 40:
            error = '成长空间启动较慢，请稍后重试。'
        if error and error != self._error:
            self._error = error
            if self.on_error:
                self.on_error(error)
        if not self.closed():
            self._timer = self.parent.after(200, self._poll)

    def request_close(self):
        self.closing = True
        if self.closed():
            self.cleanup()
            if self.on_closed:
                self.on_closed()
        else:
            atomic_json(self.control, {'close': True, 'id': uuid.uuid4().hex})

    def closed(self):
        return self.process is None or self.process.poll() is not None

    def cleanup(self):
        if not self.closed():
            return
        if self._timer:
            try:
                self.parent.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        for path in (self.session, self.status, self.control, self.actions):
            path.unlink(missing_ok=True)


class GrowthSpacePage(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store, self.host = store, None
        self._status = tk.StringVar(value='正在打开 PA 成长空间…')
        ttk.Label(self, textvariable=self._status, foreground=MUTED).pack(pady=40)
        self._retry = ttk.Button(self, text='重新打开', command=self.retry)
        self.canvas = tk.Frame(self, background=APP_BG)
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

    def start(self):
        if self.host and not self.host.closed():
            return
        if self.host:
            self.host.cleanup()
        try:
            self.host = GrowthHost(self.canvas, self.store, on_error=self._failed)
            self.canvas.lift()
        except Exception as exc:
            self._failed(str(exc))

    def retry(self):
        if self.host and not self.host.closed():
            self.host._restart_after_close = True
            self.host.on_restart = self.start
            self.host.request_close()
        else:
            self.start()

    def _failed(self, error):
        self.canvas.lower()
        self._status.set(error)
        self._retry.pack(pady=10)


def open_chart_cases(owner, store, item):
    """A sibling window never replaces or serializes the active market chart."""
    window = tk.Toplevel(owner)
    window.title('历史案例对照')
    window.configure(background=APP_BG)
    width = min(1000, max(760, owner.winfo_screenwidth() // 2))
    window.geometry(f'{width}x800+{max(0, owner.winfo_screenwidth()-width-20)}+80')
    window.minsize(700, 500)
    canvas = tk.Frame(window, background=APP_BG)
    canvas.pack(fill=tk.BOTH, expand=True)
    def error(text):
        # Do not cover an existing WebView or discard staged content.
        window.title('历史案例对照 · ' + text)
    context = dict(symbol=item.code, name=item.name, timeframe=item.timeframe or '', comparison=True)
    def return_chart():
        owner._switch_section('premarket')
        owner.lift()
        owner.focus_force()
    def compare_case(_case_id):
        owner._switch_section('premarket')
        owner.lift()
        window.lift()
        window.focus_force()
    host = GrowthHost(canvas, store, context, on_error=error, on_closed=window.destroy,
                      on_return=return_chart, on_compare=compare_case)
    window.protocol('WM_DELETE_WINDOW', host.request_close)
    return host
