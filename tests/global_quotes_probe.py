"""Synthetic native header/quote controls, never the installed user app."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(folder, manual=False):
    from gui.main_window import AplusMainWindow
    from workbench.service import Service
    from workbench.store import Store
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder/'synthetic-runtime')
    service = Service(store)
    window = AplusMainWindow(service=service, store=store, enable_tray=False)
    window.title('Aplus isolated global quotes acceptance')
    window.after_cancel(window.version_controller.startup_check_id)
    window.version_controller.startup_check_id = None
    try:
        window.geometry('1280x900')
        window.update()
        assert not hasattr(window, 'usage_button')
        assert window.auto_quote_button.master is window.section_nav
        assert 'other' not in window._pages
        window.auto_quote_button.invoke()
        assert store.holding_auto_quotes() is True
        assert 'other' not in window._pages  # No eager construction just to toggle quotes.
        for section in ('premarket', 'afterhours', 'strategy', 'other'):
            window._switch_section(section)
            window.update()
            assert window.auto_quote_button.winfo_ismapped()
            assert abs(window.auto_quote_button.winfo_rooty()-window.version_controller.button.winfo_rooty()) < 20
        page = window.trade_management
        assert page.auto_quote_var is window.auto_quote_var
        window.auto_quote_button.invoke()
        assert store.holding_auto_quotes() is False and page.auto_quote_var.get() is False
        window.auto_quote_button.invoke()
        assert store.holding_auto_quotes() is True and page.auto_quote_var.get() is True
        window._switch_section('premarket')
        window.update()
        if manual:
            (folder/'ready').write_text('synthetic', encoding='utf-8')
            deadline = time.monotonic()+180
            while time.monotonic()<deadline and not (folder/'finish').exists():
                window.update()
                time.sleep(.05)
        (folder/'result.json').write_text(json.dumps(dict(global_header=True, shared_variable=True,
            lazy_page=True, persists=True, help_button_removed=True, synthetic=True)), encoding='utf-8')
    finally:
        window.destroy()
        service.pool.shutdown(wait=True)


if __name__ == '__main__':
    run(sys.argv[1], '--manual' in sys.argv)
