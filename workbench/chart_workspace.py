"""Editable chart analysis, independent of frozen strategies and trading books."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import uuid

import pandas as pd

TOOLS = [('fib', '斐波那契'), ('long', '多头测算'), ('ray', '水平射线'),
         ('trend', '趋势线'), ('short', '空头测算'), ('rectangle', '矩形'),
         ('up', '向上箭头'), ('down', '向下箭头'), ('arrow', '箭头标记'),
         ('path', '路径'), ('channel', '平行通道')]
POINT_COUNTS = dict(fib=2, long=1, ray=1, trend=2, short=1, rectangle=2,
                    up=1, down=1, arrow=2, path=0, channel=3)
DEFAULT_SETTINGS = dict(ema=True, ema_length=20, ema_source='close', ema_offset=0,
                        ema_color='#f0a202', bar_count=False, count_interval=2,
                        count_color='#f0a202', count_size=11, volume=False,
                        up='#ffffff', down='#2962ff', magnet=True,
                        fib_levels=[0, .5, 1], risk_budget=1000., indicator_labels=True)


def identity(code, source, adjustment):
    return hashlib.sha256(f'{code}|{source}|{adjustment}'.encode()).hexdigest()


def document():
    return dict(revision=0, settings=deepcopy(DEFAULT_SETTINGS), drawings=[], alerts=[])


def validate_document(value):
    value = deepcopy(value)
    settings = {**DEFAULT_SETTINGS, **value.get('settings', {})}
    if not 1 <= int(settings['ema_length']) <= 500 or not -500 <= int(settings['ema_offset']) <= 500:
        raise ValueError('EMA 长度或偏移超出范围')
    if settings['ema_source'] not in ('open', 'high', 'low', 'close'):
        raise ValueError('不支持的均线价格来源')
    if not 1 <= int(settings['count_interval']) <= 500:
        raise ValueError('计数间隔必须为 1～500')
    if not 6 <= int(settings['count_size']) <= 40:
        raise ValueError('标签字号必须为 6～40')
    if not math.isfinite(float(settings['risk_budget'])) or float(settings['risk_budget']) <= 0:
        raise ValueError('测算风险预算必须大于零')
    for key in ('ema_color', 'count_color', 'up', 'down'):
        color = settings[key]
        if len(color) != 7 or color[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
            raise ValueError('颜色须使用 #RRGGBB')
    levels = [float(n) for n in settings['fib_levels']]
    if not levels or len(levels) > 20 or not all(math.isfinite(n) for n in levels):
        raise ValueError('斐波那契级别无效')
    settings['fib_levels'] = levels
    value['settings'] = settings
    drawings = value.get('drawings', [])
    alerts = value.get('alerts', [])
    if not isinstance(drawings, list) or not isinstance(alerts, list):
        raise ValueError('图表对象格式无效')
    ids = set()
    for obj in drawings:
        kind = obj['kind']
        if kind not in POINT_COUNTS or not isinstance(obj.get('id'), str) or not obj['id']:
            raise ValueError('未知绘图对象')
        if obj['id'] in ids:
            raise ValueError('绘图对象编号重复')
        ids.add(obj['id'])
        count = len(obj['points'])
        expected = 4 if kind in ('long', 'short') else POINT_COUNTS[kind]
        if count != expected and not (kind == 'path' and count >= 2):
            raise ValueError('绘图锚点数量不正确')
        if not 1 <= int(obj.get('width', 2)) <= 8:
            raise ValueError('线宽必须为 1～8')
        for day, price in obj['points']:
            pd.Timestamp(day)
            if not math.isfinite(float(price)) or float(price) <= 0:
                raise ValueError('绘图价格必须为有限正数')
    alert_ids = set()
    for alert in alerts:
        if not isinstance(alert.get('id'), str) or not alert['id'] or alert['id'] in alert_ids:
            raise ValueError('提醒编号无效或重复')
        alert_ids.add(alert['id'])
        if alert['condition'] not in ('above', 'below', 'cross_up', 'cross_down', 'cross'):
            raise ValueError('提醒条件无效')
        if alert['target'] not in ('price', 'ema') or alert['frequency'] not in ('once', 'bar_close'):
            raise ValueError('提醒口径无效')
        if not math.isfinite(float(alert['value'])) or float(alert['value']) <= 0:
            raise ValueError('提醒价格无效')
        if not 1 <= int(alert.get('ema_length', 20)) <= 500:
            raise ValueError('提醒均线长度无效')
        if alert.get('timeframe') not in ('5m', '1h', '4h', 'daily', 'weekly', 'monthly'):
            raise ValueError('提醒周期无效')
        if alert.get('source') is None or alert.get('adjustment') is None:
            raise ValueError('提醒必须记录行情来源和复权口径')
        if alert.get('expires'):
            pd.Timestamp(alert['expires'])
        if not isinstance(alert.get('events', []), list):
            raise ValueError('提醒记录无效')
    value['drawings'], value['alerts'] = drawings, alerts
    value['revision'] = int(value.get('revision', 0))
    if value['revision'] < 0:
        raise ValueError('图表版本无效')
    return value


class History:
    """One completed gesture or bulk removal is one undo unit."""
    def __init__(self, value):
        self.value = validate_document(value)
        self.undo_stack, self.redo_stack = [], []

    def commit(self, new, *, include_alerts=False):
        new = validate_document(new)
        # 提醒的检查游标和触发记录属于运行状态。画线撤销不能让游标倒退，
        # 否则应用重启或 Ctrl+Z 后会对同一根 K 线重复弹窗。
        if not include_alerts:
            new['alerts'] = deepcopy(self.value['alerts'])
        if new == self.value:
            return False
        self.undo_stack.append(deepcopy(self.value))
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()
        self.value = new
        return True

    def undo(self):
        if self.undo_stack:
            alerts = deepcopy(self.value['alerts'])
            self.redo_stack.append(deepcopy(self.value))
            self.value = self.undo_stack.pop()
            self.value['alerts'] = alerts
            return True
        return False

    def redo(self):
        if self.redo_stack:
            alerts = deepcopy(self.value['alerts'])
            self.undo_stack.append(deepcopy(self.value))
            self.value = self.redo_stack.pop()
            self.value['alerts'] = alerts
            return True
        return False


def make_drawing(kind, points, *, created_at, settings, right_date=None):
    points = [[str(day), float(price)] for day, price in points]
    if kind in ('long', 'short'):
        day, entry = points[0]
        direction = 1 if kind == 'long' else -1
        points = [[day, entry], [day, entry*(1-direction*.05)],
                  [day, entry*(1+direction*.05)], [str(right_date or day), entry]]
    return dict(id=uuid.uuid4().hex, kind=kind, points=points, created_at=str(created_at),
                color='#b36bdf' if kind == 'rectangle' else '#26b9c5',
                text='', visible=True, locked=False, width=2,
                levels=list(settings['fib_levels']), risk_budget=settings['risk_budget'])


def risk_readout(obj):
    entry, stop, target = (obj['points'][i][1] for i in range(3))
    sign = 1 if obj['kind'] == 'long' else -1
    risk, reward = sign*(entry-stop), sign*(target-entry)
    if risk <= 0 or reward <= 0:
        return '止损／目标方向无效', 0, 0
    ratio = reward/risk
    quantity = math.floor(float(obj['risk_budget'])/risk)
    return f'风险 {risk/entry:.2%} · 回报 {reward/entry:.2%} · R {ratio:.2f} · 测算 {quantity} 股', ratio, quantity


def session_counts(frame):
    """Count actual bars in a Shanghai calendar session; lunch does not reset."""
    days = pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d')
    return days.groupby(days).cumcount().add(1).tolist()


def create_alert(*, name, code, source, adjustment, timeframe, target, condition,
                 value, frame, ema_length=20, frequency='bar_close', expires=None):
    """Create a forward-only alert. Existing history is seeded, never backfilled."""
    if frame is None or frame.empty:
        raise ValueError('创建提醒前需要真实行情')
    alert = dict(id=uuid.uuid4().hex, name=str(name or '').strip() or f'{code} 价格提醒',
                 code=str(code), source=str(source), adjustment=str(adjustment),
                 timeframe=str(timeframe), target=str(target), condition=str(condition),
                 value=float(value), ema_length=int(ema_length), frequency=str(frequency),
                 expires=str(expires or ''), enabled=True, cursor=str(frame.date.iloc[-1]),
                 status='等待下一根已收盘 K 线', events=[])
    validate_document({'revision': 0, 'settings': DEFAULT_SETTINGS,
                       'drawings': [], 'alerts': [alert]})
    return alert


def aggregate_minutes(frame, minutes):
    """Aggregate complete real A-share sessions without inventing missing bars."""
    minutes = int(minutes)
    if minutes not in (60, 240):
        raise ValueError('分钟聚合仅支持 1 小时或 4 小时')
    value = frame.copy()
    stamp = pd.to_datetime(value.date)
    pieces = []
    value['_stamp'] = stamp
    for _, day in value.groupby(stamp.dt.strftime('%Y-%m-%d'), sort=False):
        morning = day.loc[day._stamp.dt.hour.lt(12)]
        afternoon = day.loc[day._stamp.dt.hour.ge(13)]
        if len(morning) != 24 or len(afternoon) != 24:
            continue
        groups = [day] if minutes == 240 else [morning.iloc[:12], morning.iloc[12:],
                                               afternoon.iloc[:12], afternoon.iloc[12:]]
        for group in groups:
            pieces.append(dict(date=str(group.date.iloc[-1]), open=float(group.open.iloc[0]),
                high=float(group.high.max()), low=float(group.low.min()), close=float(group.close.iloc[-1]),
                volume=float(group.volume.sum())))
    if not pieces:
        raise ValueError(f'没有完整的 {minutes//60} 小时交易时段；不会用缺失分钟线拼接')
    return pd.DataFrame(pieces)


def minute_frame(path, minutes=5):
    """Explicit real-data CSV import, never invent ticks or fill missing bars."""
    frame = pd.read_csv(path)
    aliases = {'时间':'date', '日期':'date', '开盘':'open', '最高':'high', '最低':'low',
               '收盘':'close', '成交量':'volume'}
    frame = frame.rename(columns=aliases)
    needed = ['date', 'open', 'high', 'low', 'close', 'volume']
    if any(c not in frame for c in needed):
        raise ValueError('CSV 需要时间、开高低收、成交量列（或英文列名）')
    frame = frame[needed].copy()
    dates = pd.to_datetime(frame.date, errors='raise')
    if dates.dt.tz is not None:
        dates = dates.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError('分钟行情时间须唯一且递增')
    if not dates.dt.hour.ne(0).any() or (dates.dt.minute % minutes).ne(0).any():
        raise ValueError('不是所选周期的分钟 K 线')
    clock = dates.dt.hour*60 + dates.dt.minute
    in_session = clock.between(9*60+30, 11*60+30) | clock.between(13*60, 15*60)
    if not in_session.all():
        raise ValueError('分钟行情包含 A 股连续交易时段之外的 K 线')
    for key in needed[1:]:
        frame[key] = pd.to_numeric(frame[key], errors='raise')
    if not frame[needed[1:]].map(math.isfinite).all().all():
        raise ValueError('分钟行情包含非有限值')
    if (frame[['open','high','low','close']] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError('价格／成交量无效')
    if (frame.high < frame[['open','close','low']].max(axis=1)).any() or (frame.low > frame[['open','close','high']].min(axis=1)).any():
        raise ValueError('OHLC 关系无效')
    frame.date = dates.dt.strftime('%Y-%m-%d %H:%M:%S')
    if len(frame) < 2:
        raise ValueError('至少需要两根真实 K 线')
    return frame


def evaluate_alert(alert, frame, *, now, replay=False):
    """Closed-bar alert; no historical backfill, duplicate delivery, or replay effects."""
    updated = deepcopy(alert)
    if replay or not alert.get('enabled', True) or len(frame) < 2:
        return updated, None
    def comparable(value):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize('Asia/Shanghai')
        return stamp.tz_convert('UTC')
    if alert['expires'] and comparable(now) >= comparable(alert['expires']):
        updated.update(enabled=False, status='已到期')
        return updated, None
    stamp = str(frame.date.iloc[-1])
    if stamp <= str(alert.get('cursor', '')):
        return updated, None
    # Creation seeds the cursor: older bars are not replayed as new alerts.
    updated['cursor'] = stamp
    series = frame.close.astype(float)
    threshold = (series.ewm(span=int(alert['ema_length']), adjust=False).mean()
                 if alert['target'] == 'ema' else pd.Series(float(alert['value']), index=series.index))
    previous, current = float(series.iloc[-2]-threshold.iloc[-2]), float(series.iloc[-1]-threshold.iloc[-1])
    condition = alert['condition']
    hit = {'above':current>0, 'below':current<0, 'cross_up':previous<=0<current,
           'cross_down':previous>=0>current, 'cross':previous<=0<current or previous>=0>current}[condition]
    updated['status'] = '已检查 ' + stamp
    if not hit:
        return updated, None
    event = dict(time=str(now), bar=stamp, price=float(series.iloc[-1]), alert=alert['id'],
                 text=alert['name'], timeframe=alert['timeframe'])
    updated['events'] = [*alert.get('events', []), event][-100:]
    updated['status'] = '已触发 ' + stamp
    if alert['frequency'] == 'once':
        updated['enabled'] = False
    return updated, event
