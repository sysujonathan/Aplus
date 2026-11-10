"""Export one dated premarket result as a client-consumable snapshot.

只读导出：经 ReadOnlyStore（SQLite mode=ro）读取，不构造 Store()，
不运行初始化/迁移、不写数据库、不碰策略、不联网。产物写到
<运行目录>/snapshots/<asof>/（可用 --out 改位置），含 snapshot.json 与
index.html；客户端只需取这两个文件，不再跑同步与扫描。

用法：
    python tools/export_premarket_snapshot.py [--asof 2026-11-09] [--out DIR]
    python -m tools.export_premarket_snapshot [--asof 2026-11-09] [--out DIR]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.snapshot import (
    ReadOnlyStore,
    latest_asof,
    load_snapshot,
    snapshot_summary,
    write_snapshot,
)
from workbench.store import dumps


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出盘前快照（只读）")
    parser.add_argument("--asof", default=None, help="快照日期，默认取最新盘前任务")
    parser.add_argument("--out", default=None, help="输出目录，默认 <运行目录>/snapshots/<asof>")
    args = parser.parse_args(argv)
    store = ReadOnlyStore()
    asof = args.asof or latest_asof(store)
    out = write_snapshot(store, asof, args.out)
    snapshot = load_snapshot(out / "snapshot.json")
    print(dumps({"out": str(out), **snapshot_summary(snapshot)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
