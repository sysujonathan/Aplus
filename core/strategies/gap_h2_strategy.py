# core/strategies/gap_h2_strategy.py
"""
[Strategy] Gap + High 2 策略 (Al Brooks 经典两腿回调入场)

理论基础：Al Brooks Price Action — High 2 Entry After Measuring Gap
核心信号：突破缺口后，经历两腿回调 (LHLL → HH → LHLL) 的经典 H2 入场

信号状态机 (State Machine):
  Phase 0: 突破缺口成立，等待第一次回调
  Phase 1: 检测到首根 LHLL (Lower High + Lower Low) → 空头第一次尝试
  Phase 2: 检测到首根 HH (Higher High) → High 1，多头恢复
  Phase 3: 检测到首根 LHLL → 空头第二次尝试 → 信号! 挂 Buy Stop

其他条件与 gap_pinbar_strategy 一致：
  - 缺口存活监控 (Gap Floor 不得被击穿)
  - TP = 2 * Gap_Floor - Prior_Swing_Low
  - SL = Gap_Floor
  - 高潮规避器
  - 去重 (每次突破仅取首次信号)
"""

import pandas as pd
import numpy as np
import logging
import re
from typing import Dict, Any, Optional
from .base import BaseStrategy
from core.formatter import get_common_context
from config import settings
from core.rating import RatingResult, clamp, band, band_calibrated, is_calibration_available
from core.rating_core import (quality_factor, pb_speed_factor, gap_width_factor,
                              consec_bear_penalty, time_decay_factor, sum_weights, factor)

logger = logging.getLogger(__name__)


