"""Causal GAP H2 opportunity and execution replay, independent of formal plans."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math

import numpy as np

from .backtest import Assumptions
from .h2_plan import _structure, project_plan
from .store import digest, dumps
from .strategies import calculate


MODEL = 'gap-h2-daily-execution-v1'
LABELS = {'pending': '仍待挂', 'invalidated': '结构失效', 'mm_first': 'MM 已先达到',
          'expired': '等待超时', 'unavailable': '无法复核', 'filtered': '条件过滤',
          'unfilled': '触发未成交', 'open': '持仓未结束', 'closed': '已结束'}


@dataclass(frozen=True)
class H2Settings:
    lookback: int = 60
    min_pullback: int = 2
    max_pullback: int = 40
    wait_bars: int = 30
    max_risk_pct: float = 0
    min_mm_r: float = 0

    def validate(self):
        for key, lo, hi in [('lookback', 5, 120), ('min_pullback', 2, 60),
                            ('max_pullback', 2, 120), ('wait_bars', 1, 100)]:
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f'{key} 必须为 {lo} 至 {hi} 的整数')
        if self.min_pullback > self.max_pullback:
            raise ValueError('最短回调不能大于最长回调')
        for value in (self.max_risk_pct, self.min_mm_r):
            if not math.isfinite(value) or value < 0:
                raise ValueError('额外筛选条件必须是有限的非负数；0 表示不筛选')


class ResearchSpec:
    """Per-run instance settings; never mutate registry, class or frozen config."""
    def __init__(self, spec, settings):
        self.spec, self.settings = spec, settings

    def instance(self):
        instance = self.spec.instance()
        if instance.get_metadata().get('signal_column') != 'signal_gap_h2':
            raise ValueError('此执行回放目前仅支持原 GAP H2 策略')
        instance.LOOKBACK_WINDOW = self.settings.lookback
        instance.MIN_PULLBACK_WINDOW = self.settings.min_pullback
        instance.MAX_PULLBACK_WINDOW = self.settings.max_pullback
        return instance


def _day(frame, i):
    return str(frame.iloc[i].date)[:10]


def _event(record, day, kind, text, plan=None, **facts):
    item = dict(date=day, kind=kind, text=text, **facts)
    if plan is not None:
        item['plan'] = dict(plan)
    record['events'].append(item)


def _executable(bar):
    # Daily snapshots lack queue/limit metadata. Refuse all one-price bars.
    return float(bar.volume) > 0 and float(bar.high) > float(bar.low)


def _close(record, frame, i, price, reason, costs):
    price *= 1 - costs.slippage_bps / 10000
    entry, stop = record['entry'], record['stop']
    net = price - entry - entry * costs.commission_bps / 10000 - price * (
        costs.commission_bps + costs.sell_tax_bps) / 10000
    path = frame.iloc[record['_fill_pos']:i + 1]
    record.update(status='closed', exit_date=_day(frame, i), exit=price, reason=reason,
                  net_return=net / entry, r_multiple=net / (entry - stop),
                  holding_bars=i - record['_fill_pos'],
                  mae=float(path.low.min() / entry - 1), mfe=float(path.high.max() / entry - 1))
    _event(record, _day(frame, i), 'exit', '模拟退出：' + reason, price=price,
           r_multiple=record['r_multiple'])


def replay_setup(frame, setup_index, structure, settings, costs, cancelled=lambda: False):
    """Replay one anchored setup. Every session uses the previous close's plan."""
    code = frame.attrs.get('code')
    record = dict(id=digest(f'{code}|{structure["bo_date"]}|{structure["h2_setup_date"]}'.encode())[:24],
                  code=code, setup_date=structure['h2_setup_date'], structure=dict(structure),
                  status='pending', triggered=False, filled=False, events=[])
    plan = project_plan(frame.iloc[:setup_index + 1], structure, code,
                        lookback=settings.lookback, wait_bars=settings.wait_bars)
    record['latest_plan'] = plan
    _event(record, _day(frame, setup_index), 'setup', 'H2 成立；收盘后生成次日计划', plan)
    risk, rr = plan.get('initial_risk_pct'), plan.get('mm_r_multiple')
    if (plan['pending_state'] != 'PENDING' or risk is None or rr is None):
        record['status'] = 'unavailable'
        return record
    if ((settings.max_risk_pct and risk > settings.max_risk_pct) or
            (settings.min_mm_r and rr < settings.min_mm_r)):
        record['status'] = 'filtered'
        _event(record, _day(frame, setup_index), 'filtered', '被本次额外风险／MM 筛选条件排除')
        return record
    t1_stop = False
    for i in range(setup_index + 1, len(frame)):
        if cancelled():
            raise InterruptedError('回测已停止；本次报告不作为完整结果')
        bar, day = frame.iloc[i], _day(frame, i)
        if record['filled']:
            if not _executable(bar):
                _event(record, day, 'blocked', '停牌／单一价格，保守不模拟退出')
                continue
            stop, target = record['stop'], record['target']
            price, reason = None, ''
            if t1_stop:
                price, reason = float(bar.open), 'T+1 延迟止损'
            elif bar.open <= stop:
                price, reason = float(bar.open), '跳空止损'
            elif bar.open >= target:
                price, reason = target, 'MM 止盈'
            elif bar.low <= stop:
                price, reason = stop, 'SL1 止损（同日双触碰优先止损）'
            elif bar.high >= target:
                price, reason = target, 'MM 止盈'
            elif i - record['_fill_pos'] >= costs.holding_bars:
                price, reason = float(bar.close), '持有期限退出'
            if price is not None:
                _close(record, frame, i, price, reason, costs)
                break
            _event(record, day, 'holding', '持仓；SL1 与 MM 固定为模拟成交时的计划价')
            continue

        prior = plan
        plan = project_plan(frame.iloc[:i + 1], structure, code,
                            lookback=settings.lookback, wait_bars=settings.wait_bars)
        record['latest_plan'] = plan
        # Price observation remains separate from executable fills and uncertain order.
        touched = bar.high >= prior['entry']
        if i - setup_index > settings.wait_bars:
            record['status'] = 'expired'
            _event(record, day, 'expired', '等待期限已过，本日不再有效', plan)
            break
        if touched:
            record.update(triggered=True, trigger_date=day, trigger_plan=dict(prior))
            _event(record, day, 'trigger', '达到前一日计划价；尚不代表成交', prior,
                   observed_high=float(bar.high))
            fill = max(float(bar.open), prior['entry']) * (1 + costs.slippage_bps / 10000)
            reason = ''
            if plan['pending_state'] != 'TRIGGERED':
                reason = '同日触发与结构失效／MM 的先后无法确认，保守不成交'
            elif not _executable(bar):
                reason = '停牌／单一价格，无法保证成交'
            elif bar.open <= prior['stop'] or (bar.open < prior['entry'] and bar.low <= prior['stop']):
                reason = '入场与 SL1 的先后无法确认，保守不成交'
            elif fill >= prior['target']:
                reason = '跳空／滑点后的入场价已达 MM'
            if reason:
                record.update(status='mm_first' if plan.get('state_reason') == 'target_already_reached'
                              else 'unfilled', reason=reason)
                _event(record, day, 'unfilled', reason, plan)
                break
            record.update(status='open', filled=True, entry_date=day, entry=fill,
                          planned_entry=prior['entry'], stop=prior['stop'], target=prior['target'],
                          initial_risk_pct=(fill - prior['stop']) / fill * 100,
                          _fill_pos=i)
            _event(record, day, 'fill', '模拟成交；买入当日按 T+1 不退出', prior, price=fill,
                   stop=prior['stop'], target=prior['target'])
            if bar.low <= prior['stop']:
                t1_stop = True
                _event(record, day, 't1', '买入日触及 SL1；下一可交易日开盘模拟延迟止损')
            elif bar.high >= prior['target']:
                _event(record, day, 't1', '买入日触及 MM；T+1 当日无法卖出')
            continue
        state = plan['pending_state']
        if state != 'PENDING':
            status = {'INVALID': 'mm_first' if plan['state_reason'] == 'target_already_reached' else 'invalidated',
                      'EXPIRED': 'expired'}.get(state, 'unavailable')
            record['status'] = status
            _event(record, day, status, LABELS[status], plan)
            break
        _event(record, day, 'plan', '收盘更新次日 Entry／SL1；原 MM 保持不变', plan)
    if record['status'] == 'open':
        record.update(mark_date=_day(frame, len(frame) - 1),
                      unrealized_return=float(frame.iloc[-1].close / record['entry'] - 1))
    record.pop('_fill_pos', None)
    return record


