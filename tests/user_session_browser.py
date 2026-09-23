"""Real-user QA session against the shipped Firefly Canvas app in real headless Chrome.

Simulates an operations user's full workflow:
  project list -> create project -> prompt node (text/save/drag/edit) ->
  Agent node (goal, 2 image task cards + dependency, plan, confirm, execute) ->
  history drawer -> reload persistence.

Uses the established isolated-harness pattern (temporary ProjectStore + the
production ASGI app) so no real project data is touched.  The image tasks run
through an offline fake adapter; without it the tasks would fail for missing
API keys, which is an environment limitation, not an app crash.
"""

from __future__ import annotations

import asyncio
import base64
import http.server
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "user_session_browser_client.js"
CLIENT_SCRIPT_URL = "/tests/user_session_browser_client.js"
LIST_PAGE_PATH = "/"
CANVAS_PAGE_PATH = "/static/smart-canvas.html"
RESULTS_PATH = "/__user_session_browser_results"
VIEWPORT = (1408, 710)


def tiny_png(color=(42, 123, 231)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    adapter_calls = {"count": 0}

    async def fake_agent_image_adapter(request):
        if request.get("kind") != "image":
            raise AssertionError(f"unexpected browser Agent kind: {request}")
        adapter_calls["count"] += 1
        # Different content per call: the dependency flow inherits the upstream
        # output as a reference, and the product rejects byte-identical results
        # as "生成内容与参考原件重复". A production provider would produce
        # distinct images; the fake must mimic that.
        color = (60 + (adapter_calls["count"] * 40) % 180, 123 + (adapter_calls["count"] * 30) % 120, 231 - (adapter_calls["count"] * 50) % 180)
        return {"images": [{"type": "b64", "value": base64.b64encode(tiny_png(color)).decode("ascii"), "mime_type": "image/png"}]}

    module.agent_image_adapter = fake_agent_image_adapter
    return app


class UserSessionBrowserServer:
    """Serves the production ASGI app, injects the QA client into BOTH the
    project-list page and the smart canvas page, and records every forwarded
    API request (method/path/status) for the report."""

    def __init__(self, store: ProjectStore, app):
        self.store = store
        self.app = app
        self.results: list[dict] = []
        self.result_ids: set[str] = set()
        self.api_requests: list[dict] = []
        self.condition = threading.Condition()
        self.asgi_loop = asyncio.new_event_loop()
        self.asgi_thread = threading.Thread(target=self.asgi_loop.run_forever, name="user-session-asgi", daemon=True)
        self.manage_lifespan = hasattr(getattr(app, "router", None), "lifespan_context")
        self.lifespan_commands = queue.Queue()
        self.lifespan_startup = threading.Event()
        self.lifespan_shutdown = threading.Event()
        self.lifespan_messages = []
        self.lifespan_error = None
        self.lifespan_future = None
        owner = self
        if not CLIENT_SCRIPT.is_file():
            raise FileNotFoundError(f"user session browser client is missing: {CLIENT_SCRIPT}")

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def send_bytes(self, payload: bytes, content_type: str, status: int = 200, headers=None):
                try:
                    def header_text(value):
                        return value.decode("latin-1") if isinstance(value, bytes) else str(value)

                    self.send_response(status)
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    has_length = False
                    for key, value in headers or []:
                        key_text = header_text(key)
                        value_text = header_text(value)
                        if key_text.lower() == "content-type":
                            continue
                        if key_text.lower() == "content-length":
                            has_length = True
                            continue
                        self.send_header(key_text, value_text)
                    if not has_length:
                        self.send_header("Content-Length", str(len(payload)))
                    if not any(header_text(key).lower() == "cache-control" for key, _value in headers or []):
                        self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass

            def send_json(self, value, status: int = 200):
                self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

            def read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

            def read_body(self):
                length = int(self.headers.get("Content-Length", "0"))
                return self.rfile.read(length) if length else b""

            def asgi_scope(self, body: bytes):
                parsed = urllib.parse.urlsplit(self.path)
                headers = [
                    (key.lower().encode("latin-1"), value.encode("latin-1"))
                    for key, value in self.headers.items()
                ]
                return {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "method": self.command,
                    "scheme": "http",
                    "path": urllib.parse.unquote(parsed.path),
                    "raw_path": parsed.path.encode("utf-8"),
                    "query_string": parsed.query.encode("ascii"),
                    "headers": headers,
                    "client": self.client_address,
                    "server": self.server.server_address,
                    "root_path": "",
                    "state": {},
                }

            def forward_to_production(self):
                body = self.read_body()
                scope = self.asgi_scope(body)
                self._log_scope = scope
                messages = []
                received = False

                async def receive():
                    nonlocal received
                    if received:
                        return {"type": "http.disconnect"}
                    received = True
                    return {"type": "http.request", "body": body, "more_body": False}

                async def send(message):
                    messages.append(message)

                async def invoke():
                    await owner.app(scope, receive, send)

                try:
                    future = asyncio.run_coroutine_threadsafe(invoke(), owner.asgi_loop)
                    future.result(timeout=120)
                    self.send_asgi_messages(messages)
                except Exception as exc:
                    self.send_json({"detail": f"生产 ASGI 路由失败：{exc}"}, 500)
                finally:
                    self._log_scope = None

            def send_asgi_messages(self, messages):
                start = next((item for item in messages if item.get("type") == "http.response.start"), None)
                if not start:
                    self.send_json({"detail": "生产 ASGI 没有返回响应"}, 502)
                    return
                body = b"".join(item.get("body", b"") for item in messages if item.get("type") == "http.response.body")
                headers = start.get("headers") or []
                content_type = next(
                    (value.decode("latin-1") if isinstance(value, bytes) else str(value)
                    for key, value in headers
                    if (key.decode("latin-1") if isinstance(key, bytes) else str(key)).lower() == "content-type"),
                    "application/octet-stream",
                )
                status = int(start.get("status") or 200)
                if scope := getattr(self, "_log_scope", None):
                    if scope["path"].startswith("/api/"):
                        owner.api_requests.append({
                            "method": scope["method"],
                            "path": scope["path"],
                            "status": status,
                        })
                self.send_bytes(body, content_type, status, headers)

            def send_injected_html(self, page_path: str):
                body = self.read_body()
                scope = self.asgi_scope(body)
                self._log_scope = scope
                messages = []

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                async def send(message):
                    messages.append(message)

                async def invoke():
                    await owner.app(scope, receive, send)

                future = asyncio.run_coroutine_threadsafe(invoke(), owner.asgi_loop)
                future.result(timeout=30)
                start = next((item for item in messages if item.get("type") == "http.response.start"), None)
                payload = b"".join(item.get("body", b"") for item in messages if item.get("type") == "http.response.body")
                if not start or int(start.get("status") or 500) != 200:
                    self.send_asgi_messages(messages)
                    self._log_scope = None
                    return
                source = payload.decode("utf-8", errors="replace")
                injection = f'<script src="{CLIENT_SCRIPT_URL}"></script>'
                if "</body>" not in source:
                    self.send_json({"detail": f"{page_path} has no </body> injection point"}, 500)
                    self._log_scope = None
                    return
                if page_path == "/" and "qaRaceProbe=1" in self.path:
                    # Deterministic race reproduction: make the project-library
                    # fetch resolve before DOMContentLoaded fires. project-list.js
                    # clears #status in render(); i18n-core then unconditionally
                    # re-sets it to the loading text via its DOMContentLoaded
                    # apply(). This makes that ordering visible every run.
                    probe_script = (
                        "<script>"
                        "window.__qaOrigFetch=window.fetch.bind(window);"
                        "window.fetch=(url,opts)=>{const u=String(url);"
                        "if(u.startsWith('/api/project-library')&&!u.includes('/media')&&!u.includes('/trash')){"
                        "return Promise.resolve(new Response(JSON.stringify({projects:[]}),{status:200,headers:{'Content-Type':'application/json'}}));}"
                        "return window.__qaOrigFetch(url,opts);};"
                        "</script>"
                    )
                    html = source.replace("<body>", f"<body>{probe_script}", 1)
                    html = html.replace("</body>", f"{injection}</body>", 1)
                    self._log_scope = None
                    headers = [
                        item for item in start.get("headers") or []
                        if (item[0].decode("latin-1") if isinstance(item[0], bytes) else str(item[0])).lower() != "content-length"
                    ]
                    self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", 200, headers)
                    return
                html = source.replace("</body>", f"{injection}</body>", 1)
                self._log_scope = None
                headers = [
                    item for item in start.get("headers") or []
                    if (item[0].decode("latin-1") if isinstance(item[0], bytes) else str(item[0])).lower() != "content-length"
                ]
                self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", 200, headers)

            def record_result(self, payload):
                with owner.condition:
                    if payload.get("kind") == "client-loaded":
                        pass
                    else:
                        result_id = str(payload.get("result_id") or "").strip()
                        if result_id:
                            if result_id in owner.result_ids:
                                self.send_json({"ok": True, "duplicate": True})
                                return
                            owner.result_ids.add(result_id)
                        owner.results.append(payload)
                    owner.condition.notify_all()
                self.send_json({"ok": True})

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path in (LIST_PAGE_PATH, "/index.html"):
                    self.send_injected_html("/")
                    return
                if parsed.path == CANVAS_PAGE_PATH:
                    self.send_injected_html(CANVAS_PAGE_PATH)
                    return
                if parsed.path == CLIENT_SCRIPT_URL:
                    self.send_bytes(CLIENT_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
                    return
                self.forward_to_production()

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == RESULTS_PATH:
                    self.record_result(self.read_json())
                    return
                self.forward_to_production()

            def do_PATCH(self):
                self.forward_to_production()

            def do_PUT(self):
                self.forward_to_production()

            def do_DELETE(self):
                self.forward_to_production()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="user-session-browser", daemon=True)

    async def run_lifespan(self):
        async def receive():
            return await asyncio.to_thread(self.lifespan_commands.get)

        async def send(message):
            self.lifespan_messages.append(message)
            message_type = message.get("type")
            if message_type in {"lifespan.startup.complete", "lifespan.startup.failed"}:
                self.lifespan_startup.set()
            if message_type in {"lifespan.shutdown.complete", "lifespan.shutdown.failed"}:
                self.lifespan_shutdown.set()

        try:
            await self.app(
                {
                    "type": "lifespan",
                    "asgi": {"version": "3.0", "spec_version": "2.0"},
                    "state": {},
                },
                receive,
                send,
            )
        except Exception as exc:
            self.lifespan_error = exc
            self.lifespan_startup.set()
            self.lifespan_shutdown.set()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self):
        self.asgi_thread.start()
        if self.manage_lifespan:
            self.lifespan_future = asyncio.run_coroutine_threadsafe(self.run_lifespan(), self.asgi_loop)
            self.lifespan_commands.put({"type": "lifespan.startup"})
            if not self.lifespan_startup.wait(30):
                raise RuntimeError("生产 ASGI lifespan startup timed out")
            if self.lifespan_error is not None:
                raise RuntimeError(f"生产 ASGI lifespan startup failed: {self.lifespan_error}")
        self.thread.start()

    def close(self):
        try:
            if self.manage_lifespan and self.lifespan_future is not None and not self.lifespan_future.done():
                self.lifespan_commands.put({"type": "lifespan.shutdown"})
                self.lifespan_shutdown.wait(30)
                self.lifespan_future.result(timeout=30)
        finally:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(timeout=5)
            self.asgi_loop.call_soon_threadsafe(self.asgi_loop.stop)
            self.asgi_thread.join(timeout=5)
            self.asgi_loop.close()

    def wait_for_result(self, start_count: int, timeout: float = 300):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.results) <= start_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "browser user-session validation timed out; "
                        f"api_requests={len(self.api_requests)}"
                    )
                self.condition.wait(remaining)
            return self.results[start_count]


