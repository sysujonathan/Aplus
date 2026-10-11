"""Closed days must not turn a current Friday cache into a false scope gap."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_workbench import store
from workbench.readiness import expected_day, audit_scope
from workbench.sources import SOURCES, source_file


def calendar(store, source, end='2026-10-09', days=None):
    payload = dict(start='2026-09-28', end=end,
                   trading_days=days if days is not None else
                   ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08', '2026-10-09'])
    path = source_file(store, 'trading_calendar.json', source)
    store.write_artifact(str(path.relative_to(store.root)), json.dumps(payload).encode())
    return path


@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize('asof', ['2026-10-09', '2026-10-10', '2026-10-11'])
def test_current_friday_resolves_through_closed_weekend_without_writes(store, source, asof):
    path = calendar(store, source)
    before = path.read_bytes()
    events = store.rows('SELECT count(*) n FROM events')[0]['n']
    assert expected_day(store, asof, source) == '2026-10-09'
    assert path.read_bytes() == before
    assert store.rows('SELECT count(*) n FROM events')[0]['n'] == events


@pytest.mark.parametrize('source', SOURCES)
def test_published_holiday_resolves_but_first_open_day_still_blocks(store, source):
    calendar(store, source, end='2026-09-30', days=['2026-09-28', '2026-09-29', '2026-09-30'])
    assert expected_day(store, '2026-10-07', source) == '2026-09-30'
    with pytest.raises(ValueError, match='交易日历尚未覆盖'):
        expected_day(store, '2026-10-08', source)


@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize('asof', ['2026-10-09', '2026-10-11', '2026-10-12'])
def test_weekend_never_hides_missing_friday_or_monday(store, source, asof):
    calendar(store, source, end='2026-10-08', days=['2026-09-30', '2026-10-08'])
    with pytest.raises(ValueError, match='交易日历尚未覆盖'):
        expected_day(store, asof, source)


@pytest.mark.parametrize('source', SOURCES)
def test_unknown_year_and_before_start_fail_closed(store, source):
    calendar(store, source)
    for day in ('2026-09-27', '2030-01-05'):
        with pytest.raises(ValueError, match='交易日历尚未覆盖'):
            expected_day(store, day, source)


@pytest.mark.parametrize('source', SOURCES)
def test_scope_accepts_current_weekend_but_rejects_stale_directory(store, source):
    calendar(store, source)
    directory = source_file(store, 'universe.csv', source)
    store.write_artifact(str(directory.relative_to(store.root)),
                         b'code,code_name,tradeStatus\nsh.600000,test,0\n')
    key = 'universe_date' if source == 'baostock' else 'universe_date:' + source
    store.execute('INSERT INTO meta VALUES(?,?)', (key, '2026-10-09'))
    audit = audit_scope(store, ['沪深主板'], '2026-10-11', source=source, timeframe='daily')
    assert audit['scope_valid'] and audit['expected_day'] == '2026-10-09'
    assert audit['suspended'] == 1 and not audit['gaps']
    store.execute('UPDATE meta SET value=? WHERE key=?', ('2026-10-08', key))
    stale = audit_scope(store, ['沪深主板'], '2026-10-11', source=source, timeframe='daily')
    assert not stale['scope_valid'] and not stale['scan_allowed']
    assert '股票目录日期' in stale['gaps'][0]['error']


@pytest.mark.parametrize('source', SOURCES)
def test_service_weekend_scan_uses_only_eligible_prices(store, source, monkeypatch):
    import pandas as pd
    from tests.test_workbench import wait_for
    from workbench.market import save_dataset
    from workbench.service import Service
    from workbench.exchange_calendar import is_trading_day
    from workbench.sources import ADJUSTMENTS
    days = [d.strftime('%Y-%m-%d') for d in pd.date_range('2026-01-01', '2026-10-09')
            if is_trading_day(d.date())]
    path = source_file(store, 'trading_calendar.json', source)
    payload = dict(start='2026-01-01', end='2026-10-09', trading_days=days)
    store.write_artifact(str(path.relative_to(store.root)), json.dumps(payload).encode())
    path = source_file(store, 'universe.csv', source)
    store.write_artifact(str(path.relative_to(store.root)),
        b'code,code_name,tradeStatus\nsh.600000,ready,1\nsh.600001,missing,1\n')
    key = 'universe_date' if source == 'baostock' else 'universe_date:' + source
    store.execute('INSERT INTO meta VALUES(?,?)', (key, '2026-10-09'))
    bars = pd.DataFrame(dict(date=days, open=10., high=11., low=9., close=10., volume=1000.))
    did = save_dataset(store, 'sh.600000', bars, source, ADJUSTMENTS[source])
    calls = []
    monkeypatch.setattr('workbench.service.completed_date', lambda: '2026-10-10')
    monkeypatch.setattr('workbench.service.signal_at_end',
                        lambda *args, **kw: calls.append(args[1].date.iloc[-1]) or None)
    service = Service(store)
    try:
        job = service.submit('scan', dict(source=source, boards=['沪深主板'], datasets=[did],
                             strategies=['MTR_MASTER'], timeframes=['daily'], asof='2026-10-10'))
        result = wait_for(store, job)
        report = json.loads(result['result'])
        assert result['status'] == 'partial', result['message']
        assert report['coverage']['expected_day'] == '2026-10-09'
        assert report['success'] == 1 and calls == ['2026-10-09']
        assert len(report['coverage']['gaps']) == 1
        assert report['coverage']['gaps'][0]['code'] == 'sh.600001'
    finally:
        service.pool.shutdown()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native toolbar')
def test_native_gap_button_uses_selected_day_and_does_not_count_scope_error(tmp_path):
    result = subprocess.run([sys.executable, '-c',
        'from tests.test_closed_day_readiness import check_toolbar; check_toolbar()', str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='utf-8'), timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr


def check_toolbar():
    import ttkbootstrap as ttk
    from unittest.mock import patch
    from gui.toolbar import ToolBar
    from workbench.store import Store
    from workbench.sources import set_market_source
    from workbench.scope import save_boards
    root = ttk.Window(themename='darkly')
    root.withdraw()
    st = Store(Path(sys.argv[1]) / 'toolbar')
    save_boards(st, ['沪深主板'])
    bar = None
    try:
        for source in SOURCES:
            calendar(st, source)
            path = source_file(st, 'universe.csv', source)
            st.write_artifact(str(path.relative_to(st.root)),
                              b'code,code_name,tradeStatus\nsh.600000,test,0\n')
            key = 'universe_date' if source == 'baostock' else 'universe_date:' + source
            st.execute('INSERT INTO meta VALUES(?,?)', (key, '2026-10-09'))
            if source != 'baostock':
                audit = audit_scope(st, ['沪深主板'], '2026-10-09', source=source, timeframe='daily')
                receipt = dict(source=source, boards=['沪深主板'], asof='2026-10-09',
                    timeframe='daily', periods={'daily': audit}, scan=audit, repair_codes=[],
                    unknown_history=[], current_halts=[], confirmed_history=[], request_failures=[])
                st.execute('INSERT INTO meta VALUES(?,?)', (source + '_integrity', json.dumps(receipt)))
        with patch('workbench.market.completed_date', return_value='2026-10-10'):
            bar = ToolBar(root, store=st)
            for source in SOURCES:
                set_market_source(st, source)
                for var, value in zip((bar.year_var, bar.month_var, bar.day_var), ('2026', '10', '09')):
                    var.set(value)
                bar._fire_date()
                assert bar._tickflow_audit['expected_day'] == '2026-10-09'
                assert bar.btn_integrity.cget('text') == '缺口分类', (source, bar._tickflow_audit, bar.btn_integrity.cget('text'))
                with patch('gui.tickflow_integrity.show_integrity') as show:
                    bar.btn_integrity.invoke()
                    assert show.call_count == 1
                    assert show.call_args.args[1]['asof'] == '2026-10-09'
                    with patch.object(bar, '_repair_tickflow', return_value=True) as repair:
                        assert show.call_args.args[2](['sh.600000'], 'daily', False)
                        assert repair.call_count == 1
                # An explicit historical selection must not be replaced by today.
                for var, value in zip((bar.year_var, bar.month_var, bar.day_var), ('2026', '09', '30')):
                    var.set(value)
                with patch('workbench.gap_review.review_receipt', side_effect=ValueError('historical')) as review:
                    bar.btn_integrity.invoke()
                    assert review.call_args.args[3] == '2026-09-30'
                with patch('workbench.gap_review.review_receipt', return_value={'asof': '2026-09-30'}), \
                     patch('gui.tickflow_integrity.show_integrity') as show, \
                     patch.object(bar, '_repair_tickflow') as repair:
                    bar.btn_integrity.invoke()
                    with pytest.raises(ValueError, match='历史日期'):
                        show.call_args.args[2](['sh.600000'], 'daily', False)
                    assert repair.call_count == 0
                # Broad date filters use the current completed trading day.
                bar.day_var.set('全部')
                bar._fire_date()
                assert bar._tickflow_audit['expected_day'] == '2026-10-09'
                with patch('workbench.market.completed_date', return_value='2026-10-12'):
                    bar._load_market_status()
                    assert not bar._tickflow_audit['scope_valid']
                    assert bar.btn_integrity.cget('text') == '范围待核验'
                    with patch('gui.tickflow_integrity.show_integrity') as show:
                        bar.btn_integrity.invoke()
                        assert show.call_count == 0
                        assert '交易日历尚未覆盖' in bar._status.get()
            assert st.rows('SELECT count(*) n FROM jobs')[0]['n'] == 0
            assert st.rows('SELECT count(*) n FROM datasets')[0]['n'] == 0
    finally:
        root.destroy()
