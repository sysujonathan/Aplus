"""K 线工作区的统一只读视图模型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


ChartMode = Literal["candidate", "watch"]


@dataclass(frozen=True, slots=True)
class ChartItem:
    """明确记录一格 K 线来自哪个列表和哪类行情。"""

    code: str
    name: str
    observation_id: str
    source: str
    mode: ChartMode
    strategy: str | None = None
    timeframe: str | None = None
    market_dataset_id: str | None = None
    market_asof: str | None = None
    anchor_asof: str | None = None


def chart_items(rows, mode: ChartMode):
    """把候选或关注行转换为 Chart 明确接受的统一模型。"""
    result = []
    for row in rows or []:
        value: Mapping = row
        observation_id = value.get("observation_id")
        if not observation_id:
            continue
        result.append(
            ChartItem(
                code=str(value.get("code") or ""),
                name=str(value.get("name") or ""),
                observation_id=str(observation_id),
                source=str(value.get("source") or "unknown"),
                mode=mode,
                strategy=value.get("strategy"),
                timeframe=value.get("timeframe"),
                market_dataset_id=value.get("market_dataset_id"),
                market_asof=value.get("market_asof"),
                anchor_asof=value.get("anchor_asof"),
            )
        )
    return result
