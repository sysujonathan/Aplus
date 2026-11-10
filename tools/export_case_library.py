"""Export the PA case library as a mobile-consumable package, read-only.

案例库（workbench/pa_growth.py）是慢变参考数据：交易员看到新匹配标的后，
按主题/品种翻案例做计划。它不进每晚的快照契约，而是单独导出一份
`cases.json` + 压缩图，放到快照包目录里，移动端（mobile/）随包读取。

只读：经 ReadOnlyStore（SQLite mode=ro）读表，原图只读不改；导出图统一
压成最宽 1280 的 JPEG。没有本地案例库属主或案例为 0 时，写一份空索引
并正常退出——桌面端案例同步到服务端之前，移动端会显示空状态。

用法：
    python tools/export_case_library.py [--out DIR]
    （--out 默认取运行目录 snapshots/ 下日期最新的快照包目录）
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from workbench.snapshot import ReadOnlyStore, resolve_runtime_root
from workbench.store import dumps

MAX_CASES = 1000
MAX_WIDTH = 1280


def latest_snapshot_dir(root):
    snapshots = root / "snapshots"
    candidates = sorted(
        (p for p in snapshots.glob("20??-??-??") if (p / "snapshot.json").is_file()),
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"{snapshots} 下没有快照包，先跑盘前快照导出")
    return candidates[0]


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except ValueError:
        return []


def export_cases(store, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    owner_rows = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")
    cases = []
    if owner_rows:
        owner = owner_rows[0]["value"]
        rows = store.rows(
            "SELECT * FROM pa_cases WHERE owner=? AND status='private_draft' "
            "ORDER BY updated DESC, id DESC LIMIT ?",
            (owner, MAX_CASES),
        )
        image_rows = store.rows(
            "SELECT ci.case_id, ci.ordinal, a.path AS rel, a.mime "
            "FROM pa_case_images ci JOIN pa_assets a ON a.id=ci.asset_id "
            "WHERE a.owner=? ORDER BY ci.case_id, ci.ordinal",
            (owner,),
        )
        images_by_case = {}
        for rel_row in image_rows:
            images_by_case.setdefault(rel_row["case_id"], []).append(rel_row)
        cases_dir = out_dir / "cases"
        for row in rows:
            images = []
            for img in images_by_case.get(row["id"], []):
                source = store.root / img["rel"]
                if not source.is_file():
                    continue
                try:
                    with Image.open(source) as pic:
                        pic = pic.convert("RGB")
                        if pic.width > MAX_WIDTH:
                            pic = pic.resize(
                                (MAX_WIDTH, round(pic.height * MAX_WIDTH / pic.width))
                            )
                        target_name = f"{row['id']}-{img['ordinal']}.jpg"
                        if not cases_dir.is_dir():
                            cases_dir.mkdir(parents=True)
                        pic.save(cases_dir / target_name, "JPEG", quality=82)
                    images.append(f"cases/{target_name}")
                except Exception:
                    continue  # 单图损坏不阻塞整包导出，索引里如实少一张
            cases.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "memory": row["memory"],
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "market": row["market"],
                    "keywords": _json_list(row["keywords"]),
                    "tags": _json_list(row["tags"]),
                    "topic_ids": _json_list(row["topic_ids"]),
                    "images": images,
                    "image_count": len(images),
                    "updated": row["updated"],
                }
            )
    payload = {
        "kind": "aplus-case-library",
        "version": 1,
        "count": len(cases),
        "cases": cases,
    }
    (out_dir / "cases.json").write_text(dumps(payload), encoding="utf-8")
    return {"out": str(out_dir), "cases": len(cases)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出案例库移动包（只读）")
    parser.add_argument("--out", default=None, help="目标包目录，默认最新快照包目录")
    args = parser.parse_args(argv)
    store = ReadOnlyStore()
    out = Path(args.out) if args.out else latest_snapshot_dir(resolve_runtime_root(None))
    print(dumps(export_cases(store, out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
