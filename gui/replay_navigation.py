"""Local chart viewport controls; they never change the event/data cutoff."""
from math import exp
from bisect import bisect_left


class ReplayNavigation:
    def __init__(self, widget, redraw, hover=None):
        self.widget, self.redraw, self.hover = widget, redraw, hover
        self.reset(False)
        widget.bind('<MouseWheel>', self.wheel)
        widget.bind('<ButtonPress-1>', self.press)
        widget.bind('<B1-Motion>', self.drag)
        widget.bind('<ButtonRelease-1>', self.release)
        widget.bind('<Double-1>', lambda e: self.reset())
        if hover:
            widget.bind('<Motion>', self.motion)
            widget.bind('<Leave>', lambda e: self.hover(''))

    def reset(self, redraw=True):
        self.bars, self.offset, self.price_scale = None, 0, 1.0
        self.info, self.origin = {}, None
        if self.hover:
            self.hover('')
        if redraw:
            self.redraw()
        return 'break'

    @property
    def viewport(self):
        return (self.bars, self.offset, self.price_scale)

    def accept(self, image):
        self.info = image.info.get('replay_view', {})
        if self.hover:
            self.hover('')

    def motion(self, event):
        """Read only already-rendered visible bars; no I/O or chart redraw."""
        if self.hover:
            self.hover(candle_readout(self.info, event.x, event.y))

    def wheel(self, event):
        if not self.info or not event.delta:
            return 'break'
        total = self.info['total']
        old = min(total, self.bars or self.info['bars'])
        new = min(total, max(min(12, total), round(old * (.8 if event.delta > 0 else 1.25))))
        left, right = self.info['price_x']
        fraction = min(1, max(0, (event.x-left) / max(1, right-left)))
        # Preserve the candle under the pointer while changing candle width.
        self.offset = min(max(0, total-new), max(0, round(self.offset + (old-new)*(1-fraction))))
        self.bars = new
        self.redraw()
        return 'break'

    def press(self, event):
        if self.info:
            self.origin = (event.x, event.y, self.info['offset'], self.price_scale,
                           event.x >= self.info['price_x'][1])
            self.widget.configure(cursor='sb_v_double_arrow' if self.origin[-1] else 'fleur')
        return 'break'

    def drag(self, event):
        if not self.origin:
            return 'break'
        x, y, offset, scale, price_axis = self.origin
        if price_axis:
            self.price_scale = min(8, max(.15, scale * exp((event.y-y)/max(100, self.widget.winfo_height())*3)))
        else:
            left, right = self.info['price_x']
            self.bars = self.info['bars']
            self.offset = min(max(0, self.info['total']-self.bars),
                              max(0, round(offset + (event.x-x)*self.bars/max(1, right-left))))
        self.redraw()
        return 'break'

    def release(self, event):
        self.origin = None
        self.widget.configure(cursor='')
        return 'break'


def candle_readout(info, x, y):
    positions, rows = info.get('candle_x', []), info.get('candles', [])
    if not positions or not rows:
        return ''
    top, bottom = info['plot_y']
    left, right = info['price_x']
    if not (left <= x <= right and top <= y <= bottom):
        return ''
    i = bisect_left(positions, x)
    if i == len(positions) or i and x-positions[i-1] <= positions[i]-x:
        i -= 1
    spacing = positions[1]-positions[0] if len(positions)>1 else right-left
    if abs(x-positions[i]) > spacing/2:
        return ''
    bar, decimals = rows[i], info.get('decimals', 2)
    return str(bar['date'])[:10] + '   ' + '  '.join(
        f'{label} {bar[key]:.{decimals}f}' for label, key in
        [('开', 'open'), ('高', 'high'), ('低', 'low'), ('收', 'close')])
