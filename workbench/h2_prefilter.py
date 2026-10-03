"""Causal necessary conditions only; the frozen detector still confirms H2.

These prices are never passed as precomputed indicators to a strategy. Every
candidate is confirmed with its original 300-bar prefix and frozen indicators.
"""
import numpy as np


def candidate_days(frame, settings):
    high, low = frame.high, frame.low
    floor = high.rolling(settings.lookback, min_periods=1).max().shift(2).to_numpy()
    prior_low = low.rolling(settings.lookback, min_periods=1).min().shift(2).to_numpy()
    breakout = ((high > high.shift(1)) & (low > low.shift(1)) &
                (low > floor - 1e-3)).to_numpy()
    highs, lows = high.to_numpy(), low.to_numpy()
    indices = np.arange(len(frame))
    latest = np.maximum.accumulate(np.where(breakout, indices, -1))
    lhll = ((high < high.shift(1)) & (low < low.shift(1))).to_numpy()
    candidates = np.zeros(len(frame), dtype=bool)
    for i in np.flatnonzero(lhll):
        bo = int(latest[i])
        if bo < 0 or not settings.min_pullback <= i - bo <= settings.max_pullback:
            continue
        # At most 120 + 120 + 2 raw bars are needed for these conditions,
        # wholly inside the original 300-bar window for every admitted setting.
        # Including the BO bar matches the frozen expanding group min/max.
        if (float(lows[bo:i + 1].min()) > floor[bo] - 1e-3 and
                float(highs[bo:i + 1].max()) < 2 * floor[bo] - prior_low[bo]):
            candidates[i] = True
    return candidates, latest
