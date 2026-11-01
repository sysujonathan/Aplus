"""CI scope boundary tests, including real Git rename/history evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools import ci_test_plan as ci

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path', [
    'core/strategies/mtr_strategy.py', 'config/settings.py', 'frozen_manifest.json',
    'workbench/store.py', 'workbench/installed_update.py', 'workbench/installed_data.py',
    'workbench/market.py', 'gui/chart_panel.py', 'requirements-lock.txt', 'conftest.py',
    'tests/conftest.py', 'tests/native_process.py', 'packaging/Launcher.cs',
    'tools/publish_installer.py', '.github/workflows/installed-release.yml',
    'docs/prototypes/tv-workspace/prototype.js', 'AGENTS.md', 'unknown.py',
])
def test_unknown_high_risk_or_mixed_change_cannot_use_fast_lane(path):
    assert ci.classify([('M', 'README.md'), ('M', path)], None, None)[0] == 'full'


@pytest.mark.parametrize('changes,lane', [
    ([('M', 'README.md'), ('A', 'docs/图标.md')], 'docs'),
    ([('D', 'docs/旧说明.md')], 'docs'),
    ([('A', 'packaging/ShortcutIcons.iss'), ('M', 'README.md')], 'shortcut'),
    ([('M', 'tests/test_installer_shortcut_icons.py')], 'shortcut'),
    ([('M', '.github/workflows/checks.yml')], 'ci'),
    ([('A', 'tools/ci_test_plan.py'), ('A', 'tests/test_ci_test_plan.py')], 'ci'),
    ([('D', 'packaging/ShortcutIcons.iss')], 'full'),
    ([('D', 'tools/ci_test_plan.py')], 'full'),
    ([('T', 'docs/图标.md')], 'full'),
    ([('M', 'docs/../core/test.md')], 'full'),
    ([('M', 'docs\\test.md')], 'full'),
    ([], 'full'),
])
def test_reviewed_fast_surfaces_and_fail_closed_cases(changes, lane):
    assert ci.classify(changes, None, None)[0] == lane


def test_real_production_installer_diff_only_adds_reviewed_cosmetic_hook():
    after = (ROOT/'packaging/Aplus.iss').read_bytes()
    before = ci.installer_without_icon_hook(after).encode('utf-8')
    changes = [('M', 'packaging/Aplus.iss')]
    assert ci.classify(changes, lambda _: before, lambda _: after)[0] == 'shortcut'
    # A single additional safety change forces full coverage, even mixed with icons.
    for old, new in [(b'if not SafeTarget(', b'if False and not SafeTarget('),
                     (b'if (Code <> 0)', b'if (Code = 0)'),
                     (b'MoveFileEx(Pointer', b'MoveFileEx(OtherPointer')]:
        assert old in after
        assert ci.classify(changes, lambda _: before, lambda _: after.replace(old, new))[0] == 'full'
    def unavailable(_):
        raise OSError('Git object unavailable')
    assert ci.classify(changes, unavailable, lambda _: after)[0] == 'full'


@pytest.fixture
def repository(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.PIPE).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'CI fixture')
    git('config', 'user.email', 'ci-fixture@example.invalid')
    for name in ('README.md', 'core/frozen.py'):
        target=tmp_path/name;target.parent.mkdir(parents=True, exist_ok=True);target.write_text('base\n')
    git('add', '.');git('commit', '-m', 'base')
    return tmp_path, git, git('rev-parse', 'HEAD')


def event(base, head):
    return {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}


def test_real_git_pr_history_handles_base_drift_and_merged_checkout(repository):
    root, git, base = repository
    git('switch', '-c', 'topic')
    (root/'README.md').write_text('topic documentation\n')
    git('commit', '-am', 'docs');head=git('rev-parse', 'HEAD')
    git('switch', 'main')
    (root/'core/frozen.py').write_text('new main only\n')
    git('commit', '-am', 'base advanced');base=git('rev-parse', 'HEAD')
    git('merge', '--no-ff', 'topic', '-m', 'integration')
    assert ci.plan('pull_request', event(base, head), root)[0] == 'docs'
    # Main always tests everything, independent of which PR paths were low risk.
    assert ci.plan('push', event(base, head), root)[0] == 'full'


def test_real_git_rename_from_protected_code_to_docs_is_not_documentation(repository):
    root, git, base = repository
    (root/'docs').mkdir()
    git('mv', 'core/frozen.py', 'docs/looks-harmless.md')
    git('commit', '-am', 'rename');head=git('rev-parse', 'HEAD')
    assert ci.plan('pull_request', event(base, head), root)[0] == 'full'


@pytest.mark.parametrize('payload', [{}, None, event('no-sha', 'a'*40), event('0'*40, '1'*40)])
def test_bad_event_or_missing_objects_fall_back_to_full(repository, payload):
    root, _, _ = repository
    assert ci.plan('pull_request', payload, root)[0] == 'full'


@pytest.mark.parametrize('name', ['push', 'workflow_dispatch', '', 'pull_request_target'])
def test_every_non_pr_event_is_full_even_without_event_file(name):
    assert ci.plan(name, {})[0] == 'full'


def test_every_lane_runs_tests_and_main_keeps_original_preflight_and_complete_pytest():
    commands=ci.commands('full')
    assert commands == [[sys.executable, '-m', 'pytest', '-q', 'tests/test_legacy_update_bridge.py', '-x', '--durations=10'],
                        [sys.executable, '-m', 'pytest', '-q', '--durations=30']]
    for lane in ('docs', 'shortcut', 'ci'):
        command=ci.commands(lane)[0]
        assert set(ci.COMMON).issubset(command)
        assert all((ROOT/path).is_file() for path in command if path.startswith('tests/'))
    assert set(ci.INSTALL).issubset(ci.commands('shortcut')[0])
    assert set(ci.INSTALL+ci.CI_CONTRACTS).issubset(ci.commands('ci')[0])
    with pytest.raises(ValueError):ci.commands('invalid')


def test_real_cli_malformed_event_reports_full_not_a_green_empty_plan(tmp_path):
    payload=tmp_path/'event.json';payload.write_text('{invalid')
    output=tmp_path/'output';summary=tmp_path/'summary'
    import os
    environment=dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(payload),
                     GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
    result=subprocess.run([sys.executable, str(ROOT/'tools/ci_test_plan.py')],
                          env=environment, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['lane'] == 'full'
    assert output.read_text() == 'lane=full\n'
    assert 'tests/test_legacy_update_bridge.py' in summary.read_text()


def test_test_failure_propagates_and_later_commands_do_not_run(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['ci_test_plan', '--run'])
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    called=[]
    def fail(command, **kwargs):
        called.append(command)
        assert kwargs['check'] is True
        raise subprocess.CalledProcessError(7, command)
    monkeypatch.setattr(ci.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError) as error:ci.main()
    assert error.value.returncode == 7 and len(called) == 1


def test_workflow_required_name_triggers_and_actual_execution_are_not_skipped():
    workflow=yaml.load((ROOT/'.github/workflows/checks.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert workflow['name'] == 'A acceptance'
    assert workflow['on'] == {'pull_request': '', 'push': {'branches': ['main']}}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'test'}
    job=workflow['jobs']['test']
    assert 'if' not in job and 'continue-on-error' not in job
    assert job['runs-on'] == 'windows-latest'
    steps=job['steps']
    assert not any('continue-on-error' in step for step in steps)
    checkout=next(step for step in steps if step.get('uses') == 'actions/checkout@v4')
    assert checkout['with'] == {'fetch-depth': '0', 'persist-credentials': 'false'}
    scope=next(step for step in steps if step.get('id') == 'scope')
    assert scope['run'] == 'python tools/ci_test_plan.py' and 'if' not in scope
    runner=next(step for step in steps if step.get('name') == 'Run required acceptance tests')
    assert runner['run'] == 'python tools/ci_test_plan.py --run' and 'if' not in runner
    assert any('verify_frozen' in step.get('run', '') and 'if' not in step for step in steps)
    compiler=next(step for step in steps if 'APLUS_ISCC=' in step.get('run', ''))
    assert compiler['if'] == "steps.scope.outputs.lane != 'docs'"
    assert "Status -ne 'Valid'" in compiler['run'] and 'Pyrsys B.V.' in compiler['run']
    assert workflow['concurrency']['cancel-in-progress'] == "${{ github.event_name == 'pull_request' }}"
    assert 'github.run_id' in workflow['concurrency']['group']
