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
