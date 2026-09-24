# core/calculator.py
import pandas as pd
import numpy as np
from typing import Optional
from config import settings

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    [V8.0 高性能向量化版]
    职责：
    1. 计算技术指标 (EMA, ATR, ADX)。
    2. 计算价格行为特征 (PA Features) - 完全向量化。
    3. 仅添加列，不进行策略判断。

    优化：
    1. 移除所有循环，使用 pandas/numpy 向量化操作。
    2. 统一列名标准。
    3. 全中文注释。
    """
    # 基础验证
    if df is None or df.empty:
        return df

    # 避免不必要的警告
    df = df.copy()

    # ==========================================
    # 0. 停牌边界保护 (P1④b) — 相邻 bar 日历间隔
    # 复牌首日 vs 数周前旧 bar 的"缺口"是恢复性缺口, 非突破缺口,
    # 不应计入突破判定 (否则产生假缺口信号)。阈值可配, 默认 10 天
    # (与实测"缺口>10天=停牌"对齐, 避免误伤长假后真实跳空)。
    # ==========================================
    try:
        _dt = pd.to_datetime(df['trade_date'], errors='coerce')
        _day_gap = _dt.diff().dt.days.fillna(0)
    except Exception:
        # 兜底: 无 trade_date 列时 (如图表重拉路径仅含 'date'),
        # 必须用 df.index 对齐, 否则默认 0..n-1 与切片后非0起点索引错位 -> 全 NaN
        _day_gap = pd.Series(0, index=df.index)
    _susp_days = getattr(settings, 'SUSPENSION_RESUME_DAYS', 10)
    df['date_gap_days'] = _day_gap.astype(int)
    df['is_suspension_resume'] = _day_gap > _susp_days

    # ==========================================
    # 1. 基础指标计算 (Indicators)
    # ==========================================
    
    # EMA 系统
    close_series = df['close']
    df['ema20'] = close_series.ewm(span=20, adjust=False).mean()
    df['ema60'] = close_series.ewm(span=60, adjust=False).mean()
    
    # EMA60 斜率 (用于 V7 趋势判断)
    # 逻辑：当前EMA60 - 5根前的EMA60
    df['ema60_slope'] = df['ema60'].diff(5)

    # ATR 计算
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = np.maximum(high_low, np.maximum(high_close, low_close))
    df['atr'] = tr.rolling(window=14, min_periods=1).mean()

    # 🟢 [V35 MTR Support] Trend Depth (120-Day High Drawdown in ATR units)
    # This is critical for filtering "Major" trends in MTR strategy.
    lookback_h_120 = df['high'].rolling(120, min_periods=60).max()
    df['trend_depth'] = (lookback_h_120 - df['low']) / df['atr'].replace(0, 1)

    # ADX 计算 (向量化)
    up = df['high'].diff()
    down = -df['low'].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    
    tr_smooth = tr.rolling(window=14, min_periods=1).mean().replace(0, 1e-9)
    plus_di = 100 * (pd.Series(plus_dm).rolling(window=14, min_periods=1).mean() / tr_smooth)
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=14, min_periods=1).mean() / tr_smooth)
    
    div = (plus_di + minus_di).replace(0, 1)
    dx = (abs(plus_di - minus_di) / div) * 100
    df['adx_str'] = dx.rolling(window=14, min_periods=1).mean()

    # 成交量均线
    vol_ma20 = df['volume'].rolling(window=20, min_periods=1).mean()
    df['vol_ma20'] = vol_ma20
    # 相对成交量 (Relative Volume)
    df['relative_vol'] = df['volume'] / vol_ma20.replace(0, 1)

    # ==========================================
    # 2. 价格行为特征计算 (PA Features - Vectorized)
    # ==========================================
    
    # 实体与影线
    body_size = (df['close'] - df['open']).abs()
    candle_range = df['high'] - df['low']
    
    # 防止除零错误
    safe_range = candle_range.replace(0, 1e-9)
    
    # 占比特征
    df['body_pct'] = body_size / safe_range
    df['upper_wick_pct'] = (df['high'] - df[['open', 'close']].max(axis=1)) / safe_range
    df['lower_wick_pct'] = (df[['open', 'close']].min(axis=1) - df['low']) / safe_range
    
    # 收盘位置 (Close Location Value, CLV)
    # 1.0 = High, 0.0 = Low, 0.5 = Mid
    df['close_loc'] = (df['close'] - df['low']) / safe_range
    
    # 趋势棒定义 (Trend Bar)
    # 实体占比 > 阈值 (默认 0.5)
    min_body_pct = getattr(settings, 'K3_BODY_PCT_A', 0.5)
    df['is_trend_bar'] = df['body_pct'] > min_body_pct
    df['is_bullish'] = df['close'] > df['open']
    
    # 巨型K线 (Big Bar / Climax)
    # 实体占比大 且 振幅 > 0.5 * ATR
    df['is_big'] = df['is_trend_bar'] & (candle_range > 0.5 * df['atr'])

    # 重叠度 (Overlap)
    # 当前K线高低点是否都在前一根K线范围内 (孕线/重叠)
    # 或者 定义为有重叠部分
    # 这里沿用 V7 定义：当前High >= 前Low 且 当前Low <= 前High (实际上只要不是跳空缺口即为重叠)
    # 但根据原代码逻辑，似乎是想找震荡区间的重叠。
    # 我们修正为 Al Brooks 的定义：Overlap means trading within prior bar's range.
    prev_high = df['high'].shift(1)
    prev_low = df['low'].shift(1)
    # 简单的重叠判断
    df['overlap'] = (df['high'] >= prev_low) & (df['low'] <= prev_high)

    # 波动压缩/扩张比 (ATR Ratio)
    df['atr_ratio'] = candle_range / df['atr'].replace(0, 1)

    # ==========================================
    # 3. 高级 PA 特征 (Advanced Features - V8.2)
    # ==========================================
    
    # 关键点位 (Swing Points) - 向量化计算
    # 使用 rolling window 寻找局部极值 (类似 fractal: 前后n根都更低/更高)
    window = 5
    df['is_swing_high'] = df['high'] == df['high'].rolling(window=window*2+1, center=True).max()
    df['is_swing_low'] = df['low'] == df['low'].rolling(window=window*2+1, center=True).min()
    
    # 最近的支撑/压力位 (填充最近的一个非NaN值)
    df['res_level'] = df['high'].where(df['is_swing_high']).ffill()
    df['sup_level'] = df['low'].where(df['is_swing_low']).ffill()
    
    # 均线乖离率 (Distance to EMA20)
    # 正值表示在均线上方，负值在下方。单位: ATR
    df['dist_ema20_atr'] = (df['close'] - df['ema20']) / df['atr'].replace(0, 1)
    
    # 趋势强度 (Trend Strength)
    # ADX > 25 且 EMA 斜率强
    df['trend_strength'] = 0
    df.loc[(df['adx_str'] > 25) & (df['ema60_slope'] > 0), 'trend_strength'] = 1 # 牛市
    df.loc[(df['adx_str'] > 25) & (df['ema60_slope'] < 0), 'trend_strength'] = -1 # 熊市

    # ==========================================
    # 4. 深度 PA 特征 (Deep PA Features - V8.5)
    # ==========================================
    
    # A. 缺口 (Gaps) - Step 6 Momentum
    # 牛市缺口: Low > High(prev)
    prev_high = df['high'].shift(1)
    prev_low = df['low'].shift(1)
    df['gap_upside'] = (df['low'] - prev_high).apply(lambda x: max(0.0, x))
    df['has_gap'] = df['gap_upside'] > 0

    # 停牌复牌首日保护: 前一根 bar 来自数周前, 其"缺口"是恢复性缺口,
    # 非突破缺口, 不计入突破判定 (P1④b)
    _resume = df['is_suspension_resume']
    df.loc[_resume, 'gap_upside'] = 0.0
    df.loc[_resume, 'has_gap'] = False
    
    # B. 影线比例 (Tail Ratio) - Step 4 Signal Bar Quality
    # 上影线/Range, 下影线/Range
    df['tail_ratio_upper'] = (df['high'] - df[['open', 'close']].max(axis=1)) / safe_range
    df['tail_ratio_lower'] = (df[['open', 'close']].min(axis=1) - df['low']) / safe_range
    
    # C. 连续性 (Consecutive Bars) - Step 7 Always In
    # 简单的向量化计算连续同向K线数 (Close > Open)
    # 1. 标记 Bull Bar = 1, Bear Bar = -1
    direction = np.where(df['close'] > df['open'], 1, -1)
    # 2. 检查是否与前一根同向
    df['direction'] = direction
    df['same_dir'] = (df['direction'] == df['direction'].shift(1))
    
    # 注意：完全向量化计算连续计数比较复杂，这里用 simplified Rolling Sum 近似
    # 如果最近3根都是同向，则认为 consecutive >= 3
    roll_sum = df['direction'].rolling(window=3).sum().abs()
    df['consecutive_3'] = roll_sum == 3 # True if last 3 bars are same color
    
    # D. 重叠率 (Overlap Rate) - Step 1 Market State
    # (Min(High, PrevHigh) - Max(Low, PrevLow)) / Range
    overlap_h = pd.DataFrame({'current': df['high'], 'prev': prev_high})
    overlap_l = pd.DataFrame({'current': df['low'], 'prev': prev_low})
    
    
    overlap_len = (overlap_h.min(axis=1) - overlap_l.max(axis=1)).apply(lambda x: max(0.0, x))
    df['overlap_rate'] = overlap_len / safe_range

    # ==========================================
    # 5. 阿布结构化特征 (Structured Features - Phase 3)
    # ==========================================
    df = add_al_brooks_features(df)
    
    # ==========================================
    # 6. MTR V1.9 物理学特征 (Physics Features)
    # ==========================================
    df = add_momentum_features(df)

    return df

def add_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    [V1.9 Upgrade] 物理学特征: 动能、缺口、阻力
    """
    # 1. 动能: 下跌缺口计数 (Bear Urgency)
    # Gap Down Definition: High < Prev_Low
    prev_low = df['low'].shift(1)
    gap_down = (df['high'] < prev_low).astype(int)
    # 停牌复牌首日: 与数周前旧 bar 的"下跌缺口"是恢复性缺口, 不计入动能 (P1④b)
    if 'is_suspension_resume' in df.columns:
        gap_down = gap_down.where(~df['is_suspension_resume'], 0)
    # Rolling Sum of Gaps (Last 20 bars)
    df['gap_down_count_20'] = gap_down.rolling(window=20).sum()
    
    # 2. 趋势力竭: 均线缺口 (Gap 20 Bars)
    # 连续多少根 K 线 Close < EMA20 (Bear Trend)
    # Vectorized consecutive count using groupby (similar to bars_below_ema logic already in AL features)
    # We can reuse 'bars_below_ema' or make it more robust here if AL features logic was simplified.
    # Re-using 'bars_below_ema' is sufficient for now.
    
    # 3. 线性回归阻力 (Linreg Resistance) - Fully Vectorized
    # Fit Linreg on Highs (Resistance Line) over last 30 bars
    # Slope m = Cov(x, y) / Var(x)
    # Intercept b = Mean(y) - m * Mean(x)
    window = 30
    y = df['high']
    
    # Valid data check
    if len(df) < window:
        df['linreg_res'] = np.nan
        df['dist_linreg'] = np.nan
        return df

    # Create an index series for covariance
    # Note: rolling().cov() needs consistent index type.
    # If Index is DateTime, we can convert to integer range.
    # Simpler: just use reset index or independent series
    # 🟢 [Fix] Remove explicit index to avoid FutureWarning (Alignment is handled by pandas logic)
    x = pd.Series(np.arange(len(df)))
    
    # Rolling Mean
    mean_y = y.rolling(window=window).mean()
    mean_x = x.rolling(window=window).mean()
    
    # Rolling Covariance
    cov_xy = y.rolling(window=window).cov(x)
    var_x = x.rolling(window=window).var()
    
    # Slope & Intercept
    # 前 window-1 行 rolling 尚未填满 -> cov_xy/var_x 均为 NaN, NaN/NaN 会触发
    # RuntimeWarning: invalid value encountered in true_divide; 用 errstate 抑制
    # (结果仍为 NaN, 不改变任何有效行的计算, 零行为变化)
    with np.errstate(divide='ignore', invalid='ignore'):
        slope = cov_xy / var_x
        intercept = mean_y - (slope * mean_x)
    
    # Current Regression Value (at current bar x)
    df['linreg_res'] = (slope * x) + intercept
    
    # Distance to LinReg (Positive = Above Line, Negative = Below Line)
    # For Bear Trend, Price < LinReg usually.
    # Breakout = Price > LinReg
    df['dist_linreg'] = df['close'] - df['linreg_res']
    
    # 4. 相对量能 (Relative Volume) - Refined
    # Exists as 'relative_vol', ensuring it handles NaN
    if 'relative_vol' not in df.columns:
        vol_ma = df['volume'].rolling(window=20).mean()
        df['relative_vol'] = df['volume'] / vol_ma.replace(0, 1)

    return df

