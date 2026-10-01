# -*- coding: utf-8 -*-
"""Aplus 桌面工作台启动器。

注入真实 service / store，并用 Windows 命名互斥锁阻止重复实例。无显示器环境
（无头服务器）会因无法创建 Tk 而报错；异常写入 TEMP 日志，避免双击启动失败时
看不到报错。
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "aplus_dashboard.log")
_MUTEX_NAME = "Local\\AplusDesktopWorkbench"


def acquire_single_instance(notify=True, name=_MUTEX_NAME):
    """Windows 命名互斥锁：第二次启动只提示，不创建第二套后端。"""
    if os.name != "nt":
        return object()
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        if notify:
            ctypes.windll.user32.MessageBoxW(
                0,
                "A 工具已经在运行。请从右下角系统托盘打开主界面。",
                "Aplus",
                0x40,
            )
        return None
    return handle


def release_single_instance(handle):
    if os.name == "nt" and handle:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


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
    mutex = acquire_single_instance()
    if mutex is None:
        return
    try:
        from gui.main_window import AplusMainWindow

        service, store = build_backend()
        app = AplusMainWindow(service=service, store=store, enable_tray=True)
        app.mainloop()
    except Exception:
        with open(LOG_PATH, "w", encoding="utf-8") as log_file:
            traceback.print_exc(file=log_file)
        sys.exit(1)
    finally:
        release_single_instance(mutex)


if __name__ == "__main__":
    main()
