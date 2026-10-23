"""Opt-in MTR fast rejection with exact bindings, shadow checks and a fuse."""
from __future__ import annotations

import json
import math
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .store import ROOT, digest, dumps, now


MODE_ENV = 'APLUS_MTR_FAST_REJECT_MODE'
APPROVAL_ENV = 'APLUS_MTR_FAST_REJECT_APPROVED_BINDING'
CANARY_RATE_ENV = 'APLUS_MTR_FAST_REJECT_CANARY_RATE'
VALID_MODES = {'OFF', 'SHADOW', 'CANARY', 'ACCELERATED'}

EXPECTED_RELEASE = '185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c'
EXPECTED_CALCULATION = '35099253154b1ebc3cee060a51054370522b665c9215580658204cc763bb6ca1'
EXPECTED_ADAPTER_SHA = '844f211f2b09eb10e8217d0aa8421b52cf738d7b56623b05925e4d9ccc138a92'
EXPECTED_METADATA = {
    'strategy_revision': 'V36.4',
    'signal_column': 'signal_mtr',
    'signal_close_position_column': 'mtr_signal_close_position',
    'signal_close_position_min': 1.0,
    'supported_timeframes': ['daily'],
}
SUPPORTED_RUNTIMES = {
    ('Windows', '3.12', '2.5.3', '2.3.3'),  # Locked GitHub validation environment.
    ('Windows', '3.14', '2.4.1', '2.3.3'),  # Audited development environment.
}


def _runtime():
    return (platform.system(), f'{sys.version_info.major}.{sys.version_info.minor}',
            np.__version__, pd.__version__)


def current_binding_id():
    """Stable approval identity; any accelerator edit requires fresh approval."""
    source_sha = digest(Path(__file__).read_bytes())
    service_sha = digest((ROOT / 'workbench/service.py').read_bytes())
    return digest(dumps({
        'contract': 'mtr-fast-reject-v1',
        'release': EXPECTED_RELEASE,
        'calculation': EXPECTED_CALCULATION,
        'adapter': EXPECTED_ADAPTER_SHA,
        'metadata': EXPECTED_METADATA,
        'runtime': _runtime(),
        'source': source_sha,
        'service': service_sha,
    }).encode())


def requested_mode():
    """Read the opt-in switch without touching the normal OFF scan path."""
    return str(os.environ.get(MODE_ENV, 'OFF')).strip().upper()


@dataclass(frozen=True)
class GateAssessment:
    safe: bool
    reject: bool
    reason: str


@dataclass(frozen=True)
class FastResult:
    signal: object
    event_action: str | None = None
    event_detail: dict | None = None


