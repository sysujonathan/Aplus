"""Explicit daily data routing; changing a source never rewrites old snapshots."""
import json

SOURCES = {'baostock': 'BaoStock', 'tickflow': 'TickFlow', 'tencent': '腾讯'}
UPDATE_SOURCES = ('baostock', 'tickflow')
RETIRED_MESSAGE = '腾讯历史行情已停用更新与补拉；已有行情、扫描和研究记录仍可查看。请改用 BaoStock 或 TickFlow 更新。'
FALLBACK_SOURCES = ('tickflow', 'tencent')
ADJUSTMENTS = {'baostock':'前复权', 'tickflow':'前复权', 'tencent':'不复权'}


def require_update_source(source):
    if source not in SOURCES:
        raise ValueError('未知日 K 数据源')
    if source not in UPDATE_SOURCES:
        raise ValueError(RETIRED_MESSAGE)


def update_selection(saved, current='baostock'):
    """Sanitize old preferences without deleting archived source facts."""
    selected = [s for s in UPDATE_SOURCES if s in (saved or [current])]
    return selected or [current if current in UPDATE_SOURCES else 'baostock']


def source_file(store, name, source='baostock'):
    """BaoStock paths remain unchanged; fallback owns its mutable inventories."""
    if source not in SOURCES:
        raise ValueError('未知行情来源')
    return store.root / ((f'sources/{source}/' + name) if source in FALLBACK_SOURCES else name)


def directory_file(store, source):
    path = source_file(store, 'universe.csv', source)
    if source == 'tickflow' and not path.exists():
        # Read-only compatibility with PR 27. BaoStock's 0/1 trade status must
        # never be silently treated as TickFlow's current directory.
        import pandas as pd
        legacy = store.root/'universe.csv'
        if legacy.exists():
            frame = pd.read_csv(legacy, dtype=str)
            if 'tradeStatus' in frame and not frame.empty and frame.tradeStatus.eq('unknown').all():
                return legacy
    return path


def directory_date(store, source):
    key = 'universe_date:'+source if source in FALLBACK_SOURCES else 'universe_date'
    if source == 'tickflow' and directory_file(store, source) == store.root/'universe.csv':
        key = 'universe_date'  # legacy TickFlow directory only
    rows = store.rows('SELECT value FROM meta WHERE key=?', (key,))
    return rows[0]['value'] if rows else None


def calendar_file(store, source='baostock'):
    path = source_file(store, 'trading_calendar.json', source)
    # The exchange calendar is public, source-neutral evidence. Copy-on-write
    # migration may read the old calendar, but TickFlow never modifies it.
    if source in FALLBACK_SOURCES and not path.exists():
        return store.root/'trading_calendar.json'
    return path


def market_source(store):
    rows = store.rows("SELECT value FROM meta WHERE key='daily_market_source'")
    value=rows[0].get('value') if rows else None
    return value if value in SOURCES else 'baostock'


def set_market_source(store, source):
    if source not in SOURCES:
        raise ValueError('未知日 K 数据源')
    running = store.rows("SELECT id,kind,spec FROM jobs WHERE status IN ('queued','running')")
    if running:
        parents = {r['id'] for r in running if r['kind'] == 'premarket'}
        # Only the display preference may change: these jobs have explicit,
        # frozen source specs. Ordinary jobs keep the original switch guard.
        if not parents or any(r['id'] not in parents and not (
                r['kind'] in ('sync','scan') and
                json.loads(r['spec']).get('premarket_parent') in parents) for r in running):
            raise ValueError('请等待当前任务结束后切换数据源')
    else:
        isolate_tickflow_legacy(store)
    store.execute("INSERT INTO meta VALUES('daily_market_source',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (source,))


def isolate_tickflow_legacy(store):
    """Preserve PR 27 inventories before a BaoStock update overwrites globals."""
    legacy=directory_file(store,'tickflow')
    target=source_file(store,'universe.csv','tickflow')
    if legacy==store.root/'universe.csv' and legacy.exists() and not target.exists():
        day=directory_date(store,'tickflow')
        if day:
            content=legacy.read_bytes()
            store.write_artifact(str(target.relative_to(store.root)),content)
            store.write_artifact(f'sources/tickflow/directories/{day}.csv',content)
            store.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                          ('universe_date:tickflow',day))
            calendar=calendar_file(store,'tickflow')
            own=source_file(store,'trading_calendar.json','tickflow')
            if calendar.exists() and not own.exists():
                store.write_artifact(str(own.relative_to(store.root)),calendar.read_bytes())


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
