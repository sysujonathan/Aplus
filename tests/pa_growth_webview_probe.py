"""Real Edge WebView + production JS + Store acceptance on synthetic images."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder, reopen=False):
    import webview
    from gui.growth_bridge import GrowthBridge
    from gui.growth_shell import ASSETS
    from workbench.store import Store
    from tests.pa_growth_desktop_probe import samples

    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('Only isolated acceptance homes are allowed')
    samples(folder / 'samples')
    store = Store(folder / 'runtime')
    image_reads = []

    class ObservedBridge(GrowthBridge):
        def get_image(self, case_id, asset_id, thumbnail=False):
            image_reads.append((case_id, asset_id, thumbnail))
            return super().get_image(case_id, asset_id, thumbnail)

    bridge = ObservedBridge(store)
    window = webview.create_window('PA 真实闭环验收 · 隔离测试', (ASSETS / 'index.html').as_uri(),
                                  js_api=bridge, hidden=True, width=1440, height=950)
    bridge._window = window
    failures, checks = [], []

    def evaluate(script):
        return window.evaluate_js(script)

    def wait(script, seconds=15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if evaluate(script):
                return
            time.sleep(.06)
        raise AssertionError('WebView wait: ' + script + '\n' + str(evaluate('document.body.innerText')))

    def click(selector):
        wait("typeof window.aplusGrowthBusy !== 'function' || !window.aplusGrowthBusy()")
        if not evaluate(f'Boolean(document.querySelector({json.dumps(selector)}))'):
            raise AssertionError('Missing action: ' + selector)
        evaluate(f"{{ const e=document.querySelector({json.dumps(selector)}); if(e.disabled) throw new Error('Action disabled'); e.dispatchEvent(new MouseEvent('click',{{bubbles:true,cancelable:true}})); }}")

    def field(selector, value):
        evaluate(f"{{ const e=document.querySelector({json.dumps(selector)}); e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}})); }}")

    def stage(indices, paste=False):
        values = [dict(name=f'sample-{i}.png', data=base64.b64encode((folder / 'samples' / f'sample-{i}.png').read_bytes()).decode()) for i in indices]
        script = '''{const dt=new DataTransfer(); for(const value of VALUES){
          const bytes=Uint8Array.from(atob(value.data),c=>c.charCodeAt(0));
          dt.items.add(new File([bytes],value.name,{type:'image/png'})); }
          document.dispatchEvent(EVENT); }'''.replace('VALUES', json.dumps(values))
        script = script.replace('EVENT', "new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})" if paste else "new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true})")
        evaluate(script)
        wait("document.querySelector('#capture-save') && !window.aplusGrowthBusy()")

    def probe():
        try:
            assert window.events.loaded.wait(20)
            wait("document.querySelector('[data-action=import]') && !document.querySelector('.failure-screen')")
            if reopen:
                wait("Boolean(document.querySelector('.case-card'))")
                assert not evaluate("Boolean(document.querySelector('.first-hint'))")
                assert len(store.pa_search(limit=1000)) == 104
                assert store.pa_growth()['stats'] == dict(cases=104, images=106, notes=1, trials=1, topics=1)
                older_id = store.pa_search('microDB')[0]['id']
                assert older_id not in {item['id'] for item in store.pa_search()}
                wait("document.querySelector('.resume [data-action=study-open]')?.dataset.id === " + json.dumps(older_id))
                click('.resume [data-action=study-open]')
                wait("document.querySelector('.study-image img')?.complete && document.querySelector('.study-image img')?.naturalWidth > 0")
                assert image_reads == [(older_id, store.pa_case(older_id, touch=False)['images'][0]['id'], False)]
                (folder / 'reopened-result.json').write_text(json.dumps(dict(ok=True, new_process=True,
                    original_notes_and_trials=True, no_repeated_hint=True, recent_case_outside_first_page=True,
                    one_original_on_resume=True)), encoding='utf-8')
                return
            assert evaluate("Boolean(document.querySelector('.first-hint'))")
            stage([1], paste=True)
            field('[data-meta=memory]', 'microDB 结构回踩测试样本')
            field('[data-meta=keywords]', 'microDB,ETHUSD,锦和商业')
            field('[data-meta=data_kind]', 'test')
            evaluate("document.querySelector('[data-topic-select=\"cont-h2\"]').checked=true")
            # A failed Store commit must keep staged image and entered memory.
            original = store.pa_save_case
            store.pa_save_case = lambda *a, **k: (_ for _ in ()).throw(OSError('验收注入：保存失败'))
            click('[data-action=save-capture]')
            wait("document.querySelector('.save-error')?.textContent.includes('保存失败')")
            assert evaluate("document.querySelector('[data-meta=memory]').value.includes('microDB') && !document.body.innerText.includes('已保存 1 张')")
            assert not store.pa_search()
            store.pa_save_case = original
            click('[data-action=save-capture]')
            wait("!document.querySelector('#capture-save') && document.querySelector('.case-card') && !window.aplusGrowthBusy()")
            assert len(store.pa_search()) == 1
            checks.extend(['paste_event_original_image', 'failure_keeps_draft', 'durable_save_ack'])
            # Batch drop defaults to independent cases.
            stage([2, 3])
            field('[data-meta=memory]', 'ETHUSD 锦和商业 批量测试样本')
            field('[data-meta=data_kind]', 'test')
            click('[data-action=save-capture]')
            wait("!document.querySelector('#capture-save') && !window.aplusGrowthBusy()")
            assert len(store.pa_search()) == 3
            assert not evaluate("Boolean(document.querySelector('.first-hint'))")
            checks.append('batch_drop_independent_cases')
            # Duplicate warning never merges or silently overwrites.
            stage([1])
            wait("Boolean(document.querySelector('#capture-duplicate'))")
            assert len(store.pa_search()) == 3
            evaluate('window.confirm=()=>true')
            click('[data-action=close-overlay]')
            wait("!document.querySelector('#capture-save')")
            checks.append('duplicate_warning_no_merge')
            click('[data-action=navigate][data-page=library]')
            wait("Boolean(document.querySelector('#search-input'))")
            for word in ('microDB', 'ETHUSD', '锦和商业'):
                field('#search-input', word)
                wait("document.querySelector('.hit')?.textContent.includes('命中')")
                assert evaluate("document.querySelector('.case-card img').src.startsWith('data:image/')")
            field('#search-input', 'not-found-不存在')
            wait("document.body.innerText.includes('没有找到对应案例')")
            field('#search-input', 'microDB')
            wait("document.querySelector('.case-card')?.innerText.includes('microDB')")
            click('.case-card')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('#original-image')?.naturalWidth > 0")
            src = evaluate("document.querySelector('#original-image').src")
            assert base64.b64decode(src.split(',', 1)[1]) == (folder / 'samples/sample-1.png').read_bytes()
            before = evaluate("document.querySelector('.zoom-label').innerText")
            click('[data-action=zoom-in]')
            assert evaluate("document.querySelector('.zoom-label').innerText") != before
            click('[data-action=close-original]')
            assert evaluate("document.querySelector('#search-input').value") == 'microDB'
            checks.extend(['three_keyword_partial_search', 'no_result_feedback', 'original_exact_bytes_zoom_back'])
            # Study note and original judgment are separate persisted records.
            case_id = store.pa_search('microDB')[0]['id']
            store.pa_save_case([{'name': f'sample-{i}.png',
                'data': (folder / 'samples' / f'sample-{i}.png').read_bytes()} for i in (2, 3)],
                case_id=case_id, allow_duplicate=True)
            click('.case-card')
            wait("Boolean(document.querySelector('[data-action=quick-study]'))")
            click('[data-action=quick-study]')
            wait("Boolean(document.querySelector('[data-action=study-pane]'))")
            click('[data-action=study-pane][data-pane=notes]')
            field('#case-note', '测试研究：等待边界确认，不从盈亏倒推判断')
            click('[data-action=save-note]')
            wait("document.querySelector('.note-list')?.innerText.includes('等待边界确认') && !window.aplusGrowthBusy()")
            click('[data-action=study-pane][data-pane=memory]')
            wait("Boolean(document.querySelector('[data-action=start-trial]'))")
            click('[data-action=start-trial]')
            wait("document.querySelector('#trial-image')?.complete && document.querySelector('#trial-image')?.naturalWidth > 0")
            assert not evaluate("Boolean(document.querySelector('#trial-evidence'))")
            first_trial_image = evaluate("document.querySelector('#trial-image').src")
            click('[data-action=trial-original]')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('#original-image')?.naturalWidth > 0")
            assert not evaluate("Boolean(document.querySelector('[data-action=original-next]') || document.querySelector('[data-action=original-prev]'))")
            assert evaluate("document.querySelector('#original-image').src") == first_trial_image
            click('[data-action=close-original]')
            wait("document.querySelector('#trial-image')?.complete && document.querySelector('#trial-image')?.naturalWidth > 0")
            assert evaluate("document.querySelector('#trial-image').src") == first_trial_image
            click('[data-action=judgment][data-value=wait]')
            click('[data-action=save-judgment]')
            wait("Boolean(document.querySelector('#trial-evidence'))")
            click('[data-action=trial-original]')
            wait("Boolean(document.querySelector('[data-action=original-next]'))")
            click('[data-action=original-next]')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('.original-footer')?.innerText.includes('2 / 3')")
            assert base64.b64decode(evaluate("document.querySelector('#original-image').src").split(',', 1)[1]) == (folder / 'samples/sample-2.png').read_bytes()
            click('[data-action=close-original]')
            assert len(store.pa_growth()['trials']) == 1
            checks.extend(['append_only_research_note', 'minimal_judgment_then_evidence_no_score',
                           'trial_original_respects_judgment_boundary'])
            click('[data-action=navigate][data-page=universe]')
            wait("Boolean(document.querySelector('[data-action=select-topic]'))")
            click('[data-action=select-topic][data-id=continuation]')
            wait("Boolean(document.querySelector('[data-action=universe-enter]'))")
            click('[data-action=universe-enter]')
            wait("Boolean(document.querySelector('[data-action=select-topic][data-id=cont-h2]'))")
            click('[data-action=select-topic][data-id=cont-h2]')
            wait("document.querySelector('#topic-note')?.dataset.draft === 'topic-note:cont-h2'")
            field('#topic-note', '测试认识：先确认背景，再看信号K线')
            click('[data-action=save-topic-note]')
            wait("!window.aplusGrowthBusy() && document.querySelector('#topic-note').value==='' ")
            click('[data-action=navigate][data-page=growth]')
            wait("document.body.innerText.includes('测试样本判断')")
            assert store.pa_growth()['stats']['topics'] == 1
            assert evaluate("document.body.innerText.includes('等待边界确认') && document.body.innerText.includes('先确认背景')")
            checks.append('knowledge_case_link_and_evidence_growth')
            reopened = Store(folder / 'runtime')
            assert len(reopened.pa_search()) == 3
            assert reopened.pa_case(case_id)['notes']
            assert reopened.pa_growth()['stats']['trials'] == 1
            checks.append('fresh_store_reads_persisted_records')
            # Appending does not make the detail API carry all full-size images.
            before = len(image_reads)
            detail = bridge.get_case(case_id)
            assert detail['ok'] and len(detail['case']['images']) == 3
            assert all('data_url' not in image for image in detail['case']['images'])
            assert len(image_reads) == before
            click('[data-action=navigate][data-page=library]')
            wait("Boolean(document.querySelector('#search-input'))")
            field('#search-input', 'microDB')
            wait("document.querySelector('.case-card')?.innerText.includes('microDB')")
            click('.case-card')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('#original-image')?.naturalWidth > 0")
            assert len(image_reads) == before, 'study/fullscreen reuse the current cached original'
            click('[data-action=original-next]')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('#original-image')?.naturalWidth > 0 && document.querySelector('.original-footer')?.innerText.includes('2 / 3')")
            assert len(image_reads) == before, 'the post-judgment original is still cached'
            click('[data-action=original-next]')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('#original-image')?.naturalWidth > 0 && document.querySelector('.original-footer')?.innerText.includes('3 / 3')")
            assert image_reads[before:] == [(case_id, detail['case']['images'][2]['id'], False)]
            assert base64.b64decode(evaluate("document.querySelector('#original-image').src").split(',', 1)[1]) == (folder / 'samples/sample-3.png').read_bytes()
            click('[data-action=original-prev]')
            wait("document.querySelector('#original-image')?.complete && document.querySelector('.original-footer')?.innerText.includes('2 / 3')")
            assert len(image_reads) == before + 1, 'returning one image reuses the bounded cache'
            click('[data-action=close-original]')
            checks.append('selected_original_only_and_cached_fullscreen')
            # The most recently viewed case may be older than the first 100.
            from PIL import Image
            tiny = io.BytesIO()
            Image.new('RGB', (32, 24), (37, 47, 60)).save(tiny, format='PNG')
            for index in range(101):
                store.pa_save_case([{'name': 'isolated-pagination-test.png', 'data': tiny.getvalue()}],
                    {'memory': f'分页隔离测试样本 {index}', 'data_kind': 'test'}, allow_duplicate=True)
            store.pa_case(case_id)
            assert case_id not in {item['id'] for item in store.pa_search()}
            click('[data-action=navigate][data-page=home]')
            wait("document.querySelector('.resume [data-action=study-open]')?.dataset.id === " + json.dumps(case_id))
            checks.append('recent_case_outside_first_page')
            (folder / 'result.json').write_text(json.dumps(dict(ok=True, real_webview=True, real_store=True,
                synthetic=True, checks=checks), ensure_ascii=False, indent=2), encoding='utf-8')
        except BaseException:
            failures.append(traceback.format_exc())
            (folder / 'failure.txt').write_text(failures[-1], encoding='utf-8')
        finally:
            window.destroy()

    webview.start(probe, gui='edgechromium', private_mode=True, storage_path=str(folder / 'webview'))
    if failures:
        raise AssertionError(failures[0])


if __name__ == '__main__':
    run(sys.argv[1], reopen='--reopen' in sys.argv[2:])
