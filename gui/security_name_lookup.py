"""Nonblocking form lookup. Late results cannot rename a different security."""
import queue
import threading
import time

from workbench.market import code_of
from workbench.security_names import fetch_security_name


class SecurityNameLookup:
    def __init__(self, parent, code, name, names, status, on_resolved=None, fetch=None):
        self.parent, self.code, self.name, self.names, self.status = parent, code, name, names, status
        self.fetch, self.on_resolved = fetch or fetch_security_name, on_resolved
        self.results = queue.Queue()
        self.wanted = self.owned_name = None
        self.busy = self.closed = False
        self.timer = None
        self.failed = {}
        self.trace = code.trace_add('write', self.changed)
        parent.bind('<Destroy>', self.close, add='+')

    def changed(self, *_):
        if self.closed:
            return
        try:
            code = code_of(self.code.get())
        except ValueError:
            code = None
        if code != self.wanted:
            if self.owned_name and self.name.get() == self.owned_name:
                self.name.set('')
            self.owned_name, self.wanted = None, code
            self.status.set('名称')
        if code:
            self.request(code)

    def request(self, code):
        if self.closed:
            return
        code = code_of(code)
        if code != self.wanted:
            self.changed()
            return
        # A supplied/manual name belongs to the user. Never overwrite it.
        if self.name.get().strip() and self.name.get() != self.owned_name:
            return
        if self.names.get(code):
            self.owned_name = self.names[code]
            self.name.set(self.owned_name)
            self.status.set('名称')
        elif not self.busy and time.monotonic() - self.failed.get(code, -1000) >= 30:
            self.busy = True
            self.status.set('名称（查询中…）')
            results, fetch = self.results, self.fetch
            def work():
                # Do not retain Tk widgets/variables on a worker after close.
                try: results.put((code, fetch(code), None))
                except Exception as exc: results.put((code, None, str(exc) or type(exc).__name__))
            threading.Thread(target=work, daemon=True, name='security-name').start()
            if self.timer is None:
                self.timer = self.parent.after(100, self.poll)

    def poll(self):
        self.timer = None
        if self.closed:
            return
        try:
            code, name, error = self.results.get_nowait()
        except queue.Empty:
            self.timer = self.parent.after(100, self.poll)
            return
        self.busy = False
        if error:
            self.failed[code] = time.monotonic()
        elif name:
            self.names[code] = name
            if self.on_resolved:
                self.on_resolved(code, name)
        if code == self.wanted:
            if name and (not self.name.get().strip() or self.name.get() == self.owned_name):
                self.owned_name = name
                self.name.set(name)
            self.status.set('名称' if name else '名称（可手动填写）')
        elif self.wanted:
            self.request(self.wanted)

    def close(self, event=None):
        if event is not None and event.widget is not self.parent:
            return
        if self.closed:
            return
        self.closed = True
        self.code.trace_remove('write', self.trace)
        if self.timer is not None:
            self.parent.after_cancel(self.timer)
            self.timer = None
