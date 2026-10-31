"""Real Tk, chart WebView and PA comparison in a disposable synthetic home."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder):
    import pandas as pd
    from tkinter import messagebox

    from gui.chart_bridge import ChartBridge, workspace_boot
    from gui.chart_embed import read_notification
    from gui.chart_items import ChartItem
    from gui.main_window import AplusMainWindow
    from workbench.store import Store

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if folder.name != 'pa-comparison-probe' or (folder / 'runtime' / 'workbench.sqlite3').exists():
        raise ValueError('Comparison acceptance requires a new isolated probe directory')
    store = Store(folder / 'runtime')
    days = pd.bdate_range('2026-01-05', periods=180).strftime('%Y-%m-%d').tolist()
    prices = [20 + n * .025 + math.sin(n / 8) for n in range(180)]
    frame = pd.DataFrame(dict(date=days, open=[v-.1 for v in prices], high=[v+.3 for v in prices],
                              low=[v-.3 for v in prices], close=prices, volume=[1000] * 180))
    item = ChartItem(code='TEST.ETHUSD', name='Synthetic PA comparison · not market data',
                     observation_id=None, source='test-synthetic', mode='watch', timeframe='daily', manual=True)
    context = dict(frame=frame, code=item.code, name=item.name, timeframe='daily', context_kind='watch',
                   record=dict(source=item.source, adjustment='不复权'))
    boot = workspace_boot(store, context)
    state = dict(timeframe='daily', objects=[dict(id='preserved-pa-trend', type='trend',
                 a=dict(index=20, date=days[20], value=20.25),
                 b=dict(index=95, date=days[95], value=23.75), width=3, color='#e5bd79')],
                 view=dict(count=73, offset=19, priceZoom=1.35, priceCenterOffset=.2),
                 indicators=dict(ema_length=17), toolbar={})
    ChartBridge(store, boot).save_state(state, 0)
    baseline = deepcopy(store.chart_document(boot['identity']))
    assert baseline['drawings'][0]['id'] == 'preserved-pa-trend'
    assert baseline['settings']['web_state']['view'] == state['view']
    assert baseline['settings']['web_state']['timeframe'] == 'daily'

    # An unexpected product modal is a failure, never an unattended CI dialog.
    def unexpected_dialog(*args, **kwargs):
        raise AssertionError('Unexpected modal during comparison probe: ' + repr(args))
    messagebox.showwarning = messagebox.showerror = messagebox.showinfo = unexpected_dialog

    window = AplusMainWindow(store=store, enable_tray=False)
    window.title('PA comparison regression · isolated synthetic data')
    # This contract is entirely offline. Version discovery and live prices are
    # unrelated to preserving the already loaded market workspace.
    if window.version_controller.startup_check_id:
        window.after_cancel(window.version_controller.startup_check_id)
        window.version_controller.startup_check_id = None
    slot = window.chart._slots[0]
    slot._intraday_on = False
    if slot._intraday_after:
        slot.after_cancel(slot._intraday_after)
        slot._intraday_after = None
    fetched = []
    def offline_fetch(*args, **kwargs):
        fetched.append(str(args))
        raise AssertionError('The comparison probe must not request live prices')
    slot._minute_fetch = slot._hourly_fetch = offline_fetch
    if slot._intraday_poller is not None:
        slot._intraday_poller.fetch = offline_fetch

    checks, failures = [], []
    phase = 'chart_start'
    chart_host = comparison = growth_host = None
    snapshot = {}
    deadline = time.monotonic() + 68
    settle_until = 0.0
    finishing = False

    def write_result(ok):
        value = dict(ok=ok, synthetic=True, real_tk=True, real_chart_webview=True,
                     real_growth_webview=True, checks=checks, failures=failures, phase=phase, **snapshot)
        (folder / 'result.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def preserved():
        assert chart_host is slot._embedded, 'Navigation replaced the chart host object'
        assert chart_host.process.pid == snapshot['chart_pid'], 'Navigation started a new chart process'
        assert chart_host.process.poll() is None and chart_host.ready(), 'The original chart is no longer usable'
        assert chart_host.token == snapshot['chart_token'], 'Navigation reloaded the chart context'
        assert chart_host._history_identity == snapshot['chart_identity'], 'Chart identity changed'
        assert chart_host.control.read_bytes() == snapshot['_control'], 'Navigation resent a chart context'
        assert chart_host.session.read_bytes() == snapshot['_session'], 'Chart bootstrap was replaced'
        assert slot._workspace_context is context and slot._workspace_context['frame'] is frame
        assert slot._code == item.code and slot._tf == item.timeframe
        assert window._tf_var.get() == item.timeframe and window.chart.current_item() == item
        assert store.chart_document(boot['identity']) == baseline, 'Saved drawing, timeframe or viewport changed'
        assert not fetched, 'Live-price fetching occurred in the offline comparison test'

    def finish(ok):
        nonlocal finishing
        if finishing:
            return
        finishing = True
        try:
            # The real application exit path requests close and waits for each
            # browser's ordinary save acknowledgement; no child is terminated.
            assert window._flush_embedded_charts(), 'The real close handshake did not finish'
            assert chart_host is None or chart_host.closed()
            assert comparison is None or comparison.closed()
            assert growth_host is None or growth_host.closed()
            assert store.chart_document(boot['identity']) == baseline
            if ok:
                checks.append('ordinary_close_guard_acknowledged_all_children')
        except Exception:
            ok = False
            failures.append(traceback.format_exc())
        finally:
            # Transport bytes are only internal assertions, not JSON output.
            snapshot.pop('_control', None)
            snapshot.pop('_session', None)
            write_result(ok)
            window._closing = True
            window.destroy()

    def fail_callback(exc_type, exc, tb):
        failures.append(''.join(traceback.format_exception(exc_type, exc, tb)))
        finish(False)
    window.report_callback_exception = fail_callback

    def poll():
        nonlocal phase, chart_host, comparison, growth_host, settle_until
        try:
            assert time.monotonic() < deadline, 'Comparison acceptance timed out in phase ' + phase
            slot._intraday_on = False
            if phase == 'chart_start':
                # Let initial empty-candidate notebook callbacks settle before
                # selecting the synthetic chart, as a trader would after launch.
                window.chart._items = window.chart._display_items = [item]
                slot._code, slot._tf = item.code, item.timeframe
                slot._title_var.set(item.name + ' · 日线')
                slot._workspace_context = context
                window._chart_mode = 'watch'
                window._tf_var.set(item.timeframe)
                slot._show_interactive()
                slot._intraday_on = False
                chart_host = slot._embedded
                assert chart_host is not None, slot.readout.get()
                phase = 'chart_ready'
            elif phase == 'chart_ready':
                if chart_host.error:
                    raise AssertionError(chart_host.error)
                assert not chart_host.closing and not chart_host.closed(), slot.readout.get()
                if chart_host.ready():
                    snapshot.update(chart_pid=chart_host.process.pid, chart_token=chart_host.token,
                                    chart_identity=boot['identity'], _control=chart_host.control.read_bytes(),
                                    _session=chart_host.session.read_bytes())
                    preserved()
                    checks.append('canonical_drawing_view_and_period_loaded_in_real_chart')
                    window._switch_section('growth')
                    phase = 'growth_ready'
            elif phase == 'growth_ready':
                preserved()
                growth_host = window._pages['growth'].host
                if growth_host is not None:
                    status = read_notification(growth_host.status) or {}
                    assert not status.get('error'), status
                    if status.get('ready'):
                        assert not window._pages['premarket'].winfo_ismapped()
                        assert window._pages['growth'].winfo_ismapped()
                        snapshot['growth_pid'] = growth_host.process.pid
                        window._switch_section('premarket')
                        settle_until = time.monotonic() + .5
                        phase = 'navigation_return'
            elif phase == 'navigation_return' and time.monotonic() >= settle_until:
                preserved()
                assert window._pages['premarket'].winfo_ismapped()
                assert not window._pages['growth'].winfo_ismapped()
                checks.append('growth_navigation_returns_same_chart_process_and_context')
                window._open_case_comparison()
                assert len(window._case_comparisons) == 1
                comparison = window._case_comparisons[0]
                comparison_payload = json.loads(comparison.session.read_text(encoding='utf-8'))
                assert comparison_payload['context'] == dict(symbol=item.code, name=item.name,
                                                             timeframe=item.timeframe, comparison=True)
                phase = 'comparison_ready'
            elif phase == 'comparison_ready':
                preserved()
                status = read_notification(comparison.status) or {}
                assert not status.get('error'), status
                if status.get('ready'):
                    assert comparison.process.pid not in (snapshot['chart_pid'], snapshot['growth_pid'])
                    assert comparison.parent.winfo_toplevel().winfo_exists()
                    snapshot['comparison_pid'] = comparison.process.pid
                    checks.append('actual_comparison_opens_beside_original_chart')
                    comparison.request_close()
                    phase = 'comparison_closed'
            elif phase == 'comparison_closed' and comparison.closed():
                preserved()
                checks.extend(['comparison_close_preserves_saved_drawings_view_and_period',
                               'same_embedded_process_token_identity_without_reload', 'no_live_price_fetch'])
                phase = 'complete'
                finish(True)
                return
            if not finishing:
                window.after(80, poll)
        except Exception:
            failures.append(traceback.format_exc())
            snapshot['diagnostic'] = dict(slot_code=slot._code, slot_timeframe=slot._tf,
                readout=slot.readout.get(), host_token=getattr(chart_host, 'token', None),
                host_error=getattr(chart_host, 'error', None),
                chart_status=read_notification(chart_host.status) if chart_host else None)
            finish(False)

    window.after(750, poll)
    window.mainloop()
    result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    assert result['ok'], '\n'.join(result['failures'])


if __name__ == '__main__':
    run(sys.argv[1])
