"""Dated vendor status evidence shared across sources, never shared prices.

Only an explicit tradestatus=0 certifies a whole-day halt. An absent row is
unknown; tradestatus=1 requires a real bar from the selected price provider.
Immutable receipts are checked before reuse. The index lives in schema-8 meta.
"""
from datetime import date
import hashlib
import json
import re
import math

from .store import dumps, now
from .suspensions import announcement_evidence, EVIDENCE_VERSION

DOCUMENTATION = 'https://www.baostock.com/mainContent?file=stockKData.md'
PREFIX = 'trading_status:baostock:'


def validate_status_rows(code, start, end, rows):
    if not re.fullmatch(r'(sh|sz)\.\d{6}', code):
        raise ValueError('交易状态证券代码无效')
    if any(date.fromisoformat(d).isoformat() != d for d in (start, end)) or start > end:
        raise ValueError('交易状态查询区间无效')
    result = []
    seen = set()
    for row in rows:
        day, status = row['date'], row['tradestatus']
        if (row['code'] != code or date.fromisoformat(day).isoformat() != day or
                not start <= day <= end or day in seen or status not in ('0', '1')):
            raise ValueError('交易状态日期、身份或状态无效，不能认证停牌')
        # A vendor-declared halt with nonzero/invalid volume is inconsistent.
        volume = str(row['volume'])
        if volume and (not math.isfinite(float(volume)) or float(volume) < 0):
            raise ValueError('交易状态成交量无效')
        # BaoStock legitimately returns an empty volume on halted sessions;
        # the explicit state, NOT the empty/zero volume, certifies the halt.
        if status == '0' and volume and float(volume) != 0:
            raise ValueError('停牌状态与成交量冲突，不能认证整日停牌')
        result.append(dict(code=code, date=day, tradestatus=status,
                           volume=str(row['volume'])))
        seen.add(day)
    return sorted(result, key=lambda r:r['date'])


def refresh_status_index(store, verify_files=False):
    index = {r['key'][len(PREFIX):]:json.loads(r['value'])
        for r in store.rows('SELECT key,value FROM meta WHERE key LIKE ?', (PREFIX+'%',))}
    if verify_files or index != getattr(store, '_trading_status_index', None):
        store._trading_status_facts = {}
    store._trading_status_index = index
    return store._trading_status_index


def status_facts(store, code):
    if store is None:
        return {}, []  # Pure diagnostics may supply a calendar without runtime.
    if not hasattr(store, '_trading_status_index'):
        refresh_status_index(store)
    if code not in store._trading_status_facts:
        refs = store._trading_status_index.get(code, [])
        facts, receipts = {}, []
        for ref in refs:
            path = (store.root/ref['path']).resolve()
            if not path.is_relative_to(store.root.resolve()/'evidence'/'trading_status'):
                raise ValueError('交易状态证据路径无效')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('交易状态证据校验失败，不能放行缺日')
            payload = json.loads(raw)
            if payload['source'] != 'baostock' or payload['code'] != code or payload['version'] != 1:
                raise ValueError('交易状态证据身份无效')
            rows = validate_status_rows(code, payload['start'], payload['end'], payload['rows'])
            for row in rows:
                day, value = row['date'], row['tradestatus']
                if day in facts and facts[day] != value:
                    raise ValueError('交易状态证据互相冲突，须人工核验')
                facts[day] = value
            receipts.append(dict(source='baostock', path=ref['path'], sha256=ref['sha256'],
                start=payload['start'], end=payload['end'], verified=payload['retrieved'],
                urls=[DOCUMENTATION], status_field='tradestatus',
                suspended_dates=[r['date'] for r in rows if r['tradestatus']=='0']))
        store._trading_status_facts[code] = facts, receipts
    return store._trading_status_facts[code]


def evidence_version(store, code):
    _, receipts = status_facts(store, code)
    if not receipts:
        return EVIDENCE_VERSION
    return hashlib.sha256(dumps([EVIDENCE_VERSION, receipts]).encode()).hexdigest()[:16]


def halt_evidence(store, code, start, end):
    days, receipts = announcement_evidence(code, start, end)
    facts, vendor = status_facts(store, code)
    if any(facts.get(d) == '1' for d in days):
        raise ValueError('公告与交易状态证据冲突，须人工核验')
    days = set(days)
    days.update(d for d, status in facts.items() if start <= d <= end and status == '0')
    receipts += [r for r in vendor if any(start <= d <= end for d in r['suspended_dates'])]
    return sorted(days), receipts


def save_status_rows(store, code, start, end, rows, job=None):
    """Called only with an explicit vendor response, never inferred from prices."""
    rows = validate_status_rows(code, start, end, rows)
    known, _ = status_facts(store, code)
    if any(r['date'] in known and known[r['date']] != r['tradestatus'] for r in rows):
        raise ValueError('新增交易状态与已存证据冲突，未覆盖证据')
    if not rows or all(known.get(r['date']) == r['tradestatus'] for r in rows):
        return False
    payload = dict(version=1, source='baostock', code=code, start=start, end=end,
                   retrieved=now(), rows=rows)
    raw = dumps(payload).encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest()
    return import_status_receipt(store, raw, sha, job)


def import_status_receipt(store, raw, sha, job=None):
    """Migrate an already verified status-only receipt, preserving provenance."""
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('迁入交易状态证据校验失败')
    payload = json.loads(raw)
    if set(payload) != {'version','source','code','start','end','retrieved','rows'} or (
            payload['version'] != 1 or payload['source'] != 'baostock'):
        raise ValueError('迁入证据格式或来源无效')
    from datetime import datetime
    if datetime.fromisoformat(payload['retrieved']).tzinfo is None:
        raise ValueError('证据查询时间缺少时区')
    code, start, end = payload['code'], payload['start'], payload['end']
    rows = validate_status_rows(code, start, end, payload['rows'])
    if any(set(r) != {'code','date','tradestatus','volume'} for r in payload['rows']):
        raise ValueError('交易状态证据不能包含价格或其他字段')
    known, _ = status_facts(store, code)
    if any(r['date'] in known and known[r['date']] != r['tradestatus'] for r in rows):
        raise ValueError('新增交易状态与已存证据冲突，未覆盖证据')
    if not rows or all(known.get(r['date']) == r['tradestatus'] for r in rows):
        return False
    path = f'evidence/trading_status/{sha}.json'
    refs = list(store._trading_status_index.get(code, []))
    refs.append(dict(path=path, sha256=sha))
    with store.atomic_write():
        store.write_artifact(path, raw, job)
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (PREFIX+code, dumps(refs)))
        store.event(job, '保存逐日交易状态证据（不含价格）', code=code, start=start, end=end,
                    receipt=path, suspended=sum(r['tradestatus']=='0' for r in rows))
    store._trading_status_index[code] = refs
    store._trading_status_facts.pop(code, None)
    return True
