"""PR30 compact layout and PR31 actions coexist in an isolated native window."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows native integrated desktop')
@pytest.mark.parametrize('constrained', [False, True])
def test_merged_toolbar_keeps_layout_date_review_and_version_hooks(tmp_path, constrained):
    script = r'''
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from workbench.store import Store
from workbench.service import Service
from gui.main_window import AplusMainWindow
s=Store(Path(sys.argv[1])/'isolated'); service=Service(s)
w=AplusMainWindow(service=service,store=s)
try:
    if sys.argv[2]=='True': w.maxsize(1280,900)
    w.geometry('3180x900+0+0')
    for _ in range(3): w.update()
    bar=w.toolbar; controller=w.version_controller
    assert controller.button.master is w.section_nav
    assert bar._on_scan_started==controller.scan_hint
    for var,value in zip((bar.year_var,bar.month_var,bar.day_var),('2026','10','09')):
        var.set(value)
    assert bar._integrity_asof()=='2026-10-09'
    with patch('workbench.readiness.expected_day',return_value='2026-10-09'), \
         patch('workbench.gap_review.review_receipt',return_value={'asof':'2026-10-09'}) as review, \
         patch('gui.tickflow_integrity.show_integrity') as show:
        bar.btn_integrity.invoke()
        assert review.call_args.args[3]=='2026-10-09'
        assert show.call_args.args[1]['asof']=='2026-10-09'
    controller.start_check=Mock()
    controller.button.invoke()
    controller.start_check.assert_called_once_with(manual=True)
    order=[]
    with patch.object(service,'submit',side_effect=lambda *a:order.append('submit') or 'synthetic'), \
         patch.object(bar,'after'), \
         patch.object(controller,'start_check',side_effect=lambda **k:order.append(k)):
        bar._submit_job('scan','扫描策略',{'source':'baostock'})
    assert order==['submit',{'manual':False}]
    assert not s.rows('SELECT id FROM jobs')  # No real scan or network task.
    for _ in range(3): w.update()
    sizes=[]
    for source in ('BaoStock','TickFlow','腾讯'):
        bar._header_data_var.set(source+' · 行情 2026-10-09 ✓ · 信号 2026-10-09 ✓ · 主板 3185/3197')
        for _ in range(3): w.update()
        sizes.append((bar.winfo_height(),w.chart.winfo_height()))
    assert len(set(sizes))==1,sizes
    needed=sum(x.winfo_reqwidth() for x in (bar.header,bar.actions,bar.filters))+40
    enough=bar.winfo_width()>=needed+bar._status_required_width()
    assert (int(bar.status_panel.grid_info()['row'])==0)==enough
    if enough:
        assert bar.filters.winfo_rootx()+bar.filters.winfo_width()<=bar.status_panel.winfo_rootx()
    # PR30's compact budget survived; the older full-receipt budget did not.
    expected=int(bar.tk.call('font','measure',bar.status_label.cget('font'),
                 '-displayof',bar._w,'行情 2099-12-31 · 信号 2099-12-31'))+8
    assert bar._status_required_width()==expected
finally:
    w.destroy(); service.pool.shutdown(wait=True)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(constrained)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
