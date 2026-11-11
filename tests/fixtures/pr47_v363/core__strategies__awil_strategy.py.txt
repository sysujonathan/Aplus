# core/strategies/awil_strategy.py
"""
[Strategy] AWIL (Always In Long) — H2 顺势入场策略

理论基础：Al Brooks Price Action — Always In Long + High 2 Entry
核心信号：EMA20上行趋势中，40根K线波段高点后的两腿回调 (L1→H1→L2→H2)

信号状态机 (State Machine):
  Phase 0: 40根K线波段高点确认 (Strong Uptrend)
  Phase 1: 检测首根 LHLL → L1 (空头第一次尝试)
  Phase 2: 检测首根 HH → H1 (多头恢复)
  Phase 3: 检测首根 LHLL → L2 (空头第二次尝试)
  Phase 4: 检测首根 HH → H2 (多头再次恢复 → 信号!)

关键条件:
  - 回调期间所有K线 OHLC 均运行在 EMA20 上方
  - H2 K线为强势阳线, 收盘在顶部 2% (close_loc >= 0.98)

冗余条件分析 (已移除):
  "EMA20 趋势向上" 由 "OHLC > EMA20" 条件数学保证:
  当 close > EMA 时, EMA_new = α*close + (1-α)*EMA > EMA, 即 EMA 必然上行。

订单参数:
  - Entry: H2 bar's High (次日 Buy Stop)
  - SL: 回调阶段最低低点 (L1 或 L2 中更低者)
  - TP: Entry + 2R (R = Entry - SL)
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
from core.rating_core import factor, sum_weights

logger = logging.getLogger(__name__)


class AWILStrategy(BaseStrategy):
    """
    Always In Long H2 顺势入场策略

    Al Brooks PA 中 Always In Long 状态下最可靠的顺势入场:
    强趋势中两腿回调后, H2 强势阳线确认多头控制 → 被困空头止损盘成为燃料。
    """

    @property
    def name(self) -> str:
        return "STRATEGY_AWIL"

    @property
    def description(self) -> str:
        return "Always In Long — H2 Two-Leg Pullback Entry (EMA20 Above)"

    @property
    def signal_column(self) -> str:
        return 'signal_awil'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """AWIL 策略元数据声明。"""
        return {
            'display_name': 'AIL',
            'sl_column': 'sl_awil',
            'entry_column': 'entry_awil',
            'tp_columns': ['tp_awil'],
            'score_column': 'sig_bar_quality_awil',
            'signal_column': 'signal_awil',
            'supported_timeframes': ['daily'],
            'tp_multiplier': 2.0,
            'ai_audit': True,
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """AWIL 信号信息提取 — 包含信号K线质量。"""
        result = super().get_signal_info(df)
        if df is None or df.empty:
            return result

        extra_info = result.get('extra_info', {})
        row = df.iloc[-1]
        q = row.get('sig_bar_quality_awil', 0)
        extra_info['sig_quality'] = q
        if extra_info:
            result['extra_info'] = extra_info

        return result

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'daily') -> Optional['RatingResult']:
        """[RATING_PLAN §4.6] AWIL 评级 (Phase 2 重设计): 全部使用随信号变化的纯 PA 因子,
        消除原恒为 A+ 退化。
        因子: 实体占比 / 回调速度(awil_pb_bars) / EMA20磁力距离 / 回撤深度比 / 两腿对称 / 下影陷阱。
        ⛔ 已移除原恒定因子 (收盘位置/全程站上EMA20/空头失败次数/RR), 因其由策略前置条件恒定,
           导致 520 信号全 A+ 无区分度 (违反评级契约)。
        """
        if df is None or df.empty:
            return None
        sig_col = 'signal_awil'
        sig_pos = len(df) - 1
        sig_row = df.iloc[-1]
        if sig_col in df.columns and df[sig_col].fillna(False).any():
            sp = df.index[df[sig_col].fillna(False)]
            sig_pos = df.index.get_loc(sp[-1])
            sig_row = df.iloc[sig_pos]

        o = float(sig_row.get('open', np.nan))
        h = float(sig_row.get('high', np.nan))
        l = float(sig_row.get('low', np.nan))
        c = float(sig_row.get('close', np.nan))
        rng = h - l
        body_pct = (c - o) / rng if (pd.notna(o) and pd.notna(c) and pd.notna(h) and pd.notna(l) and rng > 0) else 0.0

        # 1) 信号K线实体占比 (Step 4 Signal Bar Quality, 连续变化, 替代原恒定收盘位置)
        if body_pct >= 0.7:
            f_body = factor('实体占比', round(body_pct, 3), True, 1.5,
                            sop_ref='SOP Step 4', note='强实体阳线=买方完全控制')
        elif body_pct >= 0.5:
            f_body = factor('实体占比', round(body_pct, 3), True, 0.5,
                            sop_ref='SOP Step 4', note='中等实体')
        else:
            f_body = factor('实体占比', round(body_pct, 3), False, 0.0,
                            sop_ref='SOP Step 4', note='实体弱/长影线')

        # 2) 回调速度 (缺口家族共用口径, 变化; 快速=空头被套)
        pb_bars = int(sig_row.get('awil_pb_bars', 0) or 0)
        if pb_bars > 0:
            f_pb = factor('回调速度', pb_bars, pb_bars <= 8,
                          (1.0 if pb_bars <= 8 else (-1.0 if pb_bars > 15 else 0.0)),
                          sop_ref='SOP Step 8/Step 3',
                          note='快速回调=空头被套' if pb_bars <= 8 else ('拖延回调=动能衰减' if pb_bars > 15 else '回调速度中性'))
        else:
            f_pb = factor('回调速度', 0, False, 0.0, sop_ref='SOP Step 8/Step 3', note='无回调速度数据')

        # 3) EMA20 磁力距离 (Step 7 Method 3, 变化; 适中最佳, 过远=追高)
        ema = float(sig_row.get('ema20', np.nan))
        atr = float(sig_row.get('atr', np.nan))
        ema_dist = (c - ema) / atr if (pd.notna(ema) and pd.notna(atr) and atr > 0) else np.nan
        if pd.notna(ema_dist):
            if 0.5 <= ema_dist <= 5.0:
                f_ema = factor('EMA磁力距离', round(ema_dist, 2), True, 0.5,
                               sop_ref='SOP Step 7 Method 3 (EMA磁力, 非动量)', note='距EMA适中=健康低吸')
            elif ema_dist > 6.0:
                f_ema = factor('EMA磁力距离', round(ema_dist, 2), False, -0.5,
                               sop_ref='SOP Step 7 Method 3 (EMA磁力, 非动量)', note='过度远离EMA=追高')
            else:
                f_ema = factor('EMA磁力距离', round(ema_dist, 2), False, 0.0,
                               sop_ref='SOP Step 7 Method 3 (EMA磁力, 非动量)', note='贴脸EMA')
        else:
            f_ema = factor('EMA磁力距离', 0.0, False, 0.0, sop_ref='SOP Step 7 Method 3', note='无EMA数据')

        # 4) 回撤深度比 (swing_high -> 信号low, 变化; 浅回撤=强趋势)
        swing_high = float(sig_row.get('awil_swing_high', np.nan))
        if pd.notna(swing_high) and swing_high > 0 and pd.notna(l):
            depth = (swing_high - l) / swing_high
            if depth < 0.12:
                f_depth = factor('回撤深度', round(depth, 3), True, 0.5,
                                 sop_ref='SOP Step 6 (浅回调=强趋势)', note='浅回撤=强势')
            elif depth > 0.25:
                f_depth = factor('回撤深度', round(depth, 3), False, -0.5,
                                 sop_ref='SOP Step 6 (深回调=弱)', note='深回撤=趋势减弱')
            else:
                f_depth = factor('回撤深度', round(depth, 3), True, 0.0,
                                 sop_ref='SOP Step 6', note='回撤适中')
        else:
            f_depth = factor('回撤深度', 0.0, False, 0.0, sop_ref='SOP Step 6', note='无回撤数据')

        # 5) 两腿对称性 (Step 6 H2, 变化; 回测 awil 中 L2≥L1 更优 w=+0.5)
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
        f_leg2 = factor('两腿对称性', 1.0 if leg2_shallow else 0.0, leg2_shallow, 1.0 if leg2_shallow else 0.0,
                        sop_ref='SOP Step 6 H2', note='L2低点≥L1=经典H2' if leg2_shallow else 'L2跌破L1')

        # 6) 下影支撑回收 (空头陷阱, Step 8 Trap2 + Step 4 Tail, 变化稀有)
        tail_trap = False
        try:
            if sig_pos >= 1:
                prev_low = df.iloc[sig_pos - 1]['low']
                if pd.notna(prev_low) and pd.notna(l) and pd.notna(c) and pd.notna(o):
                    tail_trap = (l <= prev_low) and (c > o)
        except Exception:
            tail_trap = False
        f_tail = factor('下影支撑回收', 1.0 if tail_trap else 0.0, tail_trap, 1.0 if tail_trap else 0.0,
                        sop_ref='SOP Step 8 Trap2 + Step 4 Tail',
                        note='下影刺穿前低收阳=空头陷阱' if tail_trap else '无下影陷阱')

        factors = [f_body, f_pb, f_ema, f_depth, f_leg2, f_tail]
        raw = sum_weights(factors)
        score = clamp(40 + 12 * raw)  # Phase 2 重设计: 全变化因子后 raw 有分布, 不再恒 A+
        toxic = raw <= -3
        letter = band_calibrated(cls, score, toxic=toxic, timeframe=timeframe)
        return RatingResult(raw_score=raw, score=score, letter=letter, factors=factors,
                            toxic=toxic, calibrated=is_calibration_available())

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame,
                       strategy_type: str, **kwargs) -> None:
        """AWIL 图表标注 — Swing High, H2 信号, SL/TP 水平线。

        关键: mplfinance 横轴使用整数位置 0..N-1 (非 Timestamp)。
        早先用 Timestamp 作 x 坐标会被 matplotlib 当成纳秒巨数, 撑爆坐标轴
        (图被渲染成数亿像素, 浏览器/预览无法显示)。此处统一改用整数位,
        与 structural_gap / gap_h2 / mtr 等策略的 annotate_chart 约定一致。
        """
        if 'signal_awil' not in plot_df.columns:
            return
        sig_mask = plot_df['signal_awil']
        if not sig_mask.any():
            return

        date_list = list(plot_df.index)
        n = len(date_list)
        # 取最后一个信号的整数位 (mplfinance 横轴 = 0..N-1)
        sig_positions = [i for i, v in enumerate(sig_mask) if v]
        sig_pos = sig_positions[-1] if sig_positions else (n - 1)
        signal_row = plot_df.iloc[sig_pos]

        # 标注 H2 信号K线 (xy 用整数位 +0.5 对齐 K 线中心)
        ax.annotate("H2 Signal\n(AWIL入场)",
                    xy=(sig_pos + 0.5, signal_row['high']),
                    xytext=(sig_pos + 6.5, signal_row['high'] * 1.02),
                    arrowprops=dict(arrowstyle="->", color='green'),
                    fontsize=9, color='green', ha='center',
                    fontweight='bold')

        # SL 水平线 (红色虚线)
        sl_price = kwargs.get('sl_price') or signal_row.get('sl_awil')
        if sl_price and not np.isnan(sl_price):
            ax.axhline(y=sl_price, color='red', linestyle='--',
                       alpha=0.7, linewidth=1)
            ax.text(n - 1, sl_price, f' SL={sl_price:.2f}',
                    color='red', fontsize=8, va='center')

        # TP 水平线 (绿色虚线)
        tp_price = kwargs.get('tp1') or signal_row.get('tp_awil')
        if tp_price and not np.isnan(tp_price):
            ax.axhline(y=tp_price, color='green', linestyle='--',
                       alpha=0.7, linewidth=1)
            ax.text(n - 1, tp_price, f' TP={tp_price:.2f}',
                    color='green', fontsize=8, va='center')

        # Swing High 标注 (蓝色点线)
        swing_high = signal_row.get('awil_swing_high')
        if swing_high and not np.isnan(swing_high):
            ax.axhline(y=swing_high, color='blue', linestyle=':',
                       alpha=0.5, linewidth=1)
            ax.text(0, swing_high,
                    f'Swing High={swing_high:.2f} ',
                    color='blue', fontsize=8, va='center', ha='right')

    # =====================================================================
    # 核心计算
    # =====================================================================
    def __init__(self):
        # 波段高点回溯窗口 (Al Brooks: 40-Bar Swing High)
        self.SWING_LOOKBACK = getattr(settings, 'AWIL_SWING_LOOKBACK', 40)
        # 回调最大跟踪窗口
        self.MAX_PULLBACK_WINDOW = getattr(settings, 'AWIL_MAX_PULLBACK_WINDOW', 30)
        # 最少回调K线数 (L1+H1+L2+H2 至少需要 4 根)
        self.MIN_PULLBACK_BARS = 4
        # 收盘位置阈值 (顶部 2% → close_loc >= 0.98)
        self.CLOSE_LOC_THRESHOLD = 0.98

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """向量化计算 AWIL H2 信号。

        Args:
            df: 包含 OHLCV + 技术指标 (ema20, atr 等) 的 DataFrame

        Returns:
            添加了 signal_awil / entry / sl / tp 列的 DataFrame
        """
        if len(df) < self.SWING_LOOKBACK + 10:
            df['signal_awil'] = False
            return df

        required = ['ema20', 'atr', 'is_bullish', 'close_loc']
        if not all(col in df.columns for col in required):
            logger.warning(
                f"AWIL 缺少列: "
                f"{[c for c in required if c not in df.columns]}")
            df['signal_awil'] = False
            return df

        # ── Step 1: 波段高点事件 (Al Brooks: 40-Bar Swing High) ──
        _prev_max = df['high'].shift(1).rolling(
            window=self.SWING_LOOKBACK, min_periods=20).max()
        is_swing_event = df['high'] > _prev_max

        _groups = is_swing_event.cumsum()
        _bar_count = df.groupby(_groups).cumcount()

        # ── Step 2: 回调全程 OHLC > EMA20 (Always In Long 核心) ──
        # Low > EMA20 → OHLC 全部在 EMA20 上方 (Low 是四价最小值)
        # 数学保证: close > EMA → EMA 向上, "EMA 趋势向上"为冗余条件
        _above_ema = df['low'] > df['ema20']
        _all_above = (_above_ema.astype(int).groupby(_groups)
                      .expanding().min().droplevel(0).astype(bool))

        # ── Step 3: L1→H1→L2→H2 状态机 ──
        is_h2 = self._detect_h2_pattern(df, _groups)

        # ── Step 4: 信号K线质量 (强势阳线 + 收盘顶部2%) ──
        is_strong_bull = (df['is_bullish']
                          & (df['close_loc'] >= self.CLOSE_LOC_THRESHOLD))

        # ── Step 5: 时间窗口 + 信号组装 ──
        in_window = ((_bar_count >= self.MIN_PULLBACK_BARS)
                     & (_bar_count <= self.MAX_PULLBACK_WINDOW)
                     & (_groups > 0))

        _signal_raw = is_h2 & _all_above & is_strong_bull & in_window

        # 去重: 每次波段高点仅取首次信号
        _already = (_signal_raw.groupby(_groups)
                    .cumsum().shift(1).fillna(0) > 0)
        df['signal_awil'] = _signal_raw & ~_already
        df['sig_bar_quality_awil'] = df['close_loc'].round(3)
        df['awil_ema_above'] = _all_above
        # [Phase 2] 回调速度: swing_high 事件到 H2 信号的 bar 数 (缺口家族共用 pb_speed 口径)
        df['awil_pb_bars'] = np.where(df['signal_awil'], _bar_count, np.nan)

        # ── Step 6: 订单参数 ──
        self._compute_order_params(df, _groups, is_swing_event)

        return df

    def _detect_h2_pattern(self, df: pd.DataFrame,
                           groups: pd.Series) -> pd.Series:
        """构建 L1→H1→L2→H2 四阶段状态机。

        Al Brooks PA: 两腿回调 (Two-Leg Pullback) 的经典序列。
        空头两次尝试反转均失败 → 高概率顺势入场。

        Args:
            df: 包含 OHLC 的 DataFrame
            groups: 波段高点分组标记 (cumsum)

        Returns:
            布尔 Series, 标记每组中首根 H2 K线
        """
        # Al Brooks PA: LHLL = Lower High + Lower Low (回调特征K线)
        is_lhll = ((df['high'] < df['high'].shift(1))
                   & (df['low'] < df['low'].shift(1)))
        # Al Brooks PA: HH = Higher High (多头恢复特征K线)
        is_hh = df['high'] > df['high'].shift(1)

        # Phase 1: L1 — 首根 LHLL (空头第一次尝试反转)
        lhll_cum = is_lhll.groupby(groups).cumsum()
        phase1_done = lhll_cum >= 1

        # Phase 2: H1 — L1 后首根 HH (多头首次恢复)
        hh_cum_1 = (is_hh & phase1_done).groupby(groups).cumsum()
        phase2_done = hh_cum_1 >= 1

        # Phase 3: L2 — H1 后首根 LHLL (空头第二次尝试反转)
        lhll_cum_2 = (is_lhll & phase2_done).groupby(groups).cumsum()
        phase3_done = lhll_cum_2 >= 1

        # Phase 4: H2 — L2 后首根 HH (多头再次恢复 → 信号!)
        hh_cum_2 = (is_hh & phase3_done).groupby(groups).cumsum()
        prev_hh_cum_2 = hh_cum_2.groupby(groups).shift(1).fillna(0)
        is_h2 = (prev_hh_cum_2 == 0) & (hh_cum_2 >= 1)

        return is_h2

    def _compute_order_params(self, df: pd.DataFrame,
                              groups: pd.Series,
                              is_swing_event: pd.Series) -> None:
        """计算 Entry / SL / TP 订单参数。

        Al Brooks H2 入场:
          Entry = H2 的 High (次日 Buy Stop)
          SL = 回调最低低点 (L1 或 L2 中更低者)
          TP = Entry + 2R

        Args:
            df: DataFrame (原地修改, 添加 entry/sl/tp 列)
            groups: 波段高点分组标记
            is_swing_event: 波段高点事件标记
        """
        _group_min_low = (df['low'].groupby(groups)
                          .expanding().min().droplevel(0))
        _swing_high = np.where(is_swing_event, df['high'], np.nan)
        _swing_high = pd.Series(_swing_high, index=df.index).ffill()

        sig = df['signal_awil']

        # Entry = H2 K线的 High (次日挂 Buy Stop)
        df['entry_awil'] = np.where(sig, df['high'], np.nan)
        # SL = 回调阶段最低低点 (Al Brooks: L1 或 L2 中更低者)
        df['sl_awil'] = np.where(sig, _group_min_low, np.nan)
        # TP = Entry + 2R (R = Entry - SL, 盈亏比 2:1)
        _r = df['high'] - _group_min_low
        df['tp_awil'] = np.where(sig, df['high'] + 2 * _r, np.nan)
        # 锚点 (绘图/通知)
        df['awil_swing_high'] = np.where(sig, _swing_high, np.nan)

    # =====================================================================
    # AI 审计接口
    # =====================================================================
    def _calculate_context(self, df: pd.DataFrame) -> str:
        """为 AI 审计提供 AWIL 结构上下文。"""
        try:
            latest = df.iloc[-1]
            ema_status = ("ABOVE ✅"
                          if latest.get('awil_ema_above', False)
                          else "BELOW ❌")

            sig = latest.get('signal_awil', False)
            entry = latest.get('entry_awil', np.nan)
            sl = latest.get('sl_awil', np.nan)
            tp = latest.get('tp_awil', np.nan)

            if sig and not np.isnan(entry):
                risk = entry - sl if not np.isnan(sl) else 0
                status_str = (f"LOCKED ✅ Buy Stop={entry:.2f} | "
                              f"SL={sl:.2f} | TP={tp:.2f} | R={risk:.2f}")
            else:
                status_str = "MONITORING (Waiting for H2 Setup)"

            ema_val = latest.get('ema20', np.nan)
            atr_val = latest.get('atr', 1)
            ema_dist = ((latest['close'] - ema_val) / atr_val
                        if not np.isnan(ema_val) else 0)

            swing_h = latest.get('awil_swing_high', np.nan)
            sh_str = f"{swing_h:.2f}" if not np.isnan(swing_h) else "N/A"

            return f"""
