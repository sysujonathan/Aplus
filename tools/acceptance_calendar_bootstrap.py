"""Issue #148: isolated TickFlow calendar bootstrap and read-only reference check.

Run before/after the fix with --phase before/after. No provider or user DB is
opened. A sentinel at TickFlow construction proves the sync calendar gate passed;
this is not a network/K-line download acceptance test.
"""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.exchange_calendar import is_trading_day
from workbench.sources import calendar_file
from workbench.store import Store
from workbench.tickflow_service import sync_tickflow


class ProviderReached(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    print(f'phase={args.phase}; end=2026-11-12; provider=sentinel (no network)')
    for start, year in [('2024-11-11', 2024), ('2019-01-01', 2019), ('2016-01-01', 2016)]:
        with TemporaryDirectory(prefix='aplus-calendar-148-') as folder:
            store = Store(Path(folder))
            service = SimpleNamespace(store=store)
            with patch('workbench.tickflow_service.completed_date', return_value='2026-11-12'), \
                 patch('workbench.tickflow.TickFlow', side_effect=ProviderReached) as provider:
                try:
                    sync_tickflow(service, None, {'start': start, 'end': '2026-11-12', 'codes': []})
                except ValueError as exc:
                    assert args.phase == 'before' or year != 2024, str(exc)
                    assert f'{year} 年缺少已核验历史交易日历' in str(exc), str(exc)
                    assert provider.call_count == 0
                    assert not calendar_file(store, 'tickflow').exists()
                    print(f'{start}: BLOCKED before provider: {exc}')
                except ProviderReached:
                    assert args.phase == 'after' and year == 2024
                    payload = json.loads(calendar_file(store, 'tickflow').read_text())
                    assert payload['start'] == start and payload['end'] == '2026-11-12'
                    assert len(payload['trading_days']) == 487
                    print(f'{start}: calendar PASS; trading_days=487; provider reached')
                else:
                    raise AssertionError('provider sentinel was not reached')
    if args.reference:
        assert args.phase == 'after'
        raw = args.reference.read_bytes()
        reference = json.loads(raw)
        print(f'reference_sha256={hashlib.sha256(raw).hexdigest()}; '
              f'coverage={reference["start"]}..{reference["end"]}')
        for year, count in [(2024, 242), (2025, 243)]:
            first, last = date(year, 1, 1), date(year, 12, 31)
            assert reference['start'] <= first.isoformat() <= last.isoformat() <= reference['end']
            actual = set()
            cursor = first
            while cursor <= last:
                state = is_trading_day(cursor)
                assert state is not None, cursor
                if state:
                    actual.add(cursor.isoformat())
                cursor += timedelta(days=1)
            expected = {d for d in reference['trading_days'] if d[:4] == str(year)}
            extra, missing = sorted(actual - expected), sorted(expected - actual)
            print(f'{year}: product={len(actual)}; BaoStock={len(expected)}; '
                  f'extra={extra}; missing={missing}')
            assert len(actual) == len(expected) == count and not extra and not missing


if __name__ == '__main__':
    main()
