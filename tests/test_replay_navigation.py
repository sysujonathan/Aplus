from types import SimpleNamespace
from unittest.mock import Mock

from gui.replay_navigation import ReplayNavigation
from gui.replay_navigation import candle_readout


def test_chart_zoom_anchor_pan_price_axis_and_reset():
    widget, redraw = Mock(), Mock()
    widget.winfo_height.return_value = 600
    nav = ReplayNavigation(widget, redraw)
    nav.info = dict(total=300, bars=120, offset=0, price_x=(70, 890))
    nav.wheel(SimpleNamespace(delta=120, x=480))
    assert nav.bars == 96 and nav.offset == 12  # same middle candle
    nav.info.update(bars=96, offset=12)
    nav.press(SimpleNamespace(x=920, y=100))
    nav.drag(SimpleNamespace(x=920, y=200))
    assert nav.price_scale > 1 and nav.offset == 12
    nav.release(None)
    nav.press(SimpleNamespace(x=480, y=100))
    nav.drag(SimpleNamespace(x=890, y=100))
    assert nav.offset == 60 and nav.price_scale > 1
    nav.reset()
    assert nav.viewport == (None, 0, 1.0)
    assert redraw.call_count == 4


def test_chart_navigation_never_requests_negative_or_future_offsets():
    widget = Mock()
    widget.winfo_height.return_value = 600
    nav = ReplayNavigation(widget, Mock())
    nav.info = dict(total=125, bars=125, offset=0, price_x=(70, 890))
    nav.wheel(SimpleNamespace(delta=-120, x=200))
    assert nav.bars == 125 and nav.offset == 0
    nav.press(SimpleNamespace(x=200, y=100))
    nav.drag(SimpleNamespace(x=-9000, y=100))
    assert nav.offset == 0


def test_hover_reads_vertical_candle_band_in_price_and_volume_without_redraw():
    from PIL import Image
    widget, redraw, hover = Mock(), Mock(), Mock()
    nav = ReplayNavigation(widget, redraw, hover)
    image = Image.new('RGB', (500, 300))
    image.info['replay_view'] = dict(price_x=(20, 400), plot_y=(30, 270),
        candle_x=[40, 80, 120], decimals=3,
        candles=[dict(date=f'2026-09-0{i}', open=1.234, high=1.238, low=1.231, close=1.236)
                 for i in range(1, 4)])
    nav.accept(image)
    nav.motion(SimpleNamespace(x=89, y=100))
    assert hover.call_args.args[0] == '2026-09-02   开 1.234  高 1.238  低 1.231  收 1.236'
    nav.motion(SimpleNamespace(x=89, y=260))
    assert '2026-09-02' in hover.call_args.args[0]
    for x, y in ((10,100),(450,100),(89,10),(89,290),(200,100)):
        assert candle_readout(nav.info, x, y) == ''
    assert redraw.call_count == 0
    nav.accept(Image.new('RGB', (500,300)))
    nav.motion(SimpleNamespace(x=89, y=100))
    assert hover.call_args.args[0] == ''


def test_h2_leader_is_diagonal_dashed_and_separate_from_actual_high():
    import matplotlib.pyplot as plt
    import pandas as pd
    from gui.h2_chart import draw_h2
    fig, ax = plt.subplots()
    try:
        ax.set_xlim(-1, 5)
        ax.set_ylim(5, 20)
        plot = pd.DataFrame(dict(date=['2026-09-01', '2026-09-02'], high=[11, 12]))
        plan = dict(pending_state='TRIGGERED', pending_end_date='2026-09-02',
                    h2_setup_date='2026-09-01', h2_high=11, replay_caption='Trigger confirmed')
        draw_h2(ax, plot, plan)
        annotation = next(t for t in ax.texts if t.get_text() == 'H2')
        assert annotation.xy == (1, 12)
        assert abs(annotation.get_position()[0]) >= 20
        assert annotation.arrow_patch.get_linestyle() == '--'
        assert annotation.arrow_patch.shrinkB >= 5
    finally:
        plt.close(fig)
