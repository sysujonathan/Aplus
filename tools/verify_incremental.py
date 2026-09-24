"""Small live provider check in a disposable runtime, never production data."""
import json
import tempfile
from pathlib import Path

import pandas as pd

from workbench.market import BaoStock, load_dataset
from workbench.store import Store
from workbench.sync import sync_stock


def main():
    with tempfile.TemporaryDirectory(prefix='a-sync-verification-') as root:
        store = Store(root)
        with BaoStock() as provider:
            first, _ = sync_stock(store, lambda:provider, 'sh.600000', '2026-09-01', '2026-09-10')
            second, action = sync_stock(store, lambda:provider, 'sh.600000', '2026-09-01', '2026-09-22')
            expected = provider.fetch('sh.600000', '2026-09-01', '2026-09-22')
        def offline():
            raise AssertionError('Repeated sync attempted network access')
        third, repeated = sync_stock(store, offline, 'sh.600000', '2026-09-01', '2026-09-22')
        data, _ = load_dataset(store, second)
        pd.testing.assert_frame_equal(data, expected, check_exact=False)
        assert third == second and repeated == 'skipped'
        print(json.dumps(dict(stock='sh.600000', old_rows=len(load_dataset(store, first)[0]),
                              merged_rows=len(data), update=action, repeated=repeated,
                              full_download_comparison='equal', production_data_touched=False)))


if __name__ == '__main__':
    main()
