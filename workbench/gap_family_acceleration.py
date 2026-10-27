"""Opt-in fast rejection for Gap Pinbar, AWIL and Structural Gap."""
from __future__ import annotations

import ast
import os
import platform
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from .store import ROOT, digest, dumps, now


VALID_MODES = {'OFF', 'SHADOW', 'CANARY', 'ACCELERATED'}
EXPECTED_RELEASE = '185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c'
EXPECTED_ADAPTER_SHA = '844f211f2b09eb10e8217d0aa8421b52cf738d7b56623b05925e4d9ccc138a92'
STORE_CONTRACT_KEY = 'workbench/store.py:gap-family-persistence-contract'
EXPECTED_SUPPORT_SHAS = {
    'workbench/scan_cache.py': '6c38124c10af76a74be214cdd2fbce936821ca701a975edb700150543ed48676',
    STORE_CONTRACT_KEY: 'a8f7c985ac8e125e0a2abe853d4ec2449ba36c99cd37e18f3d85fbcc2ba28418',
}
SUPPORTED_RUNTIMES = {
    ('Windows', '3.12', '2.5.3', '2.3.3'),
    ('Windows', '3.14', '2.4.1', '2.3.3'),
}


@dataclass(frozen=True)
class StrategyConfig:
    id: str
    label: str
    slug: str
    file: str
    calculation: str
    metadata: dict

    @property
    def mode_env(self):
        return f'APLUS_{self.slug}_FAST_REJECT_MODE'

    @property
    def approval_env(self):
        return f'APLUS_{self.slug}_FAST_REJECT_APPROVED_BINDING'

    @property
    def rate_env(self):
        return f'APLUS_{self.slug}_FAST_REJECT_CANARY_RATE'


CONFIGS = {
    'STRATEGY_GAP_PINBAR': StrategyConfig(
        'STRATEGY_GAP_PINBAR', 'Gap Pinbar', 'GAP_PINBAR',
        'core/strategies/gap_pinbar_strategy.py',
        'facabaabc420b4a7aae8ef98be3e13eefef2a507601216a79d7c612384432083',
        {'display_name': 'GAP PINBAR', 'signal_column': 'signal_gap_pinbar',
         'entry_column': 'entry_gap_pinbar', 'sl_column': 'sl_gap_pinbar',
         'tp_columns': ['tp_gap_pinbar'], 'supported_timeframes': ['daily', 'weekly']},
    ),
    'STRATEGY_AWIL': StrategyConfig(
        'STRATEGY_AWIL', 'AWIL', 'AWIL', 'core/strategies/awil_strategy.py',
        'e3ac5bdba534bdc66d8a2cc2d6e2627be1d5dbefbc8c76e535c3fc74244e9d00',
        {'display_name': 'AIL', 'signal_column': 'signal_awil',
         'entry_column': 'entry_awil', 'sl_column': 'sl_awil',
         'tp_columns': ['tp_awil'], 'supported_timeframes': ['daily']},
    ),
    'STRATEGY_STRUCTURAL_GAP': StrategyConfig(
        'STRATEGY_STRUCTURAL_GAP', 'Structural Gap', 'STRUCTURAL_GAP',
        'core/strategies/structural_gap_strategy.py',
        'fb2370e15f667a6ee0b5e8c1b84ad9350acbc1ae5019292b6630e5be60eaca3f',
        {'display_name': 'GAP H1', 'signal_column': 'signal_struct_gap_confirm',
         'entry_column': 'entry_struct_gap', 'sl_column': 'sl_struct_gap',
         'tp_columns': ['tp_struct_gap'], 'supported_timeframes': ['daily', 'weekly']},
    ),
}


def _runtime():
    return (platform.system(), f'{sys.version_info.major}.{sys.version_info.minor}',
            np.__version__, pd.__version__)


