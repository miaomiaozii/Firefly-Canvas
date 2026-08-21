"""Run the single-image pending placeholder geometry contract in real headless Chrome.

Regression test for the 0b19d51 change: `.image-node.node-pending` gained a 16px
border-box padding (the node padding is the intended outer margin, shared with the
completed group-node look). The batch placeholder branch was switched to
width/height:100%, but the single-image placeholder branch still hard-codes the
inline layout width/height (= node outer size), so its placeholder sits on the
node frame instead of inside the 16px padding.

The test server ships the real smart-canvas page/JS, injects the browser client
script, and serves a fresh empty ProjectStore. The client script then injects a
single-image pending node into the page state, calls render(), and measures the
placeholder rectangle against the node rectangle.
"""

from __future__ import annotations

import http.server
import json
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectNotFound, ProjectStore, ProjectStoreError, RevisionConflict
from tests.native_browser import NativeBrowser

CLIENT_SCRIPT = ROOT / "tests" / "single_image_pending_placeholder_browser_client.js"
VIEWPORTS = [(1424, 805), (1008, 673)]


def find_browser() -> Path:
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    browser = next((candidate for candidate in candidates if candidate.is_file()), None)
    if browser is None:
        raise RuntimeError("Chrome or Edge is required for the real browser contract")
    return browser


class SinglePendingPlaceholderServer:
    def __init__(self, store: ProjectStore):
        self.store = store
        self.results: list[dict] = []
        self.condition = threading.Condition()
        owner = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)

            def log_message(self, _format, *_args):
                return

            def send_bytes(self, payload: bytes, content_type: str, status: int = 200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass

            def send_json(self, value, status: int = 200):
                self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

            def api_error(self, message: str, status: int = 400):
                self.send_json({"detail": message}, status)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/static/smart-canvas.html":
                    source = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
                    injection = '<script src="/tests/single_image_pending_placeholder_browser_client.js"></script>'
                    self.send_bytes(source.replace("</body>", f"{injection}</body>").encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path == "/tests/single_image_pending_placeholder_browser_client.js":
                    self.send_bytes(CLIENT_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
                    return
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                if parts == ["api", "config"]:
                    self.send_json({
                        "api_providers": [],
                        "chat_models": [],
                        "image_models": [],
                        "video_models": [],
                    })
                    return
                if parts == ["api", "prompt-libraries"]:
                    self.send_json({"library": {"libraries": []}})
                    return
                if parts == ["api", "smart-canvas", "prompt-templates"]:
                    self.send_json({"templates": []})
                    return
                if parts == ["api", "asset-library"]:
                    self.send_json({"libraries": [], "categories": []})
                    return
                if parts == ["api", "local-assets"]:
                    self.send_json({"items": [], "tree": {"id": "__root__", "name": "全部上传", "items": [], "children": []}})
                    return
                if parts == ["api", "global-settings", "preferences"]:
                    self.send_json({"preferences": {"theme": "light", "language": "zh"}})
                    return
                if parts[:2] == ["api", "canvases"] and len(parts) == 3:
                    try:
                        self.send_json({"canvas": owner.store.canvas(parts[2])})
                    except ProjectNotFound as exc:
                        self.api_error(str(exc), 404)
                    return
                super().do_GET()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/__single_pending_placeholder_results":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                    with owner.condition:
                        owner.results.append(payload)
                        owner.condition.notify_all()
                    self.send_json({"ok": True})
                    return
                self.api_error("not found", 404)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="single-pending-placeholder-browser", daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self):
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def wait_for_result(self, start_count: int, timeout: float = 60):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.results) <= start_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("single pending placeholder validation timed out")
                self.condition.wait(remaining)
            return self.results[start_count]


def launch(browser: Path, server: SinglePendingPlaceholderServer, viewport: tuple[int, int], project_id: str):
    before = len(server.results)
    query = urllib.parse.urlencode({"pendingNodeId": "h1-single-pending"})
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    native = NativeBrowser.launch(browser)
    try:
        native.set_environment(viewport)
        native.navigate(url)
        result = server.wait_for_result(before)
        if not result.get("pass"):
            raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        native.close()


def main():
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="firefly-single-pending-placeholder-") as root:
        results = []
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            project = store.create_project("单图占位回归项目")
            store.flush(project["id"])
            server = SinglePendingPlaceholderServer(store)
            server.start()
            try:
                results.append(launch(browser, server, viewport, project["id"]))
            finally:
                server.close()
                store.close()
    summary = {
        "issue": "single-image-pending-placeholder-padding",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
