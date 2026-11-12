import json
from contextlib import contextmanager

import pandas as pd
import pytest

from gui.data import candidate_dates, load_candidates, load_observation_candles
from workbench.market import latest_datasets, save_dataset
from workbench.snapshot import ReadOnlyStore, snapshot_digest
from workbench.sources import market_source
from workbench.store import Store, dumps
from workbench.sync_client import apply_sync_source, find_sync_source
from workbench.sync_source import write_sync_source

ASOF = '2026-11-11'


def test_find_sync_source_direct(tmp_path):
    (tmp_path / 'manifest.json').write_text('not parsed')
    assert find_sync_source(tmp_path) == tmp_path


def test_find_sync_source_latest_valid_child(tmp_path):
    for name in ['2026-11-10', '2026-11-11', '2026-11-12']:
        child = tmp_path / name
        child.mkdir()
        if name != '2026-11-12':
            (child / 'manifest.json').write_text('not parsed')
    assert find_sync_source(str(tmp_path)) == tmp_path / '2026-11-11'


@pytest.mark.parametrize('kind', ['empty', 'missing', 'none', 'blank'])
def test_find_sync_source_missing(tmp_path, kind):
    root = {'empty': tmp_path, 'missing': tmp_path / 'missing',
            'none': None, 'blank': ''}[kind]
    assert find_sync_source(root) is None


def test_find_sync_source_root_precedes_child(tmp_path):
    child = tmp_path / '2026-11-12'
    child.mkdir()
    for directory in [tmp_path, child]:
        (directory / 'manifest.json').write_text('not parsed')
    assert find_sync_source(tmp_path) == tmp_path


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.delenv('APLUS_SNAPSHOT_DIR', raising=False)
    monkeypatch.delenv('APLUS_SNAPSHOT_URL', raising=False)
    source = Store(tmp_path / 'server')
    desktop = Store(tmp_path / 'desktop')
    for index, code in enumerate(['sh.600000', 'sz.000001']):
        frame = pd.DataFrame([dict(date=ASOF, open=10, high=12, low=9, close=11, volume=100)])
        did = save_dataset(source, code, frame, 'tickflow', 'qfq')
        source.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (f'obs-{index}', 'scan', code, 'STRATEGY_GAP_H2', 'v1', 'daily', ASOF, ASOF, did,
                        dumps(dict(entry=11, stop=9, target=15, plan=dict(entry=11, stop=9, target=15))), ASOF))
    path = source.root / 'snapshots' / ASOF
    path.mkdir(parents=True)
    (path / 'snapshot.json').write_text(dumps(dict(asof=ASOF, hits=[dict(observation_id=f'obs-{i}') for i in range(2)], coverage={'ready': 2})))
    directory = source.root / 'sources/tickflow'
    directory.mkdir(parents=True)
    (directory / 'universe.csv').write_text('code,code_name\nsh.600000,样本\n')
    out = write_sync_source(ReadOnlyStore(source.root))
    return source, desktop, out


def state(store):
    return {t: store.rows(f'SELECT * FROM {t} ORDER BY rowid') for t in ['datasets', 'observations', 'meta']}


def edit(out, change, resign=True):
    path = out / 'manifest.json'
    manifest = json.loads(path.read_text())
    change(manifest)
    if resign:
        manifest['sha256'] = snapshot_digest(manifest)
    path.write_text(dumps(manifest))


def test_export_and_end_to_end(sample):
    source, desktop, out = sample
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['sha256'] == snapshot_digest(manifest)
    assert {'kind', 'schema', 'asof', 'source', 'timeframe', 'generated_at', 'engine', 'frozen_sha256', 'coverage', 'directories'} <= manifest.keys()
    assert manifest['datasets'] == source.rows('SELECT * FROM datasets')
    assert manifest['observations'] == source.rows('SELECT * FROM observations')
    assert all((out / row['path']).exists() for row in manifest['datasets'])
    result = apply_sync_source(desktop, out)
    assert result['status'] == 'applied'
    assert result['datasets_added'] == result['observations_added'] == 2
    def pairs(store):
        return {(strategy, row['code']) for strategy, rows in load_candidates(store, 'daily', source='tickflow').items() for row in rows}
    assert pairs(desktop) == pairs(source)
    assert len(pairs(desktop)) == 2
    frame, _, payload = load_observation_candles(desktop, 'obs-0')
    assert len(frame) == 1
    assert payload == load_observation_candles(source, 'obs-0')[2]
    assert ASOF in candidate_dates(desktop, 'daily', source='tickflow')
    before = state(desktop)
    assert apply_sync_source(desktop, out)['status'] == 'up-to-date'
    assert state(desktop) == before


def test_newer_local_market(sample):
    _, desktop, out = sample
    newer = save_dataset(desktop, 'sh.600000', pd.DataFrame([dict(date='2026-11-12', open=10, high=12, low=9, close=11, volume=100)]), 'tickflow', 'qfq')
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert next(r for r in latest_datasets(desktop, 'tickflow') if r['code'] == 'sh.600000')['id'] == newer


