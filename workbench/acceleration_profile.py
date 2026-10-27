"""Explicit per-user authorization for normal accelerated startup; no database writes."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import platform
import sqlite3
import sys
import uuid

import numpy as np
import pandas as pd

from . import mtr_acceleration as mtr
from . import three_k_acceleration as three_k
from . import gap_family_acceleration as family
from .store import ROOT, digest, dumps, resolve_runtime_root
from .strategies import catalog

PROFILE_ENV = 'APLUS_ACCELERATION_PROFILE'
STRATEGIES = {
    'MTR': ('MTR_MASTER', 'APLUS_MTR_FAST_REJECT'),
    '3K': ('STRATEGY_3K', 'APLUS_THREE_K_FAST_REJECT'),
    'Gap Pinbar': ('STRATEGY_GAP_PINBAR', 'APLUS_GAP_PINBAR_FAST_REJECT'),
    'AWIL': ('STRATEGY_AWIL', 'APLUS_AWIL_FAST_REJECT'),
    'Structural Gap': ('STRATEGY_STRUCTURAL_GAP', 'APLUS_STRUCTURAL_GAP_FAST_REJECT'),
}


def profile_path():
    if os.environ.get(PROFILE_ENV):
        return Path(os.environ[PROFILE_ENV]).expanduser().resolve()
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    return base / 'Aplus' / 'acceleration.json'


def startup_binding():
    # A startup-policy change cannot silently reuse an older approval.
    paths = ('workbench/acceleration_profile.py', 'launch_dashboard.py', 'app.py')
    return digest(dumps({name: digest((ROOT / name).read_bytes()) for name in paths}).encode())


def runtime_identity():
    return dict(system=platform.system(), python=platform.python_version(),
                numpy=np.__version__, pandas=pd.__version__,
                environment=str(Path(sys.prefix).resolve()).casefold())


class ReadOnlyState:
    """Only builtin specs and persisted fuse evidence are needed for mode checks."""
    def __init__(self, root=None):
        self.root = resolve_runtime_root(root)
        self.events = []

    def rows(self, sql, params=()):
        if sql == 'SELECT * FROM registry ORDER BY created':
            return []  # Do not execute research plugins to inspect five builtin modes.
        path = self.root / 'workbench.sqlite3'
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            return [dict(row) for row in db.execute(sql, params)]

    def event(self, _job, action, _path='', **detail):
        self.events.append(dict(action=action, **detail))


def _controller(name, state, spec):
    if name == 'MTR':
        return mtr.MTRFastRejector(state, None, spec)
    if name == '3K':
        return three_k.ThreeKFastRejector(state, None, spec)
    return family.GapFamilyFastRejector(state, None, spec)


def bindings():
    return {name: (mtr.current_binding_id() if name == 'MTR' else
                   three_k.current_binding_id() if name == '3K' else
                   family.current_binding_id(strategy_id))
            for name, (strategy_id, _prefix) in STRATEGIES.items()}


def _read(path):
    if path.stat().st_size > 65536:
        raise ValueError('授权配置过大')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('授权配置格式不支持')
    return value


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.approval-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(dumps(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _probe_environment(entries):
    changes = {}
    for name, (_strategy_id, prefix) in STRATEGIES.items():
        entry = entries[name]
        changes.update({prefix + '_MODE': entry['mode'],
                        prefix + '_APPROVED_BINDING': entry['binding'],
                        prefix + '_CANARY_RATE': '0.05'})
    previous = {key: os.environ.get(key) for key in changes}
    try:
        os.environ.update(changes)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def effective_status(root=None, profile=None):
    state = ReadOnlyState(root)
    result = dict(profile=profile or {}, strategies={})
    try:
        specs = catalog(state)  # Includes the existing byte-for-byte frozen check.
        for name, (strategy_id, prefix) in STRATEGIES.items():
            before = len(state.events)
            fast = _controller(name, state, specs[strategy_id])
            events = state.events[before:]
            reason = events[-1].get('reason', '') if events else ''
            if not fast.active and not reason:
                reason = ('OFF 开关生效，执行原版' if (profile or {}).get('loaded')
                          else (profile or {}).get('reason') or 'OFF 开关生效，执行原版')
            result['strategies'][name] = dict(
                requested=os.environ.get(prefix + '_MODE', 'OFF'), effective=fast.mode,
                original_audit_rate=fast.rate, binding=fast.binding_id, reason=reason)
    except Exception as exc:
        # A locked/unreadable state or frozen-check failure is never reported as accelerated.
        reason = f'{type(exc).__name__}: {exc}'
        for name in STRATEGIES:
            result['strategies'][name] = dict(effective='OFF', reason=reason)
    return result


def approve(*, confirmed=False, path=None, root=None):
    if not confirmed:
        raise ValueError('需要明确人工授权；使用 approve --confirm')
    entries = {name: dict(mode='ACCELERATED', binding=value) for name, value in bindings().items()}
    with _probe_environment(entries):
        status = effective_status(root)
    failed = {name: item['reason'] for name, item in status['strategies'].items()
              if item['effective'] != 'ACCELERATED'}
    if failed:
        raise ValueError('未写入授权，以下策略无法启用：' + dumps(failed))
    value = dict(schema=1, target='ACCELERATED', original_audit_rate=.05,
                 startup_binding=startup_binding(), runtime=runtime_identity(),
                 approved_at=datetime.now(timezone.utc).isoformat(), strategies=entries)
    _write(Path(path) if path else profile_path(), value)
    return value


def apply_profile(path=None):
    """Read existing authorization only. Never generate bindings or renew approval here."""
    path = Path(path) if path else profile_path()
    report = dict(path=str(path), loaded=False, reason='未找到本机授权配置，执行原版')
    try:
        if not path.exists():
            return report
        value = _read(path)
        if value.get('startup_binding') != startup_binding():
            raise ValueError('启动代码版本变化，原授权失效；需重新明确授权')
        if value.get('runtime') != runtime_identity():
            raise ValueError('目标 Windows/Python/依赖环境变化，原授权失效')
        if value.get('original_audit_rate') != .05:
            raise ValueError('稳定快速版要求保留 5% 原版抽查')
        entries = value.get('strategies')
        if not isinstance(entries, dict):
            raise ValueError('缺少逐策略授权')
        for name, (_strategy_id, prefix) in STRATEGIES.items():
            entry = entries.get(name, {})
            if not isinstance(entry, dict):
                entry = {}
            mode = entry.get('mode', 'OFF')
            binding = entry.get('binding', '')
            if (not isinstance(mode, str) or mode not in {'OFF', 'ACCELERATED'}
                    or not isinstance(binding, str) or len(binding) != 64):
                mode, binding = 'OFF', ''
            # Either explicit OFF wins. A local emergency stop must not be
            # defeated by an older ACCELERATED variable inherited at startup.
            if mode == 'OFF':
                os.environ[prefix + '_MODE'] = 'OFF'
            else:
                os.environ.setdefault(prefix + '_MODE', mode)
            os.environ.setdefault(prefix + '_APPROVED_BINDING', binding)
            if os.environ[prefix + '_MODE'] == 'ACCELERATED':
                os.environ[prefix + '_CANARY_RATE'] = '0.05'
            else:
                os.environ.setdefault(prefix + '_CANARY_RATE', '0.05')
        report.update(loaded=True, reason='本机明确授权已加载；实际模式仍由原控制器核验')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        report['reason'] = f'授权配置未生效：{exc}'
    return report


def set_off(name='all', path=None):
    path = Path(path) if path else profile_path()
    value = _read(path)  # Do not manufacture a new approval or erase fuse evidence.
    names = tuple(STRATEGIES) if name == 'all' else (name,)
    for selected in names:
        if selected not in STRATEGIES:
            raise ValueError('未知策略：' + selected)
        value.setdefault('strategies', {}).setdefault(selected, {})['mode'] = 'OFF'
    _write(path, value)


def configure_service(store):
    """Shared desktop/web startup entry; scanning and controllers remain unchanged."""
    from .service import Service
    profile = apply_profile()
    service = Service(store)
    service.acceleration_status = effective_status(store.root, profile)
    logging.getLogger(__name__).warning('稳定快速版实际模式：%s', dumps(service.acceleration_status))
    return service
