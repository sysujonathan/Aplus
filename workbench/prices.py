"""Price increments selected by confirmed trading instrument, never by price digits."""
from decimal import Decimal

from .market import board_of, code_of


TICK_RULES = {'a_share_stock': Decimal('.01'), 'etf': Decimal('.001'),
              'exchange_fund': Decimal('.001')}


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
    if direction not in (-1, 1):
        raise ValueError('tick 方向必须为 +1 或 -1')
    tick = tick_size(code, reference, instrument_type=instrument_type)
    return float(Decimal(str(reference)) + direction * Decimal(str(tick)))


def reaches_entry_tick(code, reference_high, current_high, *, instrument_type=None):
    """Require a full upward tick; never relax the trigger with a tolerance."""
    tick = Decimal(str(tick_size(code, reference_high, instrument_type=instrument_type)))
    high = Decimal(str(current_high))
    if not high.is_finite() or high <= 0:
        raise ValueError('价格必须为有限正数')
    return high >= Decimal(str(reference_high)) + tick
