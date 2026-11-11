"""Issue #132: watchdog emits one stack record per stall threshold."""
from gui.responsiveness import ResponsivenessWatchdog


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _watchdog():
    records = []
    clock = Clock()
    return ResponsivenessWatchdog(records.append, clock=clock), records, clock


def test_quiet_thread_emits_nothing():
    watchdog, records, clock = _watchdog()
    watchdog.beat()
    clock.now += 0.2
    assert watchdog.check_once() == []
    assert records == []


def test_each_threshold_fires_once_per_episode_with_stack():
    watchdog, records, clock = _watchdog()
    watchdog.beat()
    clock.now += 0.6
    emitted = watchdog.check_once()
    assert [r['threshold_ms'] for r in emitted] == [500]
    assert 'test_each_threshold_fires_once_per_episode_with_stack' in emitted[0]['stack']
    clock.now += 0.6
    assert [r['threshold_ms'] for r in watchdog.check_once()] == [1000]
    clock.now += 2.0
    assert [r['threshold_ms'] for r in watchdog.check_once()] == [3000]
    clock.now += 5.0
    assert watchdog.check_once() == []
    assert len(records) == 3


def test_recovery_resets_the_episode():
    watchdog, records, clock = _watchdog()
    watchdog.beat()
    clock.now += 0.7
    assert len(watchdog.check_once()) == 1
    watchdog.beat()
    clock.now += 0.1
    assert watchdog.check_once() == []
    clock.now += 0.6
    assert [r['threshold_ms'] for r in watchdog.check_once()] == [500]
