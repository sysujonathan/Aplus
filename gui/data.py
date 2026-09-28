"""桌面工作台的数据读取（只读，不修改 store，不碰冻结文件）。

集中封装候选与行情的真实数据通路，供 CandidateTabs / ChartPanel 调用。
"""
from __future__ import annotations

import json

import pandas as pd


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


def load_candidates(store, timeframe="daily", source="baostock", asof_filter=None):
    """读真实扫描观察，按 strategy 分组。返回 {strategy_key: [row, ...]}。

    asof_filter: (year, month, day) 三元组，元素为 None 表示该位不约束。
    用 LIKE 前缀匹配 asof（形如 2026-09-20 或带时间），避免依赖具体存储格式。
    """
    sql = (
        "SELECT o.code, o.strategy, o.timeframe, o.asof, o.id, o.dataset_id "
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
            }
        )
    return grouped


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


def latest_observation(store, code, timeframe="daily", source="baostock"):
    """按代码取最近一条观察 id（用于从搜索/关注定位 K 线）。无则返回 None。"""
    rows = store.rows(
        "SELECT o.id FROM observations o JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=? AND o.code=? ORDER BY o.created DESC LIMIT 1",
        (source, timeframe, code),
    )
    return rows[0]["id"] if rows else None
