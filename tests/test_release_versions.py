"""Release numbering is descriptive, not an update permission or strategy input."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from workbench import releases as r

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_covers_each_pr_without_inventing_publications():
    catalog = r.read_catalog(ROOT)
    assert [x['pr'] for x in catalog['history']] == list(range(1, 45))
    by_pr = {x['pr']: x for x in catalog['history']}
    assert by_pr[1]['version'] is None and by_pr[1]['status'] == 'not_released'
    assert by_pr[12]['included_in'] == by_pr[13]['included_in'] == 11
    assert by_pr[23]['included_in'] == 22
    assert by_pr[23]['date'] == '2026-10-06'
    assert by_pr[23]['version'] == '2026.10.06.23'
    assert by_pr[30]['status'] == by_pr[31]['status'] == by_pr[32]['status'] == 'released'
    assert by_pr[31]['commit'] == '1f6538ee3c462e753d5516fd5890fb19a69b8fe9'
    assert by_pr[32]['commit'] == 'e0fbe49033a9ffd4da83d94825f26d79158cc0e5'
    assert by_pr[33]['status'] == 'included' and by_pr[33]['included_in'] == 34
    assert by_pr[34]['status'] == by_pr[35]['status'] == 'released'
    assert by_pr[35]['commit'] == '18c29fe185f5a17df5e536e81664abd73ed1fb52'
    assert by_pr[36]['status'] == 'released' and by_pr[36]['version'] == '2026.10.12.36'
    assert by_pr[36]['commit'] == '3599dfbe9fe9211c5de3b37d64f92dc3f1a0e216'
    assert by_pr[37]['status'] == 'released' and by_pr[37]['version'] == '2026.10.12.37'
    assert by_pr[37]['commit'] == 'c88e43434967e2dc526328c02d103ceca26b3506'
    assert by_pr[38]['status'] == 'released' and by_pr[38]['version'] == '2026.10.13.38'
    assert by_pr[38]['commit'] == 'd7d0c3c476f7e2b31cbb3957855d9d6b2a6b8dca'
    assert by_pr[39]['status'] == 'released'
    assert by_pr[39]['commit'] == '4b863aae8b05415dea57296fd08c61e15824762d'
    assert by_pr[40]['status'] == 'released'
    assert by_pr[40]['commit'] == '61959b04c97dabe442af134be8993d19604a8cc9'
    assert by_pr[41]['status'] == 'released'
    assert by_pr[41]['commit'] == '3ef30d8a019c8e0f618f202c686d1852e2097101'
    assert by_pr[42]['status'] == 'released'
    assert by_pr[42]['commit'] == 'c8993064e5f5a2f05a54dc1c66f007878d56b7b7'
    assert by_pr[43]['status'] == 'released'
    assert by_pr[43]['commit'] == 'b2060e15f9b207f285fc80cb9438c805a3932a74'
    assert by_pr[44]['status'] == 'preview'
    assert all('commit' not in by_pr[x] for x in (33, 34))
    assert by_pr[34]['version'] == '2026.10.12.34'
    assert by_pr[35]['version'] == '2026.10.12.35'
    assert by_pr[30]['version'] == '2026.10.11.30'
    assert by_pr[30]['commit'] == 'e4d5f6553de053b2b423297f27d3964c2e7f236b'
    assert by_pr[29]['version'] == '2026.10.10.29'
    assert by_pr[29]['commit'] == '18670c8904768927f2bc37c317fee27a73a011df'


def test_generated_document_and_offline_catalog_are_one_source():
    text = r.changelog_text(r.read_catalog(ROOT))
    assert text.rstrip() == (ROOT / 'docs/更新日志.md').read_text(encoding='utf-8').rstrip()
    assert 'v2026.10.11.30' in text and 'v2026.10.11.30-preview' not in text
    assert '随PR11' in text and '随PR22' in text and '随PR34' in text
    assert 'v2026.10.12.34' in text and 'v2026.10.11.34-preview' not in text
    assert 'v2026.10.12.36' in text and 'v2026.10.12.36-preview' not in text
    assert 'v2026.10.12.37-preview' not in text and 'v2026.10.12.37' in text
    assert 'v2026.10.13.38-preview' not in text
    assert 'v2026.10.14.40-preview' not in text
    assert 'v2026.10.14.40' in text
    assert 'v2026.10.14.41-preview' not in text
    assert 'v2026.10.14.42-preview' not in text
    assert 'v2026.10.14.43-preview' not in text
    assert 'v2026.10.14.44-preview' in text


@pytest.mark.parametrize('mutation', ['schema', 'version', 'date', 'duplicate', 'status', 'sha', 'notes', 'build_pr', 'date_version', 'row'])
def test_invalid_catalog_is_not_a_release(mutation, tmp_path):
    c = copy.deepcopy(r.read_catalog(ROOT))
    if mutation == 'schema': c['schema'] = 99
    elif mutation == 'version': c['build']['version'] = 'v9';
    elif mutation == 'date': c['build']['date'] = '2026-13-99'
    elif mutation == 'duplicate': c['history'].append(c['history'][0])
    elif mutation == 'status': c['history'][0]['status'] = 'approved_by_version'
    elif mutation == 'sha': c['history'][1]['commit'] = '18670c8'
    elif mutation == 'notes': c['history'][1]['notes'] = 'not a list'
    elif mutation == 'build_pr': c['build']['pr'] = 999
    elif mutation == 'date_version': c['history'][1]['version'] = '2026.10.02.2'
    else: c['history'][0] = None
    (tmp_path / 'release_catalog.json').write_text(json.dumps(c), encoding='utf-8')
    assert r.read_catalog(tmp_path) is None


def test_missing_or_oversized_catalog_has_no_fake_version(tmp_path):
    assert r.read_catalog(tmp_path) is None
    assert r.identity(tmp_path)['version'] is None
    (tmp_path / 'release_catalog.json').write_text(' ' * 256001)
    assert r.read_catalog(tmp_path) is None


def test_exact_historical_main_and_current_preview_are_distinct():
    historical = r.identity(ROOT, target='18670c8904768927f2bc37c317fee27a73a011df')
    assert r.version_label(historical) == 'v2026.10.10.29'
    merged = r.identity(ROOT, target='e4d5f6553de053b2b423297f27d3964c2e7f236b',
                        git_reader=lambda *args: '{}')  # PR30 predates the manifest.
    assert r.version_label(merged) == 'v2026.10.11.30'
    assert merged['channel'] == 'main' and merged['notes']
    preview = r.identity(ROOT, dict(head='a'*40, branch='codex/preview'))
    assert r.version_label(preview) == 'v2026.10.14.44 · 预览版'
    # A short hash and an unrelated revision must not be called PR29.
    assert r.identity(ROOT, target='18670c8')['version'] is None
    assert r.identity(ROOT, target='b'*40)['version'] is None


def test_exact_remote_manifest_not_local_preview_determines_target():
    remote = copy.deepcopy(r.read_catalog(ROOT))
    remote['build'] = dict(version='2026.10.10.30', date='2026-10-10', pr=30)
    calls = []
    def read(sha, path):
        calls.append((sha, path)); return json.dumps(remote)
    release = r.identity(ROOT, target='a'*40, git_reader=read)
    assert release['version'] == '2026.10.10.30'
    assert release['channel'] == 'main'
    assert calls == [('a'*40, 'release_catalog.json')]


def test_malformed_remote_and_unknown_old_version_stay_unknown(tmp_path):
    result = r.identity(tmp_path, target='a'*40, git_reader=lambda *a: '{}')
    assert result['version'] is None


@pytest.mark.parametrize('state,phrase', [('current','无需更新'), ('ahead','本机修复版'),
    ('diverged','确认'), ('pending','验收'), ('unavailable','无法检查'), ('available','新版')])
def test_beginner_conclusions_and_permissions(state, phrase):
    result = dict(state=state, can_update=state=='available', local={'head':'a'*40}, target='b'*40,
                  local_release=dict(version='2026.10.10.29'), target_release=dict(version='2026.10.11.31'))
    view = r.presentation(result)
    assert phrase in view['title']
    assert view['can_update'] == result['can_update']
    assert 'codex/' not in view['message'] and 'PULL' not in view['message']
    assert view['current'].startswith('v2026')


@pytest.mark.parametrize('reason,phrase', [('modified','修改'), ('development','预览'), ('manual','人工安装'), ('cached','后台确认')])
def test_blocked_update_has_next_step(reason, phrase):
    view = r.presentation(dict(state='available', can_update=False, block_reason=reason))
    assert not view['can_update'] and phrase in view['message']


@pytest.mark.parametrize('status,phrase', [('running','请稍候'), ('failed','未通过'), ('missing','尚未开始')])
def test_ci_next_step_distinguishes_waiting_from_failed_checks(status, phrase):
    view=r.presentation(dict(state='pending',ci_status=status,can_update=False))
    assert phrase in view['title'] and not view['can_update']
    assert '当前版本' in view['message']
    if status=='failed': assert '负责人修复' in view['message']


def test_manual_install_is_not_presented_as_waiting_for_ci():
    view=r.presentation(dict(state='available',can_update=False,block_reason='manual',ci_passed=True))
    assert view['title']=='本次更新需要人工安装'
    assert view['action_label']=='本次需要人工安装'
    assert '这不是等待自检' in view['message'] and '不会使按钮启用' in view['message']
    assert '提供 Aplus 的同事' in view['message'] and '不要删除' in view['message']
    assert not view['can_update']


def test_same_version_different_commit_explains_code_revision_without_inventing_version():
    result=dict(state='available',local={'head':'a'*40},target='b'*40,
                local_release=dict(version='2026.10.13.38'),target_release=dict(version='2026.10.13.38'))
    view=r.presentation(result)
    assert view['current']==view['target']=='v2026.10.13.38'
    assert '同一版本号' in view['revision_note'] and not view['can_update']
    assert not r.presentation(dict(result,target='a'*40))['revision_note']


def test_running_version_is_not_relabelled_after_disk_replacement():
    running = dict(head='a'*40, version='2026.10.10.29')
    view = r.presentation(dict(state='available', can_update=True, local={'head':'b'*40},
                            local_release=dict(version='2026.10.11.31')), running)
    assert view['current'] == 'v2026.10.10.29'
    assert not view['can_update'] and '重新启动' in view['title']
    assert '托盘' in view['message']


def test_dirty_current_is_not_advertised_identical_to_main():
    view = r.presentation(dict(state='current', local={'dirty':True}))
    assert '定制' in view['title'] and '暂不能确认' in view['message']


def test_copied_worker_remains_independent_of_workbench_packages(tmp_path):
    worker = tmp_path / 'worker.py'
    shutil.copyfile(ROOT / 'workbench/version_update.py', worker)
    p = subprocess.run([sys.executable, '-I', str(worker), '--help'], cwd=tmp_path,
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows native desktop')
def test_native_manual_update_dialog_has_versions_folded_details_and_clear_actions(tmp_path):
    script = r'''
import tkinter as tk
from pathlib import Path
from unittest.mock import Mock
import sys
from workbench.store import Store
from workbench.service import Service
from gui.main_window import AplusMainWindow
from gui import version_update as ui
from gui.usage_help import show_usage
s=Store(Path(sys.argv[1])/'isolated'); service=Service(s)
w=AplusMainWindow(service=service,store=s); w.update()
c=w.version_controller
c.running=dict(head='a'*40,version='2026.10.10.29',channel='main')
release=dict(state='available',can_update=True,target='b'*40,local=dict(head='a'*40,branch='main',dirty=False),
             target_release=dict(version='2026.10.11.31',notes=['中文简明摘要']))
def children(p):
    for x in p.winfo_children():
        yield x
        yield from children(x)
def button(text):
    return next(x for x in children(c.notice) if x.winfo_class()=='TButton' and x.cget('text')==text)
height=w.chart.winfo_height()
c.show_details(release); w.update()
assert c.notice.grab_current() is None
labels=' '.join(str(x.cget('text')) for x in children(c.notice) if x.winfo_class()=='TLabel')
assert 'v2026.10.10.29' in labels and 'v2026.10.11.31' in labels and '中文简明摘要' in labels
assert 'codex/' not in labels and 'aaaaaaa' not in labels
technical=next(x for x in children(c.notice) if isinstance(x,tk.Text))
assert not technical.winfo_ismapped()
button('技术详情').invoke(); w.update(); assert technical.winfo_ismapped()
button('技术详情').invoke(); w.update(); assert not technical.winfo_ismapped()
button('更新日志').invoke(); w.update()
tabs=w._usage_window._help_tabs
assert tabs.tab(tabs.select(),'text')=='更新日志'
assert show_usage(w,tab='快速上手') is w._usage_window
assert tabs.tab(tabs.select(),'text')=='快速上手'
w._usage_window.destroy()
assert '立即更新并重启' in labels and '自动完成备份、更新和重新打开' in labels
c.begin_update=Mock()
update_button=button('立即更新并重启')
assert 'success' in str(update_button.cget('style')).lower()
assert update_button.winfo_height() > button('稍后').winfo_height()
update_button.invoke(); c.begin_update.assert_called_once_with(release)
assert c.notice is None
# Disk code changed but the running window still has the old version.
c.show_details(dict(release,local=dict(head='c'*40,branch='main'))); w.update()
assert str(button('立即更新并重启').cget('state'))=='disabled'
button('稍后').invoke()
# Manual checks during work do not offer an actionable restart.
service.cancel_flags['synthetic']=object()
c.show_details(release); w.update()
assert str(button('立即更新并重启').cget('state'))=='disabled'
button('稍后').invoke(); service.cancel_flags.clear()
blocked=dict(release,can_update=False,block_reason='manual',ci_passed=True)
c.show_details(blocked); w.update()
assert str(button('本次需要人工安装').cget('state'))=='disabled'
labels=' '.join(str(x.cget('text')) for x in children(c.notice) if x.winfo_class()=='TLabel')
assert '这不是等待自检' in labels and '提供 Aplus 的同事' in labels
button('本次需要人工安装').invoke(); assert c.begin_update.call_count==1
button('关闭').invoke()
assert w.chart.winfo_height()==height and not s.rows('SELECT id FROM jobs')
w.destroy(); service.pool.shutdown(wait=True)
'''
    p = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                       capture_output=True, text=True, timeout=50)
    assert p.returncode == 0, p.stdout + p.stderr
