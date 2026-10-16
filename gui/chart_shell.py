"""Launch the production chart in an isolated native WebView window."""
from __future__ import annotations

import importlib.util
import json
import os
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def launch_chart_workspace(store, **context):
    """Open a separate process so Tk and WebView keep independent UI loops."""
    if not webview_available():
        return None
    boot = workspace_boot(store, context)
    session = store.root / "chart_sessions" / f"{uuid.uuid4().hex}.json"
    status = session.with_suffix(".status.json")
    _write_json(session, {"runtime_root": str(store.root), "boot": boot})
    executable = Path(sys.executable)
    command = [str(executable), "-m", "gui.chart_shell", str(session)]
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
    for name in ("prototype.css", "historical-bars-v2.js", "prototype.js",
                 "chart-coordinates.js", "prototype-interactions.js"):
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
    page = target / "index.html"
    page.write_text(html, encoding="utf-8")
    return page, target


def run_session(session_path):
    session = Path(session_path).resolve()
    status = session.with_suffix(".status.json")
    generated = None
    try:
        import webview
        from workbench.store import Store
        payload = json.loads(session.read_text(encoding="utf-8"))
        store = Store(payload["runtime_root"])
        bridge = ChartBridge(store, payload["boot"])
        page, generated = _materialize_page(session, payload["boot"])
        title = f"A++ 图表工作区 · {payload['boot']['name']} {payload['boot']['displaySymbol']}"
        window = webview.create_window(title, page.as_uri(), js_api=bridge, width=1500, height=940,
                                       min_size=(980, 620), background_color="#0b0b0b")
        close_guard = ChartCloseGuard(window, bridge)
        window.events.closing += close_guard.request
        window.events.loaded += lambda: _write_json(status, {"ready": True})
        webview.start(gui="edgechromium", debug=False, private_mode=True)
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
