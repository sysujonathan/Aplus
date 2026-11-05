"""Manual-consent single-account Google acceptance; never run by pytest/CI.

Uses the production page/Bridge/Store/Google transport. Only three explicitly
marked synthetic cases are uploaded to the fixed PA folder. No cleanup/deletes.
Client JSON remains at the user's supplied path; receipts contain no tokens.
"""
import hashlib
import json
from pathlib import Path
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder, client, reuse_login=False):
    import webview
    from gui.growth_bridge import GrowthBridge
    from gui.growth_shell import ASSETS, GrowthCloseGuard
    from workbench.store import Store
    from workbench.pa_auth import GoogleSession
    from workbench.pa_shared import GoogleDrive, Workspace, FOLDER
    from workbench.pa_terms import graph
    from tests.pa_growth_desktop_probe import samples

    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT / '.test-pa-growth'):
        raise ValueError('Isolated test directory required')
    folder.mkdir(parents=True, exist_ok=True)
    previous = json.loads((folder / 'live-result.json').read_text(encoding='utf-8')) if reuse_login else None
    if reuse_login and (not previous or not previous.get('ok') or len(previous.get('case_ids', [])) != 3):
        raise ValueError('Restart verification requires a completed first live acceptance')
    samples(folder / 'samples')
    store = Store(folder / 'runtime')
    tag = 'Google单账号验收-' + folder.name
    cases = store.pa_search(tag)
    if not cases:
        for index in range(3):
            case = store.pa_save_case([dict(name=f'测试样本-{i}.png', data=(folder / 'samples' / f'sample-{i}.png').read_bytes()) for i in (1, 2)],
                dict(memory=f'{tag}-{index+1} · ETHUSD 锦和商业 microDB', data_kind='test',
                     topic_ids=['brooks-high-2', 'brooks-pullback']), allow_duplicate=True)['case']
            store.pa_trial_setup(case['id'], dict(mode='sequence', image_ids=[i['id'] for i in case['images']]))
        cases = store.pa_search(tag)
    if len(cases) != 3:
        raise ValueError('Expected exactly three isolated test cases')
    bridge = GrowthBridge(store)
    window = webview.create_window('Aplus · Google单账号验收 · 隔离测试样本', (ASSETS / 'index.html').as_uri(),
        js_api=bridge, width=1440, height=960, min_size=(900, 600), background_color='#101923')
    bridge._window = window
    guard = GrowthCloseGuard(window, bridge, folder / 'window-status.json')
    window.events.closing += guard.request

    def probe():
        checks = []
        def receipt(**extra):
            (folder / 'live-result.json').write_text(json.dumps(dict(real_google=True, accounts=1,
                synthetic_uploaded_cases=True, folder_id=FOLDER, test_tag=tag, checks=checks,
                multi_account_verified=False, real_network_outage_verified=False,
                real_revocation_verified=False, formal_runtime_modified=False,
                new_process_reuses_login=reuse_login, **extra),
                ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            window.events.loaded.wait(30)
            receipt(state='restoring_existing_login' if reuse_login else 'awaiting_user_consent', ok=False)
            # System browser opens. The user, not this probe, logs in/consents.
            user = bridge._drive.identity(True) if reuse_login else bridge._drive.session.login(client, consent_timeout=600)
            workspace = Workspace(store, bridge._drive)
            workspace.refresh()
            checks.append('real_desktop_pkce_consent_and_folder_read')
            receipt(state='checking_existing_three_samples' if reuse_login else 'uploading_three_marked_samples', ok=False)
            ids = previous['case_ids'] if reuse_login else [workspace.publish(c['id'])['case_id'] for c in cases]
            workspace.refresh()
            selected = [c for c in workspace.catalogue() if c['id'] in ids]
            assert len(selected) == 3 and len(set(ids)) == 3
            assert all(c['contributor']['id'] == user['id'] and c['data_kind'] == 'test' for c in selected)
            checks.append('three_real_google_immutable_commits_and_contributor')
            for query in (tag, 'microDB', 'ETHUSD', '锦和商业', 'H2'):
                hits = workspace.search(query)['cases']
                assert set(ids) <= {c['id'] for c in hits}
            assert len(workspace.search(tag, {'contributor': 'me'})['cases']) == 3
            g = graph(selected)
            assert len(g['nodes']) == 2 and g['edges'][0]['count'] == 3
            checks.append('real_keyword_term_contributor_search_and_cooccurrence')
            for case in selected:
                for index, image in enumerate(case['images'], 1):
                    assert workspace.image(case['id'], image['id']) == (folder / 'samples' / f'sample-{index}.png').read_bytes()
            checks.append('six_originals_verified_from_validated_cache_after_restart' if reuse_login
                          else 'six_originals_downloaded_from_google_exact_bytes')
            note = next((n for n in workspace.personal()['notes'] if n.get('case_id') == ids[0]
                         and n.get('text') == '单账号验收私密记录，不应上传'), None)
            if note is None:
                note = workspace.record('notes', ids[0], '单账号验收私密记录，不应上传')
            manifest = bridge._drive.read(ids[0].removeprefix('shared:'), 256 * 1024)
            assert note['text'].encode() not in manifest
            checks.append('personal_note_local_encrypted_not_in_cloud_manifest')
            restarted = Workspace(Store(store.root), GoogleDrive(GoogleSession(store.root)))
            restarted.refresh()
            assert set(ids) <= {c['id'] for c in restarted.catalogue()}
            assert any(n['id'] == note['id'] for n in restarted.personal()['notes'])
            checks.append('fresh_store_and_session_restore_same_ids_and_private_note')
            if reuse_login:
                checks.append('new_process_reuses_google_login_without_duplicate_uploads')
            receipt(state='single_account_protocol_passed_ui_review_pending', ok=True,
                    case_ids=ids, identity_hash=hashlib.sha256(user['id'].encode()).hexdigest(),
                    restored_judgment_count=len(restarted.personal()['trials']))
            print('Single-account Google checks passed; UI remains open for review.', flush=True)
        except Exception as exc:
            # Production transport errors intentionally contain no response
            # bodies/tokens. Do not serialize credential values or trace locals.
            receipt(state='failed_or_cancelled', ok=False, error=str(exc))
            print('Single-account Google checks incomplete: ' + str(exc), flush=True)

    webview.start(lambda: threading.Thread(target=probe, daemon=True).start(),
                  gui='edgechromium', debug=False, private_mode=True, storage_path=str(folder / ('webview-' + uuid.uuid4().hex)))


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2], reuse_login='--reuse-login' in sys.argv[3:])
