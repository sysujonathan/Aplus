# core/strategies/gap_h2_enhanced_strategy.py
"""
[Strategy] Gap + High 2 增强版 (回测实验策略, 不进入生产扫描)

本策略是 STRATEGY_GAP_H2 的参数化实验分支, 用于在历史回测中对比以下改动的影响:
  1. 缺口定义: 真缺口 (low>前60最高高)  ↔  实体缺口 (实体下沿>前60最高实体上沿)
  2. 新高重置: 突破后回调期创新高是否重置 H1/H2 计数器 (防御"新高后假 H2")
  3. 止损位置: 缺口地板  ↔  第二次回调低点下方 (更贴合 H2 形态, R 更佳)
  4. 回调窗口: 40/2  ↔  20/3

设计纪律:
  - 严格复用父类的信号骨架 (LHLL→HH→LHLL 状态机), 仅替换"地板/低点参考系"与"计数器重置"两处。
  - 完全纯 PA (价格行为), 不引入成交量/指标/基本面, 符合系统 PA 铁律。
  - 列名 (signal_gap_h2 / sl_gap_h2 / entry_gap_h2 / tp_gap_h2 / gap_h2_floor_exact /
    bars_since_breakout_h2 / sig_bar_quality_h2) 与父类完全一致, 以便复用现有回测框架
    的 evaluate_trade 与生命周期三过滤, 保证口径一致。
  - 注册时 supported_timeframes=['backtest'], 不进入日/周线生产扫描池 (参考 monthly_range_break 范式)。

注意: 本文件是回测实验品, 经回测评估且用户批准覆盖前, 原 STRATEGY_GAP_H2 生产逻辑不动。
"""

import numpy as np
import pandas as pd
import logging
from .gap_h2_strategy import GapH2Strategy

logger = logging.getLogger(__name__)

EPS = 1e-3


