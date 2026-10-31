"""Manual desktop acceptance fixture; explicit isolated home, no real data."""
from pathlib import Path
import math
import random
import sys

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def samples(folder):
    folder.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    for index, word in enumerate(('microDB', 'ETHUSD', '锦和商业')):
        image = Image.new('RGB', (1400, 800), '#101923')
        draw = ImageDraw.Draw(image)
        draw.text((35, 22), 'PA 操作验收测试样本 · 非真实行情', font=font, fill='#e5bd79')
        draw.text((35, 66), word + ' · 日线 · 原图标注保留测试', font=font, fill='#d7e1ed')
        rng = random.Random(index + 18)
        previous = 520
        for n in range(95):
            x = 45 + n * 13
            close = int(500 - n * 2.2 + 70 * math.sin(n / 10) + rng.uniform(-30, 30))
            color = '#56c9b2' if close < previous else '#ef7580'
            draw.line((x + 4, min(previous, close)-15, x + 4, max(previous, close)+20), fill=color, width=2)
            draw.rectangle((x, min(previous, close), x+8, max(previous, close)+1), fill=color)
            previous = close
        draw.line((100, 570, 1250, 335), fill='#e5bd79', width=3)
        draw.line((700, 150, 700, 700), fill='#7fb0ef', width=2)
        draw.text((720, 140), '结构锚点 →', font=small, fill='#aac8f3')
        draw.rectangle((1080, 220, 1340, 360), outline='#56c9b2', width=3)
        draw.rectangle((1080, 360, 1340, 430), outline='#ef7580', width=3)
        draw.text((1120, 450), '风险收益标注 2R', font=small, fill='#c5d4e4')
        draw.text((35, 740), f'仅供隔离验收 · {word} · sample-{index+1}', font=small, fill='#7994ac')
        image.save(folder / f'sample-{index+1}.png')


def run(home):
    from workbench.store import Store
    from gui.main_window import AplusMainWindow
    from gui.chart_items import ChartItem
    home = Path(home).resolve()
    if not home.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('验收只允许 .test-pa-growth 隔离目录')
    samples(home / 'samples')
    store = Store(home / 'runtime')
    window = AplusMainWindow(store=store, enable_tray=False)
    window.title(f'Aplus PA 验收 · {home.name} · 隔离测试样本')
    def chart():
        days = pd.bdate_range('2026-01-05', periods=180).strftime('%Y-%m-%d').tolist()
        close = [20 + n * .025 + math.sin(n / 8) for n in range(180)]
        frame = pd.DataFrame(dict(date=days, open=[v-.1 for v in close], high=[v+.3 for v in close],
                                  low=[v-.3 for v in close], close=close, volume=[1000]*180))
        item = ChartItem(code='TEST.ETHUSD', name='测试样本 · 非真实行情', observation_id=None,
                         source='test-synthetic', mode='watch', timeframe='daily', manual=True)
        window.chart._items = [item]
        window.chart._display_items = [item]
        slot = window.chart._slots[0]
        slot._code, slot._tf = item.code, 'daily'
        slot._title_var.set('测试样本 · 非真实行情 · 日线')
        slot._workspace_context = dict(frame=frame, code=item.code, name=item.name, timeframe='daily',
            context_kind='watch', record=dict(source='test-synthetic', adjustment='不复权'))
        slot._show_interactive()
        slot._intraday_on = False  # Offline fixture; never request real prices.
    window.after(500, chart)
    if '--growth' in sys.argv[2:]:
        window.after(1500, lambda: window._switch_section('growth'))
    window.mainloop()


if __name__ == '__main__':
    run(sys.argv[1])