def _store_contract_sha(source=None):
    """Hash only cache, event and fuse persistence behavior."""
    if source is None:
        source = (ROOT / 'workbench/store.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    store = next((node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == 'Store'), None)
    if store is None:
        raise ValueError('Store 持久化契约不存在')
    methods = {node.name: node for node in store.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    function_names = ('now', 'dumps')
    method_names = ('connect', 'rows', 'execute', 'event')
    if any(name not in functions for name in function_names):
        raise ValueError('Store 事件序列化契约不完整')
    if any(name not in methods for name in method_names):
        raise ValueError('Store 连接或事件持久化契约不完整')
    init = methods.get('__init__')
    if init is None:
        raise ValueError('Store 初始化契约不存在')
    ddl = {}
    for node in ast.walk(init):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for statement in node.value.split(';'):
            normalized = ' '.join(statement.split())
            for table in ('meta', 'events', 'scan_cache'):
                if f'CREATE TABLE IF NOT EXISTS {table}(' in normalized:
                    ddl[table] = normalized
    if set(ddl) != {'meta', 'events', 'scan_cache'}:
        raise ValueError('Store 元数据、事件或缓存表契约不完整')

    def stable_source(node):
        body = ast.get_source_segment(source, node)
        decorators = [ast.get_source_segment(source, item)
                      for item in node.decorator_list]
        if body is None or any(item is None for item in decorators):
            raise ValueError('Store 持久化契约源码无法读取')
        return '\n'.join([*(f'@{item}' for item in decorators), body])

    payload = {
        'functions': {name: stable_source(functions[name]) for name in function_names},
        'methods': {name: stable_source(methods[name]) for name in method_names},
        'ddl': ddl,
    }
    return digest(dumps(payload).encode())


def _support_shas():
    return {
        'workbench/scan_cache.py': digest((ROOT / 'workbench/scan_cache.py').read_bytes()),
        STORE_CONTRACT_KEY: _store_contract_sha(),
    }


def requested_mode(strategy_id):
    config = CONFIGS[strategy_id]
    return str(os.environ.get(config.mode_env, 'OFF')).strip().upper()


def current_binding_id(strategy_id):
    config = CONFIGS[strategy_id]
    source_sha = digest(Path(__file__).read_bytes())
    service_sha = digest((ROOT / 'workbench/service.py').read_bytes())
    dynamic_rules = ROOT / 'config/gap_optimized_rules.json'
    rules_state = (digest(dynamic_rules.read_bytes()) if dynamic_rules.exists() else 'absent')
    return digest(dumps({
        'contract': 'gap-family-fast-reject-v1', 'strategy': strategy_id,
        'release': EXPECTED_RELEASE, 'calculation': config.calculation,
        'adapter': EXPECTED_ADAPTER_SHA, 'metadata': config.metadata,
        'runtime': _runtime(), 'source': source_sha, 'service': service_sha,
        'support': _support_shas(),
        'structural_gap_dynamic_rules': rules_state if strategy_id == 'STRATEGY_STRUCTURAL_GAP' else None,
    }).encode())


@dataclass(frozen=True)
class GateAssessment:
    safe: bool
    reject: bool
    reason: str


@dataclass(frozen=True)
class TrustedInput:
    opening: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    ema20: np.ndarray
    atr: np.ndarray
    close_loc: np.ndarray
    bullish: np.ndarray


@dataclass(frozen=True)
class FastResult:
    signal: object
    event_action: str | None = None
    event_detail: dict | None = None


def validate_prepared(frame, prepared):
    """Return exact common inputs or a safe fallback reason."""
    raw_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
    indicator_columns = ['ema20', 'atr', 'close_loc', 'is_bullish']
    if not isinstance(frame, pd.DataFrame) or not isinstance(prepared, pd.DataFrame):
        return None, '输入不是 DataFrame'
    if len(frame) < 125 or len(prepared) != min(len(frame), 300):
        return None, '输入长度不属于正式扫描域'
    if any(column not in frame for column in raw_columns):
        return None, 'OHLCV 字段不完整'
    if any(column not in prepared for column in raw_columns + indicator_columns):
        return None, '公共指标字段不完整'
    raw = frame.tail(300).reset_index(drop=True)
    dates = prepared['date'].reset_index(drop=True)
    if dates.isna().any() or raw['date'].isna().any():
        return None, '日期包含空值'
    if not dates.astype(str).equals(raw['date'].astype(str)):
        return None, '公共指标日期与原输入不一致'
    if dates.astype(str).duplicated().any() or not dates.astype(str).is_monotonic_increasing:
        return None, '日期顺序或唯一性异常'
    try:
        raw_values = raw[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
        values = prepared[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
    except (TypeError, ValueError):
        return None, '价格无法转换为数值'
    if not np.array_equal(values, raw_values, equal_nan=True):
        return None, '公共指标改变了原始 OHLCV'
    if not np.isfinite(values).all():
        return None, 'OHLCV 含非有限数值'
    opening, high, low, close, volume = values.T
    valid = ((low > 0) & (low <= opening) & (low <= close)
             & (high >= opening) & (high >= close) & (high >= low))
    if not valid.all() or (volume < 0).any():
        return None, 'OHLCV 合法性检查失败'

    bullish_source = prepared['is_bullish']
    expected_bullish = close > opening
    if not pd.api.types.is_bool_dtype(bullish_source.dtype) or bullish_source.isna().any():
        return None, '公共阳线指标无法验证'
    bullish = bullish_source.to_numpy(dtype=bool)
    if not np.array_equal(bullish, expected_bullish):
        return None, '公共阳线指标与原始价格不一致'

    expected_ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
    candle_range = high - low
    high_close = np.r_[np.nan, np.abs(high[1:] - close[:-1])]
    low_close = np.r_[np.nan, np.abs(low[1:] - close[:-1])]
    true_range = np.maximum(candle_range, np.maximum(high_close, low_close))
    expected_atr = pd.Series(true_range).rolling(14, min_periods=1).mean().to_numpy()
    safe_range = np.where(candle_range == 0, 1e-9, candle_range)
    expected_close_loc = (close - low) / safe_range
    expected = {'ema20': expected_ema20, 'atr': expected_atr,
                'close_loc': expected_close_loc}
    actual_values = {}
    for column, expected_values in expected.items():
        source = prepared[column]
        if not pd.api.types.is_numeric_dtype(source.dtype):
            return None, f'公共指标 {column} 不是可信数值'
        try:
            actual = source.to_numpy(dtype=float)
        except (TypeError, ValueError):
            return None, f'公共指标 {column} 不是可信数值'
        if not np.allclose(actual, expected_values, rtol=1e-12, atol=1e-12,
                           equal_nan=True):
            return None, f'公共指标 {column} 与原始价格不一致'
        actual_values[column] = actual
    return TrustedInput(opening, high, low, close, actual_values['ema20'],
                        actual_values['atr'], actual_values['close_loc'], bullish), None


def assess_latest_bar(strategy_id, frame, prepared, trusted=None, validation_reason=None):
    if strategy_id not in CONFIGS:
        return GateAssessment(False, False, '未知策略')
    if trusted is None:
        if validation_reason is not None:
            return GateAssessment(False, False, validation_reason)
        trusted, reason = validate_prepared(frame, prepared)
        if trusted is None:
            return GateAssessment(False, False, reason)
    high, low, close = trusted.high, trusted.low, trusted.close
    if strategy_id == 'STRATEGY_GAP_PINBAR':
        bar_range = high[-1] - low[-1]
        if bar_range <= 0:
            qualifies = False
        else:
            lower_wick = min(trusted.opening[-1], close[-1]) - low[-1]
            qualifies = (lower_wick / bar_range >= 0.40
                         and trusted.close_loc[-1] >= 0.50
                         and low[-1] <= trusted.ema20[-1]
                         and close[-1] > trusted.ema20[-1])
        reason = ('末根仍可能形成Pinbar首次刺破确认' if qualifies
                  else '末根不满足Pinbar与EMA20确认必要条件')
    elif strategy_id == 'STRATEGY_AWIL':
        qualifies = (bool(trusted.bullish[-1]) and trusted.close_loc[-1] >= 0.98
                     and high[-1] > high[-2] and low[-1] > trusted.ema20[-1])
        reason = ('末根仍可能形成AWIL H2确认' if qualifies
                  else '末根不满足AWIL强阳H2与EMA20必要条件')
    else:
        qualifies = (low[-1] >= low[-2] and high[-1] > high[-2]
                     and bool(trusted.bullish[-1]) and close[-1] > close[-2])
        reason = ('末根仍可能形成Structural Gap反转确认' if qualifies
                  else '末根不满足Structural Gap止跌反转必要条件')
    return GateAssessment(True, not qualifies, reason)


class GapFamilyFastRejector:
    """One strategy-local controller; instances do not share fuse state."""

    def __init__(self, store, job, spec):
        self.store, self.job, self.spec = store, job, spec
        self.config = CONFIGS[spec.id]
        self.requested_mode = requested_mode(spec.id)
        self.mode, self.rate, self.fused = 'OFF', 0.0, False
        self.fuse_persist_error = None
        self.fuse_evidence = None
        self.binding_id = current_binding_id(spec.id)
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
                if os.environ.get(self.config.approval_env) != self.binding_id:
                    self._fallback('未绑定当前精确版本的人工授权，已回退原版')
                    return
                self.rate = float(os.environ.get(self.config.rate_env, '0.05'))
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
        config = self.config
        try:
            relative = Path(self.spec.file).resolve().relative_to(ROOT.resolve()).as_posix()
        except (OSError, ValueError):
            return False, f'{config.label} 文件不在受保护工程内，已回退原版'
        if relative != config.file or self.spec.version != EXPECTED_RELEASE:
            return False, f'{config.label} 策略版本变化，已回退原版'
        if digest((ROOT / 'workbench/strategies.py').read_bytes()) != EXPECTED_ADAPTER_SHA:
            return False, '正式输出适配层变化，已回退原版'
        if _support_shas() != EXPECTED_SUPPORT_SHAS:
            return False, '缓存或熔断持久化实现变化，已回退原版'
        from .scan_cache import calculation_versions
        if calculation_versions([self.spec])[self.spec.id] != config.calculation:
            return False, f'{config.label} 关键依赖变化，已回退原版'
        metadata = self.spec.cls.get_metadata()
        if any(metadata.get(key) != value for key, value in config.metadata.items()):
            return False, f'{config.label} 元数据契约变化，已回退原版'
        if self.spec.id == 'STRATEGY_STRUCTURAL_GAP' and (ROOT / 'config/gap_optimized_rules.json').exists():
            return False, 'Structural Gap 动态规则存在，已回退原版'
        if _runtime() not in SUPPORTED_RUNTIMES:
            return False, f'运行环境 {_runtime()} 未经绑定验证，已回退原版'
        return True, '精确绑定通过'

    @property
    def _fuse_key(self):
        return f'{self.config.slug.lower()}_fast_reject:fuse:{self.binding_id}'

    def _stored_fuse(self):
        if self.store.rows('SELECT value FROM meta WHERE key=?', (self._fuse_key,)):
            return True
        return bool(self.store.rows(
            'SELECT seq FROM events WHERE action=? AND detail LIKE ? LIMIT 1',
            (f'{self.config.label}快速拒绝熔断', f'%{self.binding_id}%')))

    def _fallback(self, reason):
        self.mode = 'OFF'
        try:
            self.store.event(self.job, f'{self.config.label}快速拒绝回退原版',
                             mode=self.requested_mode, binding=self.binding_id, reason=reason)
        except Exception:
            pass

    def _sampled(self, sample_key):
        if self.rate <= 0:
            return False
        if self.rate >= 1:
            return True
        space = 1 << 64
        threshold = int(Decimal(str(self.rate)) * space)
        return int(digest(str(sample_key).encode())[:16], 16) < threshold

    def _trip_fuse(self, reason, sample_key, original=None, exception=None):
        self.fused, self.mode = True, 'OFF'
        evidence = {'time': now(), 'job': self.job, 'strategy': self.spec.id,
                    'binding': self.binding_id, 'requested_mode': self.requested_mode,
                    'sample': digest(str(sample_key).encode()), 'reason': reason,
                    'original': original}
        if exception is not None:
            evidence['exception'] = {'type': type(exception).__name__, 'message': str(exception)}
        self.fuse_evidence = evidence
        try:
            with self.store.connect() as db:
                db.execute('INSERT INTO meta(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (self._fuse_key, dumps(evidence)))
                db.execute('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)',
                           (now(), self.job, f'{self.config.label}快速拒绝熔断', '', dumps(evidence)))
        except Exception as exc:
            self.fuse_persist_error = f'{type(exc).__name__}: {exc}'

    def run(self, original, frame, prepared, sample_key, trusted=None,
            validation_reason=None):
        if not self.active:
            return FastResult(original())
        try:
            gate = assess_latest_bar(self.spec.id, frame, prepared, trusted=trusted,
                                     validation_reason=validation_reason)
        except Exception as exc:
            return FastResult(original(), f'{self.config.label}快速拒绝回退原版', {
                'mode': self.mode, 'binding': self.binding_id,
                'reason': f'门禁异常：{type(exc).__name__}: {exc}'})
        if not gate.safe:
            return FastResult(original(), f'{self.config.label}快速拒绝回退原版', {
                'mode': self.mode, 'binding': self.binding_id, 'reason': gate.reason})
        if not gate.reject:
            return FastResult(original())
        sampled = self._sampled(sample_key)
        if self.mode == 'CANARY' and sampled:
            return FastResult(None, f'{self.config.label}快速拒绝Canary', {
                'mode': self.mode, 'binding': self.binding_id,
                'sample': digest(str(sample_key).encode()), 'gate_reason': gate.reason})
        run_original = self.mode in {'SHADOW', 'CANARY'} or sampled
        if run_original:
            try:
                signal = original()
            except Exception as exc:
                self._trip_fuse('门禁拒绝样本的原版发生业务异常', sample_key, exception=exc)
                raise
            if signal is not None:
                self._trip_fuse('门禁拒绝样本与原版正式结果不一致', sample_key, original=signal)
                return FastResult(signal)
            return FastResult(None, f'{self.config.label}快速拒绝旁路对拍', {
                'mode': self.requested_mode, 'binding': self.binding_id,
                'sample': digest(str(sample_key).encode()), 'matched': True,
                'gate_reason': gate.reason})
        return FastResult(None, f'{self.config.label}快速拒绝', {
            'mode': self.mode, 'binding': self.binding_id,
            'sample': digest(str(sample_key).encode()), 'gate_reason': gate.reason})
