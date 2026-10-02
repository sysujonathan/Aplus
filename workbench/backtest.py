"""Causal EOD signal study, not a portfolio return or an order execution simulator."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .strategies import signal_at_end


@dataclass
class Assumptions:
    wait_bars: int = 5
    holding_bars: int = 20
    commission_bps: float = 3
    sell_tax_bps: float = 5
    slippage_bps: float = 5
    min_score: float = 0
    min_rr: float = 0

    def validate(self):
        if not (1 <= self.wait_bars <= 100 and 1 <= self.holding_bars <= 500):
            raise ValueError('等待/持有根数超出允许范围')
        for v in (self.commission_bps,self.sell_tax_bps,self.slippage_bps,self.min_score,self.min_rr):
            if not np.isfinite(v) or v < 0:
                raise ValueError('研究条件必须为有限的非负数')


def simulate(frame, signal_index, signal, assumptions):
    """Long stop-entry after signal close. Next-bar earliest exit (daily T+1 model).

    Fixed prices at signal time; gaps fill at worse open, ambiguous bars favor stop.
    One-price bars are conservatively not executable. No forced terminal liquidation.
    """
    a = assumptions
    entry, stop, target = signal['entry'],signal['stop'],signal['target']
    if not all(v is not None and np.isfinite(v) for v in (entry,stop,target)) or not 0 < stop < entry < target:
        return {'status':'invalid_prices'}
    result = {'setup_date':signal['setup_date'], 'signal_date':str(frame.iloc[signal_index].date),
              'planned_entry':entry,'stop':stop,'target':target,'status':'not_triggered'}
    last = len(frame)-1
    fill_idx = None
    for j in range(signal_index+1,min(len(frame),signal_index+1+a.wait_bars)):
        bar = frame.iloc[j]
        if bar.high == bar.low or bar.volume <= 0:
            continue
        if bar.open <= stop:
            result['status'] = 'invalidated'
            return result
        if bar.high >= entry:
            # If both invalidation and entry could happen, refuse an optimistic fill.
            if bar.open < entry and bar.low <= stop:
                result['status'] = 'ambiguous_skipped'
                return result
            fill = max(entry,float(bar.open)) * (1+a.slippage_bps/10000)
            if fill >= target:
                result['status'] = 'gap_beyond_target'
                return result
            fill_idx = j
            result.update(entry_date=str(bar.date), entry=fill, status='open')
            break
        if bar.low <= stop or bar.high >= target:
            result['status'] = 'invalidated'
            return result
    if fill_idx is None:
        if last < signal_index+a.wait_bars:
            result['status'] = 'pending'
        return result
    for j in range(fill_idx+1,len(frame)):
        bar = frame.iloc[j]
        if bar.high == bar.low or bar.volume <= 0:
            continue
        exit_price, reason = None,None
        if bar.open <= stop:
            exit_price,reason = float(bar.open),'gap_stop'
        elif bar.open >= target:
            exit_price,reason = target,'target'
        elif bar.low <= stop:
            exit_price,reason = stop,'stop'  # both touched: pessimistic convention
        elif bar.high >= target:
            exit_price,reason = target,'target'
        elif j >= fill_idx+a.holding_bars:
            exit_price,reason = float(bar.close),'time'
        if exit_price is not None:
            exit_price *= 1-a.slippage_bps/10000
            net = exit_price-result['entry'] - result['entry']*a.commission_bps/10000 - exit_price*(a.commission_bps+a.sell_tax_bps)/10000
            path = frame.iloc[fill_idx+1:j+1]
            result.update(status='closed',exit_date=str(bar.date),exit=exit_price,reason=reason,
                          net_return=net/result['entry'],r_multiple=net/(result['entry']-stop),
                          holding_bars=j-fill_idx, mae=float(path.low.min()/result['entry']-1),
                          mfe=float(path.high.max()/result['entry']-1))
            return result
    result['mark_date'] = str(frame.iloc[-1].date)
    result['unrealized_return'] = float(frame.iloc[-1].close/result['entry']-1)
    return result


def run_study(spec, frame, start, end, assumptions, progress=lambda *a:None, cancelled=lambda:False):
    assumptions.validate()
    frame = frame[frame.date <= end].reset_index(drop=True)
    eligible = [i for i in range(124,len(frame)) if frame.iloc[i].date >= start]
    records,seen = [],set()
    for k,i in enumerate(eligible):
        if cancelled():
            raise InterruptedError('用户停止回测，已完成部分不标记为完整报告')
        # Deliberately recalculate indicators AND strategy on the visible prefix.
        # Frozen strategies can repaint prior rows; reading a full-history signal
        # column would leak future knowledge. Only today's output can be used.
        signal = signal_at_end(spec,frame.iloc[:i+1],with_rating=False,plan_prices=False)
        if signal:
            identity = signal['setup_date']
            if identity not in seen:
                seen.add(identity)
                rr = ((signal['target']-signal['entry'])/(signal['entry']-signal['stop'])
                      if not signal['warning'] else -1)
                if (signal['score'] or 0) >= assumptions.min_score and rr >= assumptions.min_rr:
                    records.append(simulate(frame,i,signal,assumptions))
        progress(k+1,len(eligible))
    return records


def summarize(records):
    trades = [r for r in records if r['status']=='closed']
    returns = [r['net_return'] for r in trades]
    winners = sum(v>0 for v in returns)
    return {'signals':len(records),'closed_trades':len(trades),
            'win_rate':winners/len(trades) if trades else None,
            'mean_return':float(np.mean(returns)) if trades else None,
            'mean_r':float(np.mean([r['r_multiple'] for r in trades])) if trades else None,
            'open_trades':sum(r['status']=='open' for r in records),
            'pending':sum(r['status']=='pending' for r in records),
            'note':'独立信号样本统计，允许重叠；不是组合收益率、资金曲线或可成交承诺。'}