class GapH2Strategy(BaseStrategy):
    """
    Gap + High 2 (两腿回调) 策略

    Al Brooks 的 High 2 是趋势中最可靠的顺势入场之一：
    空头两次尝试反转均失败 → 被困空头止损盘成为多头燃料。
    """

    @property
    def name(self) -> str:
        return "STRATEGY_GAP_H2"

    @property
    def description(self) -> str:
        return "Gap + High 2 (Two-Leg Pullback After Measuring Gap)"

    @property
    def signal_column(self) -> str:
        return 'signal_gap_h2'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """Gap H2 策略元数据声明"""
        return {
            'display_name': 'GAP H2',
            'sl_column': 'sl_gap_h2',
            'entry_column': 'entry_gap_h2',
            'tp_columns': ['tp_gap_h2'],
            'score_column': 'sig_bar_quality_h2',
            'signal_column': 'signal_gap_h2',
            'supported_timeframes': ['daily', 'weekly'],
            'tp_multiplier': 2.0,
            'ai_audit': False,
            'bars_since_breakout_column': 'bars_since_breakout_h2',
            'gap_top_exact_column': 'gap_h2_top_exact',
            # 生产活跃列 (日线 last-bar-only 持续提醒): 活跃单投影到最后一根K,
            # 挂单价随回调动态下移; 信号K列(signal_gap_h2 等)保留给回测/评级/画图定位
            'active_signal_column': 'signal_active_gap_h2',
            'active_entry_column': 'entry_active_gap_h2',
            'active_sl_column': 'sl_active_gap_h2',
            'active_tp_columns': ['tp2r_active_gap_h2', 'tp_active_gap_h2'],
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """Gap H2 信号信息提取 — 包含信号质量"""
        result = super().get_signal_info(df)
        
        if df is None or df.empty:
            return result
        
        extra_info = result.get('extra_info', {})
        row = df.iloc[-1]
        
        q = row.get('sig_bar_quality_h2', 0)
        extra_info['sig_quality'] = q
        
        if extra_info:
            result['extra_info'] = extra_info
        
        return result

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'daily') -> Optional['RatingResult']:
        """[RATING_PLAN §4.3] Gap+H2 评级: 缺口家族四因子骨架 + 缺口全程存活 + 两腿对称性 (纯 PA)."""
        if df is None or df.empty:
            return None
        meta = cls.get_metadata()
        sig_col = meta.get('signal_column', '')
        sig_pos = len(df) - 1
        sig_row = df.iloc[-1]
        if sig_col and sig_col in df.columns and df[sig_col].fillna(False).any():
            sp = df.index[df[sig_col].fillna(False)]
            sig_pos = df.index.get_loc(sp[-1])
            sig_row = df.iloc[sig_pos]

        q = float(sig_row.get('sig_bar_quality_h2', 0) or 0)
        sl = sig_row.get('sl_gap_h2', np.nan)
        gap_top = sig_row.get('gap_h2_top_exact', np.nan)
        gap_size_pct = 0.0
        if pd.notna(gap_top) and pd.notna(sl) and sl > 0:
            gap_size_pct = round((float(gap_top) - float(sl)) / float(sl) * 100, 2)

        pb_bars = int(sig_row.get('bars_since_breakout_h2', 5) or 5)
        bears = 0
        if 'bars_since_breakout_h2' in df.columns and pb_bars > 0 and 0 <= sig_pos - pb_bars < sig_pos:
            pb_df = df.iloc[sig_pos - pb_bars: sig_pos]
            is_bear = pb_df['close'] < pb_df['open']
            if len(is_bear) > 1:
                g = (is_bear != is_bear.shift()).cumsum()
                bg = is_bear.groupby(g).sum()
                bears = int(bg.max()) if not bg.empty else 0
        bars_passed = max(0, len(df) - 1 - sig_pos)

        f_q = quality_factor(q)
        f_pb = pb_speed_factor(pb_bars)
        f_gap = gap_width_factor(gap_size_pct)
        f_bear = consec_bear_penalty(bears)
        f_decay = time_decay_factor(bars_passed)

        # H2 专属签名 (纯 PA)
        gap_open = bool(sig_row.get('gap_h2_open', False)) if 'gap_h2_open' in df.columns else False
        f_gapopen = factor('缺口全程存活', 1.0 if gap_open else 0.0, gap_open, 1.0 if gap_open else 0.0,
                           sop_ref='SOP Step 6 Gap Integrity', note='回调全期缺口未破=强' if gap_open else '缺口曾破')
        leg2_shallow = False
        try:
            if sig_pos >= 2 and 'high' in df.columns and 'low' in df.columns:
                is_lhll = (df['high'] < df['high'].shift(1)) & (df['low'] < df['low'].shift(1))
                pre = df.iloc[:sig_pos]
                lhll_lows = pre['low'][is_lhll.iloc[:sig_pos]]
                if len(lhll_lows) >= 2:
                    leg2_shallow = float(lhll_lows.iloc[-1]) >= float(lhll_lows.iloc[-2])
        except Exception:
            leg2_shallow = False
        # [Phase 2 校准] 回测显示 L2低点≥L1(经典H2) 反而输钱 (命中组34.7% vs 未中42.7%, w=-0.8, δ≈8点)
        # 方向翻转: leg2_shallow 由 +1 改为 -1; hit 仍记 leg2_shallow 与回测 factor_hits 口径一致
        leg2_weight = -1.0 if leg2_shallow else 0.0
        f_leg2 = factor('两腿对称性', 1.0 if leg2_shallow else 0.0, leg2_shallow, leg2_weight,
                        sop_ref='SOP Step 6 H2', note='回测倒挂: L2≥L1反而输钱→降权' if leg2_shallow else 'L2跌破L1(更优)')

        factors = [f_q, f_pb, f_gap, f_bear, f_decay, f_gapopen, f_leg2]
        raw = sum_weights(factors)
        score = clamp(50 + 10 * raw)
        toxic = raw <= -3
        letter = band_calibrated(cls, score, toxic=toxic, timeframe=timeframe)
        return RatingResult(raw_score=raw, score=score, letter=letter, factors=factors,
                            toxic=toxic, calibrated=is_calibration_available())

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> int:
        """Gap H2 图表标注"""
        from core.strategies.structural_gap_strategy import _annotate_gap_strategy
        return _annotate_gap_strategy(ax, plot_df, strategy_type, **kwargs)

    def __init__(self):
        # 突破判定窗口
        self.LOOKBACK_WINDOW = getattr(settings, 'STRUCT_GAP_LOOKBACK', 60)
        # 回调确认最大跟踪窗口
        self.MAX_PULLBACK_WINDOW = getattr(settings, 'STRUCT_GAP_MAX_WINDOW', 40)
        # 最小回调周期
        self.MIN_PULLBACK_WINDOW = 2

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        向量化计算 Gap + High 2 信号。

        状态机转换 (全部在 breakout group 内追踪)：
          LHLL → HH → LHLL = Signal
        """
        if len(df) < self.LOOKBACK_WINDOW + 5:
            df['signal_gap_h2'] = False
            return df

        required = ['atr', 'ema20']
        if not all(col in df.columns for col in required):
            logger.warning(f"Gap H2 Strategy 缺少列: {[c for c in required if c not in df.columns]}")
            df['signal_gap_h2'] = False
            return df

        # ==============================================================================
        # 第一步：识别结构性突破 (与 gap_pinbar 一致)
        # ==============================================================================
        is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
        _gap_floor_raw = df['high'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).max().shift(2)
        df['is_breakout_h2'] = is_hh_hl & (df['low'] > _gap_floor_raw - 1e-3)

        # ==============================================================================
        # 第二步：锚定历史数据 (与 gap_pinbar 一致)
        # ==============================================================================
        _prior_swing_low_raw = df['low'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).min().shift(2)

        _gap_floor = np.where(df['is_breakout_h2'], _gap_floor_raw, np.nan)
        _gap_floor = pd.Series(_gap_floor, index=df.index).ffill()

        _prior_swing_low = np.where(df['is_breakout_h2'], _prior_swing_low_raw, np.nan)
        _prior_swing_low = pd.Series(_prior_swing_low, index=df.index).ffill()

        # ==============================================================================
        # 第三步：缺口存活监控 (与 gap_pinbar 一致)
        # ==============================================================================
        _bars_since_breakout = df['is_breakout_h2'].cumsum()
        _bar_count = df.groupby(_bars_since_breakout).cumcount()

        _group_min_low = df['low'].groupby(_bars_since_breakout).expanding().min().droplevel(0)
        df['gap_h2_open'] = _group_min_low > (_gap_floor - 1e-3)

        # ==============================================================================
        # 第四步：两腿回调状态机 (High 2 State Machine)
        # ==============================================================================
        # 回调时间窗口
        in_window = (_bar_count >= self.MIN_PULLBACK_WINDOW) & (_bar_count <= self.MAX_PULLBACK_WINDOW)
        in_window = in_window & (_bars_since_breakout > 0)

        # --- 基础 PA 模式识别 ---
        # LHLL (Lower High + Lower Low): 回调特征 K 线
        is_lhll = (df['high'] < df['high'].shift(1)) & (df['low'] < df['low'].shift(1))
        # HH (Higher High): 多头恢复特征 K 线
        is_hh = df['high'] > df['high'].shift(1)

        # --- Phase 1：检测第一次回调 (首根 LHLL) ---
        lhll_cum = is_lhll.groupby(_bars_since_breakout).cumsum()
        phase1_done = lhll_cum >= 1  # 第一次回调已发生

        # --- Phase 2：检测 High 1 (第一次回调后的首根 HH) ---
        is_hh_after_pb1 = is_hh & phase1_done
        hh_cum_after_pb1 = is_hh_after_pb1.groupby(_bars_since_breakout).cumsum()
        phase2_done = hh_cum_after_pb1 >= 1  # High 1 已确认

        # --- Phase 3：检测第二次回调 (High 1 后的首根 LHLL) → 信号! ---
        is_lhll_after_h1 = is_lhll & phase2_done
        lhll_cum_after_h1 = is_lhll_after_h1.groupby(_bars_since_breakout).cumsum()

        # 锁定"首根"：上一根的累计计数为 0，当前变为 1
        prev_lhll_cum = lhll_cum_after_h1.groupby(_bars_since_breakout).shift(1).fillna(0)
        is_second_pullback_start = (prev_lhll_cum == 0) & (lhll_cum_after_h1 >= 1)

        # 信号 K 线质量指标
        _bar_range = df['high'] - df['low']
        _safe_range = _bar_range.replace(0, np.nan)
        _sig_quality = (df['close'] - df['low']) / _safe_range
        df['sig_bar_quality_h2'] = _sig_quality.round(3)

        # ==============================================================================
        # [高潮规避器] TP = 2 * Gap_Floor - Prior_Swing_Low
        # ==============================================================================
        _target_uncond = 2 * _gap_floor - _prior_swing_low

        _group_max_high = df['high'].groupby(_bars_since_breakout).expanding().max().droplevel(0)
        _mm_not_reached = (_group_max_high < _target_uncond) | _target_uncond.isna()
        _mm_not_reached = _mm_not_reached.fillna(True)

        # ==============================================================================
        # 组装完整信号
        # ==============================================================================
        _signal_raw = (
            in_window &                    # 时间窗口
            df['gap_h2_open'] &            # 缺口存活
            is_second_pullback_start &     # 两腿回调状态机通过
            _mm_not_reached                # 高潮规避
        )

        # 去重：每次突破仅取首次信号
        _already = _signal_raw.groupby(_bars_since_breakout).cumsum().shift(1).fillna(0) > 0
        df['signal_gap_h2'] = _signal_raw & ~_already

        # 回测/分析数据
        df['bars_since_breakout_h2'] = _bars_since_breakout

        # ==============================================================================
        # 第五步：定单参数生成
        # ==============================================================================
        # SL = Gap Floor
        df['sl_gap_h2'] = np.where(df['signal_gap_h2'], _gap_floor, np.nan)

        # Entry = 信号 K 线 (第二次回调 LHLL) 的最高点，次日挂 Buy Stop
        df['entry_gap_h2'] = np.where(df['signal_gap_h2'], df['high'], np.nan)

        # TP = 2 * Gap_Floor - Prior_Swing_Low
        df['tp_gap_h2'] = np.where(df['signal_gap_h2'], _target_uncond, np.nan)

        # 关键锚点 (绘图/通知)
        df['gap_h2_prior_low'] = np.where(df['signal_gap_h2'], _prior_swing_low, np.nan)
        df['gap_h2_floor_exact'] = np.where(df['signal_gap_h2'], _gap_floor, np.nan)
        df['gap_h2_top_exact'] = np.where(df['signal_gap_h2'], _group_min_low.shift(1), np.nan)

        # 时间坐标
        try:
            df['gap_h2_prior_low_date'] = df['low'].rolling(
                window=self.LOOKBACK_WINDOW, min_periods=1).idxmin().shift(2)
            df['gap_h2_floor_date'] = df['high'].rolling(
                window=self.LOOKBACK_WINDOW, min_periods=1).idxmax().shift(2)
        except AttributeError:
            pass

        df['gap_h2_test_date'] = df.index.to_series().shift(1)

        # ==============================================================================
        # 第六步：生产活跃投影 (日线持续提醒) — 活跃单投影到最后一根K
        # 与回测动态入场共用"只看高点 HH"判据: 高点没抬升(含内包K)就下移, 抬升就收口。
        # 生产只把"截至今天仍活着的挂单"浮到今天这根K, 供 last-bar-only 扫描读取。
        # ==============================================================================
        _active_sig, _active_entry, _active_sl, _active_tp, _active_tp2r = self._project_active_entry(
            df, df['signal_gap_h2'], _gap_floor, _target_uncond, timeout=30)
        df['signal_active_gap_h2'] = _active_sig
        df['entry_active_gap_h2'] = _active_entry
        df['sl_active_gap_h2'] = _active_sl
        df['tp_active_gap_h2'] = _active_tp
        df['tp2r_active_gap_h2'] = _active_tp2r

        return df

    # =====================================================================
    # 动态入场价: H2 跟踪挂单 (回测/复盘用, 只看高点 HH)
    # ---------------------------------------------------------------------
    # 信号K(第二腿回调起点)为初始挂单价; 其后逐根扫描:
    #   - low <= 缺口地板            → 破位撤单, 信号失效(signal=False)
    #   - high >= 测量目标(MM)       → 先达目标作废(signal=False)
    #   - 超时(>timeout根)           → 过期(signal=False)
    #   - high > 参考高点            → 高点抬升(HH), 收口成交(entry=参考高点, 成交K=该根)
    #   - 否则(high <= 参考, 含内包K) → 挂单下移到该K高点, 继续
    # 判据只认"高点抬升(HH)", 不要求低点也下降(LHLL) — Al Brooks: 回调=趋势中任何暂停,
    # 含单根内包K; 回调结束的唯一标志是出现更高的高点。
    # 返回 (final_signal, entry_price, entry_bar); entry_bar 为成交K全局索引, 失效为 NaN。
    # =====================================================================
    def _apply_dynamic_entry(self, df: pd.DataFrame, signal: pd.Series,
                             floor_series: pd.Series, tp_series: pd.Series = None,
                             timeout: int = 30):
        n = len(df)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        sig_idx = np.where(signal.fillna(False).values)[0]
        final = signal.copy()
        entry = pd.Series(np.nan, index=df.index, dtype=float)
        entry_bar = pd.Series(np.nan, index=df.index, dtype=float)
        SCAN = int(self.MAX_PULLBACK_WINDOW)
        for i in sig_idx:
            fl = floor_series.iloc[i]
            tp = (tp_series.iloc[i] if (tp_series is not None
                and not pd.isna(tp_series.iloc[i])) else np.inf)
            if pd.isna(fl):
                final.iloc[i] = False
                continue
            ref_high = high[i]          # 初始挂单价 = 信号K高点
            e = high[i]
            ok, bar, bw = False, np.nan, 0
            end = min(n, i + 1 + SCAN)
            for j in range(i + 1, end):
                bw += 1
                hj, lj = high[j], low[j]
                if lj <= fl + 1e-9:     # 破地板 → 撤单
                    break
                if hj >= tp:            # 测量目标先达 → 作废
                    break
                if bw > timeout:        # 超时 → 过期
                    break
                if hj > ref_high:       # HH 高点抬升 → 收口成交
                    e, ok, bar = ref_high, True, j
                    break
                ref_high = hj           # 未抬升(含内包K) → 下移
            if ok:
                entry.iloc[i], entry_bar.iloc[i] = e, bar
            else:
                final.iloc[i] = False
        return final, entry, entry_bar

    # =====================================================================
    # 生产活跃投影 (日线 last-bar-only 持续提醒)
    # ---------------------------------------------------------------------
    # 对每个信号K, 从信号K逐根走到最后一根K(今天), 判据与回测一致(只看高点HH):
    #   - 破地板 / 先达目标 / 超时 / 高点抬升收口 → 该单不再"待挂" (不投影)
    #   - 否则(未收口) → 挂单价持续下移, 走到今天仍活着 = 活跃挂单
    # 取"最新触发"的活跃单, 把 signal/entry/sl/tp/2R 投影到最后一根K,
    # 供生产 scanner 的 iloc[-1] 读取 → 每晚图上 Entry = 现在该挂的价。
    # 2R = 挂单价 + 2×(挂单价 − 地板); MM(测量目标) = 固定值(随信号K确定, 不随挂单价变)。
    # =====================================================================
    def _project_active_entry(self, df: pd.DataFrame, signal: pd.Series,
                              floor_series: pd.Series, tp_series: pd.Series = None,
                              timeout: int = 30):
        n = len(df)
        active_sig = pd.Series(False, index=df.index)
        active_entry = pd.Series(np.nan, index=df.index, dtype=float)
        active_sl = pd.Series(np.nan, index=df.index, dtype=float)
        active_tp = pd.Series(np.nan, index=df.index, dtype=float)
        active_tp2r = pd.Series(np.nan, index=df.index, dtype=float)
        if n == 0:
            return active_sig, active_entry, active_sl, active_tp, active_tp2r
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        sig_idx = np.where(signal.fillna(False).values)[0]
        last = n - 1
        best = None  # (信号K索引, 当前挂单价, 地板, MM目标)
        for i in sig_idx:
            fl = floor_series.iloc[i]
            tp = (tp_series.iloc[i] if (tp_series is not None
                and not pd.isna(tp_series.iloc[i])) else np.inf)
            if pd.isna(fl):
                continue
            ref_high = high[i]          # 初始挂单价 = 信号K高点
            alive = True
            bw = 0
            for j in range(i + 1, n):
                bw += 1
                hj, lj = high[j], low[j]
                if lj <= fl + 1e-9:     # 破地板 → 撤单
                    alive = False
                    break
                if hj >= tp:            # 目标先达 → 作废
                    alive = False
                    break
                if bw > timeout:        # 超时 → 过期
                    alive = False
                    break
                if hj > ref_high:       # HH 收口成交 → 已成交, 不再待挂
                    alive = False
                    break
                ref_high = hj           # 未抬升(含内包K) → 下移
            if alive and (best is None or i > best[0]):
                best = (i, ref_high, fl, tp)
        if best is not None:
            i, ref_high, fl, tp = best
            active_sig.iloc[last] = True
            active_entry.iloc[last] = ref_high
            active_sl.iloc[last] = fl
            active_tp.iloc[last] = tp
            r = ref_high - fl
            if r > 0:
                active_tp2r.iloc[last] = ref_high + 2.0 * r
        return active_sig, active_entry, active_sl, active_tp, active_tp2r

    def _calculate_context(self, df: pd.DataFrame) -> str:
        """为 AI 审计提供结构上下文"""
        try:
            latest = df.iloc[-1]
            gap_status = "OPEN" if latest.get('gap_h2_open', False) else "CLOSED"

            sig = latest.get('signal_gap_h2', False)
            entry = latest.get('entry_gap_h2', np.nan)
            sl = latest.get('sl_gap_h2', np.nan)
            tp = latest.get('tp_gap_h2', np.nan)

            if sig and not np.isnan(entry):
                status_str = f"LOCKED ✅ Buy Stop={entry:.2f} | SL(Floor)={sl:.2f} | TP={tp:.2f}"
            else:
                status_str = "MONITORING (Waiting for H2 Setup)"

            return f"""
