"""Read-only export of server results for desktop synchronization."""
from datetime import date
import json
import os
from pathlib import Path
import shutil
import tempfile

from .market import latest_datasets
from .snapshot import snapshot_digest
from .sources import calendar_file, directory_date
from .store import digest, dumps, now
from .strategies import scan_engine_version

DATASET_COLUMNS = 'id code timeframe source adjustment start end rows path sha256 created'.split()
OBSERVATION_COLUMNS = 'id job_id code strategy version timeframe asof setup_date dataset_id payload created'.split()
DIRECTORY = 'sources/tickflow/universe.csv'


def frozen_sha256():
    return digest((Path(__file__).resolve().parents[1] / 'frozen_manifest.json').read_bytes())


def write_sync_source(store, asof=None, out_dir=None):
    snapshots = store.root / 'snapshots'
    if asof is None:
        dates = sorted(p.name for p in snapshots.iterdir() if p.is_dir() and (p / 'snapshot.json').is_file()) if snapshots.exists() else []
        if not dates:
            raise ValueError('没有已生成的快照')
        asof = dates[-1]
    if date.fromisoformat(asof).isoformat() != asof:
        raise ValueError('无效快照日期')
    snapshot = json.loads((snapshots / asof / 'snapshot.json').read_text(encoding='utf-8'))
    if snapshot['asof'] != asof:
        raise ValueError('快照日期不一致')
    datasets = [{k: r[k] for k in DATASET_COLUMNS} for r in latest_datasets(store, 'tickflow')]
    ids = {r['id'] for r in datasets}
    observations = []
    for oid in dict.fromkeys(h['observation_id'] for h in snapshot['hits']):
        rows = store.rows('SELECT * FROM observations WHERE id=?', (oid,))
        if not rows or rows[0]['dataset_id'] not in ids:
            raise ValueError(f'快照命中缺少观测或最新行情：{oid}')
        observations.append({k: rows[0][k] for k in OBSERVATION_COLUMNS})
    calendar = calendar_file(store, 'tickflow')
    universe_date = directory_date(store, 'tickflow')
    if not calendar.is_file() or not universe_date:
        raise ValueError('同步源缺少交易日历或目录日期')
    calendar_content = calendar.read_bytes()
    calendar_path = calendar.relative_to(store.root).as_posix()
    out = Path(out_dir) if out_dir else store.root / 'sync-source' / asof
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.sync-', dir=out.parent))
    try:
        for row in datasets:
            relative = f"market/{row['id']}.csv"
            if row['path'] != relative:
                raise ValueError('行情路径不符合内容寻址约定')
            source = store.root / relative
            if digest(source.read_bytes()) != row['sha256']:
                raise ValueError(f'行情文件不一致：{relative}')
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        directories = []
        if (store.root / DIRECTORY).is_file():
            content = (store.root / DIRECTORY).read_bytes()
            target = temporary / DIRECTORY
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            directories.append({'path': DIRECTORY, 'sha256': digest(content)})
        target = temporary / calendar_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(calendar_content)
        manifest = dict(kind='aplus-sync-source', schema=1, asof=asof, source='tickflow',
                        timeframe='daily', generated_at=now(), engine=scan_engine_version(),
                        frozen_sha256=frozen_sha256(), datasets=datasets, observations=observations,
                        coverage=snapshot['coverage'], directories=directories,
                        calendar={'path': calendar_path, 'sha256': digest(calendar_content)},
                        universe_date=universe_date)
        manifest['sha256'] = snapshot_digest(manifest)
        (temporary / 'manifest.json').write_text(dumps(manifest), encoding='utf-8')
        temporary.rename(out)
        return out
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
