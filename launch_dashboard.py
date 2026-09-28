# -*- coding: utf-8 -*-
"""Aplus 桌面工作台启动器（第一批：GUI 外壳）。

保留旧 A 的桌面启动习惯。后端 service / store 在第一批未强制连接，窗口以占位壳启动；
第二批起注入真实 service / store。无显示器环境（无头服务器）会因无法创建 Tk 而报错，
异常写入 TEMP 日志，避免双击启动失败时看不到报错。
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "aplus_dashboard.log")


def build_backend():
    """尝试构建真实后端；失败返回 (None, None)，外壳以调试模式启动。"""
    try:
        from workbench.store import Store
        from workbench.service import Service

        store = Store()
        service = Service(store)
        return service, store
    except Exception:
        return None, None


def main():
    try:
        from gui.main_window import AplusMainWindow

        service, store = build_backend()
        app = AplusMainWindow(service=service, store=store)
        app.mainloop()
    except Exception:
        with open(LOG_PATH, "w", encoding="utf-8") as log_file:
            traceback.print_exc(file=log_file)
        sys.exit(1)


if __name__ == "__main__":
    main()
