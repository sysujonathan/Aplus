"""Status counts remain readable after source/text changes without resizing."""
import os
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import ttkbootstrap as ttk

from gui.toolbar import ToolBar


@pytest.mark.parametrize("width,row", [(2200, 0), (1280, 1), (1150, 2), (860, 3)])
def test_long_status_has_its_own_row_when_controls_do_not_fit(width, row):
    toolbar = object.__new__(ToolBar)
    for name, size in (("header", 100), ("actions", 700), ("filters", 400)):
        frame = Mock()
        frame.winfo_reqwidth.return_value = size
        setattr(toolbar, name, frame)
    toolbar.status_panel = Mock()
    toolbar.status_label = Mock()
    toolbar.timing_label = Mock()
    toolbar._status_required_width = Mock(return_value=900)
    toolbar._fit_status = Mock()
    ToolBar._responsive(toolbar, SimpleNamespace(widget=toolbar, width=width))
    assert toolbar.status_panel.grid.call_args.kwargs["row"] == row
    if row:
        assert toolbar.status_panel.grid.call_args.kwargs["column"] == 0
        assert toolbar.status_panel.grid.call_args.kwargs["columnspan"] == 4
    toolbar._fit_status.assert_called_once()


@pytest.mark.skipif(os.name != "nt", reason="Windows native Tk layout")
def test_native_status_counts_fit_after_text_and_source_button_grow():
    # Isolate Tcl lifetime from other tests while running every real UI assertion.
    result = subprocess.run(
        [sys.executable, "-c", "from tests.test_toolbar_status_layout import check_native_status_window; check_native_status_window()"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def check_native_status_window():
    # Production has one Tk interpreter. Resize that same window for the sweep,
    # rather than repeatedly initializing/destroying Windows Tcl interpreters.
    root = ttk.Window(themename="darkly")
    root.withdraw()
    try:
        for width, scaling in ((1280, 1.33), (1600, 1.33), (2560, 1.33),
                               (1280, 2.0), (1600, 2.0), (2560, 2.0)):
            check_native_status(root, width, scaling)
    finally:
        root.destroy()


def check_native_status(root, width, scaling):
    root.tk.call("tk", "scaling", scaling)
    root.geometry(f"{width}x760+0+0")
    toolbar = None
    try:
        toolbar = ToolBar(root)
        # New named fonts apply each requested scaling even in a persistent Tk.
        status_font = tkfont.Font(root=root, family="Microsoft YaHei UI", size=9)
        timing_font = tkfont.Font(root=root, family="Consolas", size=9)
        toolbar.status_label.configure(font=status_font)
        toolbar.timing_label.configure(font=timing_font)
        toolbar.pack(fill=tk.X)
        root.deiconify()
        for _ in range(3):
            root.update()
        toolbar.btn_integrity.pack(side=tk.LEFT, padx=3, after=toolbar.btn_sync)
        geometry = []
        chart = ttk.Frame(root)
        chart.pack(fill=tk.BOTH, expand=True)
        for source, count, running in (('腾讯', 0, False), ('TickFlow', 352, False),
                                        ('BaoStock', 9999, True), ('腾讯', 0, False)):
            text = f'{source} · 行情 2026-10-09 ✓ · 信号 2026-09-30 ⚠待扫描 · 主板 {count}/9999'
            toolbar._header_data_var.set(text)
            toolbar._timing_var.set('行情用时 进行中 999分59秒 · 扫描用时 8分56秒' if running else
                                    '行情用时 — · 扫描用时 —')
            toolbar.btn_integrity.configure(text=f'缺口处理 {count}' if count else '缺口分类')
            toolbar.btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)
            for _ in range(3):
                root.update()
            geometry.append((toolbar.winfo_height(), int(toolbar.status_panel.grid_info()['row']), chart.winfo_height()))
            assert toolbar._status_tips[0].text == text
            assert toolbar.status_label.cget('wraplength') == 0
            assert toolbar.timing_label.cget('wraplength') == 0
        assert len(set(geometry)) == 1, (width, scaling, geometry)
        label = toolbar.status_label
        assert label.cget('text') == text or '…' in label.cget('text')
        assert toolbar._header_data_var.get() == text  # Original counts retained.
        for widget in (toolbar.status_panel, label, toolbar.timing_label):
            left = widget.winfo_rootx() - toolbar.winfo_rootx()
            assert left >= 0 and left + widget.winfo_width() <= toolbar.winfo_width()
            assert widget.winfo_reqwidth() <= widget.winfo_width() + 2
        assert label.winfo_reqheight() <= label.winfo_height()
        needed = sum(frame.winfo_reqwidth() for frame in
                     (toolbar.header, toolbar.actions, toolbar.filters)) + 40
        if width < needed + toolbar._status_required_width():
            assert int(toolbar.status_panel.grid_info()["row"]) > 0
        # Neither shorter text nor exceptionally long details alter geometry.
        toolbar._header_data_var.set("主板 3459/3459")
        root.update()
        assert toolbar.winfo_height() == geometry[-1][0]
        toolbar._header_data_var.set('BaoStock · ' + '完整状态说明 ' * 100 + '主板 3459/3459')
        root.update()
        assert toolbar.winfo_height() == geometry[-1][0]
        assert toolbar.status_label.cget('text').endswith('主板 3459/3459')
        chart.destroy()
    finally:
        if toolbar is not None:
            toolbar.destroy()
            assert toolbar._layout_traces == []
            assert toolbar._header_data_var.trace_info() == []
