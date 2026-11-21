"""Display-only history management preserves research receipts and stable identity."""
import pytest

from workbench.store import Store, dumps
from workbench.h2_replay import LEGACY_MODEL, MODEL, SUPPORTED_MODELS


def add_job(store, identity, status='completed', kind='backtest', result=None, model=MODEL):
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  (identity, kind, status, '2026-10-05T01:00:00+00:00', '{}',
                   dumps({'execution_model': model}) if result is None else result))


def test_history_can_list_legacy_and_current_without_relabeling_reports(tmp_path):
    store = Store(tmp_path)
    add_job(store, 'legacy', model=LEGACY_MODEL)
    add_job(store, 'current')
    rows = store.backtest_history(SUPPORTED_MODELS)
    assert [row['id'] for row in rows] == ['current', 'legacy']
    assert [row['display']['number'] for row in rows] == [2, 1]


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


def test_research_note_persists_and_clears_without_rewriting_receipts(tmp_path):
    store = Store(tmp_path)
    add_job(store, 'old', model=LEGACY_MODEL)
    add_job(store, 'new')
    before = store.backtest_history(SUPPORTED_MODELS)
    jobs = store.rows('SELECT * FROM jobs ORDER BY id')
    note = dict(hypothesis='假设', conclusion=' 原始结论\n第二行 ', next_step='复核未成交', author='研究员',
                date='2026-11-21')
    store.set_backtest_research_note('new', note)
    after = Store(tmp_path).backtest_history(SUPPORTED_MODELS)
    assert after[0]['display'] == dict(before[0]['display'], research_note=note)
    assert after[1] == before[1]
    store.rename_backtest('new', '新名称')
    assert store.backtest_history(SUPPORTED_MODELS)[0]['display']['research_note'] == note
    store.rename_backtest('new', '回测')
    before[0]['display']['name'] = '回测'
    store.set_backtest_research_note('new', dict.fromkeys(note, ''))
    assert store.backtest_history(SUPPORTED_MODELS) == before
    assert store.rows('SELECT * FROM jobs ORDER BY id') == jobs


@pytest.mark.parametrize('note', [
    [], {'unknown': ''}, {'conclusion': None}, {'conclusion': 'x' * 501},
    {'hypothesis': 'x' * 501}, {'next_step': 'x' * 501}, {'author': 'x' * 41},
    {'author': 'a\nb'}, {'conclusion': 'a\x7fb'}, {'hypothesis': 'a\tb'},
    {'date': '2026-02-30'}, {'date': '20261121'},
])
def test_invalid_research_note_does_not_change_display(tmp_path, note):
    store = Store(tmp_path)
    add_job(store, 'job')
    before = store.backtest_history(MODEL)
    with pytest.raises(ValueError):
        store.set_backtest_research_note('job', note)
    assert store.backtest_history(MODEL) == before


def test_latest_research_uses_history_order_across_models_and_ignores_deleted(tmp_path):
    from gui.afterhours_workspace import latest_research_row

    store = Store(tmp_path)
    add_job(store, 'current')
    add_job(store, 'legacy', model=LEGACY_MODEL)
    add_job(store, 'unrecorded')
    store.backtest_history(SUPPORTED_MODELS)
    store.set_backtest_research_note('current', {'conclusion': '现版', 'date': '2026-11-22'})
    store.set_backtest_research_note('legacy', {'conclusion': '旧版', 'date': '2026-11-21'})

    def latest():
        return latest_research_row(store.backtest_history(SUPPORTED_MODELS, include_deleted=True))

    assert latest()['id'] == 'legacy'
    store.set_backtest_deleted('legacy')
    assert latest()['id'] == 'current'
    store.set_backtest_research_note('current', None)
    assert latest() is None
    store.set_backtest_deleted('legacy', False)
    assert latest()['id'] == 'legacy'
