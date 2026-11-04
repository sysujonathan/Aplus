"""Random screenshot practice, not historical bar replay or strict blind tests."""
import json
import secrets

from .pa_growth import _owner
from .store import dumps


def choose(store, scope='public', drive=None):
    if scope not in ('public', 'private'):
        raise ValueError('试炼范围无效')
    if scope == 'public':
        cases = store.pa_shared('catalogue', drive=drive)
        candidates = [c['id'] for c in cases if c.get('trial_setup')]
    else:
        candidates = []
        for c in store.pa_case_metadata():
            try:
                if store.pa_trial_setup(c['id']):
                    candidates.append(c['id'])
            except ValueError:
                continue
    if not candidates:
        return {'case_id': None, 'eligible': 0}
    status = store.pa_shared('status', drive=drive)
    owner = status.get('user', {}).get('id') or _owner(store)
    key = f'pa_random_recent:{owner}:{scope}'
    with store.atomic_write():
        rows = store.rows('SELECT value FROM meta WHERE key=?', (key,))
        recent = json.loads(rows[0]['value']) if rows else []
        # Exclude as many recent cases as the pool permits, including across
        # restart, without pretending a one-case pool can avoid repetition.
        window = min(20, len(candidates) - 1)
        excluded = set(recent[-window:]) if window else set()
        available = [c for c in candidates if c not in excluded] or candidates
        case_id = secrets.choice(available)
        store.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                      (key, dumps((recent + [case_id])[-20:])))
    return dict(case_id=case_id, eligible=len(candidates), practice_kind='progressive-screenshot')
