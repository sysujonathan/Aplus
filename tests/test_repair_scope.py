import json
from threading import Event
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.test_workbench import store
from workbench.readiness import save_calendar, save_directory
from workbench.repair_scope import cached_baostock_repair_scope
from workbench.service import Service
from workbench.store import dumps

DAY = '2026-10-09'
CODES = ['sh.600000', 'sh.600001']


def certified_scope(store, job='original', status='partial', source='baostock', reused=False):
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  (job, 'sync', status, '2026-10-09T16:00:00', dumps(dict(source=source, boards=['沪深主板'])),
                   dumps(dict(source=source, end_requested=DAY))))
    calendar = pd.DataFrame(dict(calendar_date=['2026-10-08', DAY], is_trading_day=['1', '1']))
    directory = pd.DataFrame(dict(code=CODES, code_name=['A', 'B'], tradeStatus=['1', '0']))
    basics = pd.DataFrame(dict(code=CODES, ipoDate=['2000-01-01']*2, outDate=['']*2, type=['1']*2, status=['1']*2))
    save_calendar(store, calendar, '2026-10-08', DAY, job)
    save_directory(store, directory, DAY, job, basics=basics)
    if reused:
        certified_scope(store, job='reused', status=status, source=source)


@pytest.mark.parametrize('reused', [False, True])
def test_legacy_completed_files_reused_read_only(store, reused):
    certified_scope(store, reused=reused)
    before = store.rows('SELECT * FROM events')
    value = cached_baostock_repair_scope(store, DAY)
    assert value[0] == DAY and value[1].code.tolist() == CODES
    assert store.rows('SELECT * FROM events') == before
    assert store.rows('SELECT * FROM datasets') == []


@pytest.mark.parametrize('change', ['universe', 'dated', 'basics', 'calendar', 'date',
                                   'source', 'failed', 'running', 'no_proof', 'no_job', 'missing'])
def test_uncertified_stale_or_changed_files_not_reused(store, change):
    certified_scope(store, source='tencent' if change=='source' else 'baostock',
                    status=change if change in ('failed', 'running') else 'partial')
    if change in ('universe', 'dated', 'basics', 'calendar'):
        name = dict(universe='universe.csv', dated=f'directories/{DAY}.csv',
                    basics=f'directories/{DAY}-basics.csv', calendar='trading_calendar.json')[change]
        path = store.root/name
        # Even a whitespace-only, still-parseable edit invalidates prior proof.
        store.write_artifact(name, path.read_bytes()+b'\n')
    elif change=='date':
        store.execute("UPDATE meta SET value='2026-10-08' WHERE key='universe_date'")
    elif change=='no_proof':
        store.execute("DELETE FROM events WHERE action='写入文件'")
    elif change=='no_job':
        store.execute('DELETE FROM jobs')
    elif change=='missing':
        # Use a missing path with unchanged files; no destructive fixture operation.
        store.execute("UPDATE events SET path='absent' WHERE path LIKE '%-basics.csv'")
    assert cached_baostock_repair_scope(store, DAY) is None
    assert cached_baostock_repair_scope(store, '2026-10-12') is None


@pytest.mark.parametrize('repair', [CODES[:1], CODES[1:], ['sh.600999'], None])
def test_service_repair_reuses_certified_scope_only_and_preserves_gate(store, monkeypatch, repair):
    certified_scope(store)
    calls, preflight, progress = [], [], []
    class Fake:
        def __enter__(self):
            preflight.append('enter'); return self
        def __exit__(self, *args): pass
        def universe(self, day):
            preflight.append('directory')
            return pd.read_csv(store.root/'universe.csv', dtype=str)
        def basics(self):
            preflight.append('basics')
            return pd.read_csv(store.root/f'directories/{DAY}-basics.csv', dtype=str, keep_default_na=False)
    def results(*args, **kwargs):
        calls.append((args[2], args[6], args[9]))
        yield from ()
    monkeypatch.setattr('workbench.service.BaoStock', Fake)
    monkeypatch.setattr('workbench.service.sync_results', results)
    monkeypatch.setattr('workbench.service.completed_date', lambda: DAY)
    service = SimpleNamespace(store=store, cancel_flags={'repair': Event()}, progress=lambda *a: progress.append(a))
    spec = dict(source='baostock', boards=['沪深主板'], start='2026-10-08', end=DAY)
    if repair is not None: spec['repair_codes'] = repair
    if repair == ['sh.600999']:
        with pytest.raises(ValueError, match='不属于'):
            Service._sync(service, 'repair', spec)
        assert calls == [] and preflight == []
        return
    report = Service._sync(service, 'repair', spec)
    assert report['connections'] == 1 and report['coverage']['ready'] == 0
    assert report['scope_reused'] == (repair is not None)
    assert bool(preflight) == (repair is None)  # Normal update refreshes identities.
    assert report['suspended'] == int(repair != CODES[:1])
    assert calls == [([] if repair == CODES[1:] else CODES[:1], repair is not None, 1)]
    if repair:
        assert '直接补拉' in progress[0][-1]


def test_partial_sync_that_failed_before_basics_has_no_reusable_context(store):
    certified_scope(store)
    store.execute("DELETE FROM events WHERE path LIKE '%-basics.csv'")
    assert cached_baostock_repair_scope(store, DAY) is None
