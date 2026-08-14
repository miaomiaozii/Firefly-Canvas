"""Run the generated-history timestamp contract in a real headless Chrome session.

The server serves the shipped smart-canvas page and JavaScript, but supplies a
fresh temporary ProjectStore and a small local HTTP boundary for its API calls.
No application data, .env values, online providers, or paid models are used.

The contract under test: history records persisted by the backend carry
``created_at`` (millisecond epoch) and the history drawer must render that
real timestamp, never the page-load time.
"""

from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectNotFound, ProjectStore, RevisionConflict

CLIENT_SCRIPT = ROOT / "tests" / "history_time_browser_client.js"
FIXED_CREATED_AT = 1785590605699


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


def seed_store(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("时间历史项目")
    history_records = [
        {
            "id": "history-time-1",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "固定时间戳的历史提示词",
            "params": {"model": "offline-browser-model", "size": "1024x1024"},
            "created_at": FIXED_CREATED_AT,
            "createdAt": FIXED_CREATED_AT,
            "completed_at": FIXED_CREATED_AT + 4200,
            "outputs": [],
        },
        {
            "id": "history-time-2",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第二条固定时间戳历史",
            "params": {"model": "offline-browser-model", "size": "1024x1024"},
            "created_at": FIXED_CREATED_AT - 60 * 60 * 1000,
            "createdAt": FIXED_CREATED_AT - 60 * 60 * 1000,
            "outputs": [],
        },
    ]
    store.apply_mutations(
        project["id"],
        [{"op": "set_canvas_meta", "logs": history_records}],
        base_revision=0,
    )
    store.flush(project["id"])
    return {
        "project_id": project["id"],
        "created_at": str(FIXED_CREATED_AT),
        "older_created_at": str(FIXED_CREATED_AT - 60 * 60 * 1000),
    }


class HistoryTimeServer:
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

            def read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

            def api_error(self, message: str, status: int = 400):
                self.send_json({"detail": message}, status)

            def project_id_from_parts(self, parts):
                if len(parts) < 3 or parts[0:2] != ["api", "project-library"]:
                    return ""
                return urllib.parse.unquote(parts[2])

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/static/smart-canvas.html":
                    source = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
                    injection = '<script src="/tests/history_time_browser_client.js"></script>'
                    self.send_bytes(source.replace("</body>", f"{injection}</body>").encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path == "/tests/history_time_browser_client.js":
                    self.send_bytes(CLIENT_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
                    return
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                if parts == ["api", "config"]:
                    self.send_json({"api_providers": [], "chat_models": [], "image_models": [], "video_models": []})
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
                project_id = self.project_id_from_parts(parts)
                if project_id and len(parts) == 3:
                    try:
                        self.send_json(owner.store.open_project(project_id))
                    except ProjectNotFound as exc:
                        self.api_error(str(exc), 404)
                    return
                super().do_GET()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/__history_time_results":
                    payload = self.read_json()
                    with owner.condition:
                        owner.results.append(payload)
                        owner.condition.notify_all()
                    self.send_json({"ok": True})
                    return
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                project_id = self.project_id_from_parts(parts)
                body = self.read_json()
                try:
                    if project_id and len(parts) == 4 and parts[3] == "flush":
                        self.send_json(owner.store.flush(project_id))
                        return
                    if project_id and len(parts) == 4 and parts[3] == "save-status":
                        self.send_json(owner.store.save_status(project_id))
                        return
                    if project_id and len(parts) == 5 and parts[3] == "history" and parts[4] == "delete":
                        canvas = owner.store.delete_history_record(project_id, body["log_id"], base_revision=body["base_revision"])
                        owner.store.flush(project_id)
                        self.send_json({"ok": True, "canvas": canvas})
                        return
                except RevisionConflict as exc:
                    self.send_json({"detail": {"message": str(exc), "canvas": exc.canvas}}, 409)
                    return
                except ProjectNotFound as exc:
                    self.api_error(str(exc), 404)
                    return
                self.api_error("not found", 404)

            def do_PATCH(self):
                parsed = urllib.parse.urlparse(self.path)
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                project_id = self.project_id_from_parts(parts)
                body = self.read_json()
                if not project_id or parts[-1:] != ["canvas"]:
                    self.api_error("not found", 404)
                    return
                try:
                    result = owner.store.apply_mutations(project_id, body.get("mutations", []), base_revision=body.get("base_revision", 0))
                    owner.store.flush(project_id)
                    self.send_json({**result, "saved": True, "saved_revision": result.get("revision", 0)})
                except RevisionConflict as exc:
                    self.send_json({"detail": {"message": str(exc), "canvas": exc.canvas}}, 409)
                except ProjectNotFound as exc:
                    self.api_error(str(exc), 404)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="history-time-browser", daemon=True)

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
                    raise RuntimeError("browser history timestamp validation timed out")
                self.condition.wait(remaining)
            return self.results[start_count]


def remove_profile(profile: Path):
    for _attempt in range(20):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(0.15)


def launch(browser: Path, server: HistoryTimeServer, fixture: dict[str, str]):
    profile = Path(tempfile.mkdtemp(prefix="firefly-history-time-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode(
        {
            "createdAt": fixture["created_at"],
            "olderCreatedAt": fixture["older_created_at"],
        }
    )
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(fixture['project_id'])}&{query}"
    process = subprocess.Popen(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--window-size=1424,805",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        result = server.wait_for_result(before)
        if not result.get("pass"):
            raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        remove_profile(profile)


def main():
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="firefly-history-time-project-library-") as root:
        store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
        fixture = seed_store(store)
        server = HistoryTimeServer(store)
        server.start()
        try:
            result = launch(browser, server, fixture)
        finally:
            server.close()
            store.close()
    summary = {
        "issue": "history-time",
        "browser": str(browser),
        "runs": [result],
        "pass": result.get("pass") is True,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