<AWIL_CONTEXT>
  <SWING_HIGH>{sh_str}</SWING_HIGH>
  <EMA20_STATUS>{ema_status}</EMA20_STATUS>
  <EMA20_DISTANCE>{ema_dist:.2f} ATR</EMA20_DISTANCE>
  <SIGNAL_TYPE>H2 (Always In Long Two-Leg Pullback)</SIGNAL_TYPE>
  <SETUP_STATUS>{status_str}</SETUP_STATUS>
</AWIL_CONTEXT>
"""
        except Exception:
            return "<AWIL_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        """生成 AWIL H2 信号的 AI 审计提示词。

        Args:
            context_data: 包含 'code', 'df', 'ctx' 的上下文字典

        Returns:
            Al Brooks 风格的审计提示词
        """
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)

        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)

您正在审计【Always In Long — H2 顺势入场策略】的买入信号。

# 🕵️ Brooks Framework For Always In Long H2

1. **Always In Long (始终做多)**:
   - 市场处于强烈的多头趋势, 所有K线 OHLC 均运行在 EMA20 上方
   - 空头毫无机会, 每次回调都被多头迅速接住

2. **Two-Leg Pullback (两腿回调)**:
   - **L1**: 首根 LHLL, 空头第一次尝试反转
   - **H1**: 首根 HH, 多头首次反击
   - **L2**: H1后首根 LHLL, 空头最后一搏
   - **H2 (信号)**: L2后首根 HH, 强势阳线收盘在顶部2%以内
   - 两次反转尝试均失败 → 被困空头止损盘成为多头燃料

3. **Signal Bar Quality**:
   - H2 K线必须是强势阳线, 收盘在振幅顶部 2% (close_loc >= 0.98)
   - 这种收盘位置表明多头完全控制

4. **The Math**:
   - Entry = H2 的 High (次日 Buy Stop)
   - SL = 回调最低点 | TP = Entry + 2R

# 📊 市场微观结构与指标
{ctx['csv_str']}

# 🧪 系统探测器输出
{context_xml}

# 📝 审计报告 (XML)
<ANALYSIS>
- Trend Quality: (EMA20 斜率? 价格远离 EMA20 的程度?)
- Pullback Structure: (L1-H1-L2-H2 是否清晰? 回调深度是否合理?)
- Signal Bar: (H2 K线实体大小? 影线? 收盘位置?)
- Always In Assessment: (是否真正处于 Always In Long 状态?)
</ANALYSIS>
<PA_TAGS>Always In Long, H2顺势入场, 两腿回调, EMA20支撑</PA_TAGS>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>审计结论</DISCORD>
"""

    def parse_result(self, response_text: str) -> Dict:
        """解析 AI 审计结果, 提取 PA 标签。"""
        from core.formatter import parse_response
        parsed = parse_response(response_text)

        tags_match = re.search(
            r"<PA_TAGS>(.*?)</PA_TAGS>",
            response_text, re.DOTALL | re.IGNORECASE)
        if tags_match:
            tags = tags_match.group(1).strip()
            parsed['pa_tags'] = tags
            parsed['reason'] = tags

        return parsed
