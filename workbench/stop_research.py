"""Paired initial-stop hypotheses around an immutable, original H2 fill."""
from dataclasses import asdict, dataclass
import math

from .backtest import Assumptions
from .h2_replay import _close, _event, _executable
from .prices import offset_tick, tick_size

MODEL = 'gap-h2-stop-comparison-v1'
RULE = 'rising-lows-candidate-v0'
GROUPS = ('全部', '宽止损救回', '两者到 MM', '两者止损', '仍持有／其他退出', '无法对照')


@dataclass(frozen=True)
class AnchorSettings:
    max_bars: int = 30
    pause_ticks: int = 0

    def validate(self):
        if type(self.max_bars) is not int or not 2 <= self.max_bars <= 120:
            raise ValueError('回溯根数应为 2～120')
        if type(self.pause_ticks) is not int or not 0 <= self.pause_ticks <= 100:
            raise ValueError('浅回调容忍应为 0～100 个品种 tick')


def candidate_anchor(frame, structure, code, settings=None):
    """Only pre-BO bars; this labels a candidate, not a Brooks strength test."""
    settings = settings or AnchorSettings()
    settings.validate()
    dates = frame.date.astype(str).str[:10].tolist()
    day = structure.get('bo_date')
    if day not in dates:
        return dict(valid=False, reason='突破日期不在快照中')
    bo = dates.index(day)
    boundary = max(0, bo - settings.max_bars + 1)
    tick = tick_size(code, float(frame.iloc[bo].low),
                     instrument_type=frame.attrs.get('instrument_type'))
    start = bo
    while start > boundary:
        drop = float(frame.iloc[start-1].low) - float(frame.iloc[start].low)
        if drop + 1e-9 >= (settings.pause_ticks + 1) * tick:
            break
        start -= 1
    if start == boundary:
        return dict(valid=False, reason='回溯范围内未找到低点下降的分界')
    leg = frame.iloc[start:bo+1]
    pos = start + int(leg.low.to_numpy().argmin())
    low = float(frame.iloc[pos].low)
    stop = offset_tick(code, low, -1, instrument_type=frame.attrs.get('instrument_type'))
    if low >= structure['gap_floor']:
        return dict(valid=False, reason='候选底部不低于缺口下沿')
    if len(leg) < 2:
        return dict(valid=False, reason='候选段不足两根，无法确认重新发动')
    return dict(valid=True, rule=RULE, date=dates[pos], low=low, stop=stop,
                start_date=dates[start], bo_date=day, bars=len(leg), settings=asdict(settings),
                note='仅识别低点重新抬升段；尚未判定强突破，较长段可能是上涨通道')


def replay_fixed_stop(frame, original, stop, costs):
    """Same fill as control. Match frozen-price replay exits, including T+1."""
    dates = frame.date.astype(str).str[:10].tolist()
    fill_day = original['entry_date']
    if fill_day not in dates:
        raise ValueError('原模拟成交日期不在行情快照中')
    fill_pos = dates.index(fill_day)
    entry, target = original['entry'], original['target']
    if not all(math.isfinite(x) for x in (entry, stop, target)) or not 0 < stop < entry < target:
        raise ValueError('研究止损／入场／MM 价格顺序无效')
    record = dict(status='open', filled=True, entry=entry, stop=stop, target=target,
                  entry_date=fill_day, _fill_pos=fill_pos, events=[])
    _event(record, fill_day, 'fill', '原方案的同一笔模拟成交', price=entry, stop=stop, target=target)
    t1_stop = frame.iloc[fill_pos].low <= stop
    if t1_stop:
        _event(record, fill_day, 't1', '买入日触及止损；下一可交易日开盘退出')
    for i in range(fill_pos+1, len(frame)):
        bar, day = frame.iloc[i], dates[i]
        if not _executable(bar):
            _event(record, day, 'blocked', '停牌／单一价格，保守不模拟退出')
            continue
        price, reason = None, ''
        if t1_stop:
            price, reason = float(bar.open), 'T+1 延迟止损'
        elif bar.open <= stop:
            price, reason = float(bar.open), '跳空止损'
        elif bar.open >= target:
            price, reason = target, 'MM 止盈'
        elif bar.low <= stop:
            price, reason = stop, '止损（同日双触碰优先止损）'
        elif bar.high >= target:
            price, reason = target, 'MM 止盈'
        elif i-fill_pos >= costs.holding_bars:
            price, reason = float(bar.close), '持有期限退出'
        if price is not None:
            _close(record, frame, i, price, reason, costs)
            break
        _event(record, day, 'holding', '持仓；研究止损与原 MM 固定')
    if record['status'] == 'open':
        record.update(mark_date=dates[-1], unrealized_return=float(frame.iloc[-1].close/entry-1))
    record.pop('_fill_pos')
    return record


