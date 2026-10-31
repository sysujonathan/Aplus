"""Private, local-only PA workspace API. All durable mutations go through Store."""
from __future__ import annotations

import base64
import hashlib
from functools import wraps
import io
from pathlib import Path
import threading
import uuid

from PIL import Image, ImageGrab


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
                return {'ok': True, **method(self, *args, **kwargs)}
            except Exception as exc:
                return {'ok': False, 'error': str(exc) or '操作失败，请重试'}
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

    def _summaries(self, rows):
        result = []
        for source in rows:
            row = dict(source)
            images = row.get('images') or row.get('assets') or []
            asset_id = row.get('thumbnail_id') or row.get('cover_id') or (images[0]['id'] if images else None)
            if asset_id:
                try:
                    if asset_id not in self._thumbnails:
                        url = image_url(self._store.pa_image(asset_id), thumbnail=True)
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
        return dict(cases=self._summaries(cases[:100]), has_more=len(cases) > 100,
                    next_offset=min(100, len(cases)), topics=self._topics(metadata),
                    context=self._context, **growth)

    @guarded
    def search_cases(self, query='', filters=None, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('结果分页无效')
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
    def get_case(self, case_id):
        # Images remain metadata until the trader chooses one to inspect.
        return {'case': self._store.pa_case(case_id)}

    @guarded
    def get_image(self, case_id, asset_id, thumbnail=False):
        if type(thumbnail) is not bool:
            raise ValueError('图片读取方式无效')
        case = self._store.pa_case(case_id, touch=False)
        item = next((image for image in case.get('images', []) if image['id'] == asset_id), None)
        if item is None:
            raise ValueError('图片不存在或不属于此案例')
        return {'image': dict(item, data_url=image_url(self._store.pa_image(asset_id), thumbnail=thumbnail))}

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
    def update_case(self, case_id, metadata):
        return {'case': self._store.pa_update_case(case_id, metadata)}

    @guarded
    def add_note(self, case_id, text):
        return {'note': self._store.pa_add_note(case_id, text)}

    @guarded
    def save_trial(self, case_id, value):
        return {'trial': self._store.pa_add_trial(case_id, value.get('judgment', ''),
            value.get('depth', 'quick'), value.get('details'))}

    @guarded
    def add_topic_note(self, topic_id, text):
        if topic_id not in {item[0] for item in TOPICS}:
            raise ValueError('知识主题不存在')
        return {'note': self._store.pa_topic_note(topic_id, text)}

    @guarded
    def topics(self):
        return {'topics': self._topics()}

    @guarded
    def growth(self):
        return self._growth()

    def _growth(self, cases=None):
        data = self._store.pa_growth()
        if cases is None:
            cases = self._store.pa_case_metadata()
        case_map = {row['id']: row for row in cases}
        for row in data.get('trials', []) + data.get('notes', []):
            case = case_map.get(row.get('case_id'), {})
            row['case_title'] = case.get('title') or case.get('memory') or case.get('symbol') or '未命名案例'
            row['data_kind'] = case.get('data_kind', 'imported')
        data['stats']['test_cases'] = sum(row['data_kind'] == 'test' for row in cases)
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
    def restore_backup(self):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                               file_types=('PA备份 (*.zip)',))
        if not paths:
            return {'cancelled': True}
        return {'result': self._store.pa_restore(paths[0])}
