"""右侧观察池（P2：watchlist JOIN observations/plans，读写只走 store 方法）。

读：watchlist LEFT JOIN observations（取策略/信号日）LEFT JOIN plans（取人工状态）。
写：加入关注来自左栏候选右键（main_window 调 store.watch）；
    本面板右键菜单：修改备注（update_watch）/ 切换计划状态（save_plan）/ 结束关注（update_watch）。
单击行 -> on_select(code, observation_id) 替换活动图；拖拽可替换指定图格。
零 schema 改动：全部复用 store 现有表与方法。
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, simpledialog
import ttkbootstrap as ttk

from .theme import APP_BG, PANEL_BG, TEXT
from .tree_scroll import attach_vertical_scrollbar, bind_identity_column_autofit

# 与 store.save_plan 的合法状态保持一致（勿在此处增删）
_PLAN_STATES = ("观察", "计划交易", "已手工入场", "已手工退出", "忽略")


class WatchPanel(ttk.Frame):
    def __init__(self, parent, store=None, on_select=None, on_drag_motion=None,
                 on_drop=None, on_drag_end=None, on_page_request=None):
        super().__init__(parent)
        self.store = store
        self.on_select = on_select
        self.on_drag_motion = on_drag_motion
        self.on_drop = on_drop
        self.on_drag_end = on_drag_end
        self.on_page_request = on_page_request
        self._display_rows = []
        self._drag_item = None
        self._drag_origin = None
        self._dragging = False

        ttk.Label(
            self,
            text="关注列表",
            font=("Microsoft YaHei", 10, "bold"),
            foreground=TEXT,
        ).pack(anchor=tk.W, padx=2, pady=(0, 6))

        self.tree_host = ttk.Frame(self)
        self.tree_host.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            self.tree_host,
            columns=("number", "code", "name", "strategy", "date", "state", "notes"),
            displaycolumns=("number", "code", "name"),
            show="headings",
        )
        for col, text, width, stretch in (
            ("number", "序", 44, False),
            ("code", "代码", 112, False),
            ("name", "名称", 144, False),
            ("strategy", "策略", 52, False),
            ("date", "信号日", 56, False),
            ("state", "状态", 52, False),
            ("notes", "备注", 40, True),
        ):
            self.tree.heading(col, text=text)
            minwidth = 44 if col == "number" else (112 if col == "code" else 90)
            self.tree.column(
                col, width=width, minwidth=minwidth, anchor=tk.CENTER, stretch=stretch
            )
        self.vscroll = attach_vertical_scrollbar(self.tree_host, self.tree)
        bind_identity_column_autofit(self.tree)
        self.tree.tag_configure("row_even", background=PANEL_BG)
        self.tree.tag_configure("row_odd", background=APP_BG)
        self.tree._obs = {}    # iid -> observation_id
        self.tree._codes = {}  # iid -> code

        # 单击在松开时切换活动图；拖拽时不先误改活动图。
        # 右键操作菜单
        self.tree.bind("<Button-3>", self._context_menu)
        # 左键仍可直接切换活动图；按住拖到某个图格则精确替换该格。
        self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._drag_release, add="+")
        self.tree.bind("<Left>", lambda _event: self._request_page(-1))
        self.tree.bind("<Right>", lambda _event: self._request_page(1))
        self.tree.bind("<Up>", lambda _event: self._move_selection(-1))
        self.tree.bind("<Down>", lambda _event: self._move_selection(1))

        if self.store is not None:
            self.reload()

    # ---- 读：观察池（active=1 的关注 + 关联观察与计划状态）----
    def reload(self):
        """从 store 重读观察池。任何写操作后由主窗口/本面板调用刷新。"""
        for child in self.tree.get_children():
            self.tree.delete(child)
        self.tree._obs = {}
        self.tree._codes = {}
        self._display_rows = []
        if self.store is None:
            return
        try:
            rows = self.store.rows(
                "SELECT w.code AS code, w.notes AS wnotes, "
                "o.id AS observation_id, o.strategy AS strategy, o.asof AS asof, "
                "o.timeframe AS timeframe, d.source AS source, "
                "p.state AS state "
                "FROM watchlist w "
                "LEFT JOIN observations o ON o.id = w.observation_id "
                "LEFT JOIN datasets d ON d.id = o.dataset_id "
                "LEFT JOIN plans p ON p.observation_id = o.id "
                "WHERE w.active=1 ORDER BY w.updated DESC"
            )
        except Exception:
            return
        from .data import code_names, latest_market_dataset

        names = code_names(self.store)
        for number, r in enumerate(rows, 1):
            code = r["code"]
            state = r["state"] if r["state"] else "关注中"
            timeframe = r["timeframe"] or "daily"
            # 关注的是股票；旧 observation 只保存当初为什么关注。K 线始终
            # 指向本项目正式行情源的最新快照，且不会读取工程 A 数据源。
            market = latest_market_dataset(self.store, code, timeframe=timeframe)
            item = {
                "code": code,
                "name": names.get(code, ""),
                "observation_id": r["observation_id"],
                "source": (market or {}).get("source") or r["source"] or "unknown",
                "strategy": r["strategy"],
                "timeframe": timeframe,
                "market_dataset_id": (market or {}).get("id"),
                "market_asof": (market or {}).get("end"),
                "anchor_asof": r["asof"],
            }
            self._display_rows.append(item)
            iid = self.tree.insert(
                "", tk.END,
                values=(
                    number,
                    code,
                    item["name"],
                    r["strategy"] or "",
                    (r["asof"] or "")[:10],
                    state,
                    r["wnotes"] or "",
                ),
                tags=("row_even" if number % 2 == 0 else "row_odd",),
            )
            self.tree._obs[iid] = r["observation_id"]
            self.tree._codes[iid] = code

    def rows(self):
        """按关注列表当前顺序提供 K 线分页数据。"""
        return [dict(row) for row in self._display_rows]

    def select_observation(self, observation_id):
        for iid, value in self.tree._obs.items():
            if value == observation_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.tree.focus_set()
                return True
        return False

    def _move_selection(self, delta):
        children = self.tree.get_children()
        if not children:
            return "break"
        selection = self.tree.selection()
        try:
            index = children.index(selection[0]) if selection else 0
        except ValueError:
            index = 0
        index = max(0, min(index + int(delta), len(children) - 1))
        iid = children[index]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.tree.focus_set()
        if self.on_select:
            self.on_select(self.tree._codes.get(iid), self.tree._obs.get(iid))
        return "break"

    def _request_page(self, delta):
        if self.on_page_request:
            self.on_page_request(delta)
        return "break"

    # ---- 选中 -> 主窗口联动 K 线 ----
    def _on_select(self, event):
        if self.on_select is None:
            return
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        code = self.tree._codes.get(iid)
        obs_id = self.tree._obs.get(iid)
        self.on_select(code, obs_id)

    def _drag_start(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            self._drag_item = None
            return
        self.tree.selection_set(iid)
        self._drag_item = (
            self.tree._codes.get(iid),
            self.tree._obs.get(iid),
        )
        self._drag_origin = (event.x_root, event.y_root)
        self._dragging = False

    def _drag_motion(self, event):
        if not self._drag_item or not self._drag_origin:
            return
        dx = abs(event.x_root - self._drag_origin[0])
        dy = abs(event.y_root - self._drag_origin[1])
        if max(dx, dy) < 6:
            return
        self._dragging = True
        self.tree.configure(cursor="hand2")
        if self.on_drag_motion:
            self.on_drag_motion(event.x_root, event.y_root)

    def _drag_release(self, event):
        try:
            if self._dragging and self._drag_item and self.on_drop:
                code, observation_id = self._drag_item
                self.on_drop(code, observation_id, event.x_root, event.y_root)
            elif self._drag_item and self.on_select:
                code, observation_id = self._drag_item
                self.on_select(code, observation_id)
        finally:
            self.tree.configure(cursor="")
            self._drag_item = None
            self._drag_origin = None
            self._dragging = False
            if self.on_drag_end:
                self.on_drag_end()

    # ---- 右键菜单 ----
    def _context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        code = self.tree._codes.get(iid)
        obs_id = self.tree._obs.get(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="修改备注", command=lambda: self._edit_notes(code))
        state_menu = tk.Menu(menu, tearoff=0)
        for st in _PLAN_STATES:
            state_menu.add_command(
                label=st, command=lambda s=st: self._change_state(code, obs_id, s)
            )
        state_label = "计划状态…"
        menu.add_cascade(label=state_label, menu=state_menu)
        menu.add_separator()
        menu.add_command(label="结束关注", command=lambda: self._end_watch(code))
        menu.tk_popup(event.x_root, event.y_root)

    # ---- 写：修改备注（update_watch）----
    def _edit_notes(self, code):
        if self.store is None or not code:
            return
        try:
            rows = self.store.rows("SELECT notes FROM watchlist WHERE code=?", (code,))
            current = rows[0]["notes"] if rows else ""
        except Exception:
            current = ""
        notes = simpledialog.askstring(
            "修改备注", f"{code} 的备注：", initialvalue=current, parent=self
        )
        if notes is None:
            return
        try:
            self.store.update_watch(code, notes, active=True)
        except Exception as exc:
            messagebox.showerror("修改备注失败", str(exc), parent=self)
            return
        self.reload()

    # ---- 写：切换计划状态（save_plan，含内置风控校验）----
    def _change_state(self, code, obs_id, state):
        if self.store is None or not code:
            return
        if not obs_id:
            messagebox.showinfo(
                "无关联观察", f"{code} 的关注记录没有关联的观察信号，无法建立计划。", parent=self
            )
            return
        # 预填：现有计划优先，其次观察 payload（entry/stop/target）
        entry = stop = target = 0.0
        quantity = 0
        notes = ""
        plans = self.store.rows(
            "SELECT entry, stop, target, quantity, notes FROM plans "
            "WHERE observation_id=? ORDER BY updated DESC LIMIT 1",
            (obs_id,),
        )
        if plans:
            p = plans[0]
            entry = p["entry"] or 0.0
            stop = p["stop"] or 0.0
            target = p["target"] or 0.0
            quantity = p["quantity"] or 0
            notes = p["notes"] or ""
        else:
            obs = self.store.rows("SELECT payload FROM observations WHERE id=?", (obs_id,))
            if obs and obs[0]["payload"]:
                try:
                    pl = json.loads(obs[0]["payload"])
                    entry = float(pl.get("entry") or 0)
                    stop = float(pl.get("stop") or 0)
                    target = float(pl.get("target") or 0)
                except Exception:
                    pass
        dialog = _PlanDialog(self, code, state, entry, stop, target, quantity, notes)
        self.wait_window(dialog.top)
        if not dialog.confirmed:
            return
        try:
            self.store.save_plan(
                obs_id, state, dialog.entry, dialog.stop,
                dialog.target, dialog.quantity, dialog.notes,
            )
        except Exception as exc:
            # store 内置风控校验（0<止损<入场<目标 等）不通过会在这里抛出
            messagebox.showerror("保存计划失败", str(exc), parent=self)
            return
        self.reload()

    # ---- 写：结束关注（update_watch active=False，行从池中消失）----
    def _end_watch(self, code):
        if self.store is None or not code:
            return
        if not messagebox.askyesno("结束关注", f"结束关注 {code}？", parent=self):
            return
        try:
            rows = self.store.rows("SELECT notes FROM watchlist WHERE code=?", (code,))
            notes = rows[0]["notes"] if rows else ""
            self.store.update_watch(code, notes, active=False)
        except Exception as exc:
            messagebox.showerror("结束关注失败", str(exc), parent=self)
            return
        self.reload()


class _PlanDialog:
    """轻量计划录入框：一个 Toplevel，确定后回读字段（不引第三方依赖）。"""

    def __init__(self, parent, code, state, entry, stop, target, quantity, notes):
        self.confirmed = False
        self.entry = self.stop = self.target = 0.0
        self.quantity = 0
        self.notes = ""
        self.top = tk.Toplevel(parent)
        self.top.title(f"计划状态：{state} · {code}")
        self.top.configure(bg=APP_BG)
        self.top.transient(parent.winfo_toplevel())
        self.top.grab_set()
        self.top.resizable(False, False)

        ttk.Label(self.top, text=f"{code} → {state}", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, padx=8, pady=(8, 2)
        )
        self._vars = {}
        fields = (
            ("入场价", entry), ("止损价", stop), ("目标价", target),
            ("股数", quantity), ("备注", notes),
        )
        for i, (label, value) in enumerate(fields, start=1):
            ttk.Label(self.top, text=label).grid(row=i, column=0, sticky=tk.W, padx=8, pady=3)
            var = tk.StringVar(value=str(value))
            entry_widget = ttk.Entry(self.top, textvariable=var, width=14)
            entry_widget.grid(row=i, column=1, sticky=tk.EW, padx=8, pady=3)
            self._vars[label] = var
        ttk.Button(self.top, text="确定", command=self._ok).grid(
            row=len(fields) + 1, column=0, pady=8, padx=8, sticky=tk.E
        )
        ttk.Button(self.top, text="取消", command=self.top.destroy).grid(
            row=len(fields) + 1, column=1, pady=8, padx=8, sticky=tk.W
        )

    def _ok(self):
        try:
            self.entry = float(self._vars["入场价"].get())
            self.stop = float(self._vars["止损价"].get())
            self.target = float(self._vars["目标价"].get())
            self.quantity = int(float(self._vars["股数"].get()))
            self.notes = self._vars["备注"].get()
        except ValueError:
            messagebox.showerror("输入无效", "价格/股数必须是数字", parent=self.top)
            return
        self.confirmed = True
        self.top.destroy()