def run_replay(spec, frame, start, end, settings=None, costs=None,
               progress=lambda *a: None, cancelled=lambda: False):
    settings, costs = settings or H2Settings(), costs or Assumptions()
    settings.validate()
    costs.validate()
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError('开始日期不能晚于结束日期')
    source = frame[frame.date <= end].reset_index(drop=True).copy()
    source.attrs.update(frame.attrs)
    adapted = ResearchSpec(spec, settings)
    adapted.instance()  # Reject other strategies even with no eligible bars.
    indices = [i for i in range(max(124, settings.lookback + 4), len(source))
               if _day(source, i) >= start]
    records, seen = [], set()
    for n, i in enumerate(indices):
        if cancelled():
            raise InterruptedError('回测已停止；本次报告不作为完整结果')
        # The frozen H2's raw signal requires this day's LHLL. This causal
        # necessary condition avoids recalculating indicators on impossible days;
        # it never reads a future row or changes the qualifying detector.
        today, yesterday = source.iloc[i], source.iloc[i - 1]
        if not (today.high < yesterday.high and today.low < yesterday.low):
            if n % 10 == 0 or n + 1 == len(indices):
                progress(n + 1, len(indices))
            continue
        _, calculated = calculate(adapted, source.iloc[:i + 1])
        # Only newly confirmed H2 at this close. Never use repaintable old flags.
        if bool(calculated.iloc[-1].get('signal_gap_h2', False)):
            structure = _structure(calculated, len(calculated) - 1, settings.lookback)
            if structure and structure['bo_date'] not in seen:
                seen.add(structure['bo_date'])
                records.append(replay_setup(source, i, structure, settings, costs, cancelled))
        if n % 10 == 0 or n + 1 == len(indices):
            progress(n + 1, len(indices))
    return records


