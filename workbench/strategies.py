"""Adapters surround, but never modify, the frozen strategy implementation."""
from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.calculator import add_indicators
from core.strategy_registry import StrategyRegistry
from .store import ROOT, digest, dumps, now


def verify_frozen():
    manifest = json.loads((ROOT / 'frozen_manifest.json').read_text(encoding='utf-8'))
    errors = []
    for file in manifest['files']:
        path = ROOT / file['path']
        if not path.exists() or digest(path.read_bytes()) != file['sha256']:
            errors.append(file['path'])
    if errors:
        raise ValueError('原策略保护检查失败，停止运行：' + ', '.join(errors))
    return digest(dumps(manifest).encode())


@dataclass
class StrategySpec:
    id: str
    name: str
    cls: type
    params: dict
    version: str
    timeframes: list
    state: str
    file: str

    def instance(self):
        return self.cls(**self.params)


def catalog(store):
    frozen = verify_frozen()
    entries = {}
    for key, item in StrategyRegistry._strategies.items():
        meta = item['metadata']
        tfs = meta.get('supported_timeframes', ['daily'])
        tfs = ['daily'] if tfs == ['backtest'] else tfs
        entries[key] = StrategySpec(key, meta['display_name'], item['class'], {}, frozen, tfs,
                                    'active' if key in StrategyRegistry.list_strategies() else 'research',
                                    str(inspect.getfile(item['class'])))
    for row in store.rows('SELECT * FROM registry ORDER BY created'):
        path = store.root / row['module']
        if digest(path.read_bytes()) != row['sha256']:
            raise ValueError(f"研究策略 {row['name']} 文件被修改，请注册为新版本")
        module_name = 'a_plugin_' + row['sha256']
        if module_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
        cls = getattr(sys.modules[module_name], row['class_name'])
        params = json.loads(row['params'])
        version = digest((row['sha256'] + frozen + dumps(params)).encode())
        entries[row['id']] = StrategySpec(row['id'], row['name'], cls, params, version,
                                          json.loads(row['timeframes']), row['state'], str(path))
    return entries


def register(store, content, class_name, name, params, timeframes):
    if not re.fullmatch(r'[A-Za-z_]\w*', class_name):
        raise ValueError('类名格式不正确')
    if not name.strip() or not timeframes or not set(timeframes) <= {'daily','weekly','monthly'}:
        raise ValueError('需要策略名称及有效周期')
    if not isinstance(params, dict):
        raise ValueError('参数应为 JSON 对象')
    # Registration executes trusted local Python; UI requires explicit acknowledgement.
    sha = digest(content)
    rel = f'plugins/{sha}.py'
    store.write_artifact(rel, content)
    module_spec = importlib.util.spec_from_file_location('candidate_' + sha, store.root / rel)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    try:
        module_spec.loader.exec_module(module)
        cls = getattr(module, class_name)
        instance = cls(**params)
        meta = instance.get_metadata()
        if not callable(getattr(instance, 'calculate_signals', None)) or not meta.get('signal_column'):
            raise ValueError('策略需提供 calculate_signals 和 get_metadata（含 signal_column）')
        if not set(timeframes) <= set(meta.get('supported_timeframes',['daily'])):
            raise ValueError('注册周期不能超出策略自己声明的适用周期')
    finally:
        sys.modules.pop(module_spec.name, None)
    key = 'CUSTOM_' + digest((sha + class_name + dumps(params)).encode())[:16]
    store.execute('INSERT OR IGNORE INTO registry VALUES(?,?,?,?,?,?,?,?,?)',
                  (key, name.strip(), rel, class_name, 'research', sha, dumps(params), dumps(timeframes), now()))
    store.event(None, '注册研究策略', rel, strategy=key, params=params)
    return key


def set_active(store, key, approved, reason):
    spec = catalog(store)[key]
    if not key.startswith('CUSTOM_'):
        raise ValueError('第一阶段不修改原策略的注册范围')
    if not reason.strip():
        raise ValueError('请记录这次决定的理由')
    if approved:
        validations = store.rows('SELECT * FROM validations WHERE strategy=? AND version=? AND passed=1', (key,spec.version))
        evidence = set()
        for job in store.rows("SELECT * FROM jobs WHERE kind='backtest' AND status='completed'"):
            settings = json.loads(job['spec'])
            report = json.loads(job['result'])
            if (settings.get('strategy') == key and report.get('strategy_version') == spec.version
                    and report.get('closed_trades', 0) > 0 and report.get('real_data', False)):
                evidence.add(settings['timeframe'])
        validated = {json.loads(v['report']).get('timeframe') for v in validations}
        if not set(spec.timeframes)<=validated or not set(spec.timeframes)<=evidence:
            raise ValueError('先完成当前版本所有注册周期的接口验证和真实行情回测（各至少一笔已结束模拟交易），再人工启用')
    store.execute('UPDATE registry SET state=? WHERE id=?', ('active' if approved else 'research',key))
    store.event(None, '人工启用策略' if approved else '退回研究区', spec.file, strategy=key, reason=reason)


