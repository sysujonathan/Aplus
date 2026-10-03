"""Versioned, checksummed, complete per-snapshot research results; no DB schema."""
from dataclasses import asdict
import json
import logging
import platform

import numpy as np
import pandas as pd

from .store import ROOT, digest, dumps


logger = logging.getLogger(__name__)


def engine_version():
    return digest(b''.join((ROOT / 'workbench' / name).read_bytes() for name in
                          ('h2_replay.py', 'h2_prefilter.py', 'h2_replay_cache.py',
                           'h2_replay_service.py', 'h2_plan.py', 'prices.py',
                           'strategies.py', 'backtest.py', 'market.py')))


def cache_key(snapshot, strategy, start, end, settings, costs, engine):
    payload = dict(snapshot={k: snapshot[k] for k in ('id', 'sha256', 'code', 'source', 'adjustment', 'timeframe')},
                   strategy=strategy.version, start=start, end=end, settings=asdict(settings), costs=asdict(costs),
                   engine=engine, pandas=pd.__version__, numpy=np.__version__, python=platform.python_version())
    return digest(dumps(payload).encode('utf-8'))


def _path(key):
    return f'research-cache/h2/{key}.json'


def read_cache(store, key, job=None):
    if not (store.root / _path(key)).is_file():
        return None
    try:
        envelope = json.loads(store.read_artifact(_path(key), job).decode('utf-8'))
        records = envelope['records']
        if (envelope['key'] != key or not isinstance(records, list) or
                digest(dumps(records).encode('utf-8')) != envelope['sha256']):
            raise ValueError('研究缓存校验不一致')
        return records
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A damaged research cache is never accepted as a completed result.
        logger.warning('H2 cache ignored: %s', exc)
        store.event(job, '研究缓存校验失败，重新计算', _path(key), error=str(exc))
        return None


def write_cache(store, key, records, job=None):
    envelope = dict(key=key, records=records, sha256=digest(dumps(records).encode('utf-8')))
    store.write_artifact(_path(key), dumps(envelope).encode('utf-8'), job)
