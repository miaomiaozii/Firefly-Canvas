"""Exercise Issue #13 through the shipped canvas in real headless Chrome.

The HTTP server and ProjectStore are fresh temporary fixtures.  The browser
never sees a real project library, API key, .env file, or external model.
"""

from __future__ import annotations

import asyncio
import http.server
import importlib.util
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import wave
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore


CLIENT_SCRIPT = ROOT / "tests" / "conversation_browser_client.js"
HTML_PAGE_PATH = "/static/smart-canvas.html"
CLIENT_SCRIPT_URL = "/tests/conversation_browser_client.js"
INJECT_PATHS = {HTML_PAGE_PATH}
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


def image_bytes(label: str, color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (480, 320), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, 468, 308), outline=(255, 255, 255), width=5)
    draw.text((28, 28), label, fill=(255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def video_bytes() -> bytes:
    """Return one uncompressed AVI frame that ffmpeg can preview offline."""
    width, height = 2, 2
    row_stride = (width * 3 + 3) & ~3

    def bgr_row(pixels: tuple[tuple[int, int, int], ...]) -> bytes:
        row = b"".join(bytes((blue, green, red)) for red, green, blue in pixels)
        return row + b"\x00" * (row_stride - len(row))

    frame = bgr_row(((46, 92, 138), (138, 74, 46))) + bgr_row(((74, 138, 92), (255, 255, 255)))
    frame_size = len(frame)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        padding = b"\x00" if len(payload) % 2 else b""
        return kind + struct.pack("<I", len(payload)) + payload + padding

    def list_chunk(kind: bytes, payload: bytes) -> bytes:
        return chunk(b"LIST", kind + payload)

    avih = struct.pack(
        "<14I",
        1_000_000,
        frame_size,
        0,
        0x10,
        1,
        0,
        1,
        frame_size,
        width,
        height,
        0,
        0,
        0,
        0,
    )
    strh = struct.pack(
        "<4s4sIHH8I4h",
        b"vids",
        b"DIB ",
        0,
        0,
        0,
        0,
        1,
        1,
        0,
        1,
        frame_size,
        0xFFFFFFFF,
        0,
        0,
        0,
        width,
        height,
    )
    strf = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        frame_size,
        0,
        0,
        0,
        0,
    )
    stream = list_chunk(b"strl", chunk(b"strh", strh) + chunk(b"strf", strf))
    header = list_chunk(b"hdrl", chunk(b"avih", avih) + stream)
    movi = list_chunk(b"movi", chunk(b"00db", frame))
    index = chunk(b"idx1", struct.pack("<4sIII", b"00db", 0x10, 0, frame_size))
    return chunk(b"RIFF", b"AVI " + header + movi + index)


def audio_bytes() -> bytes:
    """Return a short valid PCM WAV fixture without embedding binary data."""
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8_000)
        stream.writeframes(b"\x00\x00" * 800)
    return output.getvalue()


