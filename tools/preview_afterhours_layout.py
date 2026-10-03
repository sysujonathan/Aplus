"""Capture only our own Tk window against an isolated real-data acceptance home."""
import argparse
import ctypes
from pathlib import Path

from PIL import ImageGrab

from gui.main_window import AplusMainWindow
from workbench.h2_replay_service import load_receipt
from workbench.store import Store, dumps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    home, output = Path(args.home).resolve(), Path(args.output).resolve()
    if not (home / 'benchmark.json').is_file() and not (home / 'acceptance.json').is_file():
        raise ValueError('仅使用独立工程验收数据仓，不打开日常运行目录')
    output.mkdir(parents=True, exist_ok=True)
    store = Store(home)
    business = ('observations', 'plans', 'plan_history', 'watchlist', 'accounts',
                'executions', 'positions', 'position_fills', 'closed_trades')
    before = {table: store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'] for table in business}
    job = store.rows("SELECT id FROM jobs WHERE kind='backtest' AND status='completed' "
                     "ORDER BY created DESC,rowid DESC LIMIT 1")[0]['id']
    row, report, records = load_receipt(store, job)
    window = AplusMainWindow(store=store)
    captured = []
    try:
        window._switch_section('afterhours')
        page = window.afterhours
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        def capture(name):
            window.update()
            handle = user32.GetParent(window.winfo_id())
            image = ImageGrab.grab(window=handle)
            image.save(output / name)
            captured.append(name)
        for size in ('1600x1000', '1280x760', '2560x1440'):
            window.geometry(size + '+0+0')
            page._show_report(row, report, records)
            capture(f'results-{size}.png')
            if records:
                record = next((r for r in records if r['filled']), records[0])
                page._open(record['id'])
                window.update()
                page._fit()
                assert page._photo.width() == page.chart.winfo_width()
                assert page._photo.height() == page.chart.winfo_height()
                capture(f'replay-{size}.png')
                page._step_event(len(record['events']) - 1)
                window.update()
                page._fit()
                capture(f'replay-exit-{size}.png')
                page._back()
        after = {table: store.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'] for table in business}
        assert before == after
        evidence = dict(real_data=report['real_data'], opportunities=len(records), captures=captured,
                        business_tables_unchanged=True, note='实际 Windows 窗口与真实行情工程验收；非长期交易员验收。')
        (output / 'layout-acceptance.json').write_text(dumps(evidence), encoding='utf-8')
        print(dumps(evidence))
    finally:
        window.destroy()


if __name__ == '__main__':
    main()
