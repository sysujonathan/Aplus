"""Actual desktop checks for the small, read-only statistics addition."""
from copy import deepcopy
import json
import os
from unittest.mock import Mock

import pytest
import ttkbootstrap as ttk

from gui.afterhours import AfterhoursPage
from gui.research_views import holding_distribution
from gui.theme import CONTROL_BG, MUTED
from tests.native_ui import create_window
from tests.test_h2_plan import h2_bars, h2_spec, append_bar
from workbench.h2_replay import LEGACY_MODEL, MODEL, run_replay, summarize_replay
from workbench.market import save_dataset
from workbench.store import Store


def set_test_geometry(window, size):
    """Exercise the requested client size even on a smaller CI desktop."""
    width, height = map(int, size.split('x'))
    max_width, max_height = window.maxsize()
    window.maxsize(max(width, max_width), max(height, max_height))
    window.geometry(size)
    window.update()
    assert (window.winfo_width(), window.winfo_height()) == (width, height)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows desktop layout')
@pytest.mark.native_process
def test_statistics_filters_legacy_receipts_and_layout_are_read_only(tmp_path, h2_bars, h2_spec):
    store = Store(tmp_path/'isolated')
    window = create_window(lambda: ttk.Window(themename='darkly'))
    try:
        # Reproduce the CI work-area ceiling locally, rather than assuming a
        # geometry request guarantees a 1600x1000 window on every desktop.
        window.maxsize(1008, 749)
        window.geometry('1600x1000')
        window.update()
        assert window.winfo_width() <= 1008 and window.winfo_height() <= 749
        page = AfterhoursPage(window, service=Mock(), store=store)
        page.pack(fill='both', expand=True)
        bars = append_bar(append_bar(h2_bars, 11.8, 10.8), 15, 11)
        records = run_replay(h2_spec, bars, bars.date.iloc[124], bars.date.iloc[-1])
        dataset = save_dataset(store, 'sh.600000', bars, 'csv', '合成工程测试，非真实数据验收')
        for record in records:
            record['dataset_id'] = dataset
        report = summarize_replay(records)
        report.update(start=bars.date.iloc[124], end=bars.date.iloc[-1], processed_datasets=1,
                      requested_datasets=2, coverage_warnings=[{'reason': '工程覆盖不足'}],
                      errors=[], real_data=False, execution_model=MODEL)
        before = deepcopy((report, records))
        raw = json.dumps([report, records], ensure_ascii=False).encode('utf-8')
        saved = store.root/'research/original.json'
        saved.parent.mkdir(parents=True)
        saved.write_bytes(raw)
        page._show_report({'status': 'partial'}, report, records)
        for size in ('1600x1000', '1280x760'):
            set_test_geometry(window, size)
            assert len(page.stats) == 6 and page.stats[4].get().endswith('根')
            assert '部分完成' in page.status.get()
            assert page.preview_chart.winfo_height() >= (100 if size == '1280x760' else 300), (
                size, window.winfo_height(), page.analysis.winfo_height(), page.review.winfo_height(),
                [(key, w.winfo_width(), w.winfo_height(), w.winfo_reqheight()) for key, w in page.aux_labels.items()])
            assert page.preview_chart.winfo_width() >= 350
            assert page.holding_histogram.winfo_width() >= 200
            for label in page.aux_labels.values():
                assert label.winfo_height() >= label.winfo_reqheight()
            # Seven rows, including a tail beyond 30, must also fit without
            # borrowing the red/green meaning of profit and loss.
            page._holding_data = holding_distribution([
                dict(status='closed', holding_bars=n) for n in (1, 3, 5, 10, 20, 30, 31)])
            page._holding_histogram()
            window.update()
            assert len(page._holding_data['buckets']) == 7
            for item in page.holding_histogram.find_all():
                if page.holding_histogram.type(item) == 'rectangle':
                    assert page.holding_histogram.itemcget(item, 'fill') in (CONTROL_BG, MUTED)
            # The three holding buttons stay alongside their full title.
            buttons = page.holding_buttons
            heading = buttons['全部'].master
            title = heading.winfo_children()[0]
            assert title.winfo_x()+title.winfo_width() <= buttons['全部'].winfo_x()
            for canvas in (page.histogram, page.holding_histogram):
                for item in canvas.find_all():
                    box = canvas.bbox(item)
                    assert box[0] >= -3 and box[2] <= canvas.winfo_width()+3
                    assert box[1] >= -3 and box[3] <= canvas.winfo_height()+3
            text_items = [i for i in page.holding_histogram.find_all()
                          if page.holding_histogram.type(i) == 'text']
            for offset in range(0, len(text_items), 3):
                count, percentage = text_items[offset+1:offset+3]
                assert page.holding_histogram.bbox(count)[2] < page.holding_histogram.bbox(percentage)[0]
        selection, stats = page.tree.selection(), [v.get() for v in page.stats]
        conditions = (page.start.get(), page.end.get(), deepcopy(page.settings))
        for category in ('盈利', '亏损', '全部'):
            page.holding_buttons[category].invoke()
            window.update()
            assert page.tree.selection() == selection
            assert [v.get() for v in page.stats] == stats
            assert (page.start.get(), page.end.get(), page.settings) == conditions
        page.service.submit.assert_not_called()
        assert (report, records) == before and saved.read_bytes() == raw
        old = deepcopy(records)
        for record in old:
            record.pop('holding_bars', None)
            record.pop('r_multiple', None)
            record.pop('reason', None)
        legacy_report = dict(report, execution_model=LEGACY_MODEL, boundaries={'profit': [], 'loss': []})
        page._show_report({'status': 'partial'}, legacy_report, old)
        assert page.stats[4].get() == '—'
        assert page.aux_stats['win'].get() == page.aux_stats['loss'].get() == '—'
        assert page.aux_stats['exits'].get() == '—'
        assert '—' in page.aux_stats['excess'].get()
        assert page.tree.heading('stop', 'text') == 'SL1'
        page._clear_history_result()
        assert all(v.get() == '—' for v in page.stats)
        assert page._holding_data['total'] == 0
    finally:
        window.destroy()
