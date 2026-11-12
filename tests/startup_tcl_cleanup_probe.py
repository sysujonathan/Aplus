"""Real entry points, isolated synthetic Store and forced worker GC after paint."""
import ctypes
import gc
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder, entry):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    os.environ.update(A_WORKBENCH_HOME=str(folder / 'synthetic-runtime'),
                      APLUS_INSTALL_STATE=str(folder / 'synthetic-profile'),
                      APLUS_STARTUP_TRACE_PATH=str(folder / 'startup.jsonl'))
    if entry == 'plain-loader':
        # Import no ttkbootstrap/test fixtures: this is the exact native panic
        # path observed before the first desktop module has been imported.
        from gui.startup_loading import initialize
        for _ in range(3):
            assert initialize(lambda: 'ready', handoff=False) == 'ready'
            worker = threading.Thread(target=gc.collect, name='plain-worker-gc')
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
        print('Plain Tk interpreters released safely', flush=True)
        return
    # No real account, provider calls, release checks or process-wide app lock.
    import pandas as pd
    from workbench.store import Store
    from workbench.market import save_dataset
    store = Store(folder / 'synthetic-runtime')
    frame = pd.DataFrame(dict(date=pd.bdate_range(end='2026-11-12', periods=180).strftime('%Y-%m-%d'),
                              open=10., high=11., low=9., close=10.5, volume=100.))
    save_dataset(store, '600000', frame, 'baostock', '前复权')
    store.add_manual_watch('600000', 'Synthetic startup verification')
    from workbench.holding_quotes import QuotePoller
    from workbench.live_minutes import MinutePoller, HourlyHistory
    QuotePoller.start = MinutePoller.start = lambda *a, **k: False
    HourlyHistory.request = lambda *a, **k: False
    from gui import watch_chart
    watch_chart.chart_day = lambda: '2026-11-12'
    def unexpected_network(*a, **k):
        raise AssertionError('Startup probe must only use its synthetic local dataset')
    watch_chart.fetch_history = unexpected_network

    import launch_dashboard
    from gui.version_update import VersionController
    VersionController.startup_check = lambda *a: None
    launch_dashboard.acquire_single_instance = lambda *a, **k: object()
    launch_dashboard.release_single_instance = lambda *a: None
    factory = launch_dashboard.prepare_workbench
    result = dict(entry=entry, collections=0, heartbeats=0, chart_ready=False,
                  startup_ack=False, ok=False)
    failures = []
    stop = threading.Event()
    collectors = []
    ack = folder / 'synthetic-profile' / 'updates' / 'isolated' / 'started.json'

    def prepare():
        service, state, window_type = factory()  # Real background factory/import.
        class Window(window_type):
            def __init__(self, *a, **k):
                k['enable_tray'] = False
                super().__init__(*a, **k)
                self.after(50, self.verify_start)
                self.after(50, self.beat)
                self.deadline = time.monotonic() + 35
                self.testing = False
                self.selected = False

            def beat(self):
                result['heartbeats'] += 1
                if not self._closing:
                    self.after(50, self.beat)

            def verify_start(self):
                if self._closing:
                    return
                if not self.selected and self.watch.rows():
                    self.selected = True
                    self.on_watch_selected('sh.600000')
                hosts = self.chart.embedded_hosts()
                ready = bool(hosts) and all(host.ready() for host in hosts)
                if not self.testing and ready:
                    result['chart_ready'] = True
                    self.testing = True
                    def collect():
                        for _ in range(40):
                            if stop.wait(.05): break
                            gc.collect()
                            result['collections'] += 1
                    worker = threading.Thread(target=collect, name='startup-data-worker-gc')
                    collectors.append(worker)
                    worker.start()
                if self.testing and result['collections'] >= 40:
                    result['startup_ack'] = ack.exists()
                    result['ok'] = self.winfo_ismapped() and bool(hosts)
                    self._exit_from_tray()  # Original chart-save close handshake.
                    return
                if time.monotonic() >= self.deadline:
                    failures.append('Desktop/chart did not complete native startup')
                    self._exit_from_tray()
                    return
                self.after(100, self.verify_start)
        return service, state, Window
    launch_dashboard.prepare_workbench = prepare
    try:
        if entry == 'installed-update':
            from workbench import installed_runtime as installed, installed_update
            installed.bound_runtime = lambda: store.root
            os.environ['APLUS_INSTALL_ROOT'] = str(folder / 'synthetic-install')
            os.environ['APLUS_INSTALLED_ACK'] = str(ack)
            ack.parent.mkdir(parents=True)
            installed_update.current_release = lambda: dict(version='2026.11.12.3',
                commit='0' * 40, database_schema=9)
            # Maintenance scheduling is unchanged; this probe is not a backup test.
            from workbench import installed_backup_rotation
            installed_backup_rotation.rotate_backups = lambda **k: None
            installed.launch()
        else:
            launch_dashboard.main()
    finally:
        stop.set()
        for worker in collectors: worker.join(timeout=5)
        result['failures'] = failures
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['ok'] and not failures, result
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
