"""Remember unchanged successful re-downloads, never waive a missing-price gate."""
import json

from .market import latest_datasets
from .store import dumps
from .suspensions import EVIDENCE_VERSION


def before_repair(store, source, codes):
    wanted=set(codes)
    return {r['code']:r['id'] for r in latest_datasets(store,source) if r['code'] in wanted}


def remember_repairs(store, source, asof, before, dataset_ids):
    for did in set(dataset_ids):
        rows=store.rows('SELECT code FROM datasets WHERE id=? AND source=?',(did,source))
        if not rows or rows[0]['code'] not in before:
            continue
        code=rows[0]['code']
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('repair_outcome:'+source+':'+code,dumps(dict(dataset=did,asof=asof,
             unchanged=before[code]==did,evidence_version=EVIDENCE_VERSION))))


def unchanged_repairs(store, source, asof, records):
    rows=store.rows('SELECT key,value FROM meta WHERE key LIKE ?',('repair_outcome:'+source+':%',))
    result=[]
    for row in rows:
        code=row['key'].split(':',2)[2]; state=json.loads(row['value'])
        record=records.get(code)
        if (record and state.get('unchanged') and state.get('dataset')==record['id'] and
                state.get('asof')==asof and state.get('evidence_version')==EVIDENCE_VERSION):
            result.append(code)
    # Compatibility: old PR27 repair receipts also prove a same-content reply
    # when its snapshot equals the latest earlier receipt for that stock.
    # Read-only, bounded to recent jobs; absence of proof leaves it retryable.
    registered={row['key'].split(':',2)[2] for row in rows}
    jobs=store.rows("SELECT created,spec,result FROM jobs WHERE kind='sync' AND status IN ('completed','partial') "
                   "AND json_extract(spec,'$.source')=? ORDER BY created DESC LIMIT 12",(source,))
    for index,job in enumerate(jobs):
        spec=json.loads(job['spec']); report=json.loads(job['result'] or '{}')
        wanted=set(spec.get('repair_codes') or [])-registered
        if not wanted or report.get('end_requested')!=asof:
            continue
        earlier={}
        for previous in jobs[index+1:]:
            ids=list(dict.fromkeys(json.loads(previous['result'] or '{}').get('datasets',[])))
            for offset in range(0,len(ids),500):
                chunk=ids[offset:offset+500]
                snapshots=store.rows("SELECT id,code FROM datasets WHERE source=? AND timeframe='daily' AND id IN ("+
                    ','.join('?' for _ in chunk)+')',[source]+chunk)
                for snapshot in snapshots:
                    if snapshot['code'] in wanted:
                        earlier.setdefault(snapshot['code'],snapshot['id'])
            if wanted.issubset(earlier):break
        received=set(report.get('datasets',[]))
        for code in wanted:
            if code in records and records[code]['id'] in received and earlier.get(code)==records[code]['id']:
                result.append(code)
    return sorted(set(result))
