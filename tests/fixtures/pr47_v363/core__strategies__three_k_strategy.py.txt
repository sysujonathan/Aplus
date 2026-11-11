
# core/strategies/three_k_strategy.py
"""
[Strategy] 3K Momentum Strategy (Consecutive 3 Bullish Bars)
Original logic from Brooks-AI Strategy Factory.
This strategy identifies strong bullish micro-channels with gap-up characteristics.
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
from core.rating_core import factor, rr_factor, sum_weights

# Al Brooks PA 概念标注:
# - Breakout Gap (突破缺口): 3K中相邻K线之间的跳空, Low(i) >= High(i-1)
# - Measured Gap (测量缺口): 突破缺口在回调中未被回补, 趋势至少再延续等距

logger = logging.getLogger(__name__)

class ThreeKStrategy(BaseStrategy):
    """
    3K Strategy: Identifies 3 consecutive bullish bars with strong momentum.
    Includes:
    - Micro Channel confirmation
    - Gap-up urgency check
    - Anti-climax filter
    - Bull trap detection
    """

    @property
    def name(self) -> str:
        return "STRATEGY_3K"

    @property
    def description(self) -> str:
        return "Consecutive 3 Bullish Bars + Gap/Trap Logic (Isolated)"

    @property
    def signal_column(self) -> str:
        return 'signal_3k_gap_test'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """3K 策略元数据声明"""
        return {
            'display_name': '3K',
            'sl_column': 'sl_3k_gap_test',
            'entry_column': 'entry_3k_gap_test',
            'tp_columns': ['tp_3k_gap_test'],
            'score_column': '',
            'signal_column': 'signal_3k_gap_test',
            # 🔴 3K 只声明日线。周线扫描(桌面端 + 命令行)只跑 3 个 gap 家族策略,
            #    3K 不参与 —— 用户 2026-08-30 拍板: "我周线都没有考虑过非 gap 策略"。
            #    注: scan_engine.scan_weekly_3k_signals 这条扫描能力仍在(研究用), 但不再
            #    通过注册表自动进入任何周线扫描入口; 命令行亦不得再硬编码补入 3K。
            'supported_timeframes': ['daily'],
            'tp_multiplier': 1.0,
            'ai_audit': False,
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """3K 信号信息提取 — 包含缺口测试确认的额外信息"""
        result = super().get_signal_info(df)
        
        if df is None or df.empty:
            return result
        
        extra_info = result.get('extra_info', {})
        row = df.iloc[-1]
        
        # 尝试找最近的缺口测试确认信号
        gt_col = 'signal_3k_gap_test'
        if gt_col in df.columns:
            recent = df.tail(25)
            gt_rows = recent[recent[gt_col] == True]
            if not gt_rows.empty:
                gt_row = gt_rows.iloc[-1]
                gt_entry = gt_row.get('entry_3k_gap_test', np.nan)
                gt_sl = gt_row.get('sl_3k_gap_test', np.nan)
                gt_tp = gt_row.get('tp_3k_gap_test', np.nan)
                if not np.isnan(gt_entry) if isinstance(gt_entry, float) else gt_entry is not None:
                    gt_entry_f = float(gt_entry) if not np.isnan(gt_entry) else 0
                    gt_sl_f = float(gt_sl) if not np.isnan(gt_sl) else 0
                    gt_tp_f = float(gt_tp) if not np.isnan(gt_tp) else 0
                    extra_info['gap_test_entry'] = gt_entry_f
                    extra_info['gap_test_sl'] = gt_sl_f
                    extra_info['gap_test_tp'] = gt_tp_f
                    risk = gt_entry_f - gt_sl_f if gt_sl_f else 0
                    reward = gt_tp_f - gt_entry_f if gt_tp_f else 0
                    extra_info['gap_test_rr'] = round(reward / risk, 1) if risk > 0 else 0
        
        # 3K 评分 (简化版: 使用 location_pct)
        loc = row.get('location_pct', 0.5)
        extra_info['score'] = round((1 - loc) * 100, 1)  # 低位加分
        
        if extra_info:
            result['extra_info'] = extra_info
        
        return result

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'daily') -> Optional['RatingResult']:
        """[RATING_PLAN §4.5] 3K 评级: 最小手调集 (纯 PA, 已删违规 trend_align/EMA动量).
        因子: 信号K质量/缺口保持开放/陷阱过滤/非买入高潮/三连阳/RR≥2。
        """
        if df is None or df.empty:
            return None
        sig_col = 'signal_3k_gap_test'
        sig_pos = len(df) - 1
        sig_row = df.iloc[-1]
        if sig_col in df.columns and df[sig_col].fillna(False).any():
            sp = df.index[df[sig_col].fillna(False)]
            sig_pos = df.index.get_loc(sp[-1])
            sig_row = df.iloc[sig_pos]

        # 信号K质量 (Step 4)
        body_pct = float(sig_row.get('body_pct', 0) or 0)
        morph_ok = bool(sig_row.get('morph_ok', False))
        f_q = factor('信号K质量', round(body_pct, 3), morph_ok, 1.0 if morph_ok else 0.0,
                     sop_ref='SOP Step 4', note='实体饱满=强阳' if morph_ok else '形态不达标')

        # 缺口保持开放 (Step 6 Body Gap — Measured Gap 完整)
        gap_open = bool(sig_row.get('breakout_gap_open', False))
        f_gap = factor('缺口保持开放', 1.0 if gap_open else 0.0, gap_open, 2.0 if gap_open else 0.0,
                       sop_ref='SOP Step 6 Body Gap', note='Measured Gap完整=趋势延续' if gap_open else '缺口被补')

        # 陷阱过滤 (Step 8 FBO/Climax)
        trap_ok = bool(sig_row.get('trap_check_ok', True))
        f_trap = factor('陷阱过滤通过', 1.0 if trap_ok else 0.0, trap_ok, 2.0 if trap_ok else 0.0,
                        sop_ref='SOP Step 8 (FBO/Climax)', note='非多头陷阱' if trap_ok else 'Bull Trap风险')

        climax_ok = bool(sig_row.get('climax_ok', True))
        f_climax = factor('非买入高潮', 1.0 if climax_ok else 0.0, climax_ok, 1.0 if climax_ok else 0.0,
                          sop_ref='SOP Step 8 Climax', note='非 exhaustion move' if climax_ok else '买入高潮')

        # 三连阳动能 (Step 6/7)
        three_bulls = bool(sig_row.get('three_bulls', False))
        f_3b = factor('三连阳动能', 1.0 if three_bulls else 0.0, three_bulls, 1.0 if three_bulls else 0.0,
                      sop_ref='SOP Step 6/7', note='强动能微通道' if three_bulls else '动能不足')

        # RR (Step 9)
        entry = sig_row.get('entry_3k_gap_test', np.nan)
        sl = sig_row.get('sl_3k_gap_test', np.nan)
        tp = sig_row.get('tp_3k_gap_test', np.nan)
        rr = 0.0
        if pd.notna(entry) and pd.notna(sl) and pd.notna(tp) and entry > sl:
            rr = round((tp - entry) / (entry - sl), 1)
        f_rr = rr_factor(rr)

        factors = [f_q, f_gap, f_trap, f_climax, f_3b, f_rr]
        raw = sum_weights(factors)
        score = clamp(40 + 12 * raw)  # 占位归一, Phase 2 改逐策略切点
        toxic = raw <= -3
        letter = band_calibrated(cls, score, toxic=toxic, timeframe=timeframe)
        return RatingResult(raw_score=raw, score=score, letter=letter, factors=factors,
                            toxic=toxic, calibrated=is_calibration_available())

    def __init__(self):
        # Load parameters from settings
        self.BODY_PCT_A = getattr(settings, 'K3_BODY_PCT_A', 0.50)
        self.WICK_LONG_PCT = getattr(settings, 'K3_WICK_LONG_PCT', 0.33)
        self.WICK_SHORT_PCT = getattr(settings, 'K3_WICK_SHORT_PCT', 0.10)
        self.GAP_CONFIRM_WINDOW = getattr(settings, 'K3_GAP_CONFIRM_WINDOW', 5)
        self.GAP_TEST_MAX_WINDOW = getattr(settings, 'K3_GAP_TEST_MAX_WINDOW', 20)

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Isolated 3K strategy logic.
        """
        if len(df) < 60:
            df['signal_3k'] = False
            return df

        required = ['ema20', 'atr', 'body_pct', 'lower_wick_pct', 'upper_wick_pct', 'close_loc', 'is_bullish']
        if not all(col in df.columns for col in required):
            logger.warning(f"3K Strategy missing columns: {[c for c in required if c not in df.columns]}")
            df['signal_3k'] = False
            return df

        # 1. Momentum: 3 Consecutive Bullish Bars with Close in upper half
        is_bull_high = df['is_bullish'] & (df['close_loc'] >= 0.5)
        df['three_bulls'] = is_bull_high & is_bull_high.shift(1) & is_bull_high.shift(2)

        # 2. [V2.5 Bugfix] Urgency: 放宽缺口条件 (Al Brooks Breakout Gap — Relaxed)
        #    a. 递增高低点 → 已由 extremes_ok 保证
        #    b. K3_Low > K1_High → K3 和 K1 之间存在缺口空间
        #    c. K2/K3 开盘价 >= 前一根收盘价 → 引入 1e-3 容差对抗复权浮点误差
        df['gap_ok'] = (
            (df['low'] > df['high'].shift(2)) &             
            (df['open'].shift(1) >= df['close'].shift(2) - 1e-3) &  
            (df['open'] >= df['close'].shift(1) - 1e-3)              
        )

        # 3. Structure: Increasing Highs and Lows (Micro-Channel)
        df['extremes_ok'] = (
            (df['high'] > df['high'].shift(1)) & (df['high'].shift(1) > df['high'].shift(2)) &
            (df['low'] > df['low'].shift(1)) & (df['low'].shift(1) > df['low'].shift(2))
        )

        # 4. Filter: Anti-Climax (Don't buy at the peak of an exhaustion move)
        range_val = df['high'] - df['low']
        # [V2.5 Bugfix] 增加 min_periods=1 防止新股因为拿不到 20 天数据被掐头全灭
        max_past_range = range_val.rolling(window=20, min_periods=1).max().shift(3)
        df['climax_ok'] = range_val <= max_past_range

        # 5. Morphology: Bar Quality
        def check_morph(suffix=0):
            d = df if suffix == 0 else df.shift(suffix)
            cond_a = d['body_pct'] >= self.BODY_PCT_A
            cond_b = (d['lower_wick_pct'] >= self.WICK_LONG_PCT) & (d['upper_wick_pct'] <= self.WICK_SHORT_PCT)
            return cond_a | cond_b

        df['morph_ok'] = check_morph(0) & check_morph(1) & check_morph(2)

        # 6. Context: Distance from EMA20 (Breakout space)
        dist_ema = (df['close'] - df['ema20']).abs()
        df['env_ok'] = dist_ema >= (0.2 * df['atr'])

        # 7. Trap/Surprise: Protection against Bull Traps
        highest_60 = df['high'].rolling(min_periods=1, window=60).max().shift(1)
        lowest_60 = df['low'].rolling(min_periods=1, window=60).min().shift(1)
        is_breakout = df['close'] > highest_60
        
        # Surprise: very large 3-bar height (Aggressive Trend)
        pattern_height = df['high'] - df['low'].shift(2)
        is_surprise = (pattern_height > 3 * df['atr']) & is_breakout
        
        # Trap: near highs but showing weakness
        range_60 = highest_60 - lowest_60
        trap_threshold = lowest_60 + 0.75 * range_60
        is_weak_bar = df['upper_wick_pct'] > df['body_pct']
        is_trap = (df['close'] > trap_threshold) & (~is_breakout) & is_weak_bar
        
        # 8. Location in Range (20-bar) for Trader's Equation
        h20 = df['high'].rolling(min_periods=1, window=20).max()
        l20 = df['low'].rolling(min_periods=1, window=20).min()
        df['location_pct'] = (df['close'] - l20) / (h20 - l20 + 1e-9)
        
        df['trap_check_ok'] = is_surprise | (~is_trap)

        # Final Signal Integration
        # ===== Step 2: 前期波段高/低点 (Al Brooks: Prior Swing High/Low) =====
        SWING_LOOKBACK = getattr(settings, 'K3_SWING_LOOKBACK', 40)
        # 前期波段高点: K1之前 SWING_LOOKBACK 根K线内的最高点
        # [V2.5 Bugfix] 添加 min_periods=1 保证最少也有波段最高最低
        _prior_swing_high_raw = df['high'].rolling(window=SWING_LOOKBACK, min_periods=1).max().shift(3)
        # 前期波段低点: K1之前 SWING_LOOKBACK 根K线内的最低点 (测量目标的起点)
        _prior_swing_low_raw = df['low'].rolling(window=SWING_LOOKBACK, min_periods=1).min().shift(3)

        # ===== Step 1 + Step 3: 识别强劲3K + K3低点 > 突破点 =====
        # [V2.4] 放宽突破点(Gap Floor) = max(K1_High, Prior Swing High)
        # 根据用户要求降低标准：不再强制 K3_Low 必须大于 K2_High，只需满足 K3_Low > K1_High 及趋势过滤即可
        _k1_high_raw = df['high'].shift(2)  # K1高点
        _gap_floor_raw = np.maximum(_k1_high_raw, _prior_swing_high_raw)
        
        df['signal_3k'] = (
            df['three_bulls'] & 
            df['gap_ok'] & 
            df['extremes_ok'] & 
            df['climax_ok'] & 
            df['morph_ok'] & 
            df['env_ok'] & 
            df['trap_check_ok'] &
            (df['low'] > _gap_floor_raw)  # Step 3: K3_Low > max(K1H, K2H, PSH)
        )
        
        # [V2.2] SL = gap_floor (缺口被补 = 突破失败 → 止损出场)
        df['sl_3k'] = np.where(df['signal_3k'], _gap_floor_raw, np.nan)

        # ===== Step 4: 突破缺口 → 测量缺口 后验确认 =====
        # K1 高点 (3K形态中第一根阳线的高点)
        _k1_high = np.where(df['signal_3k'], df['high'].shift(2), np.nan)
        _k1_high = pd.Series(_k1_high, index=df.index).ffill()

        # K2 高点 (3K形态中第二根阳线的高点)
        _k2_high = np.where(df['signal_3k'], df['high'].shift(1), np.nan)
        _k2_high = pd.Series(_k2_high, index=df.index).ffill()

        # K3 低点 (3K形态中第三根阳线的低点, 即缺口顶部)
        _k3_low = np.where(df['signal_3k'], df['low'], np.nan)
        _k3_low = pd.Series(_k3_low, index=df.index).ffill()

        # K3 高点
        _k3_high = np.where(df['signal_3k'], df['high'], np.nan)
        _k3_high = pd.Series(_k3_high, index=df.index).ffill()

        # 前期波段高/低点 (锚定到3K信号位置并前向填充)
        _prior_swing_high = np.where(df['signal_3k'], _prior_swing_high_raw, np.nan)
        _prior_swing_high = pd.Series(_prior_swing_high, index=df.index).ffill()
        _prior_swing_low = np.where(df['signal_3k'], _prior_swing_low_raw, np.nan)
        _prior_swing_low = pd.Series(_prior_swing_low, index=df.index).ffill()

        # [V2.4] 放宽后缺口测试底线 = max(K1_High, prior_swing_high)
        # 回调不能跌破 K1 顶部及该波段的起涨抗性线, 否则缺口被回补
        _gap_floor = np.maximum(_k1_high, _prior_swing_high)

        # 1. 距离上次3K信号的K线数 (通过cumsum分组)
        _bars_since_3k = df['signal_3k'].cumsum()
        _bar_count = df.groupby(_bars_since_3k).cumcount()

        # 提取分组内的累积最低点
        _group_min_low = df['low'].groupby(_bars_since_3k).expanding().min().droplevel(0)

        # [V2.5 Bugfix] 突破缺口保持开放: 本次波段内历史以来的最低点始终保存在 gap_floor 之上
        df['breakout_gap_open'] = _group_min_low > _gap_floor

        # 测量缺口目标价:
        # [V2.2] Al Brooks Measured Gap: 缺口是整个波段移动的中点
        # 1. 缺口中点 = (gap_floor + K3_Low) / 2  (突破点到K3低之间)
        # 2. 移动起点 = 前期波段低点 (swing_low_before)
        # 3. 目标 = 缺口中点 + (缺口中点 - 波段低点) = 2 * 缺口中点 - 波段低点
        _gap_midpoint = (_gap_floor + _k3_low) / 2
        df['measured_gap_target'] = np.where(
            df['breakout_gap_open'],
            2 * _gap_midpoint - _prior_swing_low,
            np.nan
        )

        # ===== 缺口测试确认信号 (Al Brooks: Gap Test → Buy Stop Order) =====
        # 3K信号后, 回调测试K1高点缺口 → 缺口保持开放 + 阳线反转 → 触发 Buy Stop

        # 2. 在监控窗口内 (3~MAX_WINDOW 根K线, 给回调留出时间)
        GAP_TEST_MIN = 3
        in_window = (_bar_count >= GAP_TEST_MIN) & (_bar_count <= self.GAP_TEST_MAX_WINDOW)
        # 必须在有3K信号的分组中 (排除3K信号前的数据)
        in_window = in_window & (_bars_since_3k > 0)

        # 3. [V2.5 Bugfix] 缺口从未关闭: 该组积累最低点 > gap_floor（防止单K线起死回生）
        gap_still_open = df['breakout_gap_open']

        # 4. 回调后反转确认:
        #   a. 当前K线是阳线
        #   b. 当前K线 Close > 前一根K线 High (向上突破, Al Brooks: 强Follow-through)
        #   c. 前一根K线 Low 接近该组内(3K信号后)的最低点 (波段低点附近, ±1 ATR 容差)
        #   注意: 必须用分组expanding.min(), 不能用全局rolling, 否则会包含3K前更低的价格
        _group_min_low = df['low'].groupby(_bars_since_3k).expanding().min().droplevel(0)
        is_near_swing_low = df['low'].shift(1) <= (_group_min_low.shift(1) + 1.0 * df['atr'])
        reversal_confirm = (
            df['is_bullish'] &
            (df['close'] > df['high'].shift(1)) &
            is_near_swing_low
        )

        # 5. [V2.1 Climax Filter] MM 不能在缺口测试之前就已达到
        # Al Brooks: 3K后不经回调直达MM = Buy Climax (买入高潮), 此后入场盈亏比极差
        # 逻辑: 分组内 expanding max 追踪3K后累计最高价, 若已 >= MM目标 → 过滤
        _group_max_high = df['high'].groupby(_bars_since_3k).expanding().max().droplevel(0)
        # 无条件计算 MM 目标 (不依赖 breakout_gap_open, 因为3K当天 breakout_gap_open 通常为 False)
        # _gap_midpoint 和 _prior_swing_low 均已在3K信号位置锚定并 ffill, 直接可用
        _mm_target_unconditional = 2 * _gap_midpoint - _prior_swing_low
        # shift(1): 判断"截至前一根K线"是否已达到MM, 避免当前K线自参照
        _mm_not_reached = (_group_max_high.shift(1) < _mm_target_unconditional) | _mm_target_unconditional.isna()
        _mm_not_reached = _mm_not_reached.fillna(True)  # 安全兜底 (3K前无目标)

        # 6. 组合条件 (含高潮过滤)
        _gap_test_raw = reversal_confirm & in_window & gap_still_open & _mm_not_reached

        # 7. 去重: 每个3K信号只触发一次确认 (取首次)
        _already_confirmed = _gap_test_raw.groupby(_bars_since_3k).cumsum().shift(1).fillna(0) > 0
        df['signal_3k_gap_test'] = _gap_test_raw & ~_already_confirmed

        # Buy Stop Entry = 触发缺口测试确认（反转信号）当天的 High
        # [V2.2] SL = gap_floor (缺口被补 = 止损), 而非测试K线的Low
        # Al Brooks: 当日大阳反转确认支撑有效，次日若突破今日高点则加仓跟进
        df['entry_3k_gap_test'] = np.where(df['signal_3k_gap_test'], df['high'], np.nan)
        df['sl_3k_gap_test'] = np.where(df['signal_3k_gap_test'], _gap_floor, np.nan)
        # TP = 测量缺口目标价 (已计算的 measured_gap_target)
        df['tp_3k_gap_test'] = np.where(
            df['signal_3k_gap_test'],
            df['measured_gap_target'],
            np.nan
        )

        return df

    def _calculate_context(self, df: pd.DataFrame) -> str:
        try:
            latest = df.iloc[-1]
            loc_pct = latest.get('location_pct', 0.5)
            # 💡 确定阿布概率基准
            if loc_pct <= 0.3: context_pos = "BOTTOM (Prob High: 65%)"
            elif loc_pct >= 0.7: context_pos = "TOP (Prob Low: 35%)"
            else: context_pos = "MIDDLE (Prob Med: 50%)"

            # 突破缺口状态
            gap_status = "OPEN ✅ (→ Measured Gap)" if latest.get('breakout_gap_open', False) else "PENDING"
            mg_target = latest.get('measured_gap_target', np.nan)
            mg_str = f"{mg_target:.2f}" if not np.isnan(mg_target) else "N/A"

            # 缺口测试确认状态
            gap_test = latest.get('signal_3k_gap_test', False)
            entry_gt = latest.get('entry_3k_gap_test', np.nan)
            sl_gt = latest.get('sl_3k_gap_test', np.nan)
            if gap_test and not np.isnan(entry_gt):
                gt_status = f"CONFIRMED ✅ Buy Stop={entry_gt:.2f} SL={sl_gt:.2f}"
            else:
                gt_status = "MONITORING"
            
            return f"""
<3K_CONTEXT>
  <LOCATION_PCT>{loc_pct:.2f}</LOCATION_PCT>
  <POSITION>{context_pos}</POSITION>
  <MOMENTUM>{"YES" if latest.get('three_bulls', False) else "NO"}</MOMENTUM>
  <GAPS>{"OK" if latest.get('gap_ok', False) else "FAIL"}</GAPS>
  <BREAKOUT_GAP>{gap_status}</BREAKOUT_GAP>
  <MEASURED_GAP_TARGET>{mg_str}</MEASURED_GAP_TARGET>
  <GAP_TEST>{gt_status}</GAP_TEST>
  <TRAP_CHECK>{"SAFE" if latest.get('trap_check_ok', False) else "DANGER"}</TRAP_CHECK>
</3K_CONTEXT>
"""
        except Exception:            return "<3K_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)
        latest = df.iloc[-1]
        
        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)
