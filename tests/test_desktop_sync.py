import json
from contextlib import contextmanager

import pandas as pd
import pytest

from gui.data import candidate_dates, load_candidates, load_observation_candles
from workbench.market import latest_datasets, save_dataset
from workbench.snapshot import ReadOnlyStore, snapshot_digest
from workbench.sources import market_source, directory_date
from workbench.readiness import expected_day, audit_scope
from workbench.store import Store, dumps
from workbench.sync_client import apply_sync_source, find_sync_source
from workbench.sync_source import write_sync_source

ASOF = '2026-11-11'


def test_find_sync_source_direct(tmp_path):
    (tmp_path / 'manifest.json').write_text('not parsed', encoding='utf-8')
    assert find_sync_source(tmp_path) == tmp_path


def test_find_sync_source_latest_valid_child(tmp_path):
    for name in ['2026-11-10', '2026-11-11', '2026-11-12']:
        child = tmp_path / name
        child.mkdir()
        if name != '2026-11-12':
            (child / 'manifest.json').write_text('not parsed', encoding='utf-8')
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
        (directory / 'manifest.json').write_text('not parsed', encoding='utf-8')
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
    (path / 'snapshot.json').write_text(dumps(dict(asof=ASOF, hits=[dict(observation_id=f'obs-{i}') for i in range(2)], coverage={'ready': 2})), encoding='utf-8')
    directory = source.root / 'sources/tickflow'
    directory.mkdir(parents=True)
    (directory / 'universe.csv').write_text('code,code_name\nsh.600000,样本\n', encoding='utf-8')
    source.write_artifact('sources/tickflow/trading_calendar.json', dumps(dict(start=ASOF, end=ASOF, trading_days=[ASOF])).encode())
    source.execute("INSERT INTO meta VALUES('universe_date:tickflow', ?)", (ASOF,))
    out = write_sync_source(ReadOnlyStore(source.root))
    return source, desktop, out


def state(store):
    return {t: store.rows(f'SELECT * FROM {t} ORDER BY rowid') for t in ['datasets', 'observations', 'meta']}


def edit(out, change, resign=True):
    path = out / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    change(manifest)
    if resign:
        manifest['sha256'] = snapshot_digest(manifest)
    path.write_text(dumps(manifest), encoding='utf-8')


def test_export_and_end_to_end(sample):
    source, desktop, out = sample
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
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
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
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
        (out / 'sources/tickflow/universe.csv').write_text('bad', encoding='utf-8')
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


@pytest.mark.parametrize('missing', ['snapshot', 'observation', 'market', 'calendar', 'universe-date'])
def test_export_failure_no_partial_output(sample, missing):
    source, _, _ = sample
    if missing == 'snapshot':
        (source.root / 'snapshots' / ASOF / 'snapshot.json').unlink()
    elif missing == 'observation':
        source.execute("DELETE FROM observations WHERE id='obs-0'")
    elif missing == 'calendar':
        (source.root / 'sources/tickflow/trading_calendar.json').unlink()
    elif missing == 'universe-date':
        source.execute("DELETE FROM meta WHERE key='universe_date:tickflow'")
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


@pytest.mark.parametrize('root_calendar', [False, True])
def test_calendar_and_directory_date(sample, root_calendar):
    source, desktop, out = sample
    relative = 'sources/tickflow/trading_calendar.json'
    content = (source.root / relative).read_bytes()
    source.write_artifact('trading_calendar.json', b'stale root calendar')
    if root_calendar:
        (source.root / relative).unlink()
        relative = 'trading_calendar.json'
        source.write_artifact(relative, content)
    out = write_sync_source(ReadOnlyStore(source.root), ASOF, source.root / 'calendar-export')
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['schema'] == 1
    assert manifest['calendar']['path'] == relative
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert (desktop.root / relative).read_bytes() == content
    assert directory_date(desktop, 'tickflow') == ASOF
    assert expected_day(desktop, ASOF, 'tickflow') == ASOF
    assert audit_scope(desktop, ['沪深主板'], ASOF, source='tickflow')['scope_valid'] is True


def test_old_manifest_preserves_calendar_and_date(sample):
    _, desktop, out = sample
    relative = 'sources/tickflow/trading_calendar.json'
    desktop.write_artifact(relative, b'old calendar')
    desktop.execute("INSERT INTO meta VALUES('universe_date:tickflow', 'old-date')")
    edit(out, lambda m: (m.pop('calendar'), m.pop('universe_date')))
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    assert (desktop.root / relative).read_bytes() == b'old calendar'
    assert directory_date(desktop, 'tickflow') == 'old-date'


