"""Daily coverage gate. Counts stocks, never mistakes processing for completeness."""
import json

import pandas as pd

from .market import latest_datasets, select_board_codes, board_of
from .store import dumps
from .sources import market_source, source_file, directory_file, directory_date, calendar_file


def save_directory(store, directory, day, job=None, basics=None, retired_codes=None, source='baostock'):
    """Never silently reduce an established universe after a short provider reply."""
    if directory.empty or 'code' not in directory or directory.code.duplicated().any():
        raise ValueError('股票目录为空、缺代码或有重复，未覆盖原目录')
    retired = set(retired_codes or [])
    if basics is not None:
        required = {'code','ipoDate','outDate','type','status'}
        if not required.issubset(basics.columns) or basics.code.duplicated().any():
            raise ValueError('证券档案缺字段或重复，不能判断上市与退市')
        basics = basics.fillna('').astype(str)
        stocks = basics[(basics.type == '1') & basics.code.map(board_of).notna()]
        for column in ['ipoDate','outDate']:
            values = stocks.loc[stocks[column] != '',column]
            if pd.to_datetime(values,format='%Y-%m-%d',errors='coerce').isna().any():
                raise ValueError('证券档案日期无效，不能判断上市与退市')
        retired = set(stocks.loc[(stocks.outDate != '') & (stocks.outDate <= day), 'code'])
        eligible = stocks[(stocks.ipoDate != '') & (stocks.ipoDate <= day) &
                          ((stocks.outDate == '') | (stocks.outDate > day))]
        missing_active = sorted(set(eligible.code) - set(directory.code))
        unexplained = sorted(set(directory.code) - set(eligible.code))
        if missing_active or unexplained:
            raise ValueError(f'当日目录与证券档案不一致：应在市却缺少 {len(missing_active)} 只，状态待核对 {len(unexplained)} 只；'
                             + '、'.join((missing_active+unexplained)[:8]) + '。不会自动视为退市，请稍后重试')
        if not {'tradeStatus','code_name'}.issubset(directory.columns) or not directory.tradeStatus.astype(str).isin(['0','1']).all():
            raise ValueError('股票目录缺少名称或有效交易状态，拒绝猜测停牌')
    path = directory_file(store, source)
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        removed = set(old.code) - set(directory.code)
        missing = sorted(removed - retired)
        if missing:
            store.event(job, '股票目录缩减待核对', expected=len(old), received=len(directory), missing=missing)
            raise ValueError(f'股票目录原有 {len(old)} 只，本次返回 {len(directory)} 只，缺少 {len(missing)} 只：'
                             + '、'.join(missing[:8]) + '。未覆盖原目录；可能是接口不完整或退市变化，需核对，不能自动缩小扫描范围')
        if removed & retired:
            store.event(job,'确认退市，保留历史与关注记录',date=day,codes=sorted(removed & retired))
        added = set(directory.code) - set(old.code)
        if added:
            store.event(job,'新增上市股票',date=day,codes=sorted(added))
    if basics is not None:
        store.write_artifact(f'directories/{day}-basics.csv', basics.to_csv(index=False).encode('utf-8-sig'), job)
    for name in (f'directories/{day}.csv', 'universe.csv'):
        store.write_artifact(str(source_file(store, name, source).relative_to(store.root)),
                             directory.to_csv(index=False).encode('utf-8-sig'), job)
    key = 'universe_date:tickflow' if source == 'tickflow' else 'universe_date'
    store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, day))


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


def expected_day(store, asof, source='baostock'):
    try:
        calendar = json.loads(calendar_file(store, source).read_text(encoding='utf-8'))
        if not calendar['start'] <= asof <= calendar['end']:
            raise ValueError('交易日历尚未覆盖所选日期，请先同步市场数据')
        days = [d for d in calendar['trading_days'] if d <= asof]
        if not days:
            raise ValueError('所选日期之前没有可核验的交易日')
        return max(days)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('缺少有效交易日历，请先同步一次市场数据') from exc


def extend_exchange_calendar(store,start,end,job=None,source='baostock'):
    """Keep verified historical dates; extend only published exchange years."""
    from datetime import date,timedelta
    from .exchange_calendar import is_trading_day, SCHEDULES
    path=calendar_file(store, source)
    old=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if old:
        if old['start']>start:
            raise ValueError('本地已核验交易日历缺少更早区间；不能从缺失 K 线推测休市')
        left=old['start']; days=set(old['trading_days'])
        cursor=date.fromisoformat(old['end'])+timedelta(days=1)
        if old['end']>=end:
            target=source_file(store, 'trading_calendar.json', source)
            if not target.exists():
                store.write_artifact(str(target.relative_to(store.root)),dumps(old).encode(),job)
            return old
    else:
        left=start; days=set(); cursor=date.fromisoformat(start)
    last=date.fromisoformat(end)
    while cursor<=last:
        if cursor.year not in SCHEDULES:
            raise ValueError(f'{cursor.year} 年缺少已核验历史交易日历；请迁入已有 trading_calendar.json，不能把缺数据当休市')
        if is_trading_day(cursor):
            days.add(cursor.isoformat())
        cursor+=timedelta(days=1)
    payload=dict(start=left,end=end,trading_days=sorted(days),extension_source='交易所已公布休市安排')
    store.write_artifact(str(source_file(store,'trading_calendar.json',source).relative_to(store.root)),dumps(payload).encode(),job)
    return payload


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


