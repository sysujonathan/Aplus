"""Immutable TickFlow research snapshots kept outside the production catalog."""
import io
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

from .history_quality import describe_history
from .market import board_of, completed_date, load_dataset, validate_bars
from .store import digest, dumps
from .tickflow_calendar import _read_calendar
from .sources import calendar_file, source_file

SOURCE = 'research-tickflow'
FOLDER = 'research-market'
CALENDAR = Path(__file__).parent / 'evidence' / 'tickflow' / 'trading_calendar.json'


def migrate_calendar(store):
    """Extend the TickFlow calendar explicitly, keeping verified overlapping facts."""
    reference, days = _read_calendar(CALENDAR)
    target = source_file(store, 'trading_calendar.json', 'tickflow')
    current = calendar_file(store, 'tickflow')
    if current.exists():
        local, local_days = _read_calendar(current)
        left = max(local['start'], reference['start'])
        right = min(local['end'], reference['end'])
        overlap = {d for d in days if left <= d <= right}
        if left > right or overlap != {d for d in local_days if left <= d <= right}:
            raise ValueError('TickFlow 日历与迁入日历不连续或重叠区间不一致，请先核对')
        if current == target and local['start'] <= reference['start'] and local['end'] >= reference['end']:
            return local
        reference = dict(local, start=min(local['start'], reference['start']),
                         end=max(local['end'], reference['end']), trading_days=sorted(days | local_days),
                         reference_sha256=reference['reference_sha256'])
    store.write_artifact(str(target.relative_to(store.root)), dumps(reference).encode())
    return reference


def register(store, folder, codes):
    """Validate source files once and publish per-symbol metadata after the bars."""
    folder = Path(folder).resolve()
    raw_manifest = (folder / 'manifest.json').read_bytes()
    manifest = json.loads(raw_manifest)
    meta = manifest['meta']
    anchor = meta['snapshot_date']
    if (meta['source'] != 'tickflow' or meta['adjustment'] != 'qfq'
            or date.fromisoformat(anchor).isoformat() != anchor):
        raise ValueError('需要带快照日期的 TickFlow 前复权日线清单')
    calendar, _ = _read_calendar(CALENDAR)
    calendar_sha = digest(dumps(calendar).encode())
    manifest_sha = digest(raw_manifest)
    for code in codes:
        if board_of(code) is None:
            raise ValueError(f'{code} 不是受支持的 A 股代码')
        item = manifest['codes'].get(code, {})
        relative = f'bars/{code}.csv'
        if item.get('status') != 'done' or item.get('file') != relative:
            raise ValueError(f'{code} 在源清单中没有完整下载记录')
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder):
            raise ValueError(f'{code} 行情越过来源目录')
        content = path.read_bytes()
        sha = digest(content)
        declared = item.get('sha256', '')
        if len(declared) not in (16, 64) or not sha.startswith(declared):
            raise ValueError(f'{code} 与源清单 SHA-256 不一致')
        frame = validate_bars(pd.read_csv(io.BytesIO(content)))
        first, last = frame.date.iloc[0], frame.date.iloc[-1]
        if ((first, last, len(frame)) != (item['first'], item['last'], item['bars'])
                or not meta['start'] <= first <= last <= min(meta['end'], anchor, completed_date())
                or frame.date.isna().any()):
            raise ValueError(f'{code} 的行情起止或行数与源清单不一致')
        quality = describe_history(store, code, frame, first, last, calendar, source='tickflow')
        if quality['missing_dates']:
            raise ValueError(f'{code} 有 {len(quality["missing_dates"])} 个未认证交易日缺口，无法注册')
        metadata = dict(code=code, source=SOURCE, timeframe='daily', adjustment='qfq',
                        start=first, end=last, rows=len(frame), sha256=sha, snapshot_date=anchor,
                        manifest_sha256=manifest_sha, calendar_sha256=calendar_sha)
        identity = SOURCE + ':' + _metadata_digest(metadata)
        basename = _record_path(identity).removesuffix('.json')
        record = dict(metadata, id=identity, path=basename + '.csv')
        store.write_artifact(record['path'], content)
        store.write_artifact(basename + '.json', dumps(record).encode())
        yield record


def _record_path(identity):
    if not re.fullmatch(SOURCE + r':[0-9a-f]{64}', identity):
        raise ValueError('长历史研究回测只能选用已注册的研究快照')
    return f'{FOLDER}/{identity.split(":")[1]}.json'


def _metadata_digest(metadata):
    return digest(dumps(dict(sorted(metadata.items()))).encode())


def _record(store, identity):
    relative = _record_path(identity)
    try:
        metadata = json.loads(store.read_artifact(relative))
    except FileNotFoundError as exc:
        raise ValueError(f'未找到已注册的研究快照 {identity}') from exc
    stored_id = metadata.pop('id', None)
    path = metadata.pop('path', None)
    if (stored_id != identity or metadata.get('source') != SOURCE
            or path != relative.removesuffix('.json') + '.csv'
            or identity != SOURCE + ':' + _metadata_digest(metadata)):
        raise ValueError('长历史研究快照标识校验失败')
    return dict(metadata, id=identity, path=path)


def coverage_records(store, ids, start, end):
    """Reject outside the intersection of selected snapshots using metadata only."""
    records = {did: _record(store, did) for did in ids}
    if len({r['manifest_sha256'] for r in records.values()}) != 1:
        raise ValueError('一次长历史回测只能使用同一来源清单及复权快照')
    for record in records.values():
        if not record['start'] <= start <= end <= record['end']:
            raise ValueError(f'{record["code"]} 所选 {start} 至 {end} 超出研究快照实际覆盖 '
                             f'{record["start"]} 至 {record["end"]}（{record["rows"]} 根日线）')
    return records


def load_research_dataset(store, identity, job=None, record=None):
    record = record if record is not None else _record(store, identity)
    content = store.read_artifact(record['path'], job)
    if digest(content) != record['sha256']:
        raise ValueError('长历史研究快照校验失败，请检查源文件并重新注册')
    return validate_bars(pd.read_csv(io.BytesIO(content))), record


def load_replay_dataset(store, identity, job=None):
    """Read either catalog when reopening a saved research receipt."""
    loader = load_research_dataset if identity.startswith(SOURCE + ':') else load_dataset
    return loader(store, identity, job)
