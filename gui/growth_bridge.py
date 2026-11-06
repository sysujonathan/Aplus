"""Private, local-only PA workspace API. All durable mutations go through Store."""
from __future__ import annotations

import base64
from contextlib import nullcontext
import json
import hashlib
from functools import wraps
import io
from pathlib import Path
import threading
import uuid

from PIL import Image, ImageGrab
from workbench.pa_terms import TERMS, IDS as TERM_IDS, graph, selected
from workbench.pa_auth import GoogleSession, SessionChanged


TOPICS = [
    ('context', '市场背景', None), ('continuation', '趋势延续', None),
    ('range', '交易区间', None), ('reversal', '反转', None),
    ('structure', 'K线结构', None), ('discipline', '交易管理', None),
    ('context-trend', '趋势', 'context'), ('context-range', '区间', 'context'),
    ('context-transition', '过渡', 'context'), ('context-mtf', '多周期', 'context'),
    ('cont-h2', 'H2', 'continuation'), ('cont-h1', 'H1', 'continuation'),
    ('cont-breakout', '突破', 'continuation'), ('cont-pullback', '回调', 'continuation'),
    ('cont-gap', '缺口', 'continuation'), ('cont-mm', '测量目标', 'continuation'),
    ('range-boundary', '区间边界', 'range'), ('range-fail', '失败突破', 'range'),
    ('range-middle', '区间中部', 'range'), ('rev-mtr', '主要趋势反转', 'reversal'),
    ('rev-climax', '高潮', 'reversal'), ('rev-wedge', '楔形', 'reversal'),
    ('rev-double', '双重结构', 'reversal'), ('struct-signal', '信号K线', 'structure'),
    ('struct-tail', '影线', 'structure'), ('struct-inside', '内包', 'structure'),
    ('struct-outside', '外包', 'structure'), ('struct-slope', '斜率', 'structure'),
    ('risk-entry', '入场', 'discipline'), ('risk-sl', '失效与止损', 'discipline'),
    ('risk-exposure', '风险敞口', 'discipline'), ('risk-exit', '退出', 'discipline'),
]
MAX_IMAGE = 32 * 1024 * 1024
MAX_PENDING = 128 * 1024 * 1024


