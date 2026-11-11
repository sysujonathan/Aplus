"""案例库移动包导出（tools/export_case_library.py）的只读契约测试。

在临时运行目录里造最小 pa_* 表与一张原图，验证：索引字段齐全、原图被压
成包内 JPEG、无属主时写空索引且不报错、原图文件不被改动。
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE pa_cases (
    id TEXT, owner TEXT, status TEXT, title TEXT, memory TEXT,
    keywords TEXT, tags TEXT, topic_ids TEXT, symbol TEXT,
    timeframe TEXT, market TEXT, data_kind TEXT,
    created TEXT, updated TEXT, viewed TEXT
);
CREATE TABLE pa_assets (
    id TEXT, owner TEXT, sha256 TEXT, path TEXT, name TEXT,
    mime TEXT, width INT, height INT, bytes INT, created TEXT
);
CREATE TABLE pa_case_images (case_id TEXT, asset_id TEXT, ordinal INT);
"""


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "export_case_library", REPO / "tools" / "export_case_library.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_runtime(tmp_path, with_case=True):
    root = tmp_path / "runtime"
    root.mkdir()
    db = sqlite3.connect(root / "workbench.sqlite3")
    db.executescript(SCHEMA)
    if with_case:
        originals = root / "pa_growth" / "originals" / "owner-1"
        originals.mkdir(parents=True)
        Image.new("RGB", (2000, 1000), (20, 30, 40)).save(originals / "a1.png")
        db.execute("INSERT INTO meta VALUES('pa_local_owner','owner-1')")
        db.execute(
            "INSERT INTO pa_cases VALUES('c1','owner-1','private_draft',"
            "'缺口高二复盘','背景与入场复盘','[]','[\"GAP H2\"]',"
            "'[\"cont-gap\",\"cont-h2\"]','sh.600000','D','A股','case',"
            "'2026-11-01','2026-11-10','')"
        )
        db.execute(
            "INSERT INTO pa_assets VALUES('a1','owner-1','x',"
            "'pa_growth/originals/owner-1/a1.png','a1.png','image/png',"
            "2000,1000,100,'2026-11-01')"
        )
        db.execute("INSERT INTO pa_case_images VALUES('c1','a1',0)")
    db.commit()
    db.close()
    return root


def test_export_with_case(tmp_path):
    module = _load_module()
    root = _make_runtime(tmp_path)
    from workbench.snapshot import ReadOnlyStore

    out = tmp_path / "package"
    summary = module.export_cases(ReadOnlyStore(root), out)
    assert summary["cases"] == 1
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert case["title"] == "缺口高二复盘"
    assert case["topic_ids"] == ["cont-gap", "cont-h2"]
    assert case["images"] == ["cases/c1-0.jpg"]
    with Image.open(out / "cases" / "c1-0.jpg") as pic:
        assert pic.width == 1280  # 原图 2000 宽被压到移动端尺寸
    # 只读：原图仍在且未被改动
    assert (root / "pa_growth" / "originals" / "owner-1" / "a1.png").is_file()


def test_export_empty_library(tmp_path):
    module = _load_module()
    root = _make_runtime(tmp_path, with_case=False)
    from workbench.snapshot import ReadOnlyStore

    out = tmp_path / "package"
    summary = module.export_cases(ReadOnlyStore(root), out)
    assert summary["cases"] == 0
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    assert payload["count"] == 0 and payload["cases"] == []


def test_empty_export_does_not_overwrite_existing_package(tmp_path):
    # 目标目录已有非空案例包（如共享库同步链路生成）时，本地空导出不得冲掉
    module = _load_module()
    root = _make_runtime(tmp_path, with_case=False)
    from workbench.snapshot import ReadOnlyStore

    out = tmp_path / "package"
    out.mkdir()
    existing = {"kind": "aplus-case-library", "version": 1, "count": 9, "cases": []}
    (out / "cases.json").write_text(json.dumps(existing), encoding="utf-8")

    summary = module.export_cases(ReadOnlyStore(root), out)
    assert summary["cases"] == 0
    assert summary["skipped_overwrite"] == 9
    assert json.loads((out / "cases.json").read_text(encoding="utf-8"))["count"] == 9

    forced = module.export_cases(ReadOnlyStore(root), out, force=True)
    assert forced["cases"] == 0 and "skipped_overwrite" not in forced
    assert json.loads((out / "cases.json").read_text(encoding="utf-8"))["count"] == 0
