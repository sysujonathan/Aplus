"""桌面工作台的数据读取（只读，不修改 store，不碰冻结文件）。

集中封装候选与行情的真实数据通路，供 CandidateTabs / ChartPanel 调用。
"""
from __future__ import annotations

import json

import pandas as pd


REALTIME_MARKET_SOURCE = "baostock"
LEGACY_MARKET_SOURCE = "legacy-engine-a"
LOCAL_CANDIDATE_SOURCES = (REALTIME_MARKET_SOURCE, LEGACY_MARKET_SOURCE)


def code_names(store):
    """复刻 workbench/dashboard.py 的命名：代码 -> 名称。失败返回空字典。"""
    try:
        meta = store.rows("SELECT value FROM meta WHERE key='universe_date'")
        result = {}
        if meta:
            path = store.root / f"directories/{meta[0]['value']}-basics.csv"
            if path.exists():
                basic = pd.read_csv(path, dtype=str).fillna("")
                if "code_name" in basic:
                    result.update(zip(basic.code, basic.code_name))
        frame = pd.read_csv(store.root / "universe.csv", dtype=str)
        result.update(zip(frame.code, frame.code_name))
        return result
    except Exception:
        return {}


def load_candidates(store, timeframe="daily", source=REALTIME_MARKET_SOURCE, asof_filter=None):
    """按一个明确行情来源读取扫描观察，返回 {strategy_key: [row, ...]}。

    asof_filter: (year, month, day) 三元组，元素为 None 表示该位不约束。
    用 LIKE 前缀匹配 asof（形如 2026-09-20 或带时间），避免依赖具体存储格式。
    """
    sql = (
        "SELECT o.code, o.strategy, o.timeframe, o.asof, o.id, o.dataset_id, "
        "d.source AS source "
        "FROM observations o JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=?"
    )
    params = [source, timeframe]
    pattern = _build_asof_pattern(asof_filter)
    if pattern is not None:
        sql += " AND o.asof LIKE ?"
        params.append(pattern)
    sql += " ORDER BY o.created DESC"
    rows = store.rows(sql, tuple(params))
    names = code_names(store)
    grouped = {}
    for r in rows:
        key = r["strategy"]
        grouped.setdefault(key, []).append(
            {
                "code": r["code"],
                "name": names.get(r["code"], ""),
                "date": r["asof"],
                "observation_id": r["id"],
                "dataset_id": r["dataset_id"],
                "source": r["source"],
                "strategy": r["strategy"],
                "timeframe": r["timeframe"],
            }
        )
    return grouped


def load_legacy_candidates(store, timeframe="daily", asof_filter=None):
    """只读工程 A 历史候选；调用方必须来自显式历史浏览入口。"""
    return load_candidates(
        store,
        timeframe=timeframe,
        source=LEGACY_MARKET_SOURCE,
        asof_filter=asof_filter,
    )


def candidate_dates(store, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """主界面策略结果时间线；默认只读 Aplus 正式扫描。"""
    rows = store.rows(
        "SELECT DISTINCT o.asof FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=? ORDER BY o.asof DESC",
        (source, timeframe),
    )
    return [row["asof"] for row in rows if row.get("asof")]


def latest_candidate_date(store, timeframe="daily"):
    """返回启动时应选中的 Aplus 正式策略结果日期。"""
    return latest_signal_date(store, timeframe=timeframe)


def latest_market_date(store, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """返回指定正式行情源的最新 K 线日期。"""
    rows = store.rows(
        "SELECT MAX(end) AS day FROM datasets WHERE source=? AND timeframe=?",
        (source, timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_signal_date(store, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """返回指定正式行情源最近一次真正产生观察结果的日期。"""
    rows = store.rows(
        "SELECT MAX(o.asof) AS day FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=?",
        (source, timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_market_dataset(store, code, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """按股票读取最新正式行情快照；不会回退到工程 A 历史源。"""
    rows = store.rows(
        "SELECT * FROM datasets WHERE source=? AND timeframe=? AND code=? "
        "ORDER BY end DESC, created DESC, rowid DESC LIMIT 1",
        (source, timeframe, code),
    )
    return rows[0] if rows else None


def candidate_source_for_date(store, timeframe="daily", asof_filter=None):
    """为一个日期筛选选择单一来源；同日优先当前 Aplus 正式扫描。"""
    pattern = _build_asof_pattern(asof_filter)
    sql = (
        "SELECT d.source, MAX(o.asof) AS latest FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source IN (?,?) AND o.timeframe=?"
    )
    params = [*LOCAL_CANDIDATE_SOURCES, timeframe]
    if pattern is not None:
        sql += " AND o.asof LIKE ?"
        params.append(pattern)
    sql += (
        " GROUP BY d.source ORDER BY latest DESC, "
        "CASE d.source WHEN ? THEN 0 ELSE 1 END LIMIT 1"
    )
    params.append(REALTIME_MARKET_SOURCE)
    rows = store.rows(sql, params)
    return rows[0]["source"] if rows else REALTIME_MARKET_SOURCE


def _build_asof_pattern(asof_filter):
    """把 (年,月,日) 三元组拼成 LIKE 模式；全 None 返回 None（不过滤）。"""
    if not asof_filter:
        return None
    y, m, d = asof_filter
    if not y and not m and not d:
        return None
    seg_y = y if y else "%"
    seg_m = m if m else "%"
    seg_d = (d + "%") if d else "%"
    pattern = f"{seg_y}-{seg_m}-{seg_d}"
    # 三个都是通配 -> 等于不过滤
    if pattern == "%-%-%":
        return None
    return pattern


def load_observation_candles(store, observation_id):
    """取某观察对应的行情帧与 payload。返回 (frame, record, payload)；无则 (None, None, None)。"""
    obs = store.rows("SELECT * FROM observations WHERE id=?", (observation_id,))
    if not obs:
        return None, None, None
    o = obs[0]
    payload = json.loads(o["payload"]) if o["payload"] else {}
    try:
        from workbench.market import load_dataset

        frame, record = load_dataset(store, o["dataset_id"])
    except Exception:
        return None, None, payload
    return frame, record, payload


def latest_observation(store, code, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """在一个明确行情来源内按代码取最近观察；无则返回 None。"""
    rows = store.rows(
        "SELECT o.id FROM observations o JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=? AND o.code=? "
        "ORDER BY o.created DESC LIMIT 1",
        (source, timeframe, code),
    )
    return rows[0]["id"] if rows else None


def latest_legacy_observation(store, code, timeframe="daily"):
    """只在工程 A 历史快照中定位最近观察。"""
    return latest_observation(
        store,
        code,
        timeframe=timeframe,
        source=LEGACY_MARKET_SOURCE,
    )
