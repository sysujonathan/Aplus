"""Real SQLite crashes, only disposable already-bound libraries."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from workbench import installed_data as data, version_update
from workbench.store import Store


@pytest.fixture
def bound(tmp_path):
    runtime=tmp_path/'runtime';store=Store(runtime)
    store.execute("INSERT INTO watchlist(code,observation_id,notes,active,created,updated) VALUES('sz.000001','','keep',1,'now','now')")
    home=tmp_path/'state';data.bind_existing(runtime,home=home)
    return runtime,home


@pytest.fixture
def unlocked(monkeypatch):
    monkeypatch.setattr(version_update,'application_mutex',nullcontext)
    monkeypatch.setattr(version_update,'require_web_closed',lambda:None)


def snapshots(home):return list((home/'backups').glob('recovery-*'))


@pytest.mark.parametrize('kind',['stale','hot','wal'])
def test_explicit_recovery_preserves_raw_snapshot_then_sqlite_recovers(bound,unlocked,kind):
    runtime,home=bound;database=runtime/'workbench.sqlite3'
    if kind=='stale':database.with_name(database.name+'-journal').write_bytes(b'x'*108)
    else:
        with sqlite3.connect(database) as db:
            db.executemany("INSERT INTO meta(key,value) VALUES(?,?)",[(f'crash-{n}','old'*100) for n in range(1000)])
        script='''import sqlite3,os,sys
db=sqlite3.connect(sys.argv[1]);db.execute('PRAGMA cache_size=2')
if sys.argv[2]=='wal':
 db.execute('PRAGMA journal_mode=WAL');db.execute("UPDATE meta SET value='committed' WHERE key='crash-0'");db.commit()
else:
 db.execute('BEGIN IMMEDIATE');db.execute("UPDATE meta SET value='unfinished' WHERE key LIKE 'crash-%'")
os._exit(0)
'''
        subprocess.run([sys.executable,'-c',script,str(database),kind],check=True,timeout=15)
    with pytest.raises(data.RecoveryRequired):data.bound_runtime(home=home)
    raw={p.name:p.read_bytes() for p in runtime.glob('workbench.sqlite3*')}
    assert data.recover_bound_runtime(home=home)==runtime
    assert data.bound_runtime(home=home)==runtime
    folder=snapshots(home)[0]
    assert all((folder/name).read_bytes()==value for name,value in raw.items())
    assert json.loads((folder/'receipt.json').read_text())['state']=='recovered'
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT notes FROM watchlist').fetchone()[0]=='keep'
        if kind!='stale':assert db.execute("SELECT value FROM meta WHERE key='crash-0'").fetchone()[0]==('committed' if kind=='wal' else 'old'*100)


def test_first_discovery_still_never_repairs_unbound_library(bound):
    runtime,home=bound;journal=runtime/'workbench.sqlite3-journal';journal.write_bytes(b'x'*108)
    original=(runtime/'workbench.sqlite3').read_bytes()
    with pytest.raises(data.RecoveryRequired):data.candidate_data(runtime)
    assert journal.read_bytes()==b'x'*108 and (runtime/'workbench.sqlite3').read_bytes()==original
    assert not snapshots(home)


@pytest.mark.parametrize('blocked',['desktop','web','disk'])
def test_recovery_stops_before_snapshot_or_sqlite_when_not_safe(bound,unlocked,monkeypatch,blocked):
    runtime,home=bound;journal=runtime/'workbench.sqlite3-journal';journal.write_bytes(b'x'*108)
    original=(runtime/'workbench.sqlite3').read_bytes()
    def busy(*a):raise ValueError('busy')
    if blocked=='desktop':monkeypatch.setattr(version_update,'application_mutex',busy)
    elif blocked=='web':monkeypatch.setattr(version_update,'require_web_closed',busy)
    else:monkeypatch.setattr(data.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ValueError):data.recover_bound_runtime(home=home)
    assert journal.read_bytes()==b'x'*108 and (runtime/'workbench.sqlite3').read_bytes()==original
    assert not snapshots(home)


def test_active_sqlite_writer_is_never_forced_open(bound,unlocked):
    runtime,home=bound;database=runtime/'workbench.sqlite3'
    db=sqlite3.connect(database)
    try:
        db.execute('BEGIN IMMEDIATE');db.execute("UPDATE watchlist SET notes='uncommitted'")
        raw={p.name:p.read_bytes() for p in runtime.glob('workbench.sqlite3*')}
        with pytest.raises(ValueError,match='备份已保留'):data.recover_bound_runtime(home=home)
        assert all((runtime/name).read_bytes()==value for name,value in raw.items())
    finally:db.rollback();db.close()
    assert data.bound_runtime(home=home)==runtime


def test_corrupt_database_keeps_snapshot_and_binding(bound,unlocked):
    runtime,home=bound;database=runtime/'workbench.sqlite3';database.write_bytes(b'corrupt database')
    journal=runtime/'workbench.sqlite3-journal';journal.write_bytes(b'x'*108)
    binding=(home/'data.json').read_bytes()
    with pytest.raises(ValueError,match='备份已保留'):data.recover_bound_runtime(home=home)
    assert (snapshots(home)[0]/database.name).read_bytes()==b'corrupt database'
    assert (home/'data.json').read_bytes()==binding


def test_missing_or_unbound_database_never_creates_empty_file(tmp_path,unlocked):
    with pytest.raises(FileNotFoundError):data.recover_bound_runtime(home=tmp_path/'none')
    home=tmp_path/'bound';data.atomic_json(home/'data.json',dict(runtime=str(tmp_path/'missing')))
    with pytest.raises(ValueError,match='不存在'):data.recover_bound_runtime(home=home)
    assert not (tmp_path/'missing').exists()


@pytest.mark.parametrize('result',[True,False])
def test_bound_crash_startup_does_not_reenter_legacy_picker(tmp_path,monkeypatch,result):
    from workbench import installed_runtime as runtime, strategies
    import launch_dashboard
    monkeypatch.setattr(strategies,'verify_frozen',lambda:None)
    def required():raise data.RecoveryRequired('crash')
    monkeypatch.setattr(runtime,'bound_runtime',required)
    monkeypatch.setattr(runtime,'recover_data',lambda:tmp_path if result else None)
    monkeypatch.setattr(runtime,'choose_data',lambda p:pytest.fail('must not redirect to legacy picker'))
    opened=[];monkeypatch.setattr(launch_dashboard,'main',lambda:opened.append(True))
    runtime.launch()
    assert bool(opened)==result
