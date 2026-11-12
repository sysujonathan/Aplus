"""Display/session calendar, independent of downloaded bar coverage. No writes."""
from datetime import date
from functools import lru_cache

# Published exchange schedule, not the government's make-up working days.
# Only listed published years are verified; other years' weekdays remain unknown.
SCHEDULES = {
    # 上证公告〔2023〕47号：2024 年部分节假日休市安排
    # https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml
    2024: (("01-01", "01-01"), ("02-09", "02-17"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-10", "06-10"), ("09-15", "09-17"),
           ("10-01", "10-07")),
    # 上证公告〔2024〕38号：2025 年部分节假日休市安排
    # https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml
    2025: (("01-01", "01-01"), ("01-28", "02-04"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("05-31", "06-02"), ("10-01", "10-08")),
    # https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml
    2026: (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
           ("10-01", "10-07")),
}


@lru_cache(maxsize=4)
def _validated_days(start, end, days):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or first.isoformat() != start or last.isoformat() != end:
        raise ValueError("Invalid calendar coverage")
    parsed = frozenset(days)
    if len(parsed) != len(days) or any(
        date.fromisoformat(day).isoformat() != day or not start <= day <= end
        for day in parsed
    ):
        raise ValueError("Invalid calendar dates")
    return parsed


def is_trading_day(day, calendar=None):
    """True=open, False=closed, None=unverified. Covered local calendar wins."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    key = day.isoformat()
    if isinstance(calendar, dict):
        try:
            start, end, days = calendar["start"], calendar["end"], calendar["trading_days"]
            if (isinstance(start, str) and isinstance(end, str) and isinstance(days, list)
                and len(days) <= 100000 and start <= key <= end):
                return key in _validated_days(start, end, tuple(days))
        except (KeyError, ValueError, TypeError):
            pass
    if day.weekday() >= 5:
        return False
    holidays = SCHEDULES.get(day.year)
    if holidays is None:
        return None
    return not any(start <= key[5:] <= end for start, end in holidays)