def test_sync_makes_candidates_visible_by_default(sample):
    _, desktop, out = sample
    assert desktop.rows("SELECT value FROM meta WHERE key='daily_market_source'") == []
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert market_source(desktop) == 'tickflow'
    candidates = load_candidates(desktop, 'daily')
    assert {row['observation_id'] for rows in candidates.values() for row in rows} == {'obs-0', 'obs-1'}
    assert ASOF in candidate_dates(desktop, 'daily')


def test_rejected_sync_preserves_market_source(sample):
    _, desktop, out = sample
    desktop.execute("INSERT INTO meta VALUES('daily_market_source', 'tencent')")
    edit(out, lambda m: m['observations'][0].update(payload='{}'), resign=False)
    before = state(desktop)
    assert apply_sync_source(desktop, out)['status'] == 'rejected'
    assert market_source(desktop) == 'tencent'
    assert state(desktop) == before


def test_up_to_date_sync_preserves_market_source(sample):
    _, desktop, out = sample
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    desktop.execute("UPDATE meta SET value='baostock' WHERE key='daily_market_source'")
    before = state(desktop)
    assert apply_sync_source(desktop, out)['status'] == 'up-to-date'
    assert market_source(desktop) == 'baostock'
    assert state(desktop) == before


@pytest.mark.parametrize('kind', ['market', 'payload', 'schema', 'stale', 'engine', 'directory', 'bad-json', 'dataset-ref', 'existing-file'])
def test_reject_without_database_changes(sample, kind):
    _, desktop, out = sample
    manifest = json.loads((out / 'manifest.json').read_text())
    if kind == 'market':
        path = out / manifest['datasets'][0]['path']
        path.write_bytes(path.read_bytes() + b'x')
    elif kind == 'payload':
        edit(out, lambda m: m['observations'][0].update(payload='{}'), resign=False)
    elif kind == 'schema':
        edit(out, lambda m: m.update(schema=2))
    elif kind == 'stale':
        assert apply_sync_source(desktop, out)['status'] == 'applied'
        edit(out, lambda m: m.update(asof='2026-11-10'))
    elif kind == 'engine':
        edit(out, lambda m: m.update(engine='fake'))
    elif kind == 'directory':
        (out / 'sources/tickflow/universe.csv').write_text('bad')
    elif kind == 'bad-json':
        edit(out, lambda m: m['observations'][0].update(payload='{'))
    elif kind == 'dataset-ref':
        edit(out, lambda m: m['observations'][0].update(dataset_id='unknown'))
    else:
        path = desktop.root / manifest['datasets'][0]['path']
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'external modification')
    before = state(desktop)
    assert apply_sync_source(desktop, out)['status'] == 'rejected'
    assert state(desktop) == before


def test_user_state_untouched(sample):
    _, desktop, out = sample
    # Seed every required field using the actual table definition; values are synthetic.
    tables = ['watchlist', 'plans', 'accounts', 'positions']
    for table in tables:
        columns = desktop.rows(f'PRAGMA table_info({table})')
        names = [c['name'] for c in columns if c['dflt_value'] is None]
        desktop.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", tuple('sample' if next(c for c in columns if c['name'] == name)['type'] == 'TEXT' else 1 for name in names))
    before = {t: desktop.rows(f'SELECT * FROM {t}') for t in tables}
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert {t: desktop.rows(f'SELECT * FROM {t}') for t in tables} == before


def test_database_failure_rolls_back(sample, monkeypatch):
    _, desktop, out = sample
    before = state(desktop)
    original = desktop.connect
    @contextmanager
    def failing():
        with original() as db:
            db.execute("CREATE TEMP TRIGGER fail_sync BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT, 'injected'); END")
            yield db
    monkeypatch.setattr(desktop, 'connect', failing)
    with pytest.raises(Exception, match='injected'):
        apply_sync_source(desktop, out)
    monkeypatch.setattr(desktop, 'connect', original)
    assert state(desktop) == before
    assert not (desktop.root / 'sources/tickflow/universe.csv').exists()


@pytest.mark.parametrize('missing', ['snapshot', 'observation', 'market'])
def test_export_failure_no_partial_output(sample, missing):
    source, _, _ = sample
    if missing == 'snapshot':
        (source.root / 'snapshots' / ASOF / 'snapshot.json').unlink()
    elif missing == 'observation':
        source.execute("DELETE FROM observations WHERE id='obs-0'")
    else:
        (source.root / source.rows('SELECT path FROM datasets')[0]['path']).unlink()
    target = source.root / 'failed-export'
    with pytest.raises((ValueError, OSError)):
        write_sync_source(ReadOnlyStore(source.root), ASOF, target)
    assert not target.exists()


def test_existing_dataset_skips_source_file(sample, monkeypatch):
    source, desktop, out = sample
    row = source.rows('SELECT * FROM datasets')[0]
    desktop.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
    (out / row['path']).unlink()
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert len(desktop.rows('SELECT * FROM datasets')) == 2
    def unexpected(*args, **kwargs):
        pytest.fail('up-to-date must not write artifacts')
    monkeypatch.setattr(desktop, 'write_artifact', unexpected)
    assert apply_sync_source(desktop, out)['status'] == 'up-to-date'
