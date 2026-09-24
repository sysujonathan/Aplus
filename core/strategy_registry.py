from typing import Dict, Type, List, Any
from .strategies.base import BaseStrategy
from .strategies.mtr_strategy import MTRStrategy
from .strategies.three_k_strategy import ThreeKStrategy
from .strategies.structural_gap_strategy import StructuralGapStrategy
from .strategies.gap_pinbar_strategy import GapPinbarStrategy
from .strategies.gap_h2_strategy import GapH2Strategy
from .strategies.gap_h2_enhanced_strategy import GapH2EnhancedStrategy
from .strategies.awil_strategy import AWILStrategy
from .strategies.monthly_range_break_strategy import MonthlyRangeBreakStrategy

class StrategyRegistry:
    """
    Central registry for all trading strategies.
    
    P1 Enhancement: Metadata-driven strategy lookup.
    - _strategies now stores {'class': cls, 'metadata': dict} for self-description
    - get_metadata(name): returns strategy metadata without instantiation
    - get_strategies_by_timeframe(tf): returns strategies supporting a given timeframe
    - _resolve_class(name): internal lookup that extracts class from registry entry
    """
    
    # 内部映射表 (P1: 升级为包含 metadata 的结构)
    _strategies: Dict[str, Dict[str, Any]] = {
        "MTR_MASTER": {
            'class': MTRStrategy,
            'metadata': MTRStrategy.get_metadata(),
        },
        "STRATEGY_3K": {
            'class': ThreeKStrategy,
            'metadata': ThreeKStrategy.get_metadata(),
        },
        "STRATEGY_STRUCTURAL_GAP": {
            'class': StructuralGapStrategy,
            'metadata': StructuralGapStrategy.get_metadata(),
        },
        "STRATEGY_GAP_PINBAR": {
            'class': GapPinbarStrategy,
            'metadata': GapPinbarStrategy.get_metadata(),
        },
        "STRATEGY_GAP_H2": {
            'class': GapH2Strategy,
            'metadata': GapH2Strategy.get_metadata(),
        },
        "STRATEGY_GAP_H2_ENHANCED": {
            'class': GapH2EnhancedStrategy,
            'metadata': GapH2EnhancedStrategy.get_metadata(),
        },
        "STRATEGY_AWIL": {
            'class': AWILStrategy,
            'metadata': AWILStrategy.get_metadata(),
        },
        "STRATEGY_MONTHLY_RANGE_BREAK": {
            'class': MonthlyRangeBreakStrategy,
            'metadata': MonthlyRangeBreakStrategy.get_metadata(),
        },
    }
    
    # 官方对外展示列表（日线/周线扫描自动遍历的策略池）
    # 注意：STRATEGY_MONTHLY_RANGE_BREAK 不在此列——它是独立只读快照工具（tools/monthly_pinbar_snapshot.py）专用，
    # 不参与日线/周线扫描流水线。2026-09-03 证实：若列入会导致日线扫描结果混入"月线区间破位Pinbar"信号。
    _OFFICIAL_LIST = ["MTR_MASTER", "STRATEGY_3K", "STRATEGY_STRUCTURAL_GAP", "STRATEGY_GAP_PINBAR", "STRATEGY_GAP_H2", "STRATEGY_AWIL"]
    
    # =====================================================================
    # P1: Internal class resolution
    # =====================================================================
    @classmethod
    def _resolve_class(cls, name: str) -> Type[BaseStrategy]:
        """
        从注册表中解析策略类。
        支持精确匹配和别名模糊匹配。
        
        Args:
            name: 策略名称 (如 'MTR_MASTER', 'STRATEGY_3K')
        Returns:
            策略类 (Type[BaseStrategy])
        """
        name_upper = name.upper()
        
        # 1. 精确匹配
        if name_upper in cls._strategies:
            return cls._strategies[name_upper]['class']
        
        # 2. 别名模糊匹配 (兼容旧代码)
        if "MTR" in name_upper:
            return MTRStrategy
        if "3K" in name_upper:
            return ThreeKStrategy
        if "STRUCTURAL" in name_upper:
            return StructuralGapStrategy
        if "PINBAR" in name_upper or "GAP_PINBAR" in name_upper:
            return GapPinbarStrategy
        if "H2" in name_upper or "GAP_H2" in name_upper:
            return GapH2Strategy
        if "AWIL" in name_upper:
            return AWILStrategy
        
        # 3. 未知策略: 显式报错 (fail-fast), 不再静默回退 MTR
        #    —— 避免 CLI 拼错/误写策略名时"悄悄跑 MTR"而用户无感知 (P1-1 藏错)。
        raise KeyError(
            f"未知/不支持的策略名: {name!r} "
            f"(可用: {', '.join(cls._strategies.keys())})"
        )

    # =====================================================================
    # 原有接口 (保持向后兼容)
    # =====================================================================
    @classmethod
    def get_strategy(cls, name: str) -> BaseStrategy:
        """
        工厂方法：根据名称实例化策略。支持别名自动映射。
        """
        strategy_class = cls._resolve_class(name)
        return strategy_class()
    
    @classmethod
    def list_strategies(cls):
        """仅返回官方定义的策略，消除菜单污染"""
        return cls._OFFICIAL_LIST

    # =====================================================================
    # P1: 新增元数据驱动接口
    # =====================================================================
    @classmethod
    def get_metadata(cls, name: str) -> Dict[str, Any]:
        """
        获取指定策略的元数据 (无需实例化)。
        
        Args:
            name: 策略名称 (支持别名)
        Returns:
            dict: 包含 display_name, sl_column, entry_column 等字段
        """
        strategy_class = cls._resolve_class(name)
        return strategy_class.get_metadata()
    
    @classmethod
    def get_strategies_by_timeframe(cls, timeframe: str) -> List[str]:
        """
        返回支持指定时间周期的策略名称列表。
        
        Args:
            timeframe: 时间周期 ('daily' 或 'weekly')
        Returns:
            list: 策略名称列表 (如 ['STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2'])
        """
        tf = timeframe.lower()
        result = []
        for name, entry in cls._strategies.items():
            metadata = entry.get('metadata', {})
            supported = metadata.get('supported_timeframes', ['daily'])
            if tf in supported:
                result.append(name)
        return result
