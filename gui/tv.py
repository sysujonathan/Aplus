"""桌面与 Web 共用的简体中文 TradingView 链接构造（纯函数）。"""
from __future__ import annotations

from urllib.parse import urlencode

_PREFIX_MAP = {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}


def tv_link(code: str, timeframe: str = "daily") -> str:
    """把 `sh.603955` 这类本地代码转成 TradingView 图表链接。"""
    prefix, ticker = code.split(".")
    return "https://cn.tradingview.com/chart/?" + urlencode(
        {
            "symbol": _PREFIX_MAP[prefix] + ":" + ticker,
            "interval": "W" if timeframe == "weekly" else "D",
            "locale": "zh_CN",
        }
    )
