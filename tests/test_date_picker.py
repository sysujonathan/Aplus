"""Calendar picker must never hand back a day the verified calendar lacks."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gui.date_picker import available_days, month_index

DATES = ['2026-10-27', '2026-10-26', '2026-10-23',
         '2025-09-30', '2025-09-29', '2016-10-10']


def test_month_index_keeps_only_well_formed_sessions():
    index = month_index(DATES + ['2026-13-01', '2025-02-30', 'bad', '', None, '2026-10'])
    assert sorted(index) == [2016, 2025, 2026]
    assert index[2025] == {9: {29, 30}}
    assert index[2026] == {10: {23, 26, 27}}
    assert available_days(index, 2025, 9) == {29, 30}
    assert available_days(index, 2025, 3) == set()
    assert available_days(index, 1999, 9) == set()


def test_month_index_of_an_empty_selector_is_empty():
    assert month_index([]) == {}
    assert available_days({}, 2026, 10) == set()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Tk controls')
def test_native_picker_only_offers_verified_days():
    result = subprocess.run([sys.executable, '-c',
        'from tests.test_date_picker import check_native; check_native()'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def check_native():
    import ttkbootstrap as ttk
    from gui.date_picker import DatePicker

    root = ttk.Window(themename='darkly')
    root.withdraw()
    chosen = []
    try:
        picker = DatePicker(root, DATES, '2025-09-29', chosen.append)
        assert picker.year_var.get() == '2025' and picker.month_var.get() == '9'
        assert sorted(picker.day_buttons) == [29, 30]
        grid = picker.grid_holder.winfo_children()
        blocked = [w for w in grid if 'disabled' in w.state()]
        assert blocked, '休市或未验证的日子必须显示为不可选'
        assert all(w.cget('text') not in ('29', '30') for w in blocked)
        # 打开时的当前值有独立样式，便于一眼确认
        assert 'info' in str(picker.day_buttons[29].cget('style')).lower()
        assert 'info' not in str(picker.day_buttons[30].cget('style')).lower()
        picker.day_buttons[30].invoke()
        assert chosen == ['2025-09-30']
        assert not picker.winfo_exists()

        # 年份切换后，没有交易日的月份不留在下拉里
        other = DatePicker(root, DATES, '2016-10-10', chosen.append)
        assert other.year_var.get() == '2016'
        other.year_var.set('2026')
        other._on_period_change()
        assert list(other.month_box.cget('values')) == ['10']
        assert other.month_var.get() == '10'
        assert sorted(other.day_buttons) == [23, 26, 27]
        other.destroy()

        # 没有可用交易日时说明原因，不摆一个空日历
        empty = DatePicker(root, [], '', chosen.append)
        assert empty.day_buttons == {}
        assert '没有可用的交易日' in ' '.join(
            w.cget('text') for w in empty.body.winfo_children() if w.winfo_class() == 'TLabel')
        empty.destroy()
    finally:
        root.destroy()
