import json

from tests.test_workbench import store, frame
from workbench.market import save_dataset, latest_datasets
from workbench.repair_outcomes import before_repair, remember_repairs, unchanged_repairs


def test_unchanged_attempt_is_source_date_and_snapshot_specific(store,frame):
    code='sh.600000'; day=frame.date.iloc[-1]
    did=save_dataset(store,code,frame,'tickflow','前复权')
    before=before_repair(store,'tickflow',[code])
    remember_repairs(store,'tickflow',day,before,[did])
    records={code:latest_datasets(store,'tickflow')[0]}
    assert unchanged_repairs(store,'tickflow',day,records)==[code]
    assert unchanged_repairs(store,'tencent',day,records)==[]
    assert unchanged_repairs(store,'tickflow','2030-01-01',records)==[]
    changed=save_dataset(store,code,frame.assign(close=frame.close+.01),'tickflow','前复权')
    remember_repairs(store,'tickflow',day,before,[changed])
    assert unchanged_repairs(store,'tickflow',day,{code:latest_datasets(store,'tickflow')[0]})==[]


def test_old_successful_same_snapshot_receipts_are_read_only_evidence(store,frame):
    code='sh.600000'; day=frame.date.iloc[-1]
    did=save_dataset(store,code,frame,'tickflow','前复权')
    def job(name,stamp,repair,ids):
        store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
            (name,'sync','partial',stamp,json.dumps(dict(source='tickflow',repair_codes=repair)),
             json.dumps(dict(end_requested=day,datasets=ids))))
    job('first','2026-01-01T00:00:00',[],[did])
    job('repair','2026-01-01T00:01:00',[code],[did])
    records={code:latest_datasets(store,'tickflow')[0]}
    before=store.rows('SELECT * FROM meta')
    assert unchanged_repairs(store,'tickflow',day,records)==[code]
    assert store.rows('SELECT * FROM meta')==before
    newer=save_dataset(store,code,frame.assign(close=frame.close+.01),'tickflow','前复权')
    job('previous-changed','2026-01-01T00:00:30',[],[newer])
    # Returning an older snapshot is a change from the last successful receipt,
    # not proof of an unchanged repair merely because it existed long ago.
    assert unchanged_repairs(store,'tickflow',day,records)==[]
    store.execute("DELETE FROM jobs WHERE id='previous-changed'")
    store.execute("UPDATE jobs SET result=? WHERE id='repair'",(json.dumps(dict(end_requested=day,datasets=[])),))
    assert unchanged_repairs(store,'tickflow',day,records)==[]
