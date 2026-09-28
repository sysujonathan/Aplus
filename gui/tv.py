"""TradingView 链接构造（纯函数，零重依赖；与 app.tv_link 逻辑一致）。

gui 桌面端不能 import app.py（Streamlit 入口，模块级会启动整个 Web 运行时），
故把 tv_link 抽成独立纯函数模块，桌面与 Web 各取所需、行为一致。
"""
from __future__ import annotations

from urllib.parse import urlencode

_PREFIX_MAP = {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}


def tv_link(code: str, timeframe: str = "daily") -> str:
    """把 `sh.603955` 这类本地代码转成 TradingView 图表链接。"""
    prefix, ticker = code.split(".")
    return "https://www.tradingview.com/chart/?" + urlencode(
        {
            "symbol": _PREFIX_MAP[prefix] + ":" + ticker,
            "interval": "W" if timeframe == "weekly" else "D",
        }
    )
