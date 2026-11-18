"""Bounded read-only CPU processes; original Service owns order and all writes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .process_entry import scan_command
from .store import ROOT

MIN_STOCKS = 64
WORKERS_ENV = 'APLUS_TICKFLOW_SCAN_WORKERS'
MIB = 1024**2
WORKING_SET_BUDGET = 128*MIB
PARENT_HEADROOM = 128*MIB
COMMIT_BUDGET = 384*MIB
COMMIT_HEADROOM = 512*MIB


def memory_snapshot():
    if os.name != 'nt':
        return dict(available_bytes=os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'))
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ('total', 'available', 'page_total', 'page_available',
                       'virtual_total', 'virtual_available', 'extended')]]
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError('GlobalMemoryStatusEx failed')
    return dict(available_bytes=int(memory.available), commit_available_bytes=int(memory.page_available))


def available_memory():
    return memory_snapshot()['available_bytes']


def worker_selection():
    requested = os.environ.get(WORKERS_ENV, 'auto').strip().lower()
    cpus = os.cpu_count() or 1
    maximum = min(8, max(1, cpus // 2))
    decision = dict(requested=requested, cpu_count=cpus, workers=1, retryable=False)
    if requested != 'auto':
        try:
            value = int(requested)
            if not 1 <= value <= 8:
                return dict(decision, reason='invalid_worker_setting')
            maximum = min(maximum, value)
        except ValueError:
            return dict(decision, reason='invalid_worker_setting')
        if value == 1:
            return dict(decision, reason='explicit_serial')
    if maximum < 2:
        return dict(decision, reason='cpu_limit')
    # Daily workers read one immutable file and enrich only a 300-bar window.
    # Budget physical working sets separately from private committed memory.
    # Never trade a lower physical threshold for an exhausted commit limit.
    try:
        memory = memory_snapshot()
        available = memory['available_bytes']
        maximum = min(maximum, max(1, (available - PARENT_HEADROOM) // WORKING_SET_BUDGET))
        if 'commit_available_bytes' in memory:
            maximum = min(maximum, max(1, (memory['commit_available_bytes'] - COMMIT_HEADROOM)
                                      // COMMIT_BUDGET))
    except (AttributeError, OSError, ValueError) as exc:
        return dict(decision, reason='memory_probe_failed', retryable=True, error=type(exc).__name__)
    return dict(decision, **memory, workers=int(maximum),
                reason='memory_limit' if maximum < 2 else 'available', retryable=maximum < 2)


def worker_count():
    return worker_selection()['workers']


class CPUWorker:
    def __init__(self):
        self.process = subprocess.Popen(
            scan_command(), cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1'),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception as exc:
            self.messages.put(dict(error=str(exc)))
        finally:
            self.messages.put(dict(error='扫描计算进程已退出'))

    def compute(self, payload, stop):
        # ASCII JSON also works when a frozen Windows stream retains the
        # system code page; Unicode paths and payloads travel as JSON escapes.
        self.process.stdin.write(json.dumps(payload, ensure_ascii=True, allow_nan=False, default=str) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 120
        while True:
            if stop.is_set():
                raise InterruptedError('已停止扫描')
            if time.monotonic() >= deadline:
                raise TimeoutError('扫描计算进程超过120秒无响应')
            try:
                response = self.messages.get(timeout=.1)
            except queue.Empty:
                continue
            if 'error' in response:
                raise RuntimeError(response['error'])
            result = response['result']
            if (result['dataset'] != payload['record']['id']
                    or result['sha256'] != payload['record']['sha256']):
                raise ValueError('扫描计算进程返回的行情身份不一致')
            if not result.get('prepare_error') and set(result['answers']) != {
                    item['id'] for item in payload['strategies']}:
                raise ValueError('扫描计算进程返回的策略范围不一致')
            return result

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass  # A crashing child can exit between poll and terminate.
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        try:
            self.process.stdin.close()
        except OSError:
            pass  # Broken input pipes must not prevent serial fallback.
        self.reader.join(timeout=2)
        self.process.stdout.close()


class OrderedComputations:
    def __init__(self, tasks, workers, stop):
        self.tasks = iter(tasks)
        self.stop = stop
        self.workers = workers
        self.limit = workers * 2
        self.pending = {}
        self.abandoned = set()
        self.processes = []
        self.lock = threading.Lock()
        self.local = threading.local()
        self.closed = False
        self.failure = None
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='scan-cpu')
        self._fill()

    def _compute(self, payload):
        if self.stop.is_set():
            raise InterruptedError('已停止扫描')
        worker = getattr(self.local, 'worker', None)
        if worker is None:
            with self.lock:
                if self.closed:
                    raise InterruptedError('扫描计算进程已关闭')
                worker = CPUWorker()
                self.processes.append(worker)
                self.local.worker = worker
        return worker.compute(payload, self.stop)

    def _fill(self, minimum=0):
        self.abandoned = {future for future in self.abandoned if not future.done()}
        while not self.closed and len(self.pending) + len(self.abandoned) < self.limit:
            item = next(self.tasks, None)
            if item is None:
                break
            index, payload = item
            if index < minimum:
                continue
            self.pending[index] = self.pool.submit(self._compute, payload)

    def take(self, index, needed=True):
        if not self.closed:
            try:
                memory = memory_snapshot()
                if (memory['available_bytes'] < PARENT_HEADROOM or
                        memory.get('commit_available_bytes', COMMIT_BUDGET) < COMMIT_BUDGET):
                    raise MemoryError('扫描内存余量不足，关闭计算进程并回退串行')
            except (AttributeError, OSError, ValueError, MemoryError) as exc:
                self.failure = str(exc)
                self.close()
                return None
        # Cached duplicate datasets and input failures can skip speculative work.
        for old in [key for key in self.pending if key < index]:
            future = self.pending.pop(old)
            if not future.cancel() and not future.done():
                self.abandoned.add(future)
        self._fill(index)
        future = self.pending.pop(index, None)
        result = None
        if future is not None:
            if not needed:
                if not future.cancel() and not future.done():
                    self.abandoned.add(future)
            else:
                try:
                    while True:
                        if self.stop.is_set():
                            raise InterruptedError('已停止扫描')
                        try:
                            result = future.result(timeout=.1)
                            break
                        except TimeoutError:
                            if future.done():
                                raise
                except InterruptedError:
                    raise
                except Exception as exc:
                    # Infrastructure failure resumes the unchanged serial path.
                    self.failure = str(exc)
                    self.close()
        self._fill(index + 1)
        return result

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        for future in self.pending.values():
            future.cancel()
        for worker in self.processes:
            worker.close()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.pending.clear()
        self.abandoned.clear()


def open_computations(store, spec, strategies, records, lookups, cached, stop, accelerated,
                      *, start_index=0, decision=None):
    decision = {} if decision is None else decision
    decision.update(stock_index=start_index, workers=1, retryable=False)
    def serial(reason):
        decision.update(reason=reason, retryable=False)
        return None
    if accelerated:
        return serial('active_fast_reject')
    if not records:
        return serial('missing_metadata')
    # Research plugins, BaoStock and already approved fast-reject controllers
    # retain their existing execution/authorization/fuse route.
    if any(Path(item.file).resolve().parent != ROOT / 'core' / 'strategies'
           or item.params for item in strategies):
        return serial('custom_strategy_or_params')
    tasks = []
    seen = set()
    for index, (did, (keys, legacy, bridged)) in enumerate(zip(spec['datasets'], lookups)):
        if index < start_index:
            continue  # Previously committed stocks must not be recomputed.
        record = records.get(did)
        if record is None or record['source'] != 'tickflow':
            return serial('unsupported_source_or_metadata')
        if did in seen and not spec.get('force'):
            continue
        seen.add(did)
        missing = [item for item in strategies if not any(
            key in cached for key in (keys[item.id], legacy[item.id], bridged.get(item.id)))]
        if missing:
            tasks.append((index, dict(root=str(store.root), record=record,
                timeframe=spec['timeframe'], asof=spec['asof'], strategies=[
                    dict(id=item.id, version=item.version, params=item.params) for item in missing])))
    if len(tasks) < MIN_STOCKS:
        return serial('below_parallel_threshold')
    # Fixed serial gates need no resource probe. Besides avoiding needless
    # work, their receipt stays independent of fluctuating desktop memory.
    decision.update(worker_selection(), pending_stocks=len(tasks))
    workers = decision['workers']
    if workers < 2:
        return None  # Only transient resource decisions permit a bounded retry.
    return OrderedComputations(tasks, min(workers, len(tasks)), stop)
