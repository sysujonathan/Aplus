"""Launch the production chart in an isolated native WebView window."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from .chart_bridge import ChartBridge, workspace_boot


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "prototypes" / "tv-workspace"


class ChartCloseGuard:
    """Keep the page alive until its queued Store writes have acknowledged."""

    def __init__(self, window, bridge):
        self.window = window
        self.allow_close = False
        self.flushing = False
        self._lock = threading.Lock()
        bridge._close_window = self.finish
        bridge._cancel_close = self.failed

    def request(self):
        with self._lock:
            if self.allow_close:
                return True
            if not self.flushing:
                self.flushing = True
                threading.Thread(target=self._flush, daemon=True).start()
        return False

    def _flush(self):
        try:
            installed = self.window.evaluate_js(
                "typeof window.aplusFlushAndClose === 'function' ? "
                "(window.aplusFlushAndClose(), true) : false"
            )
            # No script means the chart has not accepted edits yet.
            if not installed:
                self.finish()
        except Exception:
            self.failed()

    def failed(self):
        self.cancel()
        if self.window.create_confirmation_dialog(
            "图表尚未确认保存", "最后的修改尚未确认保存。是否放弃未保存的修改并关闭？取消将保留窗口。"
        ):
            self.finish()

    def finish(self):
        with self._lock:
            self.allow_close = True
        self.window.destroy()

    def cancel(self):
        with self._lock:
            self.flushing = False


def webview_available():
    return importlib.util.find_spec("webview") is not None


def _write_json(path, value):
    from .chart_embed import atomic_json
    atomic_json(path, value)


def launch_chart_workspace(store, **context):
    """Open a separate process so Tk and WebView keep independent UI loops."""
    if not webview_available():
        return None
    boot = workspace_boot(store, context)
    session = store.root / "chart_sessions" / f"{uuid.uuid4().hex}.json"
    status = session.with_suffix(".status.json")
    _write_json(session, {"runtime_root": str(store.root), "boot": boot})
    from workbench.process_entry import chart_command
    command = chart_command(session)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, cwd=ROOT, creationflags=flags)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if status.exists():
                result = json.loads(status.read_text(encoding="utf-8"))
                status.unlink(missing_ok=True)
                if result.get("ready"):
                    return process
                raise RuntimeError(result.get("error") or "本机 WebView 启动失败")
            if process.poll() is not None:
                raise RuntimeError("本机 WebView 在显示图表前已经退出")
            time.sleep(.05)
        return process
    except Exception:
        session.unlink(missing_ok=True)
        status.unlink(missing_ok=True)
        raise


def _materialize_page(session, boot):
    target = session.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)
    if boot is None:
        page = target / 'index.html'
        page.write_text('''<!doctype html><meta charset="utf-8"><style>
body{background:#0b0b0b;color:#a9abb3;font:15px "Segoe UI";display:grid;place-items:center;height:95vh}
</style><p>从候选或自选列表选择标的，在此图上继续分析</p><script>
let switching=false;
window.aplusSwitchContext=async()=>{if(switching)return;switching=true;try{
 const r=await window.pywebview.api.activate_context();location.replace(location.pathname+'?chart='+r.token);
}catch(e){switching=false;await window.pywebview.api.cancel_context_switch(String(e));}};
window.aplusFlushAndClose=()=>window.pywebview.api.finish_close();</script>''', encoding='utf-8')
        return page, target
    for name in ("prototype.css", "historical-bars-v2.js", "prototype.js",
                 "chart-coordinates.js", "chart-indicators.js", "chart-position.js", "drawing-settings.js", "drawing-quickbar.js",
                 "prototype-interactions.js", "production-controls.js", "chart-palette.js"):
        shutil.copyfile(ASSETS / name, target / name)
    chart_script = (target / "prototype.js").read_text(encoding="utf-8")
    chart_script = chart_script.replace("const bars = [", "const demoBars = [", 1)
    chart_script = chart_script.replace(
        "bars.unshift(...window.prototypeEarlierBars);",
        "const bars = window.APLUS_CHART_BOOT?.bars?.length ? "
        "window.APLUS_CHART_BOOT.bars : demoBars;\n"
        "if (!window.APLUS_CHART_BOOT?.production) bars.unshift(...window.prototypeEarlierBars);",
        1,
    )
    (target / "prototype.js").write_text(chart_script, encoding="utf-8")
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    encoded = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(
        "<script>window.tvPrototypeV2 = true;</script>",
        f"<script>window.tvPrototypeV2 = true;window.APLUS_CHART_BOOT={encoded};</script>",
    )
    html = html.replace("A+ 图表工作台高保真原型", "A++ 图表工作区")
    if boot.get('embedded'):
        html = html.replace('<body>', '<body class="embedded">')
    # The same slot reloads after switching symbols. Give changed assets new
    # URLs so WebView cannot combine cached old scripts with a fresh document.
    build = hashlib.sha256(b''.join(path.read_bytes() for path in sorted(target.iterdir())
                                   if path.suffix in {'.js', '.css'})).hexdigest()[:16]
    html = re.sub(r'((?:src|href)=")([^"?]+\.(?:js|css))(?:\?[^"}]+)?"',
                  lambda match: f'{match[1]}{match[2]}?build={build}"', html)
    page = target / "index.html"
    page.write_text(html, encoding="utf-8")
    return page, target


def run_session(session_path):
    session = Path(session_path).resolve()
    status = session.with_suffix(".status.json")
    generated = None
    try:
        payload = json.loads(session.read_text(encoding="utf-8"))
        if payload.get('parent_hwnd'):
            from .chart_embed import adopt_parent_dpi
            adopt_parent_dpi(payload['parent_hwnd'])
        import webview
        from workbench.store import Store
        store = Store(payload["runtime_root"])
        embedded = bool(payload.get('parent_hwnd'))
        if embedded:
            from .chart_embed import ChartSessionBridge, run_embedded_transport
            bridge = ChartSessionBridge(store, payload['boot'], lambda boot: _materialize_page(session, boot))
            bridge.active_token = payload.get('token', 0)
        else:
            bridge = ChartBridge(store, payload["boot"])
        page, generated = _materialize_page(session, bridge.bootstrap())
        title = 'A++ 主图' if embedded else f"A++ 图表工作区 · {payload['boot']['name']} {payload['boot']['displaySymbol']}"
        window = webview.create_window(title, page.as_uri(), js_api=bridge, width=1500, height=940,
                                       min_size=(200, 200) if embedded else (980, 620),
                                       frameless=embedded, easy_drag=False, hidden=embedded,
                                       background_color="#0b0b0b")
        close_guard = ChartCloseGuard(window, bridge)
        from .chart_snapshot import copy_chart_png
        bridge._copy_snapshot = lambda data: copy_chart_png(data, int(window.native.Handle.ToInt64()))
        if embedded:
            def cancelled():
                close_guard.failed()
                if not close_guard.allow_close:
                    bridge.close_requested = False
                    _write_json(status, {'ready': True, 'closeCancelled': True})
            bridge._cancel_close = cancelled
        window.events.closing += close_guard.request
        def loaded():
            if embedded:
                bridge.busy = False
            _write_json(status, {'ready': True, 'token': getattr(bridge, 'active_token', 0)})
            if embedded and bridge.close_requested:
                # A close requested during page replacement must flush the new
                # page too; the old document was already acknowledged.
                close_guard.cancel()
                window.destroy()
        window.events.loaded += loaded
        def start_embedded():
            try:
                if not window.events.loaded.wait(30):
                    raise TimeoutError('交互图表页面加载超时；未写入或删除分析对象')
                # Parent before showing, so no detached window flashes on stock selection.
                from .chart_embed import NativeChild
                native = NativeChild(int(window.native.Handle.ToInt64()), payload['parent_hwnd'])
                window.show()
                run_embedded_transport(window, bridge, session, payload['parent_hwnd'], native)
            except Exception as exc:
                _write_json(status, {'ready': False, 'error': str(exc)})
        webview.start(start_embedded if embedded else None, gui="edgechromium", debug=False, private_mode=True)
    except Exception as exc:
        _write_json(status, {"ready": False, "error": str(exc)})
        raise
    finally:
        if generated is not None:
            shutil.rmtree(generated, ignore_errors=True)
        session.unlink(missing_ok=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: python -m gui.chart_shell <session.json>")
    run_session(args[0])


if __name__ == "__main__":
    main()
