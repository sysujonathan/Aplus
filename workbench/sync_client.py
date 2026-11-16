"""Apply shared server results, keeping desktop trading state untouched."""
from datetime import date
import json
import logging
import os
from pathlib import Path
import re
import sys

from .snapshot import snapshot_digest
from .store import digest, dumps, now
from .strategies import scan_engine_version
from .sync_source import DATASET_COLUMNS, OBSERVATION_COLUMNS, DIRECTORY, frozen_sha256

logger = logging.getLogger(__name__)


def find_sync_source(root) -> Path | None:
    """Locate a direct source or the latest named child, without reading it."""
    if not root:
        return None
    try:
        root = Path(root)
        if (root / 'manifest.json').is_file():
            return root
        return max(
            (child for child in root.iterdir()
             if child.is_dir() and (child / 'manifest.json').is_file()),
            key=lambda child: child.name,
            default=None,
        )
    except (OSError, ValueError):
        return None


def _sync_search_roots():
    """Probe drive roots only; never traverse the filesystem."""
    if sys.platform != 'win32':
        return []
    roots = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = Path(f'{letter}:\\')
        try:
            if root.is_dir():
                roots.append(root)
        except OSError:
            continue
    return roots


def _sync_candidates(roots):
    """Known layouts only, including the verified server-side backup parent."""
    for root in roots:
        for layer in ('', '我的云端硬盘', 'My Drive'):
            for parent in ('', 'CyberMe原件备份'):
                yield Path(root) / layer / parent / '02-交易原件' / 'Aplus桌面同步源'


def locate_sync_source(store, search_roots=None) -> Path | None:
    """Prefer an explicit override, then memory, then known Drive locations."""
    if 'APLUS_SYNC_DIR' in os.environ:
        return find_sync_source(os.environ['APLUS_SYNC_DIR'])

    def remember(source):
        source = source.resolve()
        store.execute(
            "INSERT INTO meta(key,value) VALUES('sync_dir',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(source),))
        return source

    saved = store.rows("SELECT value FROM meta WHERE key='sync_dir'")
    if saved:
        source = find_sync_source(saved[0]['value'])
        if source is not None:
            return remember(source)
    roots = _sync_search_roots() if search_roots is None else search_roots
    for candidate in _sync_candidates(roots):
        # Discovery requires the manifest in this exact directory, not a child.
        try:
            if not (candidate / 'manifest.json').is_file():
                continue
        except OSError:
            continue
        source = find_sync_source(candidate)
        if source == candidate:
            return remember(source)
    return None


def missing_sync_message(store, search_roots=None):
    """Explain discovery failure without changing locator precedence or layout."""
    if 'APLUS_SYNC_DIR' in os.environ:
        return ('指定的同步来源不可用：请检查 APLUS_SYNC_DIR，或清除该设置后重试；'
                '来源根目录必须有 manifest.json')
    saved = store.rows("SELECT value FROM meta WHERE key='sync_dir'")
    candidates = [Path(saved[0]['value'])] if saved else []
    roots = _sync_search_roots() if search_roots is None else search_roots
    candidates.extend(_sync_candidates(roots))
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return ('同步源为空或不完整：根目录缺少 manifest.json；'
                        '请等待 Google Drive 下载完成，仍缺少时请联系服务端重新发布')
        except OSError:
            continue
    return ('未找到同步源文件夹：请确认 Google Drive 已登录并同步了 '
            '02-交易原件/Aplus桌面同步源；镜像到其他位置时请设置 APLUS_SYNC_DIR')


def rejection_message(store, reason=None):
    rows = store.rows("SELECT value FROM meta WHERE key='sync_asof'")
    message = f"同步失败，仍为 {rows[0]['value'] if rows else '原有'} 数据"
    reason = reason or '请检查 Google Drive 连接及本地磁盘空间后重试；仍失败时请提供桌面日志'
    return f'{message}。{reason}'


def sync_age_days(asof):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone(timedelta(hours=8))).date() - date.fromisoformat(asof)).days


def _rejection_reason(exc):
    if isinstance(exc, FileNotFoundError):
        return '同步源文件未下载齐；请将同步源设为“可离线使用”，等待 Drive 下载完成后重试'
    if isinstance(exc, PermissionError):
        return '无法读取同步源；请检查文件权限及 Google Drive 登录状态后重试'
    if isinstance(exc, OSError):
        return '无法读取同步源；请检查 Google Drive 连接并等待下载完成后重试'
    detail = str(exc)
    if detail.startswith('version mismatch'):
        return '同步源与桌面版本不一致；请更新桌面程序，并让服务端用相同版本重新发布'
    if detail.startswith('同步源'):
        return detail
    if detail == 'stale or invalid asof':
        return '同步源日期无效或早于本地数据；请联系服务端发布最新同步源'
    return '同步源内容校验失败；请等待 Drive 下载完成，仍失败时请联系服务端重新发布'