def launch(browser: Path, server: UserSessionBrowserServer, viewport: tuple[int, int], *, race_probe: bool = False):
    width, height = viewport
    url = f"{server.base_url}/" + ("?qaRaceProbe=1" if race_probe else "")
    print(f"launching {viewport} against {url}", flush=True)
    profile = Path(tempfile.mkdtemp(prefix="firefly-user-session-profile-"))
    before = len(server.results)
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
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        return server.wait_for_result(before, timeout=120 if race_probe else 300)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        harness.remove_profile(profile)


def build_report(result: dict, api_requests: list[dict], server_state: dict | None = None, probe_result: dict | None = None) -> dict:
    failures = [item for item in api_requests if item["status"] >= 400]
    return {
        "surface": "user-session",
        "browser": str(harness.find_browser()),
        "viewport": list(VIEWPORT),
        "project": {"id": result.get("projectId"), "name": result.get("projectName")},
        "steps": result.get("steps") or [],
        "timeline": result.get("timeline") or [],
        "i18n_race_probe": probe_result,
        "server_state": server_state,
        "console_errors": result.get("consoleErrors") or [],
        "page_errors": result.get("pageErrors") or [],
        "exceptions": result.get("exceptions") or [],
        "api_failures": failures,
        "api_request_count": len(api_requests),
        "pass": bool(result.get("pass")),
    }


