"""Drive transport for the existing human-approved upload executor.

The executor calls publish_approved with its trusted approval verifier, and
supplies a read-only client-view probe for the mounted desktop distribution.
The staging runner has no access to this entry point.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.prepare_sync_delivery import TARGET_FOLDER_ID, TARGET_PARENT_ID
from tools.publish_sync_delivery import LocalRehearsal, publish
from workbench.store import digest
from workbench.sync_packages import load_latest, package_name


class DriveTransport:
    def __init__(self, client_latest, cli):
        self.client_latest = client_latest
        self.cli = cli

    def _call(self, *args, params=None):
        command = [self.cli, 'drive', *args]
        if params:
            command += ['--params', json.dumps(params)]
        return subprocess.run(command, check=True, capture_output=True, timeout=1800).stdout

    def _find(self, name):
        request = dict(q=f"'{TARGET_FOLDER_ID}' in parents and name = '{name}' and trashed = false",
                       fields='nextPageToken,files(id,name,sha256Checksum)', pageSize=100)
        result = json.loads(self._call('files', 'list', params=request))
        items = result['files']
        if len(items) > 1 or result.get('nextPageToken'):
            raise ValueError(f'Drive 同步源存在同名文件：{name}')
        return items[0] if items else None

    def put(self, name, source):
        folder = json.loads(self._call('files', 'get', params=dict(
            fileId=TARGET_FOLDER_ID, fields='id,parents,mimeType,trashed')))
        if (folder['id'] != TARGET_FOLDER_ID or folder.get('parents') != [TARGET_PARENT_ID]
                or folder['mimeType'] != 'application/vnd.google-apps.folder' or folder['trashed']):
            raise ValueError('Drive 同步发布目录身份或父级不符')
        item = self._find(name)
        if item is None:
            self._call('+upload', str(source), '--parent', TARGET_FOLDER_ID)
        elif name == 'latest.json':
            self._call('files', 'update', '--upload', str(source), params=dict(fileId=item['id']))
        else:
            self.verify(name, name.removeprefix('package-').removesuffix('.zip'))

    def verify(self, name, sha):
        item = self._find(name)
        if not item or item.get('sha256Checksum') != sha:
            raise ValueError(f'Drive 远端包 SHA-256 核验失败：{name}')

    def read_latest(self):
        item = self._find('latest.json')
        if item is None:
            return None
        body = self._call('files', 'get', params=dict(fileId=item['id'], alt='media'))
        # The CLI may append a newline while printing; the Drive checksum
        # confirms that normalization preserves the exact approved bytes.
        if digest(body) != item.get('sha256Checksum'):
            body = body.removesuffix(b'\n')
        if digest(body) != item.get('sha256Checksum'):
            raise ValueError('Drive latest 回读摘要不一致')
        return body

    def read_visible_latest(self):
        return Path(self.client_latest).read_bytes()


def load_request(staged):
    """Bind the staging request to the exact pointer and intended destination."""
    staged = Path(staged)
    request = json.loads((staged / 'upload-request.json').read_bytes())
    if (request['target_folder_id'], request['expected_parent_id']) != (TARGET_FOLDER_ID, TARGET_PARENT_ID):
        raise ValueError('待审批请求目标目录或父级不符')
    body = (staged / 'latest.json').read_bytes()
    plan = load_latest(body)
    target = plan['chain'][-1]
    if (request['status'] != 'awaiting-human-upload-approval'
            or request['pointer_sha256'] != digest(body) or request['package'] != target
            or request.get('fallback') != plan.get('fallback')
            or request['manifest_digest'] != target['version'] or request['asof'] != target['asof']):
        raise ValueError('待审批请求与版本包清单不符')
    with ZipFile(staged / package_name(target['sha256'])) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    if (manifest['sha256'] != target['version'] or manifest['asof'] != target['asof']
            or len(manifest['datasets']) + len(manifest['directories']) + 2 != request['files']):
        raise ValueError('待审批请求与包内版本或文件数不符')
    return request


def _execute(staged, receipts, transport, check_approval, *, mode):
    from fcntl import LOCK_EX, flock

    receipts = Path(receipts)
    receipts.mkdir(parents=True, exist_ok=True)
    with (receipts / 'publish.lock').open('a') as lock:
        flock(lock, LOCK_EX)
        request = load_request(staged)

        def approved(sha):
            return sha == request['pointer_sha256'] and check_approval(sha) is True

        return publish(staged, transport, receipts, approved, request=request, mode=mode)


def publish_approved(staged, receipts, client_latest, check_approval, cli='/opt/hatch/bin/hatch_gws_cli'):
    """Scheduler callback for a trusted, serialized approval executor."""
    return _execute(
        staged, receipts, DriveTransport(client_latest, cli),
        lambda sha: check_approval(sha, TARGET_FOLDER_ID, TARGET_PARENT_ID), mode='drive')


def main(argv=None):
    parser = argparse.ArgumentParser(description='执行已备桌面发布包的隔离演练，生产调用由人工审批执行器发起')
    parser.add_argument('staged', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--rehearsal-root', type=Path, help='隔离演练目录，可连续发布同一版本链')
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.exit(2, '阻塞在审批：请由现有人工审批执行器调用 publish_approved。\n')
    if args.rehearsal_root is None:
        parser.error('--dry-run 需要 --rehearsal-root')
    root = args.rehearsal_root
    staged = args.staged.resolve()
    resolved = root.resolve()
    if resolved.is_relative_to(staged) or staged.is_relative_to(resolved):
        parser.error('演练目录与待审批目录必须隔离')
    root.mkdir(parents=True, exist_ok=True)
    remote = root / 'remote'
    remote.mkdir(exist_ok=True)
    if remote.is_symlink():
        parser.error('演练远端不能是符号链接')
    _execute(staged, root, LocalRehearsal(remote), lambda _sha: True, mode='dry-run')


if __name__ == '__main__':
    main()
