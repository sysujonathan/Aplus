# core/strategies/gap_pinbar_strategy.py
"""
[Strategy] Gap + Pinbar 策略 (独立新增)

理论基础：Al Brooks Price Action — Structural Measuring Gap
核心信号：突破缺口回调期间，首次刺破 EMA20 的 Pinbar 形态

与原有 structural_gap_strategy 的区别：
  1. 信号条件：统一为 Pinbar + 首次刺破 EMA20（原版为反转阳线突破）
  2. TP 公式：TP = 2 * Gap_Floor - Prior_Swing_Low（原版使用缺口中线）
  3. 完整保留三条生命周期过滤规则

回测验证结果：
  - 周线 EV = +0.5077 R/单 (15.2 年, 3308 只标的)
  - 日线 EV = +0.0105 R/单 (3.4 年, 3308 只标的)
"""

import pandas as pd
import numpy as np
import logging
import re
from typing import Dict, Any, Optional
from .base import BaseStrategy
from core.formatter import get_common_context
from config import settings

logger = logging.getLogger(__name__)


class GapPinbarStrategy(BaseStrategy):
    """
    Gap + Pinbar + 首次刺破 EMA20 策略

    信号逻辑（向量化）：
      1. 结构性突破：HH + HL，且 Low > 60 根 K 线最高点 (Gap Floor)
      2. 缺口存活：回调期内 min(Low) > Gap Floor
      3. Pinbar：下影线 >= 40%，收盘位置 >= 50%
      4. 首次刺破 EMA20：回调期内第一次 Low <= EMA20，且 Close > EMA20
      5. 高潮规避：回调期 max(High) < TP
      6. 去重：每次突破仅取首次信号
    """

    @property
    def name(self) -> str:
        return "STRATEGY_GAP_PINBAR"

    @property
    def description(self) -> str:
        return "Gap + Pinbar + EMA20 Pierce (Measuring Gap Reversal)"

    @property
    def signal_column(self) -> str:
        return 'signal_gap_pinbar'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """Gap Pinbar 策略元数据声明"""
        return {
            'display_name': 'GAP PINBAR',
            'sl_column': 'sl_gap_pinbar',
            'entry_column': 'entry_gap_pinbar',
            'tp_columns': ['tp_gap_pinbar'],
            'score_column': 'sig_bar_quality_gp',
            'signal_column': 'signal_gap_pinbar',
            'supported_timeframes': ['daily', 'weekly'],
            'tp_multiplier': 2.0,
            'ai_audit': False,
            'bars_since_breakout_column': 'bars_since_breakout_gp',
            'gap_top_exact_column': 'gap_pinbar_top_exact',
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """Gap Pinbar 信号信息提取 — 包含信号质量"""
        result = super().get_signal_info(df)
        
        if df is None or df.empty:
            return result
        
        extra_info = result.get('extra_info', {})
        row = df.iloc[-1]
        
        q = row.get('sig_bar_quality_gp', 0)
        extra_info['sig_quality'] = q
        
        if extra_info:
            result['extra_info'] = extra_info
        
        return result


    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> int:
        """Gap Pinbar 图表标注"""
        from core.strategies.structural_gap_strategy import _annotate_gap_strategy
        return _annotate_gap_strategy(ax, plot_df, strategy_type, **kwargs)

    def __init__(self):
        # 突破判定窗口 (日线 ~3 个月, 周线 ~1.2 年)
        self.LOOKBACK_WINDOW = getattr(settings, 'STRUCT_GAP_LOOKBACK', 60)
        # 回调确认最大跟踪窗口
        self.MAX_PULLBACK_WINDOW = getattr(settings, 'STRUCT_GAP_MAX_WINDOW', 40)
        # 最小回调周期
        self.MIN_PULLBACK_WINDOW = 2
        # Pinbar 参数
        self.PINBAR_LOWER_WICK_MIN = 0.40
        self.PINBAR_CLOSE_LOC_MIN = 0.50

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        向量化计算 Gap + Pinbar + 首次刺破 EMA20 信号。
        所有条件严格遵循 Al Brooks Price Action 理论。
        """
        if len(df) < self.LOOKBACK_WINDOW + 5:
            df['signal_gap_pinbar'] = False
            return df

        # 确保基础指标存在
        required = ['atr', 'ema20']
        if not all(col in df.columns for col in required):
            logger.warning(f"Gap Pinbar Strategy 缺少列: {[c for c in required if c not in df.columns]}")
            df['signal_gap_pinbar'] = False
            return df

        # ==============================================================================
        # 第一步：识别结构性突破 (Breakout Detection)
        # ==============================================================================
        # 微观：Higher High + Higher Low
        is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))

        # 宏观：Gap Floor = 过去 N 根 K 线的最高高点
        # shift(2) 因为从突破 K 线前一天(K-1)为止的过去 N 根
        _gap_floor_raw = df['high'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).max().shift(2)

        # 突破认定：Low > Gap Floor (含 1e-3 浮点容差)
        df['is_breakout_gp'] = is_hh_hl & (df['low'] > _gap_floor_raw - 1e-3)

        # ==============================================================================
        # 第二步：锚定历史数据 (Anchor Swing Extremes)
        # ==============================================================================
        _prior_swing_low_raw = df['low'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).min().shift(2)

        # 仅在突破时刻锁定，向后 ffill
        _gap_floor = np.where(df['is_breakout_gp'], _gap_floor_raw, np.nan)
        _gap_floor = pd.Series(_gap_floor, index=df.index).ffill()

        _prior_swing_low = np.where(df['is_breakout_gp'], _prior_swing_low_raw, np.nan)
        _prior_swing_low = pd.Series(_prior_swing_low, index=df.index).ffill()

        # ==============================================================================
        # 第三步：缺口存活监控 (Gap Survival)
        # ==============================================================================
        _bars_since_breakout = df['is_breakout_gp'].cumsum()
        _bar_count = df.groupby(_bars_since_breakout).cumcount()

        _group_min_low = df['low'].groupby(_bars_since_breakout).expanding().min().droplevel(0)
        # 护城河铁律：回调期内最低点不得击穿 Gap Floor
        df['gap_pinbar_open'] = _group_min_low > (_gap_floor - 1e-3)

        # ==============================================================================
        # 第四步：回调首次刺破 EMA20 的 Pinbar 确认 (Signal Bar Confirmation)
        # ==============================================================================

        # 4.1 回调时间窗口
        in_window = (_bar_count >= self.MIN_PULLBACK_WINDOW) & (_bar_count <= self.MAX_PULLBACK_WINDOW)
        in_window = in_window & (_bars_since_breakout > 0)

        # 4.2 Pinbar 形态判定
        _bar_range = df['high'] - df['low']
        _safe_range = _bar_range.replace(0, np.nan)

        # 信号 K 线收盘位置质量 (CLV)
        _sig_quality = (df['close'] - df['low']) / _safe_range
        df['sig_bar_quality_gp'] = _sig_quality.round(3)

        # Pinbar 条件：下影线占比 >= 40%，收盘位置 >= 50%
        _lower_wick = df[['open', 'close']].min(axis=1) - df['low']
        _lower_wick_ratio = _lower_wick / _safe_range
        is_pinbar = (_lower_wick_ratio >= self.PINBAR_LOWER_WICK_MIN) & (_sig_quality >= self.PINBAR_CLOSE_LOC_MIN)

        # 4.3 首次刺破 EMA20 均线判定
        is_pierced_today = df['low'] <= df['ema20']
        # 分组统计截至前一天为止的累计刺破次数，以锁定"首次"
        _pierce_count_prev = is_pierced_today.groupby(_bars_since_breakout).cumsum().shift(1).fillna(0)
        is_first_pierce = (_pierce_count_prev == 0) & is_pierced_today

        # 4.4 组装信号条件
        _reversal_confirm_raw = (
            in_window &
            df['gap_pinbar_open'] &
            is_pinbar &
            is_first_pierce &
            (df['close'] > df['ema20'])  # 收盘站回 EMA20 上方
        )

        # ==============================================================================
        # [高潮规避器] TP = 2 * Gap_Floor - Prior_Swing_Low (起涨区间上翻)
        # ==============================================================================
        _target_uncond = 2 * _gap_floor - _prior_swing_low

        _group_max_high = df['high'].groupby(_bars_since_breakout).expanding().max().droplevel(0)
        _mm_not_reached = (_group_max_high < _target_uncond) | _target_uncond.isna()
        _mm_not_reached = _mm_not_reached.fillna(True)

        _signal_raw = _reversal_confirm_raw & _mm_not_reached

        # 去重：每次突破仅取首次信号
        _already_confirmed = _signal_raw.groupby(_bars_since_breakout).cumsum().shift(1).fillna(0) > 0
        df['signal_gap_pinbar'] = _signal_raw & ~_already_confirmed

        # 回测/分析用数据
        df['bars_since_breakout_gp'] = _bars_since_breakout

        # ==============================================================================
        # 第五步：定单参数生成 (Order Parameters)
        # ==============================================================================
        # SL = Gap Floor (阻力转支撑)
        df['sl_gap_pinbar'] = np.where(df['signal_gap_pinbar'], _gap_floor, np.nan)

        # Entry = 信号 K 线最高点 (次日挂 Buy Stop)
        df['entry_gap_pinbar'] = np.where(df['signal_gap_pinbar'], df['high'], np.nan)

        # TP = 2 * Gap_Floor - Prior_Swing_Low (起涨区间上翻)
        df['tp_gap_pinbar'] = np.where(df['signal_gap_pinbar'], _target_uncond, np.nan)

        # 关键结构锚点 (绘图/通知使用)
        df['gap_pinbar_prior_low'] = np.where(df['signal_gap_pinbar'], _prior_swing_low, np.nan)
        df['gap_pinbar_floor_exact'] = np.where(df['signal_gap_pinbar'], _gap_floor, np.nan)

        # 缺口上沿
        df['gap_pinbar_top_exact'] = np.where(
            df['signal_gap_pinbar'], _group_min_low.shift(1), np.nan
        )

        # 时间坐标 (绘图用)
        try:
            df['gap_pinbar_prior_low_date'] = df['low'].rolling(
                window=self.LOOKBACK_WINDOW, min_periods=1).idxmin().shift(2)
            df['gap_pinbar_floor_date'] = df['high'].rolling(
                window=self.LOOKBACK_WINDOW, min_periods=1).idxmax().shift(2)
        except AttributeError:
            pass

        df['gap_pinbar_test_date'] = df.index.to_series().shift(1)

        return df

    def _calculate_context(self, df: pd.DataFrame) -> str:
        """为 AI 审计提供结构上下文"""
        try:
            latest = df.iloc[-1]
            gap_status = "OPEN (Structurally Protected)" if latest.get('gap_pinbar_open', False) else "CLOSED / COMPROMISED"

            sig = latest.get('signal_gap_pinbar', False)
            entry = latest.get('entry_gap_pinbar', np.nan)
            sl = latest.get('sl_gap_pinbar', np.nan)
            tp = latest.get('tp_gap_pinbar', np.nan)

            if sig and not np.isnan(entry):
                status_str = f"LOCKED ✅ Buy Stop={entry:.2f} | Floor(SL)={sl:.2f} | Proj(TP)={tp:.2f}"
            else:
                status_str = "MONITORING THE PULLBACK"

            return f"""
<GAP_PINBAR_CONTEXT>
  <MACRO_WINDOW>{self.LOOKBACK_WINDOW} Bars</MACRO_WINDOW>
  <BREAKOUT_DEFENSIVE_FLOOR>{gap_status}</BREAKOUT_DEFENSIVE_FLOOR>
  <SIGNAL_TYPE>Pinbar + First EMA20 Pierce</SIGNAL_TYPE>
  <SETUP_STATUS>{status_str}</SETUP_STATUS>
</GAP_PINBAR_CONTEXT>
"""
        except Exception:            return "<GAP_PINBAR_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)

        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)

您正在审计【Gap + Pinbar 结构性缺口策略】的买入信号。

# 🕵️ Brooks Framework For Gap + Pinbar
1. **The Breakout (脱离性质)**: 股价跨越了 {self.LOOKBACK_WINDOW} 个周期的最高点，代表供需极端失衡。
2. **The Surviving Pullback (幸存的回撤)**: 回撤底部从未触及 Gap Floor，缺口完全无菌。
3. **The Pinbar Signal (Pinbar 反转信号)**:
   - 回调过程中**首次刺破 EMA20**均线
   - K 线形态为 Pinbar（下影线 ≥ 40%，收盘位置 ≥ 50%）
   - **收盘站回 EMA20 上方** — 多头拒绝进一步回调的强烈信号
4. **The Math (算法倍率)**: TP = 2 × Gap_Floor - Prior_Swing_Low (起涨区间以 Gap Floor 为支点上翻)

# 📊 市场微观结构与指标
{ctx['csv_str']}

# 🧪 结构系统探测器输出
{context_xml}

# 📝 审计报告 (XML)
请严格审视 Pinbar 信号 K 线的质量：
<ANALYSIS>
- Breakout Validation: (How convincing was the initial structural break?)
- Pullback Action: (Did it probe smoothly or violently? First EMA20 touch?)
- Pinbar Quality: (Lower wick ratio? Close location? Is it a genuine rejection?)
- Gap Integrity: (Has the gap floor been truly respected by all bars?)
</ANALYSIS>
<PA_TAGS>结构性跳空, Pinbar反转, 首次刺破EMA20, 阻力转支撑确认</PA_TAGS>
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
