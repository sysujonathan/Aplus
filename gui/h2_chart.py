"""GAP H2 historical structure plus the latest next-session plan."""
from __future__ import annotations

from workbench.h2_plan import STATE_LABELS
from .theme import ACCENT, ANNOTATION, BORDER, CONTROL_BG, MUTED, STOP, TARGET, TEXT


GAP_COLOR = ANNOTATION


def _gap_label(ax, center):
    from matplotlib.text import Annotation

    class BoundedGapLabel(Annotation):
        """Fit the label at render time, including resized and exported figures."""

        def draw(self, renderer):
            # Recompute from the preferred offset, never accumulate resize shifts.
            self.set_position((28, 18))
            self.update_positions(renderer)
            self.update_bbox_position_size(renderer)
            text_box = self.get_bbox_patch().get_window_extent(renderer)
            plot_box = self.axes.get_window_extent(renderer)
            scale = renderer.points_to_pixels(1)
            margin = 3 * scale

            def correction(low, high, start, end):
                if high - low > end - start - 2 * margin:
                    return (start + end - low - high) / 2
                return max(start + margin - low, min(0, end - margin - high))

            obstacles = []
            for text in self.axes.texts:
                if text.get_gid() in ('h2-decision-card', 'h2-decision-prices'):
                    text.update_bbox_position_size(renderer)
                    obstacles.append(text.get_bbox_patch().get_window_extent(renderer))
            candidates = [(0, 0)]
            for box in obstacles:
                candidates.extend(((box.x1 + margin - text_box.x0, 0),
                                   (box.x0 - margin - text_box.x1, 0),
                                   (0, box.y1 + margin - text_box.y0),
                                   (0, box.y0 - margin - text_box.y1)))

            def fit_and_score(offset):
                dx, dy = offset
                dx += correction(text_box.x0 + dx, text_box.x1 + dx, plot_box.x0, plot_box.x1)
                dy += correction(text_box.y0 + dy, text_box.y1 + dy, plot_box.y0, plot_box.y1)
                overlap = sum(
                    max(0, min(text_box.x1 + dx, box.x1 + margin) - max(text_box.x0 + dx, box.x0 - margin)) *
                    max(0, min(text_box.y1 + dy, box.y1 + margin) - max(text_box.y0 + dy, box.y0 - margin))
                    for box in obstacles)
                return (overlap > .01, overlap, dx*dx + dy*dy), (dx, dy)

            _, (dx, dy) = min(map(fit_and_score, candidates), key=lambda result: result[0])
            self.set_position((28 + dx / scale, 18 + dy / scale))
            super().draw(renderer)

    label = BoundedGapLabel(
        'Gap', xy=center, xytext=(28, 18), textcoords='offset points',
        color=GAP_COLOR, fontsize=9, fontweight='normal',
        ha='center', va='center',
        bbox=dict(boxstyle='round,pad=0.15', facecolor=CONTROL_BG,
                  edgecolor='none', alpha=.9),
        arrowprops=dict(arrowstyle='-', color=ANNOTATION, lw=.9,
                        shrinkA=2, shrinkB=5), annotation_clip=True,
    )
    ax.add_artist(label)


