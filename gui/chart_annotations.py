"""GUI 绘图适配层：接回冻结策略标注，并保持 Aplus 深色主题。"""
from __future__ import annotations

from dataclasses import dataclass
import logging

import pandas as pd

from .theme import ANNOTATION, BORDER, CONTROL_BG, TARGET


logger = logging.getLogger(__name__)


GAP_STRATEGIES = frozenset({
    "STRATEGY_STRUCTURAL_GAP",
    "STRATEGY_GAP_PINBAR",
    "STRATEGY_GAP_H2",
    "STRATEGY_GAP_H2_ENHANCED",
})
ANNOTATED_STRATEGIES = GAP_STRATEGIES | {
    "MTR_MASTER",
    "STRATEGY_AWIL",
    "STRATEGY_MONTHLY_RANGE_BREAK",
}


@dataclass(frozen=True, slots=True)
class MarkerSpec:
    column: str
    price_column: str
    multiplier: float
    marker: str
    size: float
    color: str
    label: str


@dataclass(frozen=True, slots=True)
class TrendSpec:
    column: str
    color: str
    linestyle: str
    width: float
    label: str


def marker_specs(strategy_type, meta):
    """返回策略明确声明的散点语义；不再把所有信号都画成同一颗星。"""
    if strategy_type == "MTR_MASTER":
        return [
            MarkerSpec("is_sw_h_geometric", "high", 1.015, "v", 48, "#58A6FF", "波段高点"),
            MarkerSpec("signal_mtr", "low", .98, "*", 95, TARGET, "MTR 信号"),
        ]
    if strategy_type == "STRATEGY_3K":
        return [
            MarkerSpec("signal_3k", "low", .98, "^", 58, "#F59E0B", "3K 信号"),
            MarkerSpec("signal_3k_gap_test", "low", .98, "*", 95, TARGET, "缺口测试确认"),
        ]
    if strategy_type in ANNOTATED_STRATEGIES:
        return []
    signal_column = (meta or {}).get("signal_column")
    return (
        [MarkerSpec(signal_column, "low", .98, "*", 95, TARGET, "策略信号")]
        if signal_column else []
    )


def trend_specs(strategy_type):
    if strategy_type == "MTR_MASTER":
        return [TrendSpec("geometric_trendline", "#8B949E", "--", 1.0, "几何趋势线")]
    return []


def owns_risk_lines(strategy_type):
    """GAP 与 AWIL 的专属标注已经画风险线，通用层不重复叠线。"""
    return strategy_type in GAP_STRATEGIES or strategy_type == "STRATEGY_AWIL"


def annotation_frame(frame):
    """冻结策略标注统一使用 mplfinance 的 0..N-1 横轴。"""
    return frame.tail(120).reset_index(drop=True).copy()


def gap_annotation_frame(frame, payload, strategy_type):
    """只读恢复归档缺口结构；不让冻结绘图器用最新突破或估算低点冒充原结构。"""
    prefixes = {
        'STRATEGY_STRUCTURAL_GAP': ('struct_gap', 'is_breakout'),
        'STRATEGY_GAP_PINBAR': ('gap_pinbar', 'is_breakout_gp'),
        'STRATEGY_GAP_H2': ('gap_h2', 'is_breakout_h2'),
        'STRATEGY_GAP_H2_ENHANCED': ('gap_h2', 'is_breakout_h2'),
    }
    prefix, bo_column = prefixes[strategy_type]
    anchor = str(payload.get('setup_date') or payload.get('asof') or '')[:10]
    data = frame.reset_index(drop=True).copy()
    matches = data.index[data.date.astype(str).str[:10] == anchor]
    if not len(matches):
        return None
    position = int(matches[0])
    exact_columns = [prefix + suffix for suffix in ('_floor_exact', '_prior_low', '_top_exact')]
    stop, target = _number(payload.get('stop')), _number(payload.get('target'))
    if all(column in data and pd.notna(data.loc[position, column]) for column in exact_columns):
        # 同日重算结构也要与归档计划口径一致，不能混用两份价位。
        floor, prior, top = (float(data.loc[position, column]) for column in exact_columns)
        expected_target = floor+top-prior if prefix == 'struct_gap' else 2*floor-prior
        if (stop is not None and target is not None
                and abs(floor-stop) <= .011 and abs(expected_target-target) <= .021):
            return data
    if stop is None or target is None or bo_column not in data:
        return None
    # 归档只保存价格，必须由真实突破窗口同时印证 Floor 和 MM，
    # 不能用 floor*0.98 或最新突破高点补造结构。
    candidates = []
    from config import settings
    lookback = getattr(settings, 'STRUCT_GAP_LOOKBACK', 60)
    for bo in data.index[data[bo_column].fillna(False).astype(bool)]:
        if bo > position or bo < lookback + 1:
            continue
        window = data.iloc[bo-lookback-1:bo-1]
        floor, prior = float(window.high.max()), float(window.low.min())
        top = float(data.iloc[bo:position].low.min()) if bo < position else None
        expected_target = floor + top - prior if prefix == 'struct_gap' and top is not None else 2*floor-prior
        if abs(floor-stop) <= .011 and abs(expected_target-target) <= .021 and top is not None:
            candidates.append((bo, floor, prior, top))
    if len(candidates) != 1:
        return None
    bo, floor, prior, top = candidates[0]
    if top <= floor:
        return None
    for column, value in zip(exact_columns, (floor, prior, top)):
        data.loc[position, column] = value
    entry = _number(payload.get('entry'))
    if entry is not None:
        column = 'entry_struct_gap' if prefix == 'struct_gap' else 'entry_' + prefix
        data.loc[position, column] = entry
    # 仅标注副本定位原突破，不能被原形态之后的突破抢走丈量窗口。
    data.loc[bo+1:position, bo_column] = False
    return data


