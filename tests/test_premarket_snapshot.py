"""Premarket snapshot export: read-only build, files, and reload."""
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3

import pytest

from tests.test_workbench import store  # noqa: F401  (pytest fixture)
from workbench.snapshot import (
    ReadOnlyStore,
    build_snapshot,
    latest_asof,
    load_snapshot,
    render_html,
    write_snapshot,
)
from workbench.strategies import scan_engine_version
from workbench.store import dumps

ASOF = (date.today() - timedelta(days=1)).isoformat()


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
        "observation_ids": ["obs-1", "obs-2"] if hits else [],
        "engine_version": scan_engine_version(),
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
             ASOF, (date.fromisoformat(ASOF) - timedelta(days=4)).isoformat(), "ds-1",
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
    assert snapshot["engine_version"] == scan_engine_version()
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


def _patch_scan_result(store, **patch):
    rows = store.rows("SELECT result FROM jobs WHERE id='scan-1'")
    result = json.loads(rows[0]["result"])
    result.update(patch)
    store.execute("UPDATE jobs SET result=? WHERE id='scan-1'", (dumps(result),))


def _insert_observation(store, oid, job_id, code, strategy, timeframe, dataset_id,
                        entry=10.0, asof=ASOF):
    store.execute(
        "INSERT INTO observations(id,job_id,code,strategy,version,timeframe,asof,"
        "setup_date,dataset_id,payload,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (oid, job_id, code, strategy, "v-x", timeframe, asof, asof, dataset_id,
         dumps({"entry": entry, "stop": 9.0, "target": 12.0}),
         ASOF + "T13:20:00+00:00"),
    )


def _insert_dataset(store, dataset_id, code, source, timeframe="daily"):
    store.execute(
        "INSERT INTO datasets(id,code,timeframe,source,adjustment,start,end,rows,"
        "path,sha256,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (dataset_id, code, timeframe, source, "qfq", "2024-01-01", ASOF, 100,
         f"{dataset_id}.csv", "x" * 64, ASOF + "T13:00:00+00:00"),
    )


def test_hits_bound_to_selected_scan_receipt(store):
    _seed(store)
    # A legitimately reused hit from an earlier scan job: the receipt lists
    # its id even though its job_id is not the selected scan job.
    _insert_dataset(store, "ds-3", "sh.600566", "tickflow")
    _insert_observation(store, "obs-reused", "scan-old", "sh.600566",
                        "MTR_MASTER", "daily", "ds-3", entry=29.74)
    # Same-day observations that are NOT in the selected receipt must never
    # leak into the snapshot: other source, other board, other timeframe,
    # unselected strategy, and a superseded revision of an included hit.
    _insert_dataset(store, "ds-bao", "sz.300001", "baostock")
    _insert_observation(store, "obs-x-source", "scan-other", "sz.300001",
                        "STRATEGY_GAP_H2", "daily", "ds-bao")
    _insert_observation(store, "obs-x-weekly", "scan-1", "sh.600853",
                        "MTR_MASTER", "weekly", "ds-2")
    _insert_observation(store, "obs-x-strategy", "scan-1", "sh.600853",
                        "STRATEGY_3K", "daily", "ds-2")
    _insert_observation(store, "obs-x-revision", "scan-1", "sh.601919",
                        "STRATEGY_GAP_H2", "daily", "ds-1", entry=99.99)
    _patch_scan_result(store, observation_ids=["obs-1", "obs-2", "obs-reused"])

    snapshot = build_snapshot(store, ASOF)
    codes = sorted(hit["code"] for hit in snapshot["hits"])
    assert codes == ["sh.600566", "sh.600853", "sh.601919"]
    gap = next(hit for hit in snapshot["hits"] if hit["code"] == "sh.601919")
    assert gap["entry"] == 17.63  # the receipt revision, not the 99.99 revision


