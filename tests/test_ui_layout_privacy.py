"""Native display contracts using only isolated, synthetic accounts and quotes."""
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import os

import pytest


def capture_evidence(root, name):
    destination = os.environ.get('APLUS_UI_EVIDENCE_DIR')
    if destination:
        from PIL import ImageGrab
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        root.update()
        ImageGrab.grab(window=root.winfo_id()).save(folder / name)


def test_chart_readout_and_axis_layout():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/chart_readout_layout.cjs')],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows WebView2')
def test_actual_webview_readout_and_label_layout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'tests/ui_display_probe.py'), str(tmp_path)],
        cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('desktop_limit', [None, (1024, 768)])
@pytest.mark.native_process
def test_trade_space_and_closed_privacy_preserve_selection_and_ledger(tmp_path, desktop_limit):
    import ttkbootstrap as ttk
    from gui.trade_management import TradeManagementFrame
    from workbench.store import Store

    store = Store(tmp_path)
    aid = store.save_account('合成账户', 100000)
    store.save_closed_trade(aid, 'sh.600000', '合成盈利', '2026-10-27', 8, 1234, 3.21)
    store.save_closed_trade(aid, 'sz.000001', '合成亏损', '2026-10-28', 2, -432, -1.23)
    root = ttk.Window(themename='darkly')
    root.tk.call('tk', 'scaling', 96 / 72)
    if desktop_limit:
        root.maxsize(*desktop_limit)
    root.geometry('1800x1100')
    try:
        page = TradeManagementFrame(root, store)
        page.pack(fill='both', expand=True)
        root.update()
        top, funds, bottom, calendar = page._panels
        assert bottom.winfo_height() > top.winfo_height()
        if not page._compact_panels and page._panel_canvas.winfo_height() >= page._panel_host.winfo_reqheight():
            assert calendar.winfo_rooty() + calendar.winfo_height() <= page.winfo_rooty() + page.winfo_height()
        else:
            page._panel_canvas.yview_moveto(1)
            root.update()
            assert page._panel_canvas.yview()[1] == 1
            assert calendar.winfo_rooty() < page.winfo_rooty() + page.winfo_height()
            page._panel_canvas.yview_moveto(0)
        assert page.position_tree.winfo_height() > 100
        metrics = page.fund_metrics
        assert metrics['available_cash'][1].winfo_rooty() - metrics['market_value'][1].winfo_rooty() < 100
        tree = page.closed_tree
        identities = tree.get_children('')
        tree.selection_set(identities[-1])
        before = store.rows('SELECT * FROM closed_trades ORDER BY id')
        for iid in identities:
            assert tree.set(iid, 'pnl') == tree.set(iid, 'return') == '••••••'
            assert not tree.item(iid, 'tags')
        capture_evidence(root, 'trade-hidden.png')
        page._toggle_fund_values()
        assert {tree.set(iid, 'pnl') for iid in identities} == {'1,234', '-432'}
        assert {tree.set(iid, 'return') for iid in identities} == {'3.21%', '-1.23%'}
        assert all(tree.item(iid, 'tags') for iid in identities)
        capture_evidence(root, 'trade-visible.png')
        page.hide_private_values()
        assert tree.get_children('') == identities and tree.selection() == (identities[-1],)
        assert all(tree.set(iid, 'pnl') == tree.set(iid, 'return') == '••••••' for iid in identities)
        assert all(not tree.item(iid, 'tags') for iid in identities)
        assert store.rows('SELECT * FROM closed_trades ORDER BY id') == before
        with patch('gui.trade_management.messagebox.showinfo') as info:
            page.duration_help_button.invoke()
            assert '0/1' in info.call_args.args[1] and '日历未覆盖' in info.call_args.args[1]
        # The small/high-DPI fallback still lets the user reach the calendar.
        root.geometry('900x700')
        root.update()
        assert page._compact_panels
        page._panel_canvas.yview_moveto(1)
        root.update()
        assert page._panel_canvas.yview()[1] == 1
    finally:
        root.destroy()


@pytest.mark.parametrize('desktop_limit', [None, (1024, 768)])
@pytest.mark.native_process
def test_multichart_equal_columns_and_watch_header_budget(tmp_path, desktop_limit):
    import ttkbootstrap as ttk
    from gui.chart_panel import ChartGrid
    from gui.watch_panel import WatchPanel
    from workbench.store import Store

    store = Store(tmp_path)
    store.add_manual_watch('159518', '合成很长的证券名称用于列宽验收', '合成备注')
    root = ttk.Window(themename='darkly')
    root.tk.call('tk', 'scaling', 96 / 72)
    if desktop_limit:
        root.maxsize(*desktop_limit)
    root.geometry('1800x1000')
    try:
        grid = ChartGrid(root, store=store, interactive=False)
        watch = WatchPanel(root, store)
        watch.pack(side='right', fill='y')
        grid.pack(side='left', fill='both', expand=True)
        # Independent fixed sidebar, as in the real main window.
        watch.configure(width=420)
        watch.pack_propagate(False)
        if os.environ.get('APLUS_UI_EVIDENCE_DIR'):
            import pandas as pd
            from gui.chart_renderer import render_chart
            dates = pd.bdate_range('2026-01-05', periods=200).strftime('%Y-%m-%d')
            closes = [15 + index*.012 + (index % 17)*.015 for index in range(200)]
            frame = pd.DataFrame(dict(date=dates, open=[v-.02 for v in closes],
                close=closes, high=[v+.12 for v in closes], low=[v-.10 for v in closes], volume=1000))
            chart_image = render_chart(frame, {}, '', {})
        for count in (6, 8, 1, 8):
            grid.set_layout(count)
            for index, slot in enumerate(grid._slots):
                slot.header.configure(width=100 + 100 * index)
            root.update()
            widths = [slot.winfo_width() for slot in grid._slots]
            heights = [slot.winfo_height() for slot in grid._slots]
            assert max(widths) - min(widths) <= 1
            assert max(heights) - min(heights) <= 1
            assert watch.winfo_width() == 420
            if os.environ.get('APLUS_UI_EVIDENCE_DIR') and count in (6, 8):
                for index, slot in enumerate(grid._slots):
                    slot._title_var.set(f'合成标的 {index+1} · 多图列宽验收')
                    slot._image = chart_image
                    slot._fit_image()
                capture_evidence(root, f'grid-{count}.png')
        for width in (1800, 1500):
            root.geometry(f'{width}x1000')
            root.update()
            assert max(s.winfo_width() for s in grid._slots) - min(s.winfo_width() for s in grid._slots) <= 1
        tree = watch.tree
        columns = tree['displaycolumns']
        assert sum(tree.column(c, 'width') for c in columns) <= tree.winfo_width()
        assert tree.column('name', 'width') > tree.column('code', 'width') * 1.5
        assert tree.column('notes', 'width') < tree.column('code', 'width')
        watch.views.data['columns'] += ['price', 'percent', 'asof']
        tree.configure(displaycolumns=watch.views.columns())
        watch._fit_columns()
        root.update()
        assert tree.column('name', 'width') >= tree.column('name', 'minwidth')
        assert tree.xview()[1] < 1  # optional columns scroll instead of crushing the name
    finally:
        root.destroy()
