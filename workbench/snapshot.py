"""Export a dated premarket result as a client-consumable snapshot.

只读导出：经 ReadOnlyStore（SQLite mode=ro，不运行 Store 初始化/迁移）
读取 observations / jobs / 目录清单，不写数据库、不碰策略、不改 schema、
不联网。命中只认所选盘前扫描回执的 observation_ids（含合法复用结果），
不按日期扫 observations 表。产物是「服务端备好、客户端只取」的最小形态：

  snapshot.json  结构化快照（机器消费；含策略版本锚定与覆盖口径）
  index.html     同内容的可读清单（人直接打开看，不依赖任何外部资源）
  charts/        既有候选渲染器生成的 PNG（失败项见 chart_errors）

快照 v1 只支持单数据源的一键盘前结果；多源结果、缺盘前任务记录时
明确报错，不静默拼一份不完整的快照。
"""
from __future__ import annotations

import contextlib
import csv
import html
import hashlib
import math
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .market import board_of, completed_date
from .scan_receipt import receipt_for_read
from .sources import directory_file, market_source
from .store import dumps, now, resolve_runtime_root
from .strategies import scan_engine_version, verify_frozen

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_KIND = "aplus-premarket-snapshot"
MAX_EXCLUDED_IN_HTML = 100
_CST = timezone(timedelta(hours=8))  # A 股交易日时区


class ReadOnlyStore:
    """Read-only store adapter: SQLite mode=ro, no Store init or migration.

    构造 Store() 会建表、迁移 schema_version 并生成迁移备份；导出链路只许
    读，因此这里只提供 rows() 与 root/path，连接串带 mode=ro，物理上
    无法写入（只读挂载副本也能打开）。
    """

    def __init__(self, root=None):
        self.root = resolve_runtime_root(root)
        self.path = self.root / "workbench.sqlite3"
        if not self.path.is_file():
            raise ValueError(f"运行目录 {self.root} 下没有 workbench.sqlite3，无法导出快照")

    def rows(self, sql, args=()):
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def read_artifact(self, relative, job=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("不允许越过运行目录读取文件")
        return path.read_bytes()

# observation payload 里除入场/止损/目标外，快照原样带出的计划字段。
# 只白名单、不透传：payload 未来新增字段不会悄悄进入分发面。
EXTRA_PLAN_KEYS = (
    "rating",
    "score",
    "plan_kind",
    "pending_state",
    "sl1_plan_price",
    "mm_target",
    "prior_open_gap_count",
    "initial_risk_pct",
    "tp_r_multiple",
    "price_tick",
)


def _read_json(text, what):
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} 不是有效 JSON：{exc}") from exc


def _round(value, places=3):
    return round(value, places) if isinstance(value, (int, float)) else value


def latest_premarket_job(store, asof):
    """Return (job row, result dict) for the newest premarket job of ``asof``."""
    rows = store.rows(
        "SELECT * FROM jobs WHERE kind='premarket' ORDER BY created DESC, rowid DESC"
    )
    for row in rows:
        result = _read_json(row.get("result") or "{}", f"盘前任务 {row['id']} 回执")
        if result.get("asof") == asof:
            return row, result
    raise ValueError(f"没有 {asof} 的盘前任务记录；请先跑一键同步+扫描")


def _names(store, source):
    """Offline display names from the local universe directory (no network)."""
    names = {}
    try:
        path = directory_file(store, source)
    except (OSError, ValueError):
        return names
    if not path.exists():
        return names
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                code = (row.get("code") or "").strip()
                name = (row.get("code_name") or "").strip()
                if code and name:
                    names[code] = name
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return names


def _job_result(store, job_id, what):
    rows = store.rows("SELECT result FROM jobs WHERE id=?", (job_id,))
    if not rows:
        raise ValueError(f"{what}任务 {job_id} 的回执不存在")
    return _read_json(rows[0]["result"], f"{what}任务 {job_id} 回执")


def _scan_result(store, job_id):
    """Full scan report for a job; rebuilds bounded progress receipts."""
    rows = store.rows("SELECT result FROM jobs WHERE id=?", (job_id,))
    if not rows:
        raise ValueError(f"扫描任务 {job_id} 的回执不存在")
    return receipt_for_read(store, job_id, rows[0]["result"])


