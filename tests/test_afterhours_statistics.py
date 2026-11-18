"""Descriptive statistics never recalculate executions or mutate old reports."""
from copy import deepcopy
import math

import pytest

from gui.research_views import holding_distribution, trade_statistics


def closed(bars=None, net_r=None, reason=None):
    return dict(status='closed', holding_bars=bars, r_multiple=net_r, reason=reason)


def test_holding_median_bins_tails_and_filter_denominators():
    records = [closed(n, 1 if i % 2 else -1) for i, n in enumerate(
        [1, 2, 3, 4, 5, 6, 10, 11, 20, 21, 30, 31, 67])]
    records += [closed(2, 0), dict(status='open', holding_bars=3, r_multiple=5),
                dict(status='unfilled', holding_bars=2, r_multiple=-1)]
    before = deepcopy(records)
    all_ = holding_distribution(records)
    assert all_['total'] == 14 and all_['median'] == 8
    assert [r['count'] for r in all_['buckets']] == [1, 3, 2, 2, 2, 2, 2]
    assert sum(r['share'] for r in all_['buckets']) == pytest.approx(1)
    for category, total in [('盈利', 6), ('亏损', 7)]:
        data = holding_distribution(records, category)
        assert data['total'] == total
        assert sum(r['count'] for r in data['buckets']) == total
        assert sum(r['share'] for r in data['buckets']) == pytest.approx(1)
    assert records == before
    assert holding_distribution([closed(1), closed(2)])['median'] == 1.5
    assert len(holding_distribution([closed(2)])['buckets']) == 5
    assert len(holding_distribution([closed(30)])['buckets']) == 6


@pytest.mark.parametrize('value', [None, 0, -1, 1.5, True, '5', math.inf, math.nan])
def test_invalid_holding_values_do_not_become_zero_or_enter_denominator(value):
    data = holding_distribution([closed(value, 2)])
    assert data['total'] == 0 and data['median'] is None
    assert all(row['share'] is None for row in data['buckets'])


def test_exit_categories_keep_legacy_mm_and_current_2r_distinct():
    reasons = ['2R 止盈', 'MM 止盈', 'SL 止损（同日双触碰优先止损）',
               'SL1 止损（同日双触碰优先止损）', '跳空止损', 'T+1 延迟止损',
               '持有期限退出', '人工研究退出', '', None, 123]
    stats = trade_statistics([closed(3, 0, reason) for reason in reasons])
    rows = {r['label']: r for r in stats['exits']}
    assert stats['exit_count'] == 8
    assert {key: r['count'] for key, r in rows.items()} == {
        '2R止盈': 1, 'MM止盈': 1, '止损': 4, '期限退出': 1, '其他': 1}
    assert rows['止损']['share'] == .5
    assert sum(r['share'] for r in rows.values()) == pytest.approx(1)
    assert not any(r['label'] == '其他' for r in trade_statistics([closed(2, 1, 'MM 止盈')])['exits'])


def test_net_r_means_and_strict_excess_loss_thresholds():
    records = [closed(2, r) for r in [0, 1, 3, -1, -1.01, -2, -2.01]]
    records += [closed(3, None), closed(3, math.nan), closed(3, math.inf), closed(3, True),
                dict(status='open', r_multiple=-9), dict(status='pending', r_multiple=10)]
    stats = trade_statistics(records)
    assert stats['valid_r_count'] == 7
    assert stats['average_win_r'] == 2
    assert stats['average_loss_r'] == pytest.approx(-1.505)
    assert stats['loss_over_1r'] == pytest.approx(3/7)
    assert stats['loss_over_2r'] == pytest.approx(1/7)


def test_old_missing_fields_and_no_closed_samples_remain_unknown():
    for records in ([], [dict(status='open')], [dict(status='closed')]):
        stats = trade_statistics(records)
        assert stats['holding']['median'] is None
        assert stats['average_win_r'] is stats['average_loss_r'] is None
        assert stats['loss_over_1r'] is stats['loss_over_2r'] is None
        assert stats['exits'] == []


def test_zero_r_is_closed_but_neither_a_win_nor_a_loss():
    records = [closed(4, 0, '期限退出'), closed(6, None, '期限退出')]
    stats = trade_statistics(records)
    assert stats['holding']['median'] == 5
    assert stats['average_win_r'] is stats['average_loss_r'] is None
    assert stats['valid_r_count'] == 1 and stats['exit_count'] == 2
    assert stats['loss_over_1r'] == stats['loss_over_2r'] == 0
    assert holding_distribution(records, '盈利')['total'] == 0
    assert holding_distribution(records, '亏损')['total'] == 0


