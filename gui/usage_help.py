"""Offline help: read packaged documentation, never touch trading records."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk

from .theme import BORDER, MUTED, PANEL_BG, TEXT


def render_changelog(text, catalog):
    """Read-only typeset view of the catalog, not a Markdown/source-code view."""
    text.configure(state=tk.NORMAL, background=PANEL_BG, foreground=TEXT,
                   relief=tk.FLAT, borderwidth=0, highlightthickness=0,
                   padx=26, pady=20, font=("Microsoft YaHei UI", 11))
    for rule in getattr(text, "_release_rules", []):
        rule.destroy()
    text.delete("1.0", tk.END)
    text.tag_configure("log_title", font=("Microsoft YaHei UI", 22, "bold"), spacing3=14)
    text.tag_configure("log_intro", font=("Microsoft YaHei UI", 10),
                       foreground=MUTED, spacing3=14)
    text.tag_configure("version", font=("Microsoft YaHei UI", 16, "bold"),
                       spacing1=14, spacing3=6)
    text.tag_configure("metadata", font=("Microsoft YaHei UI", 9),
                       foreground=MUTED, spacing3=12)
    text.tag_configure("bullet", spacing1=2, spacing2=3, spacing3=7,
                       lmargin1="8p", lmargin2="22p")
    text.insert(tk.END, "Aplus 更新日志\n", "log_title")
    rules = text._release_rules = []
    if not catalog:
        text.insert(tk.END, "更新日志暂未安装或格式需核对；不影响行情和交易记录。\n", "log_intro")
    else:
        text.insert(tk.END, "按日期＋PR号回溯整理；预览版尚未合入主线。\n"
                    "历史编号不代表当时创建过发布标签；1.1.4等为功能阶段名称。\n", "log_intro")
        rows = list(reversed(catalog["history"]))
        for index, row in enumerate(rows):
            title = "v" + row["version"] if row["version"] else "流程测试（无发布编号）"
            state = {"released": "已合入主线（历史回溯）", "preview": "预览版 · 待合入主线",
                     "included": f"随 PR{row.get('included_in', '')} 纳入主线",
                     "not_released": "未发布"}[row["status"]]
            text.insert(tk.END, title + "  版本更新\n", "version")
            text.insert(tk.END, f"{row['date']}  ·  PR{row['pr']}  ·  {state}\n", "metadata")
            for note in row["notes"]:
                text.insert(tk.END, "•  " + note + "\n", "bullet")
            if index + 1 < len(rows):
                # Real rules resize with the text viewport; no fixed-width hyphens.
                frame = tk.Frame(text, background=PANEL_BG, height=16)
                frame.pack_propagate(False)
                line = tk.Frame(frame, background=BORDER, height=1)
                line.pack(fill=tk.X, side=tk.BOTTOM, pady=(7, 7))
                text.window_create(tk.END, window=frame, align=tk.CENTER)
                text.insert(tk.END, "\n")
                rules.append(frame)
                for widget in (frame, line):
                    # High-DPI scaling (e.g. 200%) makes Tk ignore the
                    # event_generate() synthesized <MouseWheel>, so the text
                    # area would not scroll while the pointer is over the
                    # separator. Scroll it directly instead.
                    widget.bind(
                        "<MouseWheel>",
                        lambda e: text.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                    )

    def resize_rules(event=None):
        width = max(80, text.winfo_width() - 2 * int(text.cget("padx")) - 8)
        for frame in rules:
            frame.configure(width=width)

    text.bind("<Configure>", resize_rules)
    resize_rules()
    text.configure(state=tk.DISABLED)


def show_usage(window, root=None, tab=None):
    existing = getattr(window, "_usage_window", None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        if tab and hasattr(existing, "_help_tabs"):
            for item in existing._help_tabs.tabs():
                if existing._help_tabs.tab(item, "text") == tab:
                    existing._help_tabs.select(item)
        return existing
    root = Path(root or Path(__file__).resolve().parents[1])
    try:
        documents = [(title, (root / "docs" / name).read_text(encoding="utf-8"))
                     for title, name in (("快速上手", "快速上手.md"), ("详细说明", "使用说明.md"),
                                         ("软件更新", "版本更新.md"))]
        from workbench.releases import read_catalog
        catalog = read_catalog(root)
        documents.append(("更新日志", catalog))
    except OSError:
        messagebox.showinfo("使用说明", "说明文件缺失，请核对软件安装；行情和交易记录不受影响。", parent=window)
        return None
    dialog = window._usage_window = tk.Toplevel(window)
    dialog.title("Aplus 使用说明")
    dialog.geometry("820x640")
    dialog.minsize(600, 400)
    tabs = ttk.Notebook(dialog)
    dialog._help_tabs = tabs
    tabs.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
    for title, content in documents:
        page = ttk.Frame(tabs)
        tabs.add(page, text=title)
        text = tk.Text(page, wrap=tk.WORD, padx=14, pady=12, font=("Microsoft YaHei UI", 11))
        scrollbar = ttk.Scrollbar(page, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.configure(yscrollcommand=scrollbar.set)
        if title == "更新日志":
            render_changelog(text, content)
        else:
            text.insert("1.0", content)
        text.configure(state=tk.DISABLED)
        if title == tab:
            tabs.select(page)
    return dialog
