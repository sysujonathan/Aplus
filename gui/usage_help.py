"""Offline help: read packaged documentation, never touch trading records."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk


def show_usage(window, root=None):
    existing = getattr(window, "_usage_window", None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        return existing
    root = Path(root or Path(__file__).resolve().parents[1])
    try:
        documents = [(title, (root / "docs" / name).read_text(encoding="utf-8"))
                     for title, name in (("快速上手", "快速上手.md"), ("详细说明", "使用说明.md"),
                                         ("软件更新", "版本更新.md"))]
    except OSError:
        messagebox.showinfo("使用说明", "说明文件缺失，请核对软件安装；行情和交易记录不受影响。", parent=window)
        return None
    dialog = window._usage_window = tk.Toplevel(window)
    dialog.title("Aplus 使用说明")
    dialog.geometry("820x640")
    dialog.minsize(600, 400)
    tabs = ttk.Notebook(dialog)
    tabs.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
    for title, content in documents:
        page = ttk.Frame(tabs)
        tabs.add(page, text=title)
        text = tk.Text(page, wrap=tk.WORD, padx=14, pady=12, font=("Microsoft YaHei UI", 11))
        scrollbar = ttk.Scrollbar(page, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)
    return dialog
