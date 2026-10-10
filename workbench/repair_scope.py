"""Read-only reuse of a completed BaoStock sync's certified preflight files."""
import io
import json

import pandas as pd

from .readiness import expected_day
from .sources import directory_date
from .store import digest


def cached_baostock_repair_scope(store, asof):
    """Return (trading day, directory) or None; file presence alone is not proof.

    Legacy syncs already logged hashes and dated basics after membership checks.
    Bind those exact bytes to a completed/partial sync, so an update performed
    before this code upgrade can be reused without another network preflight.
    No Store writes, price reads, source fallback or quality-gate changes.
    """
    try:
        day = expected_day(store, asof, source='baostock')
        if directory_date(store, 'baostock') != day:
            return None
        relatives = ('universe.csv', f'directories/{day}.csv',
                     f'directories/{day}-basics.csv', 'trading_calendar.json')
        paths = [str((store.root / name).resolve()) for name in relatives]
        contents = [(store.root / name).read_bytes() for name in relatives]
        if contents[0] != contents[1]:
            return None
        for job in store.rows("SELECT id,spec,result FROM jobs WHERE kind='sync' "
                              "AND status IN ('completed','partial') ORDER BY created DESC,rowid DESC"):
            spec, report = json.loads(job['spec']), json.loads(job['result'])
            if (spec.get('source', 'baostock') != 'baostock' or 'boards' not in spec or
                    report.get('source') != 'baostock' or report.get('end_requested') != day):
                continue
            events = store.rows("SELECT seq,path FROM events WHERE job_id=? "
                                "AND action IN ('写入文件','复用已有文件') "
                                "AND path IN (?,?,?)", (job['id'], *paths[:3]))
            if set(e['path'] for e in events) != set(paths[:3]):
                continue
            bound = max(e['seq'] for e in events)
            for path, content in zip(paths, contents):
                proof = store.rows("SELECT detail FROM events WHERE path=? AND action='写入文件' "
                                   "AND seq<=? ORDER BY seq DESC LIMIT 1", (path, bound))
                if not proof or json.loads(proof[0]['detail']).get('sha256') != digest(content):
                    break
            else:
                directory = pd.read_csv(io.BytesIO(contents[0]), dtype=str, keep_default_na=False)
                if (not directory.empty and {'code', 'code_name', 'tradeStatus'} <= set(directory) and
                        not directory.code.duplicated().any() and directory.tradeStatus.isin(['0', '1']).all()):
                    return day, directory
        return None
    except (OSError, ValueError, KeyError, TypeError):
        # Stale, unverified or damaged context follows the original preflight.
        return None
