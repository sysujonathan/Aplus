"""Aplus 桌面工作台 GUI 包（第一批：外壳重建）。

只负责展示与交互，不重新实现策略或数据逻辑。
后端 service / store 通过 AplusMainWindow 构造参数注入；第一批未连接时显示占位数据，
后续批次（第二批接候选、第三批接关注、第四批接 K 线/AI）再逐步接线。
"""

from .main_window import AplusMainWindow

__all__ = ["AplusMainWindow"]