def _receipt_hits(store, scan_result, *, scan_job, source, spec):
    """Hits bound to the selected scan receipt's effective observation ids.

    The receipt's observation_ids are the scan's effective hit set (freshly
    calculated plus legitimately reused results). Anything else sharing the
    asof date — another source/board/timeframe/strategy, or a superseded
    revision — never enters the snapshot. Ids whose rows fall outside the
    selected scope are dropped and counted, never shipped silently.
    """
    if "observation_ids" not in scan_result:
        raise ValueError(
            f"扫描任务 {scan_job} 回执缺少 observation_ids，"
            "无法把命中绑定到所选扫描；请重跑扫描后再导出"
        )
    ids = list(dict.fromkeys(scan_result.get("observation_ids") or []))
    if not ids:
        return [], 0
    rows_by_id = {}
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        for row in store.rows(
            "SELECT o.id, o.code, o.strategy, o.version, o.setup_date, o.payload,"
            " o.timeframe, o.dataset_id, o.asof, d.source AS dataset_source FROM observations o"
            f" LEFT JOIN datasets d ON d.id = o.dataset_id WHERE o.id IN ({placeholders})",
            batch,
        ):
            rows_by_id[row["id"]] = row
    timeframe = spec.get("timeframe")
    strategies = set(spec.get("strategies") or [])
    boards = set(spec.get("boards") or [])
    kept, dropped = [], 0
    for oid in ids:
        row = rows_by_id.get(oid)
        if (
            row is None
            or (timeframe and row["timeframe"] != timeframe)
            or (strategies and row["strategy"] not in strategies)
            or (boards and board_of(row["code"]) not in boards)
            or (row.get("dataset_source") is not None
                and row["dataset_source"] != source)
        ):
            dropped += 1
            continue
        kept.append(row)
    kept.sort(key=lambda row: (row["strategy"], row["code"]))
    return kept, dropped


def _excluded(scan_result):
    """Canonical excluded list, from the scan receipt (categories + reasons)."""
    excluded = []
    seen = set()
    for error in scan_result.get("errors") or []:
        code = error.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        reason = str(error.get("error") or "")[:200]
        excluded.append(
            {"code": code, "category": error.get("category") or "unknown", "reason": reason}
        )
    excluded.sort(key=lambda item: item["code"])
    return excluded


def _hit(row, names):
    payload = _read_json(row["payload"], f"信号 {row['code']} {row['strategy']}")
    hit = {
        "observation_id": row["id"],
        "dataset_id": row.get("dataset_id"),
        "asof": row.get("asof"),
        "timeframe": row["timeframe"],
        "code": row["code"],
        "name": names.get(row["code"]),
        "strategy": row["strategy"],
        "version": row["version"],
        "setup_date": row["setup_date"],
        "entry": payload.get("entry"),
        "stop": payload.get("stop"),
        "target": payload.get("target"),
    }
    extras = {key: payload[key] for key in EXTRA_PLAN_KEYS if key in payload}
    if extras:
        hit["plan"] = extras
    return hit


