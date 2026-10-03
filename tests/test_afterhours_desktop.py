"""Real Tk layout and interaction on Windows; Linux CI runs pure engine tests."""
import os
import tkinter as tk
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from gui.main_window import AplusMainWindow
from workbench.h2_replay import run_replay, summarize_replay
from workbench.backtest import Assumptions
from workbench.store import Store
from workbench.market import BOARDS, save_dataset
from tests.test_h2_plan import h2_bars, h2_spec, append_bar


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows desktop layout')
def test_real_desktop_layout_filter_replay_navigation_and_return(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path / 'gui')
    window = AplusMainWindow(store=store)
    try:
        window._switch_section('afterhours')
        page = window.afterhours
        assert page._selected_boards() == list(BOARDS)
        assert page.scope_button.cget('text') == '范围：全市场'
        assert [page.scope_menu.entrycget(i, 'label') for i in range(4)] == list(BOARDS)
        for size in ('1600x1000', '1280x760'):
            window.geometry(size)
            window.update()
            assert page.run_button.winfo_rootx() + page.run_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert page.tree.winfo_width() > 600
            assert page.boundary_trees['profit'].winfo_width() > 200
            assert page.tree.winfo_width() / (page.tree.winfo_width() + page.boundary_trees['profit'].winfo_width()) == pytest.approx(.76, abs=.04)
        assert page._compact_side
        page._select_edge_tab('loss')
        assert page._edge_tab == 'loss'
        page._select_edge_tab('profit')
        bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 14.1, 11)
        records = run_replay(h2_spec, bars, bars.date.iloc[124], bars.date.iloc[-1], costs=Assumptions())
        report = summarize_replay(records)
        report.update(start=bars.date.iloc[124], end=bars.date.iloc[-1], processed_datasets=1,
                      requested_datasets=1, coverage_warnings=[], errors=[], real_data=False)
        page._show_report({'status': 'completed'}, report, records)
        assert page.start.get() == report['start'] and page.end.get() == report['end']
        identity = records[0]['id']
        assert len(page.tree.get_children()) == 1
        with patch('gui.afterhours.render_replay', return_value=Image.new('RGB', (1000, 500))) as render:
            page.tree.selection_set(identity)
            page._open_selected()
            window.update()
            assert page._detail_visible and page._event_index == 0
            page._step_event(1)
            window.update()
            assert page._event_index == 1
            assert page._photo is not None
            assert page._photo.width() == page.chart.winfo_width()
            assert page._photo.height() == page.chart.winfo_height()
            assert not page.conditions.winfo_ismapped() and not page.research_status.winfo_ismapped()
            window.geometry('1600x1000')
            window.update()
            page._fit()
            assert render.call_args.kwargs['pixel_size'] == (page.chart.winfo_width(), page.chart.winfo_height())
            assert page._photo.width() == page.chart.winfo_width()
            assert page._photo.height() == page.chart.winfo_height()
            page._back()
            window.update()
            assert page.conditions.winfo_ismapped()
            assert not page._detail_visible and page.tree.selection() == (identity,)
            page.boundary_trees['profit'].selection_set(identity)
            page._open_boundary('profit')
            window.update()
            assert page._group == 'profit'
            page._step_trade(1)
            assert page._trade['id'] == identity
            page._back()
            assert page.boundary_trees['profit'].selection() == (identity,)
            assert render.call_count >= 2
        page.filter.set('仍待挂')
        page._fill_rows()
        assert not page.tree.get_children()
        page.filter.set('全部')
        page._fill_rows()
        assert page.tree.exists(identity)
        assert len(store.rows('SELECT * FROM observations')) == 0
        save_dataset(store, 'sh.600000', h2_bars, 'csv', '合成工程测试')
        page.service = Mock()
        page.service.submit.return_value = 'new-job'
        for board, variable in page.board_vars.items():
            variable.set(board == '沪深主板')
        page._board_changed()
        page.start.set(h2_bars.date.iloc[124])
        page.end.set(h2_bars.date.iloc[-1])
        page._run()
        assert page.service.submit.call_args.args[1]['boards'] == ['沪深主板']
        assert page.job == 'new-job' and not page.records and not page.report
        assert page.stats[0].get() == '—' and not page.tree.get_children()
    finally:
        window.destroy()
