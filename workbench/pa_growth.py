"""Private, local PA research persistence behind Store's public methods.

No OCR guesses, trading writes, network calls, scoring or team impersonation.
The search catalogue is derived directly from committed case rows, so it can
always be rebuilt by reopening the database; originals remain immutable bytes.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import uuid
import warnings
import zipfile

from PIL import Image

from .store import digest, dumps


MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_CASE_IMAGES = 100
MAX_CASE_BYTES = 128 * 1024 * 1024
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
MAX_BACKUP_RECORDS = 20_000
MAX_BACKUP_MANIFEST_BYTES = 20 * 1024 * 1024
FORMATS = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'), 'WEBP': ('image/webp', '.webp')}
TEXT_FIELDS = ('title', 'memory', 'symbol', 'timeframe', 'market')
LIST_FIELDS = ('keywords', 'tags', 'topic_ids')
TOPIC_IDS = frozenset(('root context continuation range reversal structure discipline '
    'context-trend context-range context-transition context-mtf '
    'cont-h2 cont-h1 cont-breakout cont-pullback cont-gap cont-mm '
    'range-boundary range-fail range-middle rev-mtr rev-climax rev-wedge rev-double '
    'struct-signal struct-tail struct-inside struct-outside struct-slope '
    'risk-entry risk-sl risk-exposure risk-exit').split())


def now():
    # A trader can inspect several cases within one second. Preserve the
    # actual order of visits and append-only research without touching the
    # timestamp contract of existing trading records.
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _text(value, limit=20_000):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError('文字内容无效或过长')
    return value.strip()


def _list(value, *, topics=False):
    if isinstance(value, str):
        value = value.replace('，', ',').replace('；', ',').replace(';', ',').split(',')
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('关键词或主题数量过多')
    result = list(dict.fromkeys(_text(item, 200) for item in value if item))
    result = [item for item in result if item]
    if topics and not set(result).issubset(TOPIC_IDS):
        raise ValueError('未知知识主题')
    return result


def metadata(value=None, *, partial=False):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('案例资料无效')
    if set(value) - set(TEXT_FIELDS + LIST_FIELDS + ('data_kind',)):
        raise ValueError('案例包含不支持的字段')
    result = {key: _text(value.get(key, '')) for key in TEXT_FIELDS if key in value or not partial}
    result.update({key: _list(value.get(key, []), topics=key == 'topic_ids')
                   for key in LIST_FIELDS if key in value or not partial})
    if 'data_kind' in value or not partial:
        if value.get('data_kind', 'imported') not in ('imported', 'test'):
            raise ValueError('只能保存导入截图或明确标识的测试样本')
        result['data_kind'] = value.get('data_kind', 'imported')
    return result


def image_info(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('图片为空或超过 40 MB')
    result = _image_details(lambda: io.BytesIO(data))
    return dict(result, sha256=digest(data), bytes=len(data))


def _image_details(source):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source()) as image:
                if image.format not in FORMATS:
                    raise ValueError('仅支持 PNG、JPEG 或 WebP 图片')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError('图片尺寸超过 5000 万像素')
                mime, suffix = FORMATS[image.format]
                size = image.size
                image.verify()
            # verify() checks container integrity; decoding also catches a
            # truncated JPEG stream that would otherwise fail in the viewer.
            with Image.open(source()) as image:
                image.load()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('图片无法读取，请重新复制或选择原图') from exc
    return {'mime': mime, 'suffix': suffix, 'width': size[0], 'height': size[1]}


def _owner(store):
    # The local profile is owned by this data home. Requests never choose it.
    rows = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")
    if rows:
        return rows[0]['value']
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO meta VALUES('pa_local_owner',?)", (uuid.uuid4().hex,))
        return db.execute("SELECT value FROM meta WHERE key='pa_local_owner'").fetchone()[0]


def _decode(row):
    value = dict(row)
    for key in LIST_FIELDS:
        if key in value:
            value[key] = json.loads(value[key])
    value.pop('owner', None)
    return value


def case_metadata(store):
    """Statistics/labels need no image catalogue or original image reads."""
    owner = _owner(store)
    rows = store.rows("SELECT id,title,memory,symbol,topic_ids,data_kind FROM pa_cases "
                      "WHERE owner=? AND status='private_draft'", (owner,))
    return [_decode(row) for row in rows]


def _case_row(store, case_id, owner):
    rows = store.rows("SELECT * FROM pa_cases WHERE id=? AND owner=? AND status='private_draft'", (case_id, owner))
    if not rows:
        raise ValueError('案例不存在或无权访问')
    return _decode(rows[0])


def _images(store, case_id, owner):
    return store.rows('SELECT a.id,a.name,a.sha256,a.mime,a.width,a.height,a.bytes,a.created '
                      'FROM pa_assets a JOIN pa_case_images i ON i.asset_id=a.id '
                      'JOIN pa_cases c ON c.id=i.case_id '
                      "WHERE c.id=? AND c.owner=? AND a.owner=? AND c.status='private_draft' ORDER BY i.ordinal",
                      (case_id, owner, owner))


def _summary(store, row, owner):
    result = _decode(row)
    result['images'] = _images(store, result['id'], owner)
    result['image_count'] = len(result['images'])
    result['asset_id'] = result['images'][0]['id'] if result['images'] else None
    return result


def find_duplicates(store, data):
    owner = _owner(store)
    checksum = digest(data)
    rows = store.rows('SELECT DISTINCT c.* FROM pa_cases c JOIN pa_case_images i ON i.case_id=c.id '
                      'JOIN pa_assets a ON a.id=i.asset_id WHERE c.owner=? AND a.owner=? '
                      "AND c.status='private_draft' AND a.sha256=? ORDER BY c.created DESC", (owner, owner, checksum))
    return [_summary(store, row, owner) for row in rows]


def _asset_path(store, owner, relative):
    base = (store.root / 'pa_growth' / 'originals' / owner).resolve()
    path = (store.root / relative).resolve()
    if not base.is_relative_to(store.root.resolve()) or not path.is_relative_to(base) or path == base:
        raise ValueError('原图路径无效')
    return path


def _write_original(store, owner, identifier, data, info):
    relative = ('pa_growth/originals/' + owner + '/' + identifier + info['suffix'])
    path = _asset_path(store, owner, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.pa-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # UUID destinations are never reused. A failed DB commit may leave an
        # unreferenced immutable file, but cannot expose a partly saved case.
        if path.exists():
            raise ValueError('原图保存位置已存在，请重新保存')
        os.replace(temporary, path)
        created = getattr(store._atomic_local, 'pa_created', None)
        if created is not None:
            created.append((owner, relative, info['sha256'], info['bytes']))
    finally:
        temporary.unlink(missing_ok=True)
    return relative


def _file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def _cleanup_created(store, created):
    """Best effort only after this PA scope has left its actual transaction.

    A UUID alone is not deletion proof. Keep a file whenever references, path,
    bytes, or file identity cannot be checked, including an uncertain commit.
    """
    with store._files_lock:
        for owner, relative, checksum, length in created:
            try:
                candidate = store.root / relative
                resolved = _asset_path(store, owner, relative)
                if candidate.is_symlink() or candidate.absolute() != resolved:
                    continue
                before = candidate.stat()
                if not stat.S_ISREG(before.st_mode) or before.st_size != length:
                    continue
                if _file_digest(candidate) != checksum:
                    continue
                # Store.atomic_write has already cleared its active connection
                # and completed rollback/commit before cleanup can reach here.
                with store.connect() as db:
                    referenced = db.execute('SELECT 1 FROM pa_assets WHERE path=? LIMIT 1', (relative,)).fetchone()
                if referenced:
                    continue
                after = candidate.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    continue
                candidate.unlink()
            except Exception:
                # Never mask the save failure or sacrifice an uncertain file.
                continue


@contextlib.contextmanager
def write_scope(store):
    """PA-only file ownership around the existing Store transaction contract."""
    if getattr(store._atomic_local, 'pa_created', None) is not None:
        yield
        return
    if getattr(store._atomic_local, 'connection', None) is not None:
        # A generic caller owns the final commit. Do not guess its outcome.
        yield
        return
    created = []
    store._atomic_local.pa_created = created
    try:
        try:
            with store.atomic_write():
                yield
        except BaseException:
            _cleanup_created(store, created)
            raise
    finally:
        del store._atomic_local.pa_created


def _check_case_capacity(count, size):
    if count > MAX_CASE_IMAGES or size > MAX_CASE_BYTES:
        raise ValueError('同一案例最多保存 100 张图片、总计 128 MB；请另存为新案例')


def save_case(store, images, value=None, *, case_id=None, allow_duplicate=False):
    value = metadata(value, partial=bool(case_id))
    if not isinstance(images, list) or not images or len(images) > 100:
        raise ValueError('请选择 1 至 100 张图片')
    _check_case_capacity(len(images), sum(len(item.get('data', b'')) for item in images
                                         if isinstance(item, dict) and isinstance(item.get('data'), bytes)))
    prepared = []
    for item in images:
        if not isinstance(item, dict):
            raise ValueError('图片资料无效')
        data = item.get('data')
        info = image_info(data)
        name = _text(item.get('name', '截图'), 500)
        prepared.append((data, info, Path(name).name or '截图'))
    owner = _owner(store)
    if case_id:
        _case_row(store, case_id, owner)
    with write_scope(store), store._files_lock:
        duplicates = {item['id']: item for data, _, _ in prepared for item in find_duplicates(store, data)}
        repeated_batch = len({info['sha256'] for _, info, _ in prepared}) < len(prepared)
        if (duplicates or repeated_batch) and not allow_duplicate:
            return {'saved': False, 'duplicates': list(duplicates.values()), 'repeated_batch': repeated_batch}
        timestamp = now()
        identifier = case_id or uuid.uuid4().hex
        assets = []
        for data, info, name in prepared:
            asset_id = uuid.uuid4().hex
            relative = _write_original(store, owner, asset_id, data, info)
            assets.append((asset_id, owner, info['sha256'], relative, name, info['mime'],
                           info['width'], info['height'], info['bytes'], timestamp))
        with store.connect() as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            if case_id:
                _case_row(store, case_id, owner)
                existing = db.execute('SELECT COUNT(*) AS n,COALESCE(SUM(a.bytes),0) AS size '
                                      'FROM pa_assets a JOIN pa_case_images i ON i.asset_id=a.id '
                                      'WHERE i.case_id=? AND a.owner=?', (case_id, owner)).fetchone()
                _check_case_capacity(existing['n'] + len(prepared), existing['size'] + sum(info['bytes'] for _, info, _ in prepared))
            else:
                _check_case_capacity(len(prepared), sum(info['bytes'] for _, info, _ in prepared))
                db.execute('INSERT INTO pa_cases(id,owner,status,title,memory,keywords,tags,topic_ids,symbol,timeframe,market,data_kind,created,updated) '
                           "VALUES(?,?,'private_draft',?,?,?,?,?,?,?,?,?,?,?)",
                           (identifier, owner, value['title'], value['memory'], dumps(value['keywords']),
                            dumps(value['tags']), dumps(value['topic_ids']), value['symbol'], value['timeframe'], value['market'],
                            value['data_kind'], timestamp, timestamp))
            ordinal = db.execute('SELECT COALESCE(MAX(ordinal),-1)+1 FROM pa_case_images WHERE case_id=?', (identifier,)).fetchone()[0]
            for index, asset in enumerate(assets, ordinal):
                db.execute('INSERT INTO pa_assets VALUES(?,?,?,?,?,?,?,?,?,?)', asset)
                db.execute('INSERT INTO pa_case_images VALUES(?,?,?)', (identifier, asset[0], index))
            db.execute('UPDATE pa_cases SET updated=? WHERE id=? AND owner=?', (timestamp, identifier, owner))
            if case_id and value:
                columns = list(value)
                parameters = [dumps(value[key]) if key in LIST_FIELDS else value[key] for key in columns]
                db.execute('UPDATE pa_cases SET ' + ','.join(key + '=?' for key in columns) + ' WHERE id=? AND owner=?',
                           (*parameters, case_id, owner))
    return {'saved': True, 'case': read_case(store, identifier, touch=False)}


def search(store, query='', filters=None, limit=100, offset=0):
    query = _text(query, 1000).casefold()
    if not isinstance(filters or {}, dict):
        raise ValueError('筛选条件无效')
    filters = filters or {}
    allowed = {'symbol', 'timeframe', 'market', 'tag', 'topic_id', 'data_kind'}
    if set(filters) - allowed:
        raise ValueError('未知筛选条件')
    if not isinstance(limit, int) or not 1 <= limit <= 1000 or not isinstance(offset, int) or offset < 0:
        raise ValueError('结果分页无效')
    owner = _owner(store)
    rows = store.rows("SELECT * FROM pa_cases WHERE owner=? AND status='private_draft' ORDER BY updated DESC,id DESC", (owner,))
    note_hits = set()
    if query:
        note_hits = {note['case_id'] for note in store.rows('SELECT case_id,text FROM pa_notes WHERE owner=?', (owner,))
                     if query in note['text'].casefold()}
    results = []
    matched = 0
    labels = {'title': '标题', 'memory': '记忆描述', 'keywords': '个人关键词', 'tags': '已确认 PA 标签',
              'symbol': '品种', 'timeframe': '周期', 'market': '市场'}
    for row in rows:
        item = _decode(row)
        if any((_text(str(value), 1000).casefold() not in
                ([entry.casefold() for entry in item['tags']] if key == 'tag' else
                 item['topic_ids'] if key == 'topic_id' else [item.get(key, '').casefold()]))
               for key, value in filters.items() if value):
            continue
        reasons = [label for key, label in labels.items()
                   if query and query in (' '.join(item[key]) if key in LIST_FIELDS else item[key]).casefold()]
        if query and not reasons:
            # Appended research notes are also deliberate personal text.
            if item['id'] in note_hits:
                reasons = ['研究笔记']
            else:
                continue
        matched += 1
        if matched <= offset:
            continue
        item['match_reasons'] = reasons
        results.append(item)
        if len(results) == limit:
            break
    # Fetch only the displayed page's image metadata in one owner-scoped read.
    if results:
        ids = [item['id'] for item in results]
        images = store.rows('SELECT i.case_id,a.id,a.name,a.sha256,a.mime,a.width,a.height,a.bytes,a.created '
                            'FROM pa_assets a JOIN pa_case_images i ON i.asset_id=a.id '
                            'JOIN pa_cases c ON c.id=i.case_id WHERE c.owner=? AND a.owner=? '
                            "AND c.status='private_draft' AND c.id IN (" + ','.join('?' for _ in ids) + ') ORDER BY i.ordinal',
                            (owner, owner, *ids))
        by_case = {identifier: [] for identifier in ids}
        for image in images:
            by_case[image.pop('case_id')].append(image)
        for item in results:
            item['images'] = by_case[item['id']]
            item['image_count'] = len(item['images'])
            item['asset_id'] = item['images'][0]['id'] if item['images'] else None
    return results


def read_case(store, case_id, *, touch=True):
    owner = _owner(store)
    result = _case_row(store, case_id, owner)
    result['images'] = _images(store, case_id, owner)
    result['notes'] = store.rows('SELECT id,author,text,created FROM pa_notes WHERE case_id=? AND owner=? ORDER BY created,id', (case_id, owner))
    result['trials'] = store.rows('SELECT id,judgment,depth,details,created FROM pa_trials WHERE case_id=? AND owner=? ORDER BY created,id', (case_id, owner))
    for item in result['trials']:
        item['details'] = json.loads(item['details'])
    if touch:
        result['viewed'] = now()
        store.execute('UPDATE pa_cases SET viewed=? WHERE id=? AND owner=?', (result['viewed'], case_id, owner))
    return result


def read_image(store, asset_id):
    owner = _owner(store)
    rows = store.rows('SELECT a.* FROM pa_assets a JOIN pa_case_images i ON i.asset_id=a.id '
                      'JOIN pa_cases c ON c.id=i.case_id WHERE a.id=? AND a.owner=? AND c.owner=? '
                      "AND c.status='private_draft' LIMIT 1", (asset_id, owner, owner))
    if not rows:
        raise ValueError('原图不存在或无权访问')
    row = rows[0]
    path = _asset_path(store, owner, row['path'])
    data = path.read_bytes()
    if len(data) != row['bytes'] or digest(data) != row['sha256']:
        raise ValueError('原图校验失败，请从备份恢复；不会替换原图')
    return data


def update_case(store, case_id, value):
    value = metadata(value, partial=True)
    owner = _owner(store)
    _case_row(store, case_id, owner)
    if value:
        columns = list(value)
        parameters = [dumps(value[key]) if key in LIST_FIELDS else value[key] for key in columns]
        store.execute('UPDATE pa_cases SET ' + ','.join(key + '=?' for key in columns) + ',updated=? WHERE id=? AND owner=?',
                      (*parameters, now(), case_id, owner))
    return read_case(store, case_id, touch=False)


def add_note(store, case_id, text):
    owner = _owner(store)
    _case_row(store, case_id, owner)
    text = _text(text)
    if not text:
        raise ValueError('请先写下研究笔记')
    item = {'id': uuid.uuid4().hex, 'author': '我', 'text': text, 'created': now()}
    with store.connect() as db:
        db.execute('INSERT INTO pa_notes VALUES(?,?,?,?,?,?)', (item['id'], case_id, owner, item['author'], text, item['created']))
        db.execute('UPDATE pa_cases SET updated=? WHERE id=? AND owner=?', (item['created'], case_id, owner))
    return item


def add_trial(store, case_id, judgment, depth='quick', details=None):
    owner = _owner(store)
    _case_row(store, case_id, owner)
    judgment = _text(judgment, 2000)
    if not judgment or depth not in ('quick', 'structure', 'deep'):
        raise ValueError('请先作出判断，并选择试炼深度')
    if not isinstance(details or {}, dict) or len(dumps(details or {})) > 30_000:
        raise ValueError('判断记录无效或过长')
    # Details are a trader's research evidence, never an automatic grade.
    if set(details or {}) & {'score', 'win_rate', 'correct', 'profit'}:
        raise ValueError('试炼不生成自动成绩或收益')
    item = {'id': uuid.uuid4().hex, 'judgment': judgment, 'depth': depth, 'details': details or {}, 'created': now()}
    store.execute('INSERT INTO pa_trials VALUES(?,?,?,?,?,?,?)',
                  (item['id'], case_id, owner, judgment, depth, dumps(item['details']), item['created']))
    return item


def topic_note(store, topic_id, text):
    if topic_id not in TOPIC_IDS:
        raise ValueError('未知知识主题')
    text = _text(text)
    if not text:
        raise ValueError('请先写下研究记录')
    owner = _owner(store)
    item = {'id': uuid.uuid4().hex, 'topic_id': topic_id, 'text': text, 'created': now(), 'author': '我'}
    store.execute('INSERT INTO pa_topic_notes VALUES(?,?,?,?,?)', (item['id'], owner, topic_id, text, item['created']))
    return item


def growth(store):
    owner = _owner(store)
    # Counts use the full owner-scoped tables, independent of search pagination.
    count = lambda table: store.rows('SELECT COUNT(*) AS n FROM ' + table + ' WHERE owner=?', (owner,))[0]['n']
    notes = store.rows('SELECT n.id,n.case_id,n.author,n.text,n.created FROM pa_notes n JOIN pa_cases c ON c.id=n.case_id '
                       'WHERE n.owner=? AND c.owner=? ORDER BY n.created DESC,n.id DESC LIMIT 100', (owner, owner))
    trials = store.rows('SELECT t.id,t.case_id,t.judgment,t.depth,t.details,t.created,c.data_kind '
                        'FROM pa_trials t JOIN pa_cases c ON c.id=t.case_id WHERE t.owner=? AND c.owner=? '
                        'ORDER BY t.created DESC,t.id DESC LIMIT 100', (owner, owner))
    for trial in trials:
        trial['details'] = json.loads(trial['details'])
    topic_notes = store.rows('SELECT id,topic_id,text,created FROM pa_topic_notes WHERE owner=? ORDER BY created DESC,id DESC', (owner,))
    for item in topic_notes:
        item['author'] = '我'
    researched = {item['topic_id'] for item in topic_notes}
    recent = store.rows("SELECT * FROM pa_cases WHERE owner=? AND status='private_draft' AND viewed<>'' ORDER BY viewed DESC LIMIT 6", (owner,))
    cases = count('pa_cases')
    return {'stats': {'cases': cases, 'images': count('pa_assets'), 'notes': count('pa_notes'),
                      'trials': count('pa_trials'), 'topics': len(researched)},
            'recent': [_summary(store, row, owner) for row in recent],
            'notes': notes, 'trials': trials, 'topic_notes': topic_notes,
            'first_use': not bool(cases or trials or topic_notes),
            'scope': 'local_private', 'team_available': False}


def _durable_zip(path, writer):
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise ValueError('目标文件已存在，请另选备份名称')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.pa-backup-' + uuid.uuid4().hex + '.tmp')
    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            writer(archive)
        with temporary.open('r+b') as handle:
            os.fsync(handle.fileno())
        if path.exists():
            raise ValueError('目标文件已存在，请另选备份名称')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup(store, path):
    from .pa_trial import backup_setting, validate_issues
    owner = _owner(store)
    warnings = []
    # Serialize with local image saves. SQLite snapshot covers notes/case edits.
    with store._files_lock, store.connect() as db:
        db.execute('BEGIN')
        cases = [dict(row) for row in db.execute("SELECT * FROM pa_cases WHERE owner=? AND status='private_draft'", (owner,))]
        assets = [dict(row) for row in db.execute('SELECT DISTINCT a.* FROM pa_assets a JOIN pa_case_images i ON i.asset_id=a.id '
                  'JOIN pa_cases c ON c.id=i.case_id WHERE a.owner=? AND c.owner=?', (owner, owner))]
        links = [dict(row) for row in db.execute('SELECT i.* FROM pa_case_images i JOIN pa_cases c ON c.id=i.case_id WHERE c.owner=?', (owner,))]
        image_ids = {}
        for link in links:
            image_ids.setdefault(link['case_id'], set()).add(link['asset_id'])
        for case in cases:
            setup = db.execute('SELECT value FROM meta WHERE key=?', (f"pa_trial_setup:{owner}:{case['id']}",)).fetchone()
            ids = image_ids.get(case['id'], set())
            case['trial_setup'], issue = backup_setting(setup[0] if setup else None, ids)
            previous = db.execute('SELECT value FROM meta WHERE key=?', (f"pa_trial_setup_issue:{owner}:{case['id']}",)).fetchone()
            issues = validate_issues(json.loads(previous[0])) if previous else []
            if issue and issue not in issues:
                issues.append(issue)
            if issues:
                case['trial_setup_issues'] = validate_issues(issues)
                warnings.append({'case_id': case['id'], 'code': 'trial_setup_quarantined'})
        notes = [dict(row) for row in db.execute('SELECT * FROM pa_notes WHERE owner=?', (owner,))]
        trials = [dict(row) for row in db.execute('SELECT * FROM pa_trials WHERE owner=?', (owner,))]
        topics = [dict(row) for row in db.execute('SELECT * FROM pa_topic_notes WHERE owner=?', (owner,))]
        manifest = {'format': 'aplus-pa-private', 'version': 1, 'created': now(),
                    'cases': cases, 'assets': assets, 'links': links, 'notes': notes, 'trials': trials, 'topic_notes': topics}
        for collection in (cases, assets, notes, trials, topics):
            for item in collection:
                item.pop('owner', None)
        for asset in assets:
            asset.pop('path')
        payload = dumps(manifest).encode('utf-8')
        if any(len(manifest[key]) > MAX_BACKUP_RECORDS for key in ('cases', 'assets', 'links', 'notes', 'trials', 'topic_notes')):
            raise ValueError('备份资料条目超过可恢复上限，未生成备份')
        if len(payload) > MAX_BACKUP_MANIFEST_BYTES:
            raise ValueError('备份目录超过可恢复大小上限，未生成备份')
        if len(payload) + sum(asset['bytes'] for asset in assets) > MAX_BACKUP_BYTES:
            raise ValueError('备份资料超过可恢复容量上限，未生成备份')
        def writer(archive):
            archive.writestr('manifest.json', payload)
            for asset in assets:
                archive.writestr('originals/' + asset['id'], read_image(store, asset['id']))
        _durable_zip(path, writer)
    result = {'cases': len(cases), 'images': len(assets), 'path': str(Path(path).resolve())}
    if warnings:
        result['warnings'] = warnings
    return result


def _read_backup(path, staging):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(entries) > MAX_BACKUP_RECORDS * 5 or len(names) != len(set(names)):
            raise ValueError('备份包含重复条目或条目过多')
        if any(PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or ':' in name for name in names):
            raise ValueError('备份包含越界路径')
        if sum(item.file_size for item in entries) > MAX_BACKUP_BYTES:
            raise ValueError('备份解压大小超过限制')
        info = archive.getinfo('manifest.json')
        if info.file_size > MAX_BACKUP_MANIFEST_BYTES:
            raise ValueError('备份目录过大')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('format') != 'aplus-pa-private' or manifest.get('version') != 1:
            raise ValueError('不支持的 PA 备份格式')
        for key in ('cases', 'assets', 'links', 'notes', 'trials', 'topic_notes'):
            if not isinstance(manifest.get(key), list) or len(manifest[key]) > MAX_BACKUP_RECORDS:
                raise ValueError('备份资料无效或过多')
        asset_data = {}
        for asset in manifest['assets']:
            asset_id = asset['id']
            if not isinstance(asset_id, str) or len(asset_id) != 32 or any(c not in '0123456789abcdef' for c in asset_id):
                raise ValueError('备份原图身份无效')
            entry = archive.getinfo('originals/' + asset_id)
            if entry.file_size > MAX_IMAGE_BYTES:
                raise ValueError('备份原图过大')
            staged = staging / asset_id
            checksum = hashlib.sha256()
            length = 0
            with archive.open(entry) as source, staged.open('xb') as target:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    length += len(block)
                    if length > MAX_IMAGE_BYTES or length > entry.file_size:
                        raise ValueError('备份原图过大或长度不一致')
                    checksum.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if not length or length != entry.file_size:
                raise ValueError('备份原图为空或长度不一致')
            image = dict(_image_details(lambda: staged), sha256=checksum.hexdigest(), bytes=length)
            if asset_id in asset_data or image['sha256'] != asset['sha256'] or image['bytes'] != asset['bytes']:
                raise ValueError('备份原图校验失败')
            # Retain a path and small metadata only; the next image may be
            # validated without keeping this image's bytes or decoded pixels.
            asset_data[asset_id] = (staged, image)
        expected = {'manifest.json'} | {'originals/' + item for item in asset_data}
        if set(names) != expected:
            raise ValueError('备份包含未登记文件')
    return manifest, asset_data


def restore(store, path):
    # Staging is private to this runtime and removed on success or failure.
    # Never extract archive paths and never retain the complete image payload.
    with tempfile.TemporaryDirectory(prefix='.pa-restore-', dir=store.root) as temporary:
        staging = Path(temporary).resolve()
        if not staging.is_relative_to(store.root.resolve()):
            raise ValueError('恢复暂存目录必须位于本机运行目录')
        return _restore_staged(store, path, staging)


def _restore_staged(store, path, staging):
    # Validate the complete archive before writing anything. Importing creates
    # fresh identities and never replaces an existing case or trading record.
    try:
        manifest, asset_data = _read_backup(path, staging)
        case_ids = [case['id'] for case in manifest['cases']]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError('备份案例身份重复')
        if any(not isinstance(identifier, str) or len(identifier) > 100 for identifier in case_ids):
            raise ValueError('备份案例身份无效')
        for case in manifest['cases']:
            metadata({key: json.loads(case[key]) if key in LIST_FIELDS else case[key]
                      for key in TEXT_FIELDS + LIST_FIELDS + ('data_kind',)})
            if case.get('status') != 'private_draft':
                raise ValueError('只能恢复私人草稿')
            for field in ('created', 'updated', 'viewed'):
                _text(case.get(field, ''), 100)
        for asset in manifest['assets']:
            _text(asset['name'], 500)
            _text(asset['created'], 100)
        links = manifest['links']
        if any(link['case_id'] not in case_ids or link['asset_id'] not in asset_data for link in links):
            raise ValueError('备份图片关联无效')
        if any(not any(link['case_id'] == identifier for link in links) for identifier in case_ids):
            raise ValueError('备份案例缺少原图')
        if any(not any(link['asset_id'] == identifier for link in links) for identifier in asset_data):
            raise ValueError('备份存在无关联原图')
        pairs = [(link['case_id'], link['asset_id']) for link in links]
        slots = [(link['case_id'], link['ordinal']) for link in links]
        if len(pairs) != len(set(pairs)) or len(slots) != len(set(slots)) or any(
                type(link['ordinal']) is not int or link['ordinal'] < 0 for link in links):
            raise ValueError('备份图片顺序或关联重复')
        # Version 1 backups can contain older cases that exceed today's import
        # limits. Restore those intact under the archive-wide bounds; save_case
        # still prevents any further additions to an already oversized case.
        from .pa_trial import validate, validate_issues
        for case in manifest['cases']:
            if case.get('trial_setup') is not None:
                case['trial_setup'] = validate(case['trial_setup'], {link['asset_id'] for link in links if link['case_id'] == case['id']})
            if 'trial_setup_issues' in case:
                validate_issues(case['trial_setup_issues'])
        if any(item['case_id'] not in case_ids for item in manifest['notes'] + manifest['trials']):
            raise ValueError('备份研究记录关联无效')
        for item in manifest['notes']:
            if not _text(item['text']):
                raise ValueError('备份研究笔记为空')
            _text(item['author'], 200); _text(item['created'], 100)
        for item in manifest['trials']:
            if not _text(item['judgment'], 2000):
                raise ValueError('备份判断记录为空')
            _text(item['created'], 100)
            details = json.loads(item['details'])
            if (item['depth'] not in ('quick', 'structure', 'deep') or not isinstance(details, dict)
                    or len(dumps(details)) > 30_000 or set(details) & {'score', 'win_rate', 'correct', 'profit'}):
                raise ValueError('备份判断记录无效')
            if details.get('source') == 'progressive-screenshot':
                validate(details.get('presentation'), {link['asset_id'] for link in links if link['case_id'] == item['case_id']})
        for item in manifest['topic_notes']:
            if item['topic_id'] not in TOPIC_IDS:
                raise ValueError('备份知识主题无效')
            if not _text(item['text']):
                raise ValueError('备份知识记录为空')
            _text(item['created'], 100)
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError('备份无法读取或资料不完整') from exc
    owner = _owner(store)
    case_map = {identifier: uuid.uuid4().hex for identifier in case_ids}
    asset_map = {identifier: uuid.uuid4().hex for identifier in asset_data}
    def restored_details(raw):
        value = json.loads(raw)
        if value.get('source') == 'progressive-screenshot':
            value['presentation']['image_ids'] = [asset_map[i] for i in value['presentation']['image_ids']]
        if isinstance(value.get('image_id'), str):
            value['image_id'] = asset_map.get(value['image_id'], value['image_id'])
        for mark in value.get('marks', []) if isinstance(value.get('marks'), list) else []:
            if isinstance(mark, dict) and isinstance(mark.get('image_id'), str):
                mark['image_id'] = asset_map.get(mark['image_id'], mark['image_id'])
        return dumps(value)
    with write_scope(store), store._files_lock:
        new_assets = []
        for old in manifest['assets']:
            staged, image = asset_data[old['id']]
            # The staged immutable image is the sole raw image held here.
            data = staged.read_bytes()
            if len(data) != image['bytes'] or digest(data) != image['sha256']:
                raise ValueError('恢复暂存原图校验失败')
            identifier = asset_map[old['id']]
            relative = _write_original(store, owner, identifier, data, image)
            del data
            new_assets.append((identifier, owner, image['sha256'], relative, _text(old['name'], 500), image['mime'],
                               image['width'], image['height'], image['bytes'], _text(old['created'], 100)))
        with store.connect() as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            for case in manifest['cases']:
                fields = TEXT_FIELDS + LIST_FIELDS + ('data_kind',)
                value = metadata({key: json.loads(case[key]) if key in LIST_FIELDS else case[key] for key in fields})
                db.execute('INSERT INTO pa_cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (case_map[case['id']], owner, 'private_draft', value['title'], value['memory'], dumps(value['keywords']),
                            dumps(value['tags']), dumps(value['topic_ids']), value['symbol'], value['timeframe'], value['market'], value['data_kind'],
                            _text(case['created'], 100), _text(case['updated'], 100), _text(case.get('viewed', ''), 100)))
            db.executemany('INSERT INTO pa_assets VALUES(?,?,?,?,?,?,?,?,?,?)', new_assets)
            db.executemany('INSERT INTO pa_case_images VALUES(?,?,?)',
                           [(case_map[link['case_id']], asset_map[link['asset_id']], int(link['ordinal'])) for link in links])
            for case in manifest['cases']:
                setup = case.get('trial_setup')
                if setup:
                    setup = dict(setup, image_ids=[asset_map[i] for i in setup['image_ids']])
                    store.pa_trial_setup(case_map[case['id']], setup)
                if case.get('trial_setup_issues'):
                    store.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                                  (f"pa_trial_setup_issue:{owner}:{case_map[case['id']]}", dumps(case['trial_setup_issues'])))
            db.executemany('INSERT INTO pa_notes VALUES(?,?,?,?,?,?)',
                           [(uuid.uuid4().hex, case_map[item['case_id']], owner, _text(item['author'], 200), _text(item['text']),
                             _text(item['created'], 100)) for item in manifest['notes']])
            db.executemany('INSERT INTO pa_trials VALUES(?,?,?,?,?,?,?)',
                           [(uuid.uuid4().hex, case_map[item['case_id']], owner, _text(item['judgment'], 2000), item['depth'], restored_details(item['details']),
                             _text(item['created'], 100)) for item in manifest['trials']])
            db.executemany('INSERT INTO pa_topic_notes VALUES(?,?,?,?,?)',
                           [(uuid.uuid4().hex, owner, item['topic_id'], _text(item['text']), _text(item['created'], 100))
                            for item in manifest['topic_notes']])
    result = {'cases': len(case_map), 'images': len(asset_map), 'notes': len(manifest['notes']), 'trials': len(manifest['trials'])}
    issues = [case for case in manifest['cases'] if case.get('trial_setup_issues')]
    if issues:
        result['warnings'] = [{'case_id': case_map[case['id']], 'code': 'trial_setup_quarantined'} for case in issues]
    return result
