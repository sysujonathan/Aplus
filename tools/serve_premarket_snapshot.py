"""Serve one exported premarket snapshot directory, read-only.

最小只读获取方式：只响应快照目录内的 snapshot.json / index.html，
GET 之外一律拒绝，不列目录、不越过目录、不写任何文件。默认只绑本机，
供同一台机器上的客户端（或手机浏览器经局域网）取数验证链路；
对外分发请改用正式静态托管，不要长期跑这个脚本。

用法：
    python tools/serve_premarket_snapshot.py --dir <运行目录>/snapshots/2026-11-09 [--host 127.0.0.1] [--port 8000]
"""
import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ALLOWED = {
    "snapshot.json": "application/json; charset=utf-8",
    "index.html": "text/html; charset=utf-8",
}


class SnapshotHandler(BaseHTTPRequestHandler):
    root = Path(".")

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

    def do_GET(self):
        name = unquote(urlparse(self.path).path).lstrip("/")
        if name in ("", "index.html"):
            name = "index.html"
        if name not in ALLOWED:
            self._reject(404, "只提供 snapshot.json 与 index.html")
            return
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            self._reject(404, "文件不存在")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ALLOWED[name])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._reject(405, "只读服务不接受写入")

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读提供盘前快照目录")
    parser.add_argument("--dir", required=True, help="快照目录（须含 snapshot.json）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args(argv)
    root = Path(args.dir).resolve()
    if not (root / "snapshot.json").is_file():
        parser.error(f"{root} 下没有 snapshot.json，请先跑 tools/export_premarket_snapshot.py")
    SnapshotHandler.root = root
    server = ThreadingHTTPServer((args.host, args.port), SnapshotHandler)
    print(f"只读快照服务：http://{args.host}:{args.port}/ （目录 {root}，Ctrl+C 停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
