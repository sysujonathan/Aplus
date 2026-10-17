"""Display-only source selection, shared by the interactive and fallback charts."""
EMA_SOURCES = ('open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4')


def price_source(frame, source):
    if source == 'hl2':
        return (frame.high + frame.low) / 2
    if source == 'hlc3':
        return (frame.high + frame.low + frame.close) / 3
    if source == 'ohlc4':
        return (frame.open + frame.high + frame.low + frame.close) / 4
    if source not in EMA_SOURCES:
        raise ValueError('不支持的均线价格来源')
    return frame[source]
