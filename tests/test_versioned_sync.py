"""The sync publication and activation boundaries use separate isolated roots."""
from errno import ENOSPC
import json
import os
from zipfile import ZipFile

import pytest

from gui.data import load_candidates, load_observation_candles
from tests.test_desktop_sync import ASOF, NEXT, edit, sample
from tools.publish_sync_delivery import LocalRehearsal, publish
from workbench.sources import calendar_file, directory_date, directory_file
from workbench.store import Store, digest, dumps
from workbench.sync_client import apply_sync_source
from workbench.sync_packages import _splice, package_name, write_latest, write_package


@pytest.fixture
def delivery(sample, tmp_path):
    server, desktop, source = sample
    remote = tmp_path / 'remote'
    remote.mkdir()
    receipts = tmp_path / 'receipts'
    staged = tmp_path / 'staged'
    staged.mkdir()

    def stage(root, parent=None, previous=None, recovery=False):
        out = staged / str(len(list(staged.iterdir())))
        out.mkdir()
        item = write_package(root, out, parent)
        fallback = write_package(root, out) if recovery else None
        sha = write_latest(out, item, previous.read_bytes() if previous else None, fallback)
        return out, item, sha

    def send(entry):
        out, _, sha = entry
        return publish(out, LocalRehearsal(remote), receipts, lambda value: value == sha)

    return server, desktop, source, remote, receipts, stage, send


def _copy_revision(src, tmp_path, number, *, day=ASOF):
    import shutil
    out = tmp_path / f'revision-{number}'
    shutil.copytree(src, out)
    edit(out, lambda manifest: manifest.update(generated_at=f'{day}T12:00:0{number}+00:00'))
    return out


