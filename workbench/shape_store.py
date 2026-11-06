"""Private derived research records, accessed only through Store's public API.

Uses the existing meta/artifact transaction contract. No new schema, second
watchlist, formal observations or plans. Immutable versions/runs remain readable.
"""
from __future__ import annotations

from datetime import date
import json
import math
import re
import uuid

from .store import digest, dumps, now
from .shape_matching import ALGORITHM, WINDOWS, normalize


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w-]{1,80}', value):
        raise ValueError('形态记录编号无效')
    return value


def read_meta(store, key, default=None):
    rows = store.rows('SELECT value FROM meta WHERE key=?', (key,))
    return json.loads(rows[0]['value']) if rows else default


def write_meta(store, key, value):
    store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, dumps(value)))


def options(value=None):
    value = dict(value or {})
    window = int(value.get('window',60))
    threshold = float(value.get('threshold',75))
    topn = int(value.get('topn',30))
    if window not in WINDOWS or not math.isfinite(threshold) or not 0 <= threshold <= 100 or not 1 <= topn <= 200:
        raise ValueError('长度须为30／60／120，阈值为0—100，结果数为1—200')
    from .market import BOARDS, code_of
    boards = value.get('boards') or list(BOARDS)
    if not isinstance(boards,list) or not boards or any(b not in BOARDS for b in boards):
        raise ValueError('股票范围无效')
    codes = value.get('codes') or []
    if not isinstance(codes,list) or len(codes) > 10000:
        raise ValueError('股票代码范围无效')
    return dict(window=window, threshold=threshold, topn=topn, boards=boards,
                codes=sorted(set(code_of(v) for v in codes)))


def templates(store):
    rows = store.rows("SELECT value FROM meta WHERE key LIKE 'shape_head:%' ORDER BY key")
    result = []
    for row in rows:
        head = json.loads(row['value'])
        version = read_meta(store, f"shape_template:{head['id']}:{head['version']}")
        if version:
            result.append(dict(version, tracking=head['tracking'], settings=head['settings'], revision=head['revision']))
    return result


def save_template(store, value, expected_revision=None):
    identifier = _id(value.get('id') or uuid.uuid4().hex)
    name = str(value.get('name') or '').strip()
    if not name or len(name) > 80 or any(ord(c)<32 for c in name):
        raise ValueError('请输入80字以内的模板名')
    kind = value.get('kind')
    if kind not in ('classic','drawing','case','chart','screenshot'):
        raise ValueError('模板来源无效')
    path = normalize(value.get('path')).round(10).tolist()
    origin = dict(value.get('origin') or {})
    if len(dumps(origin)) > 50000:
        raise ValueError('模板来源信息过大')
    # Case/chart templates are only accepted with a real verified market prefix.
    if kind in ('case','chart'):
        from .market import load_dataset
        frame, record = load_dataset(store, origin.get('dataset_id'))
        start, cutoff = str(origin.get('start') or ''), str(origin.get('cutoff') or '')
        date.fromisoformat(start); date.fromisoformat(cutoff)
        clip = frame[(frame.date >= start) & (frame.date <= cutoff)]
        if len(clip) < 3 or start > cutoff or record['sha256'] != origin.get('sha256'):
            raise ValueError('案例行情区间或快照校验不一致')
        path = normalize(clip.close).round(10).tolist()
        origin.update(code=record['code'],source=record['source'],adjustment=record['adjustment'],
                      start=str(clip.date.iloc[0]),cutoff=str(clip.date.iloc[-1]),bars=len(clip))
    definition = dict(id=identifier,name=name,kind=kind,path=path,origin=origin,algorithm=ALGORITHM)
    version = digest(dumps(definition).encode())
    settings = options(value.get('settings'))
    with store.atomic_write():
        store.execute('BEGIN IMMEDIATE')
        head = read_meta(store, 'shape_head:'+identifier)
        if head and expected_revision != head['revision']:
            raise ValueError('模板已在另一窗口修改，请刷新后重试')
        if not head and expected_revision not in (None,0):
            raise ValueError('原模板不存在')
        version_key = f'shape_template:{identifier}:{version}'
        if read_meta(store,version_key) is None:
            write_meta(store,version_key,dict(definition,version=version,created=now()))
        write_meta(store,'shape_head:'+identifier,dict(id=identifier,version=version,
                   tracking=bool(value.get('tracking')),settings=settings,revision=(head or {}).get('revision',0)+1))
    return next(t for t in templates(store) if t['id'] == identifier)


def save_run(store, report):
    """One immutable receipt plus a small index; latest never destroys history."""
    identifier = _id(report['id'])
    relative = f'shape_runs/{identifier}.json'
    content = dumps(report).encode('utf-8')
    sha = digest(content)
    with store.atomic_write():
        if read_meta(store,'shape_run:'+identifier):
            raise ValueError('形态扫描回执已存在，不允许覆盖')
        store.write_artifact(relative,content)
        write_meta(store,'shape_run:'+identifier,dict(id=identifier,path=relative,sha256=sha,
                   asof=report['asof'],source=report['source'],created=report['created'],
                   status=report['status'],mode=report['mode']))
    return identifier


def run(store, identifier):
    index = read_meta(store,'shape_run:'+_id(identifier))
    if not index:
        raise ValueError('形态扫描记录不存在')
    content = store.read_artifact(index['path'])
    if digest(content) != index['sha256']:
        raise ValueError('形态历史回执校验失败，不使用已修改的记录')
    return json.loads(content)


def runs(store, source=None):
    values = [json.loads(row['value']) for row in store.rows("SELECT value FROM meta WHERE key LIKE 'shape_run:%'")]
    return sorted((v for v in values if source is None or v['source']==source),key=lambda v:v['created'],reverse=True)


def candidates(store, asof_filter=None, source=None):
    """One effective run per template/day/mode; every prior run remains in history."""
    result, seen = [], set()
    for index in runs(store,source):
        if asof_filter and any(part and str(part) != index['asof'].split('-')[i] for i,part in enumerate(asof_filter)):
            continue
        if index['status'] not in ('completed','partial'):
            continue
        report = run(store,index['id'])
        for template in report['templates']:
            identity = (index['asof'],template['id'],index['mode'])
            if identity in seen:
                continue
            seen.add(identity)
            for match in report['matches']:
                if match['template_id'] != template['id']:
                    continue
                result.append(dict(match, kind='shape', strategy=template['id'],
                    label=template['name'], template=template, run_id=index['id'],
                    source=index['source'], date=index['asof'], timeframe='daily',
                    observation_id='shape:'+index['id']+':'+match['id'],
                    scan_mode=index['mode'], scan_status=index['status']))
    return result


def watch_match(store, run_id, match_id):
    report = run(store,run_id)
    match = next((v for v in report['matches'] if v['id']==match_id),None)
    if not match:
        raise ValueError('形态候选不存在')
    template = next(t for t in report['templates'] if t['id']==match['template_id'])
    code = match['code']
    with store.atomic_write():
        store.add_manual_watch(code,match.get('name',''),f"形态研究：{template['name']} · {report['asof']}")
        evidence = read_meta(store,'shape_watch:'+code,[])
        item = dict(run_id=run_id,match_id=match_id,template=template['name'],asof=report['asof'])
        if item not in evidence:
            write_meta(store,'shape_watch:'+code,(evidence+[item])[-200:])
    return code
