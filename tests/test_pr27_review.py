"""Regression checks for PR27 source-switch controls and shared Web sources."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_workbench import frame
from workbench.market import save_dataset
from workbench.sources import SOURCES
from workbench.store import ROOT, Store


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Tk controls')
def test_native_source_switch_rebuilds_date_choices():
    result = subprocess.run(
        [sys.executable, '-c', 'from tests.test_pr27_review import check_source_switch; check_source_switch()'],
        cwd=ROOT, capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def check_source_switch():
    import tempfile
    import ttkbootstrap as ttk
    from unittest.mock import patch
    from gui.toolbar import ToolBar
    from workbench.sources import market_source
    from workbench.store import dumps, now

    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory)/'isolated')
        dates = {'baostock': ['2026-09-29', '2026-09-30'],
                 'tencent': ['2026-10-08'], 'tickflow': ['2026-08-31', '2026-10-09']}
        for source, days in dates.items():
            for day in days:
                did = source + day
                store.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (did, 'sh.600000', 'daily', source, '不复权' if source == 'tencent' else '前复权',
                     day, day, 1, 'unused.csv', 'unused', now()))
                store.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (did, 'test', 'sh.600000', 'MTR_MASTER', 'test', 'daily', day, day, did, dumps({}), now()))
        before = store.rows('SELECT * FROM observations')
        callbacks = []
        root = ttk.Window(themename='darkly'); root.withdraw()
        try:
            # Status readiness is unrelated; date queries, widgets and source
            # writes below are the real production paths, not mocked helpers.
            with patch('workbench.readiness.audit_scope', return_value={}):
                toolbar = ToolBar(root, store=store, on_date_change=lambda *values: callbacks.append(
                    (market_source(store), values, tuple(toolbar.day_combo.cget('values')))))
                root.update()
                for source, month, day, options in (
                    ('baostock', '09', '30', ('全部', '29', '30')),
                    ('tencent', '10', '08', ('全部', '08')),
                    ('tickflow', '10', '09', ('全部', '09')),
                    ('baostock', '09', '30', ('全部', '29', '30')),
                ):
                    toolbar.source_combo.set(SOURCES[source])
                    toolbar.source_combo.event_generate('<<ComboboxSelected>>', when='tail')
                    root.update()
                    assert market_source(store) == source
                    assert tuple(toolbar.day_combo.cget('values')) == options
                    assert toolbar.selected_date() == ('2026', month, day)
                    assert callbacks[-1] == (source, ('2026', month, day), options)
                    assert set(toolbar._days_by_ym) == {('2026', value[5:7]) for value in dates[source]}
                # A source with market data but no saved signal resets selection,
                # while retaining only its own market dates in the dropdown.
                store.execute("DELETE FROM observations WHERE dataset_id LIKE 'tencent%'")
                toolbar.source_combo.set(SOURCES['tencent'])
                toolbar.source_combo.event_generate('<<ComboboxSelected>>', when='tail')
                root.update()
                assert toolbar.selected_date() == (None, None, None)
                assert tuple(toolbar.day_combo.cget('values')) == ('全部', '08')
                assert callbacks[-1][0:2] == ('tencent', (None, None, None))
                assert store.rows("SELECT * FROM observations WHERE dataset_id NOT LIKE 'tencent%'") == [
                    row for row in before if not row['dataset_id'].startswith('tencent')]
                assert not store.rows('SELECT * FROM jobs')
        finally:
            root.destroy()


def test_web_research_offers_all_shared_sources_and_keeps_csv_demo(tmp_path, monkeypatch, frame):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('A_WORKBENCH_HOME', str(tmp_path/'web'))
    store = Store(tmp_path/'web')
    save_dataset(store, 'sh.600000', frame, 'tencent', '不复权')
    app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
    app.sidebar.radio[0].set_value('回测研究').run()
    assert not app.exception
    selector = next(item for item in app.selectbox if item.label == '行情来源')
    assert selector.options == ['真实行情 · ' + label for label in SOURCES.values()] + [
        '自行导入 · CSV', '演示行情 · 非真实市场']
    selector.set_value('tencent').run()
    assert not app.exception and not app.error
    assert next(item for item in app.selectbox if item.label == '行情来源').value == 'tencent'
    assert any('当前选择 1 只股票' in item.value for item in app.caption)
