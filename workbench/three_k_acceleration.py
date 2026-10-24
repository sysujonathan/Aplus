"""Opt-in 3K fast rejection with exact bindings, shadow checks and a fuse."""
from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .store import ROOT, digest, dumps, now


MODE_ENV = 'APLUS_THREE_K_FAST_REJECT_MODE'
APPROVAL_ENV = 'APLUS_THREE_K_FAST_REJECT_APPROVED_BINDING'
CANARY_RATE_ENV = 'APLUS_THREE_K_FAST_REJECT_CANARY_RATE'
VALID_MODES = {'OFF', 'SHADOW', 'CANARY', 'ACCELERATED'}

EXPECTED_RELEASE = '185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c'
EXPECTED_CALCULATION = '7ee2750c5c14e317f2755158589bf6eac37bf40ec5b4282e0bf4ab17ac5e19e5'
EXPECTED_ADAPTER_SHA = '844f211f2b09eb10e8217d0aa8421b52cf738d7b56623b05925e4d9ccc138a92'
EXPECTED_METADATA = {
    'display_name': '3K',
    'signal_column': 'signal_3k_gap_test',
    'entry_column': 'entry_3k_gap_test',
    'sl_column': 'sl_3k_gap_test',
    'tp_columns': ['tp_3k_gap_test'],
    'supported_timeframes': ['daily'],
}
SUPPORTED_RUNTIMES = {
    ('Windows', '3.12', '2.5.3', '2.3.3'),
    ('Windows', '3.14', '2.4.1', '2.3.3'),
}


def _runtime():
    return (platform.system(), f'{sys.version_info.major}.{sys.version_info.minor}',
            np.__version__, pd.__version__)


def current_binding_id():
    source_sha = digest(Path(__file__).read_bytes())
    service_sha = digest((ROOT / 'workbench/service.py').read_bytes())
    return digest(dumps({
        'contract': 'three-k-fast-reject-v1',
        'release': EXPECTED_RELEASE,
        'calculation': EXPECTED_CALCULATION,
        'adapter': EXPECTED_ADAPTER_SHA,
        'metadata': EXPECTED_METADATA,
        'runtime': _runtime(),
        'source': source_sha,
        'service': service_sha,
    }).encode())


def requested_mode():
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
    """Validate the formal input, then apply 3K's latest confirmation necessity."""
    required = ['date', 'open', 'high', 'low', 'close', 'volume']
    prepared_required = required + [
        'ema20', 'atr', 'body_pct', 'lower_wick_pct', 'upper_wick_pct',
        'close_loc', 'is_bullish',
    ]
    if not isinstance(frame, pd.DataFrame) or not isinstance(prepared, pd.DataFrame):
        return GateAssessment(False, False, '输入不是 DataFrame')
    if len(frame) < 125 or len(prepared) != min(len(frame), 300):
        return GateAssessment(False, False, '输入长度不属于正式扫描域')
    if any(column not in frame for column in required):
        return GateAssessment(False, False, 'OHLCV 字段不完整')
    if any(column not in prepared for column in prepared_required):
        return GateAssessment(False, False, '公共指标字段不完整')

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
    expected_bullish = close > opening
    try:
        actual_bullish = prepared['is_bullish'].fillna(False).astype(bool).to_numpy()
    except (TypeError, ValueError):
        return GateAssessment(False, False, '公共阳线指标无法验证')
    if not np.array_equal(actual_bullish, expected_bullish):
        return GateAssessment(False, False, '公共阳线指标与原始价格不一致')

    qualifies = bool(expected_bullish[-1] and close[-1] > high[-2])
    reason = ('末根可能形成3K回调确认' if qualifies
              else '末根不是阳线或未收盘突破前一根最高价')
    return GateAssessment(True, not qualifies, reason)


class ThreeKFastRejector:
    """One scan-local controller. OFF keeps the existing 3K path untouched."""

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

    @property
    def active(self):
        return self.mode != 'OFF'

    @property
    def cache_tag(self):
        return dumps([self.binding_id, self.mode, self.rate]) if self.active else None

    def cache_engine(self, engine):
        return digest(dumps([engine, self.cache_tag]).encode()) if self.cache_tag else engine

    def _binding_matches(self):
        if self.spec is None or self.spec.id != 'STRATEGY_3K':
            return False, '本轮没有精确 STRATEGY_3K，已回退原版'
        try:
            relative = Path(self.spec.file).resolve().relative_to(ROOT.resolve()).as_posix()
        except (OSError, ValueError):
            return False, '3K 文件不在受保护工程内，已回退原版'
        if relative != 'core/strategies/three_k_strategy.py' or self.spec.version != EXPECTED_RELEASE:
            return False, '3K 策略版本变化，已回退原版'
        if digest((ROOT / 'workbench/strategies.py').read_bytes()) != EXPECTED_ADAPTER_SHA:
            return False, '正式输出适配层变化，已回退原版'
        from .scan_cache import calculation_versions
        if calculation_versions([self.spec])[self.spec.id] != EXPECTED_CALCULATION:
            return False, '3K 关键依赖变化，已回退原版'
        meta = self.spec.cls.get_metadata()
        if any(meta.get(key) != value for key, value in EXPECTED_METADATA.items()):
            return False, '3K 元数据契约变化，已回退原版'
        if _runtime() not in SUPPORTED_RUNTIMES:
            return False, f'运行环境 {_runtime()} 未经绑定验证，已回退原版'
        return True, '精确绑定通过'

    @property
    def _fuse_key(self):
        return 'three_k_fast_reject:fuse:' + self.binding_id

    def _stored_fuse(self):
        if self.store.rows('SELECT value FROM meta WHERE key=?', (self._fuse_key,)):
            return True
        return bool(self.store.rows(
            "SELECT seq FROM events WHERE action='3K快速拒绝熔断' AND detail LIKE ? LIMIT 1",
            (f'%{self.binding_id}%',)))

    def _fallback(self, reason):
        self.mode = 'OFF'
        try:
            self.store.event(self.job, '3K快速拒绝回退原版', mode=self.requested_mode,
                             binding=self.binding_id, reason=reason)
        except Exception:
            pass

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
                           (now(), self.job, '3K快速拒绝熔断', '', dumps(evidence)))
        except Exception as exc:
            self.fuse_persist_error = f'{type(exc).__name__}: {exc}'

    def run(self, original, frame, prepared, sample_key):
        if not self.active:
            return FastResult(original())
        try:
            gate = assess_latest_bar(frame, prepared)
        except Exception as exc:
            return FastResult(original(), '3K快速拒绝回退原版', {
                'mode': self.mode, 'binding': self.binding_id,
                'reason': f'门禁异常：{type(exc).__name__}: {exc}'})
        if not gate.safe:
            return FastResult(original(), '3K快速拒绝回退原版', {
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
            return FastResult(None, '3K快速拒绝旁路对拍', {
                'mode': self.requested_mode, 'binding': self.binding_id,
                'sample': digest(str(sample_key).encode()), 'matched': True,
                'gate_reason': gate.reason})

        return FastResult(None, '3K快速拒绝', {
            'mode': self.mode, 'binding': self.binding_id,
            'sample': digest(str(sample_key).encode()), 'gate_reason': gate.reason})
