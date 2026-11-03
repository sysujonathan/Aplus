"""Keep the old validation contract while removing per-row formatting/reductions."""
import numpy as np
import pandas as pd
import pytest

from workbench.market import FIELDS, validate_bars


def reference(df):
    """Pre-optimization validation, retained as an independent comparison oracle."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = set(FIELDS) - set(df.columns)
    if missing:
        raise ValueError(f"缺少行情列：{', '.join(sorted(missing))}")
    df = df[FIELDS].copy()
    if df.empty:
        raise ValueError('行情为空；不会当作同步成功')
    dates = pd.to_datetime(df.date, errors='raise')
    df['date'] = dates.dt.strftime('%Y-%m-%d')
    if df.date.duplicated().any():
        raise ValueError('同一股票存在重复日期，请先核对 CSV')
    for col in FIELDS[1:]:
        df[col] = pd.to_numeric(df[col], errors='raise').astype(float)
    if not np.isfinite(df[FIELDS[1:]].to_numpy()).all():
        raise ValueError('行情包含空值或无穷值，不自动用未来价格填补')
    invalid = ((df[['open','high','low','close']] <= 0).any(axis=1)
               | (df.high < df[['open','close','low']].max(axis=1))
               | (df.low > df[['open','close','high']].min(axis=1)) | (df.volume < 0))
    if invalid.any():
        raise ValueError(f'有 {int(invalid.sum())} 根 K 线价格关系不合法')
    return df.sort_values('date').reset_index(drop=True)


@pytest.mark.parametrize('dates', [
    ['2026-10-29','2026-10-30'],
    ['2026-10-29 23:59:59','2026-10-30 00:00:00'],
    ['1960-01-01 00:00:00','1960-01-02 12:34:00'],
    pd.date_range('2026-10-29 23:00', periods=2, freq='2h', tz='Asia/Shanghai'),
    pd.to_datetime(['2026-10-29',None]),
])
def test_formatting_and_types_equal_reference(dates):
    frame = pd.DataFrame(dict(date=dates, open=['1','2'], high=['2','3'],
                              low=['.5','1'], close=['1.5','2.5'], volume=['0','100']))
    frame.index = [8, 3]
    pd.testing.assert_frame_equal(validate_bars(frame), reference(frame))


@pytest.mark.parametrize('seed', range(10))
def test_all_price_relationships_preserve_reference(seed):
    rng = np.random.default_rng(seed)
    size = 250
    values = rng.normal(1, 2, (size, 5))
    frame = pd.DataFrame(values, columns=FIELDS[1:])
    frame.insert(0, 'date', pd.bdate_range('2025-01-01', periods=size).strftime('%Y-%m-%d'))
    with pytest.raises(ValueError) as old:
        reference(frame)
    with pytest.raises(ValueError) as new:
        validate_bars(frame)
    assert str(new.value) == str(old.value)


@pytest.mark.parametrize('column,value', [('close',np.nan),('volume',np.inf),('high','bad'),('date','2026-02-30')])
def test_bad_input_still_fails(column, value):
    frame = pd.DataFrame([dict(date='2026-10-30',open=1,high=2,low=.5,close=1,volume=0)])
    frame[column] = value
    with pytest.raises((ValueError,TypeError)):
        reference(frame)
    with pytest.raises((ValueError,TypeError)):
        validate_bars(frame)
