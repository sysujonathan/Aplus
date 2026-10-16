"""Incremental refresh with immutable snapshots and overlap adjustment checks."""
from __future__ import annotations

import json
from bisect import bisect_left
import contextlib
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


def tail_pending(store, code, last, end, source='baostock', calendar=None):
    """Only explicit dated halts or verified closed sessions excuse absent bars."""
    if last >= end:
        return False
    try:
        if calendar is None:
            calendar = json.loads(calendar_file(store, source).read_text(encoding='utf-8'))
        if (not isinstance(calendar, dict) or
                not calendar['start'] <= last <= end <= calendar['end']):
            return True
        days = calendar['trading_days']
        if not isinstance(days, list) or any(not isinstance(day, str) for day in days):
            return True
        needed = {day for day in days if last < day <= end}
    except (OSError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        return True
    if not needed:
        return False
    from .trading_status import halt_evidence
    # Corrupt/conflicting status evidence fails closed BEFORE another request.
    # Do not turn an evidence error into the calendar's conservative fallback.
    halted, _ = halt_evidence(store, code, last, end)
    return bool(needed - set(halted))


def repair_intervals(store, code, old, start, end, timeframe, source='baostock'):
    """Unknown holes in the selected scan input, bounded by real price anchors."""
    from .history_quality import describe_history
    quality = describe_history(store, code, old, start, end, source=source)
    window = quality['windows'][timeframe]
    dates = old.date.tolist()
    intervals = []
    for day in quality['missing_dates']:
        if not window['start'] <= day <= window['end']:
            continue
        i = bisect_left(dates, day)
        # Prefix/tail are handled separately. Missing dates before the first
        # real bar do not prove an IPO date, nor have two price anchors.
        if i == 0 or i == len(dates):
            continue
        a, b = dates[i-1], dates[i]
        if intervals and a <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(b, intervals[-1][1]))
        else:
            intervals.append((a, b))
    # Fragmented legacy gaps must not turn one stock into hundreds of calls.
    if len(intervals) > 4:
        intervals = [(intervals[0][0], intervals[-1][1])]
    return intervals


def sync_stock(store, provider, code, start, end, job=None, force=False, source='baostock', batch_writes=False,
               on_request=None, repair_timeframe=None):
    """provider is lazy: a fully cached request makes no network connection."""
    if repair_timeframe is not None and repair_timeframe not in ('daily', 'weekly'):
        raise ValueError('定向缺口补拉须选择日线或周线')
    coverage = coverage_state(store,code,source)
    track_quality = source in FALLBACK_SOURCES or calendar_file(store, source).is_file()
    state = coverage[0] if coverage else None
    notify = on_request or (lambda operation, a, b: None)
    if state and not force and repair_timeframe is None and state['start'] <= start and end <= state['end']:
        # Preserve the tamper gate while avoiding pandas CSV parsing and the
        # full OHLCV validation pass for every unchanged stock.
        notify('cached', None, None)
        verify_dataset(store, state['dataset_id'], job)
        quality = None
        if track_quality:
            from .history_quality import quality_for, save_quality
            record = store.rows('SELECT * FROM datasets WHERE id=?', (state['dataset_id'],))[0]
            quality = quality_for(store, record)
        with store.atomic_write() if batch_writes else contextlib.nullcontext():
            if quality is not None:
                save_quality(store, state['dataset_id'], quality)
            store.event(job, '跳过已有行情', code=code, start=start, end=end,
                        dataset=state['dataset_id'], integrity='sha256')
        return state['dataset_id'], 'skipped'
    old, state = local_history(store, code, job, coverage,source)
    # A v1 runtime has no sync_coverage row.  Its last successful immutable
    # snapshot is recovered above once, then future runs use the fast path.
    if state and not force and repair_timeframe is None and state['start'] <= start and end <= state['end']:
        notify('cached', None, None)
        quality = None
        if track_quality:
            from .history_quality import describe_history, save_quality
            quality = describe_history(store, code, old, state['start'], state['end'], source=source)
        with store.atomic_write() if batch_writes else contextlib.nullcontext():
            save_coverage(store,code,state['dataset_id'],state['start'],state['end'],source)
            if quality is not None:
                save_quality(store, state['dataset_id'], quality)
            store.event(job, '跳过已有行情', code=code, start=start, end=end, dataset=state['dataset_id'])
        return state['dataset_id'], 'skipped'
    left = min(start, state['start']) if state else start
    right = max(end, state['end']) if state else end

    if (source == 'baostock' and state and not force
            and (start < state['start'] or repair_timeframe is not None)
            and tail_pending(store, code, state['end'], end)):
        # Commit usable latest bars before attempting the earlier extension.
        # A failed prefix cannot roll back a successfully verified new tail.
        from .readiness import expected_day
        target_end = expected_day(store, end, source) if calendar_file(store, source).is_file() else end
        tail_id, tail_action = sync_stock(store, provider, code, state['start'], target_end, job,
            source=source, batch_writes=batch_writes, on_request=on_request)
        updated = coverage_state(store, code, source)[0]
        if tail_pending(store, code, updated['end'], target_end):
            exc = ValueError('最新尾部尚未完整返回，保留已保存行情；未开始补齐早期历史或历史缺口')
            if updated['end'] > state['end']:
                exc.latest_dataset_id = tail_id
            raise exc
        try:
            did, action = sync_stock(store, provider, code, start, target_end, job,
                source=source, batch_writes=batch_writes, on_request=on_request,
                repair_timeframe=repair_timeframe)
        except Exception as exc:
            exc.latest_dataset_id = tail_id
            raise
        return did, 'refreshed' if tail_action == 'refreshed' else (tail_action if action == 'skipped' else action)

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
        if source == 'baostock' and tail_pending(store, code, state['end'], end):
            pieces.append(fetch(old.date.iloc[-1], right, 'tail'))
        if start < state['start']:
            pieces.append(fetch(left, old.date.iloc[0], 'prefix'))
        if source != 'baostock' and tail_pending(store,code,state['end'],end,source):
            pieces.append(fetch(old.date.iloc[-1], right, 'tail'))
        if repair_timeframe is not None:
            for a, b in repair_intervals(store, code, old, left, end, repair_timeframe, source):
                part = fetch(a, b, 'repair')
                if not set(old.loc[old.date.between(a, b), 'date']).issubset(set(part.date)):
                    raise ValueError('局部补拉返回不完整，保留原快照，未推进同步进度')
                pieces.append(part)
        if not pieces:
            notify('cached', None, None)
            from .history_quality import describe_history, save_quality
            quality = describe_history(store, code, old, left, right, source=source)
            with store.atomic_write() if batch_writes else contextlib.nullcontext():
                save_quality(store, state['dataset_id'], quality)
                save_coverage(store, code, state['dataset_id'], left, min(right, old.date.max()), source)
                store.event(job, '跳过已有行情', code=code, start=start, end=end,
                            dataset=state['dataset_id'], repair_timeframe=repair_timeframe)
            return state['dataset_id'], 'skipped'
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
    # Network and quality checks are complete before entering this short unit.
    # Each opting-in caller commits one stock, never a whole market batch.
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
