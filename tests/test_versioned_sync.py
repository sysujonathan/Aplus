"""The sync publication and activation boundaries use separate isolated roots."""
import json
from zipfile import ZipFile

import pytest

from gui.data import load_candidates, load_observation_candles
from tests.test_desktop_sync import ASOF, edit, sample
from tools.publish_sync_delivery import LocalRehearsal, publish
from workbench.sources import calendar_file
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
    events = [json.loads(line) for line in (receipts / 'publish.jsonl').read_text().splitlines()]
    assert events[-1]['state'] == 'failed'
    assert events[-1]['last_completed'] == 'latest-activated'
    assert all(entry['state'] != 'client-visible' for entry in events)
    assert send(entry) == entry[1]['version']
    events = [json.loads(line) for line in (receipts / 'publish.jsonl').read_text().splitlines()]
    assert events[-1]['state'] == 'client-visible'
    assert events[-1]['pointer_sha256'] == entry[2]


@pytest.mark.parametrize('damage', ['truncated', 'wrong-parent', 'missing', 'cancelled', 'failed-commit'])
def test_failed_activation_keeps_prior_candidates_and_charts(delivery, tmp_path, damage):
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

    phases = []
    result = apply_sync_source(desktop, remote, progress=phases.append,
                               cancelled=lambda: damage == 'cancelled' and
                               any('重建并校验' in phase for phase in phases))
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
