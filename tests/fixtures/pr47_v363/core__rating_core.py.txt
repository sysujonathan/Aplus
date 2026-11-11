# -*- coding: utf-8 -*-
"""
core/rating_core.py — 跨策略通用 PA 因子库 (RATING_PLAN §4.x)

仅含纯价格行为因子 (config/sop_rules.md 16-Step SOP 可溯源).
⛔ 严禁 volume / ADX / EMA 斜率 / 均线多头排列 类因子.

各策略覆写 compute_rating 时, 复用本模块构造标准 RatingFactor,
保证跨策略口径一致 (评分公式各策略自留, 输出契约统一).

PA 因子分类:
  通用核心 (缺口家族 + 3K + AWIL + MTR 共享): 信号K质量 / RR / 连阴 / 时间衰减
  缺口家族专属签名: 回调速度 / 缺口宽度% (见 §2.5 白名单)
"""
from typing import Optional
from .rating import RatingFactor


def factor(name: str, value, hit: bool, weight: float,
           win_rate: Optional[float] = None, sop_ref: str = '', note: str = '') -> RatingFactor:
    """通用 RatingFactor 构造器 (所有因子经此统一封装, 强制带 sop_ref)."""
    return RatingFactor(name=name, value=float(value), hit=hit, weight=weight,
                        win_rate=win_rate, sop_ref=sop_ref, note=note)


# =====================================================================
# 跨策略通用 PA 核心因子
# =====================================================================

def quality_factor(q: float, sop_ref: str = 'SOP Step 4') -> RatingFactor:
    """信号K质量: close_loc/body_pct 综合 (Step 4 Signal Bar Quality)."""
    if q > 0.8:
        hit, weight = True, 1.0
    elif q < 0.5:
        hit, weight = False, -1.0
    else:
        hit, weight = True, 0.0
    return factor('信号K质量', q, hit, weight, sop_ref=sop_ref,
                  note='收盘强/实体大=买方控制' if weight >= 0 else '收盘弱/影线长=质量差')


def rr_factor(rr: float, min_rr: float = 2.0, sop_ref: str = 'SOP Step 9') -> RatingFactor:
    """交易者方程 RR (Step 9 Traders Equation). RR>=2 达标."""
    hit = rr >= min_rr
    weight = 1.0 if hit else 0.0
    return factor('盈亏比RR', rr, hit, weight, sop_ref=sop_ref,
                  note=f'RR≥{min_rr} 交易者方程成立' if hit else 'RR不足, 赔率不划算')


def consec_bear_penalty(n: int, sop_ref: str = 'SOP Step 7/Step 8') -> RatingFactor:
    """连阴扣分: 回调中连续阴线多=卖压未尽 (Step 7 连续K / Step 8 竭尽)."""
    hit = n < 3
    weight = -1.0 if n >= 3 else 0.0
    return factor('连阴数', n, hit, weight, sop_ref=sop_ref,
                  note='连阴≥3=卖压未尽' if weight < 0 else '连阴可控')


def time_decay_factor(bars_passed: int, sop_ref: str = 'SOP Step 12/Step 16') -> RatingFactor:
    """时间衰减: 信号后拖太久未走出来 扣分 (生命周期, 低权)."""
    if bars_passed > 10:
        weight = -2.0
    elif bars_passed > 5:
        weight = -1.0
    else:
        weight = 0.0
    return factor('时间衰减', bars_passed, weight >= 0, weight, sop_ref=sop_ref,
                  note='拖延过久=预期衰减' if weight < 0 else '新鲜信号')


# =====================================================================
# 缺口家族专属 PA 签名因子 (仅缺口家族 4.1-4.3 使用)
# =====================================================================

def pb_speed_factor(pb_bars: int, sop_ref: str = 'SOP Step 8/Step 3') -> RatingFactor:
    """回调速度: 快速回调(急跌急复=空头被套)加分, 拖延回调减分 (Step 8 陷阱/Step 3 真空)."""
    if pb_bars <= 4:
        hit, weight = True, 2.0
    elif pb_bars > 7:
        hit, weight = False, -2.0
    else:
        hit, weight = True, 0.0
    return factor('回调速度', pb_bars, hit, weight, sop_ref=sop_ref,
                  note='快速回调=空头被套' if weight > 0 else ('拖延回调=动能衰减' if weight < 0 else '回调速度中性'))


def gap_width_factor(gap_pct: float, sop_ref: str = 'SOP Step 6 Ind.2') -> RatingFactor:
    """缺口宽度%: 宽缺口=失衡/紧迫 加分 (Step 6 Ind.2 Body Gap). 缺口家族专属."""
    if gap_pct > 7:
        hit, weight = True, 2.0
    elif gap_pct < 3:
        hit, weight = False, -1.0
    else:
        hit, weight = True, 0.0
    return factor('缺口宽度%', gap_pct, hit, weight, sop_ref=sop_ref,
                  note='宽缺口=供需失衡' if weight > 0 else ('窄缺口=失衡弱' if weight < 0 else '缺口中性'))


def sum_weights(factors: list) -> float:
    """汇总因子贡献分 (用于构造 raw_score)."""
    return sum(f.weight for f in factors)
