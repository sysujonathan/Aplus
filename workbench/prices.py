"""Price increments for the A-share stock universe (not funds or B shares)."""
from decimal import Decimal

from .market import board_of, code_of


def tick_size(code, price):
    value = Decimal(str(price))
    if not value.is_finite() or value <= 0:
        raise ValueError('价格必须为有限正数')
    if code and board_of(code_of(code)) is None:
        raise ValueError('尚未配置该证券的 tick，不能套用 A 股股票价格规则')
    return .01


def offset_tick(code, reference, direction):
    if direction not in (-1, 1):
        raise ValueError('tick 方向必须为 +1 或 -1')
    return float(Decimal(str(reference)) + direction * Decimal(str(tick_size(code, reference))))
