import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workbench import acceleration_profile as profile
from workbench.store import Store
from workbench.strategies import catalog, prepare_indicators, verify_frozen
from tests.test_workbench import frame
from tests.test_gap_family_fast_reject import reject_frame


@pytest.fixture
def local_profile(tmp_path, monkeypatch):
    # Configuration activation changes process environment only; never inherit a
    # trader's real local authorization, and restore all keys after this test.
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    for _strategy, prefix in profile.STRATEGIES.values():
        for suffix in ('_MODE', '_APPROVED_BINDING', '_CANARY_RATE'):
            os.environ.pop(prefix + suffix, None)
    path = tmp_path/'local-approval.json'
    os.environ[profile.PROFILE_ENV] = str(path)
    return path


def test_no_approval_is_manufactured_by_normal_startup(local_profile, tmp_path):
    report = profile.apply_profile()
    assert not report['loaded'] and not local_profile.exists()
    assert all(row['effective'] == 'OFF' for row in
               profile.effective_status(tmp_path, report)['strategies'].values())
    with pytest.raises(ValueError, match='明确人工授权'):
        profile.approve(path=local_profile, root=tmp_path)
    assert not local_profile.exists()


def test_unreadable_profile_directory_falls_back_without_breaking_startup(
        local_profile, tmp_path, monkeypatch):
    original_exists = Path.exists
    def exists(path):
        if path == local_profile:
            raise PermissionError('profile directory unavailable')
        return original_exists(path)
    monkeypatch.setattr(Path, 'exists', exists)
    report = profile.apply_profile()
    assert not report['loaded'] and 'profile directory unavailable' in report['reason']
    assert all(row['effective'] == 'OFF' for row in
               profile.effective_status(tmp_path, report)['strategies'].values())


def test_explicit_approval_reused_by_real_desktop_backend(local_profile, tmp_path):
    from launch_dashboard import build_backend
    original = verify_frozen()
    profile.approve(confirmed=True, root=tmp_path)
    service, store = build_backend()
    assert service is not None
    try:
        rows = service.acceleration_status['strategies']
        assert len(rows) == 5
        assert all(row['effective'] == 'ACCELERATED' for row in rows.values())
        assert all(row['original_audit_rate'] == .05 for row in rows.values())
        assert 'STRATEGY_GAP_H2' not in profile.STRATEGIES
        assert verify_frozen() == original
        assert not store.rows('SELECT * FROM observations')
        assert not store.rows('SELECT * FROM plans')
    finally:
        service.pool.shutdown()


