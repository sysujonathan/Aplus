"""Versioned ZIP transport for existing TickFlow manifests and CSV bytes."""
from datetime import date
from hashlib import file_digest
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from .snapshot import snapshot_digest
from .store import digest, dumps
from .sync_source import DIRECTORY

CALENDARS = {'trading_calendar.json', 'sources/tickflow/trading_calendar.json'}


def is_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def package_name(sha):
    if not is_sha(sha):
        raise ValueError('同步源包摘要无效')
    return f'package-{sha}.zip'


def load_latest(raw):
    plan = json.loads(raw)
    if (plan['kind'] != 'aplus-sync-latest' or type(plan['schema']) is not int or plan['schema'] != 1
            or plan['sha256'] != snapshot_digest(plan)):
        raise ValueError('同步源 latest 清单校验失败')
    chain = plan['chain']
    if not isinstance(chain, list) or not 1 <= len(chain) <= 365:
        raise ValueError('同步源版本链无效')
    parent, day = None, ''
    versions = set()
    for item in chain:
        version = item['version']
        package_name(item['sha256'])
        if (not is_sha(version) or version in versions or item['parent'] != parent
                or date.fromisoformat(item['asof']).isoformat() != item['asof']
                or item['asof'] < day):
            raise ValueError('同步源父版本或交易日顺序无效')
        versions.add(version)
        parent, day = version, item['asof']
    fallback = plan.get('fallback')
    if fallback is not None:
        package_name(fallback['sha256'])
        if fallback['version'] != parent or fallback['asof'] != day or fallback['parent'] is not None:
            raise ValueError('同步源恢复基线与目标版本不一致')
    return plan


def manifest_files(manifest):
    if (manifest['kind'] != 'aplus-sync-source' or type(manifest['schema']) is not int or manifest['schema'] != 1
            or not is_sha(manifest['sha256']) or manifest['sha256'] != snapshot_digest(manifest)):
        raise ValueError('同步源版本清单校验失败')
    files = {}
    for row in manifest['datasets']:
        if not is_sha(row['id']) or row['path'] != f"market/{row['id']}.csv":
            raise ValueError('同步源行情路径不在白名单内')
        files[row['path']] = row
    if len(files) != len(manifest['datasets']):
        raise ValueError('同步源包含重复行情文件')
    for row in [*manifest['directories'], manifest['calendar']]:
        if row['path'] not in (CALENDARS | {DIRECTORY}) or row['path'] in files:
            raise ValueError('同步源库存文件路径无效')
        files[row['path']] = row
    if (len(manifest['directories']) > 1
            or any(row['path'] != DIRECTORY for row in manifest['directories'])
            or manifest['calendar']['path'] not in CALENDARS
            or not all(is_sha(row['sha256']) for row in files.values())):
        raise ValueError('同步源文件摘要或库存路径无效')
    return files


def _read_source(root, row):
    path = root / row['path']
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('同步源文件越界')
    body = path.read_bytes()
    if digest(body) != row['sha256']:
        raise ValueError(f"同步源文件校验失败：{row['path']}")
    return body


def _splice(before, after):
    """Return one line-aligned replacement interval, including append/delete."""
    if after.startswith(before):
        return len(before), len(before), after[len(before):]
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    first = 0
    while first < min(len(old), len(new)) and old[first] == new[first]:
        first += 1
    tail = 0
    while tail < min(len(old), len(new)) - first and old[-tail - 1] == new[-tail - 1]:
        tail += 1
    start = sum(map(len, old[:first]))
    end = len(before) - sum(map(len, old[len(old) - tail:]))
    return start, end, b''.join(new[first:len(new) - tail])


def write_package(source, out, parent_source=None):
    source, out = Path(source), Path(out)
    manifest_raw = (source / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    files = manifest_files(manifest)
    parent = None
    parent_files = {}
    parent_symbols = {}
    if parent_source is not None:
        parent_source = Path(parent_source)
        parent = json.loads((parent_source / 'manifest.json').read_bytes())
        parent_files = manifest_files(parent)
        if (parent['asof'] > manifest['asof'] or parent['sha256'] == manifest['sha256']
                or any(parent[k] != manifest[k] for k in ('source', 'timeframe', 'engine', 'frozen_sha256'))):
            raise ValueError('同步源父版本不适用于本次增量')
        parent_symbols = {(r['code'], r['timeframe'], r['adjustment']): r for r in parent['datasets']}

    temporary = out / 'package.zip'
    ops = {}
    with ZipFile(temporary, 'x', ZIP_DEFLATED) as archive:
        for relative, row in files.items():
            body = _read_source(source, row)
            previous = parent_files.get(relative)
            if previous and previous['sha256'] == row['sha256']:
                ops[relative] = {'reuse': relative}
                continue
            if 'code' in row:
                previous = parent_symbols.get((row['code'], row['timeframe'], row['adjustment']))
                if previous:
                    before = _read_source(parent_source, previous)
                    start, end, patch = _splice(before, body)
                    if len(patch) + 128 < len(body):
                        ops[relative] = {'base': previous['path'], 'start': start, 'end': end}
                        body = patch
            archive.writestr(relative, body)
        archive.writestr('manifest.json', manifest_raw)
        archive.writestr('version.json', dumps(dict(schema=1, parent=parent['sha256'] if parent else None,
                                                   version=manifest['sha256'], ops=ops)))
    with temporary.open('rb') as stream:
        sha = file_digest(stream, 'sha256').hexdigest()
    temporary.replace(out / package_name(sha))
    return dict(version=manifest['sha256'], asof=manifest['asof'],
                parent=parent['sha256'] if parent else None, sha256=sha)


def write_latest(out, item, previous=None, fallback=None):
    chain = [*load_latest(previous)['chain'], item] if previous is not None else [item]
    plan = dict(kind='aplus-sync-latest', schema=1, chain=chain)
    if fallback is not None:
        plan['fallback'] = fallback
    plan['sha256'] = snapshot_digest(plan)
    raw = dumps(plan).encode('utf-8')
    load_latest(raw)
    (Path(out) / 'latest.json').write_bytes(raw)
    return digest(raw)
