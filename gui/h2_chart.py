"""GAP H2 historical structure plus the latest next-session plan."""
from __future__ import annotations

from workbench.h2_plan import STATE_LABELS
from .theme import ACCENT, BORDER, CONTROL_BG, MUTED, STOP, TARGET, TEXT


def draw_h2(ax, plot, plan):
    from matplotlib.patches import Rectangle

    dates = plot.date.astype(str).str[:10].tolist()
    state = plan.get('pending_state', 'UNAVAILABLE')

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
        if py + dy * scale > box.y1 - 16 * scale:
            dy = -18
        elif py + dy * scale < box.y0 + 16 * scale:
            dy = 18
        ax.annotate(label, xy=(x, price), xytext=(dx, dy), textcoords='offset points',
                    ha='center', va='bottom' if dy >= 0 else 'top',
                    fontsize=8, color=color,
                    bbox=dict(boxstyle='round,pad=.15', facecolor=CONTROL_BG,
                              edgecolor='none', alpha=.9),
                    arrowprops=dict(arrowstyle='-', color=color, lw=.9,
                                    shrinkA=2, shrinkB=0), annotation_clip=True)

    for label, day_key, price_key, color, offset in (
        ('BO', 'bo_date', 'bo_high', ACCENT, (0, 16)),
        ('H1', 'h1_date', 'h1_high', ACCENT, (0, 30)),
        ('H2', 'h2_setup_date', 'h2_high', TARGET, (0, 44)),
        ('MM low', 'mm_low_date', 'mm_low', MUTED, (18, -18)),
        ('Gap Floor', 'gap_floor_date', 'gap_floor', ACCENT, (-12, -28)),
    ):
        mark(label, plan.get(day_key), plan.get(price_key), color, offset)
    floor, top = plan.get('gap_floor'), plan.get('gap_top')
    if floor is not None:
        ax.axhline(floor, color=ACCENT, linestyle=':', linewidth=.8, alpha=.65)
    if floor is not None and top is not None and top > floor:
        bo = dates.index(plan['bo_date']) if plan.get('bo_date') in dates else 0
        setup = dates.index(plan['h2_setup_date']) if plan.get('h2_setup_date') in dates else 0
        if setup >= bo and plan.get('h2_setup_date', '') >= dates[0]:
            ax.add_patch(Rectangle((bo - .4, floor), max(setup - bo + .8, .8), top - floor,
                                   facecolor=ACCENT, edgecolor=ACCENT, alpha=.12))
            ax.text((bo + setup) / 2, (floor + top) / 2, 'Gap', color=ACCENT,
                    fontsize=8, ha='center', va='center')
    for gap in plan.get('prior_open_gaps', []):
        if gap['date'] in dates:
            ax.hlines(gap['floor'], dates.index(gap['date']), len(dates) - 1,
                      colors=MUTED, linestyles=':', linewidth=.7, alpha=.6)
    if state == 'PENDING':
        mark('Entry', plan.get('entry_reference_date'), plan.get('entry_plan_price'),
             TARGET, (-26, 18))
        mark('SL1', plan.get('sl1_reference_date'), plan.get('sl1_plan_price'),
             STOP, (18, -28))

    risk = plan.get('initial_risk_pct')
    reward = plan.get('mm_r_multiple')
    gaps = plan.get('prior_open_gap_count')
    if state == 'PENDING':
        headline = ('明日挂单计划\n'
                    + (f'风险  {risk:.2f}%' if risk is not None else '风险  待核对') + '\n'
                    + (f'收益  {reward:.2f}R' if reward is not None else '收益  待核对') + '\n'
                    + (f'趋势  前序开放缺口 {gaps}个' if gaps is not None else '趋势  数据不足'))
        prices = '   '.join(f'{label} {plan[key]:.2f}' for label, key in (
            ('Entry', 'entry_plan_price'), ('SL1', 'sl1_plan_price'), ('MM', 'mm_target'))
                            if plan.get(key) is not None)
    else:
        label = STATE_LABELS.get(state, state)
        if plan.get('state_reason') == 'target_already_reached':
            label = 'MM 已先达到'
        headline = f'待挂机会结束\n{label}' if state != 'UNAVAILABLE' else label
        prices = '当前无待挂计划'
    card = ax.text(.02, .97, headline, transform=ax.transAxes, fontsize=10, fontweight='bold',
            color=TEXT, va='top', linespacing=1.5,
            bbox=dict(boxstyle='round,pad=.4', facecolor=CONTROL_BG, alpha=.94, edgecolor=BORDER))
    # Secondary prices below the four-line decision card, in a smaller font.
    box = card.get_window_extent(renderer=ax.figure.canvas.get_renderer())
    y = ax.transAxes.inverted().transform((box.x0, box.y0))[1] - .025
    ax.text(.02, y, prices, transform=ax.transAxes, fontsize=8, color=MUTED, va='top',
            bbox=dict(boxstyle='round,pad=.25', facecolor=CONTROL_BG, alpha=.94, edgecolor=BORDER))
