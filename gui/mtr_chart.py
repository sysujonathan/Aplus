"""Read-only MTR presentation. Never infer nodes or recalculate plan prices."""
from __future__ import annotations

import math

from .theme import ANNOTATION, MUTED

ENTRY, SL, TP = '#089981', '#F23645', '#D6B86A'
LEGEND = '○ 结构 H0/L1/H1/TL · ◇ S 信号（非成交）'


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def mtr_evidence(frame, payload, *, decimals=2):
    """Consume the original calculated sequence, not a reset viewport slice.

    setup_date identifies the selected signal; asof is its evidence watermark.
    Archive prices take precedence, including when recomputation is incomplete.
    Original integer indices are mapped to dates before any viewport filtering.
    """
    result = dict(contract='mtr-v1', items=[], facts=[], selectedDate=None)
    if frame.empty or 'date' not in frame:
        result['facts'] = ['原信号不在当前行情中，结构待核对']
        return result
    dates = frame.date.astype(str).str[:10].tolist()
    available = str(payload.get('asof') or payload.get('setup_date') or dates[-1])[:10]
    result['availableDate'] = available
    selected = payload.get('setup_date') or payload.get('asof')
    flags = frame.get('signal_mtr')
    signals = [i for i, day in enumerate(dates)
               if day <= available and flags is not None and flags.iloc[i] is not None
               and str(flags.iloc[i]) in ('True', '1', '1.0')]
    selected = str(selected)[:10] if selected else (dates[signals[-1]] if signals else None)
    result['selectedDate'] = selected
    row_index = dates.index(selected) if selected in dates and selected <= available else None
    confirmed = row_index in signals and _number(frame.iloc[row_index].get('low')) is not None
    row = frame.iloc[row_index] if row_index is not None else {}
    items, facts = result['items'], result['facts']
    # A small historical diamond is still a signal, never a fill or structure.
    for i in signals:
        if dates[i] >= (selected or ''):
            continue
        price = _number(frame.iloc[i].get('low'))
        if price:
            items.append(dict(kind='marker', a=dict(date=dates[i], value=price),
                              marker='D', size=3, pixelOffset=[0, 6], fill=False,
                              color=ANNOTATION, alpha=.45, text='历史 MTR 信号'))
    if confirmed:
        signal = dict(date=selected, value=float(row['low']))
        items.append(dict(kind='marker', a=signal, marker='D', size=5,
                          pixelOffset=[0, 6], fill=False, color=ANNOTATION, selected=True))
        items.append(dict(kind='label', a=signal, text='S', offset=[22, 28],
                          pixelOffset=[0, 6], color=ANNOTATION))
        # RangeIndex subsets retaining their original positions can be mapped;
        # a reset/truncated slice fails bounds/price checks instead of clamping.
        import pandas as pd
        start = frame.index.start if isinstance(frame.index, pd.RangeIndex) and frame.index.step == 1 else 0
        for name, column, offset in [('H0', 'high', [-22, -24]), ('L1', 'low', [-22, 26]),
                                     ('H1', 'high', [22, -24]), ('TL', 'low', [-22, 26])]:
            price = _number(row.get(f'mtr_{name}_price'))
            # Index zero is valid (prices must be strictly positive).
            raw = row.get(f'mtr_{name}_idx')
            try:
                index = float(raw)
                valid_index = math.isfinite(index) and index.is_integer() and index >= start
                i = int(index) - start if valid_index else -1
            except (TypeError, ValueError):
                i = -1
            if (price is None or not 0 <= i < len(frame) or dates[i] > selected
                    or not math.isclose(float(frame.iloc[i][column]), price, rel_tol=1e-7, abs_tol=1e-9)):
                facts.append(f'{name} 结构待核对')
                continue
            point = dict(date=dates[i], value=price)
            items.extend([dict(kind='marker', a=point, marker='o', size=3, fill=False, color=ANNOTATION),
                          dict(kind='label', a=point, text=name, offset=offset, color=ANNOTATION)])
        # Preserve supplied geometric evidence, breaking at missing values.
        points = []
        for i, value in enumerate(frame.get('geometric_trendline', [])):
            price = _number(value)
            if dates[i] <= selected and price:
                points.append(dict(date=dates[i], value=price))
            else:
                if len(points) > 1:
                    items.append(dict(kind='line', points=points, color=ANNOTATION, dash='--', width=.8, alpha=.6))
                points = []
        if len(points) > 1:
            items.append(dict(kind='line', points=points, color=ANNOTATION, dash='--', width=.8, alpha=.6))
    else:
        facts.append('原信号未确认或不在当前行情中，结构待核对（保留归档计划价）')
    prices = []
    for key, column, label, color, dash in (
        ('entry', 'entry_price', 'Entry', ENTRY, [2, 4]),
        ('stop', 'sl_price', 'SL', SL, [6, 4]),
        ('target', 'tp1_price', 'TP1', TP, [8, 3, 2, 3]),
    ):
        # Do not replace an explicitly missing archive field with a new value.
        price = _number(payload[key] if key in payload else row.get(column))
        if price is None:
            facts.append(f'{label} 待核对')
            continue
        prices.append(f'{label} {price:.{decimals}f}')
        if row_index is not None:
            items.append(dict(kind='level', a=dict(date=selected, value=price),
                              text=label if confirmed else label+'参考', color=color, dash=dash, width=1))
    if prices:
        facts.insert(0, ' · '.join(prices))
    if confirmed:
        facts.insert(0, f'MTR · 信号 {selected}')
    levels = {}
    for item in list(items):
        if item['kind'] != 'level':
            continue
        value = item['a']['value']
        if value in levels:
            original = levels[value]
            original['text'] += '/' + item['text']
            # One geometry; risk has priority over entry and target at equal prices.
            if item['color'] == SL:
                original.update(color=SL, dash=[6, 4])
            items.remove(item)
        else:
            levels[value] = item
    missing = [item.removesuffix(' 结构待核对').removesuffix(' 待核对') for item in facts if item.endswith('待核对')]
    facts[:] = [item for item in facts if not item.endswith('待核对')]
    if missing:
        facts.append('待核对：' + '/'.join(missing))
    if any(item['kind'] == 'line' for item in items):
        facts.append('灰虚线：原几何趋势线（辅助）')
    facts.extend([LEGEND, '计划显示参考区间 · 有效期未核验'])
    return result


