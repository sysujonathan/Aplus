"""Serve the mobile premarket PWA plus one snapshot package, read-only.

三层架构第三层的最小服务端：把 `mobile/` 应用外壳挂在 `/`，把一个已导出
的盘前快照包（snapshot.json + 图表文件）挂在 `/data/`，手机浏览器打开
即看昨晚的匹配结果。只响应 GET，不列目录、不越目录、不写任何文件。

用法：
    python tools/serve_mobile.py --dir <快照包目录> [--host 127.0.0.1] [--port 8765]

不带 --dir 时自动取运行目录 snapshots/ 下日期最新的快照包。

注意：PWA 的 service worker 与“添加到主屏幕”需要安全上下文（HTTPS 或
localhost）。局域网 http 地址只能当普通网页打开；正式装成 APP 请把
mobile/ 外壳与快照包放到 HTTPS 静态托管，数据通道见架构评估结论。
"""
import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL_DIR = REPO_ROOT / "mobile"

SHELL_TYPES = {
    "": "text/html; charset=utf-8",
    "index.html": "text/html; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    "sw.js": "text/javascript; charset=utf-8",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
}
DATA_TYPES = {
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".html": "text/html; charset=utf-8",
}


def latest_snapshot_dir():
    from workbench.snapshot import resolve_runtime_root

    snapshots = resolve_runtime_root(None) / "snapshots"
    candidates = sorted(
        (p for p in snapshots.glob("20??-??-??") if (p / "snapshot.json").is_file()),
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"运行目录 {snapshots} 下没有已导出的快照包，先跑导出工具")
    return candidates[0]


class MobileHandler(BaseHTTPRequestHandler):
    shell_dir = SHELL_DIR
    data_dir = Path(".")

    def log_message(self, *args):  # 只保留错误日志，不刷屏
        if args and "404" in str(args[1]):
            super().log_message(*args)

    def _reject(self, code, message):
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type, cache_control):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if path.name == "sw.js":
            self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        name = unquote(urlparse(self.path).path).lstrip("/")
        if name.startswith("data/"):
            rel = name[len("data/"):]
            path = (self.data_dir / rel).resolve()
            ctype = DATA_TYPES.get(path.suffix.lower())
            if (
                ctype is None
                or not path.is_relative_to(self.data_dir.resolve())
                or not path.is_file()
            ):
                self._reject(404, "快照包内没有这个文件")
                return
            self._send_file(path, ctype, "no-store")
            return
        if name not in SHELL_TYPES:
            self._reject(404, "只有应用外壳与 data/ 快照包可读")
            return
        path = self.shell_dir / (name or "index.html")
        if not path.is_file():
            self._reject(404, "应用外壳文件缺失")
            return
        cache = "no-cache" if name in ("", "index.html", "sw.js") else "max-age=86400"
        self._send_file(path, SHELL_TYPES[name], cache)

    def do_POST(self):
        self._reject(405, "只读服务不接受写入")

    do_PUT = do_POST
    do_DELETE = do_POST


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读服务：移动端盘前 PWA + 快照包")
    parser.add_argument("--dir", type=Path, default=None, help="快照包目录（默认取最新）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    data_dir = (args.dir or latest_snapshot_dir()).resolve()
    if not (data_dir / "snapshot.json").is_file():
        raise SystemExit(f"{data_dir} 里没有 snapshot.json，不是快照包目录")
    MobileHandler.data_dir = data_dir
    server = ThreadingHTTPServer((args.host, args.port), MobileHandler)
    print(f"移动端盘前：http://{args.host}:{args.port}/  快照包：{data_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
