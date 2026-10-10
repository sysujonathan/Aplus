"""Real Tk layout and interaction on Windows; Linux CI runs pure engine tests."""
import os
import gc
import time
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


@pytest.fixture(autouse=True)
def collect_destroyed_desktop_on_main_thread():
    yield
    # Tk variables in destroyed dialog/button cycles must finalize here,
    # after the test's local references are released. Otherwise a later
    # Service worker can trigger GC and call Tcl from that background thread.
    gc.collect()


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
        from gui.chart_renderer import render_chart
        image = render_chart(h2_bars, {}, '', {})
        window.geometry('1600x1000')
        window.update()
        for count in (1,4,6,9):
            window.chart.set_layout(count)
            window.update()
            slot = window.chart._slots[0]
            slot._image = image
            slot._fit_image()
            window.update()
            info = image.info['replay_view']
            i = len(info['candles'])//2
            width, height = slot._photo.width(), slot._photo.height()
            x = (slot.chart_label.winfo_width()-width)/2 + info['candle_x'][i]*width/image.width
            y = (slot.chart_label.winfo_height()-height)/2 + sum(info['price_y'])/2*height/image.height
            chart_height = slot.chart_label.winfo_height()
            slot.chart_label.event_generate('<Motion>',x=round(x),y=round(y))
            window.update()
            assert str(info['candles'][i]['date'])[:10] in slot.readout.get()
            assert slot.chart_label.winfo_height() == chart_height
            slot.show_placeholder()
            assert slot._image is None and '移到 K 线' in slot.readout.get()
        window._switch_section('afterhours')
        page = window.afterhours
        for size in ('2560x1440', '1600x1000', '1280x760'):
            window.geometry(size)
            window.update()
            assert page.run_button.winfo_rootx() + page.run_button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert page.run_button.winfo_width() >= page.run_button.winfo_reqwidth()
            assert page.tree.winfo_width() > 350
            assert page.boundary_trees['profit'].winfo_width() > 200
            assert .25 < page.list_host.winfo_width() / page.review.winfo_width() < .65
            assert page.preview_host.winfo_width() > 350
            assert page.history_canvas.winfo_ismapped()
            for column in page.tree['columns']:
                assert page.tree.heading(column, 'anchor') == page.tree.column(column, 'anchor')
            assert not page.tree.column('entry', 'stretch')
            from tkinter.font import Font
            heading_font = Font(page, font=page.tk.call('ttk::style', 'lookup', 'ResearchList.Treeview.Heading', '-font'))
            for col in page.tree['displaycolumns']:
                assert page.tree.column(col, 'width') >= heading_font.measure(page.tree.heading(col, 'text'))+15
            assert page.compare_button.winfo_rooty() < page.history_canvas.winfo_rooty()
            assert page.chart.bind('<MouseWheel>') and page.preview_chart.bind('<B1-Motion>')
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
        page._names[records[0]['code']] = '测试股票'
        with patch('gui.afterhours.render_replay', return_value=Image.new('RGB', (1000, 500))) as render:
            page.tree.selection_set(identity)
            page._open_selected()
            window.update()
            assert page._detail_visible and page._event_index == 0
            assert '测试股票' in page.detail_title.get()
            for size in ('1280x760', '1600x1000', '2560x1440'):
                window.geometry(size)
                window.update()
                assert page.timeline.winfo_width() >= 260
                assert page.timeline_host.winfo_width() < page.chart.winfo_width()*.5
                assert page.timeline.column('date', 'width') >= 100
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
        # Windows MapNotify can arrive after the first update following a grid
        # rearrangement. Await the real mapping, not an arbitrary long sleep;
        # keep the original mapping/size assertions and a bounded failure.
        deadline = time.monotonic() + 2
        while not page.preview_host.winfo_ismapped() and time.monotonic() < deadline:
            window.after(10)
            window.update()
        assert page.preview_host.winfo_ismapped()
        assert .25 < page.list_host.winfo_width() / page.review.winfo_width() < .65
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
        # Resizing replaces history-card Tk variables. Collect their dead
        # callback cycles on this UI thread before starting a Service worker.
        gc.collect()
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
            assert '2 次' in page.compare_button.cget('text')
            page.compare_button.invoke()
            window.update()
            assert any(child.winfo_class() == 'Toplevel' for child in page.winfo_children())
            for child in page.winfo_children():
                if child.winfo_class() == 'Toplevel':
                    child.destroy()
            page._receipt_dialog()
            page._data_receipt_dialog()
            def texts(parent):
                return [w.get('1.0', 'end') for w in parent.winfo_children() if isinstance(w, tk.Text)] + [
                    t for w in parent.winfo_children() for t in texts(w)]
            dialogs = [w for w in page.winfo_children() if w.winfo_class() == 'Toplevel']
            assert '覆盖提示与失败' not in ''.join(texts(dialogs[0]))
            assert '覆盖提示与失败' in ''.join(texts(dialogs[1]))
            with patch('gui.afterhours.simpledialog.askstring', return_value='主板基准') as prompt:
                page._rename_history()
                assert prompt.call_args.kwargs['initialvalue'] == ''
            assert '主板基准' in page.result_title.get()
            assert page._compare_ids == jobs
            assert page._history_rows[jobs[0]][0]['display']['number'] == 1
            from workbench.stop_research import MODEL as STOP_MODEL
            from workbench.stop_research_service import load_experiment
            gc.collect()
            experiment=service.submit('backtest',dict(execution_model=STOP_MODEL,source_job=jobs[0]))
            assert wait_for(store,experiment)['status']=='completed'
            iteration=window.strategy_iteration
            iteration.service=service
            window._switch_section('strategy')
            window.update()
            iteration._load(experiment)
            window.update()
            assert iteration.rows and iteration.report['source_job']==jobs[0]
            for geometry in ('1800x1100','1280x760'):
                window.geometry(geometry);window.update()
                assert iteration.run.winfo_rootx()+iteration.run.winfo_width()<=window.winfo_rootx()+window.winfo_width()
                assert iteration.chart.winfo_width()>350
                assert iteration.samples.winfo_width()>300
            assert iteration.samples.get_children()
            iteration.group.set('无法对照');iteration._fill_rows();window.update()
            assert iteration.samples.get_children()
            assert load_experiment(store,experiment)[1]['excluded']>0
            with patch('gui.afterhours.messagebox.askyesno', return_value=False):
                page._delete_history()
            assert jobs[0] in page._history_rows
            with patch('gui.afterhours.messagebox.askyesno', return_value=True):
                page._delete_history()
                assert jobs[0] not in page._history_rows and page._shown_job == jobs[1]
                assert page._compare_ids == [jobs[1]]
                page._delete_history()
            assert not page.records and not page.report and not page.tree.get_children()
            assert page.result_title.get() == '暂无回测记录'
            page._restore_history_dialog()
            window.update()
            restore_dialog = [w for w in page.winfo_children() if w.winfo_class() == 'Toplevel'][-1]
            table = next(w for w in restore_dialog.winfo_children() if isinstance(w, __import__('ttkbootstrap').Treeview))
            table.selection_set(jobs[0])
            window.update()
            next(w for w in restore_dialog.winfo_children() if isinstance(w, __import__('ttkbootstrap').Button)).invoke()
            assert page._shown_job == jobs[0] and '主板基准' in page.result_title.get()
            assert page._history_rows[jobs[0]][0]['display']['number'] == 1
        finally:
            service.pool.shutdown()
    finally:
        window.destroy()