def seed_store(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("Issue 13 对话画布")
    assets = {}
    for label, filename, mime, color in (
        ("IMAGE", "reference.png", "image/png", (46, 92, 138)),
        ("VIDEO", "reference.avi", "video/x-msvideo", (138, 74, 46)),
        ("AUDIO", "reference.wav", "audio/wav", (74, 138, 92)),
    ):
        content = image_bytes(label, color) if label == "IMAGE" else video_bytes() if label == "VIDEO" else audio_bytes()
        assets[label.lower()] = store.import_media(
            project["id"],
            content,
            filename,
            mime,
            {"type": "issue-13-browser-fixture", "name": filename},
        )["asset_id"]

    source_nodes = [
        {
            "id": "reference-image",
            "type": "smart-image",
            "x": 24,
            "y": 120,
            "w": 180,
            "h": 120,
            "scale": 1,
            "images": [{"asset_id": assets["image"], "kind": "image", "name": "reference.png"}],
        },
        {
            "id": "reference-video",
            "type": "smart-image",
            "x": 24,
            "y": 270,
            "w": 180,
            "h": 120,
            "scale": 1,
            "images": [{"asset_id": assets["video"], "kind": "video", "name": "reference.avi"}],
        },
        {
            "id": "reference-audio",
            "type": "smart-image",
            "x": 24,
            "y": 420,
            "w": 180,
            "h": 120,
            "scale": 1,
            "images": [{"asset_id": assets["audio"], "kind": "audio", "name": "reference.wav"}],
        },
    ]
    conversation = {
        "id": "conversation-1",
        "type": "smart-conversation",
        "x": 300,
        "y": 110,
        "w": 390,
        "h": 430,
        "title": "Conversation",
        "messages": [
            {
                "id": "message-user-1",
                "role": "user",
                "text": "请先分析这组三个参考素材。",
                "references": [],
                "created_at": 1,
            },
            {
                "id": "message-assistant-1",
                "role": "assistant",
                "text": "参考素材已收到；保留这段可提取的提示词片段。",
                "references": [],
                "created_at": 2,
            },
        ],
    }
    prompt = {
        "id": "prompt-existing",
        "type": "smart-prompt",
        "x": 760,
        "y": 150,
        "w": 316,
        "h": 240,
        "title": "Prompt",
        "text": "已有提示词，不是聊天历史",
    }
    store.apply_mutations(
        project["id"],
        [
            *({"op": "upsert_node", "node": node} for node in source_nodes),
            {"op": "upsert_node", "node": conversation},
            {"op": "upsert_node", "node": prompt},
            *(
                {
                    "op": "upsert_edge",
                    "edge": {"id": f"{source['id']}-conversation-1", "from": source["id"], "to": "conversation-1", "kind": "input"},
                }
                for source in source_nodes
            ),
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return {
        "project_id": project["id"],
        "selected_text": "参考素材已收到；保留这段可提取的提示词片段。",
        **{f"{key}_asset": value for key, value in assets.items()},
    }


class ConversationBrowserServer:
    def __init__(self, store: ProjectStore, app):
        self.store = store
        self.app = app
        self.results: list[dict] = []
        self.result_ids: set[str] = set()
        self.client_events: list[dict] = []
        self.condition = threading.Condition()
        self.asgi_loop = asyncio.new_event_loop()
        self.asgi_thread = threading.Thread(target=self.asgi_loop.run_forever, name="issue-13-conversation-asgi", daemon=True)
        self.manage_lifespan = hasattr(getattr(app, "router", None), "lifespan_context")
        self.lifespan_commands = queue.Queue()
        self.lifespan_startup = threading.Event()
        self.lifespan_shutdown = threading.Event()
        self.lifespan_messages = []
        self.lifespan_error = None
        self.lifespan_future = None
        self.delayed_conversation_response = False
        self.delayed_conversation_response_lock = threading.Lock()
        owner = self
        if not CLIENT_SCRIPT.is_file():
            raise FileNotFoundError(f"conversation browser client is missing: {CLIENT_SCRIPT}")

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                print(_format % _args, flush=True)

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

            def read_body(self):
                length = int(self.headers.get("Content-Length", "0"))
                return self.rfile.read(length) if length else b""

            def forward_to_production(self):
                body = self.read_body()
                scope = self.asgi_scope(body)
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
                    if self.command == "POST" and "/conversation/" in scope["path"] and scope["path"].endswith("/messages"):
                        with owner.delayed_conversation_response_lock:
                            delay_response = not owner.delayed_conversation_response
                            if delay_response:
                                owner.delayed_conversation_response = True
                        if delay_response:
                            time.sleep(16)
                    self.send_asgi_messages(messages)
                except Exception as exc:
                    self.send_json({"detail": f"生产 ASGI 路由失败：{exc}"}, 500)

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
                self.send_bytes(body, content_type, int(start.get("status") or 200), headers)

            def send_injected_canvas_html(self):
                body = self.read_body()
                scope = self.asgi_scope(body)
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
                body = b"".join(item.get("body", b"") for item in messages if item.get("type") == "http.response.body")
                if not start or int(start.get("status") or 500) != 200:
                    self.send_asgi_messages(messages)
                    return
                source = body.decode("utf-8")
                injection = f'<script src="{CLIENT_SCRIPT_URL}"></script>'
                if "</body>" not in source:
                    self.send_json({"detail": "smart-canvas.html has no </body> injection point"}, 500)
                    return
                html = source.replace("</body>", f"{injection}</body>", 1)
                headers = [
                    item for item in start.get("headers") or []
                    if (item[0].decode("latin-1") if isinstance(item[0], bytes) else str(item[0])).lower() != "content-length"
                ]
                self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", 200, headers)

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path in INJECT_PATHS:
                    self.send_injected_canvas_html()
                    return
                if parsed.path == CLIENT_SCRIPT_URL:
                    self.send_bytes(CLIENT_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
                    return
                self.forward_to_production()

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/__conversation_browser_results":
                    with owner.condition:
                        payload = self.read_json()
                        if payload.get("kind") == "client-loaded":
                            owner.client_events.append(payload)
                        else:
                            result_id = str(payload.get("result_id") or "").strip()
                            if result_id and result_id in owner.result_ids:
                                self.send_json({"ok": True, "duplicate": True})
                                return
                            if result_id:
                                owner.result_ids.add(result_id)
                            owner.results.append(payload)
                        owner.condition.notify_all()
                    self.send_json({"ok": True})
                    return
                self.forward_to_production()

            def do_PATCH(self):
                self.forward_to_production()

            def do_PUT(self):
                self.forward_to_production()

            def do_DELETE(self):
                self.forward_to_production()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="issue-13-conversation-browser", daemon=True)

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
            if any(item.get("type") == "lifespan.startup.failed" for item in self.lifespan_messages):
                raise RuntimeError(f"生产 ASGI lifespan startup failed: {self.lifespan_messages[-1]}")
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

    def wait_for_result(self, start_count: int, timeout: float = 60):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.results) <= start_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "browser conversation validation timed out; "
                        f"client_events={json.dumps(self.client_events, ensure_ascii=False)}"
                    )
                self.condition.wait(remaining)
            return self.results[start_count]


def remove_profile(profile: Path):
    for _attempt in range(20):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(0.15)


def load_production_app(root: Path, store: ProjectStore):
    """Load the shipped ASGI app against only temporary runtime/project roots."""
    runtime_root = (root / "runtime").resolve()
    projects_root = (root / "unused-projects").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    os.environ["FIREFLY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(projects_root)
    module_name = f"_firefly_issue13_main_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot create an isolated production app module")
    previous_main_module = os.environ.get("FIREFLY_MAIN_MODULE")
    os.environ["FIREFLY_MAIN_MODULE"] = module_name
    for _name in list(sys.modules):
        if _name.startswith("app.") or _name in ("app", "main"):
            sys.modules.pop(_name, None)
    production_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = production_main
    try:
        spec.loader.exec_module(production_main)
    finally:
        sys.modules.pop(module_name, None)
        if previous_main_module is None:
            os.environ.pop("FIREFLY_MAIN_MODULE", None)
        else:
            os.environ["FIREFLY_MAIN_MODULE"] = previous_main_module
    previous_store = production_main.FIREFLY_PROJECT_STORE
    production_main.FIREFLY_PROJECT_STORE = store
    if previous_store is not None and previous_store is not store:
        previous_store.close()

    async def fake_project_conversation_adapter(request):
        references = [item for item in request.get("references") or [] if isinstance(item, dict)]
        audio_references = [item for item in references if item.get("kind") == "audio" and item.get("asset_id")]
        if not audio_references:
            raise AssertionError("browser fake adapter did not receive the connected audio asset_id")
        return {"text": "离线假适配器回复：已保留这轮参考上下文。", "model": "offline-browser-model"}

    production_main.project_conversation_adapter = fake_project_conversation_adapter
    production_main.sync_static_html_versions = lambda: None
    production_main.app.state.issue13_module = production_main
    production_main.app.state.issue13_runtime_root = runtime_root
    production_main.app.state.issue13_projects_root = projects_root
    return production_main.app


def launch(browser: Path, server: ConversationBrowserServer, viewport: tuple[int, int], fixture: dict[str, str]):
    width, height = viewport
    print(f"launching {viewport} on {server.base_url}", flush=True)
    profile = Path(tempfile.mkdtemp(prefix="firefly-issue13-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": fixture["project_id"], "conversationProject": fixture["project_id"], "selectedText": fixture["selected_text"], "browserTest": "1"})
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
            f"--window-size={width},{height}",
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
    print(f"browser={browser}", flush=True)
    with tempfile.TemporaryDirectory(prefix="firefly-issue13-project-library-") as root:
        results = []
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            fixture = seed_store(store)
            print(f"seeded {fixture['project_id']}", flush=True)
            app = load_production_app(Path(root) / f"runtime-{index}", store)
            server = ConversationBrowserServer(store, app)
            server.start()
            print("server started", flush=True)
            try:
                results.append(launch(browser, server, viewport, fixture))
            finally:
                server.close()
                store.close()
    summary = {"issue": 13, "browser": str(browser), "viewports": VIEWPORTS, "runs": results, "pass": all(item.get("pass") for item in results)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
