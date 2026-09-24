import re
import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional
from .base import BaseStrategy
from core.formatter import get_common_context
from config import settings
from core.rating import RatingResult, RatingFactor, clamp, band, band_calibrated, is_calibration_available
from .geometric_engine import GeometricTrendlineEngine 
from core.strategies.mtr_structural_v35 import MTRStructuralEngineV35

logger = logging.getLogger(__name__)

class MTRStrategy(BaseStrategy):
    """
    [Strategy] Major Trend Reversal (MTR) V30.0 - Al Brooks Physical Master.
    
    Evolution:
    - V29.5: Selective Surprise + 0.23R Expectancy.
    - V30.0: Structural Alignment with the "Revised Logic Diagram".
    
    Architecture:
    1. Structural Layer: Trend -> MLH -> Break -> HL (V30.0).
    2. Filter Layer: Surprise Bar + EMA + Body (V29.5).
    3. Signal Layer: Final Conquest & Confirmation.
    """
    
    @property
    def name(self) -> str:
        return "MTR_V35_STRUCTURAL"

    MIN_BARS = 30
    LOOKBACK_MAJOR = 60
    MEAN_PERIOD_20 = 20

    @property
    def description(self) -> str:
        return "MTR V35.0 5-Point Structural Sequence (Fibonacci Strict)"

    @property
    def signal_column(self) -> str:
        return 'signal_mtr'

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """MTR 策略元数据声明"""
        return {
            'display_name': 'MTR',
            'sl_column': 'sl_price',
            'entry_column': 'entry_price',
            'tp_columns': ['tp1_price', 'tp2_price'],
            'score_column': 'mtr_score',
            'signal_column': 'signal_mtr',
            'supported_timeframes': ['daily'],
            'tp_multiplier': 2.0,
            'ai_audit': False,
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """MTR 信号信息提取 (使用基类默认实现即可, 无额外信息)"""
        return super().get_signal_info(df)

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'daily') -> Optional['RatingResult']:
        """[RATING_PLAN §4.4] MTR 评级: 包七维综合分 mtr_score(0-100).
        ⚠️ Phase 0 直接包手调七维权重; 维度2(EMA20突破)权重待 Phase 2 下调至低档
           (Step 7 MA_Gap 翻转证据, 非动量因子); calibrated=False。
        """
        if df is None or df.empty:
            return None
        meta = cls.get_metadata()
        score_col = meta.get('score_column', 'mtr_score')
        raw = 0.0
        if score_col in df.columns:
            val = df.iloc[-1].get(score_col, np.nan)
            if pd.notna(val):
                raw = float(val)
        score = clamp(round(raw))
        toxic = score < 30
        letter = band_calibrated(cls, score, toxic=toxic, timeframe=timeframe)
        factors = [RatingFactor(
            name='七维综合评分', value=raw, hit=raw >= 50, weight=0.0,
            sop_ref='SOP Step 1/2/4/5/7/11',
            note='七维手调加权(结构15+趋势10+反弹20+回调35+极值5+信号K10+深度5); '
                 '维度2 EMA权重待Phase2下调至低档(MA_Gap翻转证据, 非动量因子)')]
        return RatingResult(raw_score=raw, score=score, letter=letter, factors=factors,
                            toxic=toxic, calibrated=is_calibration_available())

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> None:
        """MTR 教科书式标注 — Sell Climax, First Leg, Signal HL"""
        try:
            if 'signal_mtr' not in plot_df.columns:
                return
            sig_mask = plot_df['signal_mtr']
            if not sig_mask.any():
                return
            
            signal_date = sig_mask[sig_mask].index[-1]
            signal_price = plot_df.loc[signal_date]['low']
            
            # 极值低点 (Climax Low) - 在信号前
            pre_signal = plot_df.loc[:signal_date]
            climax_date = pre_signal['low'].idxmin()
            climax_price = pre_signal.loc[climax_date]['low']
            
            # 第一腿高点 (Leg 1 Peak) - 在低点和信号之间
            if climax_date != signal_date:
                leg1_range = plot_df.loc[climax_date:signal_date]
                leg1_date = leg1_range['high'].idxmax()
                leg1_price = leg1_range.loc[leg1_date]['high']
            else:
                leg1_date = climax_date
                leg1_price = climax_price * 1.05

            # Stage 2: Sell Climax
            ax.annotate("2. Sell Climax\n(抛售高潮)", 
                        xy=(climax_date, climax_price), 
                        xytext=(climax_date, climax_price * 0.90),
                        arrowprops=dict(arrowstyle="->", color='red'),
                        fontsize=9, color='red', ha='center', va='top')

            # Stage 3: First Leg Breakout
            ax.annotate("3. First Leg\n(一腿突破)", 
                        xy=(leg1_date, leg1_price), 
                        xytext=(leg1_date, leg1_price * 1.10),
                        arrowprops=dict(arrowstyle="->", color='blue'),
                        fontsize=9, color='blue', ha='center', va='bottom')
                        
            # Stage 4: Signal (Higher Low)
            ax.annotate("4. SIGNAL (HL)\n(次低点确认)", 
                        xy=(signal_date, signal_price), 
                        xytext=(signal_date, signal_price * 0.90),
                        arrowprops=dict(arrowstyle="->", color='green', linewidth=2),
                        fontsize=10, color='green', fontweight='bold', ha='center', va='top',
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='#e6ffe6', edgecolor='green'))
        except Exception as e:
            logger.warning(f"MTR Annotation Failed: {e}")

    def __init__(self):
        self.structural_engine = MTRStructuralEngineV35()

    def _calculate_spirit_context_v29(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        [Phase1 精简] 保留 trend_depth 和 dist_ema 计算。
        
        - trend_depth: 被 calculate_signals() L248 用于 "Major Trend" 门槛过滤
        - dist_ema: 被 _calculate_context() / format_prompt() 用于 AI 审计报告
        
        原 V29 中的 is_sell_climax_raw / bear_dominance / is_prior_bear 已被 V35 结构引擎替代，不再需要。
        原 _detect_swing_v24 / _detect_mtr_structural_v30 / _validate_blueprint 方法已废弃并删除。
        """
        atr = df['atr'] + 1e-9
        # EMA20 已在 calculate_signals() 开头计算，此处避免重复
        if 'ema20' not in df.columns:
            df['ema20'] = df['close'].ewm(span=self.MEAN_PERIOD_20, adjust=False).mean()
        
        # dist_ema: 用于 AI 审计 prompt
        df['dist_ema'] = (df['ema20'] - df['low']) / atr
        
        # trend_depth: 120 根内最大跌幅 (ATR 倍数)，用于判定 "Major" 门槛
        lookback_h = df['high'].rolling(120).max()
        df['trend_depth'] = (lookback_h - df['low']) / atr
        
        return df

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range manually if missing"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = np.maximum(high - low, np.maximum(abs(high - close), abs(low - close)))
        return tr.rolling(period).mean()

    def _apply_geometric_logic_v31(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        [V31.0 Core] Geometric Trendline Engine.
        Calculates true bear trendlines and physical breakouts.
        """
        # Ensure ATR exists
        if 'atr' not in df.columns:
            df['atr'] = self._calculate_atr(df) # Helper or use existing if any
            
        engine = GeometricTrendlineEngine(
            atr_window=14,
            swing_window=3,        # 🟢 Al Brooks typically uses smaller fractals (3-5 bars)
            break_threshold=0.3    # 🟢 0.5 ATR might be too strict for A-shares
        )
        
        # 1. Identify Global Swings (Vectorized helper)
        swing_highs, swing_lows = engine.find_swing_points(df)
        swing_points = engine.identify_swing_objects(df, swing_highs, swing_lows)
        
        # Store swings for visualization
        df['is_sw_h_geometric'] = swing_highs
        df['is_sw_l_geometric'] = swing_lows
        
        # 🟢 [Optimized] Remove explicit index to avoid FutureWarning (Relies on reset index)
        trendline_vals = pd.Series([np.nan] * len(df), dtype=float)
        is_break = pd.Series([False] * len(df), dtype=bool)
        
        # Minimum history to form a trend
        start_idx = 50 
        
        for i in range(start_idx, len(df)):
            idx_label = df.index[i]
            
            # Find best trendline existing up to this point
            trendline = engine.find_bear_trendline(df, swing_points, idx_label)
            
            if trendline:
                # Calculate value at current bar
                val = engine.calculate_trendline_value(df, trendline, idx_label)
                trendline_vals.iloc[i] = val
                
                # Check for Breakout
                if engine.check_trendline_break(df, idx_label, trendline):
                    is_break.iloc[i] = True

        df['geometric_trendline'] = trendline_vals
        df['is_break_geometric'] = is_break
        return df

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # --- 0. Setup ---
        df['ema20'] = df['close'].ewm(span=self.MEAN_PERIOD_20, adjust=False).mean()
        df['range'] = df['high'] - df['low'] + 1e-9
        df['body_pct'] = (df['close'] - df['open']).abs() / df['range']
        df['close_pos'] = (df['close'] - df['low']) / df['range']
        
        # Ensure ATR exists
        if 'atr' not in df.columns:
            df['atr'] = self._calculate_atr(df)
            
        # --- V32.0/V35.0 Structural Sequence Engine ---
        # 1. 预计算场景背景 (V29/V30 物理要素作为辅助)
        df = self._calculate_spirit_context_v29(df)
        
        # 2. 识别波段点
        swings = self.structural_engine.find_swing_points(df)
        
        # 3. 初始化输出字段
        df['signal_mtr'] = False
        df['mtr_score'] = 0.0
        df['mtr_stage'] = 'NONE'
        
        # 🟢 [Optimization] Pre-init MTR result columns to avoid df.at dynamic creation warning
        for pt_name in ['H0', 'L1', 'H1', 'TL', 'H2']:
            df[f'mtr_{pt_name}_price'] = np.nan
            df[f'mtr_{pt_name}_idx'] = np.nan
        # [V35.2] 信号K线和入场价
        df['mtr_signal_bar_idx'] = np.nan
        df['mtr_entry_price'] = np.nan
        df['mtr_signal_bar_quality'] = np.nan
        
        # 4. 跳级扫描 (仅在摆动点确认位置运行)
        swing_check_indices = set([s.index for s in swings] + [s.index + 5 for s in swings])
        # 补充：在最新 K 线也进行一次扫描
        swing_check_indices.add(len(df) - 1)
        
        for i in range(len(df)):
            if i not in swing_check_indices:
                continue
                
            res = self.structural_engine.match_mtr_pattern(df, swings, i)
            if res:
                # 🟢 [V35.0 Upgrade] Context Filter
                # Ensure it is a "Major" Trend Reversal
                # Check Trend Depth at L1 point (> 5.0 ATR drop from recent high)
                points = res.get('points', {})
                l1_pt = points.get('L1')
                if l1_pt:
                    # 使用预计算的 trend_depth 字段
                    depth_at_l1 = df['trend_depth'].iloc[l1_pt.index]
                    if depth_at_l1 < 5.0:
                        # Reject: Not a "Major" trend (Drop < 5 ATR is just a pullback in range)
                        continue

                df.at[df.index[i], 'mtr_score'] = res['score']
                df.at[df.index[i], 'mtr_stage'] = res['stage']
                
                if res['stage'] == 'SETUP_READY':
                    # [V35.2] signal_mtr 标记在信号K线位置，不是检测位置
                    signal_bar = res.get('signal_bar')
                    if signal_bar and signal_bar['idx'] < len(df):
                        sb_idx = signal_bar['idx']
                        # 检测位置(detection bar)记录内部索引和质量
                        df.at[df.index[i], 'mtr_signal_bar_idx'] = sb_idx
                        df.at[df.index[i], 'mtr_entry_price'] = signal_bar['high']
                        df.at[df.index[i], 'mtr_signal_bar_quality'] = signal_bar.get('quality', 0)

                        # 🟢 [Bugfix] 把信号信息同步写到信号K线位置，因为下游
                        #    run_scanner_all/绘图/归档读取的是 signal_mtr=True 的那一行。
                        df.at[df.index[sb_idx], 'signal_mtr'] = True
                        df.at[df.index[sb_idx], 'mtr_score'] = res['score']
                        df.at[df.index[sb_idx], 'mtr_stage'] = res['stage']
                        df.at[df.index[sb_idx], 'mtr_entry_price'] = signal_bar['high']
                        # 结构价格也复制到信号K线，供下游计算 SL/TP 与绘图标注
                        for name, pt in res['points'].items():
                            if pt:
                                df.at[df.index[sb_idx], f'mtr_{name}_price'] = pt.price
                                df.at[df.index[sb_idx], f'mtr_{name}_idx'] = pt.index
                    else:
                        # 无信号K时仍标记当前位置（向后兼容）
                        df.at[df.index[i], 'signal_mtr'] = True
                
                # 记录关键坐标用于图表渲染
                for name, pt in res['points'].items():
                    if pt:
                        df.at[df.index[i], f'mtr_{name}_price'] = pt.price
                        df.at[df.index[i], f'mtr_{name}_idx'] = pt.index

        # --- 5. Risk & Target Management [V35.2] ---
        #     入场价 = 信号K的 high (buy stop order)
        #     SL = min(L1, TL) - 1 tick
        #     TP = 入场价 + 2R
        mask = (df['mtr_stage'] != 'NONE')
        if mask.any():
            def get_sl(row):
                l1 = row.get('mtr_L1_price', np.nan)
                tl = row.get('mtr_TL_price', np.nan)
                # 🟢 [UX] L1/TL 均未识别时两值均为 NaN, 直接返回 NaN(下游跳过),
                #    避免 np.nanmin([nan,nan]) 刷 RuntimeWarning 并连带打印源码行, 淹没终端有效信息.
                if pd.isna(l1) and pd.isna(tl):
                    return np.nan
                extreme = np.nanmin([l1, tl])
                return extreme - 0.01 if pd.notna(extreme) else np.nan
            
            df.loc[mask, 'sl_price'] = df[mask].apply(get_sl, axis=1)
            
            # [V35.2] 入场价优先用信号K的 high，回退到 close
            entry = df['mtr_entry_price'].where(df['mtr_entry_price'].notna(), df['close'])
            risk = entry - df['sl_price']
            df.loc[mask, 'entry_price'] = entry
            # TP1 为 2R (Brooks 标准止盈)
            df.loc[mask, 'tp1_price'] = entry + 2 * risk
            # TP2 为 3R (扩展止盈)
            df.loc[mask, 'tp2_price'] = entry + 3 * risk
            
        return df

    def _calculate_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        try:
            latest = df.iloc[-1]
            return {
                'stage': latest.get('mtr_stage', "NONE"),
                'dist_ema': latest.get('dist_ema', 0),
                'leg1_str': latest.get('leg1_strength', 0),
                'tp1': latest.get('tp1_price', 0),
                'sl': latest.get('sl_price', 0),
                'is_confirmed': latest.get('is_confirmed', False)
            }
        except Exception:return {}

    def format_prompt(self, context_data: Dict) -> str:
        df = context_data['df']
        physics = self._calculate_context(df)
        
        from core.formatter import get_common_context, load_sop_rules
        ctx = get_common_context(df)
        sop = load_sop_rules()

        prompt = f"""
# 👤 角色：Al Brooks Price Action 分析师
你是一位客观的 Price Action 分析师，严格基于 Al Brooks 的价格行为学理论进行评估。
**严禁使用"量价背离"、"MACD"等非 PA 术语。**

# 🔧 系统说明
Python 引擎已完成以下验证（无需你重复质疑）：
- ✅ H0→L1→H1→TL 五点结构已识别并通过斐波那契回撤校验
- ✅ 七维度综合评分: **{df.iloc[-1].get('mtr_score', 0):.1f} / 100**（≥50分才会提交给你审核）
- ✅ Buy Stop 入场价已定位在信号K线的 High

**你的职责**是评估以下两个维度，给出客观判断：

# 📋 评估维度
## 1. 信号K线与入场质量 (Signal Bar Quality)
- 信号K线是否为强势阳线（收盘在上半部）？
- 有无跟进力量（Follow-through）的早期迹象？
- 入场后到 TP1 之间是否有明显阻力（如前高、密集成交区）？

## 2. 市场环境与盈亏比 (Context & Trader's Equation)
- 前期趋势是否已充分发展（非 Trading Range 中的一条腿）？
- H1 反弹的通道质量如何？（连续阳线 = 强；震荡横盘 = 弱）
- TL 回调的性质？（弱势缩量回调 = 利好；凶猛下跌 = Bear Flag 风险）
- Reward:Risk 是否 ≥ 2:1？

# 📊 量化数据
- **当前阶段**: {physics.get('stage')}
- **物理止损位**: {physics.get('sl', 0):.2f}
- **突破倾角强度**: {physics.get('leg1_str', 0):.2f} ATR
- **七维度得分**: {df.iloc[-1].get('mtr_score', 0):.1f} / 100

# 🔍 K线数据 (最近 60 天)
{ctx['csv_str']}

# 📝 请输出 (XML 格式)
<ANALYSIS>
1. 信号K线评估: (强/中/弱，理由)
2. 市场环境: (趋势充分度、通道质量)
3. 盈亏比计算: (具体数值)
</ANALYSIS>
<PA_TAGS>使用中文 Brooks 术语，如：更高低点, 趋势线突破, 双重底, 通道回调, 强势突破K线</PA_TAGS>
<VERDICT>PASS / FAIL</VERDICT>
<DISCORD>一句话结论</DISCORD>

<PLAN>
入口: {df.iloc[-1].get('entry_price', df.iloc[-1].get('mtr_entry_price', df.iloc[-1]['close'])):.2f} | 止损: {physics.get('sl', 0):.2f} | 目标: {physics.get('tp1', 0):.2f}
</PLAN>
"""
        return prompt

    def parse_result(self, response_text: str) -> Dict:
        from core.formatter import parse_response
        parsed = parse_response(response_text)
        tags_match = re.search(r"<PA_TAGS>(.*?)</PA_TAGS>", response_text, re.DOTALL | re.IGNORECASE)
        if tags_match:
            parsed['pa_tags'] = tags_match.group(1).strip()
            parsed['reason'] = parsed['pa_tags']
        return parsed