def audit_scope(store, boards, asof, dataset_ids=None, source=None, timeframe=None, history_cache_only=False):
    """Cheap UI check; actual files still hash-validated before strategy execution.

    Suspension is excused only by the directory for this exact trading day.
    This gate establishes stock coverage and freshness, NOT every historical bar.
    """
    source=source or market_source(store)
    report = {'expected': 0, 'ready': 0, 'suspended': 0, 'gaps': [], 'source':source,
              'eligible_ids': [], 'expected_day': None, 'boards': boards,
              'warnings': [], 'scope_valid': False}
    try:
        day = expected_day(store, asof, source)
        report['expected_day'] = day
        directory = pd.read_csv(directory_file(store, source), dtype=str)
        if 'code' not in directory:
            raise ValueError('股票目录缺少代码列，请重新获取目录')
        codes = select_board_codes(directory, boards)
        report['expected'] = len(codes)
        directory_day = directory_date(store, source)
        if directory_day != day:
            raise ValueError(f'股票目录日期为 {directory_day or "未核验"}，应为 {day}；请同步该日期行情，避免遗漏新股或误判停牌')
        directory = directory.drop_duplicates('code').set_index('code')
        report['scope_valid'] = True
        if source == 'tickflow':
            from .history_quality import calendar_of, quality_for, scan_window_error
            calendar = calendar_of(store)
            if timeframe is not None and timeframe not in ('daily', 'weekly'):
                raise ValueError('TickFlow 日常扫描只支持日线或周线')
        required_day = day
        if source == 'tickflow' and timeframe == 'weekly':
            from datetime import date, timedelta
            current = date.fromisoformat(day)
            friday = (current-timedelta(days=(current.weekday()-4)%7)).isoformat()
            finished = [d for d in calendar['trading_days'] if d <= friday]
            if not finished:
                raise ValueError('所选日期之前没有已完成周线')
            required_day = max(finished)
        records = latest_datasets(store, source)
        if dataset_ids is not None:
            ids = set(dataset_ids)
            records = store.rows("SELECT * FROM datasets WHERE source=? AND timeframe='daily'",(source,))
            records = [r for r in records if r['id'] in ids]
        records = {r['code']: r for r in records if r['timeframe'] == 'daily'}
        qualities = None
        if source == 'tickflow' and timeframe is not None:
            # UI refresh must not open one SQLite connection per stock.
            keys = ['market_quality:'+r['id'] for r in records.values()]
            qualities = {}
            for offset in range(0,len(keys),500):
                chunk=keys[offset:offset+500]
                qualities.update((r['key'],r['value']) for r in store.rows(
                    'SELECT key,value FROM meta WHERE key IN ('+','.join('?' for _ in chunk)+')',chunk))
        for code in codes:
            if source == 'tickflow':
                from .suspensions import announcement_evidence
                if day in announcement_evidence(code, day, day)[0]:
                    report['suspended'] += 1
                    continue
            if 'tradeStatus' in directory.columns and directory.loc[code, 'tradeStatus'] == '0':
                report['suspended'] += 1
                continue
            r = records.get(code)
            reason = None
            if r is None:
                reason = '本地缺少行情'
            elif not (store.root/r['path']).is_file():
                reason = '行情文件缺失'
            elif r['end'] < required_day:
                reason = f"行情只到 {r['end']}，应到 {required_day}（不能自动假定停牌）"
            elif r['start'] > day:
                reason = '所选日期尚无本地行情'
            if reason is None and source == 'tickflow' and timeframe is not None:
                try:
                    quality = quality_for(store, r, day, calendar, cache_only=history_cache_only,prefetched=qualities)
                    reason = scan_window_error(quality, timeframe)
                    if quality['missing_dates']:
                        report['warnings'].append(dict(code=code, dataset=r['id'],
                            history_gaps=len(quality['missing_dates']), input_start=quality['windows'][timeframe]['start'],
                            error='历史有未认证缺口；不等于停牌，历史研究仍须核对'))
                except (OSError, ValueError, KeyError) as exc:
                    reason = str(exc)
            if reason:
                report['gaps'].append({'code': code, 'error': reason})
            else:
                report['ready'] += 1
                report['eligible_ids'].append(r['id'])
    except (OSError, ValueError, KeyError) as exc:
        report['scope_valid'] = False
        report['gaps'].append({'code': '范围核验', 'error': str(exc)})
    report['complete'] = not report['gaps'] and report['expected'] > 0
    # Only the independent fallback permits explicitly reported stock exclusions.
    # A bad/stale directory or calendar still blocks the whole task.
    report['scan_allowed'] = report['complete'] if source != 'tickflow' else (
        report['scope_valid'] and report['ready'] > 0)
    return report


def filter_tickflow_directory(store, directory, day, job=None):
    """Exclude only retired codes proven by an existing dated security archive.

    No BaoStock network dependency. Unknown listings stay visible for per-stock
    validation; missing bars or a name containing '退' are not retirement proof.
    """
    files = sorted((store.root/'directories').glob('*-basics.csv'))
    files = [p for p in files if p.name[:10] <= day]
    retired = set()
    if files:
        basics = pd.read_csv(files[-1], dtype=str).fillna('')
        if not {'code', 'outDate', 'type'}.issubset(basics.columns):
            raise ValueError('本地证券档案缺少退市证据字段')
        rows = basics[(basics.type == '1') & (basics.outDate != '')].copy()
        if pd.to_datetime(rows.outDate, format='%Y-%m-%d', errors='coerce').isna().any():
            raise ValueError('证券档案退市日期无效，未缩小目录')
        retired = set(rows.loc[rows.outDate <= day, 'code'])
    excluded = sorted(set(directory.code) & retired)
    if excluded:
        store.event(job, 'TickFlow 目录排除已确认退市证券', codes=excluded,
                    evidence=str(files[-1].relative_to(store.root)), archive_date=files[-1].name[:10])
    return directory[~directory.code.isin(retired)].reset_index(drop=True), retired, excluded
