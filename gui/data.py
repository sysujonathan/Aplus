"""桌面工作台的数据读取（只读，不修改 store，不碰冻结文件）。

集中封装候选与行情的真实数据通路，供 CandidateTabs / ChartPanel 调用。
"""
from __future__ import annotations

import json
import re

import pandas as pd


REALTIME_MARKET_SOURCE = "baostock"
LEGACY_MARKET_SOURCE = "legacy-engine-a"
LOCAL_CANDIDATE_SOURCES = (REALTIME_MARKET_SOURCE, LEGACY_MARKET_SOURCE)

_pinyin = None
_pinyin_style = None
_pinyin_checked = False


def _name_initials(name):
    """Lazy pinyin initials, following BPA Scanner's fast lookup design."""
    global _pinyin, _pinyin_style, _pinyin_checked
    if not _pinyin_checked:
        _pinyin_checked = True
        try:
            from pypinyin import Style, pinyin
            _pinyin, _pinyin_style = pinyin, Style
        except ImportError:
            _pinyin = _pinyin_style = None
    if _pinyin is None:
        return ""
    try:
        return "".join(
            syllable[0][0].lower()
            for syllable in _pinyin(name, style=_pinyin_style.FIRST_LETTER, errors="ignore")
            if syllable and syllable[0]
        )
    except Exception:
        return ""


class StockNameLookup:
    """Resolve A-share code/name queries, including lazy pinyin-initial matching."""

    def __init__(self, names):
        self.names = dict(names or {})
        self.by_name = {}
        for code, name in self.names.items():
            if name:
                self.by_name.setdefault(name, code)
        self._initials = None

    def _ensure_initials(self):
        if self._initials is not None:
            return
        self._initials = {}
        for code, name in self.names.items():
            initials = _name_initials(name)
            if initials:
                self._initials.setdefault(initials, []).append(code)

    def suggest(self, query, limit=8):
        text = str(query or "").strip()
        if not text:
            return []
        folded = text.casefold()
        found = []
        seen = set()

        def add(code):
            if code not in seen and code in self.names:
                seen.add(code)
                found.append((code, self.names[code]))

        digits = re.sub(r"\D", "", text)
        if digits and (text.isdigit() or folded.startswith(("sh", "sz", "bj"))):
            for code in sorted(self.names):
                if code.split(".")[-1].startswith(digits):
                    add(code)
                    if len(found) >= limit:
                        return found
        for name, code in self.by_name.items():
            if name.casefold() == folded:
                add(code)
        for name, code in self.by_name.items():
            if name.casefold().startswith(folded):
                add(code)
        for name, code in self.by_name.items():
            if folded in name.casefold():
                add(code)
            if len(found) >= limit:
                return found
        self._ensure_initials()
        for mode in ("exact", "prefix", "contains"):
            for initials, codes in self._initials.items():
                matched = (initials == folded if mode == "exact" else
                           initials.startswith(folded) if mode == "prefix" else
                           folded in initials)
                if matched:
                    for code in codes:
                        add(code)
                        if len(found) >= limit:
                            return found
        return found

    def resolve(self, query):
        matches = self.suggest(query, limit=1)
        return matches[0] if matches else (None, None)


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


def candidate_dates(store, timeframe="daily", source=None):
    """返回日期栏时间线：已同步行情日 + 已保存策略扫描日。

    主界面用默认值展示完整本地工作流；显式 source 仍可供隔离测试和
    历史读取使用，不会把两个来源混入同一次候选查询。
    """
    if source is not None:
        rows = store.rows(
            "SELECT DISTINCT substr(o.asof,1,10) AS day FROM observations o "
            "JOIN datasets d ON d.id=o.dataset_id "
            "WHERE d.source=? AND o.timeframe=? ORDER BY day DESC",
            (source, timeframe),
        )
    else:
        rows = store.rows(
            "SELECT day FROM ("
            "SELECT substr(o.asof,1,10) AS day FROM observations o "
            "JOIN datasets d ON d.id=o.dataset_id "
            "WHERE d.source IN (?,?) AND o.timeframe=? "
            "UNION "
            "SELECT d.end AS day FROM datasets d "
            "WHERE d.source=? AND d.timeframe=?"
            ") WHERE day IS NOT NULL AND day<>'' ORDER BY day DESC",
            (*LOCAL_CANDIDATE_SOURCES, timeframe, REALTIME_MARKET_SOURCE, timeframe),
        )
    return [row["day"] for row in rows if row.get("day")]


def latest_candidate_date(store, timeframe="daily"):
    """返回启动默认页：本地最近一次已有策略结果，而非最新行情日。"""
    return latest_scan_date(store, timeframe=timeframe)


def latest_market_date(store, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """返回指定正式行情源的最新 K 线日期。"""
    rows = store.rows(
        "SELECT MAX(end) AS day FROM datasets WHERE source=? AND timeframe=?",
        (source, timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_observation_date(store, timeframe="daily", source=REALTIME_MARKET_SOURCE):
    """返回指定行情来源最近一次保存观察结果的日期。"""
    rows = store.rows(
        "SELECT MAX(o.asof) AS day FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=?",
        (source, timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_scan_date(store, timeframe="daily"):
    """返回本地已保存的最近扫描日，包含已迁入 Aplus 的历史成果。"""
    rows = store.rows(
        "SELECT MAX(o.asof) AS day FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source IN (?,?) AND o.timeframe=?",
        (*LOCAL_CANDIDATE_SOURCES, timeframe),
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
