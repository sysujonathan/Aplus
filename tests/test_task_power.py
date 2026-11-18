import pytest

from tests.test_workbench import store
from workbench import task_power


@pytest.mark.parametrize('error', [None, InterruptedError, ValueError])
def test_task_request_is_released_on_same_thread_on_every_exit(store, monkeypatch, error):
    import threading
    calls = []
    monkeypatch.setattr(task_power.os, 'name', 'nt')
    monkeypatch.setattr(task_power, 'execution_state',
                        lambda flags: calls.append((flags, threading.get_ident())))
    try:
        with task_power.active_task(store, 'fixture', 'premarket'):
            if error:
                raise error('fixture')
    except (InterruptedError, ValueError):
        assert error is not None
    assert calls == [(0x80000001, threading.get_ident()), (0x80000000, threading.get_ident())]


def test_unavailable_power_request_does_not_block_task(store, monkeypatch):
    monkeypatch.setattr(task_power.os, 'name', 'nt')
    def unavailable(_flags):
        raise OSError('fixture unavailable')
    monkeypatch.setattr(task_power, 'execution_state', unavailable)
    with task_power.active_task(store, 'fixture', 'scan'):
        assert True
    events = store.rows("SELECT detail FROM events WHERE action='任务防空闲休眠'")
    assert 'fixture unavailable' in events[0]['detail']
