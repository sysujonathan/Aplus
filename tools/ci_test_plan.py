"""Conservative PR test selection. Every main push still runs the full suite.

No labels, PR descriptions or caller-supplied test paths control admission.
Unknown changes and unreadable Git/event evidence fall back to full coverage.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
POLICY = {'.github/workflows/checks.yml', 'tools/ci_test_plan.py', 'tests/test_ci_test_plan.py'}
SHORTCUT = {'packaging/ShortcutIcons.iss', 'tests/test_installer_shortcut_icons.py'}
COMMON = ('tests/test_ci_test_plan.py', 'tests/test_release_versions.py')
INSTALL = ('tests/test_installer_shortcut_icons.py', 'tests/test_brand_runtime_icons.py',
           'tests/test_installed_distribution.py', 'tests/test_installed_release_policy.py',
           'tests/test_auto_installed_release.py', 'tests/test_update_review_safety.py')
CI_CONTRACTS = ('tests/test_legacy_update_bridge.py', 'tests/test_version_update.py',
                'tests/test_installed_schema_bridge.py')
# Exact reviewed surfaces, not directory or test-name prefixes. Authentication,
# schema, shared chart transport, strategy/data and release changes stay full.
MODULE_TESTS = {
    'help': ('tests/test_changelog_layout.py', 'tests/test_update_notice_help.py'),
    'intraday': ('tests/test_intraday_drawings.py', 'tests/test_chart_exit_recovery.py',
                 'tests/test_chart_switch_transport.py', 'tests/test_tv_workspace_prototype.py'),
    'pa': (
        'tests/test_pa_growth_store.py', 'tests/test_pa_growth_bridge.py',
        'tests/test_pa_growth_lifecycle.py', 'tests/test_pa_growth_distribution.py',
        'tests/test_pa_growth_gui.py', 'tests/test_pa_growth_comparison.py',
        'tests/test_pa_growth_storage_review.py', 'tests/test_pa_growth_bridge_review.py',
        'tests/test_pa_growth_native_review.py', 'tests/test_pa_growth_progressive.py',
        'tests/test_pa_growth_progressive_gui.py', 'tests/test_pa_backup_optional_setup.py',
        'tests/test_pa_review_finish.py', 'tests/test_pa_oauth_bundle.py',
        'tests/test_pa_terms.py', 'tests/test_pa_shared.py', 'tests/test_pa_drive_transport.py',
        'tests/test_pa_session_boundaries.py', 'tests/test_pa_permission_boundaries.py',
        'tests/test_pa_security_ui.py', 'tests/test_pa_random.py', 'tests/test_pa_ime.py',
        'tests/test_pa_shared_gui.py', 'tests/test_pa_navigation_race.py',
        'tests/test_desktop_layout.py', 'tests/test_chart_exit_recovery.py',
        'tests/test_chart_switch_transport.py'),
    # Reviewed pending PR110 test: selected only when that exact file changes.
    # Keeping it separate avoids requiring an unmerged test for current PA PRs.
    'pa-collaboration': ('tests/test_pa_collaboration.py',),
}
MODULE_PATHS = {
    'help': {'gui/usage_help.py'},
    # Shared production chart JS affects more than minute drawing geometry and
    # intentionally remains full; this first mapping covers the test/probe seam.
    'intraday': {'tests/intraday_drawing_probe.py', 'tests/chart_intraday_drawings.cjs'},
    'pa': {
        'gui/growth_bridge.py', 'gui/growth_space.py', 'gui/growth_shell.py',
        'gui/pa_growth/index.html', 'gui/pa_growth/growth.js', 'gui/pa_growth/growth.css',
        'workbench/pa_growth.py', 'workbench/pa_trial.py', 'workbench/pa_terms.py',
        'workbench/pa_random.py', 'workbench/pa_shared.py',
        'tests/pa_growth_webview_probe.py', 'tests/pa_growth_comparison_probe.py',
        'tests/pa_growth_progressive_probe.py', 'tests/pa_shared_native_probe.py',
        'tests/pa_security_native_probe.py', 'tests/pa_growth_ui_regression.cjs',
        'tests/pa_growth_scope_regression.cjs', 'tests/pa_security_regression.cjs',
        'tests/pa_ime_regression.cjs', 'tests/pa_navigation_regression.cjs',
    },
    'pa-collaboration': {'tests/pa_collaboration_ui.cjs'},
}
for module, tests in MODULE_TESTS.items():
    # Adjacent contracts need not grant admission to the module's fast lane.
    MODULE_PATHS[module].update(p for p in tests if
        (module == 'help' and p == 'tests/test_changelog_layout.py') or
        (module == 'intraday' and p == 'tests/test_intraday_drawings.py') or
        (module.startswith('pa') and PurePosixPath(p).name.startswith('test_pa_')))


ICON_INCLUDE = '#include "ShortcutIcons.iss"\n\n'
ICON_HOOK = """    Refreshed := RefreshExistingShortcutIcons(ExpandConstant('{userdesktop}'),
      ExpandConstant('{app}\\Aplus.exe'),
      ExpandConstant('{app}\\app\\{#AppVersion}\\_internal\\icon_aplus.ico'));
    Log('APLUS existing desktop shortcut icons refreshed: '+IntToStr(Refreshed));
