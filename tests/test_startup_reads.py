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


def test_worker_invalid_snapshot_falls_back_with_same_payload_and_new_identity(tmp_path,monkeypatch):
    from tests.test_premarket_snapshot import _client_package,ASOF
    from gui.startup_reads import match_rows
    from gui.data import load_candidates,candidate_source_status
    store=Store(tmp_path/'runtime');_client_package(store,tmp_path,monkeypatch)
    snapshot=tmp_path/'snapshot';snapshot.mkdir()
    path=snapshot/'snapshot.json';path.write_text('{invalid',encoding='utf-8')
    monkeypatch.setenv('APLUS_SNAPSHOT_DIR',str(snapshot))
    monkeypatch.delenv('APLUS_SNAPSHOT_URL',raising=False)
    day=tuple(ASOF.split('-'))
    expected=load_candidates(store,'daily','tickflow',day)
    value=match_rows(store.root,('daily',day,'tickflow',(),None))
    assert value['rows']==expected
    assert sum(map(len,expected.values()))>0
    assert '快照拒绝' in candidate_source_status(store)
    path.write_text('{invalid changed}',encoding='utf-8')
    refreshed=match_rows(store.root,('daily',day,'tickflow',(),value['cache']))
    assert refreshed['cache'][0]!=value['cache'][0]
    assert refreshed['rows']==expected


@pytest.mark.parametrize('asof', [None, (), (None,None,None), ('','','')])
def test_worker_all_dates_keeps_current_source_and_dated_history_resolves(tmp_path, asof):
    import pandas as pd
    from gui.startup_reads import match_rows
    from gui.data import candidate_source_for_date, load_candidates
    from workbench.market import save_dataset
    from workbench.store import now
    from workbench.sources import market_source
    store=Store(tmp_path/'runtime')
    for source,code,day in [('baostock','sh.600000','2026-11-10'),
                            ('legacy-engine-a','sz.000001','2026-11-11')]:
        frame=pd.DataFrame([dict(date=day,open=10.,high=11.,low=9.,close=10.,volume=1000.)])
        dataset=save_dataset(store,code,frame,source,'前复权')
        with store.connect() as db:
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (source,'synthetic-job',code,'MTR_MASTER','test','daily',day,day,dataset,'{}',now()))
    assert market_source(store)=='baostock'
    assert candidate_source_for_date(store,'daily',asof)=='legacy-engine-a'
    value=match_rows(store.root,('daily',asof,None,(),None))
    assert value['source']=='baostock'
    assert [r['code'] for r in value['all_rows']]==['sh.600000']
    for day,expected,code in [('10','baostock','sh.600000'),
                              ('11','legacy-engine-a','sz.000001')]:
        date=('2026','11',day)
        dated=match_rows(store.root,('daily',date,None,(),None))
        assert dated['source']==candidate_source_for_date(store,'daily',date)==expected
        assert dated['rows']==load_candidates(store,'daily',expected,date)
        assert [r['code'] for r in dated['all_rows']]==[code]
    explicit=match_rows(store.root,('daily',asof,'legacy-engine-a',(),None))
    assert explicit['source']=='legacy-engine-a'
    assert [r['code'] for r in explicit['all_rows']]==['sz.000001']
