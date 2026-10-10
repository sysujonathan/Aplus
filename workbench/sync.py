"""Incremental refresh with immutable snapshots and overlap adjustment checks."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from .market import FIELDS, load_dataset, save_dataset, validate_bars, verify_dataset
from .readiness import check_response_dates
from .sources import coverage_state, save_coverage, FALLBACK_SOURCES, ADJUSTMENTS, calendar_file


def local_history(store, code, job, coverage=None, source='baostock'):
    coverage = coverage if coverage is not None else coverage_state(store,code,source)
    if coverage:
        state = coverage[0]
        frame, _ = load_dataset(store, state['dataset_id'], job)
        return frame, state
    records = store.rows("SELECT * FROM datasets WHERE code=? AND source=? "
                         "AND adjustment=? AND timeframe='daily' "
                         "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1", (code,source,ADJUSTMENTS[source]))
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


def sync_stock(store, provider, code, start, end, job=None, force=False, source='baostock', batch_writes=False,
               on_request=None):
    """provider is lazy: a fully cached request makes no network connection."""
    coverage = coverage_state(store,code,source)
    track_quality = source in FALLBACK_SOURCES or calendar_file(store, source).is_file()
    state = coverage[0] if coverage else None
    notify = on_request or (lambda operation, a, b: None)
    if state and not force and state['start'] <= start and end <= state['end']:
        # Preserve the tamper gate while avoiding pandas CSV parsing and the
        # full OHLCV validation pass for every unchanged stock.
        notify('cached', None, None)
        verify_dataset(store, state['dataset_id'], job)
        if track_quality:
            from .history_quality import quality_for, save_quality
            record = store.rows('SELECT * FROM datasets WHERE id=?', (state['dataset_id'],))[0]
            save_quality(store, record['id'], quality_for(store, record))
        store.event(job, '跳过已有行情', code=code, start=start, end=end,
                    dataset=state['dataset_id'], integrity='sha256')
        return state['dataset_id'], 'skipped'
    old, state = local_history(store, code, job, coverage,source)
    # A v1 runtime has no sync_coverage row.  Its last successful immutable
    # snapshot is recovered above once, then future runs use the fast path.
    if state and not force and state['start'] <= start and end <= state['end']:
        notify('cached', None, None)
        save_coverage(store,code,state['dataset_id'],state['start'],state['end'],source)
        if track_quality:
            from .history_quality import describe_history, save_quality
            save_quality(store, state['dataset_id'], describe_history(store, code, old, state['start'], state['end'], source=source))
        store.event(job, '跳过已有行情', code=code, start=start, end=end, dataset=state['dataset_id'])
        return state['dataset_id'], 'skipped'
    left = min(start, state['start']) if state else start
    right = max(end, state['end']) if state else end

    def fetch(a, b, operation):
        notify(operation, a, b)
        store.event(job, '请求行情区间', code=code, start=a, end=b)
        result = provider().fetch(code, a, b)
        if result.attrs.get('suspension_evidence'):
            store.event(job,'核对公开停牌证据',code=code,source=source,
                        evidence=result.attrs['suspension_evidence'])
        if source not in FALLBACK_SOURCES:
            check_response_dates(store, result, a, b)
        if source == 'baostock' and result.attrs.get('trading_status_rows'):
            from .trading_status import save_status_rows
            save_status_rows(store, code, a, b, result.attrs['trading_status_rows'], job)
        if result.empty:
            return pd.DataFrame(columns=FIELDS)
        result = validate_bars(result)
        if (result.date < a).any() or (result.date > b).any():
            raise ValueError('行情服务返回了请求范围之外的数据')
        return result

    action = 'downloaded' if state is None else 'updated'
    if state is None or force:
        frame = fetch(left, right, 'repair' if force else 'history')
        if frame.empty:
            raise ValueError('所选范围无可用行情；未标记为已完成，可稍后重试')
        if old is not None and not set(old.date).issubset(set(frame.date)):
            raise ValueError('历史刷新返回不完整，保留原快照，未推进同步进度')
        action = 'refreshed' if force else action
    else:
        pieces = []
        if start < state['start']:
            pieces.append(fetch(left, old.date.iloc[0], 'prefix'))
        if end > state['end']:
            pieces.append(fetch(old.date.iloc[-1], right, 'tail'))
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
            frame = fetch(left, right, 'refresh')
            # A truncated provider response must not replace usable history.
            if not set(old.date).issubset(set(frame.date)):
                raise ValueError('历史刷新返回不完整，保留原快照，未推进同步进度')
            action = 'refreshed'
        else:
            frame = pd.concat([old, *pieces], ignore_index=True).drop_duplicates('date', keep='last')
    quality = None
    if track_quality:
        from .history_quality import describe_history, save_quality
        quality = describe_history(store, code, frame, left, right, source=source)
    import contextlib
    # Network and quality checks are complete before entering this short unit.
    # BaoStock retains its original behavior. TickFlow explicitly opts in.
    with store.atomic_write() if batch_writes else contextlib.nullcontext():
        did = save_dataset(store, code, frame, source, ADJUSTMENTS[source], job)
        if quality is not None:
            save_quality(store, did, quality)
            if quality['missing_dates']:
                store.event(job, '保存真实行情，历史缺口未认证', code=code, dataset=did,
                            missing_dates=quality['missing_dates'], count=len(quality['missing_dates']))
        # Never cache an unpublished day as an empty success.
        right = min(right, frame.date.max())
        save_coverage(store,code,did,left,right,source)
        store.event(job, '保存同步进度', code=code, start=left, end=right, dataset=did, outcome=action)
    return did, action
