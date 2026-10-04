from copy import deepcopy
import pytest

from gui.research_views import comparison_rows, scope_caption
from workbench.h2_replay_service import available_snapshots
from workbench.market import BOARDS, save_dataset
from workbench.store import Store
from workbench.h2_replay import MODEL
from workbench.h2_replay_service import load_receipt
from workbench.service import Service
from tests.test_workbench import wait_for
from tests.test_h2_plan import h2_bars


def test_market_boards_use_shared_instrument_classification(tmp_path, h2_bars):
    store = Store(tmp_path)
    codes = ['sh.600000', 'sz.300001', 'sh.688001', 'bj.920001']
    for code in codes:
        save_dataset(store, code, h2_bars, 'csv', '隔离工程样本')
    for board, code in zip(BOARDS, codes):
        rows, missing = available_snapshots(store, 'market', [board])
        assert [r['code'] for r in rows] == [code]
        assert not missing
    assert len(available_snapshots(store, 'market', list(BOARDS))[0]) == 4
    with pytest.raises(ValueError):
        available_snapshots(store, 'market', [])
    with pytest.raises(ValueError):
        available_snapshots(store, 'market', ['虚构板块'])


def test_comparison_unknown_and_changed_inputs_never_claim_equivalence():
    a = dict(boards=list(BOARDS), start='2026-01-01', end='2026-10-02',
             h2_settings={'max_risk_pct': 0}, assumptions={'commission_bps': 3},
             datasets=[{'id': 'snapshot-A'}], strategy_version='v1', engine_version='e1',
             opportunities=10, filled=8, closed_trades=6, win_rate=.5, mean_r=0, median_risk_pct=8)
    b = deepcopy(a)
    b.update(datasets=[{'id': 'snapshot-B'}], mean_r=None, win_rate=.4)
    before = deepcopy((a, b))
    rows = {r[0]: r[1:] for r in comparison_rows(a, b)}
    assert rows['行情快照'][2] == '不同'
    assert rows['胜率'][2] == '+10.00 个百分点'
    assert rows['平均净 R'] == ('0.00R', '—', '—')
    assert (a, b) == before
    old = dict(a)
    del old['boards']
    del old['datasets']
    assert '未记录' in scope_caption(old)
    unknown = {r[0]: r[1:] for r in comparison_rows(old, old)}
    assert unknown['行情快照'][2] == '未记录'
    assert unknown['市场范围'][2] == '未记录'


def test_two_runs_keep_original_receipt_and_market_provenance(tmp_path, h2_bars):
    store = Store(tmp_path)
    did = save_dataset(store, 'sh.600000', h2_bars, 'csv', '隔离工程样本')
    service = Service(store)
    spec = dict(strategy='STRATEGY_GAP_H2', timeframe='daily', execution_model=MODEL,
                datasets=[did], start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1],
                scope='market', boards=[BOARDS[0]])
    try:
        first = service.submit('backtest', spec)
        assert wait_for(store, first)['status'] == 'completed'
        _, original, records = load_receipt(store, first)
        raw = store.read_artifact(original['records_path'])
        second = service.submit('backtest', dict(spec, h2_settings={'max_risk_pct': 1}))
        assert wait_for(store, second)['status'] == 'completed'
        _, changed, _ = load_receipt(store, second)
        assert first != second
        assert original['boards'] == changed['boards'] == [BOARDS[0]]
        assert changed['h2_settings'] != original['h2_settings']
        assert load_receipt(store, first)[1:] == (original, records)
        assert store.read_artifact(original['records_path']) == raw
        assert original['report_path'] != changed['report_path']
        for table in ('observations', 'plans', 'positions', 'position_fills', 'closed_trades'):
            assert not store.rows(f'SELECT * FROM {table}')
    finally:
        service.pool.shutdown()
