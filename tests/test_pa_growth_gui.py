"""Production PA UI checks; every writable path belongs to an isolated test home."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_pa_growth_browser_acknowledgements_and_private_drafts():
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript 行为回归需要 Node.js（CI Windows 镜像提供）')
    result = subprocess.run(
        [node, str(ROOT / 'tests' / 'pa_growth_ui_regression.cjs')],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = json.loads(result.stdout)
    assert verdict['ok'] is True
    assert 'durable-acknowledgement' in verdict['checks']
    assert 'duplicate-confirmation' in verdict['checks']
    assert 'draft-scope' in verdict['checks']
    assert 'selected-original-only' in verdict['checks']
    assert 'latest-image-race' in verdict['checks']
    assert 'recent-outside-first-page' in verdict['checks']


@pytest.mark.skipif(sys.platform != 'win32', reason='正式桌面 UI 使用 Windows Edge WebView')
def test_pa_growth_real_webview_store_capture_search_and_reopen():
    if importlib.util.find_spec('webview') is None:
        pytest.skip('真实桌面 UI 验收需要已安装 pywebview')
    base = (ROOT / '.test-pa-growth').resolve()
    folder = base / ('gui-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    temporary = folder / 'temp'
    temporary.mkdir()
    environment = {
        **os.environ,
        'TEMP': str(temporary),
        'TMP': str(temporary),
        'A_WORKBENCH_HOME': str(folder / 'runtime'),
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUTF8': '1',
    }
    def run_probe(*options):
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tests' / 'pa_growth_webview_probe.py'), str(folder), *options],
                cwd=ROOT, env=environment, capture_output=True, text=True,
                encoding='utf-8', timeout=90,
            )
        except subprocess.TimeoutExpired as error:
            pytest.fail(f'真实成长空间 UI 验收超时；隔离回执目录：{folder}\n{error}')
        failure = folder / 'failure.txt'
        detail = failure.read_text(encoding='utf-8') if failure.exists() else ''
        assert result.returncode == 0, result.stdout + result.stderr + detail

    run_probe()
    receipt = folder / 'result.json'
    assert receipt.is_file(), f'未生成真实 UI 验收回执：{folder}'
    verdict = json.loads(receipt.read_text(encoding='utf-8'))
    assert verdict['ok'] is True
    assert verdict['real_webview'] is True
    assert verdict['real_store'] is True
    assert verdict['synthetic'] is True
    assert {
        'paste_event_original_image',
        'failure_keeps_draft',
        'durable_save_ack',
        'batch_drop_independent_cases',
        'duplicate_warning_no_merge',
        'three_keyword_partial_search',
        'no_result_feedback',
        'original_exact_bytes_zoom_back',
        'append_only_research_note',
        'minimal_judgment_then_evidence_no_score',
        'knowledge_case_link_and_evidence_growth',
        'fresh_store_reads_persisted_records',
        'selected_original_only_and_cached_fullscreen',
        'recent_case_outside_first_page',
    }.issubset(verdict['checks'])
    # A fresh native process must find the same originals and research without
    # relying on a browser reload or a live Store instance from the first run.
    run_probe('--reopen')
    reopened_receipt = folder / 'reopened-result.json'
    assert reopened_receipt.is_file(), f'未生成重新打开验收回执：{folder}'
    reopened = json.loads(reopened_receipt.read_text(encoding='utf-8'))
    assert reopened['ok'] is True
    assert reopened['new_process'] is True
    assert reopened['original_notes_and_trials'] is True
    assert reopened['no_repeated_hint'] is True
    assert reopened['recent_case_outside_first_page'] is True
    assert reopened['one_original_on_resume'] is True
