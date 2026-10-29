"""Actual isolated interpreters; no installation into the developer environment."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from workbench import version_update as v
from tests.test_version_update import repo, git, api_for, installation_contract


@pytest.fixture(scope='module')
def legacy_python(tmp_path_factory):
    folder = tmp_path_factory.mktemp('legacy-env')/'venv'
    subprocess.run([sys.executable, '-I', '-m', 'venv', str(folder)], check=True,
                   capture_output=True, timeout=180)
    return v._environment_python(folder)


def reuse_inputs(root, tmp_path, lock=b''):
    stage = tmp_path/'stage'; stage.mkdir()
    (stage/'requirements-lock.txt').write_bytes(lock)
    contract = {**installation_contract(lock), 'environment_changed': False}
    return stage, {'installation': contract}


def test_unmarked_legacy_environment_is_adopted_only_after_checks_and_rechecked(
        repo, tmp_path, legacy_python, monkeypatch):
    root, _, _ = repo
    stage, plan = reuse_inputs(root, tmp_path)
    verify = Mock(wraps=v._verify_python_environment)
    monkeypatch.setattr(v, '_verify_python_environment', verify)
    smoke = Mock()
    for _ in range(2):
        python, created = v.prepare_target_environment(root, stage, plan, legacy_python, smoke=smoke)
        assert python == legacy_python.resolve() and created is None
    assert verify.call_count == 2 and smoke.call_count == 2
    records = list((v.metadata_dir(root)/'adopted-environments').glob('*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding='utf-8'))['executable'] == str(python)
    assert not v._environment_marker(legacy_python.parent.parent).exists()


@pytest.mark.parametrize('fault', ['package', 'python', 'pip-check', 'startup'])
def test_unchanged_environment_drift_or_failed_probe_is_not_adopted(
        repo, tmp_path, legacy_python, monkeypatch, fault):
    root, _, _ = repo
    stage, plan = reuse_inputs(root, tmp_path, b'pip==0.0\n' if fault == 'package' else b'')
    if fault == 'python':
        plan['installation']['python'] = '3.99'
    if fault == 'pip-check':
        original = v._run_checked
        def checked(command, **kwargs):
            if list(map(str, command[-2:])) == ['pip', 'check']:
                raise v.UpdateError('synthetic pip check failure')
            return original(command, **kwargs)
        monkeypatch.setattr(v, '_run_checked', checked)
    smoke = Mock(side_effect=v.UpdateError('startup failed') if fault == 'startup' else None)
    pointer = v.environment_pointer(root); pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(legacy_python), encoding='utf-8'); before = pointer.read_bytes()
    with pytest.raises(v.UpdateError):
        v.prepare_target_environment(root, stage, plan, legacy_python, smoke=smoke)
    if fault != 'startup':
        smoke.assert_not_called()  # A passing startup probe cannot waive dependency checks.
    assert not (pointer.parent/'adopted-environments').exists()
    assert pointer.read_bytes() == before and legacy_python.is_file()


def test_managed_unchanged_environment_requires_marker(repo, tmp_path):
    root, _, _ = repo
    stage, plan = reuse_inputs(root, tmp_path)
    folder = v.metadata_dir(root)/'environments'/plan['installation']['environment_id']
    python = v._environment_python(folder); python.parent.mkdir(parents=True)
    python.touch()
    smoke = Mock()
    with pytest.raises(v.UpdateError, match='验收标记'):
        v.prepare_target_environment(root, stage, plan, python, smoke=smoke)
    smoke.assert_not_called()


def test_first_manifest_without_dependency_diff_still_rejects_drift(repo, tmp_path, legacy_python):
    root, _, _ = repo
    lock = b'pip==0.0\n'
    (root/'requirements-lock.txt').write_bytes(lock)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'legacy lock without manifest')
    git(root, 'switch', '-c', 'first-manifest')
    v.write_json(root/v.RELEASE_INSTALLATION, installation_contract(lock))
    git(root, 'add', '.'); git(root, 'commit', '-m', 'first release manifest')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', target)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and not plan['installation']['environment_changed']
    smoke = Mock()
    with pytest.raises(v.UpdateError, match='依赖版本'):
        v.apply_update(root, plan, tmp_path/'backup.zip', legacy_python,
                       api=api_for(target), smoke=smoke)
    smoke.assert_not_called()
    assert git(root, 'branch', '--show-current') == 'main'
    assert not (v.metadata_dir(root)/'adopted-environments').exists()


def test_hash_lock_cannot_be_removed_to_build_unhashed_environment(monkeypatch):
    source = installation_contract(b'', hashes=b'# trusted lock\n')
    target = installation_contract(b'')
    monkeypatch.setattr(v, 'release_installation', lambda root, revision, **kw:
                        source if revision == 'old' else target)
    with pytest.raises(v.ManualUpdateError, match='逐包哈希'):
        v.installation_plan('.', 'old', 'new', set())


def test_hash_lock_only_release_builds_new_fixed_environment_and_installs(repo, tmp_path):
    root, _, _ = repo
    lock = b''
    first = b'# trusted installation artifacts, revision one\n'
    second = b'# trusted installation artifacts, revision two\n'
    (root/'requirements-lock.txt').write_bytes(lock)
    (root/'requirements-hashes.txt').write_bytes(first)
    source = installation_contract(lock, hashes=first)
    v.write_json(root/v.RELEASE_INSTALLATION, source)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'hashed source release')
    git(root, 'switch', '-c', 'hash-lock-only')
    (root/'requirements-hashes.txt').write_bytes(second)
    target_contract = installation_contract(lock, hashes=second)
    v.write_json(root/v.RELEASE_INSTALLATION, target_contract)
    git(root, 'add', '.'); git(root, 'commit', '-m', 'only hash lock revision')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', target)
    old_python, old_folder = v.prepare_target_environment(
        root, root, {'installation': {**source, 'environment_changed': True}},
        sys.executable, smoke=Mock())
    marker_before = v._environment_marker(old_folder).read_bytes()
    v.write_environment_pointer(root, old_python)
    pointer_before = v.environment_pointer(root).read_bytes()
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert plan['can_update'] and plan['installation']['environment_changed']
    assert target_contract['environment_id'] != source['environment_id']
    result = v.apply_update(root, plan, tmp_path/'backup.zip', old_python,
                            api=api_for(target), smoke=Mock())
    new_python = Path(result['python'])
    assert new_python != old_python and new_python.is_file()
    assert new_python.parent.parent.name == target_contract['environment_id']
    assert v._verify_environment(new_python.parent.parent, root, plan['installation']) == new_python
    reused, created = v.prepare_target_environment(
        root, root, {'installation': {**plan['installation'], 'environment_changed': False}},
        new_python, smoke=Mock())
    assert reused == new_python and created is None
    assert v._environment_marker(old_folder).read_bytes() == marker_before
    assert v.environment_pointer(root).read_bytes() == pointer_before
    assert git(root, 'rev-parse', 'HEAD') == target
