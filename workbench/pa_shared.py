"""One Drive authority; immutable case commits, disposable cache, private learning.

Called only through Store.pa_shared. No SQLite file is uploaded. A manifest is
the commit marker: incomplete original uploads never appear as public cases.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import uuid
from urllib.parse import urlencode, urlparse

from PIL import Image

from .pa_auth import GoogleSession, DriveError, request, protect, atomic_secret
from .pa_growth import now, metadata, image_info, _owner, MAX_CASE_BYTES, MAX_IMAGE_BYTES
from .pa_terms import IDS, TERMS, selected, matching, graph
from .pa_trial import validate as validate_trial
from .store import dumps

FOLDER = '1r_wPMsPboY9BXZ_k4X2dtKULVXcwE7YW'
MAX_MANIFEST = 256 * 1024
MAX_CASES = 20_000
FIELDS = 'id,name,mimeType,parents,trashed,size,owners(permissionId),properties,capabilities(canAddChildren)'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', value):
        raise ValueError('云端文件身份无效')
    return value


def encoded(value):
    return dumps(value).encode('utf-8')


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class GoogleDrive:
    def __init__(self, session=None):
        self.session = session or GoogleSession()

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
        return info.get('capabilities', {}).get('canAddChildren', False)

    def allocate(self, count):
        raw, _ = self.call('files/generateIds?' + urlencode({'count': count, 'space': 'drive', 'type': 'files'}))
        return [identifier(v) for v in json.loads(raw)['ids']]

    def list_cases(self):
        rows, token = [], ''
        while True:
            params = {'q': f"'{FOLDER}' in parents and trashed=false and properties has {{ key='aplus_kind' and value='case-v1' }}",
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

    def status(self):
        try:
            user = self.drive.identity()
        except ValueError:
            return dict(connected=False, folder_id=FOLDER, cached=False)
        access = self.get('pa_shared_access:' + user['id'], {})
        return dict(connected=True, user=user, folder_id=FOLDER, cached=bool(access.get('granted')),
                    refreshed=access.get('refreshed'), writable=access.get('writable', False))

    def refresh(self):
        user = self.drive.identity(True)
        key = 'pa_shared_access:' + user['id']
        try:
            writable = self.drive.check_folder()
            rows = self.drive.list_cases()
            cases = []
            for row in rows:
                raw = self.drive.read(row['id'], MAX_MANIFEST)
                value = validate_manifest(json.loads(raw), row)
                cases.append(value)
        except DriveError as exc:
            if exc.status in (401, 403, 404):
                self.set(key, {'granted': False})
            raise
        # Replace snapshot only after the entire catalogue validates. Another
        # client's concurrent commits appear on the next explicit refresh.
        cases.sort(key=lambda c: (c['updated'], c['id']), reverse=True)
        self.set('pa_shared_catalogue', cases)
        self.set(key, dict(granted=True, writable=writable, refreshed=now()))
        return dict(count=len(cases), status=self.status())

    def catalogue(self):
        status = self.status()
        if not status['connected'] or not status['cached']:
            return []
        return self.get('pa_shared_catalogue', [])

    def search(self, query='', filters=None, offset=0):
        filters = filters or {}
        if type(offset) is not int or offset < 0 or not isinstance(query, str) or len(query) > 1000:
            raise ValueError('搜索条件无效')
        if set(filters) - {'symbol', 'market', 'timeframe', 'data_kind', 'topic_id', 'contributor', 'scope'}:
            raise ValueError('未知公共案例筛选')
        q = query.casefold().strip()
        terms = set(matching(q)) if q else set()
        user = self.status().get('user', {})
        results = []
        for c in self.catalogue():
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
            if q and not reasons:
                continue
            results.append(dict(c, match_reasons=reasons))
        return dict(cases=results[offset:offset + 100], has_more=len(results) > offset + 100,
                    next_offset=offset + min(100, max(0, len(results) - offset)))

    def case(self, case_id):
        value = next((c for c in self.catalogue() if c['id'] == case_id), None)
        if value is None:
            raise ValueError('公共案例不存在或尚未获得访问权限，请刷新')
        records = self.personal()
        return dict(value, notes=[v for v in records['notes'] if v['case_id'] == case_id],
                    trials=[v for v in records['trials'] if v['case_id'] == case_id])

    def image(self, case_id, image_id, thumbnail=False):
        case = self.case(case_id)
        image = next((i for i in case['images'] if i['id'] == image_id), None)
        if not image:
            raise ValueError('原图不属于当前公共案例')
        if thumbnail and case.get('cover'):
            image = case['cover']
        path = self.store.root / 'pa_growth' / 'shared_cache' / identifier(image['id'])
        if path.exists():
            if path.stat().st_size != image['bytes']:
                raise ValueError('缓存原图校验失败，请刷新后重新下载')
            data = path.read_bytes()
            if checksum(data) == image['sha256']:
                return data
            raise ValueError('缓存原图校验失败，请刷新后重新下载')
        data = self.drive.read(image['id'], MAX_IMAGE_BYTES)
        if len(data) != image['bytes'] or checksum(data) != image['sha256']:
            raise ValueError('云端原图校验失败，已保留原记录')
        image_info(data)
        atomic_secret(path, data)
        return data

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
        key = f'pa_publish:{_owner(self.store)}:{case_id}'
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
            return dict(owner=user['id'], notes=[], trials=[], topic_notes=[], studied=[])
        value = json.loads(protect(base64.b64decode(raw, validate=True), True))
        if value['owner'] != user['id']:
            raise ValueError('个人学习记录身份不匹配')
        return value

    def record_case(self, case_id):
        return self.case(case_id) if str(case_id).startswith('shared:') else self.store.pa_case(case_id, touch=False)

    def record(self, kind, case_id, value):
        if kind not in ('notes', 'trials', 'topic_notes', 'studied'):
            raise ValueError('学习记录类型无效')
        user = self.drive.identity()
        if kind == 'topic_notes':
            selected([case_id], required=True)
        else:
            self.record_case(case_id)
        if kind == 'trials':
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
        elif kind != 'studied' and (not isinstance(value, str) or not value.strip() or len(value) > 20_000):
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
            records[kind].append(item)
            raw = protect(encoded(records))
            self.set('pa_personal:' + user['id'], base64.b64encode(raw).decode())
        return item


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
    selected(value.get('term_ids'), required=True)
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
    return dict(meta, id='shared:' + identifier(info['id']), global_id=value['uuid'], contributor=author,
                term_ids=value['term_ids'], topic_ids=value['term_ids'], created=value['created'], updated=value['updated'],
                images=images, image_count=len(images), cover=cover, trial_setup=setup, status='shared')


def dispatch(store, operation, *args, drive=None):
    workspace = Workspace(store, drive or GoogleDrive())
    if operation not in ('status', 'refresh', 'catalogue', 'search', 'case', 'image', 'publish', 'personal', 'record'):
        raise ValueError('未知共享操作')
    return getattr(workspace, operation)(*args)
