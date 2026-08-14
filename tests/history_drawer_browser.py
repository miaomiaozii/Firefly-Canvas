"""Run Issue #12's history drawer contract in a real headless Chrome session.

The server serves the shipped smart-canvas page and JavaScript, but supplies a
fresh temporary ProjectStore and a small local HTTP boundary for its API calls.
No application data, .env values, online providers, or paid models are used.
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
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectNotFound, ProjectStore, ProjectStoreError, RevisionConflict


CLIENT_SCRIPT = ROOT / "tests" / "history_drawer_browser_client.js"
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
    image = Image.new("RGB", (640, 480), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 620, 460), outline=(255, 255, 255), width=6)
    draw.text((42, 42), label, fill=(255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def seed_store(store: ProjectStore) -> dict[str, str]:
    first_project = store.create_project("浏览器历史项目")
    other_project = store.create_project("另一个浏览器项目")

    first_media = store.import_media(
        first_project["id"],
        image_bytes("FIRST", (44, 82, 130)),
        "first.png",
        "image/png",
        {"type": "online_generation", "name": "first.png"},
    )
    second_media = store.import_media(
        first_project["id"],
        image_bytes("SECOND", (132, 70, 56)),
        "second.png",
        "image/png",
        {"type": "online_generation", "name": "second.png"},
    )
    third_media = store.import_media(
        first_project["id"],
        image_bytes("THIRD", (88, 76, 145)),
        "third.png",
        "image/png",
        {"type": "online_generation", "name": "third.png"},
    )
    imported_media = store.import_media(
        first_project["id"],
        image_bytes("IMPORTED", (72, 112, 112)),
        "imported.png",
        "image/png",
        {"type": "user_import", "name": "imported.png"},
    )
    downstream_media = store.import_media(
        first_project["id"],
        image_bytes("DOWNSTREAM", (50, 116, 74)),
        "downstream.png",
        "image/png",
        {"type": "online_generation", "name": "downstream.png"},
    )
    other_media = store.import_media(
        other_project["id"],
        image_bytes("OTHER", (101, 63, 139)),
        "other.png",
        "image/png",
        {"type": "online_generation", "name": "other.png"},
    )

    generator = {
        "id": "generator",
        "type": "smart-image",
        "x": 340,
        "y": 160,
        "images": [{"asset_id": first_media["asset_id"], "kind": "image", "name": "first.png"}],
        "runPrompt": "第一条历史提示词",
        "runModelPrompt": "first model prompt",
        "runPromptRefs": [],
        "runInputRefs": [{"asset_id": first_media["asset_id"], "kind": "image", "name": "first.png"}],
        "runSettings": {"model": "offline-browser-model", "size": "1024x1024"},
        "runFinishedAt": 10,
    }
    downstream = {
        "id": "downstream",
        "type": "smart-image",
        "x": 740,
        "y": 160,
        "images": [{"asset_id": downstream_media["asset_id"], "kind": "image", "name": "downstream.png"}],
        "runInputRefs": [{"asset_id": first_media["asset_id"], "kind": "image", "name": "first.png"}],
    }
    history_records = [
        {
            "id": "history-first",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第一条历史提示词",
            "actual_provider_id": "history-provider",
            "actual_provider_name": "comfly",
            "actual_model": "gemini-3.1-flash-image-preview",
            "runMs": 104319,
            "createdAt": 5000000,
            "params": {"model": "offline-browser-model", "size": "1024x1024", "resolution": "4k"},
            "outputs": [{"asset_id": first_media["asset_id"], "kind": "image", "name": "first.png"}],
            "result_node_id": "generator",
        },
        {
            "id": "history-second",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第二条历史提示词",
            "createdAt": 2000000,
            "params": {"model": "offline-browser-model", "size": "1024x1024"},
            "outputs": [{"asset_id": second_media["asset_id"], "kind": "image", "name": "second.png"}],
            "result_node_id": "second-generator",
        },
        {
            "id": "history-third",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第三条历史提示词",
            "createdAt": 3000000,
            "outputs": [{"asset_id": third_media["asset_id"], "kind": "image", "name": "third.png"}],
            "result_node_id": "third-generator",
        },
        {
            "id": "history-imported",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "导入素材不允许删除原图",
            "createdAt": 4000000,
            "outputs": [{"asset_id": imported_media["asset_id"], "kind": "image", "name": "imported.png"}],
            "result_node_id": "imported-node",
        },
    ]
    history_records.extend(
        {
            "id": f"history-filler-{index:04d}",
            "type": "online-image",
            "status": "failed" if index == 0 else "succeeded",
            "prompt": f"分页历史 {index}",
            "createdAt": 1500000 if index == 0 else 1000 + index,
            "error": "测试失败记录：模型不支持当前生成参数" if index == 0 else "",
            "outputs": [],
        }
        for index in range(4996)
    )
    # The project store returns append order (oldest to newest).
    featured = history_records[:4]
    history_records = [*history_records[4:], *featured[1:], featured[0]]
    store.apply_mutations(
        first_project["id"],
        [
            {"op": "upsert_node", "node": generator},
            {"op": "upsert_node", "node": downstream},
            {"op": "upsert_edge", "edge": {"id": "generator-downstream", "from": "generator", "to": "downstream", "kind": "flow"}},
            {
                "op": "set_canvas_meta",
                "logs": history_records,
            },
        ],
        base_revision=0,
    )
    store.apply_mutations(
        other_project["id"],
        [
            {
                "op": "set_canvas_meta",
                "logs": [
                    {
                        "id": "history-other",
                        "type": "online-image",
                        "status": "succeeded",
                        "prompt": "另一个项目的历史提示词",
                        "outputs": [{"asset_id": other_media["asset_id"], "kind": "image", "name": "other.png"}],
                    }
                ],
            }
        ],
        base_revision=0,
    )
    store.flush(first_project["id"])
    store.flush(other_project["id"])
    return {
        "project_id": first_project["id"],
        "first_asset": first_media["asset_id"],
        "second_asset": second_media["asset_id"],
        "third_asset": third_media["asset_id"],
        "imported_asset": imported_media["asset_id"],
        "other_prompt": "另一个项目的历史提示词",
    }


class HistoryBrowserServer:
    def __init__(self, store: ProjectStore):
        self.store = store
        self.results: list[dict] = []
        self.request_counts = {"history_delete_with_media": 0, "original_delete": 0, "original_restore": 0}
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
                    injection = '<script src="/tests/history_drawer_browser_client.js"></script>'
                    self.send_bytes(source.replace("</body>", f"{injection}</body>").encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path == "/tests/history_drawer_browser_client.js":
                    self.send_bytes(CLIENT_SCRIPT.read_bytes(), "text/javascript; charset=utf-8")
                    return
                if parsed.path == "/__history_request_counts":
                    self.send_json(dict(owner.request_counts))
                    return
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                if parts == ["api", "config"]:
                    self.send_json({
                        "api_providers": [{
                            "id": "history-provider",
                            "name": "comfly",
                            "image_models": ["gemini-3.1-flash-image-preview"],
                            "model_names": {"gemini-3.1-flash-image-preview": "Nano Banana 2"},
                        }],
                        "chat_models": [],
                        "image_models": ["gemini-3.1-flash-image-preview"],
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
                project_id = self.project_id_from_parts(parts)
                if project_id and len(parts) == 3:
                    try:
                        self.send_json(owner.store.open_project(project_id))
                    except ProjectNotFound as exc:
                        self.api_error(str(exc), 404)
                    return
                if project_id and len(parts) == 4 and parts[3] == "media":
                    try:
                        items = []
                        for item in owner.store.list_media(project_id):
                            asset_id = item["asset_id"]
                            base = f"/api/project-library/{urllib.parse.quote(project_id)}/media/{urllib.parse.quote(asset_id)}"
                            item["original_url"] = base
                            item["preview_url"] = f"{base}/preview" if item.get("kind") == "image" else ""
                            items.append(item)
                        self.send_json({"items": items})
                    except ProjectNotFound as exc:
                        self.api_error(str(exc), 404)
                    return
                if project_id and len(parts) in {5, 6} and parts[3] == "media":
                    asset_id = parts[4]
                    try:
                        record = owner.store.media_record(project_id, asset_id)
                        payload = Path(record["path"]).read_bytes()
                        if len(parts) == 6 and parts[5] == "preview":
                            self.send_bytes(payload, "image/png")
                        else:
                            self.send_bytes(payload, record.get("mime_type") or "application/octet-stream")
                    except (ProjectNotFound, OSError) as exc:
                        self.api_error(str(exc), 404)
                    return
                if project_id and parts[3:] == ["trash", "media"]:
                    try:
                        self.send_json({"items": owner.store.list_deleted_media(project_id)})
                    except ProjectNotFound as exc:
                        self.api_error(str(exc), 404)
                    return
                super().do_GET()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/__history_browser_results":
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
                    if project_id and len(parts) == 5 and parts[3] == "history" and parts[4] == "delete-with-media":
                        owner.request_counts["history_delete_with_media"] += 1
                        deleted = owner.store.delete_history_record_with_media(
                            project_id,
                            body["log_id"],
                            base_revision=body["base_revision"],
                        )
                        self.send_json({"ok": True, **deleted})
                        return
                    if project_id and len(parts) == 5 and parts[3] == "history" and parts[4] == "delete":
                        canvas = owner.store.delete_history_record(project_id, body["log_id"], base_revision=body["base_revision"])
                        owner.store.flush(project_id)
                        self.send_json({"ok": True, "canvas": canvas})
                        return
                    if project_id and len(parts) == 5 and parts[3] == "original" and parts[4] == "delete":
                        owner.request_counts["original_delete"] += 1
                        deleted = owner.store.delete_generated_original(
                            project_id,
                            body["asset_id"],
                            base_revision=body["base_revision"],
                            log_id=body.get("log_id"),
                            output_id=body.get("output_id"),
                            output_index=body.get("output_index"),
                        )
                        self.send_json({"ok": True, **deleted})
                        return
                    if project_id and len(parts) == 5 and parts[3] == "original" and parts[4] == "restore":
                        owner.request_counts["original_restore"] += 1
                        restored = owner.store.restore_deleted_original(
                            project_id,
                            body["asset_id"],
                            base_revision=body["base_revision"],
                            deletion_id=body.get("deletion_id"),
                        )
                        self.send_json({"ok": True, **restored})
                        return
                    if project_id and len(parts) == 4 and parts[3] == "flush":
                        self.send_json(owner.store.flush(project_id))
                        return
                    if project_id and len(parts) == 4 and parts[3] == "save-status":
                        self.send_json(owner.store.save_status(project_id))
                        return
                except RevisionConflict as exc:
                    self.send_json({"detail": {"message": str(exc), "canvas": exc.canvas}}, 409)
                    return
                except ProjectNotFound as exc:
                    self.api_error(str(exc), 404)
                    return
                except ProjectStoreError as exc:
                    self.api_error(str(exc), 409)
                    return
                self.api_error("not found", 404)

            def do_DELETE(self):
                parsed = urllib.parse.urlparse(self.path)
                parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
                project_id = self.project_id_from_parts(parts)
                if project_id and parts[3:] == ["trash", "media"]:
                    try:
                        removed = owner.store.purge_deleted_media(project_id)
                        self.send_json({"ok": True, "removed": removed})
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
                except ProjectStoreError as exc:
                    self.api_error(str(exc), 400)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="issue-12-history-browser", daemon=True)

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
                    raise RuntimeError("browser history drawer validation timed out")
                self.condition.wait(remaining)
            return self.results[start_count]


def remove_profile(profile: Path):
    for _attempt in range(20):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(0.15)


def launch(browser: Path, server: HistoryBrowserServer, viewport: tuple[int, int], fixture: dict[str, str]):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-issue12-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode(
        {
            "historyProject": fixture["project_id"],
            "otherPrompt": fixture["other_prompt"],
            "firstAsset": fixture["first_asset"],
            "secondAsset": fixture["second_asset"],
            "thirdAsset": fixture["third_asset"],
            "importedAsset": fixture["imported_asset"],
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
    with tempfile.TemporaryDirectory(prefix="firefly-issue12-project-library-") as root:
        results = []
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            fixture = seed_store(store)
            server = HistoryBrowserServer(store)
            server.start()
            try:
                results.append(launch(browser, server, viewport, fixture))
            finally:
                server.close()
                store.close()
    summary = {
        "issue": 12,
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
