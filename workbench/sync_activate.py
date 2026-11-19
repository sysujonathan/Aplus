"""Download and reconstruct immutable sync files before committing Store metadata."""
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

from .store import digest
from .sync_packages import is_sha, load_latest, manifest_files, package_name
from .sync_source import DATASET_COLUMNS, OBSERVATION_COLUMNS


def _local_path(store, relative):
    path = store.root / relative
    if not path.resolve().is_relative_to(store.root):
        raise ValueError('同步源本地文件越界')
    return path


def _download(store, remote, sha, report, number, total):
    name = package_name(sha)
    cache = _local_path(store, 'desktop-sync/packages/' + name)
    if cache.is_file():
        with cache.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == sha:
                return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache.parent, prefix='.download-', delete=False) as output:
        temporary = Path(output.name)
        try:
            with output, (remote / name).open('rb') as source:
                sha256 = hashlib.sha256()
                size = 0
                report(f'下载版本包 {number}/{total}', force=True)
                while chunk := source.read(1024 * 1024):
                    report(f'下载版本包 {number}/{total} · {size // (1024 * 1024)} MiB')
                    sha256.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
                if sha256.hexdigest() != sha:
                    raise ValueError('同步源版本包下载不完整或 SHA 校验失败')
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    return cache


def _check_rows(manifest):
    coverage = manifest['coverage']
    if manifest.get('complete') is True and (
            coverage['ready'] + coverage['suspended'] != coverage['expected']
            or coverage['excluded_count'] != 0 or manifest.get('excluded')):
        raise ValueError('同步源完整扫描标记与覆盖回执不一致')
    rows = {}
    dataset_columns = set(DATASET_COLUMNS)
    observation_columns = set(OBSERVATION_COLUMNS)
    symbols = set()
    for row in manifest['datasets']:
        identity = (row['code'], row['timeframe'], row['source'], row['adjustment'])
        expected_id = digest(('|'.join((*identity, row['sha256']))).encode())
        symbol = identity[:2]
        if (row.keys() != dataset_columns or row['id'] != expected_id or symbol in symbols
                or row['timeframe'] != 'daily' or row['source'] != 'tickflow'
                or row['adjustment'] not in ('前复权', 'qfq') or row['end'] > manifest['asof']):
            raise ValueError('同步源行情身份、日期或前复权口径不一致')
        symbols.add(symbol)
        rows[row['id']] = row
    seen = set()
    for row in manifest['observations']:
        if row.keys() != observation_columns or row['id'] in seen:
            raise ValueError('同步源候选身份无效')
        dataset = rows.get(row['dataset_id'])
        if (dataset is None or any(dataset[k] != row[k] for k in ('code', 'timeframe'))
                or row['asof'][:10] != manifest['asof'] or dataset['end'] != manifest['asof']
                or not isinstance(json.loads(row['payload']), dict)):
            raise ValueError('同步源候选与对应 K 线不一致')
        seen.add(row['id'])


def _inventory_path(version, relative):
    return f'desktop-sync/versions/{version}/{relative.rsplit("/", 1)[-1]}'


def _version_manifest(version):
    return _inventory_path(version, 'manifest.json')


def _existing(store, relative, sha, verified):
    if verified.get(relative) == sha:
        return True
    path = _local_path(store, relative)
    if not path.is_file():
        return False
    if digest(path.read_bytes()) != sha:
        raise ValueError(f'同步源本地不可变文件校验失败：{relative}')
    verified[relative] = sha
    return True