<GAP_H2_CONTEXT>
  <MACRO_WINDOW>{self.LOOKBACK_WINDOW} Bars</MACRO_WINDOW>
  <GAP_STATUS>{gap_status}</GAP_STATUS>
  <SIGNAL_TYPE>High 2 (Two-Leg Pullback: LHLL → HH → LHLL)</SIGNAL_TYPE>
  <SETUP_STATUS>{status_str}</SETUP_STATUS>
</GAP_H2_CONTEXT>
"""
        except Exception:            return "<GAP_H2_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)

        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)

您正在审计【Gap + High 2 两腿回调策略】的买入信号。

# 🕵️ Brooks Framework For High 2 After Gap
1. **The Breakout**: 股价跨越了 {self.LOOKBACK_WINDOW} 周期最高点，形成结构性缺口。
2. **The Gap Survival**: 所有回调 K 线的 Low 从未击穿 Gap Floor。
3. **The Two-Leg Pullback (H2 核心逻辑)**:
   - **Leg 1 Down (第一次回调)**: 出现 LHLL (Lower High + Lower Low) K 线
   - **High 1 (中间恢复)**: 出现 HH (Higher High)，多头首次尝试恢复
   - **Leg 2 Down (第二次回调)**: 再次出现 LHLL — 空头第二次尝试反转
   - 两次尝试反转均失败 → 被困空头的止损盘成为多头燃料
4. **Entry**: Buy Stop 挂在第二次回调 LHLL K 线的最高点之上
5. **The Math**: TP = 2×Gap_Floor - Prior_Swing_Low

# 📊 市场微观结构与指标
{ctx['csv_str']}

# 🧪 结构系统探测器输出
{context_xml}

# 📝 审计报告 (XML)
<ANALYSIS>
- Breakout Quality: (突破的力度和清晰度?)
- Leg 1 Down: (第一次回调的深度和速度? 是否有序?)
- High 1: (恢复是否有力? 还是虚弱的内包线?)
- Leg 2 Down: (第二次回调是否浅于第一次? 经典 H2 特征)
- Gap Integrity: (Gap Floor 是否被严格尊重?)
</ANALYSIS>
<PA_TAGS>结构性跳空, High 2两腿回调, 空头陷阱, 阻力转支撑</PA_TAGS>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>审计结论</DISCORD>
"""

    def parse_result(self, response_text: str) -> Dict:
        from core.formatter import parse_response
        parsed = parse_response(response_text)

        tags_match = re.search(r"<PA_TAGS>(.*?)</PA_TAGS>", response_text, re.DOTALL | re.IGNORECASE)
        if tags_match:
            tags = tags_match.group(1).strip()
            parsed['pa_tags'] = tags
            parsed['reason'] = tags

        return parsed