def prepare(frame, timeframe='daily', asof=None):
    from .market import weekly_bars
    df = frame[frame.date <= (asof or frame.date.iloc[-1])].copy()
    if timeframe == 'weekly':
        df = weekly_bars(df, asof or frame.date.iloc[-1])
    elif timeframe == 'monthly':
        df.index = pd.to_datetime(df.date)
        df = df.resample('ME').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        df = df[df.index <= pd.Timestamp(asof or frame.date.iloc[-1])]
        df.insert(0, 'date', df.index.strftime('%Y-%m-%d'))
    return df.reset_index(drop=True)


def calculate(spec, frame):
    # Match the old daily scanner's 300-bar input window; no future bars in indicators.
    raw = frame.tail(300).reset_index(drop=True).copy()
    raw['trade_date'] = raw['date']
    enriched = add_indicators(raw)
    instance = spec.instance()
    result = instance.calculate_signals(enriched.copy())
    if not isinstance(result, pd.DataFrame) or len(result) != len(raw):
        raise ValueError('策略必须返回等长 DataFrame，不能删减行情行')
    if not result['date'].equals(enriched['date']):
        raise ValueError('策略改变了行情日期或顺序')
    pd.testing.assert_frame_equal(result[['open','high','low','close','volume']], enriched[['open','high','low','close','volume']])
    meta = instance.get_metadata()
    col = meta.get('active_signal_column') or meta['signal_column']
    if col not in result:
        raise ValueError(f'策略没有返回声明的信号列 {col}')
    flags = result[col].dropna()
    if not flags.isin([True,False,0,1]).all():
        raise ValueError('信号列必须为 True/False，不能用文本代替')
    return instance, result


def _number(value):
    try:
        v = float(value)
        return v if np.isfinite(v) else None
    except (ValueError,TypeError):
        return None


def _setup_date(instance, df, meta):
    last = df.iloc[-1]
    idx = _number(last.get('mtr_signal_bar_idx'))
    if idx is not None and 0 <= idx < len(df):
        return str(df.iloc[int(idx)].date)
    if meta.get('active_signal_column') == 'signal_active_gap_h2':
        # Ask the unchanged strategy to project each raw setup separately. The strategy
        # itself remains the authority for survival, latest active setup and entry.
        raw = df[meta['signal_column']].fillna(False).astype(bool)
        for i in reversed(np.flatnonzero(raw.to_numpy())):
            one = pd.Series(False, index=df.index)
            one.iloc[i] = True
            active = instance._project_active_entry(df, one, df[meta['sl_column']], df[meta['tp_columns'][0]], timeout=30)
            if bool(active[0].iloc[-1]):
                return str(df.iloc[i].date)
        raise ValueError('活跃 H2 信号无法定位原始形态日期，停止归档以免重复机会')
    return str(last.date)


def signal_at_end(spec, frame, with_rating=True, *, plan_prices=True):
    instance, df = calculate(spec, frame)
    meta = instance.get_metadata()
    row = df.iloc[-1]
    # The frozen detector stays byte-for-byte intact. Only live GAP H2 reminders
    # use the explicit next-session plan; historical signal studies keep v1 prices.
    from .h2_plan import is_h2, plan_at_end
    if plan_prices and spec.id == 'STRATEGY_GAP_H2' and is_h2(meta):
        plan = plan_at_end(instance, df, frame.attrs.get('code'))
        if plan['pending_state'] != 'PENDING':
            return None
        entry, stop, target = plan['entry'], plan['stop'], plan['target']
        warning = '' if 0 < stop < entry < target else '当前计划无有效做多风险收益组合，请人工核对'
        return dict(plan, asof=str(row.date), setup_date=plan['h2_setup_date'],
                    score=_number(row.get(meta.get('score_column', ''))), rating=None,
                    close=float(row.close), warning=warning)
    flag = row[meta.get('active_signal_column') or meta['signal_column']]
    if pd.isna(flag) or not bool(flag):
        return None
    def price(kind):
        return _number(row.get(meta.get('active_' + kind + '_column') or meta.get(kind + '_column', '')))
    entry, stop = price('entry'), price('sl')
    targets = meta.get('active_tp_columns') or meta.get('tp_columns', [])
    target = _number(row.get(targets[0])) if targets else None
    warning = ''
    if entry is None or stop is None or target is None or not 0 < stop < entry < target:
        warning = '策略未给出有效的做多入场/止损/目标组合；保留信号供人工分析，不自动补造交易价格'
    score = _number(row.get(meta.get('score_column', '')))
    rating = None
    if with_rating and hasattr(instance, 'compute_rating'):
        try:
            obj = instance.compute_rating(df)
            rating = obj.to_dict() if obj is not None else None
        except Exception as exc:
            warning += f' 评级失败：{exc}'
    return {'asof':str(row.date), 'setup_date':_setup_date(instance,df,meta),
            'entry':entry,'stop':stop,'target':target,'score':score,'rating':rating,
            'close':float(row.close), 'warning':warning}
