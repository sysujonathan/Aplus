"""Task-scoped idle-sleep request; never changes the user's power plan."""
from contextlib import contextmanager
import os


def execution_state(flags):
    import ctypes
    call = ctypes.windll.kernel32.SetThreadExecutionState
    call.argtypes = [ctypes.c_uint]
    call.restype = ctypes.c_uint
    if not call(flags):
        raise OSError('Windows 未接受任务防休眠请求')


@contextmanager
def active_task(store, job, kind):
    requested = False
    if os.name == 'nt' and kind in ('sync', 'scan', 'premarket'):
        try:
            execution_state(0x80000001)  # CONTINUOUS | SYSTEM_REQUIRED; display may sleep.
            requested = True
            store.event(job, '任务防空闲休眠', requested=True)
        except (AttributeError, OSError) as exc:
            store.event(job, '任务防空闲休眠', requested=False, error=str(exc))
    try:
        yield
    finally:
        if requested:
            try:
                execution_state(0x80000000)
                store.event(job, '释放任务防休眠')
            except (AttributeError, OSError) as exc:
                store.event(job, '释放任务防休眠失败', error=str(exc))
