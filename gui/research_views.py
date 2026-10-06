"""Presentation of immutable research reports; no writes or calculations."""
from workbench.h2_replay import LABELS


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
        return 'MM 止盈' if '止盈' in reason else '期限退出' if '期限' in reason else '止损'
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