def apply_sync_source(store, source_dir):
    source_dir = Path(source_dir)
    saved = {r['key']: r['value'] for r in store.rows("SELECT * FROM meta WHERE key LIKE 'sync_%'")}
    try:
        manifest = json.loads((source_dir / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['kind'] != 'aplus-sync-source' or type(manifest['schema']) is not int or manifest['schema'] != 1 or manifest['sha256'] != snapshot_digest(manifest):
            raise ValueError('manifest identity/digest mismatch')
        if manifest['engine'] != scan_engine_version() or manifest['frozen_sha256'] != frozen_sha256():
            raise ValueError(f"version mismatch: source={manifest['engine']}/{manifest['frozen_sha256']}, local={scan_engine_version()}/{frozen_sha256()}")
        asof = manifest['asof']
        if date.fromisoformat(asof).isoformat() != asof or asof < saved.get('sync_asof', ''):
            raise ValueError('stale or invalid asof')
        age = sync_age_days(asof)
        if age < 0:
            raise ValueError('同步源日期在未来；请核对电脑日期及服务端快照日期')
        if age > 7:
            raise ValueError(f'同步源已过期 {age} 天；请联系服务端发布最新同步源')
        unchanged = asof == saved.get('sync_asof') and manifest['sha256'] == saved.get('sync_digest')
        if manifest['source'] != 'tickflow' or manifest['timeframe'] != 'daily':
            raise ValueError('unsupported source/timeframe')
        datasets, observations = manifest['datasets'], manifest['observations']
        existing = {r['id']: r for r in store.rows('SELECT * FROM datasets')}
        ids = set()
        writes = []
        for row in datasets:
            if set(row) != set(DATASET_COLUMNS) or not re.fullmatch('[0-9a-f]{64}', row['id']) or row['path'] != f"market/{row['id']}.csv" or row['source'] != 'tickflow':
                raise ValueError('invalid dataset row')
            if row['id'] in ids:
                raise ValueError('duplicate dataset')
            ids.add(row['id'])
            if row['id'] in existing:
                local = existing[row['id']]
                if local['path'] != row['path']:
                    raise ValueError('registered market path mismatch')
                try:
                    content = (store.root / local['path']).read_bytes()
                except OSError:
                    pass  # Recover from the validated source below.
                else:
                    if digest(content) != row['sha256']:
                        raise ValueError('existing market file modified externally')
                    continue
            content = (source_dir / row['path']).read_bytes()
            if digest(content) != row['sha256']:
                raise ValueError('market digest mismatch')
            target = store.root / row['path']
            if target.exists() and digest(target.read_bytes()) != row['sha256']:
                raise ValueError('existing market file modified externally')
            if not target.exists():
                writes.append((row['path'], content))
        for row in observations:
            if set(row) != set(OBSERVATION_COLUMNS) or row['dataset_id'] not in ids:
                raise ValueError('invalid observation row')
            json.loads(row['payload'])
        for row in manifest['directories']:
            if row['path'] != DIRECTORY:
                raise ValueError('unsupported directory file')
            content = (source_dir / DIRECTORY).read_bytes()
            if digest(content) != row['sha256']:
                raise ValueError('directory digest mismatch')
            target = store.root / DIRECTORY
            if not target.exists() or target.read_bytes() != content:
                writes.append((DIRECTORY, content))
        if 'calendar' in manifest:
            row = manifest['calendar']
            relative = row['path']
            if relative not in ('trading_calendar.json', 'sources/tickflow/trading_calendar.json'):
                raise ValueError('unsupported calendar file')
            content = (source_dir / relative).read_bytes()
            if digest(content) != row['sha256']:
                raise ValueError('calendar digest mismatch')
            # Publish to TickFlow's effective calendar; preserve other sources.
            relative = 'sources/tickflow/trading_calendar.json'
            target = store.root / relative
            if not target.exists() or target.read_bytes() != content:
                writes.append((relative, content))
        coverage = dumps(manifest['coverage'])
        if unchanged and not writes:
            return dict(status='up-to-date', message=f'已是最新（{asof}）', asof=asof, datasets_added=0, observations_added=0)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning('Synchronization rejected: %s', exc)
        return dict(status='rejected', message=rejection_message(store, _rejection_reason(exc)))
    # Mutable inventories share the artifact/DB failure boundary.
    previous = {}
    for relative, _ in writes:
        if relative == DIRECTORY or relative.endswith('trading_calendar.json'):
            target = store.root / relative
            previous[relative] = target.read_bytes() if target.exists() else None
    try:
        for relative, content in writes:
            store.write_artifact(relative, content)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_ids = {row['id'] for row in observations}
            tracked = db.execute("SELECT value FROM meta WHERE key='sync_observation_ids'").fetchone()
            tracked = json.loads(tracked['value']) if tracked else {}
            withdrawn = db.execute("SELECT value FROM meta WHERE key='sync_withdrawn_observation_ids'").fetchone()
            withdrawn = set(json.loads(withdrawn['value'])) if withdrawn else set()
            removed_ids = set(tracked['ids']) - current_ids if tracked.get('asof') == asof else set()
            removed = len(removed_ids)
            # Keep archives addressable by watchlists, plans and charts.
            # Retain withdrawals across revisions/days; reintroduced IDs reactivate.
            withdrawn = (withdrawn | removed_ids) - current_ids
            counts = []
            for table, columns, rows in [('datasets', DATASET_COLUMNS, datasets), ('observations', OBSERVATION_COLUMNS, observations)]:
                count = 0
                for row in rows:
                    count += db.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row[k] for k in columns)).rowcount
                counts.append(count)
            metadata = dict(sync_asof=asof, sync_digest=manifest['sha256'], sync_applied_at=now(),
                            sync_coverage=coverage, daily_market_source=manifest['source'],
                            sync_withdrawn_observation_ids=dumps(sorted(withdrawn)),
                            sync_observation_ids=dumps({'asof': asof, 'ids': sorted(current_ids)}))
            if 'universe_date' in manifest:
                metadata['universe_date:tickflow'] = manifest['universe_date']
            for key, value in metadata.items():
                db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    except Exception:
        for relative, content in previous.items():
            if content is None:
                (store.root / relative).unlink(missing_ok=True)
            else:
                store.write_artifact(relative, content)
        logger.exception('Synchronization failed; DB rolled back, unreferenced market artifacts may remain')
        raise
    return dict(status='applied', message=f'已同步到 {asof} 数据', asof=asof, datasets_added=counts[0], observations_added=counts[1], observations_removed=removed)
