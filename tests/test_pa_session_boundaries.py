"""Real DPAPI/files/processes, synthetic identities; never production credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from workbench import pa_auth
from workbench.pa_auth import GoogleSession, SessionChanged
from workbench.pa_shared import GoogleDrive
from workbench.store import Store
from gui.growth_bridge import GrowthBridge


def credentials(user='a', expires=0):
    return dict(user=dict(id=user, name=user), expires=expires, access_token='fake-' + user,
                client_id='fake', client_secret='fake', refresh_token='refresh-fake-' + user)


def session(path):
    value = GoogleSession(path.parent)
    value.path = path
    return value


@pytest.mark.parametrize('action', ['switch', 'logout'])
def test_other_process_invalidates_pending_refresh_and_restart_identity(tmp_path, monkeypatch, action):
    path = tmp_path / 'session.dpapi'
    old = session(path)
    old.save(credentials())
    assert old.load()['user']['id'] == 'a'
    entered, release = threading.Event(), threading.Event()
    def token(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return b'{"access_token":"fake-renewed-a","expires_in":3600}', {}
    monkeypatch.setattr(pa_auth, 'request', token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(old.access_token)
        assert entered.wait(5)
        code = """
import json,sys
from pathlib import Path
from workbench.pa_auth import GoogleSession
s=GoogleSession();s.path=Path(sys.argv[1])
if sys.argv[2]=='logout': s.logout()
else: s.save(json.loads(sys.argv[3]))
"""
        result = subprocess.run([sys.executable, '-c', code, str(path), action,
                                 json.dumps(credentials('b', time.time() + 3600))],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        release.set()
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        with pytest.raises(SessionChanged):
            pending.result(timeout=5)
    restarted = session(path)
    if action == 'logout':
        assert old.load() is None and restarted.load() is None
    else:
        assert old.load()['user']['id'] == restarted.load()['user']['id'] == 'b'
        assert restarted.access_token() == 'fake-b'


def test_operation_discards_old_identity_and_normal_renewal_keeps_generation(tmp_path, monkeypatch):
    a, b = session(tmp_path / 'session.dpapi'), session(tmp_path / 'session.dpapi')
    a.save(credentials())
    before = a.generation(a.load())
    monkeypatch.setattr(pa_auth, 'request', lambda *a, **k: (b'{"access_token":"renewed","expires_in":3600}', {}))
    with a.operation():
        assert a.access_token() == 'renewed'
    assert a.generation(a.load()) == before
    with pytest.raises(SessionChanged):
        with a.operation():
            b.save(credentials('b', time.time() + 3600))
            a.load()
    assert a.load()['user']['id'] == 'b'
    with pytest.raises(SessionChanged):
        with a.operation():
            b.logout()  # Even a result assembled before logout is rejected.


def test_parallel_refresh_same_login_reuses_winner_without_replacing_it(tmp_path, monkeypatch):
    a, b = session(tmp_path / 'session.dpapi'), session(tmp_path / 'session.dpapi')
    a.save(credentials())
    barrier = threading.Barrier(2)
    def token(*args, **kwargs):
        barrier.wait(timeout=5)
        return b'{"access_token":"same-login-renewed","expires_in":3600}', {}
    monkeypatch.setattr(pa_auth, 'request', token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda s: s.access_token(), [a, b]))
    assert results == ['same-login-renewed'] * 2
    assert session(a.path).access_token() == 'same-login-renewed'


def test_same_runtime_bridges_refuse_old_personal_requests_without_reopening_db(tmp_path):
    path = tmp_path / 'session.dpapi'
    store = Store(tmp_path / 'store')
    a, b = GrowthBridge(store), GrowthBridge(Store(store.root))
    a._drive = GoogleDrive(session(path))
    b._drive = GoogleDrive(session(path))
    a._drive.session.save(credentials('a', time.time() + 3600))
    assert a.bootstrap()['ok'] and b.bootstrap()['ok']
    first = b.security_state()['security']
    a._public('record', 'topic_notes', 'brooks-high-2', 'private-a')
    assert 'private-a' in str(b.growth())
    a._drive.session.save(credentials('b', time.time() + 3600))
    assert b.security_state()['security'] != first
    stale = b.growth()
    assert not stale['ok'] and stale['error_kind'] == 'security'
    assert 'private-a' not in str(stale)
    assert b.bootstrap()['ok'] and 'private-a' not in str(b.growth())
    # A queued click from the old screen must not write its text into B,
    # even if a background bootstrap has already acknowledged B in Python.
    stale_write = b.call_scoped('add_topic_note', ['brooks-high-2', 'old-a-draft'], first)
    assert not stale_write['ok'] and stale_write['error_kind'] == 'security'
    assert 'old-a-draft' not in str(b._public('personal'))
    a._drive.session.logout()
    assert not b.growth()['ok']
    assert b.bootstrap()['ok']
    assert b._drive.session.load() is None