def boundary_samples(records, limit=5):
    closed = [r for r in records if r['status'] == 'closed' and r.get('r_multiple') is not None]
    key = lambda r: (r['setup_date'], r.get('code', ''), r['id'])
    profit = sorted((r for r in closed if r['r_multiple'] > 0), key=lambda r: (-r['r_multiple'], *key(r)))
    loss = sorted((r for r in closed if r['r_multiple'] < 0), key=lambda r: (r['r_multiple'], *key(r)))
    return {'profit': [r['id'] for r in profit[:limit]], 'loss': [r['id'] for r in loss[:limit]]}


def summarize_replay(records):
    eligible = [r for r in records if r['status'] != 'filtered']
    closed = [r for r in eligible if r['status'] == 'closed']
    filled = [r for r in eligible if r['filled']]
    triggered = sum(r['triggered'] for r in eligible)
    counts = {s: sum(r['status'] == s for r in records) for s in LABELS}
    values = [r['r_multiple'] for r in closed]
    edges = [-float('inf'), -2, -1, 0, 1, 2, float('inf')]
    histogram = np.histogram(values, bins=edges)[0].tolist()
    return dict(opportunities=len(eligible), detected=len(records), triggered=triggered,
                trigger_rate=triggered / len(eligible) if eligible else None,
                filled=len(filled), unfilled=triggered - len(filled), closed_trades=len(closed),
                win_rate=sum(v > 0 for v in values) / len(closed) if closed else None,
                mean_r=float(np.mean(values)) if values else None,
                median_risk_pct=float(np.median([r['initial_risk_pct'] for r in filled])) if filled else None,
                counts=counts, distribution=histogram, boundaries=boundary_samples(records),
                note='独立机会可重叠；已结束交易按扣费后 R 排序。不是组合收益或真实成交记录。')