@pytest.mark.parametrize('python_name', ['python.exe', 'pythonw.exe'])
def test_clean_process_restart_reuses_file_without_five_environment_sets(
        local_profile, tmp_path, python_name):
    profile.approve(confirmed=True, root=tmp_path)
    environment = dict(os.environ)
    for _strategy, prefix in profile.STRATEGIES.values():
        for suffix in ('_MODE', '_APPROVED_BINDING', '_CANARY_RATE'):
            environment.pop(prefix + suffix, None)
    output = tmp_path/'started.json'
    script = (
        'import json,sys; from pathlib import Path; from launch_dashboard import build_backend; '
        's,db=build_backend(); assert s is not None; '
        'Path(sys.argv[1]).write_text(json.dumps(s.acceleration_status)); s.pool.shutdown()'
    )
    executable = Path(sys.executable).with_name(python_name)
    result = subprocess.run([str(executable), '-c', script, str(output)], cwd=profile.ROOT,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(output.read_text())
    assert all(row['effective'] == 'ACCELERATED' for row in status['strategies'].values())


@pytest.mark.parametrize('mutation', ['startup_binding', 'runtime', 'json', 'audit_rate'])
def test_invalid_global_authorization_fails_closed(local_profile, tmp_path, mutation):
    profile.approve(confirmed=True, root=tmp_path)
    value = json.loads(local_profile.read_text(encoding='utf-8'))
    if mutation == 'json':
        local_profile.write_text('{bad', encoding='utf-8')
    else:
        key = 'original_audit_rate' if mutation == 'audit_rate' else mutation
        value[key] = 'changed'
        local_profile.write_text(json.dumps(value), encoding='utf-8')
    report = profile.apply_profile()
    assert not report['loaded'] and '未生效' in report['reason']
    assert all(row['effective'] == 'OFF' for row in
               profile.effective_status(tmp_path, report)['strategies'].values())


def test_one_stale_binding_and_one_off_do_not_disable_other_strategies(local_profile, tmp_path):
    profile.approve(confirmed=True, root=tmp_path)
    value = json.loads(local_profile.read_text(encoding='utf-8'))
    value['strategies']['3K']['binding'] = '0' * 64
    local_profile.write_text(json.dumps(value), encoding='utf-8')
    profile.set_off('AWIL')
    report = profile.apply_profile()
    rows = profile.effective_status(tmp_path, report)['strategies']
    assert rows['3K']['effective'] == rows['AWIL']['effective'] == 'OFF'
    assert '人工授权' in rows['3K']['reason']
    assert 'OFF' in rows['AWIL']['reason']
    for name in ('MTR', 'Gap Pinbar', 'Structural Gap'):
        assert rows[name]['effective'] == 'ACCELERATED'


def test_explicit_environment_off_always_wins(local_profile, tmp_path):
    profile.approve(confirmed=True, root=tmp_path)
    os.environ['APLUS_MTR_FAST_REJECT_MODE'] = 'OFF'
    profile.apply_profile()
    rows = profile.effective_status(tmp_path)['strategies']
    assert rows['MTR']['effective'] == 'OFF'
    assert all(row['effective'] == 'ACCELERATED' for name, row in rows.items() if name != 'MTR')


@pytest.mark.parametrize('bad_entry', ['corrupt', {'mode': []}])
def test_one_corrupt_entry_is_off_without_partially_loading_global_state(
        local_profile, tmp_path, bad_entry):
    profile.approve(confirmed=True, root=tmp_path)
    value = json.loads(local_profile.read_text(encoding='utf-8'))
    value['strategies']['AWIL'] = bad_entry
    local_profile.write_text(json.dumps(value), encoding='utf-8')
    report = profile.apply_profile()
    assert report['loaded']
    rows = profile.effective_status(tmp_path, report)['strategies']
    assert rows['AWIL']['effective'] == 'OFF'
    assert all(row['effective'] == 'ACCELERATED' for name, row in rows.items() if name != 'AWIL')


def test_unknown_environment_cannot_be_approved(local_profile, tmp_path, monkeypatch):
    monkeypatch.setattr(profile.mtr, '_runtime', lambda: ('Unknown', '0', '0', '0'))
    with pytest.raises(ValueError, match='未写入授权'):
        profile.approve(confirmed=True, root=tmp_path)
    assert not local_profile.exists()


def test_local_off_beats_inherited_accelerated_and_default_audit_is_kept(local_profile, tmp_path):
    profile.approve(confirmed=True, root=tmp_path)
    profile.set_off('MTR')
    os.environ['APLUS_MTR_FAST_REJECT_MODE'] = 'ACCELERATED'
    os.environ['APLUS_THREE_K_FAST_REJECT_CANARY_RATE'] = '0'
    report = profile.apply_profile()
    rows = profile.effective_status(tmp_path, report)['strategies']
    assert rows['MTR']['effective'] == 'OFF'
    assert rows['3K']['effective'] == 'ACCELERATED'
    assert rows['3K']['original_audit_rate'] == .05


@pytest.mark.parametrize('name', tuple(profile.STRATEGIES))
def test_five_percent_original_audit_and_independent_fuse(local_profile, tmp_path, frame, name):
    store = Store(tmp_path/'isolated')
    profile.approve(confirmed=True, root=store.root)
    profile.apply_profile()
    spec_id, _prefix = profile.STRATEGIES[name]
    fast = profile._controller(name, store, catalog(store)[spec_id])
    assert fast.mode == 'ACCELERATED' and fast.rate == .05
    samples = [f'stock-{i}' for i in range(4096)]
    selected = [key for key in samples if fast._sampled(key)]
    assert .035 < len(selected) / len(samples) < .065
    original_key = selected[0]
    fast_key = next(key for key in samples if not fast._sampled(key))
    raw = reject_frame(frame)
    prepared = prepare_indicators(raw)
    calls = []
    fast.run(lambda: calls.append(True), raw, prepared, original_key)
    assert calls == [True]
    fast.run(lambda: pytest.fail('未抽中原版复核的安全拒绝样本不应调用原版'),
             raw, prepared, fast_key)
    fast.run(lambda: {'real': 'business-difference'}, raw, prepared, original_key)
    assert fast.fused and fast.mode == 'OFF'
    rows = profile.effective_status(store.root)['strategies']
    assert rows[name]['effective'] == 'OFF' and '熔断' in rows[name]['reason']
    assert all(row['effective'] == 'ACCELERATED' for key, row in rows.items() if key != name)
    assert store.rows('SELECT * FROM events')
    assert not store.rows('SELECT * FROM observations')


def test_status_is_read_only_and_preserves_database_and_fuse(local_profile, tmp_path):
    store = Store(tmp_path/'isolated')
    profile.approve(confirmed=True, root=store.root)
    profile.apply_profile()
    before = store.path.read_bytes()
    result = profile.effective_status(store.root)
    assert all(row['effective'] == 'ACCELERATED' for row in result['strategies'].values())
    assert store.path.read_bytes() == before


def test_command_status_reports_real_modes_in_utf8(local_profile, tmp_path):
    profile.approve(confirmed=True, root=tmp_path)
    result = subprocess.run(
        [sys.executable, str(profile.ROOT/'tools/acceleration_settings.py'), 'status'],
        cwd=profile.ROOT, env=dict(os.environ), capture_output=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('"effective": "ACCELERATED"') == 5
    assert '本机配置' in result.stdout and 'GAP H2：保持原版' in result.stdout
