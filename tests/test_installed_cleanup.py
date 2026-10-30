"""Only byte-exact build-owned files may be retired; data/unknown files stay."""
import json
from pathlib import Path

import pytest

from workbench import installed_cleanup as clean, installed_data as data
from tests.test_installed_distribution import manifest


@pytest.fixture
def versions(tmp_path):
    root=tmp_path/'program'
    for n in (1,2,3):
        path=root/'app'/f'2026.10.30.{n}';(path/'_internal').mkdir(parents=True)
        (path/'AplusApp.exe').write_bytes(b'application')
        data.atomic_json(path/'_internal'/'installed-release.json',dict(version=path.name,sequence=202610300000+n,commit='a'*40,preview=True))
        clean.write_inventory(path)
    (root/'current.txt').write_text('2026.10.30.3');(root/'previous.txt').write_text('2026.10.30.2')
    return root,tmp_path/'runtime',root/'app'/'2026.10.30.1'


def test_only_old_owned_version_is_removed(versions):
    root,runtime,old=versions
    assert clean.prune_versions(root,runtime,images=[])==[old.name]
    assert not old.exists()
    assert (root/'app'/'2026.10.30.2'/'AplusApp.exe').exists()
    assert (root/'app'/'2026.10.30.3'/'AplusApp.exe').exists()


@pytest.mark.parametrize('reason',['file','empty_directory','changed','no_inventory','invalid_inventory','active','runtime','database'])
def test_uncertain_or_in_use_old_version_is_retained(versions,reason):
    root,runtime,old=versions;images=[]
    if reason=='file':(old/'personal.txt').write_bytes(b'keep')
    elif reason=='empty_directory':(old/'private').mkdir()
    elif reason=='changed':(old/'AplusApp.exe').write_bytes(b'user modified')
    elif reason=='no_inventory':(old/'installed-files.json').unlink()
    elif reason=='invalid_inventory':(old/'installed-files.json').write_text('{}')
    elif reason=='active':images=[old/'AplusApp.exe']
    elif reason=='runtime':runtime=old/'user-data'
    elif reason=='database':(old/'workbench.sqlite3').write_bytes(b'keep');clean.write_inventory(old)
    before={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
    assert clean.prune_versions(root,runtime,images=images)==[]
    assert all((old/name).read_bytes()==value for name,value in before.items())


def test_completed_update_removes_verified_setup_and_keeps_receipts(versions,tmp_path,monkeypatch):
    root,runtime,old=versions;folder=tmp_path/'attempt';folder.mkdir()
    (folder/'setup.exe').write_bytes(b'installer bytes');(folder/'request.json').write_text('keep audit')
    monkeypatch.setattr(clean,'active_images',lambda:set())
    clean.finish_update(root,runtime,folder,manifest())
    assert not (folder/'setup.exe').exists() and not old.exists()
    assert (folder/'request.json').read_text()=='keep audit'
    assert json.loads((folder/'cleanup.json').read_text())['download_removed']


def test_cleanup_failure_does_not_fail_update_or_remove_unverified_setup(versions,tmp_path,monkeypatch):
    root,runtime,old=versions;folder=tmp_path/'attempt';folder.mkdir()
    (folder/'setup.exe').write_bytes(b'not the approved package')
    def unknown():raise ValueError('unknown process identity')
    monkeypatch.setattr(clean,'active_images',unknown)
    clean.finish_update(root,runtime,folder,manifest())
    assert old.exists() and (folder/'setup.exe').read_bytes()==b'not the approved package'
    assert 'deferred' in json.loads((folder/'cleanup.json').read_text())
