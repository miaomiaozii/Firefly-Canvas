"""T10 complete generation/delete/recovery journey in native Windows Chrome."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("FIREFLY_TEST_MODE", "1")

from firefly_projects import MEDIA_TRASH_RETENTION_MS, ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser

VIEWPORT = (1424, 900)
PROVIDER = {
    "id": "native-image",
    "name": "Native Browser Fake Image",
    "enabled": True,
    "protocol": "openai",
    "image_models": ["native-image-model"],
    "chat_models": [],
    "video_models": [],
    "image_model_capabilities": {
        "native-image-model": {
            "arbitrary_size": True,
            "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True,
            "max_reference_images": 0,
        }
    },
}
STEP_NAMES = (
    "overlapping-pending",
    "record-delete-during-pending",
    "original-delete-during-pending",
    "cancel-target-only",
    "failed-retry-new-identity",
    "download-original-bytes",
    "reload-retains-terminal-identities",
    "seven-day-restore",
)


def tiny_png(label: str) -> bytes:
    digest = hashlib.sha256(label.encode("utf-8")).digest()

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return len(data).to_bytes(4, "big") + kind + data + checksum.to_bytes(4, "big")

    width = height = 5
    pixel = bytes((digest[0], digest[1], digest[2], 255))
    raw = b"".join(b"\x00" + pixel * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def seed(store: ProjectStore) -> str:
    project = store.create_project("T10 Complete Generation Lifecycle")
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": node_id,
                    "type": "smart-image",
                    "x": 80 + index * 320,
                    "y": 140,
                    "w": 260,
                    "h": 220,
                    "images": [],
                },
            }
            for index, node_id in enumerate(("failed-node", "download-node", "cancel-node"))
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def api(base_url: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Origin": base_url, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        return error.code, json.loads(raw) if raw else {}


def api_bytes(base_url: str, path: str) -> tuple[int, bytes]:
    request = urllib.request.Request(base_url + path, method="GET", headers={"Origin": base_url})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def submit(base_url: str, store: ProjectStore, project_id: str, node_id: str, prompt: str, key: str) -> str:
    revision = store.open_project(project_id)["canvas"]["revision"]
    status, response = api(
        base_url,
        "POST",
        f"/api/project-library/{urllib.parse.quote(project_id)}/image-generation-tasks",
        {
            "prompt": prompt,
            "routing_mode": "manual",
            "provider_id": "native-image",
            "model": "native-image-model",
            "size": "1024x1024",
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": node_id,
            "base_revision": revision,
            "idempotency_key": key,
        },
    )
    if status != 202:
        raise RuntimeError(f"task submission failed: {status} {response}")
    return str(response["task_id"])


def wait_task(base_url: str, project_id: str, task_id: str, statuses: set[str], timeout: float = 20) -> dict:
    path = f"/api/project-library/{urllib.parse.quote(project_id)}/image-generation-tasks/{urllib.parse.quote(task_id)}"
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        status, latest = api(base_url, "GET", path)
        if status == 200 and latest.get("status") in statuses:
            return latest
        time.sleep(0.05)
    raise TimeoutError(f"task did not reach {statuses}: {latest}")


def task_record(store: ProjectStore, project_id: str, task_id: str, status: str | None = None) -> dict:
    matches = [
        item
        for item in store.open_project(project_id)["canvas"].get("logs") or []
        if str(item.get("task_id") or "") == task_id and (status is None or item.get("status") == status)
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {status or 'terminal'} record for {task_id}, got {matches}")
    return matches[0]


def output_identity(record: dict) -> str:
    outputs = record.get("outputs") or []
    if len(outputs) != 1:
        raise AssertionError(f"expected one output: {record}")
    output = outputs[0]
    asset_id = str(output.get("asset_id") or output.get("assetId") or "")
    if not asset_id:
        raise AssertionError(f"missing output identity: {output}")
    return asset_id


def wait_for_retry(store: ProjectStore, project_id: str, failed_id: str, timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = next(
            (
                item
                for item in store.open_project(project_id)["canvas"].get("logs") or []
                if item.get("task_id") != failed_id
                and item.get("status") == "succeeded"
                and "NATIVE_FAILURE" in str(item.get("prompt") or "")
            ),
            None,
        )
        if match:
            return str(match["task_id"])
        time.sleep(0.05)
    raise TimeoutError("native retry did not create a distinct successful task")


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    artifacts = (args.artifacts_dir or Path(tempfile.mkdtemp(prefix="firefly-t10-evidence-"))).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    screenshots_dir = artifacts / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    operations_path = artifacts / "operations.jsonl"
    requests_path = artifacts / "requests.json"
    console_path = artifacts / "console-errors.json"
    browser_path = find_browser("chrome")
    release_overlapping = threading.Event()
    attempts: list[str] = []
    steps: list[dict] = []
    screenshots: list[str] = []
    lifecycle: dict = {}
    browser: NativeBrowser | None = None
    store: ProjectStore | None = None
    browser_version: dict = {}
    captured_operations: list[dict] = []
    captured_requests: list[dict] = []
    captured_console_errors: list[dict] = []
    prior_operations: list[dict] = []
    prior_requests: list[dict] = []
    prior_console_errors: list[dict] = []
    failure: dict | None = None

    def pass_step(name: str, **evidence: object) -> None:
        if name != STEP_NAMES[len(steps)]:
            raise AssertionError(f"journey step out of order: {name}")
        steps.append({"name": name, "status": "passed", **evidence})

    def snap(name: str) -> None:
        if browser is None:
            return
        path = browser.capture_screenshot(screenshots_dir / f"{len(screenshots) + 1:02d}-{name}.png")
        screenshots.append(rel(path, artifacts))

    payloads = {label: tiny_png(label) for label in (
        "NATIVE_OLD_RECORD", "NATIVE_OLD_ORIGINAL", "NATIVE_DOWNLOAD", "NATIVE_FAILURE"
    )}

    async def fake_generate(prompt, *_args, **kwargs):
        label = str(prompt or "")
        attempts.append(label)
        progress = kwargs.get("progress_callback")
        if progress:
            progress("provider_generating")
        if label in {"NATIVE_FAILURE", "NATIVE_DOWNLOAD"}:
            await asyncio.to_thread(release_overlapping.wait, 20)
        if label == "NATIVE_FAILURE" and attempts.count(label) == 1:
            raise RuntimeError("native fake first attempt failed")
        if label == "NATIVE_CANCEL":
            await asyncio.sleep(30)
        value = payloads.get(label) or tiny_png(label)
        return (
            {"type": "b64", "value": base64.b64encode(value).decode("ascii"), "mime_type": "image/png"},
            {"usage": {"total_tokens": 1}},
        )

    try:
        with tempfile.TemporaryDirectory(prefix="firefly-t10-native-") as root_name:
            root = Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id = seed(store)
            app = harness.load_production_app(root / "runtime-root", store)
            production = app.state.issue13_module
            production.load_api_providers = lambda: [PROVIDER]
            production.generate_ai_image = fake_generate
            downloads = root / "downloads"
            downloads.mkdir()
            with AsgiBrowserServer(app) as server:
                old_record_task = submit(server.base_url, store, project_id, "failed-node", "NATIVE_OLD_RECORD", "native-old-record")
                wait_task(server.base_url, project_id, old_record_task, {"succeeded"})
                old_original_task = submit(server.base_url, store, project_id, "cancel-node", "NATIVE_OLD_ORIGINAL", "native-old-original")
                wait_task(server.base_url, project_id, old_original_task, {"succeeded"})
                old_record = task_record(store, project_id, old_record_task, "succeeded")
                old_original = task_record(store, project_id, old_original_task, "succeeded")
                record_asset_id = output_identity(old_record)
                original_asset_id = output_identity(old_original)
                record_url = f"/api/project-library/{urllib.parse.quote(project_id)}/media/{record_asset_id}"
                original_url = f"/api/project-library/{urllib.parse.quote(project_id)}/media/{original_asset_id}"
                original_path = Path(store.media_record(project_id, original_asset_id)["path"])
                original_bytes = original_path.read_bytes()
                record_status, record_bytes = api_bytes(
                    server.base_url,
                    "/api/download-output?url=" + urllib.parse.quote(record_url, safe=""),
                )
                if record_status != 200 or record_bytes != payloads["NATIVE_OLD_RECORD"]:
                    raise AssertionError("seeded record asset was not downloadable")

                failed_id = submit(server.base_url, store, project_id, "failed-node", "NATIVE_FAILURE", "native-failure")
                download_id = submit(server.base_url, store, project_id, "download-node", "NATIVE_DOWNLOAD", "native-download")
                cancel_id = submit(server.base_url, store, project_id, "cancel-node", "NATIVE_CANCEL", "native-cancel")
                wait_task(server.base_url, project_id, failed_id, {"running"})
                wait_task(server.base_url, project_id, download_id, {"running"})
                wait_task(server.base_url, project_id, cancel_id, {"running"})

                browser = NativeBrowser.launch(browser_path)
                browser.set_environment(VIEWPORT)
                browser.cdp.command("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads)})
                url = (
                    f"{server.base_url}/static/smart-canvas.html"
                    f"?id={urllib.parse.quote(project_id)}&project={urllib.parse.quote(project_id)}"
                    "&project_mode=1&browserTest=1"
                )
                browser.navigate(url)
                browser.wait_for("document.querySelector('[data-id=\"cancel-node\"]')", timeout=20)
                browser.native_click("#smartLogToggle")
                for task_id in (failed_id, download_id, cancel_id):
                    browser.wait_for(f"document.querySelector('[data-log-cancel-task=\"{task_id}\"]')", timeout=15)
                status_card_task_ids = browser.evaluate(
                    "[...document.querySelectorAll('[data-log-cancel-task]')].map(item => item.dataset.logCancelTask)"
                )
                if set(status_card_task_ids) != {failed_id, download_id, cancel_id}:
                    raise AssertionError(f"overlapping task cards are not independent: {status_card_task_ids}")
                before_canvas = store.open_project(project_id)["canvas"]
                failed_node = next(item for item in before_canvas["nodes"] if item.get("id") == "failed-node")
                old_output_retained = any(
                    str(item.get("asset_id") or item.get("assetId") or "") == record_asset_id
                    for item in failed_node.get("images") or []
                ) and any(str(item.get("taskId") or "") == failed_id for item in failed_node.get("pendingTasks") or [])
                if not old_output_retained:
                    raise AssertionError("same-node old output was not retained beside the new pending task")
                snap("overlapping-pending")
                pass_step("overlapping-pending", task_ids=[failed_id, download_id, cancel_id])

                record_selector = f'[data-canvas-log-id="{old_record["id"]}"] [data-log-delete="record"]'
                browser.wait_for(f"document.querySelector({json.dumps(record_selector)})")
                browser.native_click(record_selector, accept_dialog=True)
                browser.wait_for(f"!document.querySelector('[data-canvas-log-id=\"{old_record["id"]}\"]')")
                canvas_after_record = store.open_project(project_id)["canvas"]
                if any(item.get("id") == old_record["id"] for item in canvas_after_record.get("logs") or []):
                    raise AssertionError("record-only delete retained its history record")
                if store.media_record(project_id, record_asset_id) is None:
                    raise AssertionError("record-only delete removed its generated asset")
                active_after_record = {
                    str(task.get("taskId") or "")
                    for node in canvas_after_record.get("nodes") or []
                    for task in node.get("pendingTasks") or []
                }
                if not {failed_id, download_id, cancel_id}.issubset(active_after_record):
                    raise AssertionError("record-only delete changed overlapping tasks")
                snap("record-deleted")
                pass_step("record-delete-during-pending")

                original_selector = f'[data-canvas-log-id="{old_original["id"]}"] [data-log-delete-original="0"]'
                browser.wait_for(f"document.querySelector({json.dumps(original_selector)})")
                browser.native_click(original_selector, accept_dialog=True)
                restore_selector = f'[data-log-restore-original="{original_asset_id}"]'
                browser.wait_for(f"document.querySelector({json.dumps(restore_selector)})", timeout=15)
                trash = store.list_deleted_media(project_id)
                trash_item = next((item for item in trash if item.get("asset_id") == original_asset_id), None)
                if trash_item is None or int(trash_item["expires_at"]) - int(trash_item["deleted_at"]) != MEDIA_TRASH_RETENTION_MS:
                    raise AssertionError("deleted original does not have exact seven-day retention")
                deleted_status, _ = api_bytes(
                    server.base_url,
                    "/api/download-output?url=" + urllib.parse.quote(original_url, safe=""),
                )
                if deleted_status != 404:
                    raise AssertionError(f"deleted generated original returned {deleted_status} instead of the 404 contract")
                current_cancel = wait_task(server.base_url, project_id, cancel_id, {"running"})
                snap("original-deleted")
                pass_step("original-delete-during-pending", deleted_status=deleted_status, task_status=current_cancel["status"])

                release_overlapping.set()
                wait_task(server.base_url, project_id, failed_id, {"failed"})
                wait_task(server.base_url, project_id, download_id, {"succeeded"})
                browser.wait_for(f"document.querySelector('[data-log-cancel-task=\"{cancel_id}\"]')", timeout=15)
                browser.native_click(f'[data-log-cancel-task="{cancel_id}"]')
                cancelled = wait_task(server.base_url, project_id, cancel_id, {"cancelled"})
                if any(
                    item.get("task_id") == cancel_id and item.get("status") == "succeeded"
                    for item in store.open_project(project_id)["canvas"].get("logs") or []
                ):
                    raise AssertionError("cancelled task was forged as completed")
                pass_step("cancel-target-only", task_status=cancelled["status"])

                prior_operations.extend(browser.operations)
                prior_requests.extend(browser.request_summaries)
                prior_console_errors.extend(browser.console_errors)
                browser.operations.clear()
                browser.request_summaries.clear()
                browser.console_errors.clear()
                browser.navigate(url)
                browser.wait_for("document.querySelector('[data-id=\"failed-node\"]')", timeout=20)
                browser.native_click("#smartLogToggle")
                browser.wait_for(f"document.querySelector('[data-log-retry-task=\"{failed_id}\"]')", timeout=15)
                browser.native_click(f'[data-log-retry-task="{failed_id}"]')
                retry_id = wait_for_retry(store, project_id, failed_id)
                if retry_id == failed_id:
                    raise AssertionError("retry reused the failed task identity")
                failed_record = task_record(store, project_id, failed_id, "failed")
                retry_record = task_record(store, project_id, retry_id, "succeeded")
                if failed_record["id"] == retry_record["id"]:
                    raise AssertionError("retry reused the failed record identity")
                pass_step("failed-retry-new-identity", retry_task_id=retry_id)

                download_record = task_record(store, project_id, download_id, "succeeded")
                browser.wait_for(
                    f"(canvas.logs || []).some(log => log.task_id === {json.dumps(download_id)} && log.status === 'succeeded')",
                    timeout=30,
                )
                generated_asset_id = output_identity(download_record)
                if generated_asset_id in {record_asset_id, original_asset_id}:
                    raise AssertionError("download action was not bound to a distinct generated asset identity")
                generated_path = Path(store.media_record(project_id, generated_asset_id)["path"])
                generated_bytes = generated_path.read_bytes()
                download_selector = f'[data-canvas-log-id="{download_record["id"]}"] [data-log-download-original]'
                browser.wait_for(f"document.querySelector({json.dumps(download_selector)})", timeout=15)
                browser.native_click(download_selector)
                deadline = time.monotonic() + 10
                downloaded: list[Path] = []
                while time.monotonic() < deadline:
                    downloaded = [path for path in downloads.iterdir() if path.is_file() and not path.name.endswith(".crdownload")]
                    if downloaded:
                        break
                    time.sleep(0.05)
                if len(downloaded) != 1 or downloaded[0].read_bytes() != generated_bytes:
                    raise AssertionError("downloaded project original was re-encoded or changed")
                snap("terminal-actions")
                pass_step("download-original-bytes", filename=downloaded[0].name)

                prior_operations.extend(browser.operations)
                prior_requests.extend(browser.request_summaries)
                prior_console_errors.extend(browser.console_errors)
                browser.operations.clear()
                browser.request_summaries.clear()
                browser.console_errors.clear()
                browser.navigate(url)
                browser.wait_for("document.querySelector('[data-id=\"cancel-node\"]')", timeout=20)
                browser.native_click("#smartLogToggle")
                for task_id, status in ((failed_id, "failed"), (retry_id, "succeeded"), (cancel_id, "cancelled"), (download_id, "succeeded")):
                    browser.wait_for(
                        f"(canvas.logs || []).some(log => log.task_id === {json.dumps(task_id)} && log.status === {json.dumps(status)})",
                        timeout=15,
                    )
                browser.wait_for(f"document.querySelector({json.dumps(restore_selector)})", timeout=15)
                snap("after-reload")
                pass_step("reload-retains-terminal-identities")

                browser.native_click(restore_selector, accept_dialog=True)
                browser.wait_for(
                    f"document.querySelector('[data-canvas-log-id=\"{old_original["id"]}\"] [data-log-delete-original=\"0\"]')",
                    timeout=15,
                )
                restored_status, restored_bytes = api_bytes(
                    server.base_url,
                    "/api/download-output?url=" + urllib.parse.quote(original_url, safe=""),
                )
                if restored_status != 200 or restored_bytes != original_bytes or store.list_deleted_media(project_id):
                    raise AssertionError("seven-day restore did not restore the exact original")
                final_canvas = store.open_project(project_id)["canvas"]
                if {item.get("id") for item in final_canvas.get("nodes") or []} != {"failed-node", "download-node", "cancel-node"}:
                    raise AssertionError("lifecycle actions changed result-node identity")
                terminal_record_counts = {
                    task_id: sum(1 for item in final_canvas.get("logs") or [] if str(item.get("task_id") or "") == task_id)
                    for task_id in (failed_id, retry_id, cancel_id, download_id)
                }
                if set(terminal_record_counts.values()) != {1}:
                    raise AssertionError(f"terminal record identity is not one-to-one: {terminal_record_counts}")
                snap("restored")
                pass_step("seven-day-restore", restored_status=restored_status)

                if len(steps) != len(STEP_NAMES):
                    raise AssertionError("complete journey did not execute every step")
                lifecycle = {
                    "project_id": project_id,
                    "failed_task_id": failed_id,
                    "retry_task_id": retry_id,
                    "cancel_task_id": cancel_id,
                    "download_task_id": download_id,
                    "record_log_id": str(old_record["id"]),
                    "original_log_id": str(old_original["id"]),
                    "record_asset_id": record_asset_id,
                    "original_asset_id": original_asset_id,
                    "independent_status_cards": True,
                    "status_card_task_ids": status_card_task_ids,
                    "old_output_retained_with_new_task": old_output_retained,
                    "record_delete_preserved_asset": True,
                    "record_delete_preserved_tasks": True,
                    "original_delete_preserved_task": True,
                    "cancel_preserved_other_tasks": task_record(store, project_id, download_id, "succeeded")["status"] == "succeeded",
                    "cancel_not_completed": True,
                    "retry_created_new_identity": retry_id != failed_id,
                    "retry_after_reload": True,
                    "failed_record_retained": task_record(store, project_id, failed_id, "failed")["status"] == "failed",
                    "download_bytes_preserved": downloaded[0].read_bytes() == generated_bytes,
                    "download_record_log_id": str(download_record["id"]),
                    "download_asset_id": generated_asset_id,
                    "download_request_path": f"/api/project-library/{project_id}/media/{generated_asset_id}/preview",
                    "deleted_access_rejected": deleted_status == 404,
                    "reload_preserved_terminal_records": True,
                    "restore_bytes_preserved": restored_bytes == original_bytes,
                    "trash_empty_after_restore": store.list_deleted_media(project_id) == [],
                    "result_nodes_preserved": True,
                    "terminal_record_counts": terminal_record_counts,
                    "retention_ms": MEDIA_TRASH_RETENTION_MS,
                    "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
                    "restored_sha256": hashlib.sha256(restored_bytes).hexdigest(),
                    "original_bytes": len(original_bytes),
                    "restored_bytes": len(restored_bytes),
                    "generated_sha256": hashlib.sha256(generated_bytes).hexdigest(),
                    "download_sha256": hashlib.sha256(downloaded[0].read_bytes()).hexdigest(),
                    "generated_bytes": len(generated_bytes),
                    "download_bytes": downloaded[0].stat().st_size,
                    "adapter_attempts": attempts,
                }
                browser_version = browser.browser_version()
                captured_operations = prior_operations + list(browser.operations)
                captured_requests = prior_requests + list(browser.request_summaries)
                captured_console_errors = prior_console_errors + list(browser.console_errors)
                browser.close()
                browser = None
            store.close()
            store = None
    except Exception as error:  # Evidence must survive every journey failure.
        failure = {
            "step": STEP_NAMES[len(steps)] if len(steps) < len(STEP_NAMES) else "finalize",
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        }
        if browser is not None:
            try:
                snap("failure")
            except Exception:
                pass
    finally:
        release_overlapping.set()
        operations = prior_operations + list(browser.operations) if browser is not None else captured_operations
        requests = prior_requests + list(browser.request_summaries) if browser is not None else captured_requests
        console_errors = prior_console_errors + list(browser.console_errors) if browser is not None else captured_console_errors
        if browser is not None:
            browser.close()
        if store is not None:
            store.close()
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        write_json(requests_path, requests)
        write_json(console_path, console_errors)
        passed = failure is None and len(steps) == len(STEP_NAMES)
        manifest = {
            "schema_version": 1,
            "journey": "complete-generation-lifecycle",
            "status": "passed" if passed else "failed",
            "environment": {
                "os": "Windows" if os.name == "nt" else os.name,
                "browser": {
                    "name": "Google Chrome",
                    "channel": "stable",
                    "version": str(browser_version.get("product") or "unknown"),
                },
                "dpr": 1,
                "color_scheme": "light",
            },
            "attempt": {
                "status": "passed" if passed else "failed",
                "viewport": list(VIEWPORT),
                "steps": steps,
                "lifecycle": lifecycle,
                "operations": rel(operations_path, artifacts),
                "requests": rel(requests_path, artifacts),
                "console_errors": rel(console_path, artifacts),
                "screenshots": screenshots,
            },
            "first_failure": failure,
        }
        write_json(artifacts / "manifest.json", manifest)
        print(json.dumps({"artifacts": str(artifacts), "status": manifest["status"], "first_failure": failure}, ensure_ascii=False, indent=2))
    return 0 if failure is None and len(steps) == len(STEP_NAMES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
