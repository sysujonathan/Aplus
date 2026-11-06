"""UI-only workspace layouts and date/price drawing visibility contracts."""
from copy import deepcopy
import uuid

PERIODS = ('m1', 'm5', 'm15', 'm30', 'm60', 'daily', 'weekly', 'monthly')
DEFAULT_PERIOD_FAVORITES = ('m5', 'm60', 'daily', 'weekly', 'monthly')
SYNC_KEYS = ('symbol', 'timeframe', 'crosshair', 'time', 'range')
SHAPES = {1: (1, 1), 4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3)}

def _favorite_periods(document):
    saved = document['data'].get('favorites', DEFAULT_PERIOD_FAVORITES)
    if not isinstance(saved, (list, tuple)):
        raise ValueError('周期收藏设置无效')
    return [period for period in PERIODS if period in saved]


def load_period_favorites(store):
    return _favorite_periods(store.load_ui_preferences('chart_periods'))


def save_period_favorite(store, period, favorite):
    if period not in PERIODS or type(favorite) is not bool:
        raise ValueError('周期收藏选项无效')
    current = store.load_ui_preferences('chart_periods')
    selected = _favorite_periods(current)
    if favorite == (period in selected):
        return selected
    selected = set(selected)
    if favorite:
        selected.add(period)
    else:
        selected.remove(period)
    favorites = [value for value in PERIODS if value in selected]
    saved = store.save_ui_preferences('chart_periods', {'favorites': favorites}, current['revision'])
    return _favorite_periods(saved)


def validate_layout(value):
    value = deepcopy(value)
    if not isinstance(value, dict) or not isinstance(value.get('name'), str) or not 1 <= len(value['name'].strip()) <= 40:
        raise ValueError('布局名称须为 1–40 个字符')
    rows, columns = value.get('rows'), value.get('columns')
    if not isinstance(value.get('id'), str) or not value['id'] or len(value['id']) > 80:
        raise ValueError('布局编号无效')
    if type(rows) is not int or type(columns) is not int or not 1 <= rows <= 3 or not 1 <= columns <= 4 or rows * columns > 9:
        raise ValueError('布局最多九格、三行四列')
    if value.get('drawingScope', 'pane') not in ('pane', 'layout', 'global'):
        raise ValueError('绘图共享范围无效')
    if not isinstance(value.get('sync', {}), dict):
        raise ValueError('联动设置无效')
    value['sync'] = {key: bool(value.get('sync', {}).get(key, False)) for key in SYNC_KEYS}
    panes = value.get('panes', [])
    if not isinstance(panes, list) or len(panes) > rows * columns:
        raise ValueError('图格设置无效')
    for pane in panes:
        if isinstance(pane, dict) and pane.get('timeframe') == 'm240':
            pane['timeframe'] = 'm60'
        if not isinstance(pane, dict) or pane.get('timeframe', 'daily') not in PERIODS:
            raise ValueError('图格周期无效')
        item = pane.get('item')
        if item is not None:
            from .market import code_of
            if not isinstance(item, dict):
                raise ValueError('图格标的无效')
            try:
                required = {'code', 'name', 'observation_id', 'source', 'mode'}
                optional = {'strategy', 'timeframe', 'market_dataset_id', 'market_asof', 'anchor_asof', 'manual'}
                if not required <= item.keys() or item.keys() - required - optional:
                    raise ValueError('图格标的设置不完整')
                if code_of(item['code']) != item['code'] or item['mode'] not in ('candidate', 'watch'):
                    raise ValueError('图格标的无效')
            except TypeError as exc:
                raise ValueError('图格标的设置不完整') from exc
        span = pane.get('range')
        if span is not None and (not isinstance(span, list) or len(span) != 2 or any(
                v is not None and (not isinstance(v, str) or len(v) > 40) for v in span)):
            raise ValueError('图格日期范围无效')
    value['active'] = max(0, min(int(value.get('active', 0)), rows * columns - 1))
    value['name'] = value['name'].strip()
    return value


