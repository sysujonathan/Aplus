"""Real distribution contracts, with disposable metadata and no business DB."""
import json
import io
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import installed_entry, release_preflight
from workbench import installed_data, installed_imports, installed_runtime
from workbench import installed_update as update, process_entry
from tests.test_installed_distribution import manifest


ROOT = Path(__file__).resolve().parents[1]


def test_growth_source_and_frozen_process_commands(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert process_entry.growth_command(Path('a b.json')) == [
        sys.executable, '-m', 'gui.growth_shell', 'a b.json']
    monkeypatch.setattr(sys, 'frozen', True)
    assert process_entry.growth_command(Path('a b.json')) == [
        sys.executable, '--growth', 'a b.json']


def test_installed_growth_dispatch_never_opens_another_desktop(monkeypatch):
    handler = Mock()
    monkeypatch.setitem(sys.modules, 'gui.growth_shell', SimpleNamespace(main=handler))
    monkeypatch.setattr(sys, 'argv', ['AplusApp.exe', '--growth', 'case session.json'])
    monkeypatch.setattr(installed_imports, 'install', lambda: None)
    desktop = Mock(side_effect=AssertionError('duplicate desktop'))
    monkeypatch.setattr(installed_runtime, 'launch', desktop)
    installed_entry.main()
    handler.assert_called_once_with(['case session.json'])
    desktop.assert_not_called()


@pytest.mark.parametrize('source,target', [(8, 9), (9, 8)])
def test_installer_does_not_offer_or_install_a_cross_schema_update(monkeypatch, tmp_path, source, target):
    candidate = manifest()
    candidate['database_schema'] = target
    current = dict(version='2026.10.30.1', sequence=202610300001,
                   commit='b'*40, preview=False, database_schema=source)
    monkeypatch.setattr(update, 'load_manifest', lambda *args, **kwargs: candidate)
    offered = update.check_update(current)
    assert offered['state'] == 'unavailable'
    assert '单独的数据兼容升级' in offered['detail']
    with pytest.raises(ValueError, match='单独的数据兼容升级'):
        update.verify_candidate(candidate, tmp_path/'never-read.exe', current)


@pytest.mark.parametrize('schema', [True, 7, 10, '9', None])
def test_unknown_current_schema_fails_closed(schema):
    with pytest.raises(ValueError, match='数据兼容升级'):
        update.require_same_schema(manifest(), dict(database_schema=schema))


def test_missing_current_schema_is_not_assumed_to_match_the_new_code():
    with pytest.raises(ValueError, match='数据兼容升级'):
        update.require_same_schema(manifest(), {})


def test_new_release_still_reads_previous_official_schema_eight_for_publication():
    previous = manifest()
    previous['database_schema'] = 8
    head = 'c'*40
    def api(path):
        if path == '/commits/main':
            return dict(sha=head)
        if path == '/releases/latest':
            return dict(draft=False, prerelease=False, tag_name='v'+previous['version'], assets=[
                dict(name='latest.json',size=500,browser_download_url=update.PACKAGE_BASE+'v'+previous['version']+'/latest.json'),
                dict(name='setup.exe',size=previous['package']['bytes'],browser_download_url=previous['package']['url'])])
        if path.startswith('/commits/v'):
            return dict(sha=previous['commit'])
        if path.startswith('/compare/'):
            return dict(status='ahead')
        sha = path.split('head_sha=', 1)[1].split('&', 1)[0]
        return dict(workflow_runs=[dict(id=1,event='push',head_sha=sha,head_branch='main',
            name='A acceptance',path='.github/workflows/checks.yml',status='completed',conclusion='success')])
    result = release_preflight.preflight('workflow_dispatch', {}, 'refs/heads/main',
        release_preflight.REPO, head, api=api,
        opener=lambda *args, **kwargs: io.BytesIO(json.dumps(previous).encode()))
    assert result == dict(build=True, commit=head)


def test_schema_nine_binding_can_reopen_without_changing_database_bytes(tmp_path):
    runtime = tmp_path/'runtime'
    runtime.mkdir()
    path = runtime/'workbench.sqlite3'
    script = (ROOT/'tests/fixtures/installed_legacy_v1.sql').read_text(encoding='utf-8')
    with sqlite3.connect(path) as db:
        db.executescript(script)
        db.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    before = path.read_bytes()
    home = tmp_path/'state'
    installed_data.bind_existing(runtime, home=home)
    assert installed_data.bound_runtime(home=home) == runtime
    assert path.read_bytes() == before
    receipt = json.loads((home/'data.json').read_text(encoding='utf-8'))
    assert Path(receipt['backup']).read_bytes() == before


def test_both_delivery_contracts_describe_the_same_migration_without_claiming_a_release():
    package = json.loads((ROOT/'packaging/release.json').read_text(encoding='utf-8'))
    source = json.loads((ROOT/'release_installation.json').read_text(encoding='utf-8'))
    assert package['database_schema'] == source['database']['schema'] == 9
    assert source['database']['supported_from'] == list(range(1, 10))
    assert source['database']['migration'] == 'store-v9'
    spec = (ROOT/'packaging/Aplus.spec').read_text(encoding='utf-8')
    assert "'gui/pa_growth'" in spec
