"""Incremental refresh with immutable snapshots and overlap adjustment checks."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from .market import FIELDS, load_dataset, save_dataset, validate_bars


def local_history(store, code, job):
    coverage = store.rows('SELECT * FROM sync_coverage WHERE code=?', (code,))
    if coverage:
        state = coverage[0]
        frame, _ = load_dataset(store, state['dataset_id'], job)
        return frame, state
    records = store.rows("SELECT * FROM datasets WHERE code=? AND source='baostock' "
                         "AND adjustment='前复权' AND timeframe='daily' "
                         "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code,))
    if not records:
        return None, None
    record = records[0]
    frame, _ = load_dataset(store, record['id'], job)
    state = dict(dataset_id=record['id'], start=record['start'], end=record['end'])
    # v1 already recorded each successful snapshot before a job could be stopped.
    # Recover requested coverage, including weekends and pre-IPO dates.
    events = store.rows("SELECT e.detail,j.spec FROM events e JOIN jobs j ON j.id=e.job_id "
                        "WHERE e.action='登记行情快照' AND j.kind='sync' "
                        "AND json_extract(e.detail,'$.dataset')=? ORDER BY e.seq DESC LIMIT 1", (record['id'],))
    if events:
        spec = json.loads(events[0]['spec'])
        if spec['start'] <= record['start'] <= record['end'] <= spec['end']:
            state['start'] = spec['start']
            # Never infer unreturned future coverage from an old request.
            state['end'] = record['end']
    return frame, state


def sync_stock(store, provider, code, start, end, job=None, force=False):
    """provider is lazy: a fully cached request makes no network connection."""
    old, state = local_history(store, code, job)
    if state and not force and state['start'] <= start and end <= state['end']:
        store.event(job, '跳过已有行情', code=code, start=start, end=end, dataset=state['dataset_id'])
        return state['dataset_id'], 'skipped'
    left = min(start, state['start']) if state else start
    right = max(end, state['end']) if state else end

    def fetch(a, b):
        store.event(job, '请求行情区间', code=code, start=a, end=b)
        result = provider().fetch(code, a, b)
        if result.empty:
            return pd.DataFrame(columns=FIELDS)
        result = validate_bars(result)
        if (result.date < a).any() or (result.date > b).any():
            raise ValueError('行情服务返回了请求范围之外的数据')
        return result

    action = 'downloaded' if state is None else 'updated'
    if state is None or force:
        frame = fetch(left, right)
        if frame.empty:
            raise ValueError('所选范围无可用行情；未标记为已完成，可稍后重试')
        if old is not None and not set(old.date).issubset(set(frame.date)):
            raise ValueError('历史刷新返回不完整，保留原快照，未推进同步进度')
        action = 'refreshed' if force else action
    else:
        pieces = []
        if start < state['start']:
            pieces.append(fetch(left, old.date.iloc[0]))
        if end > state['end']:
            pieces.append(fetch(old.date.iloc[-1], right))
        changed = False
        for part in pieces:
            overlap = old.merge(part, on='date', suffixes=('_old', '_new'))
            if overlap.empty:
                raise ValueError('增量行情缺少价格核对日，保留原数据，请稍后重试')
            for col in FIELDS[1:]:
                if not np.allclose(overlap[col+'_old'], overlap[col+'_new'], rtol=1e-9, atol=1e-8):
                    changed = True
        if changed:
            store.event(job, '历史价格变化，刷新该股票', code=code, start=left, end=right)
            frame = fetch(left, right)
            # A truncated provider response must not replace usable history.
            if not set(old.date).issubset(set(frame.date)):
                raise ValueError('历史刷新返回不完整，保留原快照，未推进同步进度')
            action = 'refreshed'
        else:
            frame = pd.concat([old, *pieces], ignore_index=True).drop_duplicates('date', keep='last')
    did = save_dataset(store, code, frame, 'baostock', '前复权', job)
    # Do not permanently cache an unpublished trading day as an empty success.
    # A holiday/suspension may therefore recheck only the tail, never full history.
    right = min(right, frame.date.max())
    # Save progress only after the complete snapshot is durable. Never replace old files.
    store.execute('INSERT INTO sync_coverage VALUES(?,?,?,?) ON CONFLICT(code) DO UPDATE SET '
                  'dataset_id=excluded.dataset_id,start=excluded.start,end=excluded.end', (code, did, left, right))
    store.event(job, '保存同步进度', code=code, start=left, end=right, dataset=did, outcome=action)
    return did, action
