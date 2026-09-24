"""At most two isolated provider processes; one live request per connection."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .sync import sync_stock


class CancelToken:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(e.is_set() for e in self.events)

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while not self.is_set():
            remaining = until - time.monotonic()
            if remaining <= 0:
                return False
            self.events[0].wait(min(.1, remaining))
        return True


def sync_results(store, factory, codes, start, end, job, force, cancel, halt, workers, on_wait):
    """Bound submission as well as concurrency; never queue the entire market.

    BaoStock itself remains inside each adapter's killable subprocess. Threads
    only orchestrate isolated adapters, never share the vendor global session.
    """
    local = threading.local()
    connections = []
    lock = threading.Lock()
    token = CancelToken(cancel, halt)

    def one(code):
        def provider():
            if not hasattr(local, 'connection'):
                candidate = factory()
                candidate.cancel_event = token
                # Track before login so even a failed handshake is cleaned up.
                with lock:
                    connections.append(candidate)
                local.connection = candidate.__enter__()
            local.connection.on_wait = lambda op, seconds: on_wait(code, op, seconds)
            return local.connection
        if token.is_set():
            return code, None, None, InterruptedError('已停止')
        try:
            did, outcome = sync_stock(store, provider, code, start, end, job, force)
            return code, did, outcome, None
        except Exception as exc:
            return code, None, None, exc

    pool = ThreadPoolExecutor(max_workers=max(1, min(2, workers)), thread_name_prefix='market-sync')
    pending = set()
    items = iter(codes)
    try:
        for _ in range(max(1, min(2, workers))):
            code = next(items, None)
            if code is not None:
                pending.add(pool.submit(one, code))
        while pending:
            done, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
                if not token.is_set():
                    code = next(items, None)
                    if code is not None:
                        pending.add(pool.submit(one, code))
    finally:
        # Unwinding a consumer also kills outstanding network waits, not just futures.
        if pending:
            halt.set()
        pool.shutdown(wait=True)
        for connection in connections:
            connection.__exit__(None, None, None)
