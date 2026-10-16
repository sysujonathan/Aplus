"""Main approves releases; target hashes check integrity, not strategy permission."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from workbench import version_update as v
from workbench import releases
from tests.test_version_update import repo, git, api_for


PATHS = ('core/strategies/mtr_strategy.py', 'core/strategies/mtr_structural_v35.py')
OTHER = 'config/settings.py'


def checksum(blob):
    return hashlib.sha256(blob).hexdigest()


@pytest.fixture
def approved_repo(repo, monkeypatch):
    root, _, _ = repo
    old_blobs = {PATHS[0]: b'legacy_entry = 1\n', PATHS[1]: b'legacy_structure = 1\n',
                 OTHER: b'unchanged = True\n'}
    new_blobs = dict(old_blobs, **{PATHS[0]: b'approved_entry = 2\n',
                                 PATHS[1]: b'approved_structure = 2\n'})
    approval = {'strategy': 'MTR_MASTER', 'revision': 'any reviewed future version'}
    manifest = {'source': 'https://github.com/lhy438769415-wq/A', 'commit': 'a' * 40,
                'files': [{'path': path, 'sha256': checksum(blob)} for path, blob in old_blobs.items()]}
    for path, blob in old_blobs.items():
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
    (root / 'frozen_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'v41 legacy frozen version')
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'approved-release')
    for path, blob in new_blobs.items():
        (root / path).write_bytes(blob)
    manifest['approved_strategy_updates'] = [approval]
    for entry in manifest['files']:
        entry['sha256'] = checksum(new_blobs[entry['path']])
    (root / 'frozen_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'v43 includes approved PR42 upgrade')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', target)
    return root, old, target


def revise(approved_repo, change):
    root, old, target = approved_repo
    git(root, 'switch', 'approved-release')
    change(root)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'changed candidate')
    newer = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', newer)
    return root, old, newer


def edit_manifest(root, edit):
    path = root / 'frozen_manifest.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    edit(value)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_v41_to_v43_approved_upgrade_preserves_user_data_launcher_and_backup(approved_repo):
    root, old, target = approved_repo
    runtime = root / 'runtime'; runtime.mkdir()
    sentinel = runtime / 'personal.db'; sentinel.write_bytes(b'never opened or reset')
    launcher = root / 'push.cmd'; launcher.write_text('local launcher\n', encoding='utf-8')
    launcher_bytes = launcher.read_bytes()
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and plan['ci_passed'] and not plan.get('block_reason')
    assert releases.presentation(plan)['can_update']
    assert git(root, 'rev-parse', 'HEAD') == old
    backup = root / '.git' / 'old.zip'
    smoke = Mock()
    v.apply_update(root, plan, backup, 'python', api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == target and smoke.call_count == 2
    assert sentinel.read_bytes() == b'never opened or reset'
    assert launcher.read_text(encoding='utf-8') == 'local launcher\n'
    with zipfile.ZipFile(backup) as archive:
        assert archive.read(PATHS[0]) == b'legacy_entry = 1\n'
        assert archive.read('__aplus_local__/push.cmd') == launcher_bytes
        assert not any(name.startswith('runtime/') for name in archive.namelist())


@pytest.mark.parametrize('edit', [
    lambda m: m['files'].append(dict(m['files'][0])),
    lambda m: m['files'][0].update(sha256='0' * 64),
    lambda m: m['files'][0].update(sha256='malformed'),
    lambda m: m['files'][0].update(path='../escape.py'),
    lambda m: m['files'][0].update(path='core/../escape.py'),
    lambda m: m['files'][0].update(path='core\\escape.py'),
    lambda m: m['files'][0].update(extra='unrecognized'),
    lambda m: m.update(files={}),
    lambda m: m.update(files=[]),
    lambda m: m.pop('files'),
    lambda m: m['files'].pop(),
    lambda m: m['files'].pop(0),
])
def test_malformed_or_mismatched_package_is_blocked_even_with_green_ci(approved_repo, edit):
    root, old, target = revise(approved_repo, lambda root: edit_manifest(root, edit))
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not plan['can_update'] and plan['manual_kind'] == 'release_integrity'
    view = releases.presentation(plan)
    assert view['title'] == '新版发布文件校验未通过'
    assert '人工安装' not in view['message'] and view['action_label'] == '等待新版修复'
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('path', [*PATHS, OTHER, 'core/new_strategy.py',
                                  'core/新策略.py', 'config/新配置.py'])
def test_main_reviewed_strategy_and_config_need_no_client_allowlist(approved_repo, path):
    def change(root):
        (root / path).write_bytes(b'arbitrary code = 99\n')
        def update(m):
            for entry in m['files']:
                if entry['path'] == path:
                    entry['sha256'] = checksum((root / path).read_bytes())
                    break
            else:
                m['files'].append({'path': path, 'sha256': checksum((root / path).read_bytes())})
        edit_manifest(root, update)
    root, old, target = revise(approved_repo, change)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and plan['ci_passed']
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('edit', [
    lambda m: m.pop('approved_strategy_updates'),
    lambda m: m['approved_strategy_updates'][0].update(revision='future version'),
    lambda m: m['approved_strategy_updates'][0].update(reason='reviewed on main'),
    lambda m: m['approved_strategy_updates'].append({'strategy': 'another reviewed strategy'}),
    lambda m: m.update(source='new reviewed baseline'),
    lambda m: m.update(commit='b' * 40),
    lambda m: m.update(new_release_metadata=True),
])
def test_reviewed_release_metadata_may_evolve(approved_repo, edit):
    root, old, target = revise(approved_repo, lambda root: edit_manifest(root, edit))
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and plan['ci_passed']
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('path', ['core/new_strategy.py', 'core/新策略.py',
                                  'config/new_settings.json', 'config/新配置.py',
                                  'core/nested/strategy.py'])
def test_unlisted_new_protected_file_is_blocked_even_with_green_ci(approved_repo, path):
    def change(root):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'unlisted protected bytes\n')
    root, old, target = revise(approved_repo, change)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not plan['can_update'] and plan['manual_kind'] == 'release_integrity'
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('operation', ['delete', 'rename'])
def test_protected_file_set_may_evolve_when_target_manifest_is_complete(approved_repo, operation):
    def change(root):
        original = root / PATHS[0]
        if operation == 'delete':
            original.unlink()
            edit_manifest(root, lambda m: m.update(
                files=[e for e in m['files'] if e['path'] != PATHS[0]]))
        else:
            renamed = 'core/strategies/新版策略.py'
            original.rename(root / renamed)
            def update(m):
                next(e for e in m['files'] if e['path'] == PATHS[0])['path'] = renamed
            edit_manifest(root, update)
    root, old, target = revise(approved_repo, change)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and plan['ci_passed']
    v.validate_plan(root, plan, api=api_for(target))
    assert git(root, 'rev-parse', 'HEAD') == old


def test_deleted_protected_file_still_listed_is_blocked(approved_repo):
    root, old, target = revise(approved_repo, lambda root: (root / PATHS[0]).unlink())
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not plan['can_update'] and plan['manual_kind'] == 'release_integrity'
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('mode', ['120000', '160000'])
def test_non_regular_protected_tree_entries_are_not_release_files(approved_repo, monkeypatch, mode):
    root, old, target = approved_repo
    original = v.run_git
    def altered(root, *args, **kwargs):
        result = original(root, *args, **kwargs)
        if args[0] == 'ls-tree':
            record, rest = result.split(b'\0', 1)
            metadata, path = record.split(b'\t', 1)
            _mode, _kind, oid = metadata.split()
            kind = 'commit' if mode == '160000' else 'blob'
            return mode.encode() + b' ' + kind.encode() + b' ' + oid + b'\t' + path + b'\0' + rest
        return result
    monkeypatch.setattr(v, 'run_git', altered)
    assert not v.release_manifest_valid(root, target)
    assert git(root, 'rev-parse', 'HEAD') == old


def test_multiple_skipped_releases_use_final_main_not_a_chain_of_local_permissions(approved_repo):
    def change(root):
        for version in (4, 5):
            (root / PATHS[0]).write_bytes(f'reviewed_version = {version}\n'.encode())
            edit_manifest(root, lambda m: m['files'][0].update(
                sha256=checksum((root / PATHS[0]).read_bytes())))
            git(root, 'add', '.')
            git(root, 'commit', '-m', f'intermediate reviewed version {version}')
        edit_manifest(root, lambda m: m.pop('approved_strategy_updates'))
    root, old, target = revise(approved_repo, change)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update']
    v.validate_plan(root, plan, api=api_for(target))
    assert git(root, 'rev-parse', 'HEAD') == old


def test_manifest_does_not_substitute_for_official_main_acceptance(approved_repo):
    root, old, target = approved_repo
    assert v.release_manifest_valid(root, target)
    unrelated = dict(id=99, head_sha=target, head_branch='feature', event='pull_request',
                     name='A acceptance', path='.github/workflows/checks.yml',
                     status='completed', conclusion='success')
    def api(path):
        return {'commit': {'sha': target}} if path == '/branches/main' else {'workflow_runs': [unrelated]}
    plan = v.check_version(root, force=True, api=api, fetch=Mock())
    assert not plan['can_update'] and plan['state'] == 'pending'
    assert git(root, 'rev-parse', 'HEAD') == old


def test_duplicate_json_keys_are_not_accepted(approved_repo):
    def change(root):
        path = root / 'frozen_manifest.json'
        raw = path.read_text(encoding='utf-8')
        path.write_text(raw[:-1] + ', "files": []}', encoding='utf-8')
    root, old, target = revise(approved_repo, change)
    assert not v.release_manifest_valid(root, target)


@pytest.mark.parametrize('case', ['partial', 'old-mismatch', 'target-mismatch'])
def test_only_target_integrity_not_old_manifest_or_transition_shape_is_required(approved_repo, case):
    root, old, target = approved_repo
    if case == 'partial':
        def change(root):
            blob = v.run_git(root, 'show', f'{old}:{PATHS[1]}', binary=True)
            (root / PATHS[1]).write_bytes(blob)
            edit_manifest(root, lambda m: m['files'][1].update(sha256=checksum(blob)))
        root, old, target = revise(approved_repo, change)
    elif case == 'target-mismatch':
        root, old, target = revise(approved_repo, lambda root: (root / OTHER).write_bytes(b'tampered\n'))
    else:
        # Use a different committed base with a false unchanged-file checksum.
        edit_manifest(root, lambda m: m['files'][2].update(sha256='0' * 64))
        git(root, 'add', '.'); git(root, 'commit', '-m', 'invalid base manifest')
        old = git(root, 'rev-parse', 'HEAD')
        for path in (*PATHS, 'frozen_manifest.json'):
            (root / path).write_bytes(v.run_git(root, 'show', f'{target}:{path}', binary=True))
        # Keep the same false unchanged-file checksum in both versions so this
        # exercises old Git byte verification, not only delta rejection.
        edit_manifest(root, lambda m: m['files'][2].update(sha256='0' * 64))
        git(root, 'add', '.'); git(root, 'commit', '-m', 'approved target after invalid base')
        target = git(root, 'rev-parse', 'HEAD')
    assert v.release_manifest_valid(root, target) == (case == 'partial')


@pytest.mark.parametrize('state', ['failed', 'running', 'missing', 'modified', 'new-main'])
def test_approval_never_bypasses_ci_local_edits_or_fresh_target_checks(approved_repo, state):
    root, old, target = approved_repo
    api = api_for(target, 'failure' if state == 'failed' else 'success')
    if state in {'running', 'missing'}:
        original = api
        def api(path):
            value = original(path)
            if path != '/branches/main':
                if state == 'missing': value['workflow_runs'] = []
                else: value['workflow_runs'][0]['status'] = 'in_progress'
            return value
    if state == 'modified': (root / PATHS[0]).write_bytes(b'local edits\n')
    plan = v.check_version(root, force=True, api=api, fetch=Mock())
    if state == 'new-main':
        assert plan['can_update']
        with pytest.raises(v.UpdateError, match='已经变化'):
            v.validate_plan(root, plan, api=api_for('f' * 40))
    else:
        assert not plan['can_update']
    assert git(root, 'rev-parse', 'HEAD') == old


def test_ci_can_be_revoked_after_an_approved_check(approved_repo):
    root, old, target = approved_repo
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    with pytest.raises(v.UpdateError, match='验收状态已经变化'):
        v.validate_plan(root, plan, api=api_for(target, 'failure'))
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('failed_probe', [1, 2])
def test_approved_upgrade_probe_failure_stops_or_rolls_back_code_without_touching_data(approved_repo, failed_probe):
    root, old, target = approved_repo
    runtime = root / 'runtime'; runtime.mkdir()
    sentinel = runtime / 'personal.db'; sentinel.write_bytes(b'original user data')
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    smoke = Mock(side_effect=[v.UpdateError('probe failed')] if failed_probe == 1
                 else [None, v.UpdateError('probe failed')])
    with pytest.raises(v.UpdateError):
        v.apply_update(root, plan, root / '.git' / 'old.zip', 'python',
                       api=api_for(target), smoke=smoke)
    assert git(root, 'rev-parse', 'HEAD') == old
    assert (root / PATHS[0]).read_bytes() == b'legacy_entry = 1\n'
    assert sentinel.read_bytes() == b'original user data'


@pytest.mark.parametrize('path,kind', [('requirements.txt', 'dependencies'),
                                      ('workbench/store.py', 'data')])
def test_approved_strategy_upgrade_does_not_waive_dependency_or_database_protection(approved_repo, path, kind):
    def change(root):
        dest = root / path; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text('unproven replacement', encoding='utf-8')
    root, old, target = revise(approved_repo, change)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert not plan['can_update'] and plan['manual_kind'] == kind
    assert git(root, 'rev-parse', 'HEAD') == old


@pytest.mark.parametrize('kind,phrase', [('dependencies', '依赖'), ('data', '数据保存')])
def test_remaining_manual_reason_is_specific_but_never_raw_technical_details(kind, phrase):
    view = releases.presentation(dict(state='available', can_update=False, block_reason='manual',
                                     manual_kind=kind, message='secret local path or arbitrary exception'))
    assert phrase in view['message'] and '反复检查不会解锁' in view['message']
    assert 'secret' not in view['message'] and not view['can_update']


def test_production_manifest_is_valid_without_hardcoded_strategy_permissions():
    root = Path(__file__).resolve().parents[1]
    assert v.release_manifest_valid(root, git(root, 'rev-parse', 'HEAD'))
    assert not hasattr(v, 'APPROVED_FROZEN_TRANSITIONS')