您现在是 **Al Brooks** 本人。您只关心 K 线图上的价格行为（Price Action），**严禁使用“量价背离”、“MACD”等非 PA 术语**。
您的任务是用您的“机构交易员视角”严格审计这个 3K Setup（三连阳）。

# 🕵️ Al Brooks 审计核心 (The Brooks Framework)
1. **Context (背景)**: 这 3 根阳线出现在哪里？
   - 如果是 **Trading Range (震荡区间)** 顶部：这更有可能是 **Buy Climax (买入高潮)** 或 **Bull Trap (多头陷阱)**，哪怕阳线再大，也应该由 Limit Order 卖出，而不是追涨。
   - 如果是 **Trend (趋势)** 中继：我们需要看到 **Follow-through (动能延续)**。如果第 4 根 K 线是 Doji 或 Bear Bar，说明多头在犹豫，胜率会骤降。
2. **Setup Quality (形态质量)**:
   - 阳线实体是否足够大？影线是否足够小？
   - 是否有 **Gaps (缺口)**？Low(i) > High(i-1) 是**真实跳空缺口**，代表极强的 **Urgency (紧迫感)**。
   - 重叠度 (Overlap) 如何？如果 3 根 K 线大量重叠，那是 **Barbwire (铁丝网)** 震荡，不是趋势。
