"""Web image rendering must work without a desktop Tk installation."""
import subprocess
import sys
from unittest.mock import Mock

import pytest

from gui.chart_renderer import render_chart
from core.strategy_registry import StrategyRegistry
from tests.test_chart_annotations import _bars


def test_shared_renderer_works_with_tk_imports_blocked(tmp_path):
    script = r'''
import importlib.abc
import sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'tkinter', 'ttkbootstrap'} or fullname == 'PIL.ImageTk':
            raise ImportError('Desktop imports are blocked: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
from gui.chart_renderer import render_chart
import pandas as pd
frame = pd.DataFrame({'date': pd.bdate_range('2026-01-05', periods=30).strftime('%Y-%m-%d'),
                      'open': 10., 'high': 11., 'low': 9., 'close': 10.5, 'volume': 1000.})
render_chart(frame, {}, '', {}).save(sys.argv[1])
assert 'gui.main_window' not in sys.modules
assert 'gui.chart_panel' not in sys.modules
assert 'tkinter' not in sys.modules
'''
    output = tmp_path / 'web-chart.png'
    result = subprocess.run([sys.executable, '-c', script, str(output)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert output.stat().st_size > 1000


@pytest.mark.parametrize('key', ['MTR_MASTER', 'STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP',
                                  'STRATEGY_GAP_PINBAR', 'STRATEGY_AWIL'])
def test_other_strategy_annotation_routes_survive_h2_integration(key):
    strategy = StrategyRegistry.get_strategy(key)
    strategy.annotate_chart = Mock(return_value=0)
    strategy.get_signal_info = Mock(return_value={})
    frame = _bars()
    payload = {'asof': frame.date.iloc[-1], 'setup_date': frame.date.iloc[-1],
               'entry': 10., 'stop': 9., 'target': 12.}
    # 缺口路由需要已核验结构；缺少结构时不允许绘图器补造价格。
    prefix = {'STRATEGY_STRUCTURAL_GAP': 'struct_gap',
              'STRATEGY_GAP_PINBAR': 'gap_pinbar'}.get(key)
    if prefix:
        frame.loc[frame.index[-1], prefix+'_floor_exact'] = 9.
        frame.loc[frame.index[-1], prefix+'_top_exact'] = 9.5
        frame.loc[frame.index[-1], prefix+'_prior_low'] = 6.5 if prefix == 'struct_gap' else 6.
    render_chart(frame, payload, '', strategy.get_metadata(), strategy=strategy, strategy_type=key)
    strategy.annotate_chart.assert_called_once()
    args, kwargs = strategy.annotate_chart.call_args
    assert args[2] == key
    assert kwargs['anchor_signal_date'] == payload['setup_date']
    assert kwargs['sl_price'] == 9. and kwargs['tp1'] == 12.
