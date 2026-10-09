"""Keep real TickFlow bars; certify scan input separately from full history.

The existing indicator adapter passes at most 300 bars to each detector and
Service requires at least 125. Unknown holes inside that input are not excused.
Quality belongs to an immutable dataset, not to a mutable stock-level flag.
"""
import json
from datetime import date, timedelta

import pandas as pd

from .market import load_dataset
from .sources import coverage_state, calendar_file, FALLBACK_SOURCES
from .store import dumps
from .suspensions import announcement_evidence, EVIDENCE_VERSION

SCAN_BARS = 300
MIN_BARS = 125


def calendar_of(store, source='tickflow'):
    path = calendar_file(store, source)
    if not path.exists():
        raise ValueError('缺少已核验交易日历，不能核对扫描区间')
    return json.loads(path.read_text(encoding='utf-8'))


def describe_history(store, code, frame, start, end, calendar=None, source='tickflow'):
    calendar = calendar or calendar_of(store, source)
    if not calendar['start'] <= start <= end <= calendar['end']:
        raise ValueError('交易日历未覆盖本次行情质量核验')
    actual = set(frame.date)
    if not actual:
        raise ValueError('没有真实 K 线，不能登记行情质量')
    trading = set(calendar['trading_days'])
    unexpected = sorted(actual - trading)
    if unexpected:
        raise ValueError('日 K 包含非交易日：'+'、'.join(unexpected[:5]))
    suspended, evidence = announcement_evidence(code, start, end)
    if actual.intersection(suspended):
        raise ValueError('日 K 与已核验整日停牌公告冲突，未保存')
    # Before the first returned bar remains unverified, never labelled pre-IPO.
    first_bar = min(actual)
    lower = max(start, first_bar)
    expected = {d for d in trading if lower <= d <= end}
    missing = sorted(expected - actual - set(suspended))
    return dict(start=start, end=end, first_bar=first_bar, last_bar=max(actual),
                missing_dates=missing, suspended_dates=suspended, evidence=evidence,
                complete=not missing, prefix_unverified=start < first_bar, evidence_version=EVIDENCE_VERSION,
                windows=scan_windows(frame, end))


def scan_windows(frame, day):
    dates = frame.loc[frame.date <= day, 'date'].tolist()
    daily = dates[-SCAN_BARS:]
    # Match weekly_bars' W-FRI labels and exclusion of unfinished weeks.
    labels = pd.to_datetime(pd.Series(dates, dtype=str)).dt.to_period('W-FRI').dt.end_time.dt.strftime('%Y-%m-%d')
    weeks = sorted(set(labels[labels <= day]))
    weekly = weeks[-SCAN_BARS:]
    return {'daily': dict(start=daily[0] if daily else day, end=daily[-1] if daily else day, rows=len(daily)),
            'weekly': dict(start=(date.fromisoformat(weekly[0])-timedelta(days=6)).isoformat() if weekly else day,
                           end=weekly[-1] if weekly else day, rows=len(weekly))}


def save_quality(store, did, quality):
    store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  ('market_quality:'+did, dumps(quality)))


def quality_for(store, record, day=None, calendar=None, cache_only=False, prefetched=None):
    if prefetched is None:
        rows = store.rows('SELECT value FROM meta WHERE key=?', ('market_quality:'+record['id'],))
        raw = rows[0]['value'] if rows else None
    else:
        raw = prefetched.get('market_quality:'+record['id'])
    quality = json.loads(raw) if raw else None
    day = day or record['end']
    # Reuse read-only checks for old snapshots across frequent GUI refreshes.
    cache = getattr(store, '_history_quality_cache', None)
    if cache is None:
        cache = store._history_quality_cache = {}
    key = (record['id'], day, EVIDENCE_VERSION)
    if quality and day == quality['end'] and quality.get('evidence_version') == EVIDENCE_VERSION:
        return quality
    if cache_only and key not in cache:
        raise ValueError('扫描区间待核验；请更新一次所选来源行情，后台登记完整性')
    if key not in cache:
        frame, _ = load_dataset(store, record['id'])
        states = coverage_state(store, record['code'], record['source'])
        start = states[0]['start'] if states and states[0]['dataset_id'] == record['id'] else record['start']
        # No future holes or future bars enter a historical-date scan gate.
        visible = frame[frame.date <= day]
        cache[key] = describe_history(store, record['code'], visible, start, day, calendar, source=record['source'])
    return cache[key]


def scan_window_issue(quality, timeframe):
    """Machine-readable current-input issue, independent of older history."""
    window = quality['windows'][timeframe]
    if window['rows'] < MIN_BARS:
        return dict(category='insufficient_bars', window=window,
                    error=f"已完成 {timeframe} K 线仅 {window['rows']} 根，至少需要 {MIN_BARS} 根")
    holes = [d for d in quality['missing_dates'] if window['start'] <= d <= window['end']]
    if holes:
        return dict(category='input_gap', dates=holes, window=window,
                    error=f"最近 {SCAN_BARS} 根 {timeframe} 输入区间缺少 {len(holes)} 个交易日（未获停牌证明）："+'、'.join(holes[:5]))
    return None


def save_cached_quality(store, dataset_ids, day):
    """Persist completed, current-snapshot checks once, never older-date slices."""
    cache = getattr(store, '_history_quality_cache', {})
    ids = set(dataset_ids)
    candidates = {did:q for (did, stamp, version),q in cache.items()
                  if did in ids and stamp == day and version == EVIDENCE_VERSION}
    if not candidates:
        return 0
    records = {}
    keys = list(candidates)
    for offset in range(0,len(keys),500):
        chunk = keys[offset:offset+500]
        records.update((r['id'],r['end']) for r in store.rows(
            'SELECT id,end FROM datasets WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk))
    rows = [('market_quality:'+did,dumps(q)) for did,q in candidates.items() if records.get(did) == day]
    if rows:
        with store.connect() as db:
            db.executemany('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',rows)
    return len(rows)


def scan_window_error(quality, timeframe):
    issue = scan_window_issue(quality, timeframe)
    return issue['error'] if issue else None


def require_research_history(store, record, day, start=None):
    if record['source'] not in FALLBACK_SOURCES:
        return
    if record['source']=='tencent' and start is not None:
        calendar=calendar_of(store,record['source'])
        if not calendar['start']<=start<=min(day,record['end'])<=calendar['end']:
            raise ValueError('交易日历未覆盖所选回测区间，不能认证历史起点')
        expected=[d for d in calendar['trading_days'] if start<=d<=min(day,record['end'])]
        halted,_=announcement_evidence(record['code'],start,min(day,record['end']))
        expected=[d for d in expected if d not in halted]
        if expected and record['start']>min(expected):
            raise ValueError('腾讯本地历史起点晚于所选回测起点；请先补拉所需区间，不能将短历史当完整回测')
    quality = quality_for(store, record, min(day, record['end']))
    if quality['missing_dates']:
        raise ValueError(f"历史研究输入有 {len(quality['missing_dates'])} 个未认证缺口；"
                         '当前扫描就绪不代表全历史完整，请核对后再研究')
