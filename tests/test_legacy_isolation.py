"""工程 A 历史快照必须与新版实时候选保持显式隔离。"""

import pandas as pd

from gui.data import (
    latest_legacy_observation,
    latest_observation,
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
                    "2026-09-29",
                    "2026-09-29",
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
