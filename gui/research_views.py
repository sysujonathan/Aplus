"""Read-only presentation and descriptive statistics of saved research trades."""
import math
from bisect import bisect_left
from collections import Counter
from datetime import date
from statistics import mean

from workbench.h2_replay import LABELS


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


HOLDING_BOUNDS = (1, 3, 5, 10, 20, 30, math.inf)
HOLDING_LABELS = ('1根', '2–3根', '4–5根', '6–10根', '11–20根', '21–30根', '超过30根')


def _holding_bars(bars):
    if _finite_number(bars) and bars >= 1 and int(bars) == bars:
        return int(bars)
    return None


def _holding_summary(frequencies):
    counts = [0] * len(HOLDING_BOUNDS)
    total = sum(frequencies.values())
    elapsed = position = lower = upper = 0
    for bars, count in sorted(frequencies.items()):
        counts[bisect_left(HOLDING_BOUNDS, bars)] += count
        elapsed += bars * count
        if position <= (total - 1) // 2 < position + count:
            lower = bars
        if position <= total // 2 < position + count:
            upper = bars
        position += count
    last = max(4, max((i for i, count in enumerate(counts) if count), default=4))
    return dict(total=total, median=(lower + upper) / 2 if total else None,
                mean=elapsed / total if total else None,
                buckets=[dict(label=label, count=count, share=count/total if total else None)
                         for label, count in zip(HOLDING_LABELS[:last+1], counts[:last+1])])


def holding_distribution(records, category='全部'):
    """Elapsed bars; filter only this distribution, never the report."""
    if category not in ('全部', '盈利', '亏损'):
        raise ValueError('未知持仓分布类别')
    samples = Counter()
    for record in records:
        if record.get('status') != 'closed':
            continue
        net_r = record.get('r_multiple')
        if category != '全部' and (not _finite_number(net_r) or
                (net_r <= 0 if category == '盈利' else net_r >= 0)):
            continue
        bars = _holding_bars(record.get('holding_bars'))
        if bars is not None:
            samples[bars] += 1
    return _holding_summary(samples)


def _year(value):
    if isinstance(value, str) and len(value) >= 10 and value[4] == '-' and value[7] == '-':
        try:
            return date.fromisoformat(value[:10]).year
        except ValueError:
            pass
    return None


class _AnnualStatistics:
    def __init__(self):
        self.groups = {}
        self.signal_count = self.closed_count = 0

    def add(self, year, closed, net_r, win_value):
        group = self.groups.get(year)
        if group is None:
            group = self.groups[year] = [0, 0, 0, 0, 0, 0.0]
        group[0] += 1
        self.signal_count += 1
        if closed:
            group[1] += 1
            self.closed_count += 1
            if win_value is not None:
                group[2] += 1
                group[3] += win_value > 0
            if net_r is not None:
                group[4] += 1
                group[5] += net_r

    def rows(self):
        years = sorted(year for year in self.groups if year is not None)
        if None in self.groups:
            years.append(None)
        rows = []
        for year in years:
            signals, closed, known_wins, wins, known_r, total_r = self.groups[year]
            rows.append((str(year) if year is not None else '年份缺失或无效', signals, closed,
                         _annual_metric(wins, known_wins, closed, '盈亏', '{:.2%}'),
                         _annual_metric(total_r, known_r, closed, 'R', '{:.2f}R')))
        return rows


ANNUAL_COLUMNS = ('年份', '信号数', '已结束笔数', '胜率', '平均 R')


def _annual_metric(total, valid, closed, field, fmt):
    if not closed:
        return '无已结束样本'
    if valid < closed:
        return f'缺有效{field}（{closed - valid}笔）'
    return fmt.format(total / closed)


def _annual_result(annual, report, signal_key):
    rows = annual.rows()
    signals, closed = report.get(signal_key), report.get('closed_trades')
    if signals != annual.signal_count or closed != annual.closed_count:
        note = (f'逐笔明细与汇总未对齐：信号 {annual.signal_count}/{signals}，'
                f'已结束 {annual.closed_count}/{closed}。')
    else:
        note = '' if rows else '无信号样本。'
    return rows, note


