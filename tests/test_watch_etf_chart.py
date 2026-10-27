"""Watch-list ETFs must chart on their own 0.001 tick, not borrowed stock rules."""
import threading

import pandas as pd
import pytest

from gui.chart_bridge import workspace_boot
from gui.watch_chart import load_chart
from workbench.prices import instrument_type_of, tick_size
from workbench.store import Store
from workbench.watch_history import chart_source, fetch_history


def bars(end='2026-10-16', count=12, base=3.900):
    dates = pd.bdate_range(end=end, periods=count).strftime('%Y-%m-%d').tolist()
    closes = [round(base + index * 0.002, 3) for index in range(count)]
    return pd.DataFrame(dict(date=dates, open=closes,
                             high=[round(value + 0.004, 3) for value in closes],
                             low=[round(value - 0.004, 3) for value in closes],
                             close=closes, volume=[1000.0] * count))


class Provider:
    def __init__(self, frame=None, error=None):
        self.frame, self.error, self.calls = frame, error, []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def fetch_unadjusted(self, code, start, end):
        self.calls.append((code, start, end))
        if self.error:
            raise self.error
        return self.frame


@pytest.mark.parametrize('code, kind', [
    # On-exchange funds all use the 0.001 tick. The sz.16 range also carries LOF
    # and sz.18 carries REITs, so the name is exchange_fund rather than ETF.
    ('sh.510300', 'exchange_fund'), ('sh.518880', 'exchange_fund'),
    ('sh.588000', 'exchange_fund'), ('sz.159915', 'exchange_fund'),
    ('sz.161725', 'exchange_fund'), ('sz.180101', 'exchange_fund'),
    ('sh.600000', 'a_share_stock'), ('sz.000001', 'a_share_stock'),
    ('sz.300750', 'a_share_stock'), ('sh.688981', 'a_share_stock'),
    # Convertible bonds and repo share the first digit with funds but are neither.
    ('sz.123456', None), ('sz.128001', None), ('sh.113001', None), ('sh.110043', None),
])
def test_instrument_type_comes_from_exchange_code_range(code, kind):
    assert instrument_type_of(code) == kind


def test_unknown_instrument_still_refuses_to_borrow_a_stock_tick():
    with pytest.raises(ValueError, match='不能默认套用'):
        tick_size('sh.113001', 101.5)
    assert tick_size('sh.510300', 3.901, instrument_type='exchange_fund') == 0.001
    assert tick_size('sh.510300', 3.901, instrument_type=instrument_type_of('sh.510300')) == 0.001


@pytest.mark.parametrize('code', ['sh.510300', 'sz.159915'])
def test_watch_etf_chart_renders_end_to_end(tmp_path, code):
    store = Store(tmp_path)
    fetch_history(store, code, chart_source(store, code), threading.Event(),
                  provider_factory=lambda: Provider(bars()), end='2026-10-16')
    reply = load_chart(store, None, code, chart_source(store, code), 'daily', 'local', lambda: None)
    assert reply is not None
    assert reply['instrument_type'] == 'exchange_fund'
    assert reply['record']['end'] == '2026-10-16'
    assert reply['image'].size[0] > 100  # a real chart, not an error placeholder


def test_watch_etf_workspace_boots_with_fund_tick_and_drawings(tmp_path):
    store = Store(tmp_path)
    frame, record = fetch_history(store, 'sh.510300', 'baostock', threading.Event(),
                                  provider_factory=lambda: Provider(bars()), end='2026-10-16')
    boot = workspace_boot(store, dict(frame=frame, record=record, code='sh.510300', name='沪深300ETF',
                                      timeframe='daily', payload={}, metadata={}, strategy_type='',
                                      context_kind='watch', display_timeframe='daily',
                                      instrument_type=frame.attrs.get('instrument_type')))
    assert boot['priceStep'] == 0.001
    assert boot['contextKind'] == 'watch'
    assert len(boot['bars']) == len(frame)


def test_stock_watch_chart_keeps_the_stock_tick(tmp_path):
    store = Store(tmp_path)
    stock = bars(base=10.0)
    frame, record = fetch_history(store, 'sh.600000', 'baostock', threading.Event(),
                                  provider_factory=lambda: Provider(stock), end='2026-10-16')
    assert frame.attrs['instrument_type'] == 'a_share_stock'
    boot = workspace_boot(store, dict(frame=frame, record=record, code='sh.600000', name='浦发银行',
                                      timeframe='daily', payload={}, metadata={}, strategy_type='',
                                      context_kind='watch', display_timeframe='daily',
                                      instrument_type=frame.attrs.get('instrument_type')))
    assert boot['priceStep'] == 0.01


def test_unsupported_security_says_so_instead_of_a_column_error(tmp_path):
    """LOF/convertible bonds come back empty from BaoStock; the user needs a cause."""
    store = Store(tmp_path)
    with pytest.raises(ValueError, match='没有可用日线'):
        fetch_history(store, 'sz.161725', 'baostock', threading.Event(),
                      provider_factory=lambda: Provider(pd.DataFrame()), end='2026-10-16')
