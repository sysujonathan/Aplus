import pandas as pd
import numpy as np

from core.strategies.mtr_strategy import MTRStrategy
from core.strategies.mtr_structural_v35 import MTRPoint, MTRStructuralEngineV35


def bars(*rows):
    return pd.DataFrame([
        {
            'date': f'2026-10-{index + 1:02d}',
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': 1000.,
            'atr': 5.,
        }
        for index, (open_, high, low, close) in enumerate(rows)
    ])


def test_mtr_signal_bar_requires_bullish_close_at_high():
    frame = bars(
        (10., 11., 9., 10.),
        (11., 20., 10., 19.99),
        (11., 20., 10., 20.),
    )
    engine = MTRStructuralEngineV35()
    signal = engine._find_signal_bar(frame, MTRPoint(0, 9., 'LOW_TEST', '2026-10-01'))

    assert signal['idx'] == 2
    assert signal['close_position'] == 1.


def test_mtr_signal_bar_rejects_flat_bar_and_accepts_close_at_high():
    frame = bars(
        (10., 11., 9., 10.),
        (10., 10., 10., 10.),
        (11., 20., 10., 20.),
    )
    engine = MTRStructuralEngineV35()
    signal = engine._find_signal_bar(frame, MTRPoint(0, 9., 'LOW_TEST', '2026-10-01'))

    assert signal['idx'] == 2
    assert signal['close_position'] == 1.


def test_mtr_signal_bar_tolerates_only_float_storage_noise_at_high():
    frame = bars(
        (10., 11., 9., 10.),
        (11., 20., 10., 20. - 1e-10),
    )
    engine = MTRStructuralEngineV35()
    signal = engine._find_signal_bar(frame, MTRPoint(0, 9., 'LOW_TEST', '2026-10-01'))

    assert signal['idx'] == 1
    assert signal['close_position'] == 1.


def test_mtr_setup_without_marubozu_is_not_published_as_candidate():
    count = 130
    close = np.linspace(20., 10., count)
    frame = pd.DataFrame({
        'date': pd.bdate_range('2026-01-01', periods=count).strftime('%Y-%m-%d'),
        'open': close + .1,
        'high': close + .5,
        'low': close - .5,
        'close': close,
        'volume': 1000.,
        'atr': 1.,
    })

    class ReadyWithoutSignal:
        @staticmethod
        def find_swing_points(_frame):
            return []

        @staticmethod
        def match_mtr_pattern(_frame, _swings, current_idx):
            if current_idx != len(_frame) - 1:
                return None
            return {'stage': 'SETUP_READY', 'score': 80., 'signal_bar': None, 'points': {}}

    strategy = MTRStrategy()
    strategy.structural_engine = ReadyWithoutSignal()
    result = strategy.calculate_signals(frame)

    assert result.iloc[-1]['mtr_stage'] == 'SETUP_READY'
    assert not result['signal_mtr'].any()
