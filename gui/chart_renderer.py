"""桌面与 Web 共用的纯图片绘制；无需 Tk 或桌面窗口。"""
from __future__ import annotations

import io
from PIL import Image

from .chart_annotations import (
    annotation_kwargs, info_panel_lines, marker_specs,
    owns_risk_lines, restyle_strategy_annotations, signal_context, trend_specs,
    GAP_STRATEGIES, gap_annotation_frame,
)
from .theme import (
    ANNOTATION, AVERAGE, BORDER, CHART_BG, CONTROL_BG, DOWN, GRID, MUTED, STOP, TARGET, TEXT, UP,
)


def render_chart(frame, payload, title, meta, *, strategy=None, strategy_type="",
                 replay_events=(), view_bars=120, size=None, price_scale=1.0, view_offset=0,
                 code=None, instrument_type=None):
    """绘制通用底图、冻结策略专属标注和通用信息层。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    import mplfinance as mpf
    import pandas as pd

    end = max(1, len(frame) - min(max(0, view_offset), max(0, len(frame)-view_bars)))
    visible = frame.iloc[max(0, end-view_bars):end].copy()
    strategy_plot = visible.reset_index(drop=True).copy()
    plot = visible.copy()
    plot.index = pd.to_datetime(plot["date"])
    colors = mpf.make_marketcolors(
        up=UP,
        down=DOWN,
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    style = mpf.make_mpf_style(
        marketcolors=colors,
        facecolor=CHART_BG,
        figcolor=CHART_BG,
        gridcolor=GRID,
        gridstyle="-",
        y_on_right=True,
        rc={
            "font.family": ["Microsoft YaHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": MUTED,
            "text.color": TEXT,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
        },
    )
    adds = []
    legend_handles = []
    if "ema20" in plot:
        adds.append(mpf.make_addplot(plot.ema20, color=AVERAGE, width=1.35))
    signal_column = meta.get("signal_column")
    h2 = signal_column == 'signal_gap_h2' and payload.get('plan_kind') == 'gap-h2-next-session'
    structure_unavailable = False
    if not h2 and strategy_type in GAP_STRATEGIES:
        structure = gap_annotation_frame(frame, payload, strategy_type)
        if structure is not None:
            strategy_plot = structure.loc[structure.date.isin(visible.date)].reset_index(drop=True)
        anchor = str(payload.get('setup_date') or payload.get('asof') or '')[:10]
        structure_unavailable = structure is None or anchor not in visible.date.astype(str).str[:10].tolist()
    # Precision belongs to the security, not the strategy or observed digits.
    code = code or frame.attrs.get('code')
    instrument_type = instrument_type or frame.attrs.get('instrument_type')
    if code or instrument_type:
        from decimal import Decimal
        from workbench.prices import tick_size
        tick = tick_size(code, float(visible.close.iloc[-1]), instrument_type=instrument_type)
        decimals = max(0, -Decimal(str(tick)).as_tuple().exponent)
    else:
        # Compatibility for old stock charts without security context; H2
        # plans already carry precision established by the same tick rules.
        decimals = payload.get('price_decimals', 2) if h2 else 2
    for trend in trend_specs(strategy_type):
        if trend.column in plot and plot[trend.column].notna().any():
            adds.append(
                mpf.make_addplot(
                    plot[trend.column], color=trend.color,
                    linestyle=trend.linestyle, width=trend.width,
                )
            )
            legend_handles.append(
                Line2D([0], [0], color=trend.color, linestyle=trend.linestyle,
                       linewidth=trend.width, label=trend.label)
            )
    for marker in ([] if h2 else marker_specs(strategy_type, meta)):
        if marker.column not in plot or marker.price_column not in plot:
            continue
        marks = (
            plot[marker.price_column]
            .where(plot[marker.column].fillna(False).astype(bool))
            * marker.multiplier
        )
        if marks.notna().any():
            adds.append(
                mpf.make_addplot(
                    marks,
                    type="scatter",
                    marker=marker.marker,
                    markersize=marker.size,
                    color=marker.color,
                )
            )
            legend_handles.append(
                Line2D(
                    [0], [0], marker=marker.marker, color=CHART_BG,
                    label=marker.label, markerfacecolor=marker.color,
                    markersize=max(6, marker.size ** .5),
                )
            )
    strategy_info = {} if h2 else signal_context(strategy, frame, payload)
    owns_levels = not h2 and not structure_unavailable and owns_risk_lines(strategy_type)
    risk_levels = []
    lines, line_colors, styles = [], [], []
    levels = (("entry", TARGET, ":"), ("stop", STOP, "-."), ("mm_target", TARGET, "--")) if h2 else (
        ("stop", STOP, "-."), ("target", TARGET, "--"))
    for key, color, dash in levels:
        if h2 and key != 'mm_target' and payload.get('pending_state') != 'PENDING':
            continue
        if payload.get(key) is not None and float(payload[key]) > 0:
            value = float(payload[key])
            risk_levels.append(value)
            if not owns_levels:
                lines.append(value)
                line_colors.append(color)
                styles.append(dash)
    kwargs = {}
    if adds:
        kwargs["addplot"] = adds
    if lines:
        kwargs["hlines"] = dict(hlines=lines, colors=line_colors, linestyle=styles, linewidths=1)
    # 远离当前价格区间的止损/目标仍显示在参数框中，但不再把整段 K 线
    # 压缩到图角。接近当前行情的价位线会纳入可视范围。
    price_low = float(plot["low"].min())
    price_high = float(plot["high"].max())
    price_span = max(price_high - price_low, abs(price_high) * .02, .01)
    view_low = price_low - price_span * .06
    view_high = price_high + price_span * .06
    if h2:
        view_low = price_low - price_span * .12
        view_high = price_high + price_span * .18
    for level in risk_levels:
        if price_low - price_span * .18 <= level <= price_high + price_span * .18:
            view_low = min(view_low, level - price_span * .02)
            view_high = max(view_high, level + price_span * .02)
    center, half = (view_low+view_high)/2, (view_high-view_low)/2 * price_scale
    view_low, view_high = center-half, center+half
    kwargs["ylim"] = (view_low, view_high)
    fig = None
    try:
        fig, axes = mpf.plot(
            plot,
            type="candle",
            style=style,
            volume=True,
            panel_ratios=(7, 1),
            title=dict(title=title, fontsize=9 if size[0]<1000 else 12) if size else title,
            ylabel="",
            figsize=(max(100, size[0])/110, max(100, size[1])/110) if size else (12.2, 7.5),
            tight_layout=True,
            returnfig=True,
            **kwargs,
        )
        ax = axes[0]
        if size:
            # Reserve coordinate-label pixels, not a percentage of a large
            # monitor. Both panels share the same edges and retain 7:1 height.
            width, height = max(100, size[0]), max(100, size[1])
            left, right = 8/width, 1-min(64, width*.3)/width
            bottom, top = min(48, height*.2)/height, 1-min(30, height*.15)/height
            volume_height = (top-bottom)/8
            for text in fig.texts:
                text.set_position(((left+right)/2, 1-4/height))
                text.set_verticalalignment('top')
        for index, axis in enumerate(axes):
            if size:
                is_price = index < 2
                axis.set_position([left, bottom+volume_height if is_price else bottom,
                                   right-left, volume_height*(7 if is_price else 1)])
            axis.set_facecolor(CHART_BG)
            axis.tick_params(colors=MUTED, labelsize=8)
            for spine in axis.spines.values():
                spine.set_color(BORDER)
        if h2:
            from .h2_chart import draw_h2
            draw_h2(ax, plot, payload)
        open_gap_count = 0
        annotate = getattr(strategy, "annotate_chart", None)
        if not h2 and not structure_unavailable and callable(annotate):
            previous = {id(artist) for artist in [*ax.lines, *ax.collections, *ax.patches]}
            result = annotate(
                ax,
                strategy_plot,
                strategy_type,
                **annotation_kwargs(payload, strategy_info),
            )
            if isinstance(result, int):
                open_gap_count = result
            restyle_strategy_annotations(ax, strategy_type, previous)
            if strategy_type in GAP_STRATEGIES and 'GAP_H2' not in strategy_type:
                for text in list(ax.texts):
                    if text.get_text() == 'H2':
                        text.remove()
        facts = [] if h2 else info_panel_lines(
            payload, strategy_info, frame, open_gap_count=open_gap_count
        )
        if structure_unavailable:
            facts.append('原信号结构待核对（保留归档计划价格）')
        if facts:
            ax.text(
                .02,
                .965,
                "\n".join(facts),
                transform=ax.transAxes,
                fontsize=8,
                color=TEXT,
                va="top",
                bbox=dict(
                    boxstyle="round,pad=.28",
                    facecolor=CONTROL_BG,
                    alpha=.94,
                    edgecolor=BORDER,
                ),
            )
        labels = (("stop", "SL1", STOP), ("mm_target", "MM / TP", TARGET)) if h2 else (
            ("stop", "SL", STOP), ("target", "TP1", TARGET))
        for key, label, color in labels:
            if owns_levels:
                continue
            if payload.get(key) is not None and float(payload[key]) > 0:
                value = float(payload[key])
                if value > view_high:
                    ax.text(
                        .99, .985, f"{label}↑ {value:.{decimals}f}", transform=ax.transAxes,
                        ha="right", va="top", color=color, fontsize=8,
                    )
                elif value < view_low:
                    ax.text(
                        .99, .015, f"{label}↓ {value:.{decimals}f}", transform=ax.transAxes,
                        ha="right", va="bottom", color=color, fontsize=8,
                    )
                else:
                    ax.text(
                        .99, value, f"{label}: {value:.{decimals}f}",
                        transform=ax.get_yaxis_transform(), ha="right", va="bottom",
                        color=color, fontsize=8,
                    )
        if legend_handles:
            legend = ax.legend(
                handles=legend_handles,
                loc="lower left",
                framealpha=.9,
                fontsize=8,
            )
            legend.get_frame().set_facecolor(CONTROL_BG)
            legend.get_frame().set_edgecolor(BORDER)
            for label in legend.get_texts():
                label.set_color(TEXT)
        dates = visible.date.astype(str).str[:10].tolist()
        for event in replay_events:
            if event['date'] not in dates or event.get('price') is None:
                continue
            x = dates.index(event['date'])
            label = {'trigger': 'Trigger', 'fill': 'Fill', 'exit': 'Exit'}.get(event['kind'])
            if label:
                dx, dy = {'trigger': (-26, -34), 'fill': (20, -58), 'exit': (-12, -38)}[event['kind']]
                if x >= len(dates) - 4:
                    dx = min(dx, -18)
                ax.annotate(label, xy=(x, event['price']), xytext=(dx, dy),
                            textcoords='offset points', ha='center', color=TEXT, fontsize=8,
                            bbox=dict(facecolor=CONTROL_BG, edgecolor=BORDER, alpha=.9),
                            arrowprops=dict(arrowstyle='-', color=ANNOTATION, shrinkB=0))
        buf = io.BytesIO()
        # Default charts use a tight PNG crop. Retain its origin so mouse
        # coordinates remain correct when that image is later fitted to a slot.
        fig.canvas.draw()
        crop = fig.get_tightbbox(fig.canvas.get_renderer()).padded(.02) if not size else None
        factor = 110 / fig.dpi
        crop_x, crop_y = (crop.x0*110, crop.y0*110) if crop else (0, 0)
        fig.savefig(
            buf,
            format="png",
            dpi=110,
            bbox_inches=None if size else "tight",
            pad_inches=.02,
            facecolor=CHART_BG,
        )
        buf.seek(0)
        image = Image.open(buf).copy()
        box, volume_box = ax.get_window_extent(), axes[2].get_window_extent()
        image.info['replay_view'] = dict(total=len(frame), bars=len(visible), offset=len(frame)-end,
            price_x=(box.x0*factor-crop_x, box.x1*factor-crop_x),
            price_y=(image.height-(box.y1*factor-crop_y), image.height-(box.y0*factor-crop_y)),
            plot_y=(image.height-(box.y1*factor-crop_y), image.height-(volume_box.y0*factor-crop_y)),
            candle_x=[float(ax.transData.transform((i, 0))[0])*factor-crop_x for i in range(len(plot))],
            candles=visible[['date', 'open', 'high', 'low', 'close']].to_dict('records'),
            decimals=decimals)
        return image
    finally:
        if fig is not None:
            plt.close(fig)
