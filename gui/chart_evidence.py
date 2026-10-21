"""Read-only vector adapter for the existing strategy annotation contract.

Call each frozen strategy's own annotation method on its historical signal
prefix. Serialize date/price anchors, never pixels or a shared generic marker.
"""
from __future__ import annotations

import math

import pandas as pd

from .chart_annotations import (GAP_STRATEGIES, annotation_kwargs, gap_annotation_frame,
    info_panel_lines, marker_specs, owns_risk_lines, restyle_strategy_annotations,
    signal_context, trend_specs)
from .chart_renderer import serialized_render
from .theme import STOP, TARGET


@serialized_render
def strategy_evidence(context):
    strategy = context.get('strategy')
    kind = context.get('strategy_type') or ''
    payload = context.get('payload') or {}
    if strategy is None or payload.get('plan_kind') == 'gap-h2-next-session':
        return {'items': [], 'facts': []}  # H2 uses its dedicated plan adapter.
    from matplotlib.figure import Figure
    from matplotlib.colors import to_hex
    from matplotlib.patches import Rectangle
    from matplotlib.text import Annotation
    from matplotlib.collections import LineCollection, PathCollection

    source = context['frame'].reset_index(drop=True).copy()
    anchor = str(payload.get('setup_date') or payload.get('asof') or source.date.iloc[-1])[:10]
    source = source.loc[source.date.astype(str).str[:10] <= anchor].reset_index(drop=True)
    if source.empty:
        return {'items': [], 'facts': ['原信号不在当前行情中，结构待核对']}
    meta = context.get('metadata')
    if meta is None:
        meta = strategy.get_metadata()
    info = signal_context(strategy, source, payload)
    facts = info_panel_lines(payload, info, source)
    if kind in GAP_STRATEGIES:
        source = gap_annotation_frame(source, payload, kind)
        if source is None:
            items = []
            for key, label, color in [('stop', 'SL', STOP), ('target', 'TP1', TARGET)]:
                value = payload.get(key)
                if value is not None and math.isfinite(float(value)):
                    items.append(dict(kind='hline', value=float(value), text=label,
                                      color=color, dash='--', width=1, alpha=.7))
            return {'items': items,
                    'facts': [*facts, '原信号结构待核对（保留归档计划价格）'],
                    'availableDate': anchor}
    dates = source.date.astype(str).str[:10].tolist()

    def point(x, y):
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        index = max(0, min(len(dates)-1, math.floor(x)))
        return dict(date=dates[index], value=y, offset=x-index)

    items = []
    fig = Figure(figsize=(12, 7))
    ax = fig.subplots()
    try:
        ax.set_xlim(-.5, len(source)-.5)
        ax.set_ylim(float(source.low.min())*.85, float(source.high.max())*1.15)
        annotation_result = strategy.annotate_chart(
            ax, source, kind, **annotation_kwargs(payload, info)
        )
        if isinstance(annotation_result, int) and not isinstance(annotation_result, bool):
            facts = info_panel_lines(payload, info, source, open_gap_count=annotation_result)
        restyle_strategy_annotations(ax, kind, set())
        for line in ax.lines:
            x, y = line.get_xdata(), line.get_ydata()
            style = dict(color=to_hex(line.get_color()), width=float(line.get_linewidth()),
                         dash=line.get_linestyle(),
                         alpha=line.get_alpha() if line.get_alpha() is not None else 1)
            if line.get_transform() == ax.get_yaxis_transform():
                items.append(dict(kind='hline', value=float(y[0]), **style))
            else:
                points = [point(a, b) for a, b in zip(x, y)]
                items.append(dict(kind='line', points=[p for p in points if p], **style))
        for patch in ax.patches:
            if not isinstance(patch, Rectangle):
                raise ValueError(f'策略图元尚未适配：{type(patch).__name__}')
            a = point(patch.get_x(), patch.get_y())
            b = point(patch.get_x()+patch.get_width(), patch.get_y()+patch.get_height())
            if a and b:
                items.append(dict(kind='rectangle', a=a, b=b, color=to_hex(patch.get_edgecolor()),
                    width=float(patch.get_linewidth()), dash=patch.get_linestyle(),
                    alpha=patch.get_alpha() if patch.get_alpha() is not None else 1,
                    fill=to_hex(patch.get_facecolor()) if patch.get_fill() and patch.get_facecolor()[3] else None))
        for text in ax.texts:
            if kind in GAP_STRATEGIES and 'GAP_H2' not in kind and text.get_text() == 'H2':
                continue
            position = point(*text.get_position())
            arrow = point(*text.xy) if isinstance(text, Annotation) else None
            if position:
                items.append(dict(kind='label', text=text.get_text(), a=arrow or position,
                    b=position, color=to_hex(text.get_color()), size=float(text.get_fontsize()),
                    align=text.get_horizontalalignment(), vertical=text.get_verticalalignment(),
                    leader=bool(arrow and text.arrow_patch)))
        explicit_markers = marker_specs(kind, meta)
        for collection in ax.collections:
            if isinstance(collection, LineCollection):
                # hlines/vlines create LineCollections, not scatter markers.
                # Keep each finite segment's date/price endpoints and styling.
                if collection.get_transform() != ax.transData:
                    raise ValueError('策略线段使用了尚未适配的坐标系')
                colors = collection.get_colors()
                widths = collection.get_linewidths()
                dashes = collection.get_linestyles()
                for index, segment in enumerate(collection.get_segments()):
                    points = [point(x, y) for x, y in segment]
                    if len(points) < 2 or any(p is None for p in points):
                        continue
                    color = colors[index % len(colors)]
                    dash = dashes[index % len(dashes)][1]
                    items.append(dict(kind='line', points=points, color=to_hex(color),
                        width=float(widths[index % len(widths)]),
                        dash=[float(value) for value in dash] if dash is not None else '-',
                        alpha=collection.get_alpha() if collection.get_alpha() is not None else float(color[3])))
            elif isinstance(collection, PathCollection):
                if not explicit_markers:
                    raise ValueError(f'策略散点图元尚未适配：{kind or "未知策略"}')
            else:
                raise ValueError(f'策略图元尚未适配：{type(collection).__name__}')
        # Scatter artists are recreated from the explicit per-strategy marker
        # contract below. Never guess every collection is a monthly breakout.
        for marker in explicit_markers:
            if marker.column in source:
                for index in source.index[source[marker.column].fillna(False).astype(bool)]:
                    p = point(index, float(source.loc[index, marker.price_column])*marker.multiplier)
                    if p:
                        items.append(dict(kind='marker', a=p, marker=marker.marker,
                                          color=marker.color, size=math.sqrt(marker.size), text=marker.label))
        for trend in trend_specs(kind):
            if trend.column in source:
                points = [point(index, value) for index, value in enumerate(source[trend.column])]
                points = [item for item in points if item is not None]
                if len(points) >= 2:
                    items.append(dict(kind='line', points=points, color=trend.color,
                                      dash=trend.linestyle, width=trend.width, alpha=1))
        if not owns_risk_lines(kind):
            for key, label, color in [('stop', 'SL', STOP), ('target', 'TP1', TARGET)]:
                value = payload.get(key)
                if value is not None and math.isfinite(float(value)):
                    items.append(dict(kind='hline', value=float(value), text=label,
                                      color=color, dash='--', width=1, alpha=.7))
    finally:
        fig.clear()
    return {'items': items, 'facts': facts, 'availableDate': anchor}