3. **Breakout Gap → Measured Gap (突破缺口 → 测量缺口)**:
   - 3K之后的回调如果**没有回补K1高点的缺口**，则该缺口升级为 **Measured Gap (测量缺口)**。
   - 测量缺口意味着趋势至少会再延续等距距离，目标价可参考 MEASURED_GAP_TARGET。
   - 如果缺口被回补，则突破失败，信号应被降级。
4. **Probability (概率)**:
   - 只有在 **Strong Bull Breakout (强多头突破)** 时，胜率才 > 60%。
   - 大多数时候，胜率只有 40%-50%，此时必须要求 **Reward:Risk >= 2:1**。

# 📊 市场微观结构 (最近 20 天)
{ctx['csv_str']}

# 🧪 3K 物理参数
{context_xml}

# 📝 Al Brooks 决策报告 (XML)
请用 Al Brooks 的口吻（冷静、客观、以通过率和盈亏比为核心）撰写：
<ANALYSIS>
- Context Analysis: (Is it a TR, TTR, or Trend? Where in the range?)
- Bar-by-Bar Analysis: (Comment on bodies, tails, and overlap. Do not mention Volume.)
- Math (Trader's Equation): (Is P * R > Risk?)
</ANALYSIS>
<PA_TAGS>必需使用中文 Brooks 术语，例如：买入高潮, 强趋势突破, 二次探测失败, 铁丝网震荡</PA_TAGS>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>审计结论（如：高潮衰竭，Limit Sell）</DISCORD>

<PLAN>
入口: {df.iloc[-1]['close']:.2f} | 止损: {latest.get('sl_3k', 0):.2f} | 目标: (Based on Measured Move)
</PLAN>
"""

    def parse_result(self, response_text: str) -> Dict:
        """
        [Parser] 增加对 PA_TAGS 的提取，实现微信推送信息的极致精简
        """
        from core.formatter import parse_response
        parsed = parse_response(response_text)
        
        # 🟢 如果有精简标签，优先使用
        tags_match = re.search(r"<PA_TAGS>(.*?)</PA_TAGS>", response_text, re.DOTALL | re.IGNORECASE)
        if tags_match:
            tags = tags_match.group(1).strip()
            parsed['pa_tags'] = tags
            parsed['reason'] = tags # 替换为精简版本
            
        return parsed
