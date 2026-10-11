"""Independent dated broker reconciliation, not a live portfolio valuation."""
from datetime import date
import json
import math

from .closed_chart import _ReadOnlySnapshots
from .market import code_of, load_dataset

PREFIX = 'account_reconciliation:'


def validate_reference(reference):
    if not isinstance(reference, dict) or reference.get('version') != 1:
        raise ValueError('账户对账基准版本无效')
    day = reference.get('asof')
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('账户对账日期无效')
    value = reference.get('broker_total_assets')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('券商对账资产必须大于零')
    prices = reference.get('prices')
    if not isinstance(prices, dict):
        raise ValueError('缺少同日持仓估值依据')
    for code, quote in prices.items():
        if code_of(code) != code or not isinstance(quote, dict):
            raise ValueError('对账持仓身份无效')
        price = quote.get('price')
        if (quote.get('date') != day or quote.get('adjusted') is not False or
                isinstance(price, bool) or not isinstance(price, (int, float)) or
                not math.isfinite(price) or price <= 0):
            raise ValueError('对账价格必须为基准日的不复权价格')
    return reference


def books_at(store, account_id, day):
    fills = store.rows('SELECT f.*,p.code FROM position_fills f JOIN positions p ON p.id=f.position_id '
                       'WHERE p.account_id=? AND substr(f.trade_date,1,10)<=? '
                       'ORDER BY f.trade_date,f.created,f.id', (account_id, day))
    books = {}
    for fill in fills:
        book = books.setdefault(fill['position_id'], dict(code=fill['code'], quantity=0, invested=0.0))
        sign = 1 if fill['side'] == 'BUY' else -1
        book['quantity'] += sign * int(fill['quantity'])
        book['invested'] += sign * float(fill['price']) * int(fill['quantity']) + float(fill['fees'] or 0)
        if book['quantity'] < 0:
            raise ValueError('基准日前卖出数量超过买入，需核对成交记录')
    return [book for book in books.values() if book['quantity'] > 0]


def capture_reference(store, account_id, broker_total_assets, asof, price_map=None, previous_reference=None):
    """Capture independent raw prices once; never derives them from initial funds."""
    date.fromisoformat(asof)
    books = books_at(store, account_id, asof) if account_id else []
    prices = {}
    if previous_reference is not None:
        validate_reference(previous_reference)
        if previous_reference['asof'] != asof:
            raise ValueError('旧估值依据不属于所选日期')
        prices.update({code:dict(quote) for code,quote in previous_reference['prices'].items()})
    reader = _ReadOnlySnapshots(store)
    for code in sorted({book['code'] for book in books}):
        if code in prices:
            continue
        if price_map is not None:
            quote = price_map.get(code)
            if quote:
                prices[code] = dict(quote)
            continue
        # This is a broker-account evidence copy, not a cross-source strategy input.
        rows = store.rows("SELECT * FROM datasets WHERE code=? AND timeframe='daily' "
                          "AND adjustment IN ('不复权','none','3') AND start<=? AND end>=? "
                          "AND source IN ('baostock','tickflow','tencent') "
                          "ORDER BY CASE source WHEN 'tencent' THEN 0 ELSE 1 END,created DESC,rowid DESC",
                          (code, asof, asof))
        for row in rows:
            try:
                frame, record = load_dataset(reader, row['id'])
                match = frame[frame.date == asof]
                if match.empty:
                    continue
                prices[code] = dict(price=float(match.iloc[-1].close), date=asof, adjusted=False,
                                    source=record['source'], dataset_id=record['id'], sha256=record['sha256'])
                break
            except (OSError, ValueError):
                continue
    missing = {book['code'] for book in books} - prices.keys()
    if missing:
        raise ValueError('缺少基准日不复权持仓价格：' + '、'.join(sorted(missing)))
    return validate_reference(dict(version=1, asof=asof, broker_total_assets=broker_total_assets, prices=prices))


def reconcile_reference(store, account, reference):
    reference = validate_reference(reference)
    if reference['broker_total_assets'] != account.get('current_total_assets'):
        raise ValueError('券商资产已变更，需重新确认对账基准')
    day = reference['asof']
    books = books_at(store, account['id'], day)
    floating = 0.0
    for book in books:
        quote = reference['prices'].get(book['code'])
        if quote is None:
            raise ValueError('补录的基准日前持仓缺少同日估值依据')
        floating += quote['price'] * book['quantity'] - book['invested']
    closed = sum(float(r['pnl']) for r in store.rows(
        'SELECT pnl FROM closed_trades WHERE account_id=? AND substr(close_date,1,10)<=?',
        (account['id'], day)))
    adjustments = sum(float(r['amount']) for r in store.rows(
        'SELECT amount FROM cash_flows WHERE account_id=? AND substr(flow_date,1,10)<=?',
        (account['id'], day)))
    implied = reference['broker_total_assets'] - closed - floating - adjustments
    return dict(implied_initial_equity=implied, reconciliation=implied-account['initial_equity'],
                reconciliation_asof=day, reconciliation_reason='', reconciliation_reference=reference,
                reconciliation_components=dict(broker_total_assets=reference['broker_total_assets'],
                    closed_pnl=closed, floating_pnl=floating, cash_adjustments=adjustments))


def reconcile_account(store, account):
    """Recalculate corrections to the dated ledger; ignore all newer quotes/fills."""
    rows = (store.rows('SELECT value FROM meta WHERE key=?', (PREFIX + account['id'],))
            if account.get('accounting_mode') == 'history' else [])
    if not rows:
        return dict(implied_initial_equity=None, reconciliation=None,
                    reconciliation_asof=None, reconciliation_reason='未确认券商快照日期及同日估值')
    try:
        return reconcile_reference(store, account, json.loads(rows[0]['value']))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(implied_initial_equity=None, reconciliation=None,
                    reconciliation_asof=None, reconciliation_reason=str(exc))
