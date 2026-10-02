"""工程 A 历史快照必须与新版实时候选保持显式隔离。"""

import pandas as pd

from gui.data import (
    candidate_dates,
    candidate_source_for_date,
    latest_candidate_date,
    latest_market_dataset,
    latest_market_date,
    latest_legacy_observation,
    latest_observation,
    latest_scan_date,
    latest_signal_date,
    load_candidates,
    load_legacy_candidates,
)
from workbench.market import save_dataset
from workbench.store import Store, now


def test_legacy_history_never_enters_default_realtime_candidate_query(tmp_path):
    store = Store(tmp_path)
    frame = pd.DataFrame(
        {
            "date": ["2026-09-28", "2026-09-29"],
            "open": [10.0, 10.2],
            "high": [10.5, 10.6],
            "low": [9.8, 10.0],
            "close": [10.2, 10.4],
            "volume": [1000.0, 1200.0],
        }
    )
    legacy = save_dataset(
        store,
        "sz.000001",
        frame,
        "legacy-engine-a",
        "前复权（工程A只读迁移）",
    )
    live = save_dataset(store, "sh.600000", frame, "baostock", "前复权")
    latest_frame = pd.concat(
        [frame, pd.DataFrame([{
            "date": "2026-09-30", "open": 10.4, "high": 10.8,
            "low": 10.2, "close": 10.6, "volume": 1300.0,
        }])],
        ignore_index=True,
    )
    latest_live = save_dataset(
        store, "sh.600000", latest_frame, "baostock", "前复权"
    )
    created = now()
    with store.connect() as db:
        db.executemany(
            "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "legacy-observation",
                    "legacy-job",
                    "sz.000001",
                    "STRATEGY_GAP_H2",
                    "legacy",
                    "daily",
                    "2026-09-29",
                    "2026-09-29",
                    legacy,
                    "{}",
                    created,
                ),
                (
                    "live-observation",
                    "live-job",
                    "sh.600000",
                    "MTR_MASTER",
                    "live",
                    "daily",
                    "2026-09-28",
                    "2026-09-28",
                    live,
                    "{}",
                    created,
                ),
            ],
        )

    realtime = load_candidates(store, "daily")
    history = load_legacy_candidates(store, "daily")

    assert set(realtime) == {"MTR_MASTER"}
    assert realtime["MTR_MASTER"][0]["source"] == "baostock"
    assert set(history) == {"STRATEGY_GAP_H2"}
    assert history["STRATEGY_GAP_H2"][0]["source"] == "legacy-engine-a"
    assert latest_observation(store, "sz.000001") is None
    assert latest_legacy_observation(store, "sz.000001") == "legacy-observation"
    # 日期栏同时显示新行情日和已有扫描日；启动仍停在最近扫描日。
    assert candidate_dates(store) == ["2026-09-30", "2026-09-29", "2026-09-28"]
    assert candidate_dates(store, source="legacy-engine-a") == ["2026-09-29"]
    assert latest_candidate_date(store) == "2026-09-29"
    assert latest_scan_date(store) == "2026-09-29"
    assert latest_market_date(store) == "2026-09-30"
    assert latest_signal_date(store) == "2026-09-28"
    assert latest_market_dataset(store, "sh.600000")["id"] == latest_live
    assert latest_market_dataset(store, "sz.000001") is None
    assert candidate_source_for_date(store, asof_filter=("2026", "09", "29")) == "legacy-engine-a"
    assert candidate_source_for_date(store, asof_filter=("2026", "09", "28")) == "baostock"
    assert candidate_source_for_date(store, asof_filter=("2026", "09", "30")) == "baostock"

    with store.connect() as db:
        db.execute(
            "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "live-same-day",
                "live-job",
                "sh.600000",
                "MTR_MASTER",
                "live",
                "daily",
                "2026-09-29",
                "2026-09-29",
                live,
                "{}",
                created,
            ),
        )
    assert candidate_source_for_date(store, asof_filter=("2026", "09", "29")) == "baostock"
    assert latest_signal_date(store) == "2026-09-29"
