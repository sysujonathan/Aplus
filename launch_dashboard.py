# -*- coding: utf-8 -*-
"""Aplus 桌面工作台启动器。

注入真实 service / store，并用 Windows 命名互斥锁阻止重复实例。无显示器环境
（无头服务器）会因无法创建 Tk 而报错；异常写入 TEMP 日志，避免双击启动失败时
看不到报错。
"""
from __future__ import annotations

import os
import logging
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "aplus_dashboard.log")
_MUTEX_NAME = "Local\\AplusDesktopWorkbench"
# Installed bootstrap supplies the existing conservative maintenance callback.
idle_maintenance = None


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
    """Build the real backend on the startup worker; failures remain visible."""
    from workbench.startup_metrics import span
    with span('backend.import'):
        from workbench.store import Store
        from workbench.acceleration_profile import configure_service
    with span('backend.store'):
        store = Store()
    with span('backend.service'):
        service = configure_service(store)
    return service, store


def prepare_workbench():
    from workbench.startup_metrics import span
    service,store=build_backend()
    if service is None or store is None:
        raise RuntimeError('本地数据或后端未就绪，未打开空白工作台。')
    with span('window.import'):
        from gui.main_window import AplusMainWindow
    return service,store,AplusMainWindow


def main():
    mutex = acquire_single_instance()
    if mutex is None:
        return
    try:
        # pythonw 双击启动没有可见控制台，标注诊断也应保留到桌面日志。
        logging.basicConfig(
            filename=LOG_PATH, encoding="utf-8", level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        from gui.startup_loading import initialize
        from workbench.startup_metrics import span, record
        prepared=initialize(prepare_workbench)
        if prepared is None:return
        service,store,window_type=prepared
        with span('window.construct'):
            app = window_type(service=service, store=store, enable_tray=True)
        if idle_maintenance:
            app.schedule_startup_maintenance(idle_maintenance)
        app.after_idle(lambda:record('window.usable'))
        ack = os.environ.pop("APLUS_UPDATE_ACK", None)
        if ack and service is not None and store is not None:
            # A spawn is not startup success. The helper waits for the actual
            # backend and native window, not merely a newly created process.
            from pathlib import Path
            from workbench.version_update import metadata_dir, write_json
            ack_path = Path(ack).resolve()
            base = metadata_dir(ROOT).resolve()
            if ack_path.name == "started.json" and ack_path.parent.parent == base:
                app.after_idle(lambda: write_json(ack_path, {"started": True}))
        app.mainloop()
    except Exception as exc:
        logging.getLogger(__name__).exception("桌面工作台启动或运行失败")
        try:
            from tkinter import messagebox
            messagebox.showerror('工作台打开失败',
                '工作台创建或运行失败，请关闭后重试。原数据不会被空库替代。\n'
                +str(exc)+'\n诊断日志：'+LOG_PATH)
        except Exception:
            logging.getLogger(__name__).exception('无法显示启动错误提示')
        sys.exit(1)
    finally:
        from workbench import startup_metrics
        if startup_metrics.early_watchdog:
            startup_metrics.early_watchdog.stop();startup_metrics.early_watchdog=None
        release_single_instance(mutex)


if __name__ == "__main__":
    main()
