"""Native Edge acceptance of the empty atlas and masked screenshot stages."""
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
    from PIL import Image
    from gui.growth_bridge import GrowthBridge
    from gui.growth_shell import ASSETS
    from workbench.store import Store
    from tests.pa_growth_desktop_probe import samples

    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('Only isolated acceptance homes allowed')
    folder.mkdir(parents=True, exist_ok=True)
    samples(folder / 'samples')
    store = Store(folder / 'runtime')
    bridge = GrowthBridge(store)
    window = webview.create_window('PA 渐进试炼验收 · 测试样本', (ASSETS / 'index.html').as_uri(),
                                   js_api=bridge, hidden=not visual, width=1440, height=950)
    bridge._window = window
    errors, checks = [], []

    def js(code):
        return window.evaluate_js(code)

    def wait(condition):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if js(condition):
                return
            time.sleep(.1)
        raise AssertionError(condition)

    def click(selector):
        assert js(f"!!document.querySelector({json.dumps(selector)})"), selector
        js(f"document.querySelector({json.dumps(selector)}).dispatchEvent(new MouseEvent('click',{{bubbles:true,cancelable:true}}))")

    def field(selector, value, event='change'):
        js(f"var f=document.querySelector({json.dumps(selector)});f.value={json.dumps(value)};f.dispatchEvent(new Event('{event}',{{bubbles:true}}))")

    def checkpoint(name):
        if not visual:
            return
        (folder / 'visual.json').write_text(json.dumps({'stage': name}), encoding='utf-8')
        deadline = time.monotonic() + 180
        while not (folder / (name + '.continue')).exists():
            if time.monotonic() > deadline:
                raise TimeoutError(name)
            time.sleep(.1)

    def pixel(x=.9):
        return js(f"Array.from(document.querySelector('#trial-image').getContext('2d').getImageData(Math.floor(document.querySelector('#trial-image').width*{x}),Math.floor(document.querySelector('#trial-image').height*.5),1,1).data).slice(0,3)")

    def probe():
        try:
            assert window.events.loaded.wait(25)
            wait("!!document.querySelector('[data-page=universe]')")
            click('[data-page=universe]')
            wait("!!document.querySelector('.origin-point')")
            assert js("document.querySelectorAll('.topic-node,.case-star,.relationship').length") == 0
            checks.append('empty_universe_single_origin_no_phantom_topics')
            checkpoint('01-empty')
            c = store.pa_save_case([dict(name=f'sample-{i}.png', data=(folder / 'samples' / f'sample-{i}.png').read_bytes()) for i in (1, 2, 3)],
                                   dict(memory='测试样本 · 标题中的后续信息不能先显示', data_kind='test'))['case']
            original = [store.pa_image(i['id']) for i in c['images']]
            click('[data-page=home]'); click('[data-page=universe]')
            wait("document.querySelectorAll('.case-star').length===1")
            assert js("document.querySelectorAll('.topic-node').length") == 0
            checks.append('unclassified_case_grows_one_real_star')
            store.pa_update_case(c['id'], dict(topic_ids=['cont-h2']))
            click('[data-page=home]'); click('[data-page=universe]')
            wait("!!document.querySelector('[data-action=select-topic][data-id=continuation]')")
            assert not js("!!document.querySelector('[data-action=select-topic][data-id=discipline]')")
            before = js("document.querySelector('#atlas-world').getAttribute('transform')")
            click('[data-action=atlas-in]')
            assert js("document.querySelector('#atlas-world').getAttribute('transform')") != before
            click('[data-action=atlas-reset]')
            click('[data-action=select-topic][data-id=continuation]')
            wait("!!document.querySelector('[data-action=universe-enter]')")
            click('[data-action=universe-enter]')
            wait("!!document.querySelector('[data-action=select-topic][data-id=cont-h2]')")
            click('[data-action=universe-back]')
            wait("!!document.querySelector('[data-action=select-topic][data-id=continuation]')")
            assert not js("!!document.querySelector('[data-action=select-topic][data-id=cont-h2]')")
            click('[data-action=select-topic][data-id=continuation]')
            click('[data-action=universe-enter]')
            wait("!!document.querySelector('[data-action=select-topic][data-id=cont-h2]')")
            checks.append('case_backed_galaxy_drilldown_zoom_and_return')
            checkpoint('02-galaxy')
            click('[data-action=select-topic][data-id=cont-h2]')
            wait("!!document.querySelector('.topic-case')")
            click('.topic-case')
            wait("!!document.querySelector('[data-action=configure-trial]')")
            click('[data-action=configure-trial]')
            field('#setup-mode', 'mask')
            wait("document.querySelector('#setup-image')?.naturalWidth>0")
            field('#setup-cutoff', '45', 'input')
            click('[data-action=save-trial-setup]')
            wait("!document.querySelector('#setup-mode')")
            cfg = Store(store.root).pa_trial_setup(c['id'])
            assert cfg['cutoff'] == .45
            click('[data-action=start-trial]')
            wait("document.querySelector('#trial-image')?.tagName==='CANVAS' && document.querySelector('#trial-image').complete")
            assert pixel() == [8, 17, 28]
            assert not js("document.querySelector('.photo-heading').innerText.includes('后续信息')")
            click('[data-action=trial-original]')
            wait("document.querySelector('#original-image')?.tagName==='CANVAS' && document.querySelector('#original-image').complete")
            assert js("Array.from(document.querySelector('#original-image').getContext('2d').getImageData(1260,400,1,1).data).slice(0,3)") == [8, 17, 28]
            assert not js("!!document.querySelector('[data-action=original-next]')")
            click('[data-action=close-original]')
            wait("document.querySelector('#trial-image')?.complete")
            checks.append('mask_hides_future_pixels_title_and_fullscreen')
            assert not js("!!document.querySelector('.toast.error')"), 'drawing must not emit spurious errors'
            checkpoint('03-mask')
            click('[data-action=judgment][data-value=wait]')
            click('[data-action=save-judgment]')
            wait("!!document.querySelector('#trial-evidence')")
            assert pixel() == [8, 17, 28], 'saving alone must not reveal'
            for _ in range(4):
                click('[data-action=trial-next]')
            wait("document.querySelector('#trial-image')?.complete")
            assert pixel() == list(Image.open(folder / 'samples/sample-1.png').getpixel((1260, 400)))
            assert not js("!!document.querySelector('[data-action=trial-next]')")
            record = store.pa_case(c['id'])['trials'][0]
            assert record['details']['presentation'] == cfg
            assert [store.pa_image(i['id']) for i in c['images']] == original
            checks.append('explicit_four_stage_reveal_original_bytes_and_judgment_preserved')
            checkpoint('04-revealed')
            (folder / 'result.json').write_text(json.dumps(dict(ok=True, synthetic=True, real_webview=True,
                                                                checks=checks), ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            errors.append(traceback.format_exc())
            (folder / 'failure.txt').write_text(errors[-1], encoding='utf-8')
        finally:
            window.destroy()

    webview.start(probe, gui='edgechromium', private_mode=True, storage_path=str(folder / 'webview'))
    if errors:
        raise AssertionError(errors[0])


if __name__ == '__main__':
    run(sys.argv[1], '--visual' in sys.argv[2:])