def test_receipt_ids_out_of_scope_are_dropped_with_note(store, tmp_path, monkeypatch):
    _seed(store)
    # The receipt itself lists an id whose row is out of the snapshot scope
    # (weekly instead of the spec's daily): it must be dropped, not shipped.
    _insert_observation(store, "obs-weekly", "scan-1", "sh.600853",
                        "MTR_MASTER", "weekly", "ds-2")
    _patch_scan_result(store, observation_ids=["obs-1", "obs-2", "obs-weekly"])
    snapshot = build_snapshot(store, ASOF)
    assert [hit["code"] for hit in snapshot["hits"]] == ["sh.600853", "sh.601919"]
    assert any("范围外" in note for note in snapshot["notes"])
    out = write_snapshot(ReadOnlyStore(store.root), ASOF, tmp_path / "dropped")
    loaded = load_snapshot(out / "snapshot.json")
    assert set(loaded["observation_ids"]) == {"obs-1", "obs-2"}
    assert set(loaded["charts"]) | set(loaded["chart_errors"]) == set(loaded["observation_ids"])
    from gui.data import load_candidates, candidate_source_status
    monkeypatch.setenv("APLUS_SNAPSHOT_DIR", str(out))
    rows = load_candidates(store, source="tickflow")
    assert {r["observation_id"] for group in rows.values() for r in group} == {"obs-1", "obs-2"}
    assert "服务端快照" in candidate_source_status(store)



def test_rescan_without_hits_empties_snapshot(store):
    _seed(store)
    # A newer premarket run the same day whose scan found nothing: the
    # snapshot must follow the newest run, not resurrect the morning hits.
    scan2 = {
        "asof": ASOF, "success": 12, "signals": 0, "no_signal": 12,
        "reused": 0, "calculated": 12, "observation_ids": [],
        "engine_version": scan_engine_version(),
        "strategy_versions": {"STRATEGY_GAP_H2": "v-gap-h2", "MTR_MASTER": "v-mtr"},
        "coverage": {"expected": 5, "ready": 5, "suspended": 0, "gaps": 0},
        "errors": [],
    }
    report2 = {
        "asof": ASOF, "complete": True,
        "sources": {"tickflow": {"sync_job": "sync-1", "scan_job": "scan-2",
                                 "sync_seconds": 1.0, "scan_seconds": 2.0,
                                 "sync_status": "completed",
                                 "scan_status": "completed"}},
    }
    spec = {"sources": ["tickflow"], "boards": ["沪深主板"],
            "strategies": ["MTR_MASTER", "STRATEGY_GAP_H2"],
            "timeframe": "daily", "start": "2024-11-09"}
    store.execute(
        "INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)",
        ("scan-2", "scan", "completed", ASOF + "T14:03:02+00:00",
         dumps({}), dumps(scan2)),
    )
    store.execute(
        "INSERT INTO jobs(id,kind,status,created,spec,result) VALUES(?,?,?,?,?,?)",
        ("pm-2", "premarket", "completed", ASOF + "T14:00:31+00:00",
         dumps(spec), dumps(report2)),
    )
    snapshot = build_snapshot(store, ASOF)
    assert snapshot["jobs"]["premarket"] == "pm-2"
    assert snapshot["hits"] == []
    assert any("无命中" in note for note in snapshot["notes"])


def test_scan_receipt_without_observation_ids_raises(store):
    _seed(store)
    rows = store.rows("SELECT result FROM jobs WHERE id='scan-1'")
    result = json.loads(rows[0]["result"])
    del result["observation_ids"]
    store.execute("UPDATE jobs SET result=? WHERE id='scan-1'", (dumps(result),))
    with pytest.raises(ValueError, match="observation_ids"):
        build_snapshot(store, ASOF)


def test_excluded_list_not_truncated_in_json(store):
    _seed(store)
    errors = [
        {"code": f"sh.6{i:05d}", "category": "input_gap", "error": "缺少交易日"}
        for i in range(604)
    ]
    _patch_scan_result(store, errors=errors)
    snapshot = build_snapshot(store, ASOF)
    assert snapshot["coverage"]["excluded_count"] == 604
    assert len(snapshot["excluded"]) == 604
    assert snapshot["excluded"][-1]["code"] == "sh.600603"
    text = render_html(snapshot)
    details = text.split("<details>", 1)[1]
    assert details.count("<li>") == 100  # HTML stays capped, JSON is complete
    assert "共 604 只，列出前 100 只" in text


def test_readonly_store_never_initializes_database(store, tmp_path):
    _seed(store)
    store.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    before = store.path.read_bytes()
    readonly = ReadOnlyStore(store.root)
    snapshot = build_snapshot(readonly, ASOF)
    assert len(snapshot["hits"]) == 2
    assert store.path.read_bytes() == before
    assert not (store.root / "backups").exists()
    with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
        version = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert version == "8"


