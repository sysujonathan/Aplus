"""Display-only history management preserves research receipts and stable identity."""
import pytest

from workbench.store import Store, dumps
from workbench.h2_replay import MODEL


def add_job(store, identity, status='completed', kind='backtest', result=None):
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  (identity, kind, status, '2026-10-05T01:00:00+00:00', '{}',
                   dumps({'execution_model': MODEL}) if result is None else result))


def test_history_stable_numbers_names_delete_restore_and_immutable_reports(tmp_path):
    store = Store(tmp_path)
    add_job(store, 'old', 'cancelled')
    add_job(store, 'new', 'partial')
    add_job(store, 'running', 'running')
    add_job(store, 'scan', kind='scan')
    add_job(store, 'invalid', result='[]')
    add_job(store, 'other', result=dumps({'execution_model': 'other'}))
    before = store.rows('SELECT * FROM jobs ORDER BY id')
    history = store.backtest_history(MODEL)
    assert [row['id'] for row in history] == ['new', 'old']
    assert [row['display']['number'] for row in history] == [2, 1]
    store.rename_backtest('old', '  主板基准  ')
    store.set_backtest_deleted('old')
    assert [row['id'] for row in store.backtest_history(MODEL)] == ['new']
    store = Store(tmp_path)
    deleted = store.backtest_history(MODEL, include_deleted=True)[1]
    assert deleted['display'] == dict(number=1, name='主板基准', deleted=True)
    add_job(store, 'third')
    assert store.backtest_history(MODEL)[0]['display']['number'] == 3
    store.set_backtest_deleted('old', False)
    assert store.backtest_history(MODEL)[-1]['display'] == dict(number=1, name='主板基准', deleted=False)
    assert store.rows("SELECT * FROM jobs WHERE id!='third' ORDER BY id") == before
    for table in ('observations', 'plans'):
        assert not store.rows(f'SELECT * FROM {table}')


@pytest.mark.parametrize('name', ['', ' ', 'x'*41, 'name\nline', None])
def test_invalid_names_do_not_change_display(tmp_path, name):
    store = Store(tmp_path)
    add_job(store, 'job')
    before = store.backtest_history(MODEL)
    with pytest.raises(ValueError):
        store.rename_backtest('job', name)
    assert store.backtest_history(MODEL) == before


def test_cannot_manage_active_missing_or_non_backtest_jobs(tmp_path):
    store = Store(tmp_path)
    for identity, status, kind in [('running', 'running', 'backtest'), ('queued', 'queued', 'backtest'),
                                   ('scan', 'completed', 'scan')]:
        add_job(store, identity, status, kind)
    store.backtest_history(MODEL)
    for identity in ('running', 'queued', 'scan', 'missing'):
        with pytest.raises(ValueError):
            store.set_backtest_deleted(identity)
    with pytest.raises(ValueError):
        store.set_backtest_deleted('missing', 'yes')
