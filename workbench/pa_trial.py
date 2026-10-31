"""Explicit screenshot presentation settings, stored with the private case."""
import json
import math

from .pa_growth import _owner, _case_row
from .store import dumps


def validate(value, image_ids):
    if not isinstance(value, dict):
        raise ValueError('请设置入场图和后续图，或标记截图分界')
    mode = value.get('mode')
    ids = value.get('image_ids')
    if (not isinstance(ids, list) or not ids or len(ids) > 100
            or any(not isinstance(i, str) or i not in image_ids for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('试炼图片不属于此案例或顺序重复')
    if mode == 'sequence' and len(ids) >= 2:
        return {'mode': mode, 'image_ids': ids}
    cut = value.get('cutoff')
    if (mode == 'mask' and len(ids) == 1 and type(cut) in (int, float)
            and math.isfinite(cut) and .05 <= cut <= .95):
        return {'mode': mode, 'image_ids': ids, 'cutoff': float(cut)}
    raise ValueError('分阶段试炼至少两张图；单图请标记 5%—95% 之间的入场分界')


def settings(store, case_id, value=None):
    owner = _owner(store)
    _case_row(store, case_id, owner)
    key = f'pa_trial_setup:{owner}:{case_id}'
    def image_ids():
        return {i['asset_id'] for i in store.rows(
            'SELECT i.asset_id FROM pa_case_images i JOIN pa_assets a ON a.id=i.asset_id '
            'WHERE i.case_id=? AND a.owner=?', (case_id, owner))}
    if value is not None:
        value = validate(value, image_ids())
        store.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dumps(value)))
        return value
    rows = store.rows('SELECT value FROM meta WHERE key=?', (key,))
    return validate(json.loads(rows[0]['value']), image_ids()) if rows else None
