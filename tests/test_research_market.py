import json
from pathlib import Path

import pandas as pd
import pytest

from workbench.market import latest_datasets, load_dataset
from workbench.research_market import CALENDAR, coverage_records, load_research_dataset, migrate_calendar, register
from workbench.store import Store, digest


def test_register_research_snapshot_and_check_coverage_without_csv(tmp_path, monkeypatch):
    store = Store(tmp_path / 'runtime')
    folder = tmp_path / 'sample'
    path = folder / 'bars/sh.600004.csv'
    path.parent.mkdir(parents=True)
    days = json.loads(CALENDAR.read_bytes())['trading_days'][:150]
    pd.DataFrame(dict(date=days, open=10, high=11, low=9, close=10, volume=100)).to_csv(path, index=False)
    (folder / 'manifest.json').write_text(json.dumps(dict(
        meta=dict(source='tickflow', adjustment='qfq', start='2015-01-01', end=days[-1], snapshot_date=days[-1]),
        codes={'sh.600004': dict(status='done', file='bars/sh.600004.csv', bars=len(days),
                                  first=days[0], last=days[-1], sha256=digest(path.read_bytes())[:16])})),
        encoding='utf-8')
    assert migrate_calendar(store)['start'] == '2015-01-01'
    record, = register(store, folder, ['sh.600004'])
    assert list(register(store, folder, ['sh.600004'])) == [record]
    assert latest_datasets(store, 'tickflow') == []
    with pytest.raises(ValueError, match='行情快照不存在'):
        load_dataset(store, record['id'])
    frame, _ = load_research_dataset(store, record['id'])
    assert frame.date.tolist() == days
    assert not store.rows('SELECT 1 FROM datasets')

    original = store.read_artifact
    def metadata_only(relative, job=None):
        assert Path(relative).suffix == '.json'
        return original(relative, job)
    monkeypatch.setattr(store, 'read_artifact', metadata_only)
    assert coverage_records(store, [record['id']], days[0], days[-1])[record['id']] == record
    with pytest.raises(ValueError, match=f'实际覆盖 {days[0]} 至 {days[-1]}'):
        coverage_records(store, [record['id']], '2014-01-01', days[-1])
    with pytest.raises(ValueError, match='只能选用已注册'):
        coverage_records(store, ['ordinary-snapshot-id'], days[0], days[-1])
    metadata_path = (store.root / record['path']).with_suffix('.json')
    metadata_path.write_text(json.dumps(dict(record, end='2026-01-01')), encoding='utf-8')
    with pytest.raises(ValueError, match='标识校验失败'):
        coverage_records(store, [record['id']], days[0], '2026-01-01')