def test_annual_replay_matches_eligible_signals_and_closed_totals():
    from gui.research_views import comparison_rows

    records = [
        dict(closed(2, 2), setup_date='2022-12-30', exit_date='2023-01-04'),
        dict(closed(4, -1), setup_date='2023-01-03'),
        dict(status='open', setup_date='2023-06-01'),
        dict(status='filtered', setup_date='2024-06-01'),
        closed(8),
    ]
    report = dict(opportunities=4, closed_trades=3)
    rows, note = trade_statistics(records, report)['annual']
    assert not note
    assert rows == [('2022', 1, 1, '100.00%', '2.00R'),
                    ('2023', 2, 1, '0.00%', '-1.00R'),
                    ('年份缺失或无效', 1, 1, '缺有效盈亏（1笔）', '缺有效R（1笔）')]
    assert sum(row[2] for row in rows) == report['closed_trades']
    rows = comparison_rows(report, dict(opportunities=3))
    index = next(i for i, row in enumerate(rows) if row[0] == '机会数')
    assert rows[index] == ('机会数', '4', '3', '+1')
    assert rows[index + 1][-1] == '75.00%'
    assert comparison_rows(dict(opportunities=0), report)[index + 1][-1] == '基线无信号'
    assert comparison_rows(report, {})[index + 1][-1] == '缺信号数'


def test_generic_holdings_and_annual_statistics_include_legacy_rows():
    from pandas import DataFrame
    from gui.research_views import generic_report_statistics

    trades = DataFrame([
        dict(closed(1, 2), signal_date='2022-12-30', net_return=.1),
        dict(closed(4, -1), signal_date='2023-01-03', net_return=-.1),
        dict(closed(31, 1), signal_date='2023-01-04', net_return=.05),
        dict(closed(2, 0), signal_date='2023-01-05', net_return=0.),
        dict(status='open', signal_date='2023-01-06', holding_bars=700),
    ])
    report = dict(signals=5, closed_trades=4)
    rows, annual_note, holding, holding_note = generic_report_statistics(trades, report)
    assert not annual_note and not holding_note
    assert rows == [('2022', 1, 1, '100.00%', '2.00R'), ('2023', 4, 3, '33.33%', '0.00R')]
    assert (holding['total'], holding['mean'], holding['median']) == (4, 9.5, 3)
    assert (holding['win_median'], holding['loss_median']) == (16, 4)
    assert [row['count'] for row in holding['buckets']] == [1, 1, 1, 0, 0, 0, 1]

    legacy = trades.drop(columns=['signal_date', 'holding_bars', 'net_return'])
    rows, annual_note, holding, holding_note = generic_report_statistics(legacy, report)
    assert rows == [('年份缺失或无效', 5, 4, '缺有效盈亏（4笔）', '0.50R')]
    assert not annual_note and not holding['total']
    assert holding_note == '4 笔已结束记录缺有效持仓根数。'
    assert '未对齐' in generic_report_statistics(DataFrame(), report)[1]


def test_saved_generic_reports_open_and_compare_in_research_page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from workbench.store import ROOT, Store, dumps, now

    store = Store(tmp_path / 'research-runtime')
    monkeypatch.setenv('A_WORKBENCH_HOME', str(store.root))
    csv = (b'signal_date,status,holding_bars,r_multiple,net_return\n'
           b'2022-12-30,closed,1,2,0.1\n2023-01-03,closed,4,-1,-0.1\n'
           b'2023-01-04,closed,31,1,0.05\n2023-01-05,open,,,\n')
    for jid, signals, closed_count, content in (
        ('baseline', 4, 3, csv),
        ('variant', 3, 3, b'status,r_multiple\nclosed,2\nclosed,-1\nclosed,1\n'),
        ('missing', 1, 1, None),
    ):
        report = dict(signals=signals, closed_trades=closed_count, win_rate=2/3,
                      mean_return=.01, mean_r=.67, open_trades=signals-closed_count,
                      strategy_version='legacy', real_data=False, errors=[], note='构造的历史回执',
                      limitations=[], report_path=f'research/{jid}/report.json',
                      trades_path=f'research/{jid}/trades.csv')
        store.write_artifact(report['report_path'], dumps(report).encode())
        if content is not None:
            store.write_artifact(report['trades_path'], content)
        store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                      (jid, 'backtest', 'completed', now(), dumps({'strategy': 'STRATEGY_GAP_H2'}), dumps(report)))

    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
    app.sidebar.radio[0].set_value('回测研究').run()
    selector = next(widget for widget in app.multiselect if widget.label.startswith('选择报告'))
    selector.set_value(['baseline', 'variant', 'missing']).run()
    assert not app.exception
    assert not app.error
    frames = [table.value for table in app.dataframe]
    annual = next(frame for frame in frames if '年份' in frame and '2022' in set(frame['年份']))
    assert annual['已结束笔数'].tolist() == [1, 2]
    holding = next(frame for frame in frames if '持仓根数' in frame)
    assert holding['笔数'].tolist() == [1, 0, 1, 0, 0, 0, 1]
    comparison = next(frame for frame in frames if '项目' in frame)
    assert comparison['variant'].tolist() == ['+1', '75.00%']
    assert any('年份缺失或无效' in set(frame['年份']) for frame in frames if '年份' in frame)
    assert any('缺有效持仓根数' in element.value for element in app.caption)
    assert any('逐笔明细 CSV 缺失' in element.value for element in app.info)
    app.toggle(key='baseline_show_trades').set_value(True).run()
    assert any('signal_date' in table.value for table in app.dataframe)
    assert (store.root / 'research/baseline/trades.csv').read_bytes() == csv
    for table in ('observations', 'plans'):
        assert not store.rows(f'SELECT * FROM {table}')
