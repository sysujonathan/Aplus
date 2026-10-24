from __future__ import annotations

import sqlite3
import subprocess
import sys

import pytest

from tools import benchmark_mtr_fast_reject as generated_tool
from tools import validate_mtr_shadow as real_tool


def _service_row(business_digest):
    return {
        'business': {'signals': 0, 'errors': []},
        'business_digest': business_digest,
        'cold_seconds': 1.0,
        'cold_peak_python_bytes': 1,
        'cached_seconds': 0.1,
        'cached_peak_python_bytes': 1,
        'cache_rows': 1,
        'observation_rows': 0,
        'event_rows': 1,
        'event_detail_bytes': 1,
        'database_bytes': 1,
        'terminal_receipt_bytes': 1,
    }


def _source_database(root, journal_mode='delete'):
    root.mkdir()
    database = root / 'workbench.sqlite3'
    db = sqlite3.connect(database)
    db.execute(f'PRAGMA journal_mode={journal_mode}')
    db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    db.execute(
        'CREATE TABLE datasets(code TEXT,source TEXT,timeframe TEXT,rows INTEGER)')
    db.execute("INSERT INTO meta VALUES('schema_version','4')")
    db.execute("INSERT INTO datasets VALUES('sh.600000','baostock','daily',125)")
    db.commit()
    return database, db


def test_service_benchmark_checks_every_round_and_reports_location(monkeypatch):
    calls = {'SHADOW': 0}

    def fake_service_once(mode, _snapshots):
        calls[mode] = calls.get(mode, 0) + 1
        value = 'changed' if mode == 'SHADOW' and calls[mode] == 2 else 'same'
        return _service_row(value)

    monkeypatch.setattr(real_tool, 'service_once', fake_service_once)
    with pytest.raises(AssertionError, match=r'SHADOW\[round=2\]=changed'):
        real_tool.service_benchmark([object()], rounds=2)


@pytest.mark.parametrize('tool', [real_tool, generated_tool])
def test_output_writer_creates_parent_directories(tmp_path, tool):
    target = tmp_path / 'new' / 'nested' / 'result.json'
    tool.write_output(target, '{"ok":true}')
    assert target.read_text(encoding='utf-8') == '{"ok":true}\n'


@pytest.mark.parametrize('script', [
    real_tool.REPO_ROOT / 'tools' / 'validate_mtr_shadow.py',
    generated_tool.REPO_ROOT / 'tools' / 'benchmark_mtr_fast_reject.py',
])
def test_tool_help_works_outside_repository(tmp_path, script):
    result = subprocess.run(
        [sys.executable, str(script), '--help'], cwd=tmp_path,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '--output' in result.stdout


def test_repository_head_does_not_fabricate_sha_when_git_is_unavailable(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError('git')

    monkeypatch.setattr(real_tool.subprocess, 'run', unavailable)
    assert real_tool.repository_head() == {
        'status': 'unavailable', 'sha': None, 'detail': 'git executable not found'}


def test_repository_head_uses_explicit_root_and_reports_unknown(monkeypatch, tmp_path):
    captured = {}

    def unresolved(*args, **kwargs):
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(args[0], 128, '', 'not a repository')

    monkeypatch.setattr(real_tool.subprocess, 'run', unresolved)
    result = real_tool.repository_head(tmp_path)
    assert captured['kwargs']['cwd'] == tmp_path
    assert result == {
        'status': 'unknown', 'sha': None, 'detail': 'not a repository'}


def test_readonly_records_uses_locked_snapshot_for_static_database(tmp_path):
    database, db = _source_database(tmp_path / 'runtime')
    db.close()
    before = database.read_bytes()
    _root, schema, records, _quality, safety = real_tool.readonly_records(
        database.parent, 'baostock')
    assert schema == '4'
    assert len(records) == 1
    assert safety['connection'] == 'mode=ro'
    assert safety['snapshot_transaction'] is True
    assert safety['immutable_assumption'] is False
    assert database.read_bytes() == before


def test_readonly_records_sees_committed_wal_and_not_uncommitted_write(tmp_path):
    database, writer = _source_database(tmp_path / 'runtime', journal_mode='wal')
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("INSERT INTO datasets VALUES('sz.000001','baostock','daily',125)")
    try:
        _root, _schema, records, _quality, safety = real_tool.readonly_records(
            database.parent, 'baostock')
    finally:
        writer.rollback()
        writer.close()
    assert [record['code'] for record in records] == ['sh.600000']
    assert safety['journal_mode'] == 'wal'
    assert safety['wal_sidecar_present'] is True
