"""Bounded running scan receipts and recovery from durable audit rows."""
from __future__ import annotations

import json

from .store import dumps, now


PROGRESS_KIND = 'scan-progress-v1'
SEED_ACTION = '建立扫描回执基线'


def _counts(report):
    return {key: int(report.get(key, 0) or 0)
            for key in ('success', 'signals', 'no_signal', 'reused', 'calculated', 'total')}


def progress_receipt(report, timeframe):
    """Return the fixed-size contract stored while a scan is still running."""
    result = {
        '_receipt': PROGRESS_KIND,
        'timeframe': timeframe,
        **_counts(report),
        'error_count': len(report.get('errors', [])),
        'observation_count': len(report.get('observation_ids', [])),
    }
    if report.get('asof'):
        result['asof'] = report['asof']
    return result


def begin_receipt(store, job, base_report, timeframe):
    """Persist the static receipt seed once, then publish a bounded summary."""
    detail = dumps({'version': PROGRESS_KIND, 'timeframe': timeframe, 'base': base_report})
    summary = dumps(progress_receipt(base_report, timeframe))
    with store.connect() as db:
        db.execute('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)',
                   (now(), job, SEED_ACTION, '', detail))
        db.execute('UPDATE jobs SET result=? WHERE id=?', (summary, job))


def is_progress_receipt(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or '{}')
        except (TypeError, ValueError):
            return False
    return isinstance(value, dict) and value.get('_receipt') == PROGRESS_KIND


def rebuild_receipt(store, job):
    """Rebuild the last committed full scan report without changing the DB."""
    rows = store.rows('SELECT result FROM jobs WHERE id=? AND kind=?', (job, 'scan'))
    if not rows:
        return None
    try:
        summary = json.loads(rows[0].get('result') or '{}')
    except (TypeError, ValueError):
        return None
    if not is_progress_receipt(summary):
        return None
    seeds = store.rows(
        'SELECT seq,detail FROM events WHERE job_id=? AND action=? ORDER BY seq DESC LIMIT 1',
        (job, SEED_ACTION))
    if not seeds:
        return None
    try:
        seed = json.loads(seeds[0]['detail'])
        if seed.get('version') != PROGRESS_KIND:
            return None
        report = dict(seed['base'])
    except (KeyError, TypeError, ValueError):
        return None

    errors = list(report.get('errors', []))
    observation_ids = list(report.get('observation_ids', []))
    events = store.rows(
        "SELECT action,detail FROM events WHERE job_id=? AND seq>? "
        "AND action IN ('策略计算失败','记录策略命中') ORDER BY seq",
        (job, seeds[0]['seq']))
    for event in events:
        try:
            detail = json.loads(event['detail'])
        except (TypeError, ValueError):
            return None
        if event['action'] == '策略计算失败':
            required = ('code', 'strategy', 'timeframe', 'error')
            if any(key not in detail for key in required):
                return None
            errors.append({key: detail[key] for key in required})
        elif event['action'] == '记录策略命中' and detail.get('observation'):
            observation_ids.append(detail['observation'])
        else:
            return None

    for key in ('success', 'signals', 'no_signal', 'reused', 'calculated', 'total'):
        if key in summary:
            report[key] = summary[key]
    report['errors'] = errors
    report['observation_ids'] = observation_ids
    return report


def persist_rebuilt_receipt(store, job):
    report = rebuild_receipt(store, job)
    if report is not None:
        store.execute('UPDATE jobs SET result=? WHERE id=?', (dumps(report), job))
    return report


def receipt_for_read(store, job, value):
    """Give existing report consumers a full view without growing jobs.result."""
    report = json.loads(value or '{}') if isinstance(value, str) else dict(value or {})
    if is_progress_receipt(report):
        return rebuild_receipt(store, job) or report
    return report


def recover_running_scans(store):
    """Materialize partial receipts before startup marks old work interrupted."""
    for row in store.rows("SELECT id FROM jobs WHERE kind='scan' AND status='running'"):
        persist_rebuilt_receipt(store, row['id'])