def capture_server_state(store: ProjectStore, project_id: str) -> dict | None:
    if not project_id:
        return None
    try:
        opened = store.open_project(project_id)
    except Exception as exc:
        return {"error": str(exc)}
    canvas = opened.get("canvas") or {}
    agent = next((item for item in canvas.get("nodes") or [] if isinstance(item, dict) and item.get("type") == "smart-agent"), None)
    plan = (agent or {}).get("agent_plan")
    return {
        "revision": canvas.get("revision"),
        "node_count": len(canvas.get("nodes") or []),
        "node_ids": [item.get("id") for item in canvas.get("nodes") or [] if isinstance(item, dict)],
        "agent_plan": {
            "status": (plan or {}).get("status"),
            "plan_id": (plan or {}).get("plan_id"),
            "tasks": [
                {
                    "task_id": item.get("task_id"),
                    "kind": item.get("kind"),
                    "status": item.get("status"),
                    "error": (item.get("error") or "")[:500],
                }
                for item in (plan or {}).get("tasks") or []
                if isinstance(item, dict)
            ],
        } if isinstance(plan, dict) else None,
        "history": [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "status": item.get("status"),
                "error": (item.get("error") or "")[:500],
            }
            for item in canvas.get("history") or []
            if isinstance(item, dict)
        ],
    }


