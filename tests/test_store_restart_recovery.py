"""Exercise SQLite crash recovery with disposable Store databases."""
from contextlib import closing
import sqlite3
import subprocess
import sys

import pytest

from workbench.store import Store


def test_restart_recovers_hot_journal_without_waiting(tmp_path):
    store = Store(tmp_path)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("CREATE TABLE restart_probe (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO restart_probe VALUES (1, 'committed')")

    # Force dirty pages onto disk before terminating without closing SQLite.
    writer = subprocess.run(
        [sys.executable, "-c", """
import os
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
db.execute("PRAGMA synchronous=FULL")
db.execute("PRAGMA cache_size=5")
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE restart_probe SET value='uncommitted' WHERE id=1")
db.executemany("INSERT INTO restart_probe(value) VALUES (?)",
               [('x' * 8192,)] * 64)
os._exit(17)
""", str(store.path)],
        check=False,
        timeout=15,
    )
    assert writer.returncode == 17
    journal = store.path.with_name(store.path.name + "-journal")
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")

    reopened = Store(tmp_path)
    assert reopened.rows("SELECT id, value FROM restart_probe") == [
        {"id": 1, "value": "committed"},
    ]
    assert reopened.rows("PRAGMA integrity_check") == [{"integrity_check": "ok"}]
    assert not journal.exists()
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("version", ["8", "999"])
def test_restart_preserves_version_policy(tmp_path, version):
    store = Store(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))

    if version == "999":
        original = store.path.read_bytes()
        with pytest.raises(ValueError) as error:
            Store(tmp_path)
        assert str(error.value) == "数据库版本与程序不匹配，请先完成升级迁移；不会自动清理数据"
        assert store.path.read_bytes() == original
        assert not (tmp_path / "backups").exists()
        return

    reopened = Store(tmp_path)
    backups = list((tmp_path / "backups").glob("pre-pa-v8-*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == ("8",)
    assert reopened.rows("SELECT value FROM meta WHERE key='schema_version'")[0]["value"] == "9"


def test_restart_preserves_future_version_with_committed_wal(tmp_path):
    path = Store(tmp_path).path
    writer = subprocess.run(
        [sys.executable, "-c", """
import os
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
db.commit()
os._exit(17)
""", str(path)],
        check=False,
        timeout=15,
    )
    assert writer.returncode == 17
    wal = path.with_name(path.name + "-wal")
    original_db = path.read_bytes()
    original_wal = wal.read_bytes()
    assert original_wal

    with pytest.raises(ValueError) as error:
        Store(tmp_path)
    assert str(error.value) == "数据库版本与程序不匹配，请先完成升级迁移；不会自动清理数据"
    assert path.read_bytes() == original_db
    assert wal.read_bytes() == original_wal
