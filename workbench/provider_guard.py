"""Durable cooldown, typed diagnostics and an OS-owned BaoStock connection lease."""
import json
import os
import time
from pathlib import Path


class ProviderError(RuntimeError):
    def __init__(self, source, operation, message, code=None, elapsed=None):
        self.source, self.operation, self.code, self.elapsed = source, operation, code, elapsed
        self.message=message
        super().__init__(f'{source} · {operation}' + (f' · 错误码 {code}' if code else '') + ': ' + message)

    def detail(self):
        return dict(source=self.source, operation=self.operation, error_code=self.code,
                    elapsed_seconds=self.elapsed, message=self.message, error=str(self),
                    retry_after=getattr(self,'retry_after',None))


def error_detail(exc, source, operation='sync'):
    return exc.detail() if isinstance(exc, ProviderError) else dict(source=source,operation=operation,error=str(exc))


def restricted(exc):
    return getattr(exc, 'code', None) == '10001011' or any(w in str(exc).lower() for w in ('黑名单','blacklist'))


class ProviderGuard:
    def __init__(self, store, source, clock=time.time):
        self.store, self.source, self.clock = store, source, clock
        self.key = 'provider_guard:' + source

    def state(self):
        rows = self.store.rows('SELECT value FROM meta WHERE key=?', (self.key,))
        return json.loads(rows[0]['value']) if rows else {}

    def check(self):
        state = self.state()
        remaining = state.get('until', 0) - self.clock()
        if remaining > 0:
            raise ValueError(f'{self.source} 暂停请求，还需约 {int(remaining)+1} 秒；{state.get("reason", "网络失败冷却")}。可切换其他数据源，不会反复重试')

    def failure(self, exc):
        # Cached/skipped stocks must not reset this rolling network failure window.
        state, stamp = self.state(), self.clock()
        failures = [t for t in state.get('failures', []) if stamp-t < 300] + [stamp]
        ban = restricted(exc)
        duration = 6*3600 if ban else min(30 * 2**min(len(failures)-1, 4), 600)
        if getattr(exc,'code',None)=='429':
            duration=max(duration,60)
        duration=max(duration,getattr(exc,'retry_after',None) or 0)
        state.update(failures=failures[-20:], until=max(state.get('until',0), stamp+duration),
                     reason='供应商限制访问（实际解封时间以供应商为准）' if ban else str(exc),
                     last_error=error_detail(exc,self.source), updated=stamp)
        self.store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (self.key,json.dumps(state,ensure_ascii=False)))
        return state


class ConnectionLease:
    """Released by the OS on exit/crash; no stale pid file can permanently block."""
    def __init__(self, name='baostock', root=None):
        base = root or Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'Aplus/provider-locks'
        self.path = Path(base)/(name+'.lock')
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b'0'); self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close(); self.file=None
            raise ProviderError(self.path.stem,'connect','本机另一个 Aplus 已占用行情连接；请等它结束再试','LOCAL_BUSY') from exc
        return self

    def release(self):
        if self.file is not None:
            self.file.close(); self.file=None


def baostock_connection_active():
    """Detect uncooperative local clients (e.g. BPA) before opening another socket."""
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes
    size = wintypes.DWORD(0)
    api = ctypes.windll.iphlpapi.GetExtendedTcpTable
    api(None,ctypes.byref(size),False,2,5,0)  # IPv4, TCP_TABLE_OWNER_PID_ALL
    buffer = ctypes.create_string_buffer(size.value)
    if api(buffer,ctypes.byref(size),False,2,5,0) != 0:
        raise ProviderError('baostock','connect','无法核对本机已有行情连接，未新建连接','LOCAL_CHECK')
    import struct
    count = struct.unpack_from('I',buffer.raw)[0]
    for i in range(count):
        state,la,lp,ra,rp,pid = struct.unpack_from('6I',buffer.raw,4+i*24)
        port = ((rp & 255) << 8) | ((rp >> 8) & 255)
        if state in (3,4,5) and port == 10030:
            return True
    return False
