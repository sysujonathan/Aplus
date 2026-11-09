"""Premarket snapshot export: read-only build, files, and reload."""
import json

import pytest

from tests.test_workbench import store  # noqa: F401  (pytest fixture)
from workbench.snapshot import (
    build_snapshot,
    latest_asof,
    load_snapshot,
    render_html,
    write_snapshot,
)
from workbench.store import dumps

ASOF = "2026-11-09"


def _seed(store, *, sources=("tickflow",), hits=True):
    store.root.joinpath("sources/tickflow").mkdir(parents=True, exist_ok=True)
    store.root.joinpath("sources/tickflow/universe.csv").write_text(
        "code,code_name,tradeStatus,ipoDate\n"
        "sh.601919,测试<脚本>,unknown,2018-06-12\n"
        "sh.600853,示例股,unknown,2018-06-12\n",
        encoding="utf-8",
    )
    scan_result = {
        "asof": ASOF,
        "success": 12,
        "signals": 2,
        "no_signal": 10,
        "reused": 4,
        "calculated": 8,
        "engine_version": "engine-test-version",
        "strategy_versions": {"STRATEGY_GAP_H2": "v-gap-h2", "MTR_MASTER": "v-mtr"},
        "coverage": {"expected": 5, "ready": 3, "suspended": 0, "gaps": 2},
        "errors": [
            {"code": "sh.600001", "category": "input_gap", "error": "缺少 1 个交易日"},
            {"code": "sh.600002", "category": "stale_tail", "error": "行情只到 2025-06-30"},
        ],
    }
    sync_result = {"success": 1, "errors": [{"code": "sh.600001", "error": "缺少日 K 列"}]}
    report = {
        "asof": ASOF,
        "complete": False,
        "sources": {
            "tickflow": {
                "sync_job": "sync-1",
                "scan_job": "scan-1",
                "sync_seconds": 10.5,
                "scan_seconds": 60.25,
                "sync_status": "partial",
                "scan_status": "partial",
            }
        },
    }
    spec = {
        "sources": list(sources),
        "boards": ["沪深主板"],
        "strategies": ["MTR_MASTER", "STRATEGY_GAP_H2"],
        "timeframe": "daily",
        "start": "2024-11-09",
    }
    store.execute(
        "INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)",
        ("pm-1", "premarket", "partial", ASOF + "T13:00:31+00:00", dumps(spec), dumps(report)),
    )
    store.execute(
        "INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)",
        ("sync-1", "sync", "partial", ASOF + "T13:00:32+00:00", dumps({}), dumps(sync_result)),
    )
    store.execute(
        "INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)",
        ("scan-1", "scan", "partial", ASOF + "T13:03:02+00:00", dumps({}), dumps(scan_result)),
    )
    if hits:
        store.execute(
            "INSERT INTO observations(id,job_id,code,strategy,version,timeframe,asof,"
            "setup_date,dataset_id,payload,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("obs-1", "scan-1", "sh.601919", "STRATEGY_GAP_H2", "v-gap-h2", "daily",
             ASOF, "2026-11-05", "ds-1",
             dumps({"entry": 17.63, "stop": 17.04, "target": 18.81,
                    "sl1_plan_price": 17.27, "mm_target": 19.39,
                    "prior_open_gap_count": 2, "pending_state": "PENDING",
                    "plan_kind": "gap-h2-next-session", "initial_risk_pct": 3.34}),
             ASOF + "T13:20:00+00:00"),
        )
        store.execute(
            "INSERT INTO observations(id,job_id,code,strategy,version,timeframe,asof,"
            "setup_date,dataset_id,payload,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("obs-2", "scan-1", "sh.600853", "MTR_MASTER", "v-mtr", "daily",
             ASOF, ASOF, "ds-2",
             dumps({"entry": 3.85, "stop": 3.03, "target": 5.49}),
             ASOF + "T13:20:00+00:00"),
        )


def test_build_snapshot_happy_path(store):
    _seed(store)
    assert latest_asof(store) == ASOF
    snapshot = build_snapshot(store, ASOF)
    assert snapshot["kind"] == "aplus-premarket-snapshot"
    assert snapshot["schema_version"] == 1
    assert snapshot["source"] == "tickflow"
    assert snapshot["boards"] == ["沪深主板"]
    assert snapshot["complete"] is False
    assert snapshot["engine_version"] == "engine-test-version"
    assert snapshot["frozen_digest"]
    assert snapshot["jobs"] == {"premarket": "pm-1", "sync": "sync-1", "scan": "scan-1"}
    assert snapshot["timing_seconds"] == {"sync": 10.5, "scan": 60.25}
    assert snapshot["coverage"]["expected"] == 5
    assert snapshot["coverage"]["ready"] == 3
    assert snapshot["coverage"]["excluded_count"] == 2
    assert snapshot["coverage"]["error_categories"] == {"input_gap": 1, "stale_tail": 1}
    assert [hit["code"] for hit in snapshot["hits"]] == ["sh.600853", "sh.601919"]
    gap = snapshot["hits"][1]
    assert gap["name"] == "测试<脚本>"
    assert (gap["entry"], gap["stop"], gap["target"]) == (17.63, 17.04, 18.81)
    assert gap["plan"]["sl1_plan_price"] == 17.27
    assert gap["plan"]["mm_target"] == 19.39
    assert gap["plan"]["prior_open_gap_count"] == 2
    assert "plan" not in snapshot["hits"][0]
    assert [item["code"] for item in snapshot["excluded"]] == ["sh.600001", "sh.600002"]
    assert any("部分完成" in note for note in snapshot["notes"])


def test_write_snapshot_round_trip_and_html_escaping(store, tmp_path):
    _seed(store)
    out = write_snapshot(store, ASOF, tmp_path / "snap")
    assert (out / "snapshot.json").is_file()
    assert (out / "index.html").is_file()
    reloaded = load_snapshot(out / "snapshot.json")
    assert reloaded["asof"] == ASOF
    assert len(reloaded["hits"]) == 2
    text = (out / "index.html").read_text(encoding="utf-8")
    assert "sh.601919" in text and "17.63" in text
    assert "测试&lt;脚本&gt;" in text
    assert "<脚本>" not in text
    assert "http" not in text.split("<style>")[0]  # no external resources in head


def test_missing_premarket_raises(store):
    with pytest.raises(ValueError, match="没有 .* 的盘前任务记录"):
        build_snapshot(store, ASOF)


def test_empty_hits_snapshot(store):
    _seed(store, hits=False)
    snapshot = build_snapshot(store, ASOF)
    assert snapshot["hits"] == []
    assert any("无命中" in note for note in snapshot["notes"])


def test_multi_source_rejected(store):
    _seed(store, sources=("tickflow", "baostock"))
    with pytest.raises(ValueError, match="只支持单数据源"):
        build_snapshot(store, ASOF)


def test_load_snapshot_rejects_wrong_version(store, tmp_path):
    _seed(store)
    out = write_snapshot(store, ASOF, tmp_path / "snap")
    path = out / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="版本为 99"):
        load_snapshot(path)


def test_render_html_without_hits(store):
    _seed(store, hits=False)
    text = render_html(build_snapshot(store, ASOF))
    assert "命中（0）" in text
