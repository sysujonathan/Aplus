# core/strategies/monthly_range_break_strategy.py
"""
[Strategy] 月线区间底部破位 Pinbar（Spring / 弹簧线）策略

理论基础：Al Brooks Price Action —— 交易区间下沿的"假跌破 + 长下影买回"形态（Spring）。
即：价格盘中刺破一段区间的平台支撑位（Base_Low），但收盘被限价多头拉回区间内，
且收阳、带极长下影线，是空头陷阱（bear trap）的典型 PA 签名。

信号条件（严格照搬用户给出的 5 条量化定义，纯 PA —— 只用 OHLC，无任何成交量/指标因子）：
  1. Base_Low = LLV(LOW, N)        取最近 N 个月月线最低价的最低值（平台支撑位）
  2. 盘中跌破：LOW < Base_Low
  3. 收盘收回：CLOSE > Base_Low     （确认收回到箱体内部，而非停在下方）
  4. 阳线实体：CLOSE > OPEN
  5. 长下影 Pinbar：下影线(Min(O,C)-LOW) > 实体×2 且 > 上影线×2，且下影线/全根振幅 ≥ 60%

数据来源：core.data_provider.get_monthly_bars(symbol) —— 本地 daily_bars(qfq) 内存聚合为月线，
完全离线、不改 schema。

说明：本策略只负责"识别信号 K 线"（触发条件）。交易管理（入场/止损/目标）采用纯 PA 参考位：
  - 入场 entry = 信号 K 最高价（次日挂 Buy Stop 突破信号 K 高点）
  - 止损 sl    = 信号 K 最低价（弹簧最低点下方）
  - 目标 tp    = 收 + (收 - 低)（把向下刺破深度向上 1:1 投射，纯几何测量）
这些仅为参考位，非权威交易建议。
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional

from .base import BaseStrategy
from core.formatter import get_common_context
from config import settings
from core.rating import RatingResult, clamp, band_calibrated, is_calibration_available
from core.rating_core import factor, sum_weights

logger = logging.getLogger(__name__)


class MonthlyRangeBreakStrategy(BaseStrategy):
    """
    月线区间底部破位 Pinbar（Spring）策略。
    向量化扫描月线序列，逐月套 5 条件；base_low 用 LLV(LOW, N) 滚动计算。
    """

    # ---- 可调参数（与 westock 验证版 scan.js 默认一致）----
    BASE_LOOKBACK = getattr(settings, 'MRB_BASE_LOOKBACK', 20)      # Base_Low = LLV(LOW, N)
    SHADOW_BODY_MIN = getattr(settings, 'MRB_SHADOW_BODY_MIN', 2)   # 下影 > 实体 × N
    SHADOW_UPPER_MIN = getattr(settings, 'MRB_SHADOW_UPPER_MIN', 2) # 下影 > 上影 × N
    SHADOW_RANGE_PCT_MIN = getattr(settings, 'MRB_SHADOW_RANGE_PCT_MIN', 60)  # 下影/振幅 ≥ N%

    @property
    def name(self) -> str:
        return "STRATEGY_MONTHLY_RANGE_BREAK"

    @property
    def description(self) -> str:
        return "Monthly Range-Bottom Spring Pinbar (Base_Low breakdown + long lower wick recovery)"

    @property
    def signal_column(self) -> str:
        return 'signal_mrb'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """月线区间破位 Pinbar 策略元数据声明"""
        return {
            'display_name': '月线区间破位Pinbar',
            'sl_column': 'sl_mrb',
            'entry_column': 'entry_mrb',
            'tp_columns': ['tp_mrb'],
            'score_column': '',
            'signal_column': 'signal_mrb',
            # 本系统当前流水线只编排 daily/weekly；monthly 为第三周期，
            # 接入 scan_engine/gui 时需另开 'monthly' 分支（见实现路径 Phase 3-5）。
            'supported_timeframes': ['monthly'],
            'ai_audit': False,            # 5 条件为纯结构/动能，跳 AI 直接入池（同 GAP PINBAR）
            'bars_since_breakout_column': '',
            'gap_top_exact_column': '',
            'note': '信号K识别用纯PA；entry/sl/tp为PA参考位非权威建议',
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """提取信号信息：SL/Entry/TP + PA 签名（下影比/破位深/收回幅度）"""
        result = super().get_signal_info(df)
        if df is None or df.empty:
            return result
        meta = cls.get_metadata()
        sig_col = meta.get('signal_column', '')
        if sig_col not in df.columns or not df[sig_col].fillna(False).any():
            return result

        sp = df.index[df[sig_col].fillna(False)]
        sig_row = df.iloc[df.index.get_loc(sp[-1])]

        extra_info = result.get('extra_info', {})
        for k in ('lower_shadow_ratio_mrb', 'break_depth_pct_mrb',
                  'shadow_pct_mrb', 'upper_shadow_ratio_mrb', 'base_low_mrb'):
            if k in df.columns:
                extra_info[k.replace('_mrb', '')] = float(sig_row.get(k, 0) or 0)
        if extra_info:
            result['extra_info'] = extra_info
        return result

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'monthly') -> Optional['RatingResult']:
        """
        [RATING_PLAN] 月线区间破位 Pinbar 评级：纯 PA 因子（无成交量/指标）。
        因子：下影占振幅 / 下影实体比 / 破位深度(适度为佳) / 收盘收回幅度。
        """
        if df is None or df.empty:
            return None
        meta = cls.get_metadata()
        sig_col = meta.get('signal_column', '')
        if sig_col in df.columns and df[sig_col].fillna(False).any():
            sp = df.index[df[sig_col].fillna(False)]
            sig_pos = df.index.get_loc(sp[-1])
        else:
            sig_pos = len(df) - 1
        row = df.iloc[sig_pos]

        o = float(row.get('open', np.nan))
        c = float(row.get('close', np.nan))
        l = float(row.get('low', np.nan))
        h = float(row.get('high', np.nan))
        rng = (h - l) if (pd.notna(h) and pd.notna(l) and h > l) else 0.0
        lower = (min(o, c) - l) if (rng > 0 and pd.notna(o) and pd.notna(c) and pd.notna(l)) else 0.0
        body = (c - o) if (pd.notna(o) and pd.notna(c)) else 0.0
        base_low = float(row.get('base_low_mrb', np.nan)) if pd.notna(row.get('base_low_mrb', np.nan)) else np.nan

        # 因子1：下影占振幅（越高=拒绝越强）
        tail_ratio = (lower / rng) if rng > 0 else 0.0
        f_tail = factor('下影占振幅', round(tail_ratio, 3), tail_ratio >= 0.60,
                        2.0 if tail_ratio >= 0.60 else 0.0,
                        sop_ref='SOP Step4 长下影', note='下影占全根振幅≥60%=强拒绝')

        # 因子2：下影/实体比（条件5 核心）
        ratio = (lower / body) if body > 0 else 0.0
        f_ratio = factor('下影实体比', round(ratio, 2), body > 0 and lower > 2 * body,
                         1.5 if (body > 0 and lower > 2 * body) else 0.0,
                         sop_ref='用户条件5', note='下影显著大于实体')

        # 因子3：破位深度（适度刺破 1%~25% 为佳，过深或不过都减分）
        bd = ((base_low - l) / base_low) if (pd.notna(base_low) and base_low > 0) else 0.0
        bd_ok = 0.01 <= bd <= 0.25
        f_depth = factor('破位深度', round(bd * 100, 2), bd_ok,
                         1.0 if bd_ok else (-0.5 if bd > 0.25 else 0.0),
                         sop_ref='弹簧刺破深度', note='适度刺破(1%~25%)制造陷阱，过深或不过都减分')

        # 因子4：收盘收回幅度（收回到平台支撑上方越多越好）
        rec = ((c - base_low) / base_low) if (pd.notna(base_low) and base_low > 0) else 0.0
        f_rec = factor('收盘收回幅度', round(rec * 100, 2), rec > 0,
                       1.0 if rec > 0 else 0.0,
                       sop_ref='用户条件3', note='收盘站回平台支撑上方')

        factors = [f_tail, f_ratio, f_depth, f_rec]
        raw = sum_weights(factors)
        score = clamp(50 + 10 * raw)
        toxic = raw <= -3
        letter = band_calibrated(cls, score, toxic=toxic, timeframe=timeframe)
        return RatingResult(raw_score=raw, score=score, letter=letter,
                            factors=factors, toxic=toxic, calibrated=is_calibration_available())

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> int:
        """月线 K 线标注：在信号 K 上标出 Spring 低点（暂用通用标注，后续可细化）"""
        # 信号 K 标注：下跌刺破 + 长下影，画一条到最低点的虚线
        try:
            sig_col = cls.get_metadata().get('signal_column', 'signal_mrb')
            if sig_col in plot_df.columns:
                sigs = plot_df[plot_df[sig_col].fillna(False)]
                if not sigs.empty:
                    last = sigs.iloc[-1]
                    ax.scatter([last.name], [last['low']], marker='^', color='red', s=80, zorder=5)
            return 1
        except Exception as e:
            logger.warning(f"annotate_chart failed for {cls.__name__}: {e}")
            return 0

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        向量化计算月线区间底部破位 Pinbar 信号。
        输入 df：月线，列含 open/high/low/close（由 get_monthly_bars 提供），按时间升序。
        输出 df：追加 signal_mrb(bool) 及订单/PA 参考列。
        """
        required = ['open', 'high', 'low', 'close']
        if df is None or df.empty or not all(col in df.columns for col in required):
            if df is not None:
                df['signal_mrb'] = False
            return df

        n = len(df)
        # Base_Low = LLV(LOW, N)：取目标月之前连续 N 个月的最低价的最小值
        # shift(1).rolling(N).min() => 行 i 取到 [i-N .. i-1] 共 N 个前月的最低
        base_low = df['low'].shift(1).rolling(self.BASE_LOOKBACK).min()
        # 不达标（历史不足 N 月）标记为 NaN
        base_low = base_low.where(df['low'].shift(1).rolling(self.BASE_LOOKBACK).count() >= self.BASE_LOOKBACK)

        low = df['low']
        high = df['high']
        open_ = df['open']
        close = df['close']

        # 条件2：盘中跌破
        cond_break = low < base_low
        # 条件3：收盘收回
        cond_recover = close > base_low
        # 条件4：阳线
        cond_bull = close > open_

        # 条件5：长下影 Pinbar
        lower = open_.combine(close, min) - low          # 下影线 = Min(O,C) - LOW
        upper = high - open_.combine(close, max)          # 上影线 = HIGH - Max(O,C)
        body = close - open_                              # 实体（阳线 >0）
        total = high - low                               # 全根振幅

        safe_total = total.replace(0, np.nan)
        cond_shadow_body = lower > self.SHADOW_BODY_MIN * body
        cond_shadow_upper = lower > self.SHADOW_UPPER_MIN * upper
        cond_shadow_pct = (lower / safe_total) >= (self.SHADOW_RANGE_PCT_MIN / 100)

        signal = (
            cond_break.fillna(False)
            & cond_recover.fillna(False)
            & cond_bull.fillna(False)
            & cond_shadow_body.fillna(False)
            & cond_shadow_upper.fillna(False)
            & cond_shadow_pct.fillna(False)
        )

        df['signal_mrb'] = signal

        # ---- 订单/PA 参考列（仅信号行填值）----
        df['base_low_mrb'] = base_low
        df['lower_shadow_ratio_mrb'] = np.where(signal, (lower / body.replace(0, np.nan)).round(2), np.nan)
        df['break_depth_pct_mrb'] = np.where(
            signal & base_low.notna() & (base_low > 0),
            (((base_low - low) / base_low) * 100).round(2), np.nan)
        df['shadow_pct_mrb'] = np.where(
            signal & safe_total.notna(),
            ((lower / safe_total) * 100).round(1), np.nan)
        df['upper_shadow_ratio_mrb'] = np.where(signal, (upper / body.replace(0, np.nan)).round(2), np.nan)

        df['entry_mrb'] = np.where(signal, high, np.nan)              # 突破信号K高点挂 Buy Stop
        df['sl_mrb'] = np.where(signal, low, np.nan)                 # 止损于弹簧最低点
        df['tp_mrb'] = np.where(signal, (close + (close - low)).round(3), np.nan)  # 刺破深度1:1投射

        return df

    def _calculate_context(self, df: pd.DataFrame) -> str:
        """为（未来）AI 审计/通知提供结构上下文"""
        try:
            latest = df.iloc[-1]
            sig = latest.get('signal_mrb', False)
            entry = latest.get('entry_mrb', np.nan)
            sl = latest.get('sl_mrb', np.nan)
            tp = latest.get('tp_mrb', np.nan)
            bl = latest.get('base_low_mrb', np.nan)
            if sig and not np.isnan(entry):
                status = f"LOCKED ✅ BuyStop={entry:.2f} | SL={sl:.2f} | TP={tp:.2f} | BaseLow={bl:.2f}"
            else:
                status = "MONITORING (无信号)"
            return f"""
<MONTHLY_RANGE_BREAK_CONTEXT>
  <BASE_LOW>{bl}</BASE_LOW>
  <SIGNAL_TYPE>区间底部破位 Spring Pinbar</SIGNAL_TYPE>
  <SETUP_STATUS>{status}</SETUP_STATUS>
</MONTHLY_RANGE_BREAK_CONTEXT>
"""
        except Exception:
            return "<MONTHLY_RANGE_BREAK_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        """结构上下文提示（ai_audit=False 时不被调用，保留接口）"""
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)
        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)
您正在审计【月线区间底部破位 Pinbar】买入信号（{code}）。
# 📊 市场微观结构
{ctx['csv_str']}
# 🧪 结构探测器输出
{context_xml}
# 📝 审计报告 (XML)
<ANALYSIS>
- Base Low Validation: (平台支撑位是否清晰？)
- Spring Quality: (盘中刺破后长下影收回，是否为典型空头陷阱？)
- Recovery: (收盘是否强势收回区间内？)
</ANALYSIS>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>审计结论</DISCORD>
"""

    def parse_result(self, response_text: str) -> Dict:
        """解析审计结果（ai_audit=False 时不被调用，保留接口）"""
        from core.formatter import parse_response
        return parse_response(response_text)