def draw_mtr(ax, visible, evidence, decimals=2):
    """Render the same evidence with final-DPI measurement and fixed anchors."""
    from matplotlib.text import Annotation
    from matplotlib.transforms import Bbox

    dates = visible.date.astype(str).str[:10].tolist()
    labels = []
    outside = []

    def coordinate(point):
        if point['date'] not in dates:
            return None
        return dates.index(point['date']), point['value']

    class Label(Annotation):
        def draw(self, renderer):
            if self is labels[0]:
                layout(renderer)
            super().draw(renderer)

    def add_label(text, point, offset, color, leader=True):
        label = Label(text, xy=point, xytext=offset, textcoords='offset points',
                      ha='center', va='center', fontsize=8.5, color=color, zorder=8,
                      fontfamily=['Microsoft YaHei', 'DejaVu Sans'],
                      bbox=dict(facecolor='#171717', edgecolor='none', alpha=.94, pad=2),
                      arrowprops=dict(arrowstyle='-', color=color, lw=.8, shrinkA=3, shrinkB=5)
                      if leader else None)
        label._mtr_offset = offset
        ax.add_artist(label)
        labels.append(label)

    for item in evidence['items']:
        if item['kind'] == 'line':
            points = [coordinate(p) for p in item['points']]
            # Break at off-window points rather than moving them to an edge.
            points = [p for p in points if p]
            if len(points) > 1:
                ax.plot(*zip(*points), color=item['color'], lw=.8, ls='--', alpha=.6)
            continue
        point = coordinate(item['a'])
        if item['kind'] == 'level':
            if item['a']['date'] > dates[-1]:
                continue
            y = item['a']['value']
            if not ax.get_ylim()[0] <= y <= ax.get_ylim()[1]:
                continue  # Original value remains in the facts card.
            x = point[0] if point else 0
            end = ax.get_xlim()[1] - .4
            ax.plot([x, end], [y, y], color=item['color'], lw=1, ls=(0, item['dash']), clip_on=True)
            add_label(f"{item['text']} {y:.{decimals}f}", (end, y), (-34, 10), item['color'])
        elif point is not None and ax.get_ylim()[0] <= point[1] <= ax.get_ylim()[1]:
            if item['kind'] == 'marker':
                # Pixel/point offsets affect display only, not date/price.
                offset = item.get('pixelOffset', [0, 0])
                from matplotlib.transforms import ScaledTranslation
                transform = ax.transData + ScaledTranslation(offset[0]/ax.figure.dpi, -offset[1]/ax.figure.dpi, ax.figure.dpi_scale_trans)
                ax.plot(*point, marker=item['marker'], ms=item['size']*1.4,
                        mfc='none', mec=item['color'], mew=1.3 if item.get('selected') else .9,
                        alpha=item.get('alpha', 1), transform=transform, clip_on=True)
            elif item['kind'] == 'label':
                # Vector offsets use screen y down; Matplotlib points y up.
                dx, dy = item['offset']
                add_label(item['text'], point, (dx, -dy), item['color'])
        elif item['kind'] == 'label':
            outside.append(item['text'])
    if outside:
        ax.text(.01, .015, '/'.join(outside)+' 在窗口外', transform=ax.transAxes, color=MUTED, fontsize=8,
                fontfamily=['Microsoft YaHei', 'DejaVu Sans'])

    def layout(renderer):
        box = ax.get_window_extent(renderer)
        scale = renderer.points_to_pixels(1)
        margin = 6*scale
        occupied = []
        for text in ax.texts:
            if text not in labels:
                occupied.append(text.get_window_extent(renderer).expanded(1.02, 1.08))
        candles = []
        for i, row in enumerate(visible.itertuples()):
            a, b = ax.transData.transform((i-.4, row.low)), ax.transData.transform((i+.4, row.high))
            candles.append(Bbox.from_extents(a[0]-2, a[1]-2, b[0]+2, b[1]+2))
        for label in labels:
            preferred = label._mtr_offset
            candidates = [preferred, (22, 22), (-22, 22), (22, -22), (-22, -22),
                          (34, 30), (-34, 30), (34, -30), (-34, -30)]
            choices = []
            for dx, dy in candidates:
                label.set_position((dx, dy)); label.update_positions(renderer); label.update_bbox_position_size(renderer)
                bounds = label.get_bbox_patch().get_window_extent(renderer)
                dx += max(0, box.x0+margin-bounds.x0)/scale - max(0, bounds.x1-box.x1+margin)/scale
                dy += max(0, box.y0+margin-bounds.y0)/scale - max(0, bounds.y1-box.y1+margin)/scale
                label.set_position((dx, dy)); label.update_positions(renderer); label.update_bbox_position_size(renderer)
                bounds = label.get_bbox_patch().get_window_extent(renderer)
                overlap = sum(bounds.overlaps(other) for other in occupied)
                candle_overlap = sum(bounds.overlaps(other) for other in candles)
                choices.append(((overlap, candle_overlap, (dx-preferred[0])**2+(dy-preferred[1])**2), (dx, dy), bounds))
            _, offset, bounds = min(choices, key=lambda item: item[0])
            label.set_position(offset)
            occupied.append(bounds.padded(2*scale))
    return labels
