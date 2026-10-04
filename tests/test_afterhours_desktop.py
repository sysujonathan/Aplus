"""Real Tk layout and interaction on Windows; Linux CI runs pure engine tests."""
import os
import tkinter as tk
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from gui.main_window import AplusMainWindow
from workbench.h2_replay import MODEL, run_replay, summarize_replay
from workbench.backtest import Assumptions
from workbench.store import Store
from workbench.market import save_dataset
from tests.test_h2_plan import h2_bars, h2_spec, append_bar


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows desktop layout')
def test_real_desktop_layout_filter_replay_navigation_and_return(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path / 'gui')
    window = AplusMainWindow(store=store)
    try:
        trade = window.trade_management
        assert trade.position_tree._measure_font.measure("仓位%") > 0
        assert trade.tk.call(
            "ttk::style", "lookup", "PositionSell.primary.Outline.TButton", "-anchor"
        ) == "center"
        window._switch_section('afterhours')
        page = window.afterhours
        for size in ('2560x1440', '1600x1000', '1280x760'):
            window.geometry(size)
            window.update()
            assert page.run_button.winfo_rootx() + page.run_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert page.run_button.winfo_width() >= page.run_button.winfo_reqwidth()
            assert page.tree.winfo_width() > 350
            assert page.boundary_trees['profit'].winfo_width() > 200
            assert page.list_host.winfo_width() / page.review.winfo_width() == pytest.approx(.50, abs=.04)
            assert page.preview_host.winfo_width() > 350
            assert page.history_canvas.winfo_ismapped()
            for column in page.tree['columns']:
                assert page.tree.heading(column, 'anchor') == page.tree.column(column, 'anchor')
            assert not page.tree.column('entry', 'stretch')
            assert page.tree.column('name', 'stretch')
            assert int(page.status_label.cget('wraplength')) == page.status_label.winfo_width()
            assert int(page.completeness_label.cget('wraplength')) == page.completeness_label.winfo_width()
            assert page.histogram.winfo_height() >= 90
        bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 14.1, 11)
        records = run_replay(h2_spec, bars, bars.date.iloc[124], bars.date.iloc[-1], costs=Assumptions())
        report = summarize_replay(records)
        report.update(start=bars.date.iloc[124], end=bars.date.iloc[-1], processed_datasets=1,
                      requested_datasets=1, coverage_warnings=[], errors=[], real_data=False)
        page._show_report({'status': 'completed'}, report, records)
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
            page._preview_trade = records[0]
            page._preview_index = 1
            page._open_preview()
            window.update()
            assert page._event_index == 1
            page._back()
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
        page.start.set(h2_bars.date.iloc[124])
        page.end.set(h2_bars.date.iloc[-1])
        page._run()
        assert page.job == 'new-job' and page.records and page.report
        assert page.stats[0].get() == '1' and page.tree.exists(identity)
        assert page.service.submit.call_args.args[1]['boards'] == list(page.board_vars)
        page._set_category('未成交')
        assert not page.tree.get_children()
        page._set_category('已结束')
        assert page.tree.exists(identity)
        page._toggle_list()
        window.update()
        assert page.list_host.winfo_width() / page.review.winfo_width() > .95
        page._toggle_list()
        window.update()
        assert page.preview_host.winfo_ismapped()
        assert page.list_host.winfo_width() / page.review.winfo_width() == pytest.approx(.5, abs=.04)
        from copy import deepcopy
        page.records = [dict(deepcopy(records[0]), id=str(i), r_multiple=value) for i, value in enumerate([2, 10, -2, None])]
        page._sort_rows('r')
        assert list(page.tree.get_children()) == ['1', '0', '2', '3']
        page._sort_rows('r')
        assert list(page.tree.get_children()) == ['2', '0', '1', '3']
        # Use the existing Tk interpreter; ttkbootstrap styles contain images
        # belonging to that interpreter and cannot cross destroyed roots.
        from workbench.service import Service
        from tests.test_workbench import wait_for
        from workbench.market import BOARDS
        service = Service(store)
        page.job = None
        page.service = service
        try:
            jobs = []
            for settings in ({}, {'max_risk_pct': 1}):
                job = service.submit('backtest', dict(strategy='STRATEGY_GAP_H2', timeframe='daily',
                    execution_model=MODEL, datasets=[store.rows('SELECT id FROM datasets')[0]['id']],
                    start=h2_bars.date.iloc[124], end=h2_bars.date.iloc[-1],
                    scope='market', boards=[BOARDS[0]], h2_settings=settings))
                assert wait_for(store, job)['status'] == 'completed'
                jobs.append(job)
            page._refresh_history()
            assert set(page.receipts.values()) == set(jobs)
            page._select_history(jobs[0])
            assert page.report['h2_settings']['max_risk_pct'] == 0
            for job in jobs:
                page._compare_vars[job].set(True)
                page._mark_comparison(job)
            assert page._compare_ids == jobs
            page._compare_reports()
            window.update()
            assert any(child.winfo_class() == 'Toplevel' for child in page.winfo_children())
        finally:
            service.pool.shutdown()
    finally:
        window.destroy()
