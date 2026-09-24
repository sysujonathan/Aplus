"""Daily coverage gate. Counts stocks, never mistakes processing for completeness."""
import json

import pandas as pd

from .market import latest_datasets, select_board_codes
from .store import dumps


def save_directory(store, directory, day, job=None):
    """Never silently reduce an established universe after a short provider reply."""
    if directory.empty or 'code' not in directory or directory.code.duplicated().any():
        raise ValueError('股票目录为空、缺代码或有重复，未覆盖原目录')
    path = store.root/'universe.csv'
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        missing = sorted(set(old.code) - set(directory.code))
        if missing:
            store.event(job, '股票目录缩减待核对', expected=len(old), received=len(directory), missing=missing)
            raise ValueError(f'股票目录原有 {len(old)} 只，本次返回 {len(directory)} 只，缺少 {len(missing)} 只：'
                             + '、'.join(missing[:8]) + '。未覆盖原目录；可能是接口不完整或退市变化，需核对，不能自动缩小扫描范围')
    store.write_artifact('universe.csv', directory.to_csv(index=False).encode('utf-8-sig'), job)
    store.execute("INSERT INTO meta VALUES('universe_date',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))


def save_calendar(store, frame, start, end, job=None):
    days = frame[['calendar_date', 'is_trading_day']].copy()
    days['calendar_date'] = pd.to_datetime(days.calendar_date).dt.strftime('%Y-%m-%d')
    expected = set(pd.date_range(start, end).strftime('%Y-%m-%d'))
    if set(days.calendar_date) != expected or days.calendar_date.duplicated().any():
        raise ValueError('交易日历返回不完整，不能推断行情应有日期')
    flags = days.is_trading_day.astype(str)
    if not flags.isin(['0', '1']).all():
        raise ValueError('交易日历状态无法识别')
    payload = {'start': start, 'end': end,
               'trading_days': sorted(days.loc[flags == '1', 'calendar_date'].tolist())}
    store.write_artifact('trading_calendar.json', dumps(payload).encode(), job)
    return payload


def expected_day(store, asof):
    try:
        calendar = json.loads((store.root/'trading_calendar.json').read_text(encoding='utf-8'))
        if not calendar['start'] <= asof <= calendar['end']:
            raise ValueError('交易日历尚未覆盖所选日期，请先同步市场数据')
        days = [d for d in calendar['trading_days'] if d <= asof]
        if not days:
            raise ValueError('所选日期之前没有可核验的交易日')
        return max(days)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('缺少有效交易日历，请先同步一次市场数据') from exc


def check_response_dates(store, frame, start, end):
    """Reject unexplained holes in returned history; do not invent suspension bars.

    Pre-first-return history is NOT certified: an IPO date is not inferred from
    absence. Legacy caches are not retroactively certified by this check.
    """
    path = store.root/'trading_calendar.json'
    if not path.exists():
        return  # Legacy/direct import callers have no provider calendar evidence.
    calendar = json.loads(path.read_text(encoding='utf-8'))
    if not calendar['start'] <= start <= end <= calendar['end']:
        raise ValueError('交易日历未覆盖本次请求，未推进同步进度')
    received = set(frame.attrs.get('returned_dates', frame.date.tolist() if not frame.empty else []))
    if not received:
        raise ValueError('接口未返回任何交易日记录，不能假定为停牌或同步成功')
    suspended = set(frame.attrs.get('suspended_dates', []))
    actual = set(frame.date) if not frame.empty else set()
    expected = {d for d in calendar['trading_days'] if max(start, min(received)) <= d <= end}
    missing = sorted(expected - actual - suspended)
    if missing:
        raise ValueError(f'行情区间缺少 {len(missing)} 个应有交易日（未获停牌证明）：'
                         + '、'.join(missing[:5]) + '；保留原快照，请重试')


def audit_scope(store, boards, asof, dataset_ids=None):
    """Cheap UI check; actual files still hash-validated before strategy execution.

    Suspension is excused only by the directory for this exact trading day.
    This gate establishes stock coverage and freshness, NOT every historical bar.
    """
    report = {'expected': 0, 'ready': 0, 'suspended': 0, 'gaps': [],
              'eligible_ids': [], 'expected_day': None, 'boards': boards}
    try:
        day = expected_day(store, asof)
        report['expected_day'] = day
        directory = pd.read_csv(store.root/'universe.csv', dtype=str)
        if 'code' not in directory:
            raise ValueError('股票目录缺少代码列，请重新获取目录')
        codes = select_board_codes(directory, boards)
        report['expected'] = len(codes)
        meta = store.rows("SELECT value FROM meta WHERE key='universe_date'")
        directory_day = meta[0]['value'] if meta else None
        if directory_day != day:
            raise ValueError(f'股票目录日期为 {directory_day or "未核验"}，应为 {day}；请同步该日期行情，避免遗漏新股或误判停牌')
        directory = directory.drop_duplicates('code').set_index('code')
        records = latest_datasets(store, 'baostock')
        if dataset_ids is not None:
            ids = set(dataset_ids)
            records = store.rows("SELECT * FROM datasets WHERE source='baostock' AND timeframe='daily'")
            records = [r for r in records if r['id'] in ids]
        records = {r['code']: r for r in records if r['timeframe'] == 'daily'}
        for code in codes:
            if 'tradeStatus' in directory.columns and directory.loc[code, 'tradeStatus'] == '0':
                report['suspended'] += 1
                continue
            r = records.get(code)
            reason = None
            if r is None:
                reason = '本地缺少行情'
            elif not (store.root/r['path']).is_file():
                reason = '行情文件缺失'
            elif r['end'] < day:
                reason = f"行情只到 {r['end']}，应到 {day}（不能自动假定停牌）"
            elif r['start'] > day:
                reason = '所选日期尚无本地行情'
            if reason:
                report['gaps'].append({'code': code, 'error': reason})
            else:
                report['ready'] += 1
                report['eligible_ids'].append(r['id'])
    except (OSError, ValueError, KeyError) as exc:
        report['gaps'].append({'code': '范围核验', 'error': str(exc)})
    report['complete'] = not report['gaps'] and report['expected'] > 0
    return report
