"""Batch reads keep the original source, timeframe, ordering and cancellation."""
import sqlite3

import pytest

from gui.startup_reads import ReadSession, latest_for_keys, watch_rows
from workbench.store import Store


def test_batch_latest_matches_individual_tie_breaks_and_sources():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE datasets(id,source,timeframe,code,end,created)')
    rows=[('old','tickflow','daily','600000','2026-11-10','1'),
          ('new','tickflow','daily','600000','2026-11-11','2'),
          ('tie','tickflow','daily','600000','2026-11-11','2'),
          ('week','tickflow','weekly','600000','2026-11-06','3'),
          ('bao','baostock','daily','600000','2026-11-12','4')]
    db.executemany('INSERT INTO datasets VALUES(?,?,?,?,?,?)',rows)
    class View:
        def rows(self,sql,args):return [dict(r) for r in db.execute(sql,args)]
    keys=[('tickflow','daily','600000'),('tickflow','weekly','600000'),
          ('baostock','daily','600000'),('tickflow','daily','missing')]
    actual=latest_for_keys(View(),keys+keys)
    for key in keys:
        expected=db.execute('SELECT * FROM datasets WHERE source=? AND timeframe=? AND code=? '
                            'ORDER BY end DESC,created DESC,rowid DESC LIMIT 1',key).fetchone()
        assert actual.get(key)==(dict(expected) if expected else None),key
    db.close()


def test_worker_watch_preserves_manual_order_names_and_read_only(tmp_path):
    store=Store(tmp_path/'runtime')
    store.add_manual_watch('600000','浦发');store.add_manual_watch('000001','平安')
    expected=store.rows('SELECT code FROM watchlist WHERE active=1 ORDER BY updated DESC')
    before=store.path.read_bytes()
    rows=watch_rows(store.root)
    assert [r['code'] for r in rows]==[r['code'] for r in expected]
    assert {r['name'] for r in rows}=={'浦发','平安'}
    assert all(r['manual'] and r['observation_id'] is None for r in rows)
    assert store.path.read_bytes()==before
    with ReadSession(store.root) as view:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            view.db.execute("DELETE FROM watchlist")


def test_worker_cancellation_is_not_successful_empty_list(tmp_path):
    store=Store(tmp_path/'runtime')
    def cancelled():raise InterruptedError('obsolete')
    with pytest.raises(InterruptedError):watch_rows(store.root,cancelled)
