"""TickFlow calendar preparation, without a price request or BaoStock connection."""
from datetime import date, timedelta
import json

from .exchange_calendar import SCHEDULES, _validated_days
from .sources import calendar_file, source_file
from .store import dumps


# Verified SSE annual closures, including the subsequent 2019/2020 amendments.
# Keep these historical additions local to TickFlow preparation.
HISTORICAL_SCHEDULES = {
    2019: (("01-01", "01-01"), ("02-04", "02-10"), ("04-05", "04-07"),
           ("05-01", "05-04"), ("06-07", "06-09"), ("09-13", "09-15"),
           ("10-01", "10-07")),
    2020: (("01-01", "01-01"), ("01-24", "02-02"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-25", "06-27"), ("10-01", "10-08")),
    2021: (("01-01", "01-03"), ("02-11", "02-17"), ("04-03", "04-05"),
           ("05-01", "05-05"), ("06-12", "06-14"), ("09-19", "09-21"),
           ("10-01", "10-07")),
    2022: (("01-01", "01-03"), ("01-31", "02-06"), ("04-03", "04-05"),
           ("04-30", "05-04"), ("06-03", "06-05"), ("09-10", "09-12"),
           ("10-01", "10-07")),
    2023: (("01-01", "01-02"), ("01-21", "01-27"), ("04-05", "04-05"),
           ("04-29", "05-03"), ("06-22", "06-24"), ("09-29", "10-06")),
}
HISTORICAL_SOURCES = {
    2019: ("https://www.sse.com.cn/disclosure/announcement/general/c/c_20181220_4696473.shtml",
           "https://www.sse.com.cn/disclosure/announcement/general/c/c_20190418_4771364.shtml"),
    2020: ("https://www.sse.com.cn/disclosure/announcement/general/c/c_20191220_4969627.shtml",
           "https://star.sse.com.cn/star/media/news/c/c_20200127_4991583.shtml"),
    2021: ("https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20201224_5286951.shtml",),
    2022: ("https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20211220_5663057.shtml",),
    2023: ("https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20221227_5714459.shtml",),
}


def _read_calendar(path):
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        start, end, days = payload['start'], payload['end'], payload['trading_days']
        if not isinstance(days, list) or len(days) > 100000:
            raise ValueError('Invalid calendar dates')
        return payload, _validated_days(start, end, tuple(days))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('已有交易日历无效；未扩展日历或请求行情，请核对 trading_calendar.json') from exc


def extend_calendar(store, start, end, job=None):
    """Reuse local public evidence first; fill only uncovered dates from notices.

    Prepare the entire payload before writing so an unsupported year cannot
    leave a partly extended calendar. Never infer closed days from missing bars.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or first.isoformat() != start or last.isoformat() != end:
        raise ValueError('交易日历请求区间无效')
    target = source_file(store, 'trading_calendar.json', 'tickflow')
    path = calendar_file(store, 'tickflow')
    old, old_days = _read_calendar(path) if path.exists() else (None, frozenset())
    if old and old['start'] <= start <= end <= old['end']:
        if path != target:
            store.write_artifact(str(target.relative_to(store.root)), dumps(old).encode(), job)
        return old

    # A short TickFlow-owned calendar must not hide a complete public calendar
    # already obtained in this runtime. It is read-only, and shares no prices.
    shared_path = store.root / 'trading_calendar.json'
    shared, shared_days = (_read_calendar(shared_path)
                           if path != shared_path and shared_path.exists()
                           else (None, frozenset()))
    left = min(start, old['start']) if old else start
    right = max(end, old['end']) if old else end
    days = set(old_days)
    used_years = set()
    cursor = date.fromisoformat(left)
    last = date.fromisoformat(right)
    while cursor <= last:
        key = cursor.isoformat()
        if old and old['start'] <= key <= old['end']:
            pass  # Preserve covered local facts, including known closed days.
        elif shared and shared['start'] <= key <= shared['end']:
            if key in shared_days:
                days.add(key)
        else:
            holidays = HISTORICAL_SCHEDULES.get(cursor.year, SCHEDULES.get(cursor.year))
            if holidays is None:
                raise ValueError(f'{cursor.year} 年缺少已核验历史交易日历；请迁入覆盖该年的已核验 '
                                 'trading_calendar.json；不能从缺失 K 线推测休市，未请求行情')
            if cursor.weekday() < 5 and not any(a <= key[5:] <= b for a, b in holidays):
                days.add(key)
            if cursor.year in HISTORICAL_SCHEDULES:
                used_years.add(cursor.year)
        cursor += timedelta(days=1)
    payload = dict(old or {}, start=left, end=right, trading_days=sorted(days),
                   extension_source='已有交易日历及交易所已公布休市安排')
    references = dict(payload.get('historical_schedule_sources', {}))
    references.update((str(year), HISTORICAL_SOURCES[year]) for year in sorted(used_years))
    if references:
        payload['historical_schedule_sources'] = references
    store.write_artifact(str(target.relative_to(store.root)), dumps(payload).encode(), job)
    return payload
