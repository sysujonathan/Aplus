"""Opt-out startup timings; no database, market rows or callback locals in logs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

def _process_origin():
    if os.name == 'nt':
        import ctypes
        created, exited, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(-1), ctypes.byref(created),
                ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            age=time.time()-(created.value/10_000_000-11644473600)
            return time.perf_counter()-max(0,age)
    return time.perf_counter()


_origin = _process_origin()
_lock = threading.Lock()
_events = []
_listeners = set()
early_watchdog = None


def io_counters():
    if os.name != 'nt':
        return {}
    import ctypes
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    value = Counters()
    if ctypes.windll.kernel32.GetProcessIoCounters(ctypes.c_void_p(-1),
                                                  ctypes.byref(value)):
        return {name: getattr(value, name) for name, _ in value._fields_}
    return {}


def record(stage, *, duration=None, cpu=None, **details):
    event = dict(stage=stage, elapsed_ms=round((time.perf_counter()-_origin)*1000, 3),
                 thread=threading.current_thread().name, **details)
    if duration is not None:
        event['duration_ms'] = round(duration*1000, 3)
    if cpu is not None:
        event['thread_cpu_ms'] = round(cpu*1000, 3)
    with _lock:
        listeners=tuple(_listeners)
    # Subscribers only enqueue immutable diagnostics; they never call Tk here.
    for listener in listeners:
        listener(event)
    if os.environ.get('APLUS_STARTUP_TRACE', '1') == '0':
        return
    with _lock:
        _events.append(event)
        # One startup, bounded memory even if a busy consumer accidentally repeats.
        if len(_events) > 500:
            del _events[:100]
        try:
            path = Path(os.environ.get('APLUS_STARTUP_TRACE_PATH') or
                        Path(os.environ.get('TEMP', '.'))/'aplus_startup.jsonl')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False)+'\n')
        except OSError:
            pass  # Diagnostics never grant or deny data access.


@contextmanager
def span(stage):
    start, cpu, before = time.perf_counter(), time.thread_time(), io_counters()
    record(stage, state='started')
    try:
        yield
    except BaseException:
        record(stage, state='failed', duration=time.perf_counter()-start,
               cpu=time.thread_time()-cpu)
        raise
    else:
        after = io_counters()
        record(stage, state='completed', duration=time.perf_counter()-start,
               cpu=time.thread_time()-cpu,
               io={k: after.get(k, 0)-v for k, v in before.items()})


def events():
    with _lock:
        return list(_events)


def subscribe(listener):
    with _lock:_listeners.add(listener)
    def unsubscribe():
        with _lock:_listeners.discard(listener)
    return unsubscribe