def build_snapshot(store, asof):
    """Build the snapshot dict for ``asof`` from committed store records."""
    job, report = latest_premarket_job(store, asof)
    spec = _read_json(job.get("spec") or "{}", f"盘前任务 {job['id']} 规格")
    sources = spec.get("sources") or []
    if len(sources) != 1:
        raise ValueError(f"快照 v1 只支持单数据源盘前结果，当前为 {sources}")
    source = sources[0]
    source_report = (report.get("sources") or {}).get(source) or {}
    scan_result = _scan_result(store, source_report["scan_job"]) if source_report.get("scan_job") else {}
    sync_result = _job_result(store, source_report["sync_job"], "同步") if source_report.get("sync_job") else {}
    coverage = scan_result.get("coverage") or {}
    scan_errors = scan_result.get("errors") or []
    excluded = _excluded(scan_result)

    names = _names(store, source)
    hits, dropped_hits = [], 0
    if source_report.get("scan_job"):
        hit_rows, dropped_hits = _receipt_hits(
            store, scan_result,
            scan_job=source_report["scan_job"], source=source, spec=spec,
        )
        hits = [_hit(row, names) for row in hit_rows]

    complete = bool(report.get("complete"))
    notes = []
    if dropped_hits:
        notes.append(
            f"扫描回执含 {dropped_hits} 条范围外或已不存在的命中，未纳入快照"
            "（命中只认所选扫描回执的 observation_ids 并校验范围）。"
        )
    if not complete:
        notes.append(
            "部分完成：本周期可扫描 "
            f"{coverage.get('ready', '?')}/{coverage.get('expected', '?')} 只；"
            f"排除 {len(excluded)} 只，原因见 excluded（扫描不自动补齐缺口）。"
        )
    if coverage.get("suspended"):
        notes.append(f"另有 {coverage['suspended']} 只停牌，未计入可扫描范围。")
    if not hits:
        notes.append("本日无命中信号：快照只记录覆盖口径，不代表当日无机会。")

    return {
        "kind": SNAPSHOT_KIND,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "asof": asof,
        "generated_at": now(),
        "source": source,
        "boards": spec.get("boards") or [],
        "strategies": spec.get("strategies") or [],
        "timeframe": spec.get("timeframe"),
        "engine_version": scan_result.get("engine_version") or scan_engine_version(include_frozen=False),
        "frozen_digest": verify_frozen(),
        "strategy_versions": scan_result.get("strategy_versions") or {},
        "complete": complete,
        "jobs": {
            "premarket": job["id"],
            "sync": source_report.get("sync_job"),
            "scan": source_report.get("scan_job"),
        },
        "timing_seconds": {
            "sync": _round(source_report.get("sync_seconds")),
            "scan": _round(source_report.get("scan_seconds")),
        },
        "coverage": {
            "expected": coverage.get("expected"),
            "ready": coverage.get("ready"),
            "suspended": coverage.get("suspended"),
            "excluded_count": len(excluded),
            "evaluations": {
                "success": scan_result.get("success"),
                "signals": scan_result.get("signals"),
                "no_signal": scan_result.get("no_signal"),
                "reused": scan_result.get("reused"),
                "calculated": scan_result.get("calculated"),
            },
            "sync_updated": sync_result.get("success"),
            "sync_errors": len(sync_result.get("errors") or []),
            "error_categories": dict(Counter(
                error.get("category") or "unknown" for error in scan_errors
            )),
        },
        "observation_ids": [hit["observation_id"] for hit in hits],
        "hits": hits,
        "excluded": excluded,
        "notes": notes,
    }


def _esc(value):
    return html.escape("" if value is None else str(value))


