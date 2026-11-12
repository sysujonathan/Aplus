"""A weekly job cutoff must select its completed W-FRI result, not Thursday."""
import pandas as pd
import pytest

from gui.data import candidate_dates, load_candidates, scan_result_date
from gui.startup_reads import date_options
from workbench.market import save_dataset
from workbench.sources import set_market_source
from workbench.store import Store, now
from workbench.strategies import prepare


def sample(tmp_path, *, hit=True):
    store = Store(tmp_path / 'synthetic-runtime')
    set_market_source(store, 'tickflow')
    frame = pd.DataFrame(dict(date=pd.bdate_range('2026-10-19', '2026-11-12').strftime('%Y-%m-%d'),
                              open=10., high=11., low=9., close=10.5, volume=100.))
    dataset = save_dataset(store, '600000', frame, 'tickflow', '前复权')
    completed = prepare(frame, 'weekly', '2026-11-12').date.iloc[-1]
    assert completed == '2026-11-06'
    if hit:
        store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('synthetic-weekly-hit', 'synthetic-scan', 'sh.600000', 'MTR_MASTER',
             'test', 'weekly', completed, completed, dataset, '{}', now()))
    return store


@pytest.mark.parametrize('day', ['2026-11-06', '2026-11-07', '2026-11-08',
                                 '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12',
                                 '2026-11-13', '2026-01-01', '2027-01-01'])
def test_result_selector_matches_actual_weekly_aggregation(day):
    frame = pd.DataFrame(dict(date=pd.bdate_range(end=day, periods=15).strftime('%Y-%m-%d'),
                              open=10., high=11., low=9., close=10.5, volume=100.))
    assert scan_result_date(day, 'weekly') == prepare(frame, 'weekly', day).date.iloc[-1]
    assert scan_result_date(day, 'daily') == day


@pytest.mark.parametrize('hit', [True, False])
def test_weekly_inventory_uses_daily_input_without_writes_or_fake_hits(tmp_path, hit):
    store = sample(tmp_path, hit=hit)
    # A newer date in another provider must not leak into this source's view.
    other = pd.DataFrame([dict(date='2026-11-13', open=10., high=11., low=9., close=10.5, volume=100.)])
    save_dataset(store, '600000', other, 'baostock', '前复权')
    before = store.path.read_bytes()
    inventory = date_options(store.root, 'weekly')
    assert '2026-11-06' in inventory['dates']
    assert '2026-11-12' not in inventory['dates']
    assert '2026-11-13' not in inventory['dates']
    assert inventory['latest'] == ('2026-11-06' if hit else None)
    rows = load_candidates(store, 'weekly', 'tickflow', ('2026', '11', '06'))
    assert sum(map(len, rows.values())) == int(hit)
    assert candidate_dates(store, 'weekly', source='baostock') == []
    assert candidate_dates(store, 'weekly', source='tickflow') == (['2026-11-06'] if hit else [])
    assert candidate_dates(store, 'daily') == ['2026-11-12']
    assert store.path.read_bytes() == before


@pytest.mark.native_process
@pytest.mark.parametrize('kind', ['扫描策略', '一键盘前'])
@pytest.mark.parametrize('hit', [True, False])
@pytest.mark.parametrize('status', ['completed', 'partial'])
def test_completed_weekly_task_refresh_selects_saved_week_without_date_error(tmp_path, kind, hit, status):
    from gui.main_window import AplusMainWindow
    from tests.test_startup_responsive import spin
    store = sample(tmp_path, hit=hit)
    window = AplusMainWindow(store=store, enable_tray=False)
    try:
        window.candidates.on_rows_changed = lambda rows: False  # Date/list test; no chart process.
        spin(window, lambda: not window.toolbar._dates_pending)
        window._tf_var.set('weekly')
        result = dict(asof='2026-11-12', sources={'tickflow':dict(
            scan_status=status, scan_result=dict(asof='2026-11-12', signals=int(hit)))})
        window._on_job_finished(kind, status, result)
        spin(window, lambda: not window.toolbar._dates_pending and not window.candidates._loading)
        assert window.toolbar._dates_error is None
        assert window.toolbar.selected_date() == ('2026', '11', '06')
        assert window._cur_date == ('2026', '11', '06')
        assert sum(map(len, window.candidates._rows.values())) == int(hit)
    finally:
        window.destroy()
