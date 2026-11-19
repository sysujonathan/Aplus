"""Drive transport for the existing human-approved upload executor.

The executor calls publish_approved with its trusted approval verifier, and
supplies a read-only client-view probe for the mounted desktop distribution.
The staging runner has no access to this entry point.
"""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.prepare_sync_delivery import TARGET_FOLDER_ID, TARGET_PARENT_ID
from tools.publish_sync_delivery import publish


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
        from workbench.store import digest
        if digest(body) != item.get('sha256Checksum'):
            body = body.removesuffix(b'\n')
        if digest(body) != item.get('sha256Checksum'):
            raise ValueError('Drive latest 回读摘要不一致')
        return body

    def read_visible_latest(self):
        return Path(self.client_latest).read_bytes()


def publish_approved(staged, receipts, client_latest, check_approval, cli='/opt/hatch/bin/hatch_gws_cli'):
    """Called only by the serialized production approval executor."""
    from fcntl import LOCK_EX, flock

    receipts = Path(receipts)
    receipts.mkdir(parents=True, exist_ok=True)
    with (receipts / 'drive-publish.lock').open('a') as lock:
        flock(lock, LOCK_EX)
        return publish(staged, DriveTransport(client_latest, cli), receipts,
                       lambda sha: check_approval(sha, TARGET_FOLDER_ID, TARGET_PARENT_ID))
