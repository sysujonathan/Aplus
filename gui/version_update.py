"""Non-blocking desktop version hints, separate from market task status."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk

from workbench.version_update import check_version, metadata_dir, prepare_worker, require_web_closed
from workbench.version_update import write_json


def open_forms(window):
    """Any open editing/preview window must be closed before restart."""
    def visit(parent):
        for child in parent.winfo_children():
            if isinstance(child, tk.Toplevel) and child.winfo_exists():
                return True
            if visit(child):
                return True
        return False
    return visit(window)


class VersionController:
    def __init__(self, window, parent, root=None, checker=check_version):
        self.window = window
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.checker = checker
        self.results = queue.SimpleQueue()
        self.busy = False
        self.latest = None
        self.waiting = None
        self.reserved = False
        self.pending_notice = None
        self.notice = None
        self.notified_targets = set()
        self.button = ttk.Button(parent, text="版本更新", width=14, bootstyle="secondary",
                                 command=self.manual_check)
        self.button.pack(side=tk.RIGHT, padx=(8, 0), ipady=2)
        self.poll_id = window.after(200, self.poll)
        self.window.bind("<Destroy>", self.destroyed, add="+")

    def scan_hint(self):
        self.start_check(manual=False)

    def manual_check(self):
        if not self.waiting:
            if self.notice is not None:
                self.notice.destroy()
                self.notice = None
            self.start_check(manual=True)

    def start_check(self, manual):
        if self.busy:
            if manual:
                # A click during the automatic request can opt into viewing its result.
                self.show_result = True
            return
        self.busy = True
        self.show_result = manual
        self.button.configure(text="检查版本…")

        def work():
            try:
                result = self.checker(self.root, force=manual)
            except Exception:
                result = {"state": "unavailable", "can_update": False,
                          "message": "版本检查暂不可用，不影响行情与扫描。"}
            self.results.put(result)
        threading.Thread(target=work, name="aplus-version-check", daemon=True).start()

    def poll(self):
        if getattr(self.window, "_closing", False):
            return
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            result = None
        if result is not None:
            self.busy = False
            self.latest = result
            available = result.get("state") == "available"
            self.button.configure(text="有新版 · 查看" if available else "版本更新",
                                  bootstyle="warning" if available else "secondary")
            if self.show_result:
                self.pending_notice = None
                self.show_details(result)
            elif available and result.get("target"):
                self.pending_notice = result
            else:
                self.pending_notice = None
        self.maybe_show_notice()
        if self.waiting:
            folder, deadline = self.waiting
            if (folder / "ready.json").exists() and not (folder / 'receipt.json').exists():
                self.waiting = None
                self.window._close_for_version_update()
                return
            if (folder / "receipt.json").exists() or time.monotonic() > deadline:
                write_json(folder / 'cancelled.json', {'cancelled': True})
                self.waiting = None
                self.window._update_pending = False
                self.window.deiconify()
                if self.reserved:
                    self.window.service.release_code_update()
                    self.reserved = False
                messagebox.showerror("未更新", "更新保护进程未就绪，当前版本继续使用。\n可查看.git/aplus-update中的更新回执。",
                                     parent=self.window)
        self.poll_id = self.window.after(200, self.poll)

    def maybe_show_notice(self):
        """Offer a release once, only after work/editing finishes; never auto-update."""
        result = getattr(self, "pending_notice", None)
        if not result or self.waiting or self.busy or getattr(self, "notice", None):
            return
        target = result.get("target", "")
        if target in self.notified_targets:
            self.pending_notice = None
            return
        try:
            path = metadata_dir(self.root) / "notice.json"
            if json.loads(path.read_text(encoding="utf-8")).get("target") == target:
                self.notified_targets.add(target)
                self.pending_notice = None
                return
        except (OSError, ValueError):
            pass
        try:
            service = self.window.service
            if (self.window.state() != "normal" and self.window.state() != "zoomed") or open_forms(self.window):
                return
            if service is None or self.window.store is None:
                return
            # This is a read-only hint check, not the atomic update reservation.
            # begin_update still rechecks and reserves before any code replacement.
            if service.cancel_flags or service._code_update_reserved or service.store.rows(
                    "SELECT id FROM jobs WHERE status IN ('queued','running')"):
                return
            self.show_notice(result)
        except (tk.TclError, OSError, ValueError):
            return  # A version reminder must not break the trading UI.
        self.notified_targets.add(target)
        self.pending_notice = None
        try:
            write_json(path, {"target": target})
        except OSError:
            pass  # Still suppressed for this session if metadata is unwritable.

    def show_notice(self, result):
        dialog = self.notice = tk.Toplevel(self.window)
        dialog.title("软件更新")
        dialog.transient(self.window)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="发现已通过验收的新版", font=("Microsoft YaHei", 12, "bold")).pack(anchor=tk.W)
        local = result.get("local", {})
        text = (f"本地提交 {local.get('head', '未确认')[:7]} → 主线 {result['target'][:7]}\n"
                "只更新软件，保留行情和交易记录。\n"
                "稍后更新也可以继续使用当前版本。")
        if not result.get("can_update"):
            text += "\n\n" + result.get("message", "请先查看更新条件。")
        ttk.Label(body, text=text, wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, pady=12)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X)

        def close():
            dialog.destroy()
            self.notice = None

        def accept():
            close()  # Don't mistake this prompt for an unsaved editing form.
            self.show_details(result)  # Explicit confirmation + existing safety checks.

        ttk.Button(actions, text="更新并重启" if result.get("can_update") else "查看更新条件",
                   command=accept, bootstyle="primary").pack(side=tk.RIGHT)
        ttk.Button(actions, text="稍后", command=close, bootstyle="secondary").pack(side=tk.RIGHT, padx=8)
        dialog.protocol("WM_DELETE_WINDOW", close)
        # No grab_set / focus_force: this optional prompt must not hijack editing.

    def show_details(self, result):
        local = result.get("local", {})
        lines = [f"本地：{local.get('branch', '未确认')} · {local.get('head', '')[:7]}",
                 "本地有修改（提交号不代表完整代码）" if local.get("dirty") else "",
                 f"主线：{result.get('target', '未确认')[:7]}", result.get("message", ""),
                 result.get("summary", "")]
        text = "\n".join(line for line in lines if line)
        try:
            base = metadata_dir(self.root)
            receipts = sorted(base.glob("*/receipt.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if receipts:
                previous = json.loads(receipts[0].read_text(encoding="utf-8"))
                text += "\n上次更新：" + previous.get("message", "待核对")
        except (OSError, ValueError):
            pass
        if not result.get("can_update"):
            messagebox.showinfo("版本检查", text, parent=self.window)
            return
        if messagebox.askyesno("版本更新", text + "\n\n请先保存编辑并关闭弹窗。\n只更新代码，保留行情与交易记录。\n现在更新并重启？",
                               parent=self.window):
            self.begin_update(result)

    def begin_update(self, result):
        service = self.window.service
        if service is None or self.window.store is None:
            messagebox.showinfo("暂不能更新", "后端未连接，请人工检查安装。", parent=self.window)
            return
        if open_forms(self.window):
            messagebox.showinfo("请先关闭弹窗", "请保存并关闭持仓编辑、导入预览等窗口后再更新。", parent=self.window)
            return
        try:
            require_web_closed()
            service.reserve_code_update()
            self.reserved = True
            # Hide interaction before starting the helper, but don't exit until
            # it has a real handle on this process. Failed preparation restores UI.
            self.window._update_pending = True
            self.window.withdraw()
            folder = prepare_worker(self.root, result, self.window.store.root)
            self.waiting = (folder, time.monotonic() + 8)
        except Exception as exc:
            self.window._update_pending = False
            self.window.deiconify()
            if self.reserved:
                service.release_code_update()
                self.reserved = False
            messagebox.showinfo("暂不能更新", str(exc), parent=self.window)

    def destroyed(self, event):
        if event.widget is self.window:
            if self.waiting:
                write_json(self.waiting[0] / 'cancelled.json', {'cancelled': True})
            try:
                self.window.after_cancel(self.poll_id)
            except tk.TclError:
                pass