def test_export_cli_leaves_database_untouched(store, tmp_path, monkeypatch):
    from tools.export_premarket_snapshot import main as export_main

    _seed(store)
    store.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    before = store.path.read_bytes()
    monkeypatch.setenv("A_WORKBENCH_HOME", str(store.root))
    out = tmp_path / "cli-out"
    assert export_main(["--asof", ASOF, "--out", str(out)]) == 0
    snapshot = load_snapshot(out / "snapshot.json")
    assert len(snapshot["hits"]) == 2
    assert store.path.read_bytes() == before
    assert not (store.root / "backups").exists()
    with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
        version = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert version == "8"


def _client_package(store, tmp_path, monkeypatch):
    from workbench.market import save_dataset
    import pandas as pd
    import numpy as np

    _seed(store)
    x = np.arange(180)
    prices = 20 + np.sin(x / 4) + x / 100
    frame = pd.DataFrame(dict(date=pd.bdate_range(end=ASOF, periods=180).strftime('%Y-%m-%d'),
                              open=prices, high=prices + .5, low=prices - .5,
                              close=prices + .1, volume=10000.))
    for oid, code in [('obs-1', 'sh.601919'), ('obs-2', 'sh.600853')]:
        dataset = save_dataset(store, code, frame, 'tickflow', 'qfq')
        store.execute('UPDATE observations SET dataset_id=? WHERE id=?', (dataset, oid))
    payload = json.loads(store.rows("SELECT payload FROM observations WHERE id='obs-2'")[0]['payload'])
    payload.update(rating={'letter': 'A', 'score': 82}, score=71.5)
    store.execute("UPDATE observations SET payload=? WHERE id='obs-2'", (dumps(payload),))
    out = write_snapshot(ReadOnlyStore(store.root), ASOF, tmp_path / 'client-package')
    monkeypatch.setenv('APLUS_SNAPSHOT_DIR', str(out))
    monkeypatch.delenv('APLUS_SNAPSHOT_URL', raising=False)
    return out


def test_snapshot_candidates_exact_receipt_prices_without_rating(store, tmp_path, monkeypatch):
    from gui.data import load_candidates, candidate_source_status
    out = _client_package(store, tmp_path, monkeypatch)
    snapshot = load_snapshot(out / 'snapshot.json')
    # Independent receipt from the DB, not reconstructed from snapshot hits.
    receipt = json.loads(store.rows("SELECT result FROM jobs WHERE id='scan-1'")[0]['result'])
    groups = load_candidates(store, source='tickflow', asof_filter=tuple(ASOF.split('-')))
    rows = [row for group in groups.values() for row in group]
    assert {r['observation_id'] for r in rows} == set(receipt['observation_ids'])
    for row in rows:
        original = json.loads(store.rows('SELECT payload FROM observations WHERE id=?',
                                        (row['observation_id'],))[0]['payload'])
        for key in ('entry', 'stop', 'target', 'sl1_plan_price', 'mm_target'):
            assert row['payload'].get(key) == original.get(key)
    rated = next(h for h in snapshot['hits'] if h['observation_id'] == 'obs-2')
    assert 'rating' not in rated.get('plan', {})
    assert 'score' not in rated.get('plan', {})
    assert_no_retired_fields(json.loads((out / 'snapshot.json').read_text(encoding="utf-8")))
    assert '服务端快照' in candidate_source_status(store)
    assert snapshot['generated_at'] in candidate_source_status(store)


def test_snapshot_package_charts_match_manifest(store, tmp_path, monkeypatch):
    # Packaging boundary test: rendering is independently exercised below.
    from gui import chart_renderer
    from PIL import Image
    monkeypatch.setattr(chart_renderer, 'render_chart',
                        lambda *a, **kw: Image.new('RGB', (64, 64)))
    from PIL import Image
    out = _client_package(store, tmp_path, monkeypatch)
    snapshot = load_snapshot(out / 'snapshot.json')
    assert snapshot['chart_errors'] == {}
    assert set(snapshot['charts']) == set(snapshot['observation_ids'])
    for relative in snapshot['charts'].values():
        with Image.open(out / relative) as image:
            image.verify()


