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
from workbench.market import save_dataset
from tests.test_h2_plan import h2_bars, h2_spec, append_bar


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows desktop layout')
def test_real_desktop_layout_filter_replay_navigation_and_return(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path / 'gui')
    window = AplusMainWindow(store=store)
    try:
        window._switch_section('afterhours')
        page = window.afterhours
        for size in ('1600x1000', '1280x760'):
            window.geometry(size)
            window.update()
            assert page.run_button.winfo_rootx() + page.run_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert page.tree.winfo_width() > 600
            assert page.boundary_trees['profit'].winfo_width() > 200
            assert page.tree.winfo_width() / (page.tree.winfo_width() + page.boundary_trees['profit'].winfo_width()) == pytest.approx(.76, abs=.04)
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
        assert page.job == 'new-job' and not page.records and not page.report
        assert page.stats[0].get() == '—' and not page.tree.get_children()
    finally:
        window.destroy()
