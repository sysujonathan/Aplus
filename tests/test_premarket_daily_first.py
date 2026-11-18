"""One isolated daily scope: first results, status facts, same-source repair."""
import json

import pandas as pd
import pytest

from tests.test_workbench import store, wait_for
from tests.test_daily_status_maintenance import setup, receipt, vendor
from workbench.history_quality import describe_history, save_quality
from workbench.market import save_dataset
from workbench.service import Service
from workbench.store import Store
from workbench.trading_status import status_facts


@pytest.mark.parametrize('outcome', ['repaired', 'unknown', 'cancelled'])
def test_first_qualified_scan_precedes_gap_closure_then_only_needed_work(store, monkeypatch, vendor, outcome):
    import workbench.status_maintenance as maintenance
    codes, full, hole, day = setup(store, count=3)
    save_dataset(store, codes[0], full, 'tickflow', '前复权')
    good = store.rows('SELECT * FROM datasets WHERE code=? ORDER BY rows DESC', (codes[0],))[0]
    save_quality(store, good['id'], describe_history(store, codes[0], full, full.date.iloc[0], day))
    calls, repairs = [], []
    service = Service(store)
    class StatusVendor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def trading_status(self, code, start, end):
            # Real Service has already committed the usable stock's result.
            scans = store.rows("SELECT status FROM jobs WHERE kind='scan'")
            assert scans and scans[0]['status'] == 'partial'
            assert [r['code'] for r in store.rows('SELECT code FROM observations')] == [codes[0]]
            if outcome == 'cancelled':
                next(iter(service.cancel_flags.values())).set()
            state = '0' if code == codes[1] else '1'
            rows = [] if outcome == 'unknown' and code == codes[2] else [
                dict(code=code, date=start, tradestatus=state, volume='')]
            return pd.DataFrame(rows, columns=['code', 'date', 'tradestatus', 'volume'])
    monkeypatch.setattr(maintenance, 'BaoStock', StatusVendor)
    def sync(self, job, spec):
        if not spec.get('repair_codes'):
            return receipt(self.store, day)
        assert spec['source'] == 'tickflow' and spec['repair_codes'] == [codes[2]]
        repairs.append(codes[2])
        did = save_dataset(self.store, codes[2], full, 'tickflow', '前复权')
        save_quality(self.store, did, describe_history(self.store, codes[2], full, full.date.iloc[0], day))
        return dict(datasets=[did], errors=[])
    def signal(_strategy, data, **kwargs):
        calls.append(data.attrs['code'])
        return dict(asof=day, setup_date=day, entry=10., stop=9., target=12.)
    monkeypatch.setattr(Service, '_sync_once', sync)
    monkeypatch.setattr('workbench.service.signal_at_end', signal)
    monkeypatch.setattr(Service, 'shape_tasks', lambda *_a: type('NoTracking', (), {'tracked': lambda *_a: None})())
    try:
        row = wait_for(store, service.submit('premarket', dict(sources=['tickflow'],
            boards=['沪深主板'], strategies=['STRATEGY_3K'], timeframe='daily',
            start=full.date.iloc[0], end=day)))
        result = json.loads(row['result'])
        source = result['sources']['tickflow']
        assert len(store.rows("SELECT id FROM jobs WHERE kind='sync'")) == 1
        assert not store.rows("SELECT id FROM jobs WHERE status IN ('running','queued')")
        if outcome == 'cancelled':
            assert row['status'] == 'cancelled'
            assert calls == [codes[0]] and not repairs
            assert len(source['scan_jobs']) == 1
            return
        assert status_facts(Store(store.root), codes[1])[0][hole] == '0'
        assert len(source['scan_jobs']) == 2
        assert sorted(calls) == (codes if outcome == 'repaired' else codes[:2])
        assert repairs == ([codes[2]] if outcome == 'repaired' else [])
        assert row['status'] == ('completed' if outcome == 'repaired' else 'partial'), result
        rows = store.rows('SELECT code FROM observations')
        assert len(rows) == len({r['code'] for r in rows}) == len(calls)
        assert len(store.rows('SELECT key FROM scan_cache')) == len(calls)
        latest_scan = store.rows('SELECT result FROM jobs WHERE id=?', (source['scan_job'],))[0]
        scan = json.loads(latest_scan['result'])
        assert scan['calculated'] == len(calls)-1 and scan['reused'] == 1
        assert scan['coverage']['ready'] == len(calls)
        assert source['scan_result']['success'] == len(calls)
        assert not store.rows('SELECT * FROM plans')
    finally:
        service.pool.shutdown()
