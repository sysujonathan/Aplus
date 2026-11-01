"""Small application-icon entry shared by desktop and lightweight installers."""
from pathlib import Path


APPLICATION_TITLE = 'APLUS 操盘台'


def application_icon():
    # Both source and PyInstaller deploy this compatibility outlet beside
    # workbench/. Do not depend on cwd, Store, theme initialization or fonts.
    return Path(__file__).resolve().parents[1]/'icon_aplus.ico'


def apply_window_icon(window):
    """Use the same icon for this Tk root and its later child dialogs."""
    icon = application_icon()
    if not icon.is_file():return False
    import tkinter as tk
    try:
        window.iconbitmap(default=str(icon))
        return True
    except (tk.TclError, OSError):
        return False  # A cosmetic resource must not block data protection/startup.