def generic_report_statistics(trades, report):
    """Aggregate the existing CSV columns in one pass without changing the rows."""
    annual = _AnnualStatistics()
    samples, wins, losses = Counter(), Counter(), Counter()
    classified = 0
    if 'status' not in trades and len(trades):
        return [], '逐笔明细缺状态列，无法统计。', {}, '逐笔明细缺状态列，无法统计。'
    import numpy as np
    import pandas as pd

    def numeric_column(key):
        column = trades.get(key)
        if column is None or pd.api.types.is_bool_dtype(column):
            return np.full(len(trades), np.nan)
        return pd.to_numeric(column, errors='coerce').to_numpy(dtype=float, na_value=np.nan)

    dates = trades.get('signal_date')
    if dates is None:
        years = np.zeros(len(trades), dtype=int)
    else:
        codes, stamps = pd.factorize(dates)
        years = np.array([_year(stamp) or 0 for stamp in stamps] + [0])[codes]
    status = trades.get('status')
    closed = status.eq('closed').fillna(False).to_numpy(dtype=bool) if status is not None else np.zeros(len(trades), dtype=bool)
    net_r, returns, bars = map(numeric_column, ('r_multiple', 'net_return', 'holding_bars'))
    valid_r = closed & np.isfinite(net_r)
    valid_return = closed & np.isfinite(returns)
    valid_bars = closed & np.isfinite(bars) & (bars >= 1) & (bars == np.floor(bars))
    # Reduce the trade rows once, then build both views from the same counts.
    grouped = pd.DataFrame(dict(
        year=years, bars=np.where(valid_bars, bars, 0),
        side=np.where(valid_r & valid_bars, np.sign(net_r), 0),
        signals=1, closed=closed, known_wins=valid_return, wins=valid_return & (returns > 0),
        known_r=valid_r, total_r=np.where(valid_r, net_r, 0),
    )).groupby(['year', 'bars', 'side'], sort=False, as_index=False).sum()
    for year, bars, side, signals, closed, known_wins, won, known_r, total_r in grouped.itertuples(index=False, name=None):
        group = annual.groups.setdefault(year or None, [0, 0, 0, 0, 0, 0.0])
        group[0] += signals
        group[1] += closed
        group[2] += known_wins
        group[3] += won
        group[4] += known_r
        group[5] += total_r
        annual.signal_count += signals
        annual.closed_count += closed
        if bars:
            bars = int(bars)
            samples[bars] += signals
            classified += known_r
            if side > 0:
                wins[bars] += signals
            elif side < 0:
                losses[bars] += signals
    rows, annual_note = _annual_result(annual, report, 'signals')
    holding = _holding_summary(samples)
    holding.update(win_median=_holding_summary(wins)['median'],
                   loss_median=_holding_summary(losses)['median'], classified=classified)
    expected = report.get('closed_trades', annual.closed_count)
    missing = max(annual.closed_count, expected if _finite_number(expected) else 0) - holding['total']
    holding_note = f'{missing} 笔已结束记录缺有效持仓根数。' if missing else ''
    if not holding['total'] and not holding_note:
        holding_note = '无已结束样本。'
    return rows, annual_note, holding, holding_note


def trade_statistics(records, report=None):
    """Derive replay display metrics without rewriting old receipts or prices."""
    annual = _AnnualStatistics() if report is not None else None
    years = {}
    samples = Counter()
    wins, losses = [], []
    valid_r_count = over_1r = over_2r = 0
    reasons = Counter()
    for record in records:
        status = record.get('status')
        if status == 'filtered':
            continue
        closed = status == 'closed'
        net_r = record.get('r_multiple') if closed else None
        if closed and not _finite_number(net_r):
            net_r = None
        if annual is not None:
            stamp = record.get('setup_date')
            year = None
            if isinstance(stamp, str):
                if stamp not in years:
                    years[stamp] = _year(stamp)
                year = years[stamp]
            annual.add(year, closed, net_r, net_r)
        if not closed:
            continue
        bars = _holding_bars(record.get('holding_bars'))
        if bars is not None:
            samples[bars] += 1
        if net_r is not None:
            valid_r_count += 1
            if net_r > 0:
                wins.append(net_r)
            elif net_r < 0:
                losses.append(net_r)
                over_1r += net_r < -1
                over_2r += net_r < -2
        reason = record.get('reason')
        if isinstance(reason, str):
            reasons[reason] += 1
    exits = Counter()
    for reason, count in reasons.items():
        if not reason.strip():
            continue
        if '止损' in reason:
            label = '止损'
        elif '止盈' in reason:
            label = '2R止盈' if '2R' in reason else 'MM止盈' if 'MM' in reason else '止盈'
        elif '期限' in reason or '到期' in reason:
            label = '期限退出'
        else:
            label = '其他'
        exits[label] += count
    exit_total = sum(exits.values())
    order = ('2R止盈', 'MM止盈', '止盈', '止损', '期限退出', '其他')
    return dict(holding=_holding_summary(samples),
                annual=_annual_result(annual, report, 'opportunities') if annual is not None else ([], ''),
                average_win_r=mean(wins) if wins else None,
                average_loss_r=mean(losses) if losses else None,
                valid_r_count=valid_r_count, exit_count=exit_total,
                exits=[dict(label=label, count=exits[label], share=exits[label]/exit_total)
                       for label in order if label in exits],
                loss_over_1r=over_1r/valid_r_count if valid_r_count else None,
                loss_over_2r=over_2r/valid_r_count if valid_r_count else None)


