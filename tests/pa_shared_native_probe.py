"""Real Edge UI over synthetic two-identity Drive protocol, never live OAuth."""
import base64
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder, visual=False):
    import webview
    from gui.growth_bridge import GrowthBridge, guarded
    from gui.growth_shell import ASSETS
    from workbench.store import Store
    from workbench.pa_shared import Workspace
    from tests.test_pa_shared import Cloud, Drive
    from tests.pa_growth_desktop_probe import samples

    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('Isolated native test folder required')
    samples(folder / 'samples')
    stores = [Store(folder / 'a'), Store(folder / 'b')]
    cloud = Cloud()
    drives = [Drive(cloud, 'isolated-test-A'), Drive(cloud, 'isolated-test-B')]
    for index in range(5):
        store = stores[index // 3]
        case = store.pa_save_case([dict(name=f'sample-{i}.png', data=(folder / 'samples' / f'sample-{i}.png').read_bytes()) for i in (1, 2)],
            dict(memory=f'隔离测试 {"A" if index < 3 else "B"}{index+1} · ETHUSD 锦和商业 microDB',
                 data_kind='test', topic_ids=['brooks-high-2', 'brooks-pullback']), allow_duplicate=True)['case']
        store.pa_trial_setup(case['id'], dict(mode='sequence', image_ids=[i['id'] for i in case['images']]))
        Workspace(store, drives[index // 3]).publish(case['id'])
    for store, drive in zip(stores, drives):
        Workspace(store, drive).refresh()

    class ProbeBridge(GrowthBridge):
        @guarded
        def connect_google(self):
            self._drive = drives[1]
            return {'user': self._drive.identity(), **self._public('refresh')}

    bridge = ProbeBridge(stores[0])
    bridge._drive = drives[0]
    window = webview.create_window('PA 共享空间验收 · 隔离双身份测试样本', (ASSETS / 'index.html').as_uri(),
                                  js_api=bridge, hidden=not visual, width=1440, height=960)
    bridge._window = window
    checks, errors = [], []

    def js(code):
        return window.evaluate_js(code)

    def wait(condition, seconds=20):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if js(condition):
                return
            time.sleep(.08)
        raise AssertionError(condition + '\n' + str(js('document.body.innerText')))

    def click(selector):
        wait(f'!!document.querySelector({json.dumps(selector)}) && !window.aplusGrowthBusy()')
        js(f'document.querySelector({json.dumps(selector)}).dispatchEvent(new MouseEvent("click",{{bubbles:true,cancelable:true}}))')

    def field(selector, value):
        js(f"{{const f=document.querySelector({json.dumps(selector)});f.value={json.dumps(value)};f.dispatchEvent(new Event('input',{{bubbles:true}}));}}")

    def probe():
        try:
            assert window.events.loaded.wait(25)
            wait("document.querySelectorAll('.case-card').length===5")
            click('[data-page=universe]')
            wait("document.querySelectorAll('.term-node').length===2")
            assert js("document.querySelectorAll('.cooccurrence').length") == 1
            click('[data-id=brooks-high-2]')
            wait("document.querySelectorAll('.topic-case').length===5")
            checks.append('five_shared_cases_real_cooccurrence_and_high2_case_links')
            click('.topic-case')
            wait("document.querySelector('.study-image img')?.naturalWidth>0")
            click('[data-action=original]')
            wait("document.querySelector('#original-image')?.naturalWidth>0")
            raw = base64.b64decode(js("document.querySelector('#original-image').src.split(',')[1]"))
            assert raw == (folder / 'samples/sample-1.png').read_bytes()
            click('[data-action=zoom-in]')
            click('[data-action=zoom-out]')
            click('[data-action=close-original]')
            checks.append('public_original_exact_bytes_zoom_and_return')
            click('[data-action=study-pane][data-pane=notes]')
            field('#case-note', '只属于 A 的中文研究：先看背景，再看入场。')
            click('[data-action=save-note]')
            wait("document.querySelector('.note-list')?.innerText.includes('只属于 A')")
            click('[data-action=study-pane][data-pane=memory]')
            click('[data-action=mark-studied]')
            click('[data-page=trial]')
            click('[data-action=choose-trial]')
            wait("document.querySelector('#trial-image')?.naturalWidth>0")
            assert not js("!!document.querySelector('#trial-evidence')")
            first = js("document.querySelector('#trial-image').src")
            assert not js("document.querySelector('.photo-heading').innerText.includes('ETHUSD')")
            click('[data-action=judgment][data-value=wait]')
            click('[data-action=save-judgment]')
            wait("!!document.querySelector('#trial-evidence') && !window.aplusGrowthBusy()")
            assert js("document.querySelector('#trial-image').src") == first
            click('[data-action=trial-next]')
            wait("document.querySelector('#trial-image')?.naturalWidth>0 && document.querySelector('#trial-image').src!==" + json.dumps(first))
            checks.append('random_qualified_sequence_judgment_before_explicit_reveal')
            click('[data-action=finish-trial]')
            click('[data-page=growth]')
            wait("document.querySelector('.growth-number')?.innerText==='3'")
            assert js("document.body.innerText.includes('只属于 A')")
            checks.append('personal_contributions_research_and_trial_are_distinct')
            # Exercise production account-switch clearing. Only the OAuth
            # identity provider is substituted, explicitly marked synthetic.
            js("window._oldResearchText='只属于 A 的中文研究'")
            js("document.querySelector('#shared-account').insertAdjacentHTML('beforeend','<button id=account-switch-probe data-action=connect-google>切换隔离身份</button>')")
            click('#account-switch-probe')
            wait("document.querySelector('#shared-account').innerText.includes('isolated-test-B')")
            assert not js("document.body.innerText.includes('只属于 A')")
            click('[data-page=growth]')
            wait("document.querySelector('.growth-number')?.innerText==='2'")
            assert js("document.querySelectorAll('.growth-number')[2].innerText") == '0'
            assert not js("document.body.innerText.includes('只属于 A')")
            checks.append('account_switch_clears_previous_personal_ui_and_history')
            click('[data-page=library]')
            wait("!!document.querySelector('#search-input')")
            js("window._stableInput=document.querySelector('#search-input');window._stableInput.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}))")
            field('#search-input', 'jinhe')
            time.sleep(.3)
            assert js("document.querySelector('#search-input')===window._stableInput")
            field('#search-input', '锦和商业')
            js("window._stableInput.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'锦和商业'}))")
            wait("document.querySelectorAll('.case-card').length===5 && document.querySelector('.hit')?.innerText.includes('命中')")
            assert js("document.querySelector('#search-input')===window._stableInput")
            checks.append('real_webview_composition_and_chinese_results_keep_same_input')
            field('#search-input', 'H2')
            wait("document.querySelector('.hit')?.innerText.includes('Brooks')")
            cloud.offline = True
            click('[data-action=refresh-shared]')
            wait("document.querySelector('.toast.error')?.innerText.includes('断网')")
            assert js("document.querySelectorAll('.case-card').length") == 5
            checks.append('offline_refresh_keeps_validated_cache_without_false_success')
            cloud.offline = False
            click('[data-page=universe]')
            click('[data-id=brooks-high-2]')
            wait("document.querySelectorAll('.topic-case').length===5")
            (folder / 'result.json').write_text(json.dumps(dict(ok=True, real_webview=True, synthetic=True,
                real_google=False, real_ime_keyboard=False, checks=checks), ensure_ascii=False, indent=2), encoding='utf-8')
            if visual:
                (folder / 'visual-ready').write_text('ready', encoding='utf-8')
                until = time.monotonic() + 900
                while not (folder / 'finish').exists() and time.monotonic() < until:
                    time.sleep(.2)
        except Exception:
            errors.append(traceback.format_exc())
            (folder / 'failure.txt').write_text(errors[0], encoding='utf-8')
        finally:
            window.destroy()

    window.events.loaded += lambda: threading.Thread(target=probe, daemon=True).start()
    webview.start(gui='edgechromium', debug=False)
    if errors:
        raise AssertionError(errors[0])


if __name__ == '__main__':
    run(sys.argv[1], '--visual' in sys.argv[2:])
