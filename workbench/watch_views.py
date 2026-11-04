"""Named views of the existing watch pool. No duplicate plans, notes or prices."""
from copy import deepcopy
import csv
import io
import uuid
import math

from .market import code_of

FLAGS = {'red': '#F36C79', 'blue': '#7BB4FF', 'green': '#59CEAA',
         'yellow': '#F3CD6C', 'purple': '#BDA0FF', 'cyan': '#76DDE3', 'pink': '#F1A1D4'}
COLUMNS = ('flag', 'number', 'code', 'name', 'price', 'change', 'percent', 'asof', 'state', 'notes', 'strategy', 'date')
DEFAULT_COLUMNS = ('flag', 'code', 'name', 'state', 'notes')


class WatchViews:
    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self):
        self.error = None
        try:
            saved = self.store.load_ui_preferences('watch_views') if self.store else {'revision': 0, 'data': {}}
            self._validate(saved['data'])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.error = str(exc)
            saved = {'revision': 0, 'data': {}}
        self.revision = saved['revision']
        self.data = {'lists': [], 'active': 'all', 'flags': {}, 'order': {}, 'sections': {},
                     'columns': list(DEFAULT_COLUMNS), **deepcopy(saved['data'])}
        if self.data['active'] not in {'all', *[item['id'] for item in self.data['lists']]}:
            self.data['active'] = 'all'

    def save(self):
        if self.error:
            raise ValueError('自选表设置无法读取；原设置已保留，本次仅显示全部关注。请恢复设置备份后重试。')
        self._validate(self.data)
        if self.store:
            result = self.store.save_ui_preferences('watch_views', self.data, self.revision)
            self.revision = result['revision']

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise ValueError('自选表设置不是有效对象')
        lists = data.get('lists', [])
        if not isinstance(lists, list) or len(lists) > 100:
            raise ValueError('自选表数量无效')
        identities = set()
        for item in lists:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in identities:
                raise ValueError('自选表编号无效')
            identities.add(item['id'])
            WatchViews._name(item.get('name'))
            codes = item.get('codes')
            if not isinstance(codes, list) or len(codes) > 5000 or any(code_of(code) != code for code in codes):
                raise ValueError('自选表代码无效')
        if not isinstance(data.get('active', 'all'), str):
            raise ValueError('当前自选表无效')
        columns = data.get('columns', list(DEFAULT_COLUMNS))
        if not isinstance(columns, list) or any(c not in COLUMNS for c in columns):
            raise ValueError('自选表列设置无效')
        flags = data.get('flags', {})
        if not isinstance(flags, dict) or any(code_of(c) != c or v not in FLAGS for c, v in flags.items()):
            raise ValueError('标记设置无效')
        for key in ('order', 'sections'):
            values = data.get(key, {})
            if not isinstance(values, dict):
                raise ValueError('排序或分组设置无效')
            for identity, members in values.items():
                if not isinstance(identity, str) or not isinstance(members, list if key == 'order' else dict):
                    raise ValueError('排序或分组设置无效')
                if len(members) > 5000 or any(code_of(c) != c for c in members):
                    raise ValueError('排序或分组代码无效')
                if key == 'sections' and any(not isinstance(v, str) or len(v) > 40 for v in members.values()):
                    raise ValueError('分组名称无效')
        width = data.get('sidebarWidth')
        if width is not None and (type(width) not in (int, float) or not math.isfinite(width) or not 200 <= width <= 3000):
            raise ValueError('侧栏宽度无效')

    def create(self, name):
        name = self._name(name)
        if any(item['name'] == name for item in self.data['lists']):
            raise ValueError('自选表名称已存在')
        item = {'id': uuid.uuid4().hex, 'name': name, 'codes': []}
        self.data['lists'].append(item)
        self.data['active'] = item['id']
        self.save()
        return item['id']

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40 or any(ord(c) < 32 for c in name):
            raise ValueError('请输入 1–40 个可见字符')
        return name.strip()

    def collection(self, list_id=None):
        return next((item for item in self.data['lists'] if item['id'] == (list_id or self.data['active'])), None)

    def rename(self, name):
        item = self.collection()
        if item is None:
            raise ValueError('全部关注为固定入口')
        name = self._name(name)
        if any(other['id'] != item['id'] and other['name'] == name for other in self.data['lists']):
            raise ValueError('自选表名称已存在')
        item['name'] = name
        self.save()

    def delete(self):
        identity = self.data['active']
        self.data['lists'] = [item for item in self.data['lists'] if item['id'] != identity]
        self.data['active'] = 'all'
        self.save()  # Deleting a view never ends a watch or deletes a drawing.

    def add(self, code, list_id=None):
        code = code_of(code)
        item = self.collection(list_id)
        if item is not None and code not in item['codes']:
            item['codes'].append(code)
            self.save()

    def remove(self, code):
        item = self.collection()
        if item is not None:
            item['codes'] = [c for c in item['codes'] if c != code]
            self.save()

    def flag(self, code, color):
        code = code_of(code)
        if color and color not in FLAGS:
            raise ValueError('未知标记颜色')
        if color:
            self.data['flags'][code] = color
        else:
            self.data['flags'].pop(code, None)
        self.save()

    def visible(self, rows, query=''):
        item = self.collection()
        allowed = set(item['codes']) if item else None
        query = query.strip().casefold()
        result = [row for row in rows if (allowed is None or row['code'] in allowed)
                  and (not query or query in (row['code'] + ' ' + row['name']).casefold())]
        order = self.data['order'].get(self.data['active'], [])
        indices = {code: n for n, code in enumerate(order)}
        return sorted(result, key=lambda row: indices.get(row['code'], len(order)))

    def reorder(self, codes):
        self.data['order'][self.data['active']] = list(dict.fromkeys(codes))
        self.save()

    def columns(self):
        selected = set(self.data['columns'])
        return tuple(c for c in COLUMNS if c in selected or c in ('code', 'name'))


def import_codes(text):
    """Validate the entire import before the first Store write."""
    reader = csv.reader(io.StringIO(text.lstrip('\ufeff')))
    codes = []
    for n, row in enumerate(reader):
        if not row or not row[0].strip():
            continue
        value = row[0].strip()
        if n == 0 and value.casefold() in ('code', '代码', 'symbol'):
            continue
        codes.append(code_of(value))
        if len(codes) > 5000:
            raise ValueError('一次最多导入 5000 个标的')
    return list(dict.fromkeys(codes))


def export_rows(rows):
    target = io.StringIO(newline='')
    writer = csv.writer(target)
    writer.writerow(('代码', '名称', '备注'))
    for row in rows:
        values = [row.get(key, '') for key in ('code', 'name', 'notes')]
        writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
    return target.getvalue()
