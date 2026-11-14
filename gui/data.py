"""桌面工作台的数据读取（只读，不修改 store，不碰冻结文件）。

集中封装候选与行情的真实数据通路，供 CandidateTabs / ChartPanel 调用。
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
import logging
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse
import re

import pandas as pd
from workbench.sources import market_source, FALLBACK_SOURCES


REALTIME_MARKET_SOURCE = "baostock"
LEGACY_MARKET_SOURCE = "legacy-engine-a"
LOCAL_CANDIDATE_SOURCES = (REALTIME_MARKET_SOURCE, 'tickflow', 'tencent', LEGACY_MARKET_SOURCE)
LEGACY_CANDIDATE_SOURCES = (REALTIME_MARKET_SOURCE, LEGACY_MARKET_SOURCE)

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



# Readers observe the same Store commit/artifact publication path used by sync,
# registry and name edits. No new writer or parallel notification bus is needed.
_data_cache = {}


def invalidate(store=None):
    """Explicitly discard display data (or one runtime's display data)."""
    if store is None:
        _data_cache.clear()
    else:
        for key in list(_data_cache):
            if key[0] == str(store.root):
                _data_cache.pop(key, None)


def data_inputs(store):
    from workbench.scan_cache import file_state, store_inputs
    directories = store.root / 'directories'
    return (store_inputs(store), file_state((directories, *directories.glob('*.csv'),
            store.root / 'universe.csv',
            *(store.root / 'sources' / source / 'universe.csv' for source in ('tickflow', 'tencent')))),
            getattr(store, 'source', None))


def _cached_read(store, name, inputs, read):
    from copy import deepcopy
    root = (str(store.root), name)
    cached = _data_cache.get(root)
    if cached is None or cached[0] != inputs:
        _data_cache.pop(root, None)  # Existing Store publication changed inputs.
        cached = (inputs, {})
        if len(_data_cache) >= 16:
            _data_cache.clear()
        _data_cache[root] = cached
    if name not in cached[1]:
        value = read()
        cached[1][name] = value.copy() if name == 'names' else deepcopy(value)
        return value
    return cached[1][name].copy() if name == 'names' else deepcopy(cached[1][name])


def catalog(store):
    from workbench.strategies import catalog as read
    from workbench.scan_cache import engine_inputs, file_state
    plugins = store.root / 'plugins'
    inputs = (data_inputs(store), engine_inputs(),
              file_state((plugins, *plugins.glob('*.py'))))
    # Separate caches: names do not share the catalog's protected-file key.
    return _cached_read(store, 'catalog', inputs, lambda: read(store))


def code_names(store):
    """Cached names expire on existing Store commits or directory publication."""
    return _cached_read(store, 'names', data_inputs(store), lambda: _read_code_names(store))

def _read_code_names(store):
    """Display names are independent of eligibility for market scans."""
    result = {}
    def add_file(path):
        frame = pd.read_csv(path, dtype=str).fillna('')
        result.update((code, name) for code, name in zip(frame.code, frame.code_name) if name.strip())
    try:
        meta = store.rows("SELECT value FROM meta WHERE key='universe_date'")
        if meta:
            path = store.root / f"directories/{meta[0]['value']}-basics.csv"
            if path.exists():
                add_file(path)
    except Exception:
        pass  # One unavailable catalog must not discard another valid catalog.
    try:
        from workbench.sources import directory_file
        add_file(directory_file(store, market_source(store)))
    except Exception:
        pass
    # Previously confirmed fund names remain available even without a stock
    # directory entry. No new trade or market record is created by this read.
    for query in ("SELECT code,name FROM positions WHERE name<>'' UNION SELECT code,name FROM closed_trades WHERE name<>''",
                  "SELECT substr(key,12) AS code,value AS name FROM meta WHERE key LIKE 'watch_name:%'"):
        try:
            for row in store.rows(query):
                if row.get('name', '').strip():
                    result.setdefault(row['code'], row['name'])
        except Exception:
            pass
    return result


# Display-only status, keyed by runtime to avoid mixing independent windows.
_candidate_sources = {}


def snapshot_inputs():
    """Snapshot file state and URL for the pipeline cache when merging #128."""
    directory = os.environ.get("APLUS_SNAPSHOT_DIR")
    state = ()
    if directory:
        path = Path(directory).expanduser() / "snapshot.json"
        try:
            stat = path.stat()
            state = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            state = (str(path), None, None)
    return (state, os.environ.get("APLUS_SNAPSHOT_URL", ""))


def candidate_source_status(store):
    return _candidate_sources.get(str(store.root), "本地扫描")


def _snapshot_candidates(store, timeframe, source, asof_filter):
    from workbench.snapshot import load_snapshot
    directory = os.environ.get("APLUS_SNAPSHOT_DIR")
    url = os.environ.get("APLUS_SNAPSHOT_URL")
    if directory:
        snapshot = load_snapshot(Path(directory).expanduser() / "snapshot.json")
    else:
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("快照地址须使用 HTTP/HTTPS")
        address = url if url.endswith("/snapshot.json") else url.rstrip("/") + "/snapshot.json"
        with urlopen(address, timeout=10) as response:
            content = response.read(32 * 1024 * 1024 + 1)
        if len(content) > 32 * 1024 * 1024:
            raise ValueError("快照超过 32 MiB")
        with tempfile.TemporaryDirectory(prefix="aplus-snapshot-") as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_bytes(content)
            snapshot = load_snapshot(path)
    if snapshot["timeframe"] != timeframe or (source and source != snapshot["source"]):
        raise ValueError("快照来源或周期与当前候选页不符")
    if asof_filter:
        parts = date.fromisoformat(snapshot["asof"]).isoformat().split("-")
        if any(str(value) != parts[i] for i, value in enumerate(asof_filter) if value):
            raise ValueError("快照日期与当前候选页不符")
    grouped = {}
    for hit in snapshot["hits"]:
        payload = dict(hit.get("plan") or {})
        payload.update({key: hit[key] for key in ("entry", "stop", "target")})
        grouped.setdefault(hit["strategy"], []).append({
            "code": hit["code"], "name": hit.get("name") or "",
            "date": hit["asof"], "observation_id": hit["observation_id"],
            "dataset_id": hit.get("dataset_id"), "source": snapshot["source"],
            "strategy": hit["strategy"], "timeframe": hit["timeframe"],
            "payload": payload,
        })
    _candidate_sources[str(store.root)] = (
        f"服务端快照 · {snapshot['asof']} · {snapshot['generated_at']}"
    )
    return grouped


def load_candidates(store, timeframe="daily", source=None, asof_filter=None):
    """按一个明确行情来源读取扫描观察，返回 {strategy_key: [row, ...]}。

    APLUS_SNAPSHOT_DIR 优先于 APLUS_SNAPSHOT_URL；均未配置时保持本地读取。
    坏包回退原因由 candidate_source_status 供状态区显示；历史源始终本地。
    asof_filter: (year, month, day) 三元组，元素为 None 表示该位不约束。
    用 LIKE 前缀匹配 asof（形如 2026-09-20 或带时间），避免依赖具体存储格式。
    """
    # Explicit legacy browsing stays local; snapshot failures never hide.
    if source != LEGACY_MARKET_SOURCE and (os.environ.get("APLUS_SNAPSHOT_DIR") or os.environ.get("APLUS_SNAPSHOT_URL")):
        try:
            return _snapshot_candidates(store, timeframe, source, asof_filter)
        except (ValueError, OSError, TypeError, KeyError, HTTPException) as exc:
            _candidate_sources[str(store.root)] = f"本地扫描 · 快照拒绝：{exc}"
            logging.getLogger(__name__).warning("快照拒绝，回退本地扫描：%s", exc)
    else:
        _candidate_sources[str(store.root)] = "本地扫描"
    source=source or market_source(store)
    sql = (
        "SELECT o.code, o.strategy, o.version, o.payload, o.timeframe, o.asof, o.id, o.dataset_id, "
        "d.source AS source "
        "FROM observations o JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=?"
    )
    params = [source, timeframe]
    pattern = _build_asof_pattern(asof_filter)
    if pattern is not None:
        sql += " AND o.asof LIKE ?"
        params.append(pattern)
    sql += " ORDER BY o.created DESC,o.rowid DESC"
    rows = store.rows(sql, tuple(params))
    # Preserve every archived observation in the DB, but show one effective
    # result per stock/strategy/period/day. New cached no-signal judgments also
    # suppress stale old hits; lack of a newer judgment is not an empty result.
    from workbench.scan_cache import filter_candidate_revisions
    withdrawn = store.rows("SELECT value FROM meta WHERE key='sync_withdrawn_observation_ids'")
    withdrawn = set(json.loads(withdrawn[0]['value'])) if withdrawn else set()
    rows = [row for row in rows if row['id'] not in withdrawn]
    rows = filter_candidate_revisions(store, rows, source, timeframe)
    seen = set()
    names = code_names(store)
    grouped = {}
    for r in rows:
        key = r["strategy"]
        identity = (r['code'], key, str(r['asof'])[:10])
        if identity in seen:
            continue
        seen.add(identity)
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


def scan_result_date(asof, timeframe="daily"):
    """Display the completed bar date, distinct from a job's input cutoff.

    Production weekly_bars labels W-FRI bars and excludes labels after cutoff.
    This only projects that existing rule into the result-date selector.
    """
    if not asof:
        return None
    day = date.fromisoformat(str(asof)[:10])
    if timeframe == 'weekly':
        day -= timedelta(days=(day.weekday() - 4) % 7)
    return day.isoformat()


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
        allowed=(market_source(store),) if market_source(store) in FALLBACK_SOURCES else LEGACY_CANDIDATE_SOURCES
        rows = store.rows(
            "SELECT day FROM ("
            "SELECT substr(o.asof,1,10) AS day FROM observations o "
            "JOIN datasets d ON d.id=o.dataset_id "
            'WHERE d.source IN ('+','.join('?' for _ in allowed)+') AND o.timeframe=? '
            "UNION "
            "SELECT d.end AS day FROM datasets d "
            "WHERE d.source=? AND d.timeframe=?"
            ") WHERE day IS NOT NULL AND day<>'' ORDER BY day DESC",
            (*allowed, timeframe, market_source(store), timeframe),
        )
    days = {row['day'] for row in rows if row.get('day')}
    if source is None:
        if timeframe == 'weekly':
            # Weekly scans consume daily snapshots; they do not save another
            # weekly dataset. A valid zero-hit scan still needs a selectable
            # completed weekly bar, without inventing observations/signals.
            daily = store.rows("SELECT DISTINCT end AS day FROM datasets "
                               "WHERE source=? AND timeframe='daily'", (market_source(store),))
            days.update(scan_result_date(row['day'], 'weekly') for row in daily if row.get('day'))
        if timeframe == 'daily':
            days.update(r['asof'] for r in store.shape_runs(market_source(store)) if r['status'] in ('completed','partial'))
    return sorted(days,reverse=True)


def latest_candidate_date(store, timeframe="daily"):
    """返回启动默认页：本地最近一次已有策略结果，而非最新行情日。"""
    days = [latest_scan_date(store, timeframe=timeframe)]
    if timeframe == 'daily':
        days += [r['asof'] for r in store.shape_runs(market_source(store)) if r['status'] in ('completed','partial')]
    return max((day for day in days if day),default=None)


def latest_market_date(store, timeframe="daily", source=None):
    """返回指定正式行情源的最新 K 线日期。"""
    rows = store.rows(
        "SELECT MAX(end) AS day FROM datasets WHERE source=? AND timeframe=?",
        (source or market_source(store), timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_observation_date(store, timeframe="daily", source=None):
    """返回指定行情来源最近一次保存观察结果的日期。"""
    rows = store.rows(
        "SELECT MAX(o.asof) AS day FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=?",
        (source or market_source(store), timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_scan_date(store, timeframe="daily"):
    """返回本地已保存的最近扫描日，包含已迁入 Aplus 的历史成果。"""
    allowed=(market_source(store),) if market_source(store) in FALLBACK_SOURCES else LEGACY_CANDIDATE_SOURCES
    rows = store.rows(
        "SELECT MAX(o.asof) AS day FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        'WHERE d.source IN ('+','.join('?' for _ in allowed)+') AND o.timeframe=?',
        (*allowed, timeframe),
    )
    return rows[0]["day"] if rows and rows[0].get("day") else None


def latest_market_dataset(store, code, timeframe="daily", source=None):
    """按股票读取最新正式行情快照；不会回退到工程 A 历史源。"""
    rows = store.rows(
        "SELECT * FROM datasets WHERE source=? AND timeframe=? AND code=? "
        "ORDER BY end DESC, created DESC, rowid DESC LIMIT 1",
        (source or market_source(store), timeframe, code),
    )
    return rows[0] if rows else None


def candidate_source_for_date(store, timeframe="daily", asof_filter=None):
    """为一个日期筛选选择单一来源；同日优先当前 Aplus 正式扫描。"""
    pattern = _build_asof_pattern(asof_filter)
    if market_source(store) in FALLBACK_SOURCES:
        return market_source(store)
    sql = (
        "SELECT d.source, MAX(o.asof) AS latest FROM observations o "
        "JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source IN (?,?) AND o.timeframe=?"
    )
    params = [*LEGACY_CANDIDATE_SOURCES, timeframe]
    if pattern is not None:
        sql += " AND o.asof LIKE ?"
        params.append(pattern)
    sql += (
        " GROUP BY d.source ORDER BY latest DESC, "
        "CASE d.source WHEN ? THEN 0 ELSE 1 END LIMIT 1"
    )
    params.append(market_source(store))
    rows = store.rows(sql, params)
    return rows[0]["source"] if rows else market_source(store)


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
    from .background_status import display_store
    store = display_store(store)
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


def latest_observation(store, code, timeframe="daily", source=None):
    """在一个明确行情来源内按代码取最近观察；无则返回 None。"""
    rows = store.rows(
        "SELECT o.id FROM observations o JOIN datasets d ON d.id=o.dataset_id "
        "WHERE d.source=? AND o.timeframe=? AND o.code=? "
        "ORDER BY o.created DESC LIMIT 1",
        (source or market_source(store), timeframe, code),
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