def new_layout(name='默认布局', rows=1, columns=1):
    return validate_layout(dict(id=uuid.uuid4().hex, name=name, rows=rows, columns=columns,
        panes=[], active=0, drawingScope='pane', sync={key: False for key in SYNC_KEYS}))


def drawing_visible(drawing, workspace):
    """Legacy drawings remain global; source/adjustment identity is checked upstream."""
    scope = (drawing.get('web') or {}).get('shareScope')
    if not scope or scope.get('kind') == 'global':
        return True
    if not workspace:
        return False
    return (scope.get('layout') == workspace.get('layout') and
            (scope.get('kind') == 'layout' or scope.get('pane') == workspace.get('pane')))


def validate_scope(scope):
    if scope is None:
        return
    if not isinstance(scope, dict) or scope.get('kind') not in ('pane', 'layout', 'global'):
        raise ValueError('绘图共享范围无效')
    if scope['kind'] != 'global' and not isinstance(scope.get('layout'), str):
        raise ValueError('绘图缺少布局编号')
    if scope['kind'] == 'pane' and not isinstance(scope.get('pane'), str):
        raise ValueError('绘图缺少图格编号')


def merge_drawings(base, current, incoming, workspace):
    """Three-way, per-object CAS: unrelated edits merge, same-object edits fail."""
    baseline = {obj['id']: obj for obj in base if drawing_visible(obj, workspace)}
    latest = {obj['id']: obj for obj in current}
    edited = {obj['id']: obj for obj in incoming}
    if len(edited) != len(incoming):
        raise ValueError('绘图编号重复')
    result = deepcopy(latest)
    for identity in baseline.keys() | edited.keys():
        old, new = baseline.get(identity), edited.get(identity)
        if _semantic_object(old) == _semantic_object(new):
            continue
        now = latest.get(identity)
        if _semantic_object(now) != _semantic_object(old) and _semantic_object(now) != _semantic_object(new):
            raise ValueError('同一图形已在另一图格修改，请刷新后重试；本图修改仍保留')
        if old is None and identity in latest and not drawing_visible(latest[identity], workspace):
            raise ValueError('不能覆盖其他图格的私有图形')
        if new is None:
            result.pop(identity, None)
        else:
            scope = (new.get('web') or {}).get('shareScope')
            validate_scope(scope)
            if scope and scope.get('kind') != 'global' and scope.get('layout') != workspace.get('layout'):
                raise ValueError('不能写入其他布局的私有图形')
            if scope and scope.get('kind') == 'pane' and scope.get('pane') != workspace.get('pane'):
                raise ValueError('不能写入其他图格的私有图形')
            result[identity] = deepcopy(new)
    return list(result.values())


def _semantic_object(value):
    """Projected bar indices are caches; canonical dates/prices own the object."""
    value = deepcopy(value)
    if isinstance(value, dict):
        web = value.get('web') or {}
        web.pop('createdAtIndex', None)
        for key in ('a', 'b', 'c'):
            point = web.get(key)
            if isinstance(point, dict) and point.get('date'):
                point.pop('index', None)
        for key in ('points', 'pathPoints'):
            for point in web.get(key, []):
                if isinstance(point, dict) and point.get('date'):
                    point.pop('index', None)
    return value


def copy_layout_document(document, origin, target):
    """Private drawings become independent copies; global objects stay references."""
    from .chart_workspace import validate_document
    result = deepcopy(document)
    for drawing in document['drawings']:
        scope = (drawing.get('web') or {}).get('shareScope')
        if not scope or scope.get('kind') == 'global' or scope.get('layout') != origin:
            continue
        copied = deepcopy(drawing)
        copied['id'] = uuid.uuid4().hex
        copied['web']['id'] = copied['id']
        copied['web']['shareScope']['layout'] = target
        result['drawings'].append(copied)
    states = result['settings'].get('pane_states', {})
    for key, state in list(states.items()):
        if key.startswith(origin + ':'):
            states[target + key[len(origin):]] = deepcopy(state)
    return validate_document(result)
