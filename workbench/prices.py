"""Price increments selected by confirmed trading instrument, never by price digits."""
import re
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .market import board_of, code_of


TICK_RULES = {'a_share_stock': Decimal('.01'), 'etf': Decimal('.001'),
              'exchange_fund': Decimal('.001')}

# Exchange code ranges allocated to on-exchange funds. Deliberately narrow: sz.1
# also holds convertible bonds (sz.11/12) and repo (sz.13), which must never be
# reported as funds just because they share a first digit.
FUND_CODE = re.compile(r'(?:sh\.(?:50|51|52|56|58)\d{4}|sz\.(?:15|16|18)\d{4})')


def instrument_type_of(code):
    """Confirmed instrument from the exchange code range; None when unknown.

    Charting needs the tick to render prices, and the same rules refuse to guess.
    A stock's board and a fund's code range are both public allocations, so this
    is evidence rather than an inference from price digits.

    The fund ranges are wider than ETF: sz.16 also carries LOF and sz.18 carries
    REITs, so ``exchange_fund`` is the honest name — they all trade on the 0.001
    tick. Whether a market source actually serves one of those products is a
    separate question answered by the fetch path, never by this classification.
    """
    security = code_of(code)
    if board_of(security) is not None:
        return 'a_share_stock'
    if FUND_CODE.fullmatch(security):
        return 'exchange_fund'
    return None


def tick_size(code, price, *, instrument_type=None):
    value = Decimal(str(price))
    if not value.is_finite() or value <= 0:
        raise ValueError('价格必须为有限正数')
    security = code_of(code) if code else None
    stock = security is not None and board_of(security) is not None
    kind = instrument_type or ('a_share_stock' if stock else None)
    if kind not in TICK_RULES:
        raise ValueError('尚未确认该证券的交易品种或 tick，不能默认套用 0.01')
    if security:
        if kind == 'a_share_stock' and not stock:
            raise ValueError('证券代码与股票品种不符，不能确定 tick')
        if kind in ('etf', 'exchange_fund') and (stock or security[:2] not in ('sh', 'sz')):
            raise ValueError('证券代码与沪深场内基金品种不符，不能确定 tick')
    return float(TICK_RULES[kind])


def offset_tick(code, reference, direction, *, instrument_type=None):
    """Legacy arithmetic offset; retained for v1 research and experiment C."""
    if direction not in (-1, 1):
        raise ValueError('tick 方向必须为 +1 或 -1')
    tick = tick_size(code, reference, instrument_type=instrument_type)
    return float(Decimal(str(reference)) + direction * Decimal(str(tick)))


def grid_offset_tick(code, reference, direction, *, instrument_type=None):
    """Offset once, then align outward so the requested distance is never reduced."""
    if direction not in (-1, 1):
        raise ValueError('tick 方向必须为 +1 或 -1')
    value = Decimal(str(reference))
    tick = Decimal(str(tick_size(code, reference, instrument_type=instrument_type)))
    if not value.is_finite() or value <= 0:
        raise ValueError('价格必须为有限正数')
    raw = value + direction * tick
    rounding = ROUND_CEILING if direction > 0 else ROUND_FLOOR
    aligned = (raw / tick).to_integral_value(rounding=rounding) * tick
    if aligned <= 0:
        raise ValueError('按 tick 调整后的价格必须为正数')
    return float(aligned)


def reaches_entry_tick(code, reference_high, current_high, *, instrument_type=None):
    """Require a full upward tick; never relax the trigger with a tolerance."""
    tick = Decimal(str(tick_size(code, reference_high, instrument_type=instrument_type)))
    high = Decimal(str(current_high))
    if not high.is_finite() or high <= 0:
        raise ValueError('价格必须为有限正数')
    return high >= Decimal(str(reference_high)) + tick


def reaches_grid_entry(code, reference_high, current_high, *, instrument_type=None):
    """Reach the actual outward-aligned v2 entry price, not an off-grid threshold."""
    high = Decimal(str(current_high))
    if not high.is_finite() or high <= 0:
        raise ValueError('价格必须为有限正数')
    entry = Decimal(str(grid_offset_tick(
        code, reference_high, 1, instrument_type=instrument_type)))
    return high >= entry
