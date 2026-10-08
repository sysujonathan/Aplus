"""Explicit daily data routing; changing a source never rewrites old snapshots."""
SOURCES = {'baostock': 'BaoStock', 'tickflow': 'TickFlow'}


def market_source(store):
    rows = store.rows("SELECT value FROM meta WHERE key='daily_market_source'")
    value=rows[0].get('value') if rows else None
    return value if value in SOURCES else 'baostock'


def set_market_source(store, source):
    if source not in SOURCES:
        raise ValueError('未知日 K 数据源')
    if store.rows("SELECT id FROM jobs WHERE status IN ('queued','running')"):
        raise ValueError('请等待当前任务结束后切换数据源')
    store.execute("INSERT INTO meta VALUES('daily_market_source',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (source,))


def coverage_state(store, code, source='baostock'):
    import json
    if source == 'baostock':
        return store.rows('SELECT * FROM sync_coverage WHERE code=?', (code,))
    rows = store.rows('SELECT value FROM meta WHERE key=?', ('sync_coverage:'+source+':'+code,))
    return [json.loads(rows[0]['value'])] if rows else []


def save_coverage(store, code, dataset_id, start, end, source='baostock'):
    from .store import dumps
    if source == 'baostock':
        store.execute('INSERT INTO sync_coverage VALUES(?,?,?,?) ON CONFLICT(code) DO UPDATE SET '
                      'dataset_id=excluded.dataset_id,start=excluded.start,end=excluded.end', (code,dataset_id,start,end))
    else:
        store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      ('sync_coverage:'+source+':'+code, dumps(dict(code=code,dataset_id=dataset_id,start=start,end=end))))
