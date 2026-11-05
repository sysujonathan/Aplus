import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows Edge WebView')
def test_shared_cases_and_private_learning_in_real_webview():
    root = Path(__file__).resolve().parents[1]
    folder = root / '.test-pa-growth' / ('shared-' + uuid.uuid4().hex[:8])
    (folder / 'temp').mkdir(parents=True)
    env = dict(os.environ, TEMP=str(folder / 'temp'), TMP=str(folder / 'temp'), PYTHONUTF8='1',
               A_WORKBENCH_HOME=str(folder / 'a'))
    result = subprocess.run([sys.executable, str(root / 'tests/pa_shared_native_probe.py'), str(folder)],
        cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr + ((folder / 'failure.txt').read_text(encoding='utf-8') if (folder / 'failure.txt').exists() else '')
    receipt = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    assert receipt['ok'] and receipt['real_webview'] and receipt['synthetic']
    assert not receipt['real_google'] and not receipt['real_ime_keyboard']
    assert len(receipt['checks']) == 7


@pytest.mark.skipif(os.name != 'nt', reason='Windows Edge WebView')
def test_external_account_and_permission_changes_clear_both_real_webviews():
    root = Path(__file__).resolve().parents[1]
    folder = root / '.test-pa-growth' / ('security-' + uuid.uuid4().hex[:8])
    (folder / 'temp').mkdir(parents=True)
    env = dict(os.environ, TEMP=str(folder / 'temp'), TMP=str(folder / 'temp'), PYTHONUTF8='1',
               A_WORKBENCH_HOME=str(folder / 'runtime'))
    result = subprocess.run([sys.executable, str(root / 'tests/pa_security_native_probe.py'), str(folder)],
        cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr + ((folder / 'failure.txt').read_text(encoding='utf-8') if (folder / 'failure.txt').exists() else '')
    receipt = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    assert receipt['ok'] and receipt['real_webviews'] == 2 and receipt['real_dpapi']
    assert not receipt['real_google'] and len(receipt['checks']) == 3
