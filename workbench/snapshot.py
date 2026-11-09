"""Export a dated premarket result as a client-consumable snapshot.

只读导出：只读 Store（observations / jobs / meta / 目录清单），不写数据库、
不碰策略、不改 schema、不联网。产物是「服务端备好、客户端只取」的最小形态：

  snapshot.json  结构化快照（机器消费；含策略版本锚定与覆盖口径）
  index.html     同内容的可读清单（人直接打开看，不依赖任何外部资源）

快照 v1 只支持单数据源的一键盘前结果；多源结果、缺盘前任务记录时
明确报错，不静默拼一份不完整的快照。
"""
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

from .market import completed_date
from .sources import directory_file, market_source
from .store import dumps, now
from .strategies import scan_engine_version, verify_frozen

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_KIND = "aplus-premarket-snapshot"
MAX_EXCLUDED_IN_JSON = 603
MAX_EXCLUDED_IN_HTML = 100

# observation payload 里除入场/止损/目标外，快照原样带出的计划字段。
# 只白名单、不透传：payload 未来新增字段不会悄悄进入分发面。
EXTRA_PLAN_KEYS = (
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
    scan_result = _job_result(store, source_report["scan_job"], "扫描") if source_report.get("scan_job") else {}
    sync_result = _job_result(store, source_report["sync_job"], "同步") if source_report.get("sync_job") else {}
    coverage = scan_result.get("coverage") or {}
    scan_errors = scan_result.get("errors") or []
    excluded = _excluded(scan_result)

    rows = store.rows(
        "SELECT code, strategy, version, setup_date, payload FROM observations "
        "WHERE asof=? ORDER BY strategy, code",
        (asof,),
    )
    names = _names(store, source)
    hits = [_hit(row, names) for row in rows]

    complete = bool(report.get("complete"))
    notes = []
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
        "engine_version": scan_result.get("engine_version") or scan_engine_version(),
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
        "hits": hits,
        "excluded": excluded[:MAX_EXCLUDED_IN_JSON],
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
    """Write snapshot.json + index.html; returns the output directory."""
    snapshot = build_snapshot(store, asof)
    out = Path(out_dir) if out_dir else default_snapshot_dir(store, asof)
    out.mkdir(parents=True, exist_ok=True)
    (out / "snapshot.json").write_text(
        dumps(snapshot), encoding="utf-8"
    )
    (out / "index.html").write_text(render_html(snapshot), encoding="utf-8")
    return out


def load_snapshot(path):
    """Read and validate a snapshot file; returns the snapshot dict."""
    data = _read_json(Path(path).read_text(encoding="utf-8"), f"快照 {path}")
    if data.get("kind") != SNAPSHOT_KIND:
        raise ValueError(f"快照 {path} 类型不是 {SNAPSHOT_KIND}")
    if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"快照 {path} 版本为 {data.get('schema_version')}，"
            f"当前只支持 v{SNAPSHOT_SCHEMA_VERSION}"
        )
    for key in ("asof", "source", "hits", "coverage", "engine_version", "frozen_digest"):
        if key not in data:
            raise ValueError(f"快照 {path} 缺少必填字段 {key}")
    if not isinstance(data["hits"], list):
        raise ValueError(f"快照 {path} 的 hits 不是列表")
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
    }
