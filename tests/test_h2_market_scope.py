import pytest

from gui.h2_replay_chart import render_replay
from workbench.h2_replay import MODEL, run_replay
from workbench.h2_replay_service import available_snapshots, load_receipt
from workbench.market import BOARDS, save_dataset
from workbench.service import Service
from workbench.store import Store
from tests.test_h2_plan import h2_bars, h2_spec
from tests.test_workbench import wait_for


def test_market_boards_latest_snapshot_and_empty_scope(tmp_path, h2_bars):
    store = Store(tmp_path / 'boards')
    codes = ('sh.600000', 'sz.300750', 'sh.688001', 'bj.920002')
    ids = [save_dataset(store, code, h2_bars, 'csv', '合成工程测试') for code in codes]
    for board, did in zip(BOARDS, ids):
        rows, missing = available_snapshots(store, 'market', [board])
        assert [row['id'] for row in rows] == [did] and not missing
    rows, _ = available_snapshots(store, 'market', list(BOARDS))
    assert {row['id'] for row in rows} == set(ids)
    rows, _ = available_snapshots(store, 'market', ['创业板', '科创板'])
    assert {row['code'] for row in rows} == set(codes[1:3])
    changed = h2_bars.copy()
    changed.loc[changed.index[-1], 'volume'] += 1
    latest = save_dataset(store, codes[1], changed, 'csv', '合成工程测试')
    assert [row['id'] for row in available_snapshots(store, 'market', ['创业板'])[0]] == [latest]
    for boards in ([], ['未知板块']):
        with pytest.raises(ValueError, match='板块'):
            available_snapshots(store, 'market', boards)


def test_service_rejects_symbol_outside_recorded_board_scope(tmp_path, h2_bars):
    store = Store(tmp_path / 'scope')
    did = save_dataset(store, 'sz.300750', h2_bars, 'csv', '合成工程测试')
    service = Service(store)
    try:
        job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily',
            execution_model=MODEL, scope='market', boards=['沪深主板'], datasets=[did],
            start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1]))
        assert wait_for(store, job)['status'] == 'partial'
        _, report, records = load_receipt(store, job)
        assert report['boards'] == ['沪深主板'] and not records
        assert '板块' in report['errors'][0]['error']
        assert report['processed_datasets'] == 0
        assert not store.rows('SELECT * FROM observations')
    finally:
        service.pool.shutdown()


def test_native_replay_render_matches_wide_and_tall_viewport(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path / 'chart')
    did = save_dataset(store, 'sh.600000', h2_bars, 'csv', '合成工程测试')
    record = run_replay(h2_spec, h2_bars, h2_bars.date.iloc[124], h2_bars.date.iloc[-1])[0]
    record['dataset_id'] = did
    for size in ((1440, 700), (1000, 820)):
        image = render_replay(store, record, 0, pixel_size=size)
        assert abs(image.width - size[0]) <= 1 and abs(image.height - size[1]) <= 1