def receipt_time(created, full_date=False):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        stamp = datetime.fromisoformat(created)
        if stamp.tzinfo:
            stamp = stamp.astimezone(ZoneInfo('Asia/Shanghai'))
        return stamp.strftime('%Y-%m-%d %H:%M' if full_date else '%m-%d %H:%M')
    except (ValueError, TypeError):
        return str(created)[:16]


def scope_caption(report):
    if report.get('scope') == 'research':
        return 'TickFlow 长历史研究快照'
    if report.get('scope') == 'watch':
        return '关注名单'
    if 'boards' in report:
        from workbench.market import BOARDS
        return '全市场' if report['boards'] == list(BOARDS) else ' / '.join(report['boards'])
    return '市场范围未记录（旧报告）'


def outcome(record):
    if record['status'] == 'closed':
        reason = record.get('reason') or ''
        if '2R' in reason and '止盈' in reason:
            return '2R 止盈'
        if 'MM' in reason and '止盈' in reason:
            return 'MM 止盈'
        if '期限' in reason:
            return '期限退出'
        if '止损' in reason:
            return '止损'
        return reason or '已结束'
    return LABELS[record['status']]


def describe_input(key, value):
    if value == '未记录':
        return value
    if key == 'boards':
        return ' / '.join(value)
    if key == 'scope':
        return {'market': '市场板块', 'watch': '关注名单', 'specified': '指定行情快照'}.get(value, str(value))
    if key == 'h2_settings':
        fields = [('lookback', '突破观察窗口', '根'), ('min_pullback', '最短回调', '根'),
                  ('max_pullback', '最长回调', '根'), ('wait_bars', '等待期限', '根'),
                  ('max_risk_pct', '额外风险上限', '%（0 不筛选）'), ('min_mm_r', '最低 MM', 'R（0 不筛选）')]
        return '；'.join(f'{caption} {value.get(field, "未记录")}{unit}' for field, caption, unit in fields)
    if key == 'assumptions':
        fields = [('holding_bars', '最长持有', '个交易日'), ('commission_bps', '单边佣金', '基点'),
                  ('sell_tax_bps', '卖出税费', '基点'), ('slippage_bps', '单边滑点', '基点')]
        return '；'.join(f'{caption} {value.get(field, "未记录")}{unit}' for field, caption, unit in fields)
    return str(value)


def signal_retention(baseline, variant):
    if not _finite_number(baseline) or not _finite_number(variant):
        return '缺信号数'
    if baseline == 0:
        return '基线无信号'
    return f'{variant / baseline:.2%}'


def comparison_rows(a, b):
    """Unknown provenance never implies identical inputs; all deltas are A-B."""
    def value(report, key):
        result = report.get(key)
        if result is None:
            return '未记录'
        if key == 'datasets':
            return tuple(sorted(row['id'] for row in result))
        return result
    rows = []
    for caption, key in [('范围类型', 'scope'), ('市场范围', 'boards'), ('研究条件', 'h2_settings'),
                         ('成交假设', 'assumptions'), ('行情快照', 'datasets'),
                         ('策略版本', 'strategy_version'), ('回放版本', 'engine_version')]:
        av, bv = value(a, key), value(b, key)
        same = '未记录' if '未记录' in (av, bv) else '一致' if av == bv else '不同'
        # Dataset identities remain inspectable without a multi-kilobyte cell.
        if key == 'datasets':
            av = f'{len(av)} 份（见回执）' if isinstance(av, tuple) else av
            bv = f'{len(bv)} 份（见回执）' if isinstance(bv, tuple) else bv
        rows.append((caption, describe_input(key, av), describe_input(key, bv), same))
    rows.insert(1, ('区间', f'{a.get("start", "未记录")} → {a.get("end", "未记录")}',
                   f'{b.get("start", "未记录")} → {b.get("end", "未记录")}', '—'))
    for caption, key, factor, suffix in [('机会数', 'opportunities', 1, ''), ('模拟成交', 'filled', 1, ''),
                                        ('已结束', 'closed_trades', 1, ''), ('胜率', 'win_rate', 100, '%'),
                                        ('平均净 R', 'mean_r', 1, 'R'), ('初始风险中位数', 'median_risk_pct', 1, '%')]:
        av, bv = a.get(key), b.get(key)
        fmt = lambda v: '—' if v is None else str(int(v)) if not suffix else f'{v * factor:.2f}{suffix}'
        delta = ('—' if av is None or bv is None else f'{int(av-bv):+d}' if not suffix
                 else f'{(av-bv)*factor:+.2f}' + (' 个百分点' if suffix == '%' else suffix))
        rows.append((caption, fmt(av), fmt(bv), delta))
        if key == 'opportunities':
            rows.append(('信号保持率（B ÷ A）', '基线', '变体', signal_retention(av, bv)))
    return rows
