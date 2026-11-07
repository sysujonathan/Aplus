"""Small application-icon entry shared by desktop and lightweight installers."""
import logging
from pathlib import Path


APPLICATION_TITLE = 'APLUS 操盘台'


def application_icon():
    # Both source and PyInstaller deploy this compatibility outlet beside
    # workbench/. Do not depend on cwd, Store, theme initialization or fonts.
    return Path(__file__).resolve().parents[1]/'icon_aplus.ico'


def apply_window_icon(window):
    """Use the same icon for this Tk root and its later child dialogs."""
    icon = application_icon()
    import tkinter as tk
    try:
        if not icon.is_file():
            logging.getLogger(__name__).warning('应用图标不存在：%s',icon)
            return False
        # Tk on Windows may silently accept a damaged ICO as a generic file
        # icon. Diagnose that fallback without blocking business startup.
        with icon.open('rb') as resource:
            header=resource.read(6)
        if len(header)!=6 or header[:4]!=b'\x00\x00\x01\x00' or header[4:]==b'\x00\x00':
            logging.getLogger(__name__).warning('应用图标格式无效：%s',icon)
            return False
        # ttkbootstrap's explicit iconphoto is not replaced by a default icon.
        # Set the current root as well as the default for later child windows.
        window.iconbitmap(str(icon))
        window.iconbitmap(default=str(icon))
        return True
    except (tk.TclError, OSError):
        logging.getLogger(__name__).warning('设置应用图标失败：%s',icon,exc_info=True)
        return False  # A cosmetic resource must not block data protection/startup.