class GapH2EnhancedStrategy(GapH2Strategy):
    """
    Gap + High 2 增强版 (参数化实验策略)

    相对父类 STRATEGY_GAP_H2 的可调项:
      gap_mode:        'true' 真缺口 / 'body' 实体缺口
      reset_on_newhigh: 突破后回调期创新高是否重置计数器
      sl_mode:         'floor' 缺口地板 / 'pullback' 第二次回调低点下方
      max_pb / min_pb: 回调窗口 [min_pb, max_pb]
      sl_tick_buffer:  回调低点止损相对信号K低点的缓冲 (最小变动单位)
    """

    @property
    def name(self) -> str:
        return "STRATEGY_GAP_H2_ENHANCED"

    @classmethod
    def get_metadata(cls) -> dict:
        """元数据: supported_timeframes=['backtest'] → 不进入日/周线生产扫描池"""
        meta = super().get_metadata()
        meta.update({
            'display_name': 'GAP H2 增强(实验)',
            'supported_timeframes': ['backtest'],
        })
        # 增强版仅回测, calculate_signals 不产出生产活跃列(active_*), 移除父类声明避免误读
        for _k in ('active_signal_column', 'active_entry_column', 'active_sl_column', 'active_tp_columns'):
            meta.pop(_k, None)
        return meta

    def __init__(self, gap_mode: str = 'body', reset_on_newhigh: bool = True,
                 sl_mode: str = 'pullback', max_pb: int = 20, min_pb: int = 3,
                 sl_tick_buffer: float = 0.01):
        super().__init__()
        # 回调窗口按实验设计默认 20/3 (与增强策略卡一致); 原版 40/2 由调用方显式传入
        self.MAX_PULLBACK_WINDOW = int(max_pb)
        self.MIN_PULLBACK_WINDOW = int(min_pb)
        self.gap_mode = gap_mode
        self.reset_on_newhigh = bool(reset_on_newhigh)
        self.sl_mode = sl_mode
        self.sl_tick_buffer = float(sl_tick_buffer)

    # =====================================================================
    # 主入口: 向量化计算信号 + 定单参数
    # =====================================================================
    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        实体缺口 / 新高重置 / 回调低点止损 三处可开关的增强版信号计算。
        状态机骨架与父类一致 (LHLL→HH→LHLL), 仅替换参考系与计数器重置逻辑。
        """
        if len(df) < self.LOOKBACK_WINDOW + 5:
            df['signal_gap_h2'] = False
            return df

        required = ['atr', 'ema20']
        if not all(col in df.columns for col in required):
            logger.warning(f"GapH2Enhanced 缺少列: {[c for c in required if c not in df.columns]}")
            df['signal_gap_h2'] = False
            return df

        W = self.LOOKBACK_WINDOW

        # ------------------------------------------------------------------
        # 1. 结构性突破 (与父类一致: 更高高 + 更高低)
        # ------------------------------------------------------------------
        is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))

        # 真缺口参考系: 前60根最高高
        true_floor_raw = df['high'].rolling(min_periods=1, window=W).max().shift(2)
        true_swing_raw = df['low'].rolling(min_periods=1, window=W).min().shift(2)

        # 实体缺口参考系: 前60根最高实体上沿 / 最低实体下沿
        body_top = np.maximum(df['open'], df['close'])     # 实体上沿 (阳线收/阴线开, 取高)
        body_bottom = np.minimum(df['open'], df['close'])  # 实体下沿 (阳线开/阴线收, 取低)
        body_floor_raw = body_top.rolling(min_periods=1, window=W).max().shift(2)
        body_swing_raw = body_bottom.rolling(min_periods=1, window=W).min().shift(2)

        if self.gap_mode == 'body':
            # 实体缺口: 突破K的实体下沿 ≥ 前60根最高实体上沿 (两根实体无重叠)
            breakout = is_hh_hl & (body_bottom > body_floor_raw - EPS)
            floor_raw = body_floor_raw
            swing_raw = body_swing_raw
        else:
            # 真缺口: 突破K最低价 > 前60根最高高
            breakout = is_hh_hl & (df['low'] > true_floor_raw - EPS)
            floor_raw = true_floor_raw
            swing_raw = true_swing_raw

        # 锚定 + ffill (与父类一致)
        floor = pd.Series(np.where(breakout, floor_raw, np.nan), index=df.index).ffill()
        swing = pd.Series(np.where(breakout, swing_raw, np.nan), index=df.index).ffill()

        bs = breakout.cumsum()                       # 突破组编号
        df['bars_since_breakout_h2'] = bs
        bar_count = bs.groupby(bs).cumcount()        # 组内第几根 (突破根=0)

        # 缺口存活 (用所选参考系的地板)
        group_min_low = df['low'].groupby(bs).expanding().min().droplevel(0)
        gap_open = group_min_low > (floor - EPS)
        df['gap_h2_open'] = gap_open

        # 高潮规避器: TP = 2*floor - swing
        target = 2.0 * floor - swing
        group_max_high = df['high'].groupby(bs).expanding().max().droplevel(0)
        mm_not_reached = (group_max_high < target) | target.isna()
        mm_not_reached = mm_not_reached.fillna(True)

        # 信号当根成色 (与父类一致)
        _range = df['high'] - df['low']
        safe = _range.replace(0, np.nan)
        df['sig_bar_quality_h2'] = ((df['close'] - df['low']) / safe).round(3)

        # ------------------------------------------------------------------
        # 2. 信号掩码: 状态机 (LHLL→HH→LHLL)
        # ------------------------------------------------------------------
        if self.reset_on_newhigh:
            signal = self._signal_mask_reset(df, breakout, bs, bar_count, gap_open, mm_not_reached)
        else:
            signal = self._signal_mask_vectorized(df, bs, bar_count, gap_open, mm_not_reached)

        # 去重: 每个突破组仅取首次信号 (与父类一致)
        _already = signal.groupby(bs).cumsum().shift(1).fillna(0) > 0
        df['signal_gap_h2'] = signal & ~_already

        # ------------------------------------------------------------------
        # 3. 定单参数
        # ------------------------------------------------------------------
        # Entry = 信号K高点(初始挂单价); 动态入场由回测脚本自调 _apply_dynamic_entry 计算
        df['entry_gap_h2'] = np.where(df['signal_gap_h2'], df['high'], np.nan)
        if self.sl_mode == 'pullback':
            # 回调低点止损: 第二次回调 (信号K, 即LHLL) 最低价下方缓冲
            df['sl_gap_h2'] = np.where(df['signal_gap_h2'], df['low'] - self.sl_tick_buffer, np.nan)
        else:
            # 缺口地板止损
            df['sl_gap_h2'] = np.where(df['signal_gap_h2'], floor, np.nan)
        df['tp_gap_h2'] = np.where(df['signal_gap_h2'], target, np.nan)

        # 兼容性列 (供 compute_rating / 图表标注复用, 不影响回测)
        df['gap_h2_floor_exact'] = np.where(df['signal_gap_h2'], floor, np.nan)
        df['gap_h2_prior_low'] = np.where(df['signal_gap_h2'], swing, np.nan)
        df['gap_h2_top_exact'] = np.where(df['signal_gap_h2'], group_min_low.shift(1), np.nan)

        return df

    # =====================================================================
    # 状态机: 无重置 (向量化, 与父类逻辑同构, 仅地板可切换)
    # =====================================================================
    def _signal_mask_vectorized(self, df, bs, bar_count, gap_open, mm_not_reached):
        in_window = ((bar_count >= self.MIN_PULLBACK_WINDOW) &
                     (bar_count <= self.MAX_PULLBACK_WINDOW) &
                     (bs > 0))

        is_lhll = (df['high'] < df['high'].shift(1)) & (df['low'] < df['low'].shift(1))
        is_hh = df['high'] > df['high'].shift(1)

        lhll_cum = is_lhll.groupby(bs).cumsum()
        phase1 = lhll_cum >= 1
        is_hh_after = is_hh & phase1
        hh_cum = is_hh_after.groupby(bs).cumsum()
        phase2 = hh_cum >= 1
        is_lhll_after = is_lhll & phase2
        lhll_cum_after = is_lhll_after.groupby(bs).cumsum()
        prev = lhll_cum_after.groupby(bs).shift(1).fillna(0)
        second_start = (prev == 0) & (lhll_cum_after >= 1)

        raw = in_window & gap_open & second_start & mm_not_reached
        return raw

    # =====================================================================
    # 状态机: 新高重置 (路径依赖, 逐突破组在窗口内循环)
    # 规则: 突破后回调期内, 任一根K最高价超过"突破以来的绝对高点(Spike High)"→
    #       清空 H1/H2 计数器, 以该新高设为新 Spike High, 从零重新找 H1。
    # 窗口外不再可能产生信号, 循环在 [b0+1, b0+MAX+1) 内即可。
    # =====================================================================
    def _signal_mask_reset(self, df, breakout, bs, bar_count, gap_open, mm_not_reached):
        n = len(df)
        high = df['high'].values
        low = df['low'].values
        bidx = np.where(breakout.values)[0]
        group_ids = bs.values
        signal = np.zeros(n, dtype=bool)
        if len(bidx) == 0:
            return pd.Series(signal, index=df.index)

        already = {}  # group -> 是否已出信号
        for b0 in bidx:
            g = int(group_ids[b0])
            if already.get(g, False):
                continue
            spike = high[b0]
            saw_lhll1 = False
            saw_hh1 = False
            end = min(n, b0 + self.MAX_PULLBACK_WINDOW + 1)
            for i in range(b0 + 1, end):
                bc = int(bar_count.values[i])
                if not (self.MIN_PULLBACK_WINDOW <= bc <= self.MAX_PULLBACK_WINDOW):
                    continue
                h, l = high[i], low[i]
                is_lhll = (high[i] < high[i - 1]) and (low[i] < low[i - 1])
                is_hh = high[i] > high[i - 1]

                if h > spike + EPS:
                    # 新高 → 计数器销毁与重置, 以此为新的 Spike High
                    saw_lhll1 = False
                    saw_hh1 = False
                    spike = h
                    continue

                if is_lhll and not saw_lhll1:
                    saw_lhll1 = True
                elif is_hh and saw_lhll1 and not saw_hh1:
                    saw_hh1 = True
                elif is_lhll and saw_lhll1 and saw_hh1:
                    # 第二次回调起点 (H2 候选) → 触发信号
                    if bool(gap_open.values[i]) and bool(mm_not_reached.values[i]):
                        signal[i] = True
                        already[g] = True
                        break
        return pd.Series(signal, index=df.index)
