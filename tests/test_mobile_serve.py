"""移动端盘前服务（tools/serve_mobile.py）的只读契约测试。

起一个临时端口的真实 HTTP 服务，验证：外壳可达、manifest/sw 类型对、
data/ 快照与图表可读、目录穿越与外壳外文件一律 404、写入一律 405。
"""
import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_server_module():
    spec = importlib.util.spec_from_file_location(
        "serve_mobile", REPO / "tools" / "serve_mobile.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def server(tmp_path):
    module = _load_server_module()
    package = tmp_path / "snapshot-package"
    (package / "charts").mkdir(parents=True)
    (package / "snapshot.json").write_text(
        json.dumps({"asof": "2026-11-10", "hits": []}), encoding="utf-8"
    )
    (package / "charts" / "obs-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    module.MobileHandler.data_dir = package.resolve()
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), module.MobileHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type") or "", body


def test_shell_and_manifest_served(server):
    status, ctype, body = _get(server, "/")
    assert status == 200 and "text/html" in ctype and b"Aplus" in body
    status, ctype, _ = _get(server, "/manifest.webmanifest")
    assert status == 200 and "manifest+json" in ctype
    status, ctype, _ = _get(server, "/sw.js")
    assert status == 200 and "javascript" in ctype
    status, ctype, _ = _get(server, "/icon-192.png")
    assert status == 200 and ctype == "image/png"


def test_snapshot_and_chart_served(server):
    status, ctype, body = _get(server, "/data/snapshot.json")
    assert status == 200 and "application/json" in ctype
    assert json.loads(body)["asof"] == "2026-11-10"
    status, ctype, body = _get(server, "/data/charts/obs-1.png")
    assert status == 200 and ctype == "image/png" and body.startswith(b"\x89PNG")


def test_traversal_and_unknown_rejected(server):
    assert _get(server, "/data/../snapshot.json")[0] in (400, 404)
    assert _get(server, "/data/%2e%2e%2fsecret.json")[0] == 404
    assert _get(server, "/workbench/snapshot.py")[0] == 404
    assert _get(server, "/data/missing.png")[0] == 404


def test_writes_rejected(server):
    assert _get(server, "/data/snapshot.json", method="POST")[0] == 405
    assert _get(server, "/", method="PUT")[0] == 405
