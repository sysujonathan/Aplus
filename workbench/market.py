"""Validated, immutable market snapshots. No legacy database access."""
from __future__ import annotations

import io
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .store import Store, digest, now

FIELDS = ["date", "open", "high", "low", "close", "volume"]
BOARDS = ('沪深主板', '创业板', '科创板', '北交所')


def board_of(code):
    code = str(code).lower().strip()
    if re.fullmatch(r'(sh\.(600|601|603|605)\d{3}|sz\.(000|001|002|003)\d{3})', code):
        return '沪深主板'
    if re.fullmatch(r'sz\.(300|301)\d{3}', code):
        return '创业板'
    if re.fullmatch(r'sh\.688\d{3}', code):
        return '科创板'
    if re.fullmatch(r'bj\.(920\d{3}|[48]\d{5})', code):
        return '北交所'
    return None


def select_board_codes(universe, boards):
    if not boards or any(b not in BOARDS for b in boards):
        raise ValueError('请至少勾选一个有效板块')
    groups = universe.code.map(board_of)
    missing = [b for b in boards if not (groups == b).any()]
    if missing:
        raise ValueError('当前股票目录没有' + '、'.join(missing) + '行情范围。请更新股票目录；仍无记录则当前数据源未接入该板块，不能宣称同步完成。')
    return list(dict.fromkeys(universe.loc[groups.isin(boards), 'code'].tolist()))


def code_of(value):
    value = str(value).strip().lower()
    if re.fullmatch(r"(sh|sz|bj)\.\d{6}", value):
        return value
    if re.fullmatch(r"\d{6}", value):
        return ("bj." if value.startswith('920') or value[0] in '48' else "sh." if value[0] in "569" else "sz.") + value
    raise ValueError(f"股票代码格式不正确：{value}，示例 sh.600000 或 600000")


def parse_codes(text):
    return list(dict.fromkeys(code_of(x) for x in re.split(r"[,，;；\s]+", text.strip()) if x))


def completed_date():
    time = datetime.now(ZoneInfo("Asia/Shanghai"))
    # Baostock EOD availability varies; before 19:00 do not claim today's data complete.
    return (time.date() if time.hour >= 19 else time.date() - timedelta(days=1)).isoformat()


def validate_bars(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = set(FIELDS) - set(df.columns)
    if missing:
        raise ValueError(f"缺少行情列：{', '.join(sorted(missing))}")
    df = df[FIELDS].copy()
    if df.empty:
        raise ValueError("行情为空；不会当作同步成功")
    dates = pd.to_datetime(df.date, errors="raise")
    df['date'] = dates.dt.strftime('%Y-%m-%d')
    if df.date.duplicated().any():
        raise ValueError("同一股票存在重复日期，请先核对 CSV")
    for col in FIELDS[1:]:
        df[col] = pd.to_numeric(df[col], errors="raise").astype(float)
    if not np.isfinite(df[FIELDS[1:]].to_numpy()).all():
        raise ValueError("行情包含空值或无穷值，不自动用未来价格填补")
    invalid = ((df[['open', 'high', 'low', 'close']] <= 0).any(axis=1)
               | (df.high < df[['open', 'close', 'low']].max(axis=1))
               | (df.low > df[['open', 'close', 'high']].min(axis=1)) | (df.volume < 0))
    if invalid.any():
        raise ValueError(f"有 {int(invalid.sum())} 根 K 线价格关系不合法")
    return df.sort_values('date').reset_index(drop=True)


def weekly_bars(df, cutoff):
    data = validate_bars(df)
    data.index = pd.to_datetime(data.date)
    weekly = data.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    weekly = weekly.dropna().loc[lambda x: x.index <= pd.Timestamp(cutoff)]
    weekly.insert(0, 'date', weekly.index.strftime('%Y-%m-%d'))
    return weekly.reset_index(drop=True)


def save_dataset(store: Store, code, frame, source, adjustment, job=None, timeframe='daily'):
    code = code_of(code)
    data = validate_bars(frame)
    content = data.to_csv(index=False, float_format='%.12g', lineterminator='\n').encode('utf-8')
    sha = digest(content)
    identity = digest(f"{code}|{timeframe}|{source}|{adjustment}|{sha}".encode())
    rel = f"market/{identity}.csv"
    existing = store.root / rel
    if existing.exists() and digest(existing.read_bytes()) != sha:
        raise ValueError('已有行情快照被外部改动；拒绝覆盖，请先检查记录')
    store.write_artifact(rel, content, job)
    store.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (identity, code_of(code), timeframe, source, adjustment, data.date.iloc[0],
                   data.date.iloc[-1], len(data), rel, sha, now()))
    store.event(job, '登记行情快照', store.path, dataset=identity, code=code, rows=len(data))
    return identity


def load_dataset(store, dataset_id, job=None):
    rows = store.rows('SELECT * FROM datasets WHERE id=?', (dataset_id,))
    if not rows:
        raise ValueError('行情快照不存在')
    record = rows[0]
    content = store.read_artifact(record['path'], job)
    if digest(content) != record['sha256']:
        raise ValueError('行情文件被外部修改，校验不一致。请重新同步，不使用此快照')
    return validate_bars(pd.read_csv(io.BytesIO(content))), record


def latest_datasets(store, source='baostock'):
    return store.rows("SELECT * FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY code,timeframe "
                      "ORDER BY end DESC,created DESC,rowid DESC) AS rank FROM datasets WHERE source=?) WHERE rank=1", (source,))


class DirectBaoStock:
    def __enter__(self):
        import baostock as bs
        self.bs = bs
        # Bound socket waits: provider otherwise may block a worker indefinitely.
        import socket
        socket.setdefaulttimeout(25)
        result = bs.login()
        if result.error_code != '0':
            raise RuntimeError(f'行情服务登录失败：{result.error_msg}')
        return self

    def __exit__(self, *exc):
        self.bs.logout()

    @staticmethod
    def collect(result):
        if result.error_code != '0':
            raise RuntimeError(f'行情服务返回错误：{result.error_msg}')
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        if result.error_code != '0':
            raise RuntimeError(f'行情读取中断：{result.error_msg}')
        return pd.DataFrame(rows, columns=result.fields)

    def universe(self, date):
        result = self.collect(self.bs.query_all_stock(day=date))
        return result[result.code.map(board_of).notna()]

    def calendar(self, start, end):
        return self.collect(self.bs.query_trade_dates(start_date=start, end_date=end))

    def fetch(self, code, start, end):
        result = self.collect(self.bs.query_history_k_data_plus(
            code, 'date,open,high,low,close,volume,tradestatus', start_date=start,
            end_date=end, frequency='d', adjustflag='2'))
        evidence = {'returned_dates': result.date.tolist() if not result.empty else [],
                    'suspended_dates': result.loc[result.tradestatus == '0', 'date'].tolist() if not result.empty else []}
        if not result.empty:
            if not result.tradestatus.isin(['0', '1']).all():
                raise ValueError('行情交易状态不明，拒绝静默丢弃 K 线')
            result = result[result.tradestatus == '1']
        frame = validate_bars(result) if not result.empty else pd.DataFrame(columns=FIELDS)
        frame.attrs.update(evidence)
        return frame


# Keep the provider interface while isolating all vendor socket operations.
from .provider_process import BaoStock