def assess_latest_bar(frame, prepared):
    """Prove the input domain, then apply the frozen signal-bar necessity."""
    required = ['date', 'open', 'high', 'low', 'close', 'volume']
    if not isinstance(frame, pd.DataFrame) or not isinstance(prepared, pd.DataFrame):
        return GateAssessment(False, False, '输入不是 DataFrame')
    if len(frame) < 125 or len(prepared) != min(len(frame), 300):
        return GateAssessment(False, False, '输入长度不属于正式扫描域')
    if any(column not in frame or column not in prepared for column in required):
        return GateAssessment(False, False, 'OHLCV 字段不完整')
    if 'atr' not in prepared:
        return GateAssessment(False, False, '公共指标缺少 ATR')

    raw = frame.tail(300).reset_index(drop=True)
    raw_dates = raw['date'].reset_index(drop=True)
    prepared_dates = prepared['date'].reset_index(drop=True)
    if raw_dates.isna().any() or prepared_dates.isna().any():
        return GateAssessment(False, False, '日期包含空值')
    dates = prepared_dates.astype(str)
    if not dates.equals(raw_dates.astype(str)):
        return GateAssessment(False, False, '公共指标日期与原输入不一致')
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        return GateAssessment(False, False, '日期顺序或唯一性异常')

    try:
        raw_values = raw[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
        values = prepared[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
        atr = pd.to_numeric(prepared['atr'], errors='coerce').to_numpy(dtype=float)
    except (TypeError, ValueError):
        return GateAssessment(False, False, '价格或指标无法转换为数值')
    if not np.array_equal(values, raw_values, equal_nan=True):
        return GateAssessment(False, False, '公共指标改变了原始 OHLCV')
    if not np.isfinite(values).all():
        return GateAssessment(False, False, 'OHLCV 含非有限数值')
    opening, high, low, close, volume = values.T
    valid_prices = ((low > 0) & (low <= opening) & (low <= close)
                    & (high >= opening) & (high >= close) & (high >= low))
    if not valid_prices.all() or (volume < 0).any():
        return GateAssessment(False, False, 'OHLCV 合法性检查失败')
    if len(atr) < 20 or not np.isfinite(atr[20:]).all() or (atr[20:] <= 0).any():
        return GateAssessment(False, False, 'MTR 所需 ATR 不属于可信输入域')

    last_open, last_high, last_low, last_close, _last_volume = values[-1]
    qualifies = (last_high - last_low > 0 and last_close > last_open
                 and math.isclose(last_close, last_high, rel_tol=1e-9, abs_tol=1e-9))
    return GateAssessment(True, not qualifies,
                          '末根不是冻结 MTR 合格信号K' if not qualifies else '末根可能是信号K')


class MTRFastRejector:
    """One scan-local controller. OFF is byte-for-byte the normal call path."""

    def __init__(self, store, job, spec):
        self.store = store
        self.job = job
        self.spec = spec
        self.requested_mode = requested_mode()
        self.mode = 'OFF'
        self.rate = 0.0
        self.fused = False
        self.fuse_persist_error = None
        self.fuse_evidence = None
        self.binding_id = current_binding_id()
        if self.requested_mode == 'OFF':
            return
        if self.requested_mode not in VALID_MODES:
            self._fallback('未知模式，已回退原版')
            return
        try:
            safe, reason = self._binding_matches()
            if not safe:
                self._fallback(reason)
                return
            if self.requested_mode in {'CANARY', 'ACCELERATED'}:
                if os.environ.get(APPROVAL_ENV) != self.binding_id:
                    self._fallback('未绑定当前精确版本的人工授权，已回退原版')
                    return
                self.rate = float(os.environ.get(CANARY_RATE_ENV, '0.05'))
                if not 0 <= self.rate <= 1:
                    self._fallback('canary 比例不合法，已回退原版')
                    return
            if self._stored_fuse():
                self.fused = True
                self._fallback('当前绑定已熔断，已回退原版')
                return
            self.mode = self.requested_mode
        except Exception as exc:
            self._fallback(f'灰度初始化异常，已回退原版：{type(exc).__name__}: {exc}')

    @classmethod
    def disabled(cls, store, job, spec):
        instance = cls.__new__(cls)
        instance.store, instance.job, instance.spec = store, job, spec
        instance.requested_mode = instance.mode = 'OFF'
        instance.rate = 0.0
        instance.fused = False
        instance.fuse_persist_error = None
        instance.fuse_evidence = None
        instance.binding_id = current_binding_id()
        return instance

    @property
    def active(self):
        return self.mode != 'OFF'

    @property
    def cache_tag(self):
        if not self.active:
            return None
        return dumps([self.binding_id, self.mode, self.rate])

    def cache_engine(self, engine):
        return digest(dumps([engine, self.cache_tag]).encode()) if self.cache_tag else engine

    def _binding_matches(self):
        if self.spec is None or self.spec.id != 'MTR_MASTER':
            return False, '本轮没有精确 MTR_MASTER，已回退原版'
        try:
            relative = Path(self.spec.file).resolve().relative_to(ROOT.resolve()).as_posix()
        except (OSError, ValueError):
            return False, 'MTR 文件不在受保护工程内，已回退原版'
        if relative != 'core/strategies/mtr_strategy.py' or self.spec.version != EXPECTED_RELEASE:
            return False, 'MTR 策略版本变化，已回退原版'
        if digest((ROOT / 'workbench/strategies.py').read_bytes()) != EXPECTED_ADAPTER_SHA:
            return False, '正式输出适配层变化，已回退原版'
        from .scan_cache import calculation_versions
        if calculation_versions([self.spec])[self.spec.id] != EXPECTED_CALCULATION:
            return False, 'MTR 关键依赖变化，已回退原版'
        meta = self.spec.cls.get_metadata()
        if any(meta.get(key) != value for key, value in EXPECTED_METADATA.items()):
            return False, 'MTR 元数据契约变化，已回退原版'
        if _runtime() not in SUPPORTED_RUNTIMES:
            return False, f'运行环境 {_runtime()} 未经绑定验证，已回退原版'
        return True, '精确绑定通过'

    @property
    def _fuse_key(self):
        return 'mtr_fast_reject:fuse:' + self.binding_id

    def _stored_fuse(self):
        if self.store.rows('SELECT value FROM meta WHERE key=?', (self._fuse_key,)):
            return True
        return bool(self.store.rows(
            "SELECT seq FROM events WHERE action='MTR快速拒绝熔断' AND detail LIKE ? LIMIT 1",
            (f'%{self.binding_id}%',)))

    def _fallback(self, reason):
        self.mode = 'OFF'
        try:
            self.store.event(self.job, 'MTR快速拒绝回退原版', mode=self.requested_mode,
                             binding=self.binding_id, reason=reason)
        except Exception:
            pass  # Optional acceleration can never make the original path unavailable.

    def _sampled(self, sample_key):
        value = int(digest(str(sample_key).encode())[:16], 16) / float(0xffffffffffffffff)
        return value < self.rate

    def _trip_fuse(self, reason, sample_key, original=None, exception=None):
        self.fused = True
        self.mode = 'OFF'
        evidence = {
            'time': now(), 'job': self.job, 'binding': self.binding_id,
            'requested_mode': self.requested_mode,
            'sample': digest(str(sample_key).encode()), 'reason': reason,
            'original': original,
        }
        if exception is not None:
            evidence['exception'] = {'type': type(exception).__name__, 'message': str(exception)}
        self.fuse_evidence = evidence
        try:
            with self.store.connect() as db:
                db.execute('INSERT INTO meta(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (self._fuse_key, dumps(evidence)))
                db.execute('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)',
                           (now(), self.job, 'MTR快速拒绝熔断', '', dumps(evidence)))
        except Exception as exc:
            # Preserve the frozen detector's result/exception even when the
            # diagnostic database itself is temporarily unavailable.
            self.fuse_persist_error = f'{type(exc).__name__}: {exc}'

    def run(self, original, frame, prepared, sample_key):
        if not self.active:
            return FastResult(original())
        try:
            gate = assess_latest_bar(frame, prepared)
        except Exception as exc:
            signal = original()
            return FastResult(signal, 'MTR快速拒绝回退原版', {
                'mode': self.mode, 'binding': self.binding_id,
                'reason': f'门禁异常：{type(exc).__name__}: {exc}'})
        if not gate.safe:
            return FastResult(original(), 'MTR快速拒绝回退原版', {
                'mode': self.mode, 'binding': self.binding_id, 'reason': gate.reason})
        if not gate.reject:
            return FastResult(original())

        sampled = self.mode == 'SHADOW' or self._sampled(sample_key)
        if sampled:
            try:
                signal = original()
            except Exception as exc:
                self._trip_fuse('门禁拒绝样本的原版发生业务异常', sample_key, exception=exc)
                raise
            if signal is not None:
                self._trip_fuse('门禁拒绝样本与原版正式结果不一致', sample_key, original=signal)
                return FastResult(signal)
            return FastResult(None, 'MTR快速拒绝旁路对拍', {
                'mode': self.requested_mode, 'binding': self.binding_id,
                'sample': digest(str(sample_key).encode()), 'matched': True,
                'gate_reason': gate.reason})

        return FastResult(None, 'MTR快速拒绝', {
            'mode': self.mode, 'binding': self.binding_id,
            'sample': digest(str(sample_key).encode()), 'gate_reason': gate.reason})
