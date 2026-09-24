from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
import logging

from core.rating import RatingResult, RatingFactor, band, band_calibrated, map_native, clamp

logger = logging.getLogger(__name__)

class BaseStrategy(ABC):
    """
    Abstract Base Class for Al Brooks Strategies.
    All strategies must implement these methods to be usable by Hunter/Guardian.
    
    Self-Describing Interface (P1):
      - get_metadata():  Declares column names, display name, supported timeframes
      - get_signal_info(): Extracts signal data from a computed DataFrame
      - annotate_chart(): Renders strategy-specific chart annotations
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy Name (e.g., 'MTR_V1')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Brief description for UI/Logs"""
        pass

    @abstractmethod
    def format_prompt(self, context_data: Dict) -> str:
        """
        Generate the specific prompt for this strategy.
        Args:
            context_data: Dict containing 'code', 'df', 'ctx' (common data)
        Returns:
            str: The full prompt text
        """
        pass

    @abstractmethod
    def parse_result(self, response_text: str) -> Dict:
        """
        Parse the specific XML tags returned by this strategy.
        Returns:
            Dict: Parsed result (verdict, reason, etc.)
        """
        pass

    # =====================================================================
    # Self-Describing Interface (P1)
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """
        Declare this strategy's column names, display name, and supported timeframes.
        
        Subclasses MUST override to provide accurate column mappings.
        Default implementation returns empty placeholders for backward compatibility.
        
        Returns:
            dict with keys:
                - display_name: str        — Chinese display name (e.g., 'MTR反转')
                - sl_column: str           — Stop-loss column name in DataFrame
                - entry_column: str        — Entry price column name
                - tp_columns: List[str]    — Take-profit column names
                - score_column: str        — Quality/score column name (empty string if none)
                - signal_column: str       — Signal flag column name (e.g., 'signal_mtr')
                - supported_timeframes: List[str] — e.g., ['daily'] or ['daily', 'weekly']
                - ai_audit: bool           — 是否需经 AI 二次审计 (False=结构/动能信号,
                                              跳过 AI 直接入池; True=送 AI 审计。默认 True)
                - bars_since_breakout_column: str — 周线专用: 突破后经过的 bar 数 列名 (空=未声明)
                - gap_top_exact_column: str      — 周线专用: 测量缺口精确顶 列名 (空=未声明)
        """
        return {
            'display_name': '策略',
            'sl_column': '',
            'entry_column': '',
            'tp_columns': [],
            'score_column': '',
            'signal_column': '',
            'supported_timeframes': ['daily'],
            'ai_audit': True,
            'bars_since_breakout_column': '',
            'gap_top_exact_column': '',
        }

    @classmethod
    def compute_rating(cls, df: pd.DataFrame, timeframe: str = 'daily') -> Optional['RatingResult']:
        """
        [RATING_PLAN Phase 0] 计算信号评级 (统一输出契约).

        默认实现: 退化评级 (从 score_column 取质量分 0-1 -> 0-100 映射)。
        各策略 MUST 覆写此方法, 以产出基于自身 PA 签名的数据驱动 RatingResult。
        返回值经 to_dict() 由 scanner/hunter 统一消费 (res['info']['rating'])。

        ⛔ PA 约束: 任何覆写不得引入 volume/ADX/EMA斜率 类非 PA 因子。
        """
        meta = cls.get_metadata()
        score_col = meta.get('score_column', '')
        q = 0.0
        try:
            if score_col and score_col in df.columns and df is not None and not df.empty:
                val = df.iloc[-1].get(score_col, np.nan)
                if pd.notna(val):
                    q = float(val)
        except Exception:
            q = 0.0
        score = clamp(round(q * 100))
        letter = band_calibrated(cls, score, timeframe=timeframe)
        factors = [RatingFactor(
            name='信号K质量(退化)', value=q, hit=q > 0.5, weight=0.0,
            sop_ref='SOP Step 4',
            note='默认退化评级 — 该策略未覆写 compute_rating, 请用真实 PA 因子替换')]
        return RatingResult(raw_score=q, score=score, letter=letter,
                            factors=factors, calibrated=False)

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Extract signal-specific data from a DataFrame that has already been
        processed by calculate_signals().
        
        The returned dict normalises column names so that consumers (scanner, 
        notifier, weekly scanner) never need to know strategy-specific column names.
        
        Subclasses MAY override to add extra_info (e.g., 3K gap test info).
        
        Returns:
            dict with keys (all optional, absent = not applicable):
                - sl: float               — Stop-loss price from last row
                - entry: float            — Entry price from last row
                - tp1: float              — First take-profit price
                - tp2: float              — Second take-profit price (if exists)
                - score: float            — Signal quality score
                - extra_info: dict        — Strategy-specific附加信息
        """
        meta = cls.get_metadata()
        result: Dict[str, Any] = {}
        
        if df is None or df.empty:
            return result
            
        row = df.iloc[-1]
        
        # SL
        sl_col = meta.get('sl_column', '')
        if sl_col and sl_col in df.columns:
            val = row.get(sl_col, np.nan)
            if pd.notna(val):
                result['sl'] = float(val)
        
        # Entry
        entry_col = meta.get('entry_column', '')
        if entry_col and entry_col in df.columns:
            val = row.get(entry_col, np.nan)
            if pd.notna(val):
                result['entry'] = float(val)
        
        # Take-profit columns
        tp_cols = meta.get('tp_columns', [])
        for i, tp_col in enumerate(tp_cols):
            if tp_col and tp_col in df.columns:
                val = row.get(tp_col, np.nan)
                if pd.notna(val):
                    result[f'tp{i+1}'] = float(val)
        
        # Score
        score_col = meta.get('score_column', '')
        if score_col and score_col in df.columns:
            val = row.get(score_col, np.nan)
            if pd.notna(val):
                result['score'] = float(val)
        
        # 评级 (RATING_PLAN Phase 0): 统一经 compute_rating 产出, 注入 extra_info
        try:
            rating_result = cls.compute_rating(df)
            if rating_result is not None:
                result['rating'] = rating_result.to_dict()
        except Exception as e:
            logger.warning(f"compute_rating failed in get_signal_info for {cls.__name__}: {e}")

        return result

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> None:
        """
        Render strategy-specific chart annotations onto a matplotlib Axes.
        
        Subclasses MUST override to provide visual annotations.
        Default implementation is a no-op (safe fallback).
        
        Args:
            ax: matplotlib Axes object (from mpf.plot returnfig)
            plot_df: DataFrame slice used for plotting (already Datetime-indexed)
            strategy_type: Strategy name string (for compatibility checks)
            **kwargs: Additional strategy-specific parameters:
                - sl_price: float         — Stop-loss price for horizontal lines
                - tp1: float              — First take-profit price
                - tp2: float              — Second take-profit price
                - ev_rating: str          — EV rating label
                - sig_quality: float       — Signal bar quality score
                - bears: int              — Consecutive bear bars in pullback
        """
        pass