def _fmt(value, places=2):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def render_html(snapshot):
    """Self-contained readable list; no scripts, no external resources."""
    hits = snapshot["hits"]
    excluded = snapshot["excluded"]
    coverage = snapshot["coverage"]
    timing = snapshot["timing_seconds"]
    status = "完成" if snapshot["complete"] else "部分完成"
    rows = []
    for hit in hits:
        plan = hit.get("plan") or {}
        risk = plan.get("initial_risk_pct")
        rows.append(
            "<tr><td>{code}</td><td>{name}</td><td>{strategy}</td>"
            "<td>{setup}</td><td>{entry}</td><td>{stop}</td><td>{target}</td>"
            "<td>{risk}</td></tr>".format(
                code=_esc(hit["code"]),
                name=_esc(hit.get("name") or "—"),
                strategy=_esc(hit["strategy"]),
                setup=_esc(hit["setup_date"]),
                entry=_fmt(hit.get("entry")),
                stop=_fmt(hit.get("stop")),
                target=_fmt(hit.get("target")),
                risk=_fmt(risk, 2) + "%" if risk is not None else "—",
            )
        )
    excluded_rows = "".join(
        "<li>{code} · {category} · {reason}</li>".format(
            code=_esc(item["code"]),
            category=_esc(item["category"]),
            reason=_esc(item["reason"]),
        )
        for item in excluded[:MAX_EXCLUDED_IN_HTML]
    )
    notes = "".join(f"<li>{_esc(note)}</li>" for note in snapshot["notes"])
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Aplus 盘前快照 {_asof}</title>
<style>body{{font-family:sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:6px 8px;text-align:left}}
th{{background:#f0f0f0}}h1{{font-size:20px}}h2{{font-size:16px;margin-top:24px}}
.meta{{color:#555}}.badge{{font-weight:bold;color:{color}}}</style></head>
<body>
<h1>Aplus 盘前快照 {_asof}</h1>
<p class="meta">数据源 {_source} · 板块 {_boards} · 周期 {_timeframe} · 状态
<span class="badge">{status}</span> · 生成于 {_generated}</p>
<h2>覆盖</h2>
<p>范围 {expected} 只 · 可扫描 {ready} 只 · 排除 {excluded_count} 只 ·
命中 {hits} 条 · 评估 {evals} 次（复用 {reused}／实算 {calculated}）·
同步 {sync_s} 秒 · 扫描 {scan_s} 秒</p>
<ul>{notes}</ul>
<h2>命中（{hits}）</h2>
<table><tr><th>代码</th><th>名称</th><th>策略</th><th>建仓日</th>
<th>入场</th><th>止损</th><th>目标</th><th>初始风险</th></tr>
{rows}</table>
<h2>排除清单（共 {excluded_count} 只，列出前 {shown} 只）</h2>
<details><summary>展开查看</summary><ul>{excluded_rows}</ul></details>
<p class="meta">引擎 {engine} · 冻结摘要 {frozen} ·
只读快照：内容复述当日扫描结果，不写回服务端。</p>
</body></html>""".format(
        _asof=_esc(snapshot["asof"]),
        _source=_esc(snapshot["source"]),
        _boards=_esc("、".join(snapshot["boards"])),
        _timeframe=_esc(snapshot["timeframe"] or ""),
        status=status,
        color="#1a7f37" if snapshot["complete"] else "#b45309",
        _generated=_esc(snapshot["generated_at"]),
        expected=_esc(coverage.get("expected")),
        ready=_esc(coverage.get("ready")),
        excluded_count=_esc(coverage.get("excluded_count")),
        hits=len(hits),
        evals=_esc((coverage.get("evaluations") or {}).get("success")),
        reused=_esc((coverage.get("evaluations") or {}).get("reused")),
        calculated=_esc((coverage.get("evaluations") or {}).get("calculated")),
        sync_s=_esc(timing.get("sync")),
        scan_s=_esc(timing.get("scan")),
        notes=notes,
        rows="".join(rows),
        shown=min(len(excluded), MAX_EXCLUDED_IN_HTML),
        excluded_rows=excluded_rows,
        engine=_esc(str(snapshot.get("engine_version") or "")[:12]),
        frozen=_esc(str(snapshot.get("frozen_digest") or "")[:12]),
    )


def default_snapshot_dir(store, asof):
    return store.root / "snapshots" / asof


def write_snapshot(store, asof, out_dir=None):
    """Write snapshot.json, index.html and candidate charts; return directory."""
    snapshot = build_snapshot(store, asof)
    out = Path(out_dir) if out_dir else default_snapshot_dir(store, asof)
    out.mkdir(parents=True, exist_ok=True)
    snapshot["charts"], snapshot["chart_errors"] = _package_charts(store, snapshot, out)
    snapshot["sha256"] = snapshot_digest(snapshot)
    (out / "snapshot.json").write_text(
        dumps(snapshot), encoding="utf-8"
    )
    (out / "index.html").write_text(render_html(snapshot), encoding="utf-8")
    return out


def snapshot_digest(data):
    """Canonical JSON SHA-256 excluding the digest field itself."""
    return hashlib.sha256(json.dumps(
        {k: v for k, v in data.items() if k != "sha256"}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _package_charts(store, snapshot, out):
    charts, errors = {}, {}
    for hit in snapshot["hits"]:
        oid = hit["observation_id"]
        try:
            # Same candidate rendering path as ChartPanel, including H2 plans.
            from gui.chart_renderer import render_chart
            from workbench.market import load_dataset
            from workbench.strategies import catalog, prepare, calculate
            from workbench.h2_plan import display_plan, is_h2
            observation = store.rows("SELECT * FROM observations WHERE id=?", (oid,))[0]
            frame, _ = load_dataset(store, observation["dataset_id"])
            payload = json.loads(observation["payload"])
            for key in ("asof", "setup_date"):
                payload.setdefault(key, observation[key])
            spec = catalog(store)[observation["strategy"]]
            bars = prepare(frame, observation["timeframe"], str(observation["asof"])[:10])
            instance, calculated = calculate(spec, bars)
            if spec.id == "STRATEGY_GAP_H2" and is_h2(instance.get_metadata()):
                payload = display_plan(spec, bars, payload, observation["code"],
                                       observation["setup_date"] or observation["asof"], "candidate")
            image = render_chart(calculated, payload, "", instance.get_metadata(),
                                 strategy=instance, strategy_type=observation["strategy"],
                                 code=observation["code"],
                                 instrument_type=bars.attrs.get("instrument_type"))
            relative = "charts/" + hashlib.sha256(oid.encode()).hexdigest() + ".png"
            (out / "charts").mkdir(exist_ok=True)
            image.save(out / relative, format="PNG")
            charts[oid] = relative
        except Exception as exc:
            errors[oid] = str(exc)
    return charts, errors


def load_snapshot(path, *, today=None):
    """Validate SHA, local anchors, receipt set and freshness (seven calendar days)."""
    data = _read_json(Path(path).read_text(encoding="utf-8"), f"快照 {path}")
    if not isinstance(data, dict) or data.get("kind") != SNAPSHOT_KIND:
        raise ValueError(f"快照类型不是 {SNAPSHOT_KIND}")
    if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"快照版本为 {data.get('schema_version')}，当前只支持 v1")
    for key, kind in {"asof": str, "generated_at": str, "source": str,
                      "hits": list, "coverage": dict, "engine_version": str,
                      "frozen_digest": str, "observation_ids": list,
                      "timeframe": str, "boards": list, "strategies": list,
                      "charts": dict, "chart_errors": dict}.items():
        if not isinstance(data.get(key), kind):
            raise ValueError(f"快照字段 {key} 缺少或类型错误")
    if data.get("sha256") != snapshot_digest(data):
        raise ValueError("快照 SHA 校验失败")
    # Service scan receipts exclude frozen files; verify their digest separately.
    if (data["engine_version"] != scan_engine_version(include_frozen=False)
            or data["frozen_digest"] != verify_frozen()):
        raise ValueError("快照版本锚不符")
    ids = data["observation_ids"]
    if not all(isinstance(oid, str) and oid for oid in ids) or len(set(ids)) != len(ids):
        raise ValueError("快照收据 observation_ids 非法")
    for key in ("boards", "strategies"):
        if not all(isinstance(value, str) for value in data[key]):
            raise ValueError(f"快照范围 {key} 结构错误")
    hit_ids = []
    for hit in data["hits"]:
        if not isinstance(hit, dict):
            raise ValueError("快照命中结构错误")
        for key in ("observation_id", "code", "strategy", "version", "setup_date", "asof", "timeframe"):
            if not isinstance(hit.get(key), str) or not hit[key]:
                raise ValueError(f"快照命中字段 {key} 错误")
        for key in ("entry", "stop", "target"):
            if key not in hit or (hit[key] is not None and
                                  (type(hit[key]) not in (int, float) or not math.isfinite(hit[key]))):
                raise ValueError(f"快照计划价位 {key} 错误")
        if not isinstance(hit.get("plan", {}), dict):
            raise ValueError("快照计划结构错误")
        if (hit["timeframe"] != data["timeframe"]
                or (data["strategies"] and hit["strategy"] not in data["strategies"])
                or (data["boards"] and board_of(hit["code"]) not in data["boards"])):
            raise ValueError("快照命中范围越界")
        hit_ids.append(hit["observation_id"])
    if len(hit_ids) != len(set(hit_ids)) or set(hit_ids) != set(ids):
        raise ValueError("快照命中集与收据 observation_ids 不一致")
    day = date.fromisoformat(data["asof"])
    current = (date.fromisoformat(today.isoformat() if isinstance(today, date) else today)
               if today is not None else datetime.now(_CST).date())
    generated = datetime.fromisoformat(data["generated_at"])
    if generated.tzinfo is None:
        raise ValueError("快照生成时间缺少时区")
    if not 0 <= (current - day).days <= 7 or not 0 <= (current - generated.astimezone(_CST).date()).days <= 7:
        raise ValueError("快照过期或日期在未来")
    for oid, relative in data["charts"].items():
        if oid not in ids or not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("快照图表清单错误")
    if (set(data["charts"]) & set(data["chart_errors"])
            or (set(data["charts"]) | set(data["chart_errors"])) != set(ids)):
        raise ValueError("快照图表清单与命中集不一致")
    if not all(isinstance(error, str) for error in data["chart_errors"].values()):
        raise ValueError("快照图表失败记录结构错误")
    return data


def latest_asof(store):
    """Newest asof among premarket jobs, falling back to completed_date()."""
    rows = store.rows(
        "SELECT result FROM jobs WHERE kind='premarket' ORDER BY created DESC, rowid DESC LIMIT 20"
    )
    for row in rows:
        try:
            result = json.loads(row["result"])
        except (TypeError, ValueError):
            continue
        if result.get("asof"):
            return result["asof"]
    return completed_date()


def snapshot_summary(snapshot):
    return {
        "asof": snapshot["asof"],
        "source": snapshot["source"],
        "complete": snapshot["complete"],
        "hits": len(snapshot["hits"]),
        "excluded": snapshot["coverage"]["excluded_count"],
        "engine_version": (snapshot.get("engine_version") or "")[:12],
        "generated_at": snapshot["generated_at"],
        "charts": len(snapshot.get("charts") or {}),
        "chart_errors": len(snapshot.get("chart_errors") or {}),
    }
