"""Real Windows Tk stays responsive while an isolated SQLite reader waits."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows native Tk responsiveness')
def test_locked_progress_reader_does_not_freeze_tk_and_recovers(tmp_path):
    result = subprocess.run([sys.executable,'-c',
        'from tests.test_job_progress_native import check_progress; check_progress()',str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=35)
    assert result.returncode == 0, result.stdout+result.stderr


def check_progress():
    import sqlite3
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock
    import ttkbootstrap as ttk
    from gui.toolbar import ToolBar
    from tests.native_ui import create_window
    from workbench.store import Store, dumps

    store = Store(Path(sys.argv[1])/'isolated')
    root = create_window(lambda:ttk.Window(themename='darkly'))
    root.withdraw()
    service = SimpleNamespace(cancel=Mock())
    bar = ToolBar(root,store=store,service=service)
    bar.pack(fill='x')
    root.update()
    store.execute('INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)',
                  ('parent','premarket','running','now','{}',dumps({'sources':{}})))
    bar._job_id,bar._job_kind = 'parent','一键盘前'
    bar._job_started_at = time.monotonic()
    bar.btn_stop.configure(state='normal')
    ticks = []
    def pump(condition,timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            root.update(); time.sleep(.005)
            if condition(): return
        raise AssertionError('进度未恢复')
    def tick():
        ticks.append(time.monotonic())
        root.after(20,tick)
    root.after(20,tick)
    bar._poll_job()
    writer = None
    try:
        pump(lambda:bar._job_snapshot is not None)
        writer = sqlite3.connect(store.path)
        writer.execute('BEGIN EXCLUSIVE')
        before = len(ticks)
        pump(lambda:bar._job_read_error)
        assert len(ticks)-before >= 25  # Tk continues painting during DB wait.
        assert bar._job_id == 'parent' and '正在重试' in bar._status.get()
        bar.btn_stop.invoke()
        service.cancel.assert_called_once_with('parent')
        writer.rollback(); writer.close(); writer = None
        store.execute("UPDATE jobs SET progress=2,total=3,message='recovered' WHERE id='parent'")
        pump(lambda:not bar._job_read_error and bar._job_snapshot[1]['progress']==2)
        assert 'recovered' in bar._status.get()
        def finish(*_args):
            bar._job_id = None
        finished = Mock(side_effect=finish)
        bar._finish_job = finished
        store.execute("UPDATE jobs SET status='completed',message='done' WHERE id='parent'")
        pump(lambda:finished.called)
        assert finished.call_args[0][:2] == ('completed','done')
        assert bar._job_poll is None
        # Actual row tags/text, not just the counting helper: two revisions on
        # one day stay neutral; a second real trading day remains yellow x2.
        from gui.candidate_tabs import CandidateTabs
        tabs = CandidateTabs(root,Mock())
        rows = [dict(code='sz.002428',name='fixture',date='2026-10-14',
                     observation_id=str(i)) for i in range(2)]
        tabs._rows = {'MTR_MASTER':rows}
        tabs._choose('MTR_MASTER')
        iid = tabs.tree.get_children()[0]
        assert 'repeat' not in tabs.tree.item(iid,'tags')
        assert '×' not in tabs.tree.item(iid,'values')[2]
        rows.append(dict(rows[0],date='2026-10-13',observation_id='earlier'))
        tabs._choose('MTR_MASTER')
        iid = tabs.tree.get_children()[0]
        assert 'repeat' in tabs.tree.item(iid,'tags')
        assert '×2' in tabs.tree.item(iid,'values')[2]
    finally:
        if writer: writer.rollback(); writer.close()
        root.destroy()
