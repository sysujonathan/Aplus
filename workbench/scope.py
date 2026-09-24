"""Shared, persistent board selection for market updates and daily scans."""
import json

from .market import BOARDS, board_of, latest_datasets


def selected_boards(store):
    saved = store.rows("SELECT value FROM meta WHERE key='market_boards'")
    if saved:
        return [b for b in json.loads(saved[0]['value']) if b in BOARDS]
    for row in store.rows("SELECT spec FROM jobs WHERE kind='sync' ORDER BY created DESC,rowid DESC"):
        spec = json.loads(row['spec'])
        if 'boards' in spec:
            return [b for b in spec['boards'] if b in BOARDS]
    return list(BOARDS[:3])


def save_boards(store, boards):
    if selected_boards(store) == boards and store.rows("SELECT value FROM meta WHERE key='market_boards'"):
        return
    store.execute("INSERT INTO meta VALUES('market_boards',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(boards,ensure_ascii=False),))


def scan_datasets(store, source):
    boards = selected_boards(store)
    return [row for row in latest_datasets(store,source)
            if row['timeframe']=='daily' and board_of(row['code']) in boards]