def main():
    browser = harness.find_browser()
    with tempfile.TemporaryDirectory(prefix="firefly-user-session-library-") as root_text:
        root = Path(root_text)
        store = ProjectStore(root / "projects", snapshot_delay=0)
        app = load_app(root / "runtime", store)
        module = app.state.issue13_module
        server = UserSessionBrowserServer(store, app)
        server.start()
        result = None
        probe_result = None
        server_state = None
        try:
            probe_result = launch(browser, server, VIEWPORT, race_probe=True)
            result = launch(browser, server, VIEWPORT)
        finally:
            project_id = (result or {}).get("projectId") or ""
            if project_id:
                server_state = capture_server_state(store, project_id)
                try:
                    store.trash_project(project_id)
                    store.purge_trashed_project(project_id)
                    print(f"cleanup: trashed+purged test project {project_id}", flush=True)
                except Exception as exc:
                    print(f"cleanup note: {exc}", flush=True)
            server.close()
            try:
                if module.FIREFLY_PROJECT_STORE is not None and module.FIREFLY_PROJECT_STORE is not store:
                    module.FIREFLY_PROJECT_STORE.close()
            except Exception:
                pass
            store.close()

    report = build_report(result, server.api_requests, server_state, probe_result)
    report_path = ROOT / "tests" / "user_session_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "project": report["project"], "step_count": len(report["steps"]), "api_requests": report["api_request_count"], "race_probe": report.get("i18n_race_probe"), "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