def _reconstruct(store, archive_path, item, parent, verified, validate, report):
    with ZipFile(archive_path) as archive:
        manifest_raw = archive.read('manifest.json')
        manifest = json.loads(manifest_raw)
        files = manifest_files(manifest)
        version = item['version']
        header = json.loads(archive.read('version.json'))
        if (type(header['schema']) is not int or header['schema'] != 1 or header['version'] != version
                or manifest['sha256'] != version or manifest['asof'] != item['asof']
                or header['parent'] != item['parent']
                or item['parent'] != (parent['sha256'] if parent else None)):
            raise ValueError('同步源版本包的父版本或目标版本不一致')
        validate(manifest)
        _check_rows(manifest)
        if parent and any(parent[k] != manifest[k] for k in ('source', 'timeframe', 'engine', 'frozen_sha256')):
            raise ValueError('同步源增量与父版本的行情源或引擎不一致')
        ops = header['ops']
        if not isinstance(ops, dict) or not set(ops) <= files.keys() or (ops and parent is None):
            raise ValueError('同步源版本包操作清单无效')
        reused = {name for name, op in ops.items() if 'reuse' in op}
        names = archive.namelist()
        if (len(names) != len(set(names))
                or set(names) != (files.keys() - reused) | {'manifest.json', 'version.json'}):
            raise ValueError('同步源版本包文件有缺失、重复或越界')
        source_files = manifest_files(parent) if parent else {}
        report(f'重建并校验 0/{len(files)}', force=True)
        for i, (relative, row) in enumerate(files.items(), 1):
            report(f'重建并校验 {i}/{len(files)}')
            market = 'id' in row
            local = relative if market else _inventory_path(version, relative)
            op = ops.get(relative)
            body = None
            if op is not None:
                is_reuse = set(op) == {'reuse'}
                if not is_reuse and (not market or set(op) != {'base', 'start', 'end'}):
                    raise ValueError('同步源增量操作无效')
                base = op['reuse'] if is_reuse else op['base']
                previous = source_files.get(base)
                if (previous is None or (is_reuse and (base != relative or previous['sha256'] != row['sha256']))
                        or (market and any(previous.get(key) != row[key] for key in ('code', 'timeframe', 'adjustment')))):
                    raise ValueError('同步源增量文件未锚定父版本')
                if is_reuse and market and _existing(store, local, row['sha256'], verified):
                    continue
                old = base if market else _inventory_path(parent['sha256'], base)
                before = _local_path(store, old).read_bytes()
                if digest(before) != previous['sha256']:
                    raise ValueError('同步源增量父文件 SHA 不一致')
                if is_reuse:
                    body = before
                else:
                    start, end = op['start'], op['end']
                    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(before):
                        raise ValueError('同步源 CSV 片段边界无效')
                    body = before[:start] + archive.read(relative) + before[end:]
            else:
                body = archive.read(relative)
            if digest(body) != row['sha256']:
                raise ValueError(f'同步源目标文件 SHA 不一致：{relative}')
            if not _existing(store, local, row['sha256'], verified):
                store.write_immutable_sync_file(local, body)
                verified[local] = row['sha256']
        manifest_path = _version_manifest(version)
        # The canonical JSON ignores cosmetic whitespace in a repackaged baseline.
        canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if not _existing(store, manifest_path, digest(canonical), verified):
            store.write_immutable_sync_file(manifest_path, canonical)
    return manifest


def prepare_activation(store, remote, saved, validate, validate_day, report):
    # manifest_files checks identity and digest before the caller checks compatibility.
    plan = load_latest((remote / 'latest.json').read_bytes())
    chain = plan['chain']
    target = chain[-1]
    validate_day(target['asof'])
    active = saved.get('sync_version')
    report(f"已发布 {target['asof']} {target['version'][:12]} · 本地版本 {active[:12] if active else '首次'}",
           force=True)
    parent = None
    verified = {}
    active_path = _local_path(store, _version_manifest(active)) if is_sha(active) else None
    active_manifest = None
    if active_path and active_path.is_file():
        active_manifest = json.loads(active_path.read_bytes())
        active_files = manifest_files(active_manifest)
        if active_manifest['sha256'] != active or active_manifest['asof'] != saved.get('sync_asof'):
            raise ValueError('同步源本地已应用版本记录不一致')
    if active == target['version'] and active_manifest:
        if active_manifest['asof'] != target['asof']:
            raise ValueError('同步源本地版本与版本链日期不一致')
        validate(active_manifest)
        report('校验已应用版本', force=True)
        for number, (name, row) in enumerate(active_files.items(), 1):
            report(f'校验已应用版本 {number}/{len(active_files)}')
            relative = name if 'id' in row else _inventory_path(active, name)
            if not _existing(store, relative, row['sha256'], verified):
                break
        else:
            return active_manifest, None
    elif active and active != target['version']:
        active_index = next((i for i, entry in enumerate(chain) if entry['version'] == active), None)
        if active_index is not None and active_manifest:
            if chain[active_index]['asof'] != active_manifest['asof']:
                raise ValueError('同步源父版本与版本链日期不一致')
            parent = active_manifest
            chain = chain[active_index + 1:]
        elif active_index is None and target['asof'] == saved.get('sync_asof'):
            raise ValueError('同步源同日修订未锚定已应用版本')

    def apply_chain(items, current):
        for number, item in enumerate(items, 1):
            report(f'读取版本包 {number}/{len(items)}', force=True)
            archive = _download(store, remote, item['sha256'], report, number, len(items))
            current = _reconstruct(store, archive, item, current, verified, validate, report)
        return current

    try:
        manifest = apply_chain(chain, parent)
    except FileNotFoundError:
        if plan.get('fallback'):
            report('缺少增量或父文件，恢复完整基线', force=True)
            manifest = apply_chain([plan['fallback']], None)
        elif parent:
            report('缺少增量或父文件，从基线重建版本链', force=True)
            manifest = apply_chain(plan['chain'], None)
        else:
            raise
    inventory = {relative.rsplit('/', 1)[-1]: _inventory_path(target['version'], relative)
                 for relative, row in manifest_files(manifest).items() if 'id' not in row}
    return manifest, inventory
