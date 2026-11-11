"""Tk main-thread responsiveness watchdog (Issue #132).

The Tk thread proves liveness by beating a heartbeat from an ``after()``
ticker. A daemon thread compares the heartbeat age against stall
thresholds; each threshold crossed during one stall episode records the
main thread's stack (function names and line numbers only — no locals,
no trading data) to a JSONL log, so a field "not responding" report
carries its own evidence instead of a guess. Disable with
``APLUS_RESPONSIVENESS_WATCHDOG=0``.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

THRESHOLDS = (0.5, 1.0, 3.0)


def default_log_path():
    return Path(os.environ.get('TEMP', '.')) / 'aplus_responsiveness.jsonl'


def stack_of(thread_id):
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return ''
    return ''.join(traceback.format_stack(frame))


class ResponsivenessWatchdog:
    def __init__(self, sink=None, *, thresholds=THRESHOLDS, interval=0.25,
                 clock=time.monotonic):
        self._sink = sink if sink is not None else self._file_sink
        self._thresholds = tuple(sorted(thresholds))
        self._interval = interval
        self._clock = clock
        self._last_beat = None
        self._main_thread_id = None
        self._fired = set()
        self._thread = None
        self._stopped = threading.Event()

    def beat(self):
        """Called from the Tk thread's ticker; cheap and allocation-light."""
        if self._main_thread_id is None:
            self._main_thread_id = threading.get_ident()
        self._last_beat = self._clock()

    def check_once(self, now=None):
        """Emit one record per newly crossed threshold of a stall episode."""
        if self._last_beat is None:
            return []
        now = self._clock() if now is None else now
        age = now - self._last_beat
        if age < self._thresholds[0]:
            self._fired.clear()
            return []
        emitted = []
        for threshold in self._thresholds:
            if age >= threshold and threshold not in self._fired:
                self._fired.add(threshold)
                record = dict(kind='main_thread_stall',
                              age_ms=round(age * 1000),
                              threshold_ms=int(threshold * 1000),
                              at=datetime.now(timezone.utc).isoformat(),
                              stack=stack_of(self._main_thread_id))
                self._sink(record)
                emitted.append(record)
        return emitted

    def _file_sink(self, record):
        try:
            with default_log_path().open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def start(self):
        if self._thread is not None:
            return

        def loop():
            while not self._stopped.wait(self._interval):
                try:
                    self.check_once()
                except Exception:
                    pass

        self._stopped.clear()
        self._thread = threading.Thread(
            target=loop, daemon=True, name='aplus-responsiveness-watchdog')
        self._thread.start()

    def stop(self):
        self._stopped.set()
        self._thread = None
