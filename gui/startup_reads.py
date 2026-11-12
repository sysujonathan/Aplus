"""Worker-only list preparation, reusing the admitted candidate/cache pipeline."""
from copy import deepcopy
import sqlite3

from .background_status import ReadView
from workbench.startup_metrics import span


class ReadSession(ReadView):
    """One thread-owned, query-only connection; never constructs Store."""
    def __init__(self, root, check=lambda: None):
        super().__init__(root, None)
        self.check = check
        self.db = None

    def __enter__(self):
        self.db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True,
                                  timeout=1, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        def progress():
            try: self.check(); return 0
            except InterruptedError: return 1
        self.db.set_progress_handler(progress, 2000)
        from workbench.sources import market_source
        try:self.source = market_source(self)
        except BaseException:
            self.db.close();raise
        return self

    def __exit__(self, *_):
        self.db.close()

    def rows(self, sql, args=()):
        self.check()
        if self.source and sql == "SELECT value FROM meta WHERE key='daily_market_source'":
            return [{'value': self.source}]
        return [dict(row) for row in self.db.execute(sql, args)]

    def shape_runs(self, source=None):
        from workbench.shape_store import runs
        return runs(self, source)

    def shape_templates(self):
        from workbench.shape_store import templates
        return templates(self)


def latest_for_keys(view, keys):
    result, groups = {}, {}
    for source, timeframe, code in dict.fromkeys(keys):
        groups.setdefault((source, timeframe), []).append(code)
    for (source, timeframe), codes in groups.items():
        for start in range(0, len(codes), 400):
            chunk = codes[start:start+400]
            rows = view.rows('SELECT * FROM (SELECT d.*, ROW_NUMBER() OVER '
                '(PARTITION BY code ORDER BY end DESC,created DESC,rowid DESC) AS _rank '
                'FROM datasets d WHERE source=? AND timeframe=? AND code IN ('+
                ','.join('?' for _ in chunk)+')) WHERE _rank=1', (source,timeframe,*chunk))
            for row in rows:
                row.pop('_rank', None)
                result[source,timeframe,row['code']] = row
    return result


def watch_rows(root, check=lambda: None):
    from .data import code_names
    from workbench.sources import market_source
    from workbench.watch_history import chart_source
    with span('watch.prepare'), ReadSession(root, check) as view:
        rows = view.rows('SELECT w.code AS code,w.notes AS wnotes,o.id AS observation_id,'
            'o.strategy AS strategy,o.asof AS asof,o.timeframe AS timeframe,d.source AS source,'
            'p.state AS state FROM watchlist w LEFT JOIN observations o ON o.id=w.observation_id '
            'LEFT JOIN datasets d ON d.id=o.dataset_id LEFT JOIN plans p ON p.observation_id=o.id '
            'WHERE w.active=1 ORDER BY w.updated DESC')
        names = {**{r['key'][len('watch_name:'):]:r['value'] for r in view.rows(
            "SELECT key,value FROM meta WHERE key LIKE 'watch_name:%'")}, **code_names(view)}
        held = {r['code'] for r in view.rows("SELECT DISTINCT code FROM positions WHERE status='OPEN'")}
        source = market_source(view)
        keys = [(chart_source(view,r['code']) if not r['observation_id'] else source,
                 r['timeframe'] or 'daily',r['code']) for r in rows]
        latest = latest_for_keys(view, keys)
        result = []
        for row,key in zip(rows,keys):
            check(); market = latest.get(key) or {}
            manual = not row['observation_id']
            result.append(dict(code=row['code'],name=names.get(row['code'],''),
                observation_id=row['observation_id'],source=market.get('source') or
                    (key[0] if manual else None) or row['source'] or 'unknown',
                strategy=row['strategy'],timeframe=key[1],market_dataset_id=market.get('id'),
                market_asof=market.get('end'),anchor_asof=row['asof'],manual=manual,
                state=('实际持仓 · ' if row['code'] in held else '')+(row['state'] or '关注中'),
                notes=row['wnotes'] or '',date=(row['asof'] or '')[:10]))
        return result


def match_rows(root, context, check=lambda: None):
    from .data import (load_candidates,code_names,data_inputs,snapshot_inputs,candidate_source_for_date)
    from workbench.scan_cache import engine_inputs,file_state
    from workbench.sources import market_source
    timeframe, asof, source, labels, cached = context
    with span('candidates.prepare'), ReadSession(root,check) as view:
        if not source:
            source = (market_source(view) if not asof or not any(asof)
                      else candidate_source_for_date(view,timeframe,asof))
        receipts = view.rows("SELECT id,status,result FROM jobs WHERE kind='scan' "
                             "ORDER BY created DESC,rowid DESC LIMIT 32")
        shape_receipts = [r for r in view.shape_runs(market_source(view))
            if r['status'] in ('completed','partial') and (not asof or all(
                not part or str(part)==r['asof'].split('-')[i] for i,part in enumerate(asof)))] if timeframe=='daily' else []
        key = (str(view.root),tuple(asof or ()),source,timeframe,repr(receipts),repr(shape_receipts),
            file_state(view.root/r['path'] for r in shape_receipts),data_inputs(view),engine_inputs(),
            tuple(labels),snapshot_inputs())
        if cached is not None and cached[0] == key:
            groups,all_rows,templates,error = deepcopy(cached[1])
        else:
            groups = load_candidates(view,timeframe,source,asof);check()
            quant = [dict(row,kind='quant',label=dict(labels).get(k,k)) for k,rows in groups.items() for row in rows]
            error = None
            try:
                from workbench.shape_store import candidates
                shapes = candidates(view,asof,market_source(view)) if timeframe=='daily' else []
            except (ValueError,OSError) as exc:
                shapes,error = [],str(exc)
            names = code_names(view) if shapes else {}
            all_rows = quant+[dict(r,name=names.get(r['code'],r.get('name',''))) for r in shapes]
            try: templates = {t['id']:t['name'] for t in view.shape_templates()}
            except ValueError as exc: templates,error = {},str(exc)
        check()
        return dict(rows=groups,all_rows=all_rows,templates=templates,error=error,source=source,
                    cache=(key,deepcopy((groups,all_rows,templates,error))) if not error else None)


def date_options(root, timeframe, check=lambda: None):
    from .data import candidate_dates,latest_candidate_date
    with span('toolbar.dates.prepare'), ReadSession(root,check) as view:
        dates = candidate_dates(view,timeframe)
        check()
        return dict(dates=dates,latest=latest_candidate_date(view,timeframe))
