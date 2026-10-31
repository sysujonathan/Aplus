"""Native comparison actions keep the historical case above its market chart."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui.growth_space import GrowthHost, open_chart_cases
from gui.chart_embed import atomic_json


def test_compare_and_return_have_distinct_window_actions():
    from gui import growth_space
    events = []
    owner = Mock()
    owner.winfo_screenwidth.return_value = 1920
    owner._switch_section.side_effect = lambda key: events.append(('section', key))
    owner.lift.side_effect = lambda: events.append('chart-front')
    owner.focus_force.side_effect = lambda: events.append('chart-focus')
    window = Mock()
    window.lift.side_effect = lambda: events.append('case-front')
    window.focus_force.side_effect = lambda: events.append('case-focus')
    with patch.object(growth_space.tk, 'Toplevel', return_value=window), \
         patch.object(growth_space.tk, 'Frame'), \
         patch.object(growth_space, 'GrowthHost') as factory:
        open_chart_cases(owner, object(), SimpleNamespace(code='TEST.ETHUSD', name='Test', timeframe='daily'))
    callbacks = factory.call_args.kwargs
    callbacks['on_compare']('owned-case')
    assert events == [('section', 'premarket'), 'chart-front', 'case-front', 'case-focus']
    events.clear()
    callbacks['on_return']()
    assert events == [('section', 'premarket'), 'chart-front', 'chart-focus']


def test_compare_ipc_is_deduplicated_and_never_calls_return(tmp_path):
    parent, process = Mock(), Mock()
    parent.winfo_exists.return_value = True
    parent.winfo_id.return_value = 123
    process.poll.return_value = None
    compare, return_chart = Mock(), Mock()
    with patch('gui.growth_space.subprocess.Popen', return_value=process):
        host = GrowthHost(parent, SimpleNamespace(root=tmp_path), on_compare=compare, on_return=return_chart)
    atomic_json(host.actions, {'id': 'comparison-1', 'compare_case': 'owned-case'})
    host._poll()
    host._poll()
    compare.assert_called_once_with('owned-case')
    return_chart.assert_not_called()
