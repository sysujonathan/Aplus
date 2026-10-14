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
from workbench.version_update import local_version
from workbench.releases import identity, presentation


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
        try:
            self.running = identity(self.root, local_version(self.root))
        except (OSError, ValueError):
            self.running = identity(self.root)
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
        self.startup_check_id = window.after(4000, self.startup_check)
        self.window.bind("<Destroy>", self.destroyed, add="+")

    def startup_check(self):
        """Check quietly after the desktop is usable; never delay first paint."""
        self.startup_check_id = None
        if not getattr(self.window, "_closing", False):
            self.start_check(manual=False)

    def scan_hint(self):
        self.start_check(manual=False)

    def manual_check(self):
        if not self.waiting:
            if self.notice is not None:
                self.notice.destroy()
                self.notice = None
            self.start_check(manual=True)

    def start_check(self, manual, *, show_result=None):
        if self.busy:
            if manual:
                # A click during the automatic request can opt into viewing its result.
                self.show_result = True
            return
        self.busy = True
        self.show_result = manual if show_result is None else show_result
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
            if available and result.get("block_reason") == "cached":
                # A same-day cache is useful for discovering a release, but it is
                # deliberately never update permission.  Confirm it immediately
                # in the background instead of making the user understand and
                # perform this safety step.
                self.pending_notice = None
                self.button.configure(text="正在确认新版…", bootstyle="warning")
                self.start_check(manual=True, show_result=False)
                self.poll_id = self.window.after(200, self.poll)
                return
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
                messagebox.showerror("未更新", "更新没有开始，当前版本继续使用。\n"
                                     "请重新打开“版本更新”后再试；若仍失败，请联系维护者。",
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
        if self.notice is not None:
            self.notice.destroy()
        view = presentation(result, getattr(self, "running", None))
        editing = open_forms(self.window)
        service = self.window.service
        active = bool(service and (service.cancel_flags or service._code_update_reserved or
                      service.store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')")))
        dialog = self.notice = tk.Toplevel(self.window)
        dialog.title("软件更新")
        dialog.transient(self.window)
        dialog.resizable(True, True)
        dialog.minsize(520, 340)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=view["title"], font=("Microsoft YaHei", 12, "bold")).pack(anchor=tk.W)
        text = f"正在运行：{view['current']}\n正式版：{view['target']}"
        if result.get("day"):
            text += f"\n检查日期：{result['day']}"
        if view['revision_note']:
            text += '\n' + view['revision_note']
        text += "\n\n" + view["message"]
        if view["can_update"]:
            text += "\n点击“立即更新并重启”后将自动完成备份、更新和重新打开。"
        if editing:
            text += "\n请先保存并关闭编辑/预览窗口，再重新检查。"
        elif active:
            text += "\n请等待行情、扫描或研究任务结束，再重新检查。"
        if view["notes"]:
            text += "\n\n正式版更新摘要：\n" + "\n".join("• " + n for n in view["notes"])
        ttk.Label(body, text=text, wraplength=540, justify=tk.LEFT).pack(anchor=tk.W, pady=12)
        details = ttk.Frame(body)
        def toggle_details():
            if details.winfo_manager():
                details.pack_forget()
            else:
                details.pack(fill=tk.BOTH, expand=True, before=primary_actions, pady=8)
        technical = tk.Text(details, height=7, width=64, wrap=tk.WORD)
        technical.pack(fill=tk.BOTH, expand=True)
        local = result.get("local", {})
        technical.insert("1.0", f"启动时提交：{getattr(self, 'running', {}).get('head', '未确认')}\n"
                         f"磁盘分支：{local.get('branch', '未确认')}\n磁盘提交：{local.get('head', '未确认')}\n"
                         f"受保护的本机文件：{'有（更新时自动保留）' if local.get('preserved') else '无'}\n"
                         f"其他代码改动：{'有' if local.get('dirty') else '无'}\n"
                         f"主线提交：{result.get('target', '未确认')}\n{result.get('message', '')}")
        technical.configure(state=tk.DISABLED)
        from gui.usage_help import show_usage
        links = ttk.Frame(body)
        links.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(links, text="更新日志", command=lambda: show_usage(self.window, tab="更新日志")).pack(side=tk.LEFT)
        ttk.Button(links, text="技术详情", command=toggle_details).pack(side=tk.LEFT, padx=8)
        def close():
            dialog.destroy()
            self.notice = None

        def accept():
            if not view["can_update"] or editing or active:
                return
            close()  # Don't mistake this prompt for an unsaved editing form.
            self.begin_update(result)  # Existing fresh revalidation and reservation.

        primary_actions = ttk.Frame(body)
        primary_actions.pack(fill=tk.X, pady=(6, 12))
        if result.get("state") == "available":
            ttk.Button(primary_actions, text=view['action_label'], command=accept,
                       bootstyle="success", width=24,
                       state=tk.NORMAL if view["can_update"] and not editing and not active else tk.DISABLED
                       ).pack(fill=tk.X, ipady=8)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X)
        manual_block = result.get('block_reason') in ('manual','modified','development') and not view['can_update']
        ttk.Button(actions, text="稍后" if result.get("state") == "available" and not manual_block else "关闭",
                   command=close, bootstyle="secondary").pack(side=tk.RIGHT, padx=8)
        ttk.Button(actions, text="重新检查", command=lambda: (close(), self.manual_check())).pack(side=tk.LEFT)
        dialog.protocol("WM_DELETE_WINDOW", close)
        # No grab_set / focus_force: this optional prompt must not hijack editing.

    def show_details(self, result):
        self.show_notice(result)

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
            if getattr(self, "startup_check_id", None) is not None:
                try:
                    self.window.after_cancel(self.startup_check_id)
                except tk.TclError:
                    pass