def guarded(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        # pywebview invokes API calls on independent threads. One mutation at a
        # time also lets the close guard wait for an acknowledged disk commit.
        with self._lock:
            try:
                if getattr(self, '_closing', False):
                    raise ValueError('成长空间正在关闭，请重新打开后操作')
                return self._invoke(method, args, kwargs)
            except Exception as exc:
                result = {'ok': False, 'error': str(exc) or '操作失败，请重试'}
                if isinstance(exc, ConnectionError):
                    result['error_kind'] = 'offline'
                self._security_error(result, exc)
                return result
    call._growth_api = True
    return call


def image_read(method):
    """Image/network reads must not hold the mutation/close acknowledgement lock."""
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            with self._lock:
                if getattr(self, '_closing', False):
                    raise ValueError('成长空间正在关闭')
            value = self._invoke(method, args, kwargs)
            with self._lock:
                if getattr(self, '_closing', False):
                    raise ValueError('成长空间正在关闭')
            return value
        except Exception as exc:
            result = {'ok': False, 'error': str(exc) or '图片读取失败'}
            if isinstance(exc, ConnectionError):
                result['error_kind'] = 'offline'
            self._security_error(result, exc)
            return result
    call._growth_api = True
    return call


def image_url(data, *, thumbnail=False):
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME.get(image.format, 'image/png')
        if thumbnail:
            image.thumbnail((640, 400))
            output = io.BytesIO()
            image.convert('RGB').save(output, 'JPEG', quality=88)
            data, mime = output.getvalue(), 'image/jpeg'
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')


def _native_clipboard_png():
    """Read one valid PNG allocation; malformed native formats may use DIB."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    if not user.OpenClipboard(None):
        return None
    try:
        handle = user.GetClipboardData(user.RegisterClipboardFormatW('PNG'))
        if not handle:
            return None
        size = kernel.GlobalSize(handle)
        if size > MAX_IMAGE:
            raise ValueError('单张图片不能超过32MB')
        if not size:
            return None
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return None
        try:
            raw = ctypes.string_at(pointer, size)
        finally:
            kernel.GlobalUnlock(handle)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG':
                    return None
                image.verify()
        except (OSError, ValueError, SyntaxError):
            return None
        return raw
    finally:
        user.CloseClipboard()


def clipboard_images():
    """Prefer valid original PNG bytes; invalid PNG may fall back to DIB."""
    import os
    if os.name == 'nt':
        try:
            raw = _native_clipboard_png()
        except OSError:
            raw = None
        if raw is not None:
            return [{'name': '粘贴截图.png', 'data': raw}]
    value = ImageGrab.grabclipboard()
    if isinstance(value, list):
        return read_image_files(value)
    if isinstance(value, Image.Image):
        output = io.BytesIO()
        value.save(output, 'PNG')
        return [{'name': '粘贴截图.png', 'data': output.getvalue()}]
    raise ValueError('剪贴板中没有图片，请先复制截图')


def read_image_file(path):
    path = Path(path)
    if path.stat().st_size > MAX_IMAGE:
        raise ValueError('单张图片不能超过32MB')
    return path.read_bytes()


def read_image_files(paths, budget=MAX_PENDING):
    paths = list(paths)
    if len(paths) > 100:
        raise ValueError('每批最多导入100张图片')
    images = []
    for path in paths:
        size = Path(path).stat().st_size
        if size > MAX_IMAGE or size > budget:
            raise ValueError('图片过多或过大，请分批导入')
        data = read_image_file(path)
        budget -= len(data)
        images.append({'name': Path(path).name, 'data': data})
    return images


class GrowthBridge:
    def __init__(self, store, context=None):
        self._store = store
        self._context = dict(context or {})
        self._pending = {}
        self._thumbnails = {}
        self._thumbnail_size = 0
        self._lock = threading.RLock()
        self._window = None
        from workbench.pa_shared import GoogleDrive
        self._drive = GoogleDrive(profile=store.root)
        self._seen_security = None
        self._request_security = threading.local()

    def call_scoped(self, method, args, expected):
        """Bind UI mutations to the account/permission epoch the trader saw."""
        target = getattr(self, method, None) if isinstance(method, str) and not method.startswith('_') else None
        if not getattr(target, '_growth_api', False) or not isinstance(args, list):
            return {'ok': False, 'error': '未知成长空间操作'}
        if expected is not None and (not isinstance(expected, str) or len(expected) > 100):
            return {'ok': False, 'error': '账号状态无效'}
        self._request_security.expected = expected
        try:
            return target(*args)
        finally:
            del self._request_security.expected

    def _security(self):
        status = self._public('status')
        session = getattr(self._drive, 'session', None)
        generation = GoogleSession.generation(session.load()) if isinstance(session, GoogleSession) else None
        if not status.get('connected'):
            return 'anonymous'
        return hashlib.sha256(json.dumps([generation, status['user']['id'], status['cached'],
                                          status.get('access_epoch')]).encode()).hexdigest()

    def _invoke(self, method, args, kwargs):
        before = self._security()
        allowed = method.__name__ in ('bootstrap', 'security_state', 'connect_google', 'disconnect_google')
        if self._seen_security is None:
            self._seen_security = before
        expected = getattr(self._request_security, 'expected', None)
        if expected is not None and before != expected and method.__name__ != 'bootstrap':
            raise SessionChanged()
        if not allowed and before != self._seen_security:
            raise SessionChanged()
        session = getattr(self._drive, 'session', None)
        scope = session.operation() if isinstance(session, GoogleSession) and method.__name__ not in ('connect_google', 'disconnect_google') else nullcontext()
        with scope:
            value = method(self, *args, **kwargs)
        after = self._security()
        # Refresh is allowed to grant access; other in-flight responses must
        # never rehydrate old personal content after a different window changes it.
        if before != after and method.__name__ not in ('connect_google', 'disconnect_google', 'refresh_shared'):
            raise SessionChanged()
        if method.__name__ != 'security_state':
            self._seen_security = after
        return {'ok': True, **value, **({'security': after} if after != 'anonymous' or before != after else {})}

    def _security_error(self, result, exc):
        try:
            current = self._security()
            if isinstance(exc, SessionChanged) or (self._seen_security is not None and current != self._seen_security):
                self._thumbnails.clear()
                self._thumbnail_size = 0
                result.update(error_kind='security', security=current)
        except (ValueError, OSError):
            result.update(error_kind='security', security='unavailable')

    def security_state(self):
        # Local credential/meta check only: polling never contacts Google.
        # It also bypasses a long-running network mutation in this window.
        try:
            return {'ok': True, 'security': self._security()}
        except (ValueError, OSError):
            return {'ok': False, 'error': '登录记录暂时不可用，请重新连接',
                    'error_kind': 'security', 'security': 'unavailable'}

    def _public(self, operation, *args):
        return self._store.pa_shared(operation, *args, drive=self._drive)

    @guarded
    def connect_google(self):
        try:
            user = self._drive.session.login()
        finally:
            if self._window is not None:
                self._window.show()  # Also surface cancellation/timeout in PA.
        try:
            return {'user': user, **self._public('refresh')}
        except (OSError, ValueError) as exc:
            # Login has already switched identity. The page must clear the
            # previous user's in-memory research even if Drive is unavailable.
            return {'user': user, 'status': self._public('status'), 'refresh_error': str(exc)}

    @guarded
    def disconnect_google(self):
        self._drive.session.logout()
        self._thumbnails.clear()
        self._thumbnail_size = 0
        return {}

    @guarded
    def refresh_shared(self):
        self._thumbnails.clear()
        self._thumbnail_size = 0
        return self._public('refresh')

    @guarded
    def publish_case(self, case_id):
        return self._public('publish', case_id)

    @guarded
    def mark_studied(self, case_id):
        return {'record': self._public('record', 'studied', case_id, None)}

    def _summaries(self, rows):
        result = []
        for source in rows:
            row = dict(source)
            images = row.get('images') or row.get('assets') or []
            asset_id = row.get('thumbnail_id') or row.get('cover_id') or (images[0]['id'] if images else None)
            if row.get('status') == 'shared':
                # Network thumbnails are requested independently by the page.
                # A cold/offline cover must not block text search or capture.
                row['thumbnail_pending'] = True
                result.append(row)
                continue
            if asset_id:
                try:
                    if asset_id not in self._thumbnails:
                        data = self._public('image', row['id'], asset_id, True) if row.get('status') == 'shared' else self._store.pa_image(asset_id)
                        url = image_url(data, thumbnail=True)
                        while self._thumbnails and self._thumbnail_size + len(url) > 32 * 1024 * 1024:
                            oldest = next(iter(self._thumbnails))
                            self._thumbnail_size -= len(self._thumbnails.pop(oldest))
                        self._thumbnails[asset_id] = url
                        self._thumbnail_size += len(url)
                    row['thumbnail'] = self._thumbnails[asset_id]
                    row['thumbnail_url'] = row['thumbnail']
                except (OSError, ValueError):
                    row['image_error'] = '原图暂时无法读取，请检查备份'
            result.append(row)
        return result

    def _topic_family(self, topic_id):
        family = {topic_id}
        for _ in range(len(TOPICS)):
            children = {key for key, _, parent in TOPICS if parent in family}
            if children <= family:
                break
            family.update(children)
        return family

    def _topics(self, cases=None):
        if cases is None:
            cases = self._store.pa_case_metadata()
        families = {key: self._topic_family(key) for key, _, _ in TOPICS}
        return [dict(id=key, name=name, parent_id=parent, description='',
                     case_count=sum(bool(families[key].intersection(row.get('topic_ids', []))) for row in cases))
                for key, name, parent in TOPICS]

    @guarded
    def bootstrap(self):
        metadata = self._store.pa_case_metadata()
        growth = self._growth(metadata)
        growth['recent_case_id'] = (growth.get('recent') or [{}])[0].get('id')
        growth['recent_case'] = (growth.get('recent') or [None])[0]
        cases = self._store.pa_search(limit=101)
        shared = self._public('catalogue')
        return dict(shared_status=self._public('status'), public_cases=self._summaries(shared[:100]),
                    terms=list(TERMS), graph=graph(shared), public_count=len(shared), cases=self._summaries(cases[:100]), atlas_cases=metadata, has_more=len(cases) > 100,
                    next_offset=min(100, len(cases)), topics=self._topics(metadata),
                    context=self._context, **growth)

    @guarded
    def search_cases(self, query='', filters=None, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('结果分页无效')
        if filters and filters.get('scope') == 'public':
            result = self._public('search', query, {k:v for k,v in filters.items() if k != 'scope'}, offset)
            result['cases'] = self._summaries(result['cases'])
            return result
        filters = {k:v for k,v in (filters or {}).items() if k not in ('scope', 'contributor', 'hidden')}
        if filters and filters.get('topic_id'):
            family = self._topic_family(filters['topic_id'])
            if len(family) > 1:
                other = dict(filters)
                other.pop('topic_id')
                cases, scanned = [], 0
                while len(cases) < offset + 101:
                    page = self._store.pa_search(query, other, limit=1000, offset=scanned)
                    cases.extend(row for row in page if family.intersection(row.get('topic_ids', [])))
                    if len(page) < 1000:
                        break
                    scanned += 1000
                page = cases[offset:offset + 101]
                return {'cases': self._summaries(page[:100]), 'has_more': len(page) > 100,
                        'next_offset': offset + min(100, len(page))}
        page = self._store.pa_search(query, filters, limit=101, offset=offset)
        return {'cases': self._summaries(page[:100]), 'has_more': len(page) > 100,
                'next_offset': offset + min(100, len(page))}

    @guarded
    def random_trial(self, scope='public'):
        return self._store.pa_random_trial(scope, drive=self._drive)

    @guarded
    def get_case(self, case_id):
        # Images remain metadata until the trader chooses one to inspect.
        if str(case_id).startswith('shared:'):
            try:
                self._public('record', 'visited', case_id, None)
            except ValueError:
                pass  # Optional visit history must not hide intact originals.
            return {'case': self._public('case', case_id)}
        case = self._store.pa_case(case_id)
        try:
            case['trial_setup'] = self._store.pa_trial_setup(case_id)
        except (ValueError, json.JSONDecodeError):
            case['trial_setup'] = None
            case['trial_setup_error'] = '试炼设置无法读取，请重新设置；原图仍可查看。'
        if self._public('status')['connected']:
            try:
                self._public('record', 'visited', case_id, None)
                records = self._public('personal')
                case['notes'] += [r for r in records['notes'] if r['case_id'] == case_id]
                case['trials'] = [r for r in records['trials'] if r['case_id'] == case_id]
            except ValueError:
                case['personal_error'] = '账号个人学习记录无法读取；本机原案例仍可查看。'
        return {'case': case}

    @guarded
    def set_trial_setup(self, case_id, value):
        if str(case_id).startswith('shared:'):
            raise ValueError('公共案例保留原提交设置，请另存私密案例后修改')
        return {'setup': self._store.pa_trial_setup(case_id, value)}

    @image_read
    def get_image(self, case_id, asset_id, thumbnail=False, cache_only=False):
        if type(thumbnail) is not bool or type(cache_only) is not bool:
            raise ValueError('图片读取方式无效')
        shared = str(case_id).startswith('shared:')
        case = self._public('case', case_id) if shared else self._store.pa_case(case_id, touch=False)
        item = next((image for image in case.get('images', []) if image['id'] == asset_id), None)
        if item is None:
            raise ValueError('图片不存在或不属于此案例')
        return {'image': dict(item, data_url=image_url(self._public('image', case_id, asset_id, thumbnail, cache_only) if shared else self._store.pa_image(asset_id), thumbnail=thumbnail))}

    def _stage(self, images):
        total = sum(len(item['data']) for item in self._pending.values())
        staged = []
        # Validate the entire batch before accepting any pending item.
        for item in images:
            data = item['data']
            if not data or len(data) > MAX_IMAGE:
                raise ValueError('单张图片不能超过32MB')
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 40_000_000:
                    raise ValueError('图片格式或尺寸不支持')
                image.verify()
            total += len(data)
            if total > MAX_PENDING or len(self._pending) + len(images) > 100:
                raise ValueError('待保存图片过多，请先保存当前批次')
            token = uuid.uuid4().hex
            staged.append((token, item, dict(token=token, name=item['name'],
                data_url=image_url(data, thumbnail=True), duplicate_cases=self._store.pa_find_duplicates(data))))
        for token, item, _ in staged:
            self._pending[token] = item
        return {'pending': [row for _, _, row in staged]}

    @guarded
    def stage_images(self, images, case_id=None):
        if not isinstance(images, list) or len(images) > 100:
            raise ValueError('每批最多导入100张图片')
        decoded = []
        budget = MAX_PENDING - sum(len(item['data']) for item in self._pending.values())
        for item in images:
            url = item.get('data_url', '')
            if len(url) > MAX_IMAGE * 1.4 or not url.startswith('data:image/') or ';base64,' not in url:
                raise ValueError('图片内容无效')
            encoded = url.split(';base64,', 1)[1]
            if len(encoded) > (budget + 2) * 4 / 3:
                raise ValueError('待保存图片过多，请先保存当前批次')
            data = base64.b64decode(encoded, validate=True)
            budget -= len(data)
            if budget < 0:
                raise ValueError('待保存图片过多，请先保存当前批次')
            decoded.append(dict(name=str(item.get('name') or '截图.png')[:200], data=data))
        return self._stage(decoded)

    @guarded
    def import_files(self, case_id=None):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
            file_types=('K线图片 (*.png;*.jpg;*.jpeg;*.webp)',))
        budget = MAX_PENDING - sum(len(item['data']) for item in self._pending.values())
        return self._stage(read_image_files(paths or [], budget))

    @guarded
    def paste_image(self, case_id=None):
        return self._stage(clipboard_images())

    @guarded
    def discard_pending(self, tokens):
        for token in tokens:
            self._pending.pop(token, None)
        return {}

    @guarded
    def discard_capture(self, tokens):
        for token in tokens:
            self._pending.pop(token, None)
        return {}

    @guarded
    def return_chart(self):
        callback = getattr(self, '_return_chart', None)
        if callback is None:
            raise ValueError('请从 Aplus 当前图表打开案例对照')
        callback()
        return {}

    @guarded
    def compare_case(self, case_id):
        if str(case_id).startswith('shared:'):
            self._public('case', case_id)
        else:
            self._store.pa_case(case_id, touch=False)
        callback = getattr(self, '_compare_case', None)
        if callback is None:
            if self._context.get('comparison'):
                return {'already_comparing': True}
            raise ValueError('请从 Aplus 当前图表打开案例对照')
        callback(case_id)
        return {'comparison': True, 'case_id': case_id}

    @guarded
    def save_capture(self, tokens, metadata=None, options=None):
        options = options or {}
        if options.get('formal'):
            selected([i for i in (metadata or {}).get('topic_ids', []) if i in TERM_IDS], required=True)
        if not tokens or any(token not in self._pending for token in tokens):
            raise ValueError('待保存图片已失效，请重新导入')
        images = [self._pending[token] for token in tokens]
        allow = options.get('allow_duplicates') is True
        if not allow:
            duplicates = [row for item in images for row in self._store.pa_find_duplicates(item['data'])]
            seen = set()
            for item in images:
                checksum = hashlib.sha256(item['data']).hexdigest()
                if checksum in seen:
                    duplicates.append({'id': None, 'title': item['name'], 'pending': True})
                seen.add(checksum)
            if duplicates:
                return {'cases': [], 'duplicates': duplicates}
        results = []
        # One PA write scope commits the whole batch and owns rollback cleanup.
        with self._store.pa_write():
            if options.get('case_id') or options.get('group'):
                result = self._store.pa_save_case(images, metadata, case_id=options.get('case_id'), allow_duplicate=allow)
                if not result['saved']:
                    return {'cases': [], 'duplicates': result['duplicates']}
                results.append(result['case'])
            else:
                for image in images:
                    result = self._store.pa_save_case([image], metadata, allow_duplicate=allow)
                    if not result['saved']:
                        raise ValueError('批次中含相同图片，请确认重复提醒后再保存')
                    results.append(result['case'])
        for token in tokens:
            self._pending.pop(token, None)
        return {'cases': self._summaries(results), 'duplicates': []}

    @guarded
    def revise_public_case(self, case_id, expected, metadata):
        return self._public('collaborate', case_id, 'revision', expected, metadata)

    @guarded
    def comment_public_case(self, case_id, text, request_id):
        return self._public('collaborate', case_id, 'comment', request_id, text)

    @guarded
    def set_public_visibility(self, case_id, expected, hidden):
        return self._public('collaborate', case_id, 'visibility', expected, hidden)

    @guarded
    def delete_case(self, case_id):
        if str(case_id).startswith('shared:'):
            raise ValueError('公共案例删除尚未实现；管理员可选择下架，Google Drive 原件不会删除')
        return self._store.pa_delete_case(case_id)

    @guarded
    def update_case(self, case_id, metadata):
        if str(case_id).startswith('shared:'):
            raise ValueError('公共案例保留原始提交；个人研究请保存为笔记')
        case = self._store.pa_update_case(case_id, metadata)
        try:
            case['trial_setup'] = self._store.pa_trial_setup(case_id)
        except (ValueError, json.JSONDecodeError):
            case['trial_setup'] = None
            case['trial_setup_error'] = '试炼设置无法读取，请重新设置；原图仍可查看。'
        return {'case': case}

    @guarded
    def add_note(self, case_id, text):
        if str(case_id).startswith('shared:') or self._public('status')['connected']:
            return {'note': self._public('record', 'notes', case_id, text)}
        return {'note': self._store.pa_add_note(case_id, text)}

    @guarded
    def save_trial(self, case_id, value):
        if str(case_id).startswith('shared:') or self._public('status')['connected']:
            return {'trial': self._public('record', 'trials', case_id, value)}
        if (value.get('details') or {}).get('source') == 'progressive-screenshot':
            setup = self._store.pa_trial_setup(case_id)
            if not setup:
                raise ValueError('请先设置试炼的入场分界或前后截图')
            if value['details'].get('presentation') != setup:
                raise ValueError('试炼设置已改变，请重新开始后记录判断')
            value = dict(value, details=dict(value['details'], presentation=setup))
        return {'trial': self._store.pa_add_trial(case_id, value.get('judgment', ''),
            value.get('depth', 'quick'), value.get('details'))}

    @guarded
    def add_topic_note(self, topic_id, text):
        if topic_id not in {item[0] for item in TOPICS} | TERM_IDS:
            raise ValueError('知识主题不存在')
        if topic_id in TERM_IDS and self._public('status')['connected']:
            return {'note': self._public('record', 'topic_notes', topic_id, text)}
        return {'note': self._store.pa_topic_note(topic_id, text)}

    @guarded
    def topics(self):
        return {'topics': self._topics()}

    @guarded
    def growth(self):
        return self._growth()

    def _growth(self, cases=None):
        data = self._store.pa_growth()
        status = self._public('status')
        public = self._public('catalogue')
        data['stats']['public_cases'] = len(public)
        data['stats']['contributions'] = sum(c['contributor']['id'] == status.get('user', {}).get('id') for c in public)
        if status['connected']:
            try:
                personal = self._public('personal')
            except ValueError:
                data['personal_error'] = '个人学习记录暂时无法读取，原记录已保留；请从个人学习备份恢复。'
                personal = dict(notes=[], trials=[], topic_notes=[], studied=[], visited=[])
            data['notes'] = personal['notes']
            data['trials'] = personal['trials']
            data['topic_notes'] = personal['topic_notes']
            data['stats']['notes'] = len(personal['notes'])
            data['stats']['trials'] = len(personal['trials'])
            data['stats']['researched_topics'] = len({n['topic_id'] for n in personal['topic_notes']})
            data['stats']['studied_cases'] = len({n['case_id'] for n in personal['studied']})
        if cases is None:
            cases = self._store.pa_case_metadata()
        case_map = {row['id']: row for row in cases + public}
        for row in data.get('trials', []) + data.get('notes', []):
            case = case_map.get(row.get('case_id'), {})
            row['case_available'] = bool(case)
            row['case_title'] = case.get('title') or case.get('memory') or case.get('symbol') or ('未命名案例' if case else '案例已移除或不可访问')
            row['data_kind'] = case.get('data_kind', 'imported')
        if status['connected']:
            recent = sorted(personal.get('visited', []), key=lambda r: r['created'], reverse=True)
            found = next((case_map[r['case_id']] for r in recent if r['case_id'] in case_map), None)
            if found:
                data['recent'] = [found]
        data['stats']['test_cases'] = (sum(row['data_kind'] == 'test' and row['contributor']['id'] == status['user']['id'] for row in public)
                                      if status['connected'] else sum(row['data_kind'] == 'test' for row in cases))
        data['stats']['test_trials'] = sum(row.get('data_kind') == 'test' for row in data.get('trials', []))
        data['stats']['topic_notes'] = len(data.get('topic_notes', []))
        data['records'] = sorted(data.get('notes', []) + data.get('topic_notes', []),
                                 key=lambda row: row['created'], reverse=True)
        return data

    @guarded
    def export_backup(self):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='Aplus-PA备份.zip',
                                               file_types=('PA备份 (*.zip)',))
        if not paths:
            return {'cancelled': True}
        return {'result': self._store.pa_backup(paths[0])}

    @guarded
    def learning_backup(self, restore=False):
        import webview
        if type(restore) is not bool:
            raise ValueError('备份操作无效')
        self._drive.identity()
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN if restore else webview.FileDialog.SAVE,
            **({'allow_multiple': False} if restore else {'save_filename': 'Aplus-个人学习.palearn'}),
            file_types=('个人学习 (*.palearn)',))
        if not paths:
            return {'cancelled': True}
        return {'result': self._public('restore_learning' if restore else 'export_learning', paths[0])}

    @guarded
    def restore_backup(self):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                               file_types=('PA备份 (*.zip)',))
        if not paths:
            return {'cancelled': True}
        return {'result': self._store.pa_restore(paths[0])}
