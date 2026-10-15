"""One opt-in transport recovery per continue-sync, after the real guard expires."""
import math
import time

from .provider_guard import ProviderError, ProviderGuard, access_limited, error_detail


MAX_WAIT = 120


def wait(token, seconds):
    return token.wait(seconds)


def request_with_recovery(service, job, request, budget, report):
    guard = ProviderGuard(service.store, 'tickflow')
    for attempt in range(2):
        service.check_stop(job)
        guard.check()
        try:
            value = request()
            if attempt:
                report['transport_recovery']['recovered'] = True
                service.store.event(job, 'TickFlow临时断网续做成功')
            return value
        except (ProviderError, TimeoutError) as exc:
            state = getattr(exc, '_provider_guard_state', None) or guard.failure(exc)
            exc._provider_guard_state = state
            transient = isinstance(exc, TimeoutError) or getattr(exc, 'transient_transport', False)
            if (not budget or budget['used'] or not transient or access_limited(exc)
                    or getattr(exc, 'code', None) is not None or attempt):
                raise
            remaining = state.get('until', 0) - time.time()
            if not math.isfinite(remaining) or remaining > MAX_WAIT:
                raise
            budget['used'] = True
            recovery = report.setdefault('transport_recovery', dict(attempts=0, recovered=False, wait_seconds=0., errors=[]))
            recovery['attempts'] += 1
            recovery['errors'].append(error_detail(exc, 'tickflow'))
            service.store.event(job, 'TickFlow临时断网等待续做', **error_detail(exc, 'tickflow'))
            deadline = time.monotonic() + MAX_WAIT
            while True:
                service.check_stop(job)
                current = guard.state()
                # Another entry can lengthen the guard or record a restriction.
                last = current.get('last_error', {})
                if (str(last.get('error_code') or '') in ('403', '429', '10001011')
                        or access_limited(RuntimeError(last.get('error', '')))):
                    exc._provider_guard_state = current
                    raise
                remaining = current.get('until', 0) - time.time()
                if remaining <= 0:
                    break
                if not math.isfinite(remaining) or remaining > deadline - time.monotonic():
                    exc._provider_guard_state = current
                    raise
                service.progress(job, report.get('processed', 0), report.get('requested', 0),
                    f'TickFlow · 临时断网，等待 {math.ceil(remaining)} 秒后自动续做（最多重试一次）；已完成行情保留')
                stamp = time.monotonic()
                cancelled = wait(service.cancel_flags[job], min(1, remaining))
                recovery['wait_seconds'] += time.monotonic() - stamp
                if cancelled:
                    service.check_stop(job)
