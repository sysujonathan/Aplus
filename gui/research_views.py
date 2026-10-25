"""Read-only presentation and descriptive statistics of saved research trades."""
import math
from statistics import mean, median

from workbench.h2_replay import LABELS


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def holding_distribution(records, category='全部'):
    """Elapsed daily bars; filter only this distribution, never the report."""
    if category not in ('全部', '盈利', '亏损'):
        raise ValueError('未知持仓分布类别')
    samples = []
    for record in records:
        bars, net_r = record.get('holding_bars'), record.get('r_multiple')
        if record.get('status') != 'closed' or not _finite_number(bars) or bars < 1 or int(bars) != bars:
            continue
        if category != '全部' and (not _finite_number(net_r) or
                (net_r <= 0 if category == '盈利' else net_r >= 0)):
            continue
        samples.append(int(bars))
    bounds = [(1, '1根'), (3, '2–3根'), (5, '4–5根'), (10, '6–10根'),
              (20, '11–20根'), (30, '21–30根'), (math.inf, '超过30根')]
    counts = [0] * len(bounds)
    for bars in samples:
        index = next(i for i, (upper, _) in enumerate(bounds) if bars <= upper)
        counts[index] += 1
    # Keep the usual 1–20 bar scale. Show longer tails whenever present.
    last = max(4, max((i for i, count in enumerate(counts) if count), default=4))
    total = len(samples)
    return dict(total=total, median=median(samples) if samples else None,
                buckets=[dict(label=label, count=count, share=count/total if total else None)
                         for (_, label), count in zip(bounds[:last+1], counts[:last+1])])


def trade_statistics(records):
    """Derive new display metrics without rewriting old receipts or prices."""
    closed = [r for r in records if r.get('status') == 'closed']
    values = [r['r_multiple'] for r in closed if _finite_number(r.get('r_multiple'))]
    wins, losses = [r for r in values if r > 0], [r for r in values if r < 0]
    exits = {}
    for record in closed:
        reason = record.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            continue
        if '止损' in reason:
            label = '止损'
        elif '止盈' in reason:
            label = '2R止盈' if '2R' in reason else 'MM止盈' if 'MM' in reason else '止盈'
        elif '期限' in reason or '到期' in reason:
            label = '期限退出'
        else:
            label = '其他'
        exits[label] = exits.get(label, 0) + 1
    exit_total = sum(exits.values())
    order = ('2R止盈', 'MM止盈', '止盈', '止损', '期限退出', '其他')
    return dict(holding=holding_distribution(closed),
                average_win_r=mean(wins) if wins else None,
                average_loss_r=mean(losses) if losses else None,
                valid_r_count=len(values), exit_count=exit_total,
                exits=[dict(label=label, count=exits[label], share=exits[label]/exit_total)
                       for label in order if label in exits],
                loss_over_1r=sum(r < -1 for r in values)/len(values) if values else None,
                loss_over_2r=sum(r < -2 for r in values)/len(values) if values else None)


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
    if report.get('scope') == 'watch':
        return '关注名单'
    if 'boards' in report:
        from workbench.market import BOARDS
        return '全市场' if report['boards'] == list(BOARDS) else ' / '.join(report['boards'])
    return '市场范围未记录（旧报告）'


def outcome(record):
    if record['status'] == 'closed':
        reason = record.get('reason', '')
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
    return rows