def draw_h2(ax, plot, plan):
    from matplotlib.patches import Rectangle

    dates = plot.date.astype(str).str[:10].tolist()
    state = plan.get('pending_state', 'UNAVAILABLE')
    new_pricing = plan.get('pricing_basis') == 'gap-floor-sl-2r-v1'
    decimals = plan.get('price_decimals', 2)

    def mark(label, day, price, color, offset):
        if day not in dates or price is None:
            return
        x = dates.index(day)
        # mplfinance candles are centered on integer x, never x + .5.
        dx, dy = offset
        # Reserve space inside the plot for labels near its last candle or bounds.
        px, py = ax.transData.transform((x, price))
        box = ax.get_window_extent()
        scale = ax.figure.dpi / 72
        half_width = max(12, len(label) * 3.5) * scale
        dx = max((box.x0 + half_width - px) / scale,
                 min(dx, (box.x1 - half_width - px) / scale))
        # A vertical leader above H2 looks like an extension of its wick.
        # Use an offset dashed leader with an air gap at the actual candle high.
        if label == 'H2':
            dx = -28 if px > (box.x0 + box.x1) / 2 else 28
        if py + dy * scale > box.y1 - 16 * scale:
            dy = -18
        elif py + dy * scale < box.y0 + 16 * scale:
            dy = 18
        ax.annotate(label, xy=(x, price), xytext=(dx, dy), textcoords='offset points',
                    ha='center', va='bottom' if dy >= 0 else 'top',
                    fontsize=8, color=ANNOTATION,
                    bbox=dict(boxstyle='round,pad=.15', facecolor=CONTROL_BG,
                              edgecolor='none', alpha=.9),
                    arrowprops=dict(arrowstyle='-', color=ANNOTATION, lw=.9,
                                    linestyle='--' if label == 'H2' else '-',
                                    shrinkA=2, shrinkB=5 if label == 'H2' else 0), annotation_clip=True)

    for label, day_key, price_key, color, offset in (
        ('BO', 'bo_date', 'bo_high', ACCENT, (0, 16)),
        ('H1', 'h1_date', 'h1_high', '#8E24AA', (0, 30)),
        ('MM low', 'mm_low_date', 'mm_low', MUTED, (18, -18)),
        ('Gap Floor', 'gap_floor_date', 'gap_floor', ACCENT, (-12, -28)),
    ):
        mark(label, plan.get(day_key), plan.get(price_key), color, offset)
    # The pullback setup is not the subsequent upside trigger. Only a verified
    # trigger gets an H2 label; invalid/expired/pending setups remain signals.
    trigger_day = plan.get('replay_trigger_date') or (plan.get('pending_end_date') if state == 'TRIGGERED' else None)
    if trigger_day in dates:
        trigger_high = float(plot.iloc[dates.index(trigger_day)].high)
        mark('H2', trigger_day, trigger_high, TARGET, (0, 30))
    floor, top = plan.get('gap_floor'), plan.get('gap_top')
    if floor is not None:
        # The display adapter follows the trader's neutral annotation rule;
        # frozen strategy geometry stays unchanged.
        ax.axhline(floor, color=GAP_COLOR, linestyle='-', linewidth=1, alpha=.5)
    if floor is not None and top is not None and top > floor:
        bo = dates.index(plan['bo_date']) if plan.get('bo_date') in dates else 0
        setup = dates.index(plan['h2_setup_date']) if plan.get('h2_setup_date') in dates else 0
        if setup >= bo and plan.get('h2_setup_date', '') >= dates[0]:
            ax.add_patch(Rectangle(
                (bo - .4, floor), max(setup - bo + .8, .8), top - floor,
                linewidth=1.2, facecolor='none', edgecolor=GAP_COLOR,
                linestyle='--', alpha=.6,
            ))
            _gap_label(ax, ((bo + setup) / 2, (floor + top) / 2))
    for gap in plan.get('prior_open_gaps', []):
        if gap['date'] in dates:
            ax.hlines(gap['floor'], dates.index(gap['date']), len(dates) - 1,
                      colors=MUTED, linestyles=':', linewidth=.7, alpha=.6)
    if state == 'PENDING':
        mark('←Entry', plan.get('entry_reference_date'), plan.get('entry_plan_price'),
             TARGET, (-26, 18))
        mark('SL1', plan.get('sl1_reference_date'), plan.get('sl1_plan_price'),
             STOP, (18, -28))

    risk = plan.get('initial_risk_pct')
    reward = plan.get('tp_r_multiple') if new_pricing else plan.get('mm_r_multiple')
    gaps = plan.get('prior_open_gap_count')
    if state == 'PENDING':
        headline = (plan.get('replay_caption', '明日挂单计划') + '\n'
                    + (f'风险  {risk:.2f}%' if risk is not None else '风险  待核对') + '\n'
                    + (f'收益  {reward:.2f}R' if reward is not None else '收益  待核对') + '\n'
                    + (f'趋势  前序开放缺口 {gaps}个' if gaps is not None else '趋势  数据不足'))
        if new_pricing:
            primary = '   '.join(f'{label} {plan[key]:.{decimals}f}' for label, key in (
                ('Entry', 'entry_plan_price'), ('SL', 'sl_plan_price'), ('2R', 'tp_plan_price'))
                               if plan.get(key) is not None)
            references = '   '.join(f'{label} {plan[key]:.{decimals}f}' for label, key in (
                ('SL1参考', 'sl1_plan_price'), ('MM参考', 'mm_target')) if plan.get(key) is not None)
            if plan.get('mm_r_multiple') is not None:
                references += f' ({plan["mm_r_multiple"]:.2f}R)'
            prices = primary + ('\n' + references if references else '')
        else:
            prices = '   '.join(f'{label} {plan[key]:.{decimals}f}' for label, key in (
                ('Entry', 'entry_plan_price'), ('SL1', 'sl1_plan_price'), ('MM', 'mm_target'))
                                if plan.get(key) is not None)
    else:
        label = STATE_LABELS.get(state, state)
        if plan.get('state_reason') == 'target_already_reached':
            label = 'MM 已先达到'
        headline = plan.get('replay_caption') or (f'待挂机会结束\n{label}' if state != 'UNAVAILABLE' else label)
        prices = '当前无待挂计划'
    card = ax.text(.02, .97, headline, transform=ax.transAxes, fontsize=10, fontweight='bold',
            color=TEXT, va='top', linespacing=1.5,
            bbox=dict(boxstyle='round,pad=.4', facecolor=CONTROL_BG, alpha=.94, edgecolor=BORDER),
            gid='h2-decision-card')
    # Secondary prices below the four-line decision card, in a smaller font.
    box = card.get_window_extent(renderer=ax.figure.canvas.get_renderer())
    y = ax.transAxes.inverted().transform((box.x0, box.y0))[1] - .025
    ax.text(.02, y, prices, transform=ax.transAxes, fontsize=8, color=MUTED, va='top',
            bbox=dict(boxstyle='round,pad=.25', facecolor=CONTROL_BG, alpha=.94, edgecolor=BORDER),
            gid='h2-decision-prices')
