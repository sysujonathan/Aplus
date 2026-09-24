import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from .geometric_engine import GeometricTrendlineEngine

@dataclass
class MTRPoint:
    index: int
    price: float
    type: str  # 'MAJOR_HIGH', 'MAJOR_LOW', 'MINOR_HIGH', 'MINOR_LOW'
    date: str
    bars_since_last: int = 0

class MTRStructuralEngineV35:
    """
    MTR V36.0 核心引擎：Al Brooks 原文四要素严格匹配
    
    四要素: ① 趋势存在 ② 趋势线突破 ③ 趋势极值测试 ④ 二次反转深远(交由AI)
    V36.0: 七维度100分评分体系（结构15+趋势线10+反弹通道20+回调通道35+极值5+信号K10+深度5）
    """
    
    MIN_BARS = {
        "H0_to_L1": 8,
        "L1_to_H1": 3,
        "H1_to_TL": 2,
        "TL_to_H2": 1
    }
    
    IDEAL_RATIOS = {
        "retrace_range": (0.382, 0.786),   # (H1-L1)/(H0-L1) 第一腿反弹幅度 [V36.1 放宽上限]
        "pullback_range": (0.500, 0.786),  # (H1-TL)/(H1-L1) 回撤深度
        "h2_min_pos": 0.618               # (H2-TL)/(H1-TL) H2位置 (备用)
    }

    def __init__(self, ema_period: int = 20):
        self.ema_period = ema_period
        self.locked_until = -1
        self.exhausted_h1_indices = set()
        # [V35.1] 趋势线引擎 — Al Brooks 原文第②要素
        self.tl_engine = GeometricTrendlineEngine(
            swing_window=3, break_threshold=0.3
        )

    def find_swing_points(self, df: pd.DataFrame, window: int = 5) -> List[MTRPoint]:
        """识别波段极值点"""
        swings = []
        for i in range(window, len(df) - window):
            high_val = df['high'].iloc[i]
            low_val = df['low'].iloc[i]
            
            is_high = all(high_val >= df['high'].iloc[i-j] for j in range(1, window+1)) and \
                      all(high_val > df['high'].iloc[i+j] for j in range(1, window+1))
            
            is_low = all(low_val <= df['low'].iloc[i-j] for j in range(1, window+1)) and \
                     all(low_val < df['low'].iloc[i+j] for j in range(1, window+1))
            
            if is_high or is_low:
                swings.append(MTRPoint(i, high_val if is_high else low_val, 
                                     'HIGH' if is_high else 'LOW', 
                                     df['date'].iloc[i]))
        return swings

    def match_mtr_pattern(self, df: pd.DataFrame, swings: List[MTRPoint], current_idx: int) -> Optional[Dict]:
        """核心匹配逻辑 (V34.4)：优先捕获“第一腿”性质改变"""
        if current_idx <= self.locked_until:
            return None
            
        past_swings = [s for s in swings if s.index <= current_idx]
        if len(past_swings) < 3:
            return None
            
        close_vals = df['close'].values
        ema_vals = df['ema20'].values
        high_vals = df['high'].values
        low_vals = df['low'].values
        bearish_mask = (close_vals < ema_vals)
        bullish_mask = (close_vals > ema_vals)

        current_price = close_vals[current_idx]

        # --- 策略：先逆序定 L1/H0，再从 L1 向后定位第一个有效 H1 (性质改变) ---
        for l1_sw_idx in range(len(past_swings)-1, 1, -1):
            l1 = past_swings[l1_sw_idx]
            if 'LOW' not in l1.type: continue
            
            for h0_sw_idx in range(l1_sw_idx-1, -1, -1):
                h0 = past_swings[h0_sw_idx]
                if 'HIGH' not in h0.type: continue
                
                # 全局最高锚定约束
                obs_extreme_high = np.max(high_vals[h0.index : current_idx + 1])
                if h0.price < obs_extreme_high: continue
                
                # [V36.1] H0 必须是"Major"趋势起点 — 最高价在 EMA20 上方
                # EMA 下方的小反弹高点不是真正的趋势起点
                if high_vals[h0.index] <= ema_vals[h0.index]:
                    continue
                
                if (l1.index - h0.index) < self.MIN_BARS["H0_to_L1"]: continue
                if not self._validate_stage_1_np(bearish_mask, h0, l1): continue
                
                # [V36.1] L1 必须是区间最深低点
                # 如果 L1 之后还有更低的 Swing Low，说明 L1 不是真正极值
                has_deeper_low = any(
                    s.price < l1.price
                    for s in past_swings[l1_sw_idx + 1:]
                    if 'LOW' in s.type
                )
                if has_deeper_low:
                    continue
                
                # [V36.1] 寻找 L1 之后反弹的"最高"有效 H1
                # 第一轮反弹可能产生多个 EMA Gap Bar / Swing High
                # H1 = 整轮反弹中符合 Fib 条件的最高 Swing High
                found_h1 = None
                for h1_try_idx in range(l1_sw_idx + 1, len(past_swings)):
                    h1_cand = past_swings[h1_try_idx]
                    if 'HIGH' not in h1_cand.type: continue
                    
                    fall = h0.price - l1.price + 1e-9
                    rise = h1_cand.price - l1.price
                    retrace_ratio = rise / fall
                    
                    # 验证 H1 突破 EMA 的性质改变
                    if (h1_cand.index - l1.index) >= self.MIN_BARS["L1_to_H1"] and \
                       self.IDEAL_RATIOS["retrace_range"][0] <= retrace_ratio <= self.IDEAL_RATIOS["retrace_range"][1] and \
                       self._validate_stage_2_np(low_vals, close_vals, ema_vals, l1, h1_cand):
                        
                        if h1_cand.index in self.exhausted_h1_indices: continue
                        # 取更高的候选者（反弹可能多段上涨）
                        if found_h1 is None or h1_cand.price > found_h1[1].price:
                            found_h1 = (h1_try_idx, h1_cand, rise)
                        elif h1_cand.price < found_h1[1].price:
                            break  # 反弹波峰已过，后面是更低的高点
                    elif found_h1 is not None:
                        # 当前候选不满足 Fib，但已有合格 H1 → 反弹结束
                        break
                
                if not found_h1: continue
                h1_sw_idx, h1, h1_rise = found_h1

                # 生命周期重置校验 (2R 目标或严重破位)
                if np.min(low_vals[h1.index : current_idx + 1]) < l1.price * 0.80:
                    self.exhausted_h1_indices.add(h1.index)
                    continue
                target_2r = h1.price + h1_rise
                if np.max(high_vals[h1.index : current_idx + 1]) >= target_2r:
                    self.exhausted_h1_indices.add(h1.index)
                    continue

                # 在该 H1 之后寻找测试信号 TL (不再强求 H2 已经走出 Swing High)
                # V35.0: Early Access Mode - H2 识别权下放给 AI
                
                # -------------------------------------------------------
                # 模式 A：SETUP_READY — TL 已完全形成，Fib 回撤满足条件
                # -------------------------------------------------------
                if (current_idx - h1.index) >= self.MIN_BARS["H1_to_TL"]:
                    # 寻找区间内的最低点
                    interval_low_idx = np.argmin(low_vals[h1.index + 1 : current_idx + 1]) + h1.index + 1
                    interval_low = low_vals[interval_low_idx]
                    
                    # 验证 TL 回调比例
                    pullback = h1.price - interval_low
                    pullback_ratio = pullback / (h1_rise + 1e-9)
                    
                    # 🟢 [V35.0] 严格斐波那契回撤：(H1-TL)/(H1-L1) 在 0.500-0.786
                    if self.IDEAL_RATIOS["pullback_range"][0] <= pullback_ratio <= self.IDEAL_RATIOS["pullback_range"][1]:
                        # [V36.1] TL 必须在 EMA20 下方 — 真正的极值测试
                        # 如果回调点还在 EMA 上方，说明尚未进入空头领地测试
                        if close_vals[interval_low_idx] < ema_vals[interval_low_idx]:
                            # 这是一个有效的 TL 结构
                            tl = MTRPoint(interval_low_idx, interval_low, 'LOW_TEST', df['date'].iloc[interval_low_idx])
                            
                            # [V36.0] 七维度综合评分
                            depth_val = 0
                            if 'trend_depth' in df.columns:
                                depth_val = df['trend_depth'].iloc[l1.index]
                            
                            # 寻找信号K线 (buy stop order)
                            signal_bar = self._find_signal_bar(df, tl)
                            sb_quality = signal_bar.get('quality', 0) if signal_bar else 0
                            
                            score = self._calculate_quality_score_v36(
                                df, h0, l1, h1, tl,
                                trend_depth=depth_val,
                                signal_bar_quality=sb_quality
                            )
                            
                            if score >= 50:
                                self.locked_until = current_idx + 3
                                return {
                                    'stage': 'SETUP_READY',
                                    'points': {'H0': h0, 'L1': l1, 'H1': h1, 'TL': tl, 'H2': None},
                                    'score': round(score, 2),
                                    'signal_bar': signal_bar
                                }

                # -------------------------------------------------------
                # 模式 B：SETUP_FORMING — TL 尚在测试中 (潜力雷达)
                # [Bug Fix V36.2] 原代码因 break 位置错误，此模式永远不执行。
                # -------------------------------------------------------
                if (current_idx - h1.index) < 25:
                    post_h1_low_idx = np.argmin(low_vals[h1.index : current_idx + 1]) + h1.index
                    post_h1_low = low_vals[post_h1_low_idx]
                    
                    if post_h1_low >= l1.price * 0.80:
                        pullback_tot = h1.price - post_h1_low
                        buy_zone_potential = post_h1_low + pullback_tot * 0.5
                        
                        if current_price <= buy_zone_potential:
                            return {
                                'stage': 'SETUP_FORMING',
                                'points': {
                                    'H0': h0, 'L1': l1, 'H1': h1, 
                                    'TL': MTRPoint(post_h1_low_idx, post_h1_low, 'LOW_POTENTIAL', df['date'].iloc[post_h1_low_idx])
                                },
                                'score': 45.0  # 固定分，优先级低于 SETUP_READY
                            }

                # 当前 H1 两种模式均不满足，尝试更早的 H0/L1 组合
                break
        return None

    def _find_signal_bar(self, df: pd.DataFrame, tl: MTRPoint) -> dict:
        """
        [V36.0] 寻找信号K线 + 质量评估 — Brooks Buy Stop Order
        条件: 阳线 + 收盘在K线上半部（50%以上）
        质量 (quality: 0-10):
          - 实体/ATR (最高5分) — 实体越大越好
          - 上影线位置 (最高5分) — 光头阳(Close=High)满分，上影线<20%得3分
          注意: 不要求站上 EMA20，纯形态评估
        """
        close_vals = df['close'].values
        open_vals = df['open'].values
        high_vals = df['high'].values
        low_vals = df['low'].values
        has_atr = 'atr' in df.columns

        for si in range(tl.index + 1, min(len(df), tl.index + 16)):
            c = close_vals[si]
            o = open_vals[si]
            h = high_vals[si]
            lo = low_vals[si]
            bar_range = h - lo
            if bar_range <= 0:
                continue
            # 条件: 阳线 + 收盘在K线上半部
            if c > o and (c - lo) / bar_range >= 0.5:
                body = c - o
                upper_wick = h - c
                quality = 0.0

                # 维度1: 实体/ATR (最高5分) — 强劲阳线
                if has_atr:
                    atr_val = df['atr'].iloc[si]
                    if atr_val > 0:
                        quality += min(body / (atr_val * 0.5), 1.0) * 5.0

                # 维度2: 上影线位置 (最高5分)
                # 光头阳线 (Close == High) → 5分满分
                # 上影线 < 20% bar range → 3分
                # 上影线 < 40% bar range → 1分
                wick_pct = upper_wick / bar_range if bar_range > 0 else 0
                if wick_pct < 0.005:       # 近乎光头阳
                    quality += 5.0
                elif wick_pct < 0.20:
                    quality += 3.0
                elif wick_pct < 0.40:
                    quality += 1.0

                return {
                    'idx': si,
                    'high': h,
                    'date': df['date'].iloc[si] if 'date' in df.columns else '',
                    'quality': round(quality, 1)
                }
        return None

    def _validate_stage_1_np(self, bearish_mask: np.ndarray, h0: MTRPoint, l1: MTRPoint) -> bool:
        """验证①趋势存在：H0→L1区间至少55%的K线收盘低于EMA"""
        chunk = bearish_mask[h0.index : l1.index + 1]
        return np.mean(chunk) > 0.55

    def _validate_stage_2_np(self, low_vals: np.ndarray, close_vals: np.ndarray,
                             ema_vals: np.ndarray, l1: MTRPoint, h1: MTRPoint) -> bool:
        """
        [V35.1] 验证②性质改变 (Change of Character) — Al Brooks 原文:
        "反向波动必须足够强势，并突破趋势线，最好能突破移动平均线"
        
        不再使用 bullish 百分比（受前期跌势K线数量影响不可控），
        改为 PA 理论中更直观的 EMA Gap Bar 检测：
          1. 至少出现 1 根 EMA Gap Bar (bar 的 low > EMA20) — 多头尝试力量的确定性证据
          2. H1 本身必须收盘于 EMA20 之上
        """
        # EMA Gap Bar: 整根K线的低点都在 EMA 之上，说明多头已完全控盘这根K线
        gap_mask = low_vals[l1.index : h1.index + 1] > ema_vals[l1.index : h1.index + 1]
        if not np.any(gap_mask):
            return False
        # H1 本身必须收盘于 EMA 之上 — 确认突破成功
        if close_vals[h1.index] <= ema_vals[h1.index]:
            return False
        return True
    
    def _check_trendline_break(self, df: pd.DataFrame, h0: MTRPoint, l1: MTRPoint, h1: MTRPoint) -> bool:
        """
        [DEPRECATED — V37 备用] 趋势线突破检测。当前 match_mtr_pattern() 未调用此方法，
        改用 EMA Gap Bar 作为突破替代指标。保留供后续版本可能启用。
        "所有的主要趋势反转都以趋势线突破为开端"
        
        利用 GeometricTrendlineEngine 在 H0→L1 区间画出下降趋势线，
        然后检查 H1 是否突破该趋势线。
        """
        try:
            # 取 H0 之前足够的数据来构建趋势线环境
            start_idx = max(0, h0.index - 30)
            context_df = df.iloc[start_idx : h1.index + 1].copy()
            context_df = context_df.reset_index(drop=True)
            
            if len(context_df) < 15 or 'atr' not in context_df.columns:
                return False
            
            # 识别波段点并寻找下降趋势线
            swing_highs, swing_lows = self.tl_engine.find_swing_points(context_df)
            swing_points = self.tl_engine.identify_swing_objects(context_df, swing_highs, swing_lows)
            
            # 在 H1 对应位置检查突破
            h1_local_idx = h1.index - start_idx
            if h1_local_idx < 0 or h1_local_idx >= len(context_df):
                return False
            
            trendline = self.tl_engine.find_bear_trendline(context_df, swing_points, h1_local_idx)
            if trendline is None:
                return False
            
            return self.tl_engine.check_trendline_break(context_df, h1_local_idx, trendline)
        except Exception:
            return False
    
    def _assess_rally_channel(self, df: pd.DataFrame, l1: MTRPoint, h1: MTRPoint) -> float:
        """
        [V36.0] 反弹通道评估 (L1→H1) — 多头强势 = 高分
        满分: 20
        子因子: ① 向上缺口(7) ② 重叠度(7) ③ 连续趋势K线(6)
        """
        rally_len = h1.index - l1.index
        if rally_len < 2:
            return 10  # 区间太短，给中性分

        close_vals = df['close'].values
        open_vals = df['open'].values
        high_vals = df['high'].values
        low_vals = df['low'].values
        score = 0.0

        s, e = l1.index, h1.index + 1  # 区间 [s, e)

        # ① 向上缺口 (最高7分) — 当日 Low > 前日 High = 跳空
        if rally_len > 1:
            gaps_up = low_vals[s+1:e] > high_vals[s:e-1]
            n_gaps = int(np.sum(gaps_up))
            # 有缺口且未回补（简化: 只检测是否存在）
            if n_gaps >= 2:
                score += 7  # 多个跳空，极强多头
            elif n_gaps == 1:
                score += 5
            else:
                score += 2  # 无跳空，中低分

        # ② 重叠度 (最高7分) — 重叠低 = 多头强势连续推进
        # 重叠: 后一根 Low < 前一根 Close（K线之间有交叉）
        if rally_len > 2:
            overlap_mask = low_vals[s+1:e] < close_vals[s:e-1]
            overlap_pct = np.mean(overlap_mask)
            # 重叠度低 → 趋势性强 → 高分
            if overlap_pct < 0.3:
                score += 7  # 几乎无重叠，强趋势
            elif overlap_pct < 0.5:
                score += 5
            elif overlap_pct < 0.7:
                score += 3
            else:
                score += 1  # 高度重叠，弱反弹
        else:
            score += 3  # 区间太短，中性

        # ③ 连续趋势K线 (最高6分) — 连续阳线数量
        bull_bars = (close_vals[s:e] > open_vals[s:e]).astype(int)
        max_consec_bull = 0
        current_streak = 0
        for b in bull_bars:
            if b:
                current_streak += 1
                max_consec_bull = max(max_consec_bull, current_streak)
            else:
                current_streak = 0

        if max_consec_bull >= 4:
            score += 6  # 连续4+阳线，极强
        elif max_consec_bull >= 3:
            score += 4
        elif max_consec_bull >= 2:
            score += 2
        else:
            score += 0

        return min(20, score)

    def _assess_pullback_quality(self, df: pd.DataFrame, l1: MTRPoint, h1: MTRPoint, tl: MTRPoint) -> float:
        """
        [V36.0] 回调通道质量评估 (H1→TL) — 空头衰竭 = 高分
        满分: 35
        子因子: ① 缺口(9) ② 重叠度(9) ③ 连续趋势K线(9) ④ 空头高潮(8)
        """
        pb_len = tl.index - h1.index
        if pb_len < 2:
            return 18  # 回调仅1-2根K线，给中性分

        close_vals = df['close'].values
        open_vals = df['open'].values
        high_vals = df['high'].values
        low_vals = df['low'].values
        score = 0.0

        s, e = h1.index, tl.index + 1  # 区间 [s, e)

        # ① 向下缺口 (最高9分) — 对 MTR 不利
        # 存在未回补的向下缺口 → 空头还很强 → 低分
        # 缺口已回补或无缺口 → 高分
        has_unfilled_gap = False
        if pb_len > 1:
            for gi in range(s + 1, e):
                if high_vals[gi] < low_vals[gi - 1]:  # 向下缺口
                    gap_top = low_vals[gi - 1]
                    # 检查之后是否回补
                    filled = np.any(high_vals[gi:e] >= gap_top)
                    if not filled:
                        has_unfilled_gap = True
                        break

        if has_unfilled_gap:
            score += 0  # 有未回补缺口，空头强势
        else:
            # 检查是否有已回补的缺口（说明多头在反击）
            any_gap = False
            if pb_len > 1:
                gaps_down = high_vals[s+1:e] < low_vals[s:e-1]
                any_gap = np.any(gaps_down)
            if any_gap:
                score += 9  # 有缺口但已回补 → 多头力量介入
            else:
                score += 5  # 无缺口 → 中性

        # ② 重叠度 (最高9分) — 重叠高 = 空头动能弱 = 好
        # 重叠: 后一根 High > 前一根 Close（K线之间有交叉回弹）
        if pb_len > 2:
            overlap_mask = high_vals[s+1:e] > close_vals[s:e-1]
            overlap_pct = np.mean(overlap_mask)
            # 重叠度高 → 空头弱，很多阳线穿插 → 高分
            if overlap_pct > 0.7:
                score += 9  # 高度重叠，空头衰竭
            elif overlap_pct > 0.5:
                score += 6
            elif overlap_pct > 0.3:
                score += 3
            else:
                score += 0  # 几乎无重叠，单边下杀
        else:
            score += 5  # 区间太短，中性

        # ③ 连续趋势K线 (最高9分) — 连续阴线少 = 空头弱 = 好
        bear_bars = (close_vals[s:e] < open_vals[s:e]).astype(int)
        max_consec_bear = 0
        current_streak = 0
        for b in bear_bars:
            if b:
                current_streak += 1
                max_consec_bear = max(max_consec_bear, current_streak)
            else:
                current_streak = 0

        if max_consec_bear >= 4:
            score += 0  # 连续4+阴线，空头强势
        elif max_consec_bear == 3:
            score += 3  # 连续3阴线，空头尚有
        elif max_consec_bear == 2:
            score += 6  # 最多连续2阴线，阴阳交替
        else:
            score += 9  # 最多1根阴线，极弱回调

        # ④ 空头高潮 (最高8分) — 竭尽式下跌后 Pin Bar 测试极值
        # 高潮特征: 大实体阴线(body > 1.5 ATR) 后跟 Pin Bar (长下影线, 收盘在上半部)
        has_atr = 'atr' in df.columns
        climax_score = 4  # 默认中性
        if has_atr and pb_len > 2:
            atr_vals = df['atr'].values
            for ci in range(s + 1, e - 1):
                # 大实体阴线 (空头高潮)
                body_bear = open_vals[ci] - close_vals[ci]
                atr_ci = atr_vals[ci]
                if atr_ci > 0 and body_bear > 1.5 * atr_ci:
                    # 下一根是否为 Pin Bar (长下影线测试极值)
                    ni = ci + 1
                    if ni < e:
                        bar_range_ni = high_vals[ni] - low_vals[ni]
                        if bar_range_ni > 0:
                            lower_wick = min(open_vals[ni], close_vals[ni]) - low_vals[ni]
                            close_pos = (close_vals[ni] - low_vals[ni]) / bar_range_ni
                            # Pin Bar: 下影线 > 50% bar range + 收盘在上半部
                            if lower_wick / bar_range_ni > 0.5 and close_pos > 0.5:
                                climax_score = 8  # 完美高潮 + Pin Bar
                                break
                            elif close_pos > 0.5:  # 有高潮但后续非 Pin Bar
                                climax_score = 6
        score += climax_score

        return min(35, score)

    def _calculate_quality_score_v36(self, df: pd.DataFrame, h0, l1, h1, tl,
                                     trend_depth: float = 0.0,
                                     signal_bar_quality: float = 0.0) -> float:
        """
        [V36.0] 七维度综合评分系统
        
        1. 结构精度      (15) — Fib 回撤比例
        2. 趋势线突破    (10) — EMA20 突破 + 均线缺口
        3. 反弹通道      (20) — L1→H1 缺口/重叠/连续K
        4. 回调通道质量  (35) — H1→TL 缺口/重叠/连续K/高潮
        5. 极值位置       (5) — TL vs L1
        6. 信号K质量     (10) — 纯形态
        7. 趋势深度       (5) — ATR 跌幅
        满分: 100
        """
        score = 0.0
        ema_vals = df['ema20'].values
        close_vals = df['close'].values
        low_vals = df['low'].values
        fall = h0.price - l1.price + 1e-9
        rise = h1.price - l1.price

        # ─── 1. 结构精度 (Max 15) ───
        retrace = rise / fall
        dist = abs(retrace - 0.5)
        if dist < 0.25:
            score += 15 * (1 - (dist / 0.25))
        else:
            score += 2  # 边际分

        # ─── 2. 趋势线突破 (Max 10) ───
        # H1 收盘 > EMA20 → 基础8分
        # H1 的 Low > EMA20 (均线缺口) → 满分10分
        h1_close = close_vals[h1.index]
        h1_low = low_vals[h1.index]
        h1_ema = ema_vals[h1.index]
        if h1_low > h1_ema:
            score += 10  # EMA 均线缺口 — 最强突破
        elif h1_close > h1_ema:
            score += 8   # 收盘突破 EMA 但未创缺口
        else:
            score += 2   # 未突破 EMA

        # ─── 3. 反弹通道 (Max 20) ───
        score += self._assess_rally_channel(df, l1, h1)

        # ─── 4. 回调通道质量 (Max 35) ───
        score += self._assess_pullback_quality(df, l1, h1, tl)

        # ─── 5. 极值位置 (Max 5) ───
        hl_pct = (tl.price - l1.price) / (fall + 1e-9)
        if hl_pct >= 0:
            if hl_pct < 0.1:
                score += 5  # Higher Low，紧贴前低
            else:
                score += 3  # Higher Low，偏高
        else:
            if abs(hl_pct) < 0.2:
                score += 2  # 微幅 Lower Low
            else:
                score += 0  # 大幅 Lower Low

        # ─── 6. 信号K质量 (Max 10) ───
        score += min(10, signal_bar_quality)

        # ─── 7. 趋势深度 (Max 5) ───
        if trend_depth >= 5.0:
            score += 3 + min(2, (trend_depth - 5.0) * 0.5)
        elif trend_depth >= 3.0:
            score += 2
        else:
            score += 1

        return round(score, 2)
