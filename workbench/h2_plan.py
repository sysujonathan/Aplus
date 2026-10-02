"""Read-only next-session plans around the frozen GAP H2 setup detector.

No execution or position state. The supplied frame must end at the selected close.
"""
from __future__ import annotations

import math

import numpy as np

from .prices import offset_tick
from .store import ROOT, digest


TIMEOUT = 30
STATE_LABELS = {
    'PENDING': '待挂机会', 'TRIGGERED': '市场已触发计划价',
    'INVALID': '结构失效', 'EXPIRED': '超过等待期限',
    'UNAVAILABLE': '原形态无法复核',
}


def plan_version():
    return digest(b''.join((ROOT / 'workbench' / name).read_bytes()
                          for name in ('h2_plan.py', 'prices.py')))


def is_h2(meta):
    return meta.get('signal_column') == 'signal_gap_h2'


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _positions(frame, column):
    if column not in frame:
        return []
    return list(np.flatnonzero(frame[column].fillna(False).astype(bool).to_numpy()))


def _date(frame, pos):
    return str(frame.iloc[pos]['date'])[:10]


def _structure(frame, signal_pos, lookback):
    row = frame.iloc[signal_pos]
    breakouts = [i for i in _positions(frame, 'is_breakout_h2') if i <= signal_pos]
    if not breakouts:
        return None
    bo = breakouts[-1]
    if bo >= signal_pos:
        return None
    window = frame.iloc[max(0, bo - lookback - 1):bo - 1]
    if window.empty:
        return None
    floor = _number(row.get('gap_h2_floor_exact', row.get('sl_gap_h2')))
    target = _number(row.get('tp_gap_h2'))
    prior_low = _number(row.get('gap_h2_prior_low'))
    if floor is None or target is None or prior_low is None:
        return None
    high_pos = max(0, bo - lookback - 1) + int(np.argmax(window.high.to_numpy()))
    low_pos = max(0, bo - lookback - 1) + int(np.argmin(window.low.to_numpy()))
    first_pb, h1 = None, None
    for j in range(bo + 1, signal_pos + 1):
        bar, prev = frame.iloc[j], frame.iloc[j - 1]
        if first_pb is None and bar.high < prev.high and bar.low < prev.low:
            first_pb = j
        elif first_pb is not None and bar.high > prev.high:
            h1 = j
            break
    return {
        'h2_setup_date': _date(frame, signal_pos), 'bo_date': _date(frame, bo),
        'bo_high': float(frame.iloc[bo].high),
        'h1_date': _date(frame, h1) if h1 is not None else None,
        'h1_high': float(frame.iloc[h1].high) if h1 is not None else None,
        'h2_high': float(row.high), 'gap_floor': floor,
        'gap_floor_date': _date(frame, high_pos),
        'gap_top': _number(row.get('gap_h2_top_exact')),
        'mm_target': target, 'mm_low': prior_low, 'mm_low_date': _date(frame, low_pos),
        'prior_gap_structures': _open_gaps(frame.iloc[:signal_pos + 1], _date(frame, bo), floor, lookback),
        'open_gap_scope_start': _date(frame, 0),
    }


def _open_gaps(frame, bo_date, floor, lookback):
    floors = frame.high.rolling(lookback, min_periods=1).max().shift(2)
    gaps = []
    for i in _positions(frame, 'is_breakout_h2'):
        if _date(frame, i) >= bo_date:
            continue
        value = _number(floors.iloc[i])
        if value is None or value <= 0 or abs(value - floor) / max(floor, 1) < .01:
            continue
        post = frame.iloc[i + 1:]
        if post.empty or float(post.low.min()) > value - 1e-3:
            gaps.append({'date': _date(frame, i), 'floor': value})
    return gaps