def compare_trade(frame, original, settings, costs):
    row = dict(id=original['id'], code=original['code'], setup_date=original['setup_date'],
               dataset_id=original['dataset_id'], structure=original['structure'],
               study_end=original.get('study_end'), baseline=original,
               group='无法对照', comparable=False)
    if not original.get('filled'):
        row['reason'] = '原机会未模拟成交，未纳入止损对照'
        return row
    anchor = candidate_anchor(frame, original['structure'], original['code'], settings)
    row['anchor'] = anchor
    if not anchor['valid']:
        row['reason'] = anchor['reason']
        return row
    if anchor['stop'] >= original['stop']:
        row['reason'] = '候选 C 止损未宽于原 SL1'
        return row
    narrow = replay_fixed_stop(frame, original, original['stop'], costs)
    # Old receipts must reproduce before being used as the control.
    if narrow['status'] != original['status'] or narrow.get('exit_date') != original.get('exit_date') or (
            narrow['status'] == 'closed' and any(not math.isclose(narrow[key], original[key], abs_tol=1e-8)
                                                 for key in ('exit','net_return','r_multiple'))):
        row['reason'] = '原回测退出无法按当前口径复现，请重新运行盘后回测'
        return row
    wide = replay_fixed_stop(frame, original, anchor['stop'], costs)
    row.update(comparable=True, candidate=wide, reason='',
               baseline_risk_pct=(original['entry']-original['stop'])/original['entry']*100,
               candidate_risk_pct=(original['entry']-anchor['stop'])/original['entry']*100)
    both_closed = narrow['status'] == wide['status'] == 'closed'
    if both_closed:
        # Comparable R denominator stays the original SL1 price risk.
        wide['common_r'] = wide['net_return']*wide['entry']/(original['entry']-original['stop'])
        row['delta_common_r'] = wide['common_r'] - original['r_multiple']
        row['delta_return'] = wide['net_return'] - original['net_return']
    ns, ws = '止损' in narrow.get('reason', ''), '止损' in wide.get('reason', '')
    nm, wm = narrow.get('reason') == 'MM 止盈', wide.get('reason') == 'MM 止盈'
    row['group'] = ('宽止损救回' if ns and wm else '两者到 MM' if nm and wm
                    else '两者止损' if ns and ws else '仍持有／其他退出')
    return row


def summarize(rows):
    pairs = [r for r in rows if r['comparable']]
    closed = [r for r in pairs if 'delta_common_r' in r]
    return dict(opportunities=len(rows), comparable=len(pairs), excluded=len(rows)-len(pairs),
                paired_closed=len(closed), groups={g:sum(r['group']==g for r in rows) for g in GROUPS[1:]},
                mean_delta_common_r=sum(r['delta_common_r'] for r in closed)/len(closed) if closed else None,
                mean_delta_return=sum(r['delta_return'] for r in closed)/len(closed) if closed else None,
                unresolved=sum(r.get('candidate', {}).get('status') == 'open' for r in pairs))
