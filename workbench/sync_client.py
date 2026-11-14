"""Apply shared server results, keeping desktop trading state untouched."""
from datetime import date
import json
import logging
from pathlib import Path
import re

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


def rejection_message(store):
    rows = store.rows("SELECT value FROM meta WHERE key='sync_asof'")
    return f"同步失败，仍为 {rows[0]['value'] if rows else '原有'} 数据"


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
        if asof == saved.get('sync_asof') and manifest['sha256'] == saved.get('sync_digest'):
            return dict(status='up-to-date', message=f'已是最新（{asof}）', asof=asof, datasets_added=0, observations_added=0)
        if manifest['source'] != 'tickflow' or manifest['timeframe'] != 'daily':
            raise ValueError('unsupported source/timeframe')
        datasets, observations = manifest['datasets'], manifest['observations']
        existing = {r['id'] for r in store.rows('SELECT id FROM datasets')}
        ids = set()
        writes = []
        for row in datasets:
            if set(row) != set(DATASET_COLUMNS) or not re.fullmatch('[0-9a-f]{64}', row['id']) or row['path'] != f"market/{row['id']}.csv" or row['source'] != 'tickflow':
                raise ValueError('invalid dataset row')
            if row['id'] in ids:
                raise ValueError('duplicate dataset')
            ids.add(row['id'])
            if row['id'] in existing:
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
            target = store.root / relative
            if not target.exists() or target.read_bytes() != content:
                writes.append((relative, content))
        coverage = dumps(manifest['coverage'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning('Synchronization rejected: %s', exc)
        return dict(status='rejected', message=rejection_message(store))
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
            removed = 0
            if tracked.get('asof') == asof:
                for oid in set(tracked['ids']) - current_ids:
                    removed += db.execute('DELETE FROM observations WHERE asof=? AND id=?', (asof, oid)).rowcount
            counts = []
            for table, columns, rows in [('datasets', DATASET_COLUMNS, datasets), ('observations', OBSERVATION_COLUMNS, observations)]:
                count = 0
                for row in rows:
                    count += db.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row[k] for k in columns)).rowcount
                counts.append(count)
            metadata = dict(sync_asof=asof, sync_digest=manifest['sha256'], sync_applied_at=now(),
                            sync_coverage=coverage, daily_market_source=manifest['source'],
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
