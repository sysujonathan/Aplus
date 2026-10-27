"""Run marked native desktop tests with fresh Tcl state, including fixtures."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


WORKER_NODE = 'APLUS_NATIVE_TEST_NODE'
ROOT = Path(__file__).resolve().parents[1]


def run_native_test(nodeid, *, root=ROOT, timeout=180):
    environment = dict(os.environ, **{WORKER_NODE: nodeid, 'PYTHONUTF8': '1'})
    # A short directory avoids Windows MAX_PATH for hashed market artifacts.
    with TemporaryDirectory(prefix='ap-ui-') as temporary:
        return subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', nodeid, '--basetemp', temporary],
            cwd=root, env=environment, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
        )


def pytest_configure(config):
    config.addinivalue_line('markers', 'native_process: run native UI and its fixtures in a fresh process')


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if (os.name != 'nt' or item.get_closest_marker('native_process') is None
            or os.environ.get(WORKER_NODE) == item.nodeid):
        return None

    def isolated_call():
        result = run_native_test(item.nodeid, root=item.config.rootpath)
        if result.returncode != 0:
            pytest.fail('Native test worker failed (exit '
                        f'{result.returncode}):\n{result.stdout}{result.stderr}', pytrace=False)

    # Intercept before setup: Tk roots can also be created by module fixtures.
    # The child runs the normal pytest setup/call/teardown exactly once. Do not
    # retry a failed child or suppress its assertion/fixture failure.
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    try:
        call = pytest.CallInfo.from_call(isolated_call, when='call')
        report = pytest.TestReport.from_item_and_call(item, call)
        item.ihook.pytest_runtest_logreport(report=report)
    finally:
        # The preceding ordinary test may have retained parent module/session
        # fixtures because this marked item was next. Release those at the same
        # boundary as pytest's normal protocol, even though this item's own
        # fixtures ran only in the worker.
        teardown = pytest.CallInfo.from_call(
            lambda: item.session._setupstate.teardown_exact(nextitem),
            when='teardown',
        )
        item.ihook.pytest_runtest_logreport(
            report=pytest.TestReport.from_item_and_call(item, teardown))
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
