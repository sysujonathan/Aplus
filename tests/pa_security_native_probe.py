"""Two real WebViews observe one DPAPI session and one permission epoch.

Remote Drive protocol is synthetic. No real accounts or production data.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder):
    import webview
    from gui.growth_bridge import GrowthBridge
    from gui.growth_shell import ASSETS
    from workbench.pa_auth import GoogleSession, DriveError
    from workbench.pa_shared import Workspace, FOLDER
    from workbench.store import Store
    from tests.test_pa_shared import Cloud, Drive, make_case
    from tests.test_pa_session_boundaries import credentials

    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('Isolated directory required')
    folder.mkdir(parents=True, exist_ok=True)
    store, cloud = Store(folder / 'runtime'), Cloud()
    class ScopedDrive(Drive):
        def __init__(self):
            super().__init__(cloud, 'a')
            self.session = GoogleSession(store.root)
            self.session.path = folder / 'test-session.dpapi'
        def identity(self, online=False):
            if online:
                self.check()
            value = self.session.load()
            if not value:
                raise ValueError('请先连接 Google Drive')
            return value['user']
    drives = [ScopedDrive(), ScopedDrive()]
    drives[0].session.save(credentials('a', time.time() + 3600))
    case, _ = make_case(store)
    workspace = Workspace(store, drives[0])
    public = workspace.publish(case['id'])['case_id']
    workspace.refresh()
    workspace.record('notes', public, 'ONLY_ACCOUNT_A_PRIVATE')
    bridges, windows = [], []
    for i in range(2):
        bridge = GrowthBridge(Store(store.root))
        bridge._drive = drives[i]
        window = webview.create_window(f'PA账号边界隔离验收-{i}', (ASSETS / 'index.html').as_uri(),
            js_api=bridge, hidden=True, width=1100, height=800)
        bridge._window = window
        bridges.append(bridge)
        windows.append(window)
    errors = []
    def wait(window, expression):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            if window.evaluate_js(expression):
                return
            time.sleep(.1)
        raise AssertionError(expression)
    def probe():
        try:
            for window in windows:
                assert window.events.loaded.wait(25)
                wait(window, "document.querySelectorAll('.case-card').length===1")
                window.evaluate_js("document.querySelector('[data-page=growth]').click()")
                wait(window, "document.body.innerText.includes('ONLY_ACCOUNT_A_PRIVATE')")
            # External session write, not a connect button in either window.
            drives[0].session.save(credentials('b', time.time() + 3600))
            for window in windows:
                wait(window, "!document.body.innerText.includes('ONLY_ACCOUNT_A_PRIVATE') && document.querySelector('#shared-account').innerText.includes('b')")
            wb = Workspace(store, drives[0])
            wb.refresh()
            for window in windows:
                wait(window, "document.querySelectorAll('.case-card').length===1")
            image = wb.case(public)['images'][0]['id']
            # Only a disposable test cache is removed to force a cold read.
            (store.root / 'pa_growth' / 'shared_cache' / FOLDER / image).unlink(missing_ok=True)
            def denied(*args):
                raise DriveError(403)
            drives[0].read = denied
            result = bridges[0].get_image(public, image)
            assert not result['ok'] and result['error_kind'] == 'security'
            for window in windows:
                wait(window, "document.querySelectorAll('.case-card').length===0 && !document.querySelector('.study-image img') && !document.querySelector('#original-image')")
            drives[0].session.logout()
            for window in windows:
                wait(window, "document.querySelector('#shared-account').innerText.includes('连接 Google 账号')")
            (folder / 'result.json').write_text(json.dumps(dict(ok=True, real_webviews=2,
                real_dpapi=True, real_google=False, synthetic=True, checks=[
                    'external_account_switch_clears_both_windows_private_records',
                    'image_403_revokes_shared_cache_and_clears_both_windows',
                    'external_logout_invalidates_both_windows']), indent=2), encoding='utf-8')
        except Exception:
            errors.append(traceback.format_exc())
            (folder / 'failure.txt').write_text(errors[0], encoding='utf-8')
        finally:
            for window in windows:
                window.destroy()
    webview.start(lambda: threading.Thread(target=probe, daemon=True).start(), gui='edgechromium', debug=False)
    if errors:
        raise AssertionError(errors[0])


if __name__ == '__main__':
    run(sys.argv[1])