def signal_context(strategy, frame, payload):
    """按原信号日提取信息框语义；关注图推进行情时不改写原计划。"""
    if strategy is None:
        return {}
    anchor = payload.get("asof") or payload.get("setup_date")
    source = frame
    if anchor and "date" in source:
        source = source.loc[source["date"].astype(str).str[:10] <= str(anchor)[:10]]
    if source.empty:
        return {}
    try:
        info = strategy.get_signal_info(source)
        return info if isinstance(info, dict) else {}
    except Exception:
        logger.warning(
            "signal_context failed: strategy=%s anchor=%s",
            type(strategy).__name__, anchor, exc_info=True,
        )
        return {}


def annotation_kwargs(payload, info):
    extra = info.get("extra_info") if isinstance(info.get("extra_info"), dict) else {}
    return {
        "sl_price": payload.get("stop") if payload.get("stop") is not None else info.get("sl"),
        "tp1": payload.get("target") if payload.get("target") is not None else info.get("tp1"),
        "anchor_signal_date": payload.get("setup_date") or payload.get("asof"),
        "ev_rating": extra.get("ev_rating", ""),
        "sig_quality": extra.get("sig_quality", 0),
        "bears": extra.get("pb_consec_bear", 0),
    }


def _number(value):
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _anchor_row(frame, payload):
    if frame is None or frame.empty:
        return None
    anchor = payload.get("asof") or payload.get("setup_date")
    if anchor and "date" in frame:
        matches = frame.loc[frame["date"].astype(str).str[:10] <= str(anchor)[:10]]
        if not matches.empty:
            return matches.iloc[-1]
    return frame.iloc[-1]


def info_panel_lines(payload, info, frame, open_gap_count=0):
    """恢复三层信息：计划价、形态质量、命中因子。"""
    entry = _number(payload.get("entry") if payload.get("entry") is not None else info.get("entry"))
    stop = _number(payload.get("stop") if payload.get("stop") is not None else info.get("sl"))
    target = _number(payload.get("target") if payload.get("target") is not None else info.get("tp1"))
    prices = []
    if entry is not None:
        prices.append(f"Entry {entry:.2f}")
    if stop is not None:
        prices.append(f"SL {stop:.2f}")
    if target is not None:
        prices.append(f"TP1 {target:.2f}")
    if entry is not None and stop is not None and target is not None and entry > stop:
        prices.append(f"({(target - entry) / (entry - stop):.2f}R)")

    extra = info.get("extra_info") if isinstance(info.get("extra_info"), dict) else {}
    row = _anchor_row(frame, payload)
    quality = _number(extra.get("sig_quality"))
    if quality is None and row is not None:
        for key in ("sig_bar_quality", "sig_bar_quality_h2", "sig_bar_quality_gp", "sig_bar_quality_awil"):
            if key in row and pd.notna(row.get(key)):
                quality = _number(row.get(key))
                break
    pb_bars = None
    if row is not None:
        for key in ("bars_since_breakout", "bars_since_breakout_h2", "bars_since_breakout_gp", "awil_pb_bars"):
            if key in row and pd.notna(row.get(key)):
                pb_bars = int(row.get(key))
                break
    rating = payload.get("rating") or info.get("rating") or {}
    rating_score = _number(rating.get("score")) if isinstance(rating, dict) else None
    quality_parts = []
    if quality is not None:
        quality_parts.append(f"Quality {quality:.2f}")
    if pb_bars is not None:
        quality_parts.append(f"PB bars {pb_bars}")
    if rating_score is not None:
        quality_parts.append(f"Rating {rating_score:.0f}")
    if open_gap_count:
        quality_parts.append(f"前序开放缺口 {open_gap_count}个")

    factor_parts = []
    if isinstance(rating, dict):
        factor_parts = [
            str(factor.get("name"))
            for factor in rating.get("factors", [])
            if isinstance(factor, dict) and factor.get("hit") and factor.get("name")
        ][:3]

    lines = []
    if prices:
        lines.append(" · ".join(prices))
    if quality_parts:
        lines.append(" · ".join(quality_parts))
    if factor_parts:
        lines.append("命中：" + " / ".join(factor_parts))
    return lines


def restyle_strategy_annotations(ax, strategy_type, previous=None):
    """Only strategy-created artists change color; candles retain red/green."""
    for text in ax.texts:
        text.set_color(ANNOTATION)
        patch = text.get_bbox_patch()
        if patch is not None:
            patch.set_facecolor(CONTROL_BG)
            patch.set_edgecolor(BORDER)
            patch.set_alpha(.92)
        arrow = getattr(text, 'arrow_patch', None)
        if arrow is not None:
            arrow.set_color(ANNOTATION)
    if previous is not None:
        for artist in [*ax.lines, *ax.collections, *ax.patches]:
            if id(artist) in previous:
                continue
            if hasattr(artist, 'set_color'):
                artist.set_color(ANNOTATION)
            else:
                artist.set_edgecolor(ANNOTATION)
                artist.set_facecolor(ANNOTATION)
