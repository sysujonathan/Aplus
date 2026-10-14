"""Safe one-day BaoStock acceleration with per-security fail-closed fallback."""
from __future__ import annotations

import contextlib
import json

import numpy as np
import pandas as pd

from .history_quality import describe_history, save_quality
from .market import FIELDS, board_of, load_dataset, save_dataset, validate_bars
from .sources import ADJUSTMENTS, calendar_file, coverage_state, save_coverage


DAILY_FIELDS = {
    'date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume',
    'adjustflag', 'tradestatus',
}
FACTOR_FIELDS = {'code', 'dividOperateDate'}


def one_day_tail_candidates(store, codes, start, end):
    """Return stocks whose verified cache ends on the preceding open session."""
    try:
        calendar = json.loads(calendar_file(store, 'baostock').read_text(encoding='utf-8'))
        days = [day for day in calendar['trading_days'] if start <= day <= end]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return []
    if not days or days[-1] != end:
        return []
    before = [day for day in calendar['trading_days'] if day < end]
    if not before:
        return []
    previous = before[-1]
    result = []
    for code in codes:
        states = coverage_state(store, code, 'baostock')
        if states and states[0]['start'] <= start and states[0]['end'] == previous:
            result.append(code)
    return result


def validate_daily_archive(frame, day):
    data = frame.copy()
    data.columns = [str(column).strip() for column in data.columns]
    missing = DAILY_FIELDS - set(data.columns)
    if missing:
        raise ValueError('BaoStock 整日行情缺少字段：' + '、'.join(sorted(missing)))
    if data.empty or data.code.duplicated().any():
        raise ValueError('BaoStock 整日行情为空或证券代码重复')
    if not data.date.astype(str).eq(day).all():
        raise ValueError('BaoStock 整日行情日期与请求日期不一致')
    # The official all-stock archive is broader than Aplus's board roster
    # (e.g. 689 CDR / 302 securities). Validate identities first, then select
    # supported boards exactly as DirectBaoStock.universe does. An unrelated
    # security must not force thousands of valid tails onto per-stock queries.
    if not data.code.astype(str).str.fullmatch(r'(sh|sz|bj)\.\d{6}').all():
        raise ValueError('BaoStock 整日行情证券代码格式无效')
    data = data.loc[data.code.map(board_of).notna()].copy()
    if data.empty:
        raise ValueError('BaoStock 整日行情未返回支持范围内证券')
    if not data.adjustflag.astype(str).eq('3').all():
        raise ValueError('BaoStock 整日行情不是明确的不复权口径')
    if not data.tradestatus.astype(str).isin(['0', '1']).all():
        raise ValueError('BaoStock 整日行情存在未知交易状态')
    for column in ('open', 'high', 'low', 'close', 'preclose', 'volume'):
        data[column] = pd.to_numeric(data[column], errors='coerce')
    if not np.isfinite(data[['open', 'high', 'low', 'close', 'preclose', 'volume']].to_numpy()).all():
        raise ValueError('BaoStock 整日行情存在非数字或无穷值')
    return data.set_index('code', drop=False)


def validate_factor_archive(frame, day):
    data = frame.copy()
    data.columns = [str(column).strip() for column in data.columns]
    if data.empty:
        return set()
    missing = FACTOR_FIELDS - set(data.columns)
    if missing:
        raise ValueError('BaoStock 复权事件清单缺少字段：' + '、'.join(sorted(missing)))
    if data.code.duplicated().any() or not data.dividOperateDate.astype(str).eq(day).all():
        raise ValueError('BaoStock 复权事件清单日期不符或证券重复')
    return set(data.code.astype(str))


def apply_one_day_tail(store, codes, archive, factors, start, end, job=None,
                       batch_writes=True, on_progress=None, check_stop=None):
    """Append verified raw latest bars; return successes and fallback reasons.

    BaoStock front-adjustment leaves the newest bar at raw price.  An effective
    corporate action or a changed previous close must use the original per-code
    path, which can refresh all affected history.
    """
    rows = validate_daily_archive(archive, end)
    action_codes = validate_factor_archive(factors, end)
    saved, fallback = {}, {}
    progress = on_progress or (lambda done, total, code, outcome: None)
    stop = check_stop or (lambda: None)
    for index, code in enumerate(codes, 1):
        stop()
        if code in action_codes:
            fallback[code] = '当日存在复权事件'
            progress(index, len(codes), code, 'fallback')
            continue
        if code not in rows.index:
            fallback[code] = '整日行情未返回该证券'
            progress(index, len(codes), code, 'fallback')
            continue
        row = rows.loc[code]
        if str(row.tradestatus) != '1':
            fallback[code] = '整日行情与当日目录的交易状态不一致'
            progress(index, len(codes), code, 'fallback')
            continue
        try:
            state = coverage_state(store, code, 'baostock')[0]
            old, _record = load_dataset(store, state['dataset_id'], job)
            previous_close = float(row.preclose)
            if not np.isclose(float(old.iloc[-1].close), previous_close,
                              rtol=1e-7, atol=5e-4):
                fallback[code] = '前收盘与既有前复权尾部不一致'
                progress(index, len(codes), code, 'fallback')
                continue
            current = validate_bars(pd.DataFrame([{key: row[key] for key in FIELDS}]))
            if current.iloc[0].date != end or end <= old.iloc[-1].date:
                fallback[code] = '整日行情不能安全接到既有尾部'
                progress(index, len(codes), code, 'fallback')
                continue
            combined = pd.concat([old, current], ignore_index=True)
            combined = validate_bars(combined)
            left = min(start, state['start'])
            quality = describe_history(store, code, combined, left, end, source='baostock')
            with store.atomic_write() if batch_writes else contextlib.nullcontext():
                did = save_dataset(store, code, combined, 'baostock', ADJUSTMENTS['baostock'], job)
                save_quality(store, did, quality)
                save_coverage(store, code, did, left, end, 'baostock')
                store.event(job, '整日行情安全追加', code=code, date=end,
                            dataset=did, check='前收盘一致且当日无复权事件')
            saved[code] = did
            progress(index, len(codes), code, 'saved')
        except InterruptedError:
            raise
        except Exception as exc:
            fallback[code] = f'本地校验未通过：{exc}'
            progress(index, len(codes), code, 'fallback')
    return saved, fallback