def test_single_chart_failure_is_recorded(store, tmp_path, monkeypatch):
    from gui import chart_renderer
    from PIL import Image
    real = lambda *a, **kw: Image.new('RGB', (64, 64))
    def render(*args, **kwargs):
        if kwargs['code'] == 'sh.601919':
            raise RuntimeError('injected chart failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(chart_renderer, 'render_chart', render)
    out = _client_package(store, tmp_path, monkeypatch)
    snapshot = load_snapshot(out / 'snapshot.json')
    assert set(snapshot['charts']) == {'obs-2'}
    assert snapshot['chart_errors'] == {'obs-1': 'injected chart failure'}


@pytest.mark.parametrize('fault, message', [
    ('sha', 'SHA'), ('ids', 'observation_ids'),
    ('engine', '版本锚'), ('frozen', '版本锚'), ('expired', '过期'),
    ('structure', '结构'),
])
def test_bad_snapshot_rejected_and_visible_local_fallback(store, tmp_path, monkeypatch, caplog, fault, message):
    from gui.data import load_candidates, candidate_source_status
    from workbench.snapshot import snapshot_digest
    out = _client_package(store, tmp_path, monkeypatch)
    path = out / 'snapshot.json'
    data = json.loads(path.read_text(encoding="utf-8"))
    if fault == 'sha':
        data['sha256'] = '0' * 64
    else:
        if fault == 'ids': data['hits'][0]['observation_id'] = 'outside-receipt'
        elif fault == 'engine': data['engine_version'] = 'wrong-engine'
        elif fault == 'frozen': data['frozen_digest'] = 'wrong-frozen'
        elif fault == 'expired': data['asof'] = '2000-01-01'
        elif fault == 'structure': data['hits'][0] = []
        data['sha256'] = snapshot_digest(data)  # Reach the intended check, beyond SHA.
    path.write_text(dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_snapshot(path)
    monkeypatch.delenv('APLUS_SNAPSHOT_DIR')
    local = load_candidates(store, source='tickflow')
    monkeypatch.setenv('APLUS_SNAPSHOT_DIR', str(out))
    assert load_candidates(store, source='tickflow') == local
    assert message in candidate_source_status(store)
    assert '回退本地扫描' in caplog.text


def test_snapshot_http_and_directory_use_same_validation(store, tmp_path, monkeypatch):
    from gui import data
    from contextlib import contextmanager
    out = _client_package(store, tmp_path, monkeypatch)
    expected = data.load_candidates(store, source='tickflow')
    @contextmanager
    def response(address, timeout):
        assert address == 'http://snapshot.test/serve/snapshot.json'
        assert timeout == 10
        with (out / 'snapshot.json').open('rb') as stream:
            yield stream
    monkeypatch.setattr(data, 'urlopen', response)
    monkeypatch.delenv('APLUS_SNAPSHOT_DIR')
    monkeypatch.setenv('APLUS_SNAPSHOT_URL', 'http://snapshot.test/serve')
    assert data.load_candidates(store, source='tickflow') == expected


def test_real_snapshot_chart_rendering(store, tmp_path, monkeypatch):
    pytest.importorskip('mplfinance')
    out = _client_package(store, tmp_path, monkeypatch)
    snapshot = load_snapshot(out / 'snapshot.json')
    assert snapshot['chart_errors'] == {}
    assert len(snapshot['charts']) == 2


def test_snapshot_candidates_need_no_local_observations(store, tmp_path, monkeypatch):
    from gui.data import load_candidates
    out = _client_package(store, tmp_path, monkeypatch)
    class EmptyClient:
        root = tmp_path / 'empty-client'
        def rows(self, *args, **kwargs):
            pytest.fail('valid snapshot candidates must not query local observations')
    groups = load_candidates(EmptyClient(), source='tickflow')
    ids = {r['observation_id'] for rows in groups.values() for r in rows}
    assert ids == set(load_snapshot(out / 'snapshot.json')['observation_ids'])


@pytest.mark.parametrize('hour', [0, 7])
@pytest.mark.parametrize('generated_offset', [0, 8])
def test_snapshot_accepts_beijing_early_morning(store, tmp_path, monkeypatch, hour, generated_offset):
    import workbench.snapshot as module
    from workbench.snapshot import snapshot_digest
    monkeypatch.setattr(__import__(__name__, fromlist=['ASOF']), 'ASOF', '2026-11-11')
    instant = datetime(2026, 11, 11, hour, 30, tzinfo=timezone(timedelta(hours=8)))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    _seed(store)
    out = write_snapshot(store, '2026-11-11', tmp_path / 'early')
    path = out / 'snapshot.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['generated_at'] = instant.astimezone(timezone(timedelta(hours=generated_offset))).isoformat()
    data['sha256'] = snapshot_digest(data)
    path.write_text(dumps(data), encoding='utf-8')
    assert load_snapshot(path)['asof'] == '2026-11-11'
    assert load_snapshot(path, today=date(2026, 11, 11))['asof'] == '2026-11-11'
    assert load_snapshot(path, today='2026-11-11')['asof'] == '2026-11-11'


def test_relative_asof_survives_november_17(store, tmp_path, monkeypatch):
    import workbench.snapshot as module
    # Re-evaluate the module's actual ASOF expression with a simulated calendar.
    class Calendar(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 17)
    namespace = {'date': Calendar, 'timedelta': timedelta}
    from pathlib import Path
    assignment = next(line for line in Path(__file__).read_text(encoding='utf-8').splitlines()
                      if line.startswith('ASOF = '))
    exec(assignment, namespace)
    monkeypatch.setattr(__import__(__name__, fromlist=['ASOF']), 'ASOF', namespace['ASOF'])
    instant = datetime(2026, 11, 17, 4, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(module, 'now', lambda: instant.isoformat())
    test_write_snapshot_round_trip_and_html_escaping(store, tmp_path)


def test_snapshot_inputs_changes_with_file_and_url(tmp_path, monkeypatch):
    from gui.data import snapshot_inputs
    monkeypatch.setenv('APLUS_SNAPSHOT_DIR', str(tmp_path))
    monkeypatch.delenv('APLUS_SNAPSHOT_URL', raising=False)
    missing = snapshot_inputs()
    path = tmp_path / 'snapshot.json'
    path.write_text('{}', encoding='utf-8')
    created = snapshot_inputs()
    assert created != missing
    path.write_text('{"updated": true}', encoding='utf-8')
    assert snapshot_inputs() != created
    local = snapshot_inputs()
    monkeypatch.setenv('APLUS_SNAPSHOT_URL', 'https://snapshot.test')
    assert snapshot_inputs() != local
    path.unlink()
    monkeypatch.delenv('APLUS_SNAPSHOT_URL')
    assert snapshot_inputs() == missing


def test_snapshot_basic_iso_asof_candidates(store, tmp_path, monkeypatch):
    from gui.data import load_candidates, candidate_source_status
    from workbench.snapshot import snapshot_digest
    out = _client_package(store, tmp_path, monkeypatch)
    path = out / 'snapshot.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['asof'] = ASOF.replace('-', '')
    data['sha256'] = snapshot_digest(data)
    path.write_text(dumps(data), encoding='utf-8')
    assert load_candidates(store, source='tickflow', asof_filter=tuple(ASOF.split('-')))
    assert '服务端快照' in candidate_source_status(store)


def assert_no_retired_fields(value):
    """Independent export acceptance check, including nested plan/evaluation data."""
    if isinstance(value, dict):
        assert not ({'rating', 'score'} & value.keys()), 'retired rating/score field'
        for child in value.values():
            assert_no_retired_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_retired_fields(child)


def test_export_acceptance_check_detects_injected_rating():
    with pytest.raises(AssertionError, match='retired'):
        assert_no_retired_fields({'hits': [{'plan': {'rating': {'score': 82}}}]})
    with pytest.raises(AssertionError, match='retired'):
        assert_no_retired_fields({'hits': [{'score': 82}]})


def test_historical_snapshot_accepts_and_ignores_rating(store, tmp_path, monkeypatch):
    from workbench.snapshot import snapshot_digest
    out = _client_package(store, tmp_path, monkeypatch)
    path = out / 'snapshot.json'
    original = json.loads(path.read_text(encoding="utf-8"))
    expected = load_snapshot(path)
    original['hits'][0].update(rating={'score': 99}, score=99)
    original['hits'][0].setdefault('plan', {})['setup_evaluation'] = {'rating': {'score': 88}, 'score': 88}
    original['sha256'] = snapshot_digest(original)
    path.write_text(json.dumps(original))
    loaded = load_snapshot(path)
    assert_no_retired_fields(loaded)
    assert loaded['hits'][0]['entry'] == expected['hits'][0]['entry']
    assert loaded['hits'][0]['plan']['setup_evaluation'] == {}
    # Disk stays unchanged: old integrity metadata describes original bytes/data.
    assert json.loads(path.read_text(encoding="utf-8")) == original
