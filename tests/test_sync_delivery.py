"""Approval bundles must be complete, isolated and never upload automatically."""
import csv
import json
from zipfile import ZipFile

import pytest

from tests.test_desktop_sync import sample, ASOF
from tools import prepare_sync_delivery as delivery
from workbench.store import digest


def test_package_contains_every_declared_file_and_detaches_runtime(sample, tmp_path, monkeypatch):
    server, _, _ = sample
    # Packaging is tested independently of snapshot receipt validation, which
    # belongs to load_snapshot and #162. Real snapshot validation is also run E2E.
    monkeypatch.setattr(delivery, 'load_snapshot', lambda _: {'asof': ASOF})
    target = tmp_path / 'pending'
    result = delivery.prepare(server.root, ASOF, target)
    assert result['status'] == 'awaiting-human-upload-approval'
    rows = list(csv.DictReader((target / 'upload-files.csv').open(encoding='utf-8')))
    assert rows[-1]['path'] == 'manifest.json'
    assert set(r['path'] for r in rows) == {
        'manifest.json', 'sources/tickflow/universe.csv', 'sources/tickflow/trading_calendar.json',
        *(r['path'] for r in server.rows('SELECT * FROM datasets'))}
    with ZipFile(target / 'sync-source.zip') as archive:
        assert archive.namelist() == [r['path'] for r in rows]
        for row in rows:
            assert digest(archive.read(row['path'])) == row['sha256']
    record = server.rows('SELECT * FROM datasets')[0]
    original = (server.root / record['path']).read_bytes()
    assert (target / 'source' / record['path']).stat().st_ino != (server.root / record['path']).stat().st_ino
    (target / 'source' / record['path']).write_bytes(b'changed staging only')
    assert (server.root / record['path']).read_bytes() == original
    with pytest.raises(ValueError, match='已存在'):
        delivery.prepare(server.root, ASOF, target)


def test_reject_runtime_destination_and_old_snapshot(sample, tmp_path, monkeypatch):
    server, _, _ = sample
    with pytest.raises(ValueError, match='隔离'):
        delivery.prepare(server.root, ASOF, server.root / 'pending')
    monkeypatch.setattr(delivery, 'load_snapshot', lambda _: {'asof': '2026-11-10'})
    with pytest.raises(ValueError, match='拒绝回落'):
        delivery.prepare(server.root, ASOF, tmp_path / 'pending')
    assert not (tmp_path / 'pending').exists()


def test_invalid_snapshot_never_exports(sample, tmp_path):
    server, _, _ = sample
    # The main-branch synthetic fixture lacks an anchor (with #162 it is valid).
    path = server.root / 'snapshots' / ASOF / 'snapshot.json'
    payload = json.loads(path.read_text())
    payload['sha256'] = 'broken'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        delivery.prepare(server.root, ASOF, tmp_path / 'pending')
    assert not (tmp_path / 'pending').exists()
