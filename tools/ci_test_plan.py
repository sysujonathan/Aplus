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
    return path == 'README.md' or (name.parts[0] == 'docs' and name.suffix == '.md')


def classify(changes, read_before, read_after):
    """Changes are (Git status, path); renames must be expanded to delete/add."""
    kinds = set()
    if not changes:
        return 'full', 'No provable changed-file set'
    for status, path in changes:
        if not path or '\\' in path or '..' in PurePosixPath(path).parts:
            return 'full', 'Invalid changed path'
        if documentation(path) and status in {'A', 'M', 'D'}:
            continue
        if status not in {'A', 'M'}:
            return 'full', 'Non-document deletion or type/rename change: ' + path
        if path in POLICY:
            kinds.add('ci')
        elif path in SHORTCUT:
            kinds.add('shortcut')
        elif path == 'packaging/Aplus.iss':
            try:
                if installer_without_icon_hook(read_before(path)) != installer_without_icon_hook(read_after(path)):
                    return 'full', 'Installer changed beyond the reviewed icon hook'
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
                return 'full', 'Installer comparison unavailable'
            kinds.add('shortcut')
        else:
            return 'full', 'Unmapped or high-risk path: ' + path
    if 'ci' in kinds:
        return 'ci', 'CI policy contracts plus all shortcut and release-admission contracts'
    if 'shortcut' in kinds:
        return 'shortcut', 'Isolated desktop icon change plus installation/release contracts'
    return 'docs', 'Markdown documentation only'


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
                [sys.executable, '-m', 'pytest', '-q', '--durations=30']]
    if lane not in {'docs', 'shortcut', 'ci'}:
        raise ValueError('Unknown CI lane; refusing an empty check')
    tests = COMMON + (INSTALL if lane != 'docs' else ()) + (CI_CONTRACTS if lane == 'ci' else ())
    return [[sys.executable, '-m', 'pytest', '-q', *tests, '--durations=10']]


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
                output.write('lane='+lane+'\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write('### Acceptance scope\n\n```json\n'+json.dumps(
                    dict(lane=lane, reason=reason, commands=selected), ensure_ascii=True, indent=2)+'\n```\n')


if __name__ == '__main__':
    main()