@pytest.mark.parametrize('kind', ['digest', 'path'])
def test_bad_calendar_rejected(sample, kind):
    _, desktop, out = sample
    if kind == 'digest':
        (out / 'sources/tickflow/trading_calendar.json').write_bytes(b'corrupt')
    else:
        edit(out, lambda m: m['calendar'].update(path='../outside'))
    before = state(desktop)
    assert apply_sync_source(desktop, out)['status'] == 'rejected'
    assert state(desktop) == before
    assert not (desktop.root / 'sources/tickflow/trading_calendar.json').exists()


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('failure', ['artifact', 'database'])
def test_calendar_failure_restores_inventory(sample, monkeypatch, existing, failure):
    _, desktop, out = sample
    relative = 'sources/tickflow/trading_calendar.json'
    if existing:
        desktop.write_artifact(relative, b'old calendar')
        desktop.write_artifact('sources/tickflow/universe.csv', b'old directory')
        desktop.execute("INSERT INTO meta VALUES('universe_date:tickflow', 'old-date')")
    before = state(desktop)
    original = desktop.connect
    @contextmanager
    def failing():
        with original() as db:
            db.execute("CREATE TEMP TRIGGER fail_sync BEFORE INSERT ON meta BEGIN SELECT RAISE(ABORT, 'injected'); END")
            yield db
    if failure == 'database':
        monkeypatch.setattr(desktop, 'connect', failing)
    else:
        write = desktop.write_artifact
        def fail_after_calendar(path, content, *args, **kwargs):
            result = write(path, content, *args, **kwargs)
            if path == relative and content != b'old calendar':
                raise RuntimeError('injected')
            return result
        monkeypatch.setattr(desktop, 'write_artifact', fail_after_calendar)
    with pytest.raises(Exception, match='injected'):
        apply_sync_source(desktop, out)
    monkeypatch.setattr(desktop, 'connect', original)
    assert state(desktop) == before
    for path, content in [(relative, b'old calendar'), ('sources/tickflow/universe.csv', b'old directory')]:
        if existing:
            assert (desktop.root / path).read_bytes() == content
        else:
            assert not (desktop.root / path).exists()


def corrected_export(source):
    path = source.root / 'snapshots' / ASOF / 'snapshot.json'
    snapshot = json.loads(path.read_text())
    snapshot['hits'] = [dict(observation_id='obs-1')]
    path.write_text(dumps(snapshot))
    return write_sync_source(ReadOnlyStore(source.root), ASOF, source.root / 'corrected')


def test_withdrawn_sync_candidate_removed(sample):
    source, desktop, out = sample
    assert apply_sync_source(desktop, out)['observations_removed'] == 0
    datasets = desktop.rows('SELECT * FROM datasets')
    result = apply_sync_source(desktop, corrected_export(source))
    assert result['status'] == 'applied'
    assert result['observations_removed'] == 1
    assert {r['observation_id'] for rows in load_candidates(desktop, 'daily', source='tickflow').values() for r in rows} == {'obs-1'}
    assert desktop.rows("SELECT id FROM observations WHERE id='obs-0'") == []
    assert desktop.rows('SELECT * FROM datasets') == datasets
    tracked = json.loads(desktop.rows("SELECT value FROM meta WHERE key='sync_observation_ids'")[0]['value'])
    assert tracked == dict(asof=ASOF, ids=['obs-1'])


def test_sync_preserves_local_same_day_candidate(sample):
    source, desktop, out = sample
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    desktop.execute("INSERT INTO observations SELECT 'local', job_id, code, strategy, version, timeframe, asof, setup_date, dataset_id, payload, created FROM observations WHERE id='obs-0'")
    assert apply_sync_source(desktop, corrected_export(source))['observations_removed'] == 1
    assert {r['id'] for r in desktop.rows('SELECT id FROM observations')} == {'local', 'obs-1'}


def test_first_sync_preserves_untracked_observation(sample):
    source, desktop, out = sample
    row = source.rows("SELECT * FROM observations WHERE id='obs-0'")[0]
    desktop.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
    result = apply_sync_source(desktop, corrected_export(source))
    assert result['observations_removed'] == 0
    assert len(desktop.rows('SELECT * FROM observations')) == 2


def test_cross_day_sync_preserves_history(sample):
    _, desktop, out = sample
    assert apply_sync_source(desktop, out)['status'] == 'applied'
    def next_day(manifest):
        manifest['asof'] = '2026-11-12'
        manifest['observations'] = [dict(manifest['observations'][1], id='next-day', asof='2026-11-12')]
    edit(out, next_day)
    assert apply_sync_source(desktop, out)['observations_removed'] == 0
    assert {r['id'] for r in desktop.rows('SELECT id FROM observations WHERE asof=?', (ASOF,))} == {'obs-0', 'obs-1'}


def test_withdrawal_rolls_back_on_failure(sample, monkeypatch):
    source, desktop, out = sample
    apply_sync_source(desktop, out)
    out = corrected_export(source)
    before = state(desktop)
    original = desktop.connect
    @contextmanager
    def failing():
        with original() as db:
            db.execute("CREATE TEMP TRIGGER fail_sync BEFORE INSERT ON meta BEGIN SELECT RAISE(ABORT, 'injected'); END")
            yield db
    monkeypatch.setattr(desktop, 'connect', failing)
    with pytest.raises(Exception, match='injected'):
        apply_sync_source(desktop, out)
    monkeypatch.setattr(desktop, 'connect', original)
    assert state(desktop) == before


def test_empty_sync_list_withdraws_all_tracked_candidates(sample):
    _, desktop, out = sample
    apply_sync_source(desktop, out)
    edit(out, lambda m: m.update(observations=[]))
    assert apply_sync_source(desktop, out)['observations_removed'] == 2
    assert desktop.rows('SELECT * FROM observations') == []
    assert len(desktop.rows('SELECT * FROM datasets')) == 2
