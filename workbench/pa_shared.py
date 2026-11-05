"""One Drive authority; immutable case commits, disposable cache, private learning.

Called only through Store.pa_shared. No SQLite file is uploaded. A manifest is
the commit marker: incomplete original uploads never appear as public cases.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
from functools import wraps
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlencode, urlparse

from PIL import Image

from .pa_auth import GoogleSession, DriveError, SessionChanged, request, protect, atomic_secret
from .pa_growth import now, metadata, image_info, _owner, MAX_CASE_BYTES, MAX_IMAGE_BYTES
from .pa_terms import IDS, selected, matching
from .pa_trial import validate as validate_trial
from .store import dumps

FOLDER = '18qe7MkUJTEndpAM9J1H_Y5-mjbh7l5aX'
MAX_MANIFEST = 256 * 1024
MAX_CASES = 20_000
FIELDS = 'id,name,mimeType,parents,trashed,size,shared,md5Checksum,owners(permissionId),properties,capabilities(canAddChildren)'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', value):
        raise ValueError('云端文件身份无效')
    return value


def encoded(value):
    return dumps(value).encode('utf-8')


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def network_operation(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        user = self.drive.identity()
        session = getattr(self.drive, 'session', None)
        scope = session.operation() if isinstance(session, GoogleSession) else nullcontext()
        key = self.space_key('access') + ':' + user['id']
        epoch = self.get(key, {}).get('epoch')
        try:
            with scope:
                result = method(self, *args, **kwargs)
                if self.get(key, {}).get('epoch') != epoch:
                    raise ValueError('公共访问权限已改变，请重新刷新')
                return result
        except DriveError as exc:
            if exc.status in (401, 403, 404):
                # Target the initiating account, even if another window changed
                # the active login while this network request was pending.
                self.set(key, dict(granted=False, epoch=uuid.uuid4().hex, auth_required=exc.status == 401))
            raise
    return call


class GoogleDrive:
    def __init__(self, session=None, *, profile=None):
        self.session = session or GoogleSession(profile)

    def call(self, path, *, data=None, method=None, headers=None, limit=MAX_MANIFEST):
        token = self.session.access_token()
        url = 'https://www.googleapis.com/drive/v3/' + path
        return request(url, data=data, method=method, limit=limit,
                       headers={'Authorization': 'Bearer ' + token, **(headers or {})})

    def identity(self, online=False):
        value = self.session.load()
        if not value:
            raise ValueError('请先连接 Google Drive')
        user = value['user']
        identifier(user['id'])
        if online:
            raw, _ = self.call('about?fields=user')
            if json.loads(raw)['user']['permissionId'] != user['id']:
                raise ValueError('Google 身份已改变，请重新连接')
        return user

    def info(self, file_id):
        raw, _ = self.call('files/' + identifier(file_id) + '?' + urlencode({'fields': FIELDS}))
        return json.loads(raw)

    def check_folder(self):
        info = self.info(FOLDER)
        if info.get('trashed') or info.get('mimeType') != 'application/vnd.google-apps.folder':
            raise ValueError('指定的 PA 共享目录不可用')
        self.folder_shared = info.get('shared')
        self.folder_owners = [v.get('permissionId') for v in info.get('owners', [])]
        return info.get('capabilities', {}).get('canAddChildren', False)

    def allocate(self, count):
        raw, _ = self.call('files/generateIds?' + urlencode({'count': count, 'space': 'drive', 'type': 'files'}))
        return [identifier(v) for v in json.loads(raw)['ids']]

    def list_cases(self):
        return self.list_records('case-v1')

    def list_records(self, kind):
        if kind not in ('case-v1', 'collaboration-v1'):
            raise ValueError('未知公共记录类型')
        rows, token = [], ''
        while True:
            params = {'q': f"'{FOLDER}' in parents and trashed=false and properties has {{ key='aplus_kind' and value='{kind}' }}",
                      'pageSize': 100, 'fields': 'nextPageToken,files(' + FIELDS + ')'}
            if token:
                params['pageToken'] = token
            raw, _ = self.call('files?' + urlencode(params), limit=2 * 1024 * 1024)
            page = json.loads(raw)
            rows.extend(page.get('files', []))
            if len(rows) > MAX_CASES:
                raise ValueError('公共案例超过本版加载上限，请联系维护者')
            token = page.get('nextPageToken')
            if not token:
                return rows

    def read(self, file_id, limit):
        info = self.info(file_id)
        if FOLDER not in info.get('parents', []) or info.get('trashed'):
            raise ValueError('文件不在指定 PA 共享目录中')
        if int(info.get('size', limit + 1)) > limit:
            raise ValueError('云端文件超过安全上限')
        return self.call('files/' + identifier(file_id) + '?alt=media', limit=limit)[0]

    def put_once(self, file_id, name, data, mime, kind):
        # Reserve ID before network writes. On timeout/restart, read back that
        # same ID; never PATCH existing files or allocate a new commit on retry.
        try:
            existing = self.read(file_id, max(len(data), MAX_MANIFEST))
        except DriveError as exc:
            if exc.status != 404:
                raise
        else:
            if existing != data:
                raise ValueError('云端提交身份冲突，已保留原文件')
            return
        meta = dict(id=identifier(file_id), name=name, parents=[FOLDER], mimeType=mime,
                    properties={'aplus_kind': kind})
        url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id'
        token = self.session.access_token()
        try:
            _, headers = request(url, method='POST', data=encoded(meta), headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json; charset=UTF-8',
                'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(len(data))})
            location = headers.get('Location') or headers.get('location', '')
            parsed = urlparse(location)
            if parsed.scheme != 'https' or parsed.netloc != 'www.googleapis.com' or not parsed.path.startswith('/upload/drive/'):
                raise ValueError('Google 上传地址无效')
            request(location, method='PUT', data=data, headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': mime, 'Content-Length': str(len(data))})
        except DriveError as exc:
            if exc.status != 409:
                raise
        if self.read(file_id, max(len(data), MAX_MANIFEST)) != data:
            raise ValueError('云端文件校验失败，未确认同步成功')


class Workspace:
    def __init__(self, store, drive):
        self.store, self.drive = store, drive

    def get(self, key, default=None):
        rows = self.store.rows('SELECT value FROM meta WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set(self, key, value):
        self.store.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dumps(value)))

    def space_key(self, kind):
        # Cache grants and upload reservations belong to one authority. A
        # product-owner directory change must not reuse an old folder's state.
        return f'pa_shared_{kind}:{FOLDER}'

    def status(self):
        try:
            user = self.drive.identity()
        except ValueError:
            return dict(connected=False, folder_id=FOLDER, cached=False)
        access = self.get(self.space_key('access') + ':' + user['id'], {})
        return dict(connected=True, user=user, folder_id=FOLDER, cached=bool(access.get('granted')),
                    refreshed=access.get('refreshed'), writable=access.get('writable', False),
                    directory_shared=access.get('shared'),
                    admin=user['id'] in access.get('owners', []),
                    access_epoch=access.get('epoch'),
                    reconnect_required=bool(access.get('auth_required')),
                    cache_error=getattr(self.store, '_pa_shared_cache_error', None))

    @network_operation
    def refresh(self):
        user = self.drive.identity()
        key = self.space_key('access') + ':' + user['id']
        epoch = self.get(key, {}).get('epoch')
        try:
            self.drive.identity(True)
            writable = self.drive.check_folder()
            rows = self.drive.list_cases()
            try:
                previous = {c['id']: c for c in self.get(self.space_key('catalogue'), [])}
            except (ValueError, TypeError, KeyError):
                previous = {}  # Rebuild derived cache; authority stays in Drive.
            cases = []
            for row in rows:
                cached = previous.get('shared:' + row['id'])
                if cached and cached.get('cloud_md5') and row.get('md5Checksum'):
                    if cached['cloud_md5'] != row['md5Checksum']:
                        raise ValueError('已有公共案例在云端被修改，请核对原提交；未覆盖有效缓存')
                    if (FOLDER not in row.get('parents', []) or row.get('trashed')
                            or cached['contributor']['id'] not in [v.get('permissionId') for v in row.get('owners', [])]):
                        raise ValueError('已有公共案例的目录或贡献身份改变，未覆盖有效缓存')
                    cases.append(cached)
                    continue
                raw = self.drive.read(row['id'], MAX_MANIFEST)
                try:
                    value = validate_manifest(json.loads(raw), row)
                except (KeyError, TypeError, json.JSONDecodeError):
                    raise ValueError('公共案例资料损坏，未替换上次有效缓存') from None
                cases.append(value)
            if len({c['global_id'] for c in cases}) != len(cases):
                raise ValueError('云端存在重复的全局案例身份，请核对原提交；未替换本地缓存')
            owners = getattr(self.drive, 'folder_owners', [])
            events = []
            old_events = {e['id']: e for e in self.get(self.space_key('collaboration'), [])}
            for row in self.drive.list_records('collaboration-v1'):
                old = old_events.get(row['id'])
                if old and old.get('cloud_md5') and row.get('md5Checksum'):
                    if old['cloud_md5'] != row['md5Checksum']:
                        raise ValueError('公共协作记录被改写，未替换有效缓存')
                    if {'parents', 'owners', 'trashed'} <= row.keys():
                        # Same bytes still need current folder/author checks,
                        # including the folder owner for visibility records.
                        events.append(validate_event(old, row, owners))
                        continue
                try:
                    raw = self.drive.read(row['id'], MAX_MANIFEST)
                except DriveError as exc:
                    if exc.status != 404:
                        raise
                    self.drive.check_folder()
                    raise ValueError('一条公共协作记录暂不可读取，请重试刷新；已保留有效缓存') from None
                events.append(validate_event(json.loads(raw), row, owners))
            # Validate every chain before replacing either cache snapshot.
            for case in cases:
                compose_case(case, events)
            if len(encoded([cases, events])) > 32 * 1024 * 1024:
                raise ValueError('公共案例目录超过本版缓存上限；未替换原缓存')
        except DriveError as exc:
            raise
        # Replace snapshot only after the entire catalogue validates. Another
        # client's concurrent commits appear on the next explicit refresh.
        cases.sort(key=lambda c: (c['updated'], c['id']), reverse=True)
        with self.store.atomic_write():
            if self.get(key, {}).get('epoch') != epoch:
                raise ValueError('访问权限已在其他请求中失效，请重新刷新')
            self.set(self.space_key('catalogue'), cases)
            self.set(self.space_key('collaboration'), events)
            self.set(self.space_key('catalogue_stamp'), uuid.uuid4().hex)
            self.set(key, dict(granted=True, epoch=epoch, writable=writable, refreshed=now(), shared=getattr(self.drive, 'folder_shared', None), owners=owners))
        self.store._pa_shared_cache_error = None
        return dict(count=len(cases), status=self.status())

    def _originals(self):
        status = self.status()
        if not status['connected'] or not status['cached']:
            return []
        stamp = self.get(self.space_key('catalogue_stamp'))
        cached = getattr(self.store, '_pa_shared_catalogue_cache', None)
        if stamp and cached and len(cached) == 3 and cached[:2] == (FOLDER, stamp):
            return cached[2]
        try:
            result = self.get(self.space_key('catalogue'), [])
            if not isinstance(result, list) or any(not isinstance(c, dict) or
                    not {'id', 'contributor', 'term_ids', 'images', 'updated', 'data_kind'} <= c.keys() for c in result):
                raise ValueError('公共目录缓存无效')
        except (ValueError, TypeError):
            self.store._pa_shared_cache_error = '公共目录缓存无法读取，请联网刷新；私密草稿仍可使用。'
            return []
        self.store._pa_shared_cache_error = None
        self.store._pa_shared_catalogue_cache = (FOLDER, stamp, result)
        return result

    def catalogue(self, include_hidden=False):
        if include_hidden and not self.status().get('admin'):
            raise ValueError('只有知识库所有者可以查看已下架案例')
        events = self.get(self.space_key('collaboration'), [])
        cases = [compose_case(c, events) for c in self._originals()]
        return [c for c in cases if include_hidden or not c['hidden']]

    def search(self, query='', filters=None, offset=0):
        filters = filters or {}
        if type(offset) is not int or offset < 0 or not isinstance(query, str) or len(query) > 1000:
            raise ValueError('搜索条件无效')
        if set(filters) - {'symbol', 'market', 'timeframe', 'data_kind', 'topic_id', 'contributor', 'scope', 'hidden'}:
            raise ValueError('未知公共案例筛选')
        q = query.casefold().strip()
        terms = set(matching(q)) if q else set()
        user = self.status().get('user', {})
        personal_error = None
        try:
            note_hits = {n['case_id'] for n in self.personal()['notes'] if q and q in n['text'].casefold()} if q and user else set()
        except ValueError:
            note_hits = set()
            personal_error = '个人笔记暂时无法检索，公共案例资料仍可搜索'
        results = []
        hidden = filters.get('hidden') == 'yes'
        for c in self.catalogue(include_hidden=hidden):
            if hidden and not c['hidden']:
                continue
            if any(value and value.casefold() not in str(c.get(key, '')).casefold()
                   for key, value in filters.items() if key in ('symbol', 'market', 'timeframe', 'data_kind')):
                continue
            if filters.get('topic_id') and filters['topic_id'] not in c['term_ids']:
                continue
            if filters.get('contributor') and c['contributor']['id'] != (user.get('id') if filters['contributor'] == 'me' else filters['contributor']):
                continue
            reasons = [label for key, label in [('title', '标题'), ('memory', '案例描述'), ('symbol', '品种'),
                       ('timeframe', '周期'), ('market', '市场'), ('keywords', '关键词'), ('tags', '已确认标签')]
                       if q and q in str(c.get(key, '')).casefold()]
            if terms.intersection(c['term_ids']):
                reasons.append('Brooks 标准术语')
            if c['id'] in note_hits:
                reasons.append('个人研究')
            if q and not reasons:
                continue
            results.append(dict(c, match_reasons=reasons))
        return dict(cases=results[offset:offset + 100], has_more=len(results) > offset + 100,
                    next_offset=offset + min(100, max(0, len(results) - offset)), personal_error=personal_error)

    def case(self, case_id):
        value = next((c for c in self.catalogue(include_hidden=self.status().get('admin', False)) if c['id'] == case_id), None)
        if value is None:
            raise ValueError('公共案例不存在或尚未获得访问权限，请刷新')
        try:
            records = self.personal()
        except ValueError:
            return dict(value, notes=[], trials=[], personal_error='个人学习记录暂时无法读取，原图仍可查看；请从个人学习备份恢复。')
        return dict(value, notes=[v for v in records['notes'] if v['case_id'] == case_id],
                    trials=[v for v in records['trials'] if v['case_id'] == case_id])

    @network_operation
    def image(self, case_id, image_id, thumbnail=False, cache_only=False):
        case = next((c for c in self.catalogue(include_hidden=self.status().get('admin', False)) if c['id'] == case_id), None)
        if case is None:
            raise ValueError('公共案例不存在或无权访问')
        image = next((i for i in case['images'] if i['id'] == image_id), None)
        if not image:
            raise ValueError('原图不属于当前公共案例')
        if thumbnail and image_id == case['images'][0]['id'] and case.get('cover'):
            image = case['cover']
        def verify_dimensions(data):
            if not thumbnail:
                with Image.open(io.BytesIO(data)) as source:
                    if (source.width != image['width'] or source.height != image['height']
                            or Image.MIME.get(source.format) != image['mime']):
                        raise ValueError('原图尺寸或格式与案例不符，未展示试炼画面')
        path = self.store.root / 'pa_growth' / 'shared_cache' / FOLDER / identifier(image['id'])
        if path.exists():
            if path.stat().st_size == image['bytes']:
                data = path.read_bytes()
                if checksum(data) == image['sha256']:
                    verify_dimensions(data)
                    return data
            # Cache is disposable; replace only after a new download passes
            # the canonical hash. Offline failures leave the old file intact.
        if cache_only:
            raise ConnectionError('此图片尚未缓存，请连接后刷新')
        try:
            data = self.drive.read(image['id'], MAX_IMAGE_BYTES)
        except DriveError as exc:
            if exc.status != 404:
                raise
            # A missing file is not proof that the whole directory was revoked.
            # Propagate confirmed directory denial to network_operation instead.
            self.drive.check_folder()
            raise ValueError('该图片在云端不存在或不可访问，其他公共案例仍可使用') from None
        if len(data) != image['bytes'] or checksum(data) != image['sha256']:
            raise ValueError('云端原图校验失败，已保留原记录')
        image_info(data)
        verify_dimensions(data)
        if not self.status()['cached']:
            raise ValueError('公共缓存访问权限已失效，请重新连接后刷新')
        atomic_secret(path, data)
        return data

    @network_operation
    def collaborate(self, case_id, kind, expected, value):
        if kind not in ('revision', 'comment', 'visibility'):
            raise ValueError('未知协作操作')
        self.refresh()  # Online permission + observed stale-version check.
        status = self.status()
        if not status.get('writable'):
            raise ValueError('当前账号只有只读权限')
        if kind == 'visibility' and not status.get('admin'):
            raise ValueError('只有知识库所有者可以下架或恢复案例')
        case = self.case(case_id)
        stream = 'visibility' if kind == 'visibility' else 'revision'
        if kind != 'comment' and expected != case[stream + '_version']:
            raise ValueError('案例已有新修改，请重新加载后再保存')
        event = dict(version=1, type=kind, case_id=case_id, author=status['user'], created=now())
        if kind == 'comment':
            event['text'] = value
        else:
            event.update(parents=case[stream + '_heads'])
            event['values' if kind == 'revision' else 'hidden'] = value
        comment_key = None
        if kind == 'comment':
            comment_key = f'pa_comment:{FOLDER}:{status["user"]["id"]}:{case_id}:{identifier(expected)}'
            reserved = self.get(comment_key)
        else:
            reserved = None
        file_id = reserved['id'] if reserved else self.drive.allocate(1)[0]
        event = validate_event(event, dict(id=file_id, parents=[FOLDER], owners=[dict(permissionId=status['user']['id'])]),
                               [status['user']['id']] if status.get('admin') else [])
        if comment_key:
            # Reserve the exact event before upload, so a lost acknowledgement
            # retries the same ID and bytes, also across windows/processes.
            self.store.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', (comment_key, dumps(event)))
            reserved = self.get(comment_key)
            if (reserved['text'] != event['text'] or reserved['case_id'] != case_id
                    or reserved['author']['id'] != status['user']['id']):
                raise ValueError('待完成留言的内容已改变，请使用新的提交身份')
            event, file_id = reserved, reserved['id']
        raw = encoded({k: v for k, v in event.items() if k not in ('id', 'cloud_md5')})
        if len(raw) > MAX_MANIFEST:
            raise ValueError('协作记录超过共享上限，尚未提交')
        self.drive.put_once(file_id, 'collaboration-' + file_id + '.json', raw, 'application/json', 'collaboration-v1')
        self.refresh()
        if file_id not in {e['id'] for e in self.get(self.space_key('collaboration'), [])}:
            raise ValueError('记录已上传，但目录刷新尚未确认；请刷新核对后再操作')
        result = self.case(case_id)
        if kind != 'comment' and result[stream + '_conflict']:
            raise ValueError('检测到并发修改，两个版本均已保留；请重新加载并核对历史后保存')
        return dict(case=result)

    @network_operation
    def publish(self, case_id):
        user = self.drive.identity(True)
        if not self.drive.check_folder():
            raise ValueError('当前账号只有只读权限，请申请指定文件夹的编辑权限')
        case = self.store.pa_case(case_id, touch=False)
        terms = selected([i for i in case['topic_ids'] if i in IDS], required=True)
        # Only original case description is public. Personal notes, trial
        # judgments, study progress and legacy topics are never serialized.
        value = {k: case[k] for k in ('title', 'memory', 'keywords', 'tags', 'symbol', 'timeframe', 'market', 'data_kind', 'created', 'updated')}
        value.update(version=1, uuid=case_id, contributor=user, term_ids=terms)
        try:
            setup = self.store.pa_trial_setup(case_id)
        except ValueError:
            raise ValueError('试炼设置损坏，请先重新设置；原私密案例仍在') from None
        snapshot = checksum(encoded(dict(value, assets=[dict(i) for i in case['images']], trial_setup=setup)))
        key = f'pa_publish:{FOLDER}:{_owner(self.store)}:{case_id}'
        journal = self.get(key)
        if journal and (journal['user'] != user['id'] or journal['snapshot'] != snapshot):
            raise ValueError('这个案例已有不同的发布快照或贡献账号；请保留原提交，另存案例后再提交')
        if not journal:
            ids = self.drive.allocate(len(case['images']) + 2)
            if len(ids) != len(case['images']) + 2 or len(set(ids)) != len(ids):
                raise ValueError('Google 未分配有效文件身份')
            proposed = dict(user=user['id'], snapshot=snapshot, manifest=ids[0], cover=ids[1],
                            images={im['id']: remote for im, remote in zip(case['images'], ids[2:])})
            # A shared local runtime can have two windows. INSERT OR IGNORE
            # fixes the one durable reservation before either writes a file.
            self.store.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', (key, dumps(proposed)))
            journal = self.get(key)
            if journal['user'] != user['id'] or journal['snapshot'] != snapshot:
                raise ValueError('另一个窗口已保留不同发布，请刷新后检查')
        public_images, cover = [], None
        for i, image in enumerate(case['images']):
            data = self.store.pa_image(image['id'])
            remote_id = journal['images'][image['id']]
            self.drive.put_once(remote_id, f'image-{remote_id}', data, image['mime'], 'image-v1')
            public_images.append(dict(image, id=remote_id))
            if i == 0:
                with Image.open(io.BytesIO(data)) as photo:
                    photo.thumbnail((640, 400))
                    output = io.BytesIO()
                    photo.convert('RGB').save(output, 'JPEG', quality=88)
                thumb = output.getvalue()
                self.drive.put_once(journal['cover'], 'cover-' + journal['cover'], thumb, 'image/jpeg', 'cover-v1')
                cover = dict(id=journal['cover'], bytes=len(thumb), sha256=checksum(thumb))
        if setup:
            setup = dict(setup, image_ids=[journal['images'][v] for v in setup['image_ids']])
        value.update(images=public_images, cover=cover, trial_setup=setup)
        raw = encoded(value)
        if len(raw) > MAX_MANIFEST:
            raise ValueError('案例资料超过共享上限，尚未发布')
        self.drive.put_once(journal['manifest'], 'case-' + case_id + '.json', raw, 'application/json', 'case-v1')
        info = self.drive.info(journal['manifest'])
        verified = validate_manifest(json.loads(self.drive.read(journal['manifest'], MAX_MANIFEST)), info)
        # A committed cloud case is successful even if a subsequent local
        # refresh fails. Return the immutable ID, not a fake sync of all files.
        journal['committed'] = now()
        self.set(key, journal)
        return dict(published=True, case_id=verified['id'], uuid=case_id)

    def personal(self):
        user = self.drive.identity()
        raw = self.get('pa_personal:' + identifier(user['id']))
        if raw is None:
            return dict(owner=user['id'], notes=[], trials=[], topic_notes=[], studied=[], visited=[])
        value = json.loads(protect(base64.b64decode(raw, validate=True), True))
        if value['owner'] != user['id']:
            raise ValueError('个人学习记录身份不匹配')
        value.setdefault('visited', [])
        return value

    def record_case(self, case_id):
        return self.case(case_id) if str(case_id).startswith('shared:') else self.store.pa_case(case_id, touch=False)

    def record(self, kind, case_id, value):
        if kind not in ('notes', 'trials', 'topic_notes', 'studied', 'visited'):
            raise ValueError('学习记录类型无效')
        user = self.drive.identity()
        if kind == 'topic_notes':
            selected([case_id], required=True)
        else:
            self.record_case(case_id)
        if kind == 'trials':
            if not isinstance(value, dict) or set(value) - {'judgment', 'depth', 'details'}:
                raise ValueError('判断记录包含未知字段')
            case = self.record_case(case_id)
            setup = case.get('trial_setup') if case.get('status') == 'shared' else self.store.pa_trial_setup(case_id)
            if not setup or value.get('details', {}).get('presentation') != setup:
                raise ValueError('试炼设置已改变，请重新开始')
            if value.get('judgment') not in ('consider', 'wait', 'pass') or value.get('depth') not in ('quick', 'structure', 'deep'):
                raise ValueError('请先完成基本判断')
            if set(value.get('details', {})) - {'source', 'presentation', 'thought', 'marks', 'image_id'}:
                raise ValueError('判断记录包含未知字段')
            details = value.get('details', {})
            if 'thought' in details and (not isinstance(details['thought'], str) or len(details['thought']) > 20_000):
                raise ValueError('判断文字无效')
            marks = details.get('marks', [])
            if not isinstance(marks, list) or len(marks) > 100:
                raise ValueError('判断位置无效')
            for mark in marks:
                if (not isinstance(mark, dict) or mark.get('image_id') not in setup['image_ids']
                        or any(type(mark.get(k)) not in (int, float) or not 0 <= mark[k] <= 1 for k in ('x', 'y'))):
                    raise ValueError('判断位置不属于此试炼')
        elif kind not in ('studied', 'visited') and (not isinstance(value, str) or not value.strip() or len(value) > 20_000):
            raise ValueError('请填写有效研究记录')
        item = dict(id=uuid.uuid4().hex, created=now(), author='我', case_id=case_id)
        if kind == 'trials':
            item.update(value)
        elif kind == 'topic_notes':
            item.update(topic_id=case_id, text=value)
            item.pop('case_id')
        elif kind == 'notes':
            item['text'] = value
        if len(encoded(item)) > 32_000:
            raise ValueError('学习记录过长')
        # Serialize read-modify-write across independent windows too.
        with self.store.atomic_write():
            records = self.personal()
            if records['owner'] != user['id']:
                raise SessionChanged()
            records[kind].append(item)
            if kind == 'visited':
                records[kind] = records[kind][-100:]
            if len(encoded(records)) > 20 * 1024 * 1024:
                raise ValueError('个人学习记录超过本版容量，请先备份联系维护者；原记录已保留')
            raw = protect(encoded(records))
            self.set('pa_personal:' + user['id'], base64.b64encode(raw).decode())
        return item

    def export_learning(self, path):
        records = self.personal()
        atomic_secret(Path(path), protect(encoded(records)))
        return {'saved': True}

    def restore_learning(self, path):
        path = Path(path)
        if path.stat().st_size > 24 * 1024 * 1024:
            raise ValueError('个人学习备份超过上限')
        value = json.loads(protect(path.read_bytes(), True))
        user = self.drive.identity()
        if not isinstance(value, dict) or value.get('owner') != user['id']:
            raise ValueError('此个人学习备份不属于当前 Google 账号')
        kinds = ('notes', 'trials', 'topic_notes', 'studied', 'visited')
        if set(value) - {'owner', *kinds}:
            raise ValueError('个人学习备份字段无效')
        for kind in kinds:
            rows = value.get(kind, [])
            if not isinstance(rows, list) or len(rows) > 20_000:
                raise ValueError('个人学习备份记录无效')
            for row in rows:
                if (not isinstance(row, dict) or not re.fullmatch(r'[a-f0-9]{32}', row.get('id', ''))
                        or not isinstance(row.get('created'), str) or len(encoded(row)) > 32_000
                        or not isinstance(row.get('topic_id' if kind == 'topic_notes' else 'case_id'), str)
                        or (kind in ('notes', 'topic_notes') and not isinstance(row.get('text'), str))):
                    raise ValueError('个人学习备份条目无效')
                if kind == 'trials' and (row.get('judgment') not in ('consider', 'wait', 'pass')
                                          or row.get('depth') not in ('quick', 'structure', 'deep')
                                          or not isinstance(row.get('details'), dict)):
                    raise ValueError('个人判断备份无效')
        isolated = False
        with self.store.atomic_write():
            try:
                records = self.personal()
            except SessionChanged:
                raise
            except ValueError:
                # Explicit recovery from a validated own-account backup may
                # replace unreadable active bytes, while retaining them intact.
                source_key = 'pa_personal:' + user['id']
                rows = self.store.rows('SELECT value FROM meta WHERE key=?', (source_key,))
                if rows:
                    self.store.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                        ('pa_personal_issue:' + user['id'] + ':' + uuid.uuid4().hex, rows[0]['value']))
                records = dict(owner=user['id'], **{kind: [] for kind in kinds})
                isolated = True
            if records['owner'] != user['id']:
                raise SessionChanged()
            added = 0
            for kind in kinds:
                known = {r['id']: r for r in records[kind]}
                for row in value.get(kind, []):
                    if row['id'] in known and known[row['id']] != row:
                        raise ValueError('个人学习记录身份冲突，已保留原记录')
                    if row['id'] not in known:
                        records[kind].append(row)
                        known[row['id']] = row
                        added += 1
            if len(encoded(records)) > 20 * 1024 * 1024:
                raise ValueError('合并后个人学习记录超过上限，已保留原记录')
            self.set('pa_personal:' + user['id'], base64.b64encode(protect(encoded(records))).decode())
        return {'restored': added, 'isolated_unreadable': isolated}


def validate_event(value, info, folder_owners):
    """Only public fields cross this boundary; never serialize personal records."""
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('type') not in ('revision', 'comment', 'visibility'):
        raise ValueError('公共协作记录格式无效')
    if FOLDER not in info.get('parents', []) or info.get('trashed'):
        raise ValueError('公共协作记录不在指定目录')
    author = value.get('author', {})
    if (not isinstance(author, dict) or author.get('id') not in [v.get('permissionId') for v in info.get('owners', [])]
            or not isinstance(author.get('name'), str) or len(author['name']) > 300):
        raise ValueError('公共协作成员身份无法核实')
    identifier(author['id'])
    case_id = value.get('case_id', '')
    if not isinstance(case_id, str) or not case_id.startswith('shared:'):
        raise ValueError('公共协作案例身份无效')
    identifier(case_id[7:])
    if not isinstance(value.get('created'), str) or not 1 <= len(value['created']) <= 80:
        raise ValueError('公共协作时间无效')
    result = dict(version=1, id=identifier(info['id']), type=value['type'], case_id=case_id,
                  author={k: author[k] for k in ('id', 'name')}, created=value['created'], cloud_md5=info.get('md5Checksum'))
    if value['type'] == 'comment':
        text = value.get('text')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 10000:
            raise ValueError('请填写 1—10000 字的纯文本留言')
        result['text'] = text.strip()
    else:
        parents = value.get('parents')
        if not isinstance(parents, list) or len(parents) > MAX_CASES or len(set(parents)) != len(parents):
            raise ValueError('公共协作版本无效')
        result['parents'] = sorted(identifier(p) for p in parents)
        if value['type'] == 'revision':
            fields = value.get('values')
            if not isinstance(fields, dict) or set(fields) != {'title', 'memory', 'keywords', 'term_ids'}:
                raise ValueError('只能修改标题、案例描述、关键词和标准术语')
            clean = metadata({k: fields[k] for k in ('title', 'memory', 'keywords')})
            result['values'] = {k: clean[k] for k in ('title', 'memory', 'keywords')}
            result['values']['term_ids'] = selected(fields['term_ids'], required=True)
        else:
            if author['id'] not in folder_owners or type(value.get('hidden')) is not bool:
                raise ValueError('案例上下架记录无法核实知识库所有者身份')
            result['hidden'] = value['hidden']
    return result


def resolve_events(events):
    """Append-only heads: siblings stay visible as conflicts, never last-writer-wins.

    Drive has no create-if-head CAS. A later explicit full revision with all
    observed heads as parents resolves a conflict without deleting either one.
    """
    pending = {e['id']: e for e in events}
    ancestors = {}
    while pending:
        ready = [e for e in pending.values() if all(p in ancestors for p in e['parents'])]
        if not ready:
            raise ValueError('公共修订历史不完整或版本循环，请核对共享记录')
        for event in ready:
            ancestors[event['id']] = {event['id']}.union(*(ancestors[p] for p in event['parents']))
            del pending[event['id']]
    heads = sorted(set(ancestors) - {p for e in events for p in e['parents']})
    common = set.intersection(*(ancestors[h] for h in heads)) if heads else set()
    # A criss-cross merge can have multiple incomparable common ancestors.
    # They are still disputed: recurse to their shared past instead of choosing
    # one by ID. A single explicit resolution head remains authoritative.
    current = None
    while common:
        latest = common - set().union(*(ancestors[i] - {i} for i in common))
        if len(latest) == 1:
            current = next(iter(latest))
            break
        common = set.intersection(*(ancestors[i] for i in latest))
    return heads, current


def compose_case(original, events):
    rows = sorted((e for e in events if e['case_id'] == original['id']), key=lambda e: (e['created'], e['id']))
    result = dict(original, comments=[e for e in rows if e['type'] == 'comment'], hidden=False,
                  history=[e for e in rows if e['type'] == 'revision'])
    for kind in ('revision', 'visibility'):
        changes = [e for e in rows if e['type'] == kind]
        heads, current = resolve_events(changes)
        result[kind + '_heads'] = heads
        result[kind + '_version'] = checksum(encoded(heads))
        result[kind + '_conflict'] = len(heads) > 1
        if kind == 'revision' and current:
            event = next(e for e in changes if e['id'] == current)
            result.update(event['values'], updated=event['created'])
        if kind == 'visibility':
            # Conflicting admin actions fail closed until the owner resolves.
            result['hidden'] = any(e['hidden'] for e in changes if e['id'] in heads)
    result['topic_ids'] = result['term_ids']
    result['original_metadata'] = {k: original[k] for k in ('title', 'memory', 'keywords', 'term_ids')}
    if result['hidden']:
        result['trial_setup'] = None
    return result


def validate_manifest(value, info):
    if not isinstance(value, dict) or value.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', value.get('uuid', '')):
        raise ValueError('公共案例格式或版本无效')
    if FOLDER not in info.get('parents', []) or info.get('trashed'):
        raise ValueError('公共案例不在指定目录')
    author = value.get('contributor', {})
    if (not isinstance(author, dict) or author.get('id') not in [v.get('permissionId') for v in info.get('owners', [])]
            or not isinstance(author.get('name'), str) or len(author['name']) > 300):
        raise ValueError('公共案例贡献身份无法核实')
    identifier(author['id'])
    terms = selected(value.get('term_ids'), required=True)
    meta = metadata({k: value[k] for k in ('title', 'memory', 'keywords', 'tags', 'symbol', 'timeframe', 'market', 'data_kind')})
    images = value.get('images')
    if not isinstance(images, list) or not 1 <= len(images) <= 100:
        raise ValueError('公共案例图片数量无效')
    ids, total = set(), 0
    for image in images:
        identifier(image['id'])
        if image['id'] in ids or not re.fullmatch('[a-f0-9]{64}', image.get('sha256', '')):
            raise ValueError('公共案例图片校验资料无效')
        ids.add(image['id'])
        if type(image['bytes']) is not int or not 0 < image['bytes'] <= MAX_IMAGE_BYTES:
            raise ValueError('公共案例图片容量无效')
        if (type(image.get('width')) is not int or type(image.get('height')) is not int
                or image['width'] <= 0 or image['height'] <= 0
                or not 0 < image['width'] * image['height'] <= 50_000_000
                or image.get('mime') not in ('image/png', 'image/jpeg', 'image/webp')
                or not isinstance(image.get('name'), str) or len(image['name']) > 500):
            raise ValueError('公共案例图片信息无效')
        total += image['bytes']
    if total > MAX_CASE_BYTES:
        raise ValueError('公共案例原图总量超过上限')
    for key in ('created', 'updated'):
        if not isinstance(value.get(key), str) or len(value[key]) > 80:
            raise ValueError('公共案例时间无效')
    cover = value.get('cover')
    if not isinstance(cover, dict) or not 0 < cover.get('bytes', 0) <= 1024 * 1024 or not re.fullmatch('[a-f0-9]{64}', cover.get('sha256', '')):
        raise ValueError('公共案例缩略图无效')
    identifier(cover['id'])
    setup = value.get('trial_setup')
    if setup is not None:
        setup = validate_trial(setup, ids)
    clean_images = [{k: image[k] for k in ('id', 'name', 'sha256', 'mime', 'width', 'height', 'bytes')} for image in images]
    return dict(meta, id='shared:' + identifier(info['id']), global_id=value['uuid'],
                contributor={'id': author['id'], 'name': author['name']},
                term_ids=terms, topic_ids=terms, created=value['created'], updated=value['updated'],
                images=clean_images, image_count=len(images), cover={k: cover[k] for k in ('id', 'bytes', 'sha256')}, trial_setup=setup, status='shared',
                cloud_md5=info.get('md5Checksum'))


def dispatch(store, operation, *args, drive=None):
    workspace = Workspace(store, drive or GoogleDrive(profile=store.root))
    if operation not in ('status', 'refresh', 'catalogue', 'search', 'case', 'image', 'publish', 'personal', 'record',
                         'export_learning', 'restore_learning', 'collaborate'):
        raise ValueError('未知共享操作')
    session = getattr(workspace.drive, 'session', None)
    with session.operation() if isinstance(session, GoogleSession) else nullcontext():
        return getattr(workspace, operation)(*args)