"""


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)


def installer_without_icon_hook(content):
    text = content.decode('utf-8').replace('\r\n', '\n')
    # Strip only this reviewed cosmetic extension, never general installer code.
    return text.replace(ICON_INCLUDE, '').replace(ICON_HOOK, '').replace(
        'var Code, Refreshed: Integer;', 'var Code: Integer;')


def documentation(path):
    name = PurePosixPath(path)
    return (path in {'README.md', 'AGENTS.md'} or
            (name.parts[0] == 'docs' and name.suffix == '.md') or
            (path.startswith('docs/images/') and name.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}))


def classify(changes, read_before, read_after):
    """Changes are (Git status, path); renames must be expanded to delete/add."""
    kinds = set()
    if not changes:
        return 'full', 'No provable changed-file set'
    for status, path in changes:
        if (not path or '\\' in path or '..' in PurePosixPath(path).parts or
                PurePosixPath(path).is_absolute() or str(PurePosixPath(path)) != path):
            return 'full', 'Invalid changed path'
        if documentation(path) and status in {'A', 'M', 'D'}:
            continue
        if status not in {'A', 'M'}:
            return 'full', 'Non-document deletion or type/rename change: ' + path
        if path in POLICY:
            kinds.add('ci')
        elif path in SHORTCUT:
            kinds.add('shortcut')
        elif any(path in paths for paths in MODULE_PATHS.values()):
            kinds.update(module for module, paths in MODULE_PATHS.items() if path in paths)
            if 'pa-collaboration' in kinds:
                kinds.add('pa')
        elif path == 'packaging/Aplus.iss':
            try:
                if installer_without_icon_hook(read_before(path)) != installer_without_icon_hook(read_after(path)):
                    return 'full', 'Installer changed beyond the reviewed icon hook'
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
                return 'full', 'Installer comparison unavailable'
            kinds.add('shortcut')
        else:
            return 'full', 'Unmapped or high-risk path: ' + path
    lane = '+'.join(sorted(kinds)) if kinds else 'docs'
    return lane, ('Reviewed scopes: ' + lane + '; changed paths: ' +
                  ', '.join(path for _, path in changes))


def plan(event_name, event, root=ROOT):
    if event_name != 'pull_request':
        return 'full', 'Main push and all other events require full coverage'
    try:
        pr = event['pull_request']
        base, head = pr['base']['sha'], pr['head']['sha']
        if not all(isinstance(x, str) and re.fullmatch('[0-9a-f]{40}', x) for x in (base, head)):
            raise ValueError('Invalid commit identity')
        # Tests run against the checkout's integration snapshot, not an unrelated ref.
        for ref in (base, head):
            subprocess.run(['git', 'merge-base', '--is-ancestor', ref, 'HEAD'], cwd=root,
                           check=True, capture_output=True)
        ancestor = git(root, 'merge-base', base, head).decode('ascii').strip()
        fields = git(root, 'diff', '--name-status', '--no-renames', '-z', ancestor, head).decode('utf-8').split('\0')
        if fields[-1] != '' or (len(fields)-1) % 2:
            raise ValueError('Invalid Git path evidence')
        changes = list(zip(fields[:-1:2], fields[1:-1:2]))
        return classify(changes, lambda p: git(root, 'show', ancestor+':'+p),
                        lambda p: git(root, 'show', head+':'+p))
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, subprocess.SubprocessError):
        return 'full', 'Cannot prove PR scope from event and complete Git history'


def commands(lane):
    if lane == 'full':
        return [[sys.executable, '-m', 'pytest', '-q', 'tests/test_legacy_update_bridge.py', '-x', '--durations=10'],
                [sys.executable, '-m', 'pytest', '-q',
                 '--ignore=tests/test_legacy_update_bridge.py', '--durations=30']]
    kinds = set(lane.split('+'))
    if (not kinds <= {'docs', 'shortcut', 'ci', *MODULE_TESTS} or
            ('docs' in kinds and len(kinds) != 1) or
            ('pa-collaboration' in kinds and 'pa' not in kinds)):
        raise ValueError('Unknown CI lane; refusing an empty check')
    tests = list(COMMON)
    if kinds & {'shortcut', 'ci'}:
        tests.extend(INSTALL)
    if 'ci' in kinds:
        tests.extend(CI_CONTRACTS)
    for module in sorted(kinds & MODULE_TESTS.keys()):
        tests.extend(MODULE_TESTS[module])
    # Stable union: a shared contract runs once, even for mixed module PRs.
    selected = [[sys.executable, '-m', 'pytest', '-q', *dict.fromkeys(tests), '--durations=10']]
    if kinds & {'pa', 'intraday'}:
        # Existing wrappers run production Node regressions. Require Node before
        # pytest so a missing runtime cannot turn those checks into green skips.
        selected.insert(0, ['node', '--version'])
    if 'pa' in kinds:
        # This account/local-scope regression has no pytest wrapper.
        selected.append(['node', 'tests/pa_growth_scope_regression.cjs'])
    return selected


def needs_inno(lane):
    selected = commands(lane)
    return lane == 'full' or any('tests/test_installer_shortcut_icons.py' in c for c in selected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    event_name = os.environ.get('GITHUB_EVENT_NAME', '')
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8')) if event_name == 'pull_request' else {}
    except (KeyError, OSError, ValueError):
        event = {}
    lane, reason = plan(event_name, event)
    selected = commands(lane)  # Validated before output; no arbitrary shell/test arguments.
    print(json.dumps(dict(lane=lane, reason=reason, commands=selected), ensure_ascii=True), flush=True)
    if args.run:
        for command in selected:
            subprocess.run(command, cwd=ROOT, check=True)
    else:
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write('lane='+lane+'\nneeds_inno='+str(needs_inno(lane)).lower()+'\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write('### Acceptance scope\n\n```json\n'+json.dumps(
                    dict(lane=lane, reason=reason, commands=selected), ensure_ascii=True, indent=2)+'\n```\n')


if __name__ == '__main__':
    main()
