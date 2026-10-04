from types import SimpleNamespace
from unittest.mock import Mock

from gui.replay_navigation import ReplayNavigation


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