def project_plan(frame, structure, code=None, *, lookback=60):
    """Follow one original setup; a newer setup must not replace a watched one."""
    plan = dict(structure or {})
    plan.update(plan_kind='gap-h2-next-session', plan_version=plan_version(),
                pending_state='UNAVAILABLE', state_reason='missing_structure',
                plan_asof=_date(frame, len(frame) - 1) if len(frame) else None,
                entry=None, stop=None, target=None, entry_plan_price=None,
                sl1_plan_price=None, initial_risk_pct=None, mm_r_multiple=None)
    if not len(frame) or not structure:
        return plan
    dates = frame.date.astype(str).str[:10].tolist()
    setup_day = structure['h2_setup_date']
    if setup_day not in dates or structure['bo_date'] not in dates:
        return plan
    signal_pos, bo = dates.index(setup_day), dates.index(structure['bo_date'])
    floor, target = structure['gap_floor'], structure['mm_target']
    if bo >= signal_pos or not all(_number(v) is not None for v in (floor, target)):
        return plan
    ref_high = float(frame.iloc[signal_pos].high)
    state, reason, end_pos = 'PENDING', '', len(frame) - 1
    for j in range(signal_pos + 1, len(frame)):
        bar = frame.iloc[j]
        # Preserve the frozen projection's conservative same-bar ordering.
        if bar.low <= floor + 1e-9:
            state, reason = 'INVALID', 'gap_floor_broken'
        elif bar.high >= target:
            state, reason = 'INVALID', 'target_already_reached'
        elif j - signal_pos > TIMEOUT:
            state, reason = 'EXPIRED', 'timeout'
        elif bar.high >= offset_tick(code, ref_high, 1) - 1e-9:
            state, reason = 'TRIGGERED', 'previous_plan_price_reached'
        if state != 'PENDING':
            end_pos = j
            break
        ref_high = float(bar.high)
    gaps = []
    for gap in structure.get('prior_gap_structures', []):
        if gap['date'] in dates:
            post = frame.iloc[dates.index(gap['date']) + 1:]
            if post.empty or float(post.low.min()) > gap['floor'] - 1e-3:
                gaps.append(gap)
    plan.update(pending_state=state, state_reason=reason,
                pending_end_date=_date(frame, end_pos) if state != 'PENDING' else None,
                prior_open_gap_count=len(gaps), prior_open_gaps=gaps,
                open_gap_scope_start=structure.get('open_gap_scope_start', _date(frame, 0)))
    if state != 'PENDING':
        return plan
    pullback = frame.iloc[bo + 1:]
    low_pos = bo + 1 + int(np.argmin(pullback.low.to_numpy()))
    ref_low = float(frame.iloc[low_pos].low)
    entry, stop = offset_tick(code, ref_high, 1), offset_tick(code, ref_low, -1)
    risk = entry - stop
    plan.update(entry_reference_high=ref_high, entry_reference_date=dates[-1],
                sl1_reference_low=ref_low, sl1_reference_date=dates[low_pos],
                entry_plan_price=entry, sl1_plan_price=stop,
                entry=entry, stop=stop, target=target,
                initial_risk_pct=risk / entry * 100 if 0 < stop < entry else None,
                mm_r_multiple=(target - entry) / risk if 0 < stop < entry < target else None)
    return plan


def plan_at_end(instance, calculated, code=None, setup_date=None):
    """Select latest pending raw H2, or explicitly inspect a historical setup."""
    lookback = instance.LOOKBACK_WINDOW
    positions = _positions(calculated, 'signal_gap_h2')
    if setup_date:
        positions = [i for i in positions if _date(calculated, i) == str(setup_date)[:10]]
    for i in reversed(positions):
        structure = _structure(calculated, i, lookback)
        plan = project_plan(calculated, structure, code, lookback=lookback)
        if setup_date or plan['pending_state'] == 'PENDING':
            return plan
    return project_plan(calculated, None, code, lookback=lookback)


def display_plan(spec, bars, payload, code, setup_date, mode='candidate'):
    """Archived candidates keep their plan; watches replay their original setup.

    Reconstruct structure on the same latest snapshot's historical prefix, even
    when the setup has moved out of the latest 300-bar calculation window.
    This also avoids mixing price levels from different adjustment snapshots.
    """
    if mode == 'candidate' and payload.get('plan_kind') == 'gap-h2-next-session':
        return dict(payload)
    from .strategies import calculate
    setup_date = str(payload.get('h2_setup_date') or payload.get('setup_date') or setup_date)[:10]
    anchor_bars = bars[bars.date.astype(str).str[:10] <= setup_date]
    if len(anchor_bars) < 65:
        return dict(project_plan(bars, None, code), h2_setup_date=setup_date)
    instance, anchor = calculate(spec, anchor_bars)
    positions = [i for i in _positions(anchor, 'signal_gap_h2') if _date(anchor, i) == setup_date]
    if not positions and payload.get('legacy_import') and not payload.get('setup_date'):
        # Imported reminders sometimes saved the scan day as setup_date. Resolve
        # their original setup at THAT archived close, before replaying forward.
        # Never select a new setup from the watch's latest close.
        original = plan_at_end(instance, anchor, code)
        if original['pending_state'] == 'PENDING':
            setup_date = original['h2_setup_date']
            positions = [i for i in _positions(anchor, 'signal_gap_h2') if _date(anchor, i) == setup_date]
    structure = _structure(anchor, positions[-1], instance.LOOKBACK_WINDOW) if positions else None
    plan = project_plan(bars, structure, code, lookback=instance.LOOKBACK_WINDOW)
    plan['h2_setup_date'] = setup_date
    return plan
