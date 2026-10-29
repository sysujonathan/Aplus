"""Editable chart analysis, independent of frozen strategies and trading books."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import uuid

import pandas as pd

TOOLS = [('fib', '斐波那契'), ('long', '多头测算'), ('ray', '水平射线'),
         ('trend', '趋势线'), ('short', '空头测算'), ('rectangle', '矩形'),
         ('up', '向上箭头'), ('down', '向下箭头'), ('arrow', '箭头标记'),
         ('path', '路径'), ('channel', '平行通道')]
POINT_COUNTS = dict(fib=2, long=1, ray=1, trend=2, short=1, rectangle=2,
                    up=1, down=1, arrow=2, path=0, channel=3, text=1, ruler=2)
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
    from .chart_indicators import EMA_SOURCES
    if settings['ema_source'] not in EMA_SOURCES:
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
    if not levels or len(levels) > 24 or not all(math.isfinite(n) for n in levels):
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
        web = obj.get('web') or {}
        if kind in ('long', 'short'):
            for key in ('account_size','risk_value','lot_size','leverage','point_value'):
                if key in web and (not math.isfinite(float(web[key])) or float(web[key]) <= 0):
                    raise ValueError('仓位分析输入必须为有限正数')
            if web.get('risk_unit', 'cash') not in ('cash','percent'):
                raise ValueError('仓位分析风险单位无效')
            if str(web.get('qty_precision','default')) not in ('default', *map(str, range(9))):
                raise ValueError('数量显示精度无效')
        for day, price in obj['points']:
            pd.Timestamp(day)
            if not math.isfinite(float(price)) or float(price) <= 0:
                raise ValueError('绘图价格必须为有限正数')
    alert_ids = set()
    for alert in alerts:
        if not isinstance(alert.get('id'), str) or not alert['id'] or alert['id'] in alert_ids:
            raise ValueError('提醒编号无效或重复')
        alert_ids.add(alert['id'])
        if alert['condition'] not in (
                'above', 'below', 'cross_up', 'cross_down', 'cross',
                'enter_channel', 'exit_channel', 'inside_channel', 'outside_channel',
                'rise', 'fall', 'rise_pct', 'fall_pct'):
            raise ValueError('提醒条件无效')
        if alert['target'] not in ('price', 'ema') or alert['frequency'] not in ('once', 'bar_close'):
            raise ValueError('提醒口径无效')
        if not math.isfinite(float(alert['value'])) or float(alert['value']) <= 0:
            raise ValueError('提醒价格无效')
        if alert['condition'] in ('enter_channel', 'exit_channel',
                                  'inside_channel', 'outside_channel'):
            try:
                upper, lower = float(alert.get('upper')), float(alert.get('lower'))
            except (TypeError, ValueError):
                raise ValueError('提醒通道上下轨无效') from None
            if (not math.isfinite(upper) or not math.isfinite(lower)
                    or lower <= 0 or upper <= lower):
                raise ValueError('提醒通道上下轨无效')
        if alert['condition'] in ('rise', 'fall', 'rise_pct', 'fall_pct'):
            if not 1 <= int(alert.get('bars', 1)) <= 500:
                raise ValueError('提醒回看K线数量必须为 1～500')
        if not 1 <= int(alert.get('ema_length', 20)) <= 500:
            raise ValueError('提醒均线长度无效')
        if alert.get('timeframe') not in ('5m', '1h', '4h', 'daily', 'weekly', 'monthly'):
            raise ValueError('提醒周期无效')
        if alert.get('source') is None or alert.get('adjustment') is None:
            raise ValueError('提醒必须记录行情来源和复权口径')
        if alert.get('expires'):
            pd.Timestamp(alert['expires'])
        if alert.get('active_after'):
            if pd.isna(pd.Timestamp(alert['active_after'])):
                raise ValueError('提醒创建时间无效')
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
    web = obj.get('web') or {}
    lot = float(web.get('lot_size', 1))
    if any(key in web for key in ('account_size','risk_value','lot_size','leverage')):
        account = float(web.get('account_size', 1000))
        point = float(web.get('point_value', 1))
        risk_size = (float(web['risk_value']) * (account/100 if web.get('risk_unit') == 'percent' else 1)
                     if 'risk_value' in web else float(obj['risk_budget']))
        bounded = min(risk_size/(risk*point*lot),
                      account*float(web.get('leverage',10000))/(entry*point*lot))
        digits = 0 if str(web.get('qty_precision','default')) == 'default' else int(web['qty_precision'])
        quantity = math.floor(bounded * 10**digits + 1e-9) / 10**digits
    unit = '股' if lot == 1 else '手（分析单位）'
    return (f'风险 {risk/entry:.2%} · 回报 {reward/entry:.2%} · R {ratio:.2f} · '
            f'分析模拟数量 {quantity:g} {unit} · 非交易建议'), ratio, quantity


def session_counts(frame):
    """Count actual bars in a Shanghai calendar session; lunch does not reset."""
    days = pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d')
    return days.groupby(days).cumcount().add(1).tolist()


def create_alert(*, name, code, source, adjustment, timeframe, target, condition,
                 value, frame, ema_length=20, frequency='bar_close', expires=None,
                 active_after=None, upper=None, lower=None, bars=1):
    """Create a forward-only alert. Existing history is seeded, never backfilled."""
    if frame is None or frame.empty:
        raise ValueError('创建提醒前需要真实行情')
    alert = dict(id=uuid.uuid4().hex, name=str(name or '').strip() or f'{code} 价格提醒',
                 code=str(code), source=str(source), adjustment=str(adjustment),
                 timeframe=str(timeframe), target=str(target), condition=str(condition),
                 value=float(value), ema_length=int(ema_length), frequency=str(frequency),
                 expires=str(expires or ''), enabled=True, cursor=str(frame.date.iloc[-1]),
                 status='等待下一根已收盘 K 线', events=[])
    if condition in ('enter_channel', 'exit_channel', 'inside_channel', 'outside_channel'):
        alert.update(upper=upper, lower=lower)
    if condition in ('rise', 'fall', 'rise_pct', 'fall_pct'):
        alert['bars'] = bars
    if active_after is not None:
        alert['active_after'] = str(active_after)
    validate_document({'revision': 0, 'settings': DEFAULT_SETTINGS,
                       'drawings': [], 'alerts': [alert]})
    return alert


def aggregate_minutes(frame, minutes, *, allow_open_tail=False, closing_labelled=False):
    """Aggregate real five-minute sessions into 1-hour or half-day bars.

    A bar is closed only when its whole slice of the session arrived, so a
    missing minute can never be papered over.  ``allow_open_tail`` keeps the
    trailing bar of the newest session while it is still forming: that is the
    only bar a reader may see before its slice ends, and it is exactly what a
    live chart draws.  Anything earlier must be complete.

    ``closing_labelled`` says the frame's timestamp is the bar's own close, as
    every live transport here is.  Imported CSV files are start-labelled, so
    their slices are counted from the session open instead; a tail-only live
    frame cannot be counted that way because it does not start at the open.

    The half-day unit uses the exchange session grid -- morning 09:30-11:30 and
    afternoon 13:00-15:00 -- so a 4-hour bar is one per half session, two per
    day, rather than a second copy of the daily bar.
    """
    minutes = int(minutes)
    if minutes not in (60, 240):
        raise ValueError('分钟聚合仅支持 1 小时或 4 小时')
    value = frame.copy()
    stamp = pd.to_datetime(value.date)
    validate_session_sequence(stamp, 5)
    value['_stamp'] = stamp
    value['_day'] = stamp.dt.strftime('%Y-%m-%d')
    size = 12 if minutes == 60 else 24
    # Closing minute of each slice inside one session. A bar that is still
    # forming is placed where it will close rather than at whichever minute
    # arrived last, which would read as an extra candle between two closed ones.
    # The afternoon's two hours are 14:00 and 15:00, not a continuation of the
    # morning count: lunch break means 11:30 + 60 minutes is not a real close.
    grid = [10 * 60 + 30, 11 * 60 + 30, 14 * 60, 15 * 60] if minutes == 60 \
        else [11 * 60 + 30, 15 * 60]
    span = 60 if minutes == 60 else 120

    def slice_of(minute):
        """Which slice of the session a closing-labelled bar belongs to."""
        morning = minute <= 11 * 60 + 30
        base = 9 * 60 + 30 if morning else 13 * 60
        if minutes == 240:
            return 0 if morning else 1
        step = max(0, minute - base - 1) // span
        return (0 if morning else 2) + min(1, step)

    if closing_labelled:
        value['_slice'] = [slice_of(int(t.hour) * 60 + int(t.minute)) for t in stamp]
        buckets = list(value.groupby(['_day', '_slice'], sort=True))
    else:
        buckets = []
        for day_key, day in value.groupby('_day', sort=False):
            morning = day.loc[day._stamp.dt.hour.lt(12)]
            afternoon = day.loc[day._stamp.dt.hour.ge(13)]
            groups = ([morning.iloc[:size], morning.iloc[size:],
                       afternoon.iloc[:size], afternoon.iloc[size:]] if minutes == 60
                      else [morning, afternoon])
            for index, group in enumerate(groups):
                if len(group):
                    buckets.append(((day_key, index), group))
    newest = buckets[-1][0] if buckets else None
    pieces = []
    for (day_key, index), group in buckets:
        if len(group) == size:
            label = str(group.date.iloc[-1])
        elif allow_open_tail and (day_key, index) == newest and 0 < len(group) < size:
            # Only the newest slice may be forming; a short earlier one is a
            # hole in the data, not a bar that has not finished yet.
            close = grid[index]
            sample = str(group.date.iloc[-1])
            # Keep the frame's own clock precision (with or without seconds).
            label = f'{day_key} {close // 60:02d}:{close % 60:02d}{sample[16:]}'
        else:
            continue
        pieces.append(dict(date=label, open=float(group.open.iloc[0]),
            high=float(group.high.max()), low=float(group.low.min()), close=float(group.close.iloc[-1]),
            volume=float(group.volume.sum())))
    if not pieces:
        raise ValueError(f'没有完整的 {minutes//60} 小时交易时段；不会用缺失分钟线拼接')
    return pd.DataFrame(pieces)



def validate_session_sequence(dates, minutes=5):
    """Reject closed days and gaps inside each A-share half-session.

    Partial first/last sessions remain valid for live imports.  Missing bars
    inside an observed half-session do not: Bar Count and aggregation must not
    silently compress a 10:00 -> 10:20 hole into adjacent five-minute bars.
    """
    from .exchange_calendar import is_trading_day

    stamps = pd.Series(pd.to_datetime(dates)).reset_index(drop=True)
    step = timedelta(minutes=int(minutes))
    for day, group in stamps.groupby(stamps.dt.normalize(), sort=False):
        if day.date().isoformat() < '1990-12-19':
            raise ValueError('分钟行情日期早于中国证券市场有效历史范围')
        if is_trading_day(day.date()) is False:
            raise ValueError(f'{day.date().isoformat()} 不是交易日')
        clock = group.dt.hour*60 + group.dt.minute
        for start, end in ((9*60+30, 11*60+30), (13*60, 15*60)):
            session = group.loc[clock.between(start, end)]
            if len(session) > 1 and not session.diff().iloc[1:].eq(step).all():
                raise ValueError(
                    f'{day.date().isoformat()} 同一交易时段存在缺失或跳跃的 {minutes} 分钟 K 线'
                )
    return True


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
    raw_dates = frame.date.astype(str).str.strip()
    explicit_date = raw_dates.str.match(
        r'^(?:19|20)\d{2}(?:[-/.]\d{1,2}[-/.]\d{1,2}|\d{4})(?:[T\s]|$)'
    )
    if not explicit_date.all():
        raise ValueError('分钟行情时间必须包含明确交易日期，不能只有时分')
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
    validate_session_sequence(dates, minutes)
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


def bar_close_time(stamp, timeframe):
    """Market close watermark, using verified exchange days when available."""
    candle = pd.Timestamp(stamp)
    candle = (candle.tz_localize('Asia/Shanghai') if candle.tzinfo is None
              else candle.tz_convert('Asia/Shanghai'))
    if timeframe in ('5m', '1h', '4h'):
        return candle + timedelta(minutes=5)
    if timeframe == 'daily':
        return candle.normalize() + timedelta(hours=19)
    rule = {'weekly': 'W-FRI', 'monthly': 'M'}.get(timeframe)
    if not rule:
        raise ValueError('提醒周期无效')
    from .exchange_calendar import is_trading_day
    period = candle.tz_localize(None).to_period(rule)
    day = period.end_time.normalize()
    # Unknown calendar coverage retains the conservative calendar-end boundary.
    while day >= period.start_time.normalize():
        opened = is_trading_day(day.date())
        if opened is not False:
            break
        day -= timedelta(days=1)
    return day.tz_localize('Asia/Shanghai') + timedelta(hours=19)


def bar_completed(stamp, timeframe, now):
    """Whether one bar is safely closed in Shanghai market time.

    Imported intraday bars and their aggregates carry the last constituent
    five-minute timestamp, so a five-minute cushion is deliberately used for
    both start-labelled and end-labelled vendor CSVs.
    """
    def local(value):
        result = pd.Timestamp(value)
        if result.tzinfo is None:
            return result.tz_localize('Asia/Shanghai')
        return result.tz_convert('Asia/Shanghai')

    return local(now) >= bar_close_time(stamp, timeframe)


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
    completed = [index for index, stamp in enumerate(frame.date)
                 if bar_completed(stamp, alert['timeframe'], now)]
    if not completed:
        updated['status'] = '等待最新 K 线收盘确认'
        return updated, None
    closed = frame.iloc[:completed[-1]+1]
    if len(closed) < 2:
        return updated, None
    stamp = str(closed.date.iloc[-1])
    if stamp <= str(alert.get('cursor', '')):
        return updated, None
    # Creation seeds the cursor: older bars are not replayed as new alerts.
    updated['cursor'] = stamp
    if alert.get('active_after') and (
        bar_close_time(stamp, alert['timeframe']).tz_convert('UTC')
        <= comparable(alert['active_after'])
    ):
        updated['status'] = '已跳过创建前收盘行情 ' + stamp
        return updated, None
    close = closed.close.astype(float)
    ema = close.ewm(span=int(alert['ema_length']), adjust=False).mean()
    series = ema if alert['target'] == 'ema' else close
    condition = alert['condition']
    if condition in ('enter_channel', 'exit_channel', 'inside_channel', 'outside_channel'):
        upper, lower = float(alert['upper']), float(alert['lower'])
        previous_inside = lower <= float(series.iloc[-2]) <= upper
        current_inside = lower <= float(series.iloc[-1]) <= upper
        hit = {'enter_channel': not previous_inside and current_inside,
               'exit_channel': previous_inside and not current_inside,
               'inside_channel': current_inside,
               'outside_channel': not current_inside}[condition]
    elif condition in ('rise', 'fall', 'rise_pct', 'fall_pct'):
        lookback = int(alert.get('bars', 1))
        if len(series) <= lookback:
            updated['status'] = f'等待至少 {lookback + 1} 根已收盘 K 线'
            return updated, None
        baseline, current_value = float(series.iloc[-lookback-1]), float(series.iloc[-1])
        move = current_value - baseline
        percent = move / baseline * 100 if baseline else math.nan
        amount = float(alert['value'])
        hit = {'rise': move >= amount, 'fall': -move >= amount,
               'rise_pct': percent >= amount, 'fall_pct': -percent >= amount}[condition]
    else:
        if alert['target'] == 'ema':
            previous = float(close.iloc[-2] - ema.iloc[-2])
            current = float(close.iloc[-1] - ema.iloc[-1])
        else:
            previous = float(close.iloc[-2] - float(alert['value']))
            current = float(close.iloc[-1] - float(alert['value']))
        hit = {'above': current > 0, 'below': current < 0,
               'cross_up': previous <= 0 < current,
               'cross_down': previous >= 0 > current,
               'cross': previous <= 0 < current or previous >= 0 > current}[condition]
    updated['status'] = '已检查 ' + stamp
    if not hit:
        return updated, None
    event = dict(time=str(now), bar=stamp, price=float(close.iloc[-1]), alert=alert['id'],
                 text=alert['name'], timeframe=alert['timeframe'])
    updated['events'] = [*alert.get('events', []), event][-100:]
    updated['status'] = '已触发 ' + stamp
    if alert['frequency'] == 'once':
        updated['enabled'] = False
    return updated, event