def add_al_brooks_features(df: pd.DataFrame) -> pd.DataFrame:
    """[Phase 3] 计算阿布特有的结构化指标 (Signal Class, Counting, Magnets)"""
    # 1. Signal Bar Classification
    # Strong Bull Rev: Bull Body + Close in Top 1/3 + Tail < 40%
    # TBTL (Ten Bars Two Legs) filters would use this
    
    # Pre-calc conditions
    is_bull = df['close'] > df['open']
    close_loc = df['close_loc'] # 0.0 ~ 1.0
    body_pct = df['body_pct']
    
    # 信号分类 (Text Label mapped to Int for efficiency or keeping as distinct bools)
    df['sb_class'] = 'Neutral'
    
    # Strong Bull Reversal
    cond_strong_bull = (is_bull) & (close_loc > 0.66) & (df['tail_ratio_upper'] < 0.35)
    df.loc[cond_strong_bull, 'sb_class'] = 'Strong_Bull'
    
    # Strong Bear Reversal
    cond_strong_bear = (~is_bull) & (close_loc < 0.33) & (df['tail_ratio_lower'] < 0.35)
    df.loc[cond_strong_bear, 'sb_class'] = 'Strong_Bear'
    
    # Doji
    df.loc[body_pct < 0.2, 'sb_class'] = 'Doji'
    
    # 2. Setup Counting (H1/H2 Simplified)
    # 逻辑：High 1 是回调后第一次 High 突破前一根 High
    # 这在向量化中比较难完美实现，这里用 "Bars Since Pullback" 代替
    # Pullback 定义: Close 下穿 EMA20 (Bull Trend) 或 High < EMA20
    
    # 标记 Pullback 状态 (Close below EMA20)
    df['below_ema'] = df['close'] < df['ema20']
    # 简单的计数：连续在 EMA 下方多少根
    # Group by consecutive 'below_ema' blocks
    # (这里为了性能仅做简单标记，复杂计数交给 Prompt 的微观数据分析或后续专门逻辑)
    df['bars_below_ema'] = df['below_ema'].groupby((df['below_ema'] != df['below_ema'].shift()).cumsum()).cumcount() + 1
    df.loc[~df['below_ema'], 'bars_below_ema'] = 0
    
    # 3. Magnet Targets (磁力点)
    # 距离最近的 Swing High / Low (ATR 距离)
    # 前面已计算 'res_level' (Recent Swing High)
    df['dist_to_res'] = (df['res_level'] - df['close']) / df['atr'].replace(0, 1)
    df['dist_to_sup'] = (df['close'] - df['sup_level']) / df['atr'].replace(0, 1)
    
    # Measured Move (Leg 1 * 1.0)
    # 简化：假设最近一个波段幅度是 5 * ATR (默认拍脑袋，后续可精确化)
    # 用于告诉 AI 潜在空间
    df['mm_target_bull'] = df['close'] + (5 * df['atr'])
    
    return df

def calculate_targets(df: pd.DataFrame, sl_atr_multiplier: float = 2.0) -> pd.DataFrame:
    """
    [V8.3] 计算止损止盈位 (Vectorized)
    
    Args:
        df: 包含 close, atr 指标的 DataFrame
        sl_atr_multiplier: 止损倍数 (默认 2.0 ATR)
        
    Returns:
        df: 新增 entry_price, sl_price, tp1_price, tp2_price
    """
    if 'atr' not in df.columns:
        return df
        
    # 入场价：默认为当前收盘价 (市价单逻辑)
    df['entry_price'] = df['close']
    
    # 止损价 (做多逻辑): Entry - N * ATR
    df['sl_price'] = df['close'] - (df['atr'] * sl_atr_multiplier)
    
    # 风险 (R)
    risk = df['entry_price'] - df['sl_price']
    
    # 止盈价 (TP Levels)
    # TP1: 1.5R (胜率优先)
    # TP2: 3.0R (盈亏比优先)
    df['tp1_price'] = df['entry_price'] + (risk * 1.5)
    df['tp2_price'] = df['entry_price'] + (risk * 3.0)
    
    return df