def test_approved_upload_verifies_and_activates_last(delivery):
    _, _, source, remote, receipts, stage, send = delivery
    entry = stage(source)
    with pytest.raises(PermissionError):
        publish(entry[0], LocalRehearsal(remote), receipts, lambda _: False)
    assert list(remote.iterdir()) == []

    class BrokenVerification(LocalRehearsal):
        def verify(self, *_):
            raise ValueError('remote corruption')

    with pytest.raises(ValueError, match='remote corruption'):
        publish(entry[0], BrokenVerification(remote), receipts, lambda _: True)
    assert not (remote / 'latest.json').exists()

    class DelayedVisibility(LocalRehearsal):
        def read_visible_latest(self):
            return None

    with pytest.raises(ValueError, match='桌面入口'):
        publish(entry[0], DelayedVisibility(remote), receipts, lambda _: True)
    events = [json.loads(line) for line in (receipts / 'publish.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events[-1]['state'] == 'failed'
    assert events[-1]['last_completed'] == 'latest-activated'
    assert all(entry['state'] != 'client-visible' for entry in events)
    assert send(entry) == entry[1]['version']
    events = [json.loads(line) for line in (receipts / 'publish.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events[-1]['state'] == 'client-visible'
    assert events[-1]['pointer_sha256'] == entry[2]


@pytest.mark.parametrize('damage', ['truncated', 'wrong-parent', 'missing', 'cancelled', 'failed-commit', 'disk-full'])
def test_failed_activation_keeps_prior_candidates_and_charts(delivery, tmp_path, damage, monkeypatch):
    _, desktop, source, remote, _, stage, send = delivery
    baseline = stage(source)
    send(baseline)
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    desktop.watch('obs-0')
    desktop.save_plan('obs-0', '计划交易', 11, 9, 15, 100, 'sync regression')
    desktop.save_chart_document('private-chart', {}, 0)
    private_tables = ('watchlist', 'plans', 'accounts', 'positions', 'closed_trades')
    private_state = {table: desktop.rows(f'SELECT * FROM {table}') for table in private_tables}
    prior = desktop.rows("SELECT key,value FROM meta ORDER BY key")
    revision = _copy_revision(source, tmp_path, 1)
    edit(revision, lambda manifest: manifest.update(observations=manifest['observations'][1:]))
    delta = stage(revision, source, baseline[0] / 'latest.json')
    send(delta)
    package = remote / package_name(delta[1]['sha256'])
    if damage == 'truncated':
        original = package.read_bytes()
        package.write_bytes(original[:len(original)//2])
    elif damage == 'wrong-parent':
        corrupt = tmp_path / 'corrupt.zip'
        with ZipFile(package) as original, ZipFile(corrupt, 'w') as broken:
            for name in original.namelist():
                body = original.read(name)
                if name == 'version.json':
                    header = json.loads(body)
                    header['parent'] = 'f' * 64
                    body = dumps(header).encode()
                broken.writestr(name, body)
        sha = digest(corrupt.read_bytes())
        corrupt.rename(remote / package_name(sha))
        write_latest(remote, dict(delta[1], sha256=sha), (baseline[0] / 'latest.json').read_bytes())
    elif damage == 'missing':
        package.unlink()
    elif damage == 'failed-commit':
        desktop.execute("CREATE TRIGGER fail_version BEFORE UPDATE ON meta "
                        "WHEN NEW.key='sync_version' BEGIN SELECT RAISE(ABORT, 'injected'); END")

    def disk_full(*_):
        raise OSError(ENOSPC, 'injected')

    phases = []
    with monkeypatch.context() as fault:
        if damage == 'disk-full':
            fault.setattr(desktop, 'write_immutable_sync_file', disk_full)
        result = apply_sync_source(desktop, remote, progress=phases.append,
                                   cancelled=lambda: damage == 'cancelled' and
                                   any('重建并校验' in phase for phase in phases))
    if damage == 'disk-full':
        assert '本地磁盘空间不足' in result['message']
    assert result['status'] == 'rejected'
    assert desktop.rows("SELECT key,value FROM meta ORDER BY key") == prior
    assert {table: desktop.rows(f'SELECT * FROM {table}') for table in private_tables} == private_state
    assert calendar_file(desktop, 'tickflow').is_file()
    assert load_observation_candles(desktop, 'obs-0')[0].date.max() == ASOF
    assert {row['observation_id'] for rows in load_candidates(desktop, 'daily').values() for row in rows} == {
        'obs-0', 'obs-1'}

    if damage == 'truncated':
        package.write_bytes(original)
    elif damage == 'wrong-parent':
        (remote / 'latest.json').write_bytes((delta[0] / 'latest.json').read_bytes())
    elif damage == 'missing':
        send(delta)
    elif damage == 'failed-commit':
        desktop.execute('DROP TRIGGER fail_version')
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    assert {table: desktop.rows(f'SELECT * FROM {table}') for table in private_tables} == private_state
    assert load_observation_candles(desktop, 'obs-0')[0].date.max() == ASOF
    assert {row['observation_id'] for rows in load_candidates(desktop, 'daily').values() for row in rows} == {'obs-1'}
    # A desktop replay of the older same-day pointer may not undo the correction.
    (remote / 'latest.json').write_bytes((baseline[0] / 'latest.json').read_bytes())
    assert apply_sync_source(desktop, remote)['status'] == 'rejected'


def _stage_next_day(delivery, tmp_path, baseline, source):
    _, _, _, _, _, stage, send = delivery
    revision = _copy_revision(source, tmp_path, 1, day=NEXT)
    directory = 'sources/tickflow/universe.csv'
    calendar = 'sources/tickflow/trading_calendar.json'
    (revision / directory).write_bytes((source / directory).read_bytes() + b'sz.000001,new\n')
    (revision / calendar).write_text(
        dumps(dict(start=ASOF, end=NEXT, trading_days=[ASOF, NEXT])), encoding='utf-8')
    edit(revision, lambda manifest: manifest.update(
        asof=NEXT, universe_date=NEXT, observations=[],
        directories=[dict(path=directory, sha256=digest((revision / directory).read_bytes()))],
        calendar=dict(path=calendar, sha256=digest((revision / calendar).read_bytes()))))
    delta = stage(revision, source, baseline[0] / 'latest.json')
    send(delta)
    return json.loads((revision / 'manifest.json').read_bytes())


def test_retry_activates_verified_inventory_after_older_local_write(delivery, tmp_path):
    _, desktop, source, remote, _, stage, send = delivery
    baseline = stage(source)
    send(baseline)
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    manifest = _stage_next_day(delivery, tmp_path, baseline, source)

    def interrupt_before_commit(phase):
        if '激活完整版本' in phase:
            raise InterruptedError('injected interruption before commit')

    assert apply_sync_source(desktop, remote, progress=interrupt_before_commit)['status'] == 'rejected'
    applied = desktop.rows("SELECT value FROM meta WHERE key='sync_asof'")
    assert applied == [{'value': ASOF}]

    for row in [*manifest['directories'], manifest['calendar']]:
        relative = row['path']
        immutable = desktop.root / 'desktop-sync' / 'versions' / manifest['sha256'] / (source / relative).name
        assert digest(immutable.read_bytes()) == row['sha256']
        old = desktop.write_artifact(relative, (source / relative).read_bytes())
        # Make the order deterministic even on coarse-timestamp filesystems.
        later = immutable.stat().st_mtime_ns + 2_000_000_000
        os.utime(old, ns=(later, later))
    desktop.execute("INSERT INTO meta VALUES('universe_date:tickflow', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ASOF,))

    for status in ('applied', 'up-to-date'):
        result = apply_sync_source(desktop, remote)
        assert (result['status'], result['asof']) == (status, NEXT)
        calendar = json.loads(calendar_file(desktop, 'tickflow').read_bytes())
        assert (
            directory_date(desktop, 'tickflow'), max(calendar['trading_days']),
            digest(directory_file(desktop, 'tickflow').read_bytes()),
        ) == (NEXT, NEXT, manifest['directories'][0]['sha256'])


def test_activation_rejects_if_effective_calendar_routes_to_old_inventory(delivery, tmp_path, monkeypatch):
    _, desktop, source, remote, _, stage, send = delivery
    baseline = stage(source)
    send(baseline)
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    old_calendar = calendar_file(desktop, 'tickflow')
    manifest = _stage_next_day(delivery, tmp_path, baseline, source)

    with monkeypatch.context() as patch:
        patch.setattr('workbench.sync_client.calendar_file', lambda *_: old_calendar)
        assert apply_sync_source(desktop, remote)['status'] == 'rejected'
        assert desktop.rows("SELECT value FROM meta WHERE key='sync_version'") == [
            {'value': manifest['sha256']}]
        assert apply_sync_source(desktop, remote)['status'] == 'rejected'
    assert apply_sync_source(desktop, remote)['status'] == 'up-to-date'


def test_catch_up_from_active_version_and_optional_baseline(delivery, tmp_path):
    _, desktop, source, remote, _, stage, send = delivery
    baseline = stage(source)
    send(baseline)
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    one = _copy_revision(source, tmp_path, 1)
    delta = stage(one, source, baseline[0] / 'latest.json')
    send(delta)
    two = _copy_revision(one, tmp_path, 2)
    target = stage(two, one, delta[0] / 'latest.json', recovery=True)
    send(target)
    (remote / package_name(baseline[1]['sha256'])).unlink()
    assert apply_sync_source(desktop, remote)['status'] == 'applied'
    assert apply_sync_source(desktop, remote)['status'] == 'up-to-date'
    (remote / package_name(delta[1]['sha256'])).unlink()
    new_desktop = Store(tmp_path / 'clean-recovery')
    assert apply_sync_source(new_desktop, remote)['status'] == 'applied'
    assert load_observation_candles(new_desktop, 'obs-1')[0].date.max() == ASOF


@pytest.mark.parametrize(('before', 'after'), [
    (b'date,price\na,1\n', b'date,price\na,1\nb,2\n'),
    (b'date,price\na,1\nb,2\nc,3\n', b'date,price\na,1\nx,10\ny,11\nc,3\n'),
    (b'date,price\na,1\nb,2\nc,3\n', b'date,price\na,1\nc,3\n'),
    (b'date,price\na,1\n', b'date,price\nz,90\n'),
])
def test_splice_preserves_exact_csv_bytes(before, after):
    start, end, patch = _splice(before, after)
    assert before[:start] + patch + before[end:] == after


@pytest.mark.skipif(os.name == 'nt', reason='Linux runner approval executor')
def test_runner_handoff_blocks_drive_and_writes_dry_run_receipt(delivery, tmp_path, monkeypatch):
    from tools.prepare_sync_delivery import TARGET_FOLDER_ID, TARGET_PARENT_ID, delivery_files
    from tools.runner import publish_approved_sync as runner

    _, _, source, _, _, stage, _ = delivery
    staged, item, sha = stage(source)
    _, files = delivery_files(source)
    request = dict(status='awaiting-human-upload-approval', asof=item['asof'], package=item,
                   target_folder_id=TARGET_FOLDER_ID, expected_parent_id=TARGET_PARENT_ID,
                   manifest_digest=item['version'], pointer_sha256=sha, files=len(files))
    request_path = staged / 'upload-request.json'
    request_path.write_text(dumps(request), encoding='utf-8')
    reports = tmp_path / 'drive-reports'
    calls = []

    def check_approval(*args):
        calls.append(args)
        return False

    def unexpected_drive_call(*args, **kwargs):
        pytest.fail('审批完成前调用了 Drive')

    monkeypatch.setattr(runner.DriveTransport, '_call', unexpected_drive_call)
    with pytest.raises(PermissionError, match='阻塞在审批'):
        runner.publish_approved(staged, reports, tmp_path / 'absent', check_approval)
    assert calls == [(sha, TARGET_FOLDER_ID, TARGET_PARENT_ID)]
    assert not list(reports.glob('*.json'))

    rehearsal = tmp_path / 'dry'
    args = [str(staged), '--dry-run', '--rehearsal-root', str(rehearsal)]
    runner.main(args)
    receipt = json.loads((rehearsal / f'{sha}.json').read_bytes())
    assert receipt['mode'] == 'dry-run'
    assert receipt['status'] == 'client-visible'
    assert receipt['file_count'] == len(files)
    assert receipt['remote_latest'] == item
    assert receipt['package_sha256'] == item['sha256']
    assert 'remote-verified' in receipt['stage_seconds']
    request['pointer_sha256'] = '0' * 64
    request_path.write_text(dumps(request), encoding='utf-8')
    with pytest.raises(ValueError, match='待审批请求与版本包清单不符'):
        runner.main(args)
    assert (rehearsal / 'remote' / 'latest.json').read_bytes() == (staged / 'latest.json').read_bytes()
