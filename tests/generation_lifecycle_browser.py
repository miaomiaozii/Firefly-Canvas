from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import MEDIA_TRASH_RETENTION_MS, ProjectStore
from tests.conversation_browser import load_production_app
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser


VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
TASK_ID = "task-new"
PENDING_HISTORY_ID = f"pending-generation-{TASK_ID}"
RESULT_NODE_ID = "result-reused"
RECORD_HISTORY_ID = "history-record-only"
ORIGINAL_HISTORY_ID = "history-original"
UNRELATED_HISTORY_ID = "history-unrelated"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument("--viewport", action="append", default=[])
    return parser.parse_args(argv)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def image_bytes(path: Path, color: tuple[int, int, int]) -> bytes:
    Image.new("RGB", (24, 16), color).save(path, format="PNG", compress_level=9)
    return path.read_bytes()


def media_output(project_id: str, media: dict, name: str) -> dict:
    return {
        "asset_id": media["asset_id"],
        "kind": "image",
        "name": name,
        "url": f"/api/project-library/{project_id}/media/{media['asset_id']}",
        "width": 24,
        "height": 16,
    }


def seed_project(store: ProjectStore, run_root: Path) -> dict:
    project = store.create_project("Generation lifecycle evidence")
    project_id = project["id"]
    record_content = image_bytes(run_root / "record.png", (220, 70, 80))
    original_content = image_bytes(run_root / "original.png", (70, 150, 230))
    downstream_content = image_bytes(run_root / "downstream.png", (80, 190, 110))
    record_media = store.import_media(
        project_id,
        record_content,
        "record.png",
        "image/png",
        {"type": "online_generation", "task_id": "task-record", "result_node_id": RESULT_NODE_ID},
    )
    original_media = store.import_media(
        project_id,
        original_content,
        "original.png",
        "image/png",
        {"type": "online_generation", "task_id": "task-original", "result_node_id": RESULT_NODE_ID},
    )
    downstream_media = store.import_media(
        project_id,
        downstream_content,
        "downstream.png",
        "image/png",
        {"type": "online_generation", "task_id": "task-downstream", "result_node_id": "downstream"},
    )
    record_output = media_output(project_id, record_media, "record.png")
    original_output = media_output(project_id, original_media, "original.png")
    downstream_output = media_output(project_id, downstream_media, "downstream.png")
    record_ref = {"asset_id": record_media["asset_id"], "kind": "image", "name": "record.png"}
    original_ref = {"asset_id": original_media["asset_id"], "kind": "image", "name": "original.png"}
    downstream_ref = {
        "asset_id": downstream_media["asset_id"],
        "kind": "image",
        "name": "downstream.png",
    }
    pending_task = {
        "taskId": TASK_ID,
        "kind": "image",
        "count": 1,
        "providerId": "fake-provider",
        "model": "fake-model",
        "prompt": "new overlapping generation",
        "pendingHistoryId": PENDING_HISTORY_ID,
        "referenceAssetIds": [],
        "references": [],
        "progress": {"phase": "queued", "message": "queued"},
    }
    nodes = [
        {
            "id": RESULT_NODE_ID,
            "type": "image",
            "title": "Reused result",
            "x": 180,
            "y": 140,
            "w": 360,
            "h": 280,
            "images": [record_ref, original_ref],
            "generationState": "processing",
            "status": "pending",
        },
        {
            "id": "downstream",
            "type": "image",
            "title": "Completed downstream",
            "x": 620,
            "y": 160,
            "w": 320,
            "h": 250,
            "images": [downstream_ref],
            "runInputRefs": [
                {
                    **original_ref,
                }
            ],
            "generationState": "complete",
            "status": "succeeded",
        },
        {
            "id": "unrelated",
            "type": "prompt",
            "title": "Unrelated work",
            "x": 620,
            "y": 470,
            "w": 300,
            "h": 180,
            "text": "unrelated survives",
        },
    ]
    logs = [
        {
            "id": RECORD_HISTORY_ID,
            "kind": "image",
            "status": "succeeded",
            "prompt": "record-only delete",
            "resultNodeId": RESULT_NODE_ID,
            "request": {"task_id": "task-record", "provider_id": "fake-provider"},
            "outputs": [record_ref],
        },
        {
            "id": ORIGINAL_HISTORY_ID,
            "kind": "image",
            "status": "succeeded",
            "prompt": "original delete and restore",
            "resultNodeId": RESULT_NODE_ID,
            "request": {"task_id": "task-original", "provider_id": "fake-provider"},
            "outputs": [original_ref],
        },
        {
            "id": "history-downstream",
            "kind": "image",
            "status": "succeeded",
            "prompt": "completed downstream",
            "resultNodeId": "downstream",
            "references": [original_ref],
            "outputs": [downstream_ref],
        },
        {
            "id": UNRELATED_HISTORY_ID,
            "kind": "text",
            "status": "succeeded",
            "prompt": "unrelated history",
            "outputs": [],
        },
    ]
    mutations = [{"op": "upsert_node", "node": node} for node in nodes]
    mutations.extend(
        [
            {
                "op": "upsert_edge",
                "edge": {
                    "id": "result-downstream",
                    "from": RESULT_NODE_ID,
                    "to": "downstream",
                    "kind": "flow",
                },
            },
            {"op": "set_canvas_meta", "history": logs},
        ]
    )
    registered = store.apply_mutations(project_id, mutations, base_revision=0)
    store.begin_image_generation(
        project_id,
        RESULT_NODE_ID,
        pending_task,
        base_revision=int(registered["revision"]),
    )
    store.flush(project_id)
    original_record = store.media_record(project_id, original_media["asset_id"])
    original_path = Path(original_record["path"])
    initial_canvas = copy.deepcopy(store.open_project(project_id)["canvas"])
    original_log = next(item for item in initial_canvas["history"] if item.get("id") == ORIGINAL_HISTORY_ID)
    original_output = original_log["outputs"][0]
    original_node = next(item for item in initial_canvas["nodes"] if item.get("id") == RESULT_NODE_ID)
    original_node_output = next(
        item for item in original_node["images"]
        if item.get("output_id") == original_output.get("output_id")
    )
    if original_node_output.get("asset_id") != original_media["asset_id"]:
        raise AssertionError("fixture output occurrence identity is not shared by history and producer node")
    return {
        "project_id": project_id,
        "record_asset_id": record_media["asset_id"],
        "original_asset_id": original_media["asset_id"],
        "downstream_asset_id": downstream_media["asset_id"],
        "original_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
        "original_bytes": len(original_path.read_bytes()),
        "original_filename": original_record["filename"],
        "original_output_id": original_output["output_id"],
        "original_output_order": original_output["output_order"],
        "initial_canvas": initial_canvas,
    }


def task_registries(app) -> list[tuple[dict, object]]:
    production_main = app.state.issue13_module
    core = importlib.import_module("app.core")
    router = importlib.import_module("app.routers.api_canvas_image_tasks")
    pairs = []
    seen = set()
    for module in (production_main, core, router):
        registry = getattr(module, "CANVAS_TASKS", None)
        lock = getattr(module, "CANVAS_TASK_LOCK", None)
        if isinstance(registry, dict) and lock is not None and id(registry) not in seen:
            seen.add(id(registry))
            pairs.append((registry, lock))
    return pairs


def set_fake_task(app, phase: str) -> None:
    task = {
        "id": TASK_ID,
        "task_id": TASK_ID,
        "type": "online-image",
        "status": "running",
        "provider_id": "fake-provider",
        "model": "fake-model",
        "created_at": time.time(),
        "updated_at": time.time(),
        "result": None,
        "error": "",
        "progress": {"phase": phase, "message": phase},
    }
    for registry, lock in task_registries(app):
        with lock:
            registry[TASK_ID] = copy.deepcopy(task)


def clear_fake_task(app) -> None:
    for registry, lock in task_registries(app):
        with lock:
            registry.pop(TASK_ID, None)


def node_by_id(canvas: dict, node_id: str) -> dict:
    return next(node for node in canvas.get("nodes") or [] if node.get("id") == node_id)


def completed_output_snapshot(node: dict) -> dict:
    return {
        "asset_ids": [
            str(item.get("asset_id") or "")
            for item in node.get("images") or []
            if isinstance(item, dict)
        ],
        "status": node.get("status"),
        "generation_state": node.get("generationState"),
    }


def history_ids(canvas: dict) -> list[str]:
    return [str(item.get("id") or "") for item in canvas.get("history") or []]


def task_snapshot(canvas: dict) -> dict:
    task = node_by_id(canvas, RESULT_NODE_ID)["pendingTasks"][0]
    return {
        "task_id": task.get("taskId"),
        "provider_id": task.get("providerId"),
        "model": task.get("model"),
        "pending_history_id": task.get("pendingHistoryId"),
    }


def lifecycle_snapshot(store: ProjectStore, fixture: dict) -> dict:
    canvas = store.open_project(fixture["project_id"])["canvas"]
    result_node = copy.deepcopy(node_by_id(canvas, RESULT_NODE_ID))
    downstream = copy.deepcopy(node_by_id(canvas, "downstream"))
    unrelated = copy.deepcopy(node_by_id(canvas, "unrelated"))
    return {
        "revision": canvas["revision"],
        "history_ids": history_ids(canvas),
        "result_node": result_node,
        "task": task_snapshot(canvas),
        "downstream": downstream,
        "unrelated": unrelated,
        "connections": copy.deepcopy(canvas.get("connections") or []),
        "trash": copy.deepcopy(store.list_deleted_media(fixture["project_id"])),
    }


def open_history(browser: NativeBrowser, base_url: str, project_id: str) -> dict:
    browser.navigate(f"{base_url}/static/smart-canvas.html?id={project_id}")
    browser.wait_for("document.querySelector('#smartHistoryToggle')")
    browser.wait_for("typeof canvas !== 'undefined' && canvas && Array.isArray(canvas.history)")
    browser.wait_for("document.querySelector('#projectSaveState')?.dataset.state === 'saved'", timeout=30)
    operation = browser.native_click("#smartHistoryToggle")
    browser.wait_for("document.querySelector('#smartHistoryModal:not([hidden])')")
    return operation


def screenshot(browser: NativeBrowser, viewport_root: Path, name: str, artifacts_root: Path) -> str:
    return relative(browser.capture_screenshot(viewport_root / name), artifacts_root)


def run_viewport(
    browser_path: Path,
    viewport: tuple[int, int],
    run_root: Path,
    artifacts_root: Path,
) -> tuple[dict, dict]:
    width, height = viewport
    viewport_root = artifacts_root / f"viewport-{width}x{height}"
    viewport_root.mkdir(parents=True, exist_ok=False)
    operations_path = viewport_root / "operations.jsonl"
    requests_path = viewport_root / "requests.json"
    console_path = viewport_root / "console-errors.json"
    screenshots: list[str] = []
    sessions: list[dict] = []
    store = ProjectStore(run_root / "projects", snapshot_delay=0)
    fixture = seed_project(store, run_root)
    original_path = Path(store.media_record(fixture["project_id"], fixture["original_asset_id"])["path"])
    before = lifecycle_snapshot(store, fixture)
    browser = None
    server = None
    app = None
    browser_version = {}
    started = time.monotonic()
    try:
        app = load_production_app(run_root / "app-first", store)
        set_fake_task(app, "queued")
        server = AsgiBrowserServer(app)
        server.start()
        browser = NativeBrowser.launch(browser_path)
        browser.set_environment(viewport)
        open_history(browser, server.base_url, fixture["project_id"])
        browser.wait_for(f"document.querySelector('[data-canvas-history-id=\"{RECORD_HISTORY_ID}\"] [data-history-delete=\"record\"]')")
        browser.wait_for(f"document.querySelector('[data-canvas-history-id=\"{PENDING_HISTORY_ID}\"]')")
        screenshots.append(screenshot(browser, viewport_root, "01-before.png", artifacts_root))

        record_delete_operation = browser.native_click(
            f'[data-canvas-history-id="{RECORD_HISTORY_ID}"] [data-history-delete="record"]',
            accept_dialog=True,
        )
        try:
            browser.wait_for(f"!document.querySelector('[data-canvas-history-id=\"{RECORD_HISTORY_ID}\"]')")
        except TimeoutError as error:
            raise AssertionError(
                json.dumps(
                    {
                        "record_delete_operation": record_delete_operation,
                        "store_history_ids": history_ids(store.open_project(fixture["project_id"])["canvas"]),
                        "browser_logs": browser.evaluate(
                            "(canvas.history || []).map(log => ({id: log.id, status: log.status}))"
                        ),
                        "dom_cards": browser.evaluate(
                            "Array.from(document.querySelectorAll('[data-canvas-history-id]')).map(card => card.dataset.canvasHistoryId)"
                        ),
                        "requests": browser.request_summaries[-8:],
                        "console_errors": browser.console_errors[-8:],
                        "toast": browser.evaluate(
                            "document.querySelector('#toast, .toast, .smart-toast')?.textContent || ''"
                        ),
                    },
                    ensure_ascii=False,
                )
            ) from error
        set_fake_task(app, "provider_generating")
        try:
            browser.wait_for(
                f"(canvas.history || []).some(log => log.id === '{PENDING_HISTORY_ID}' && log.progress?.phase === 'provider_generating')",
                timeout=25,
            )
        except TimeoutError as error:
            task_endpoint = browser.evaluate(
                f"fetch('/api/canvas-image-tasks/{TASK_ID}').then(async response => "
                "({status: response.status, body: await response.json()}))",
                await_promise=True,
            )
            pending_logs = browser.evaluate(
                f"(canvas.history || []).filter(log => log.id === '{PENDING_HISTORY_ID}')"
            )
            registry_state = [
                {
                    "registry": id(registry),
                    "phase": ((registry.get(TASK_ID) or {}).get("progress") or {}).get("phase"),
                }
                for registry, _lock in task_registries(app)
            ]
            raise AssertionError(
                json.dumps(
                    {
                        "task_endpoint": task_endpoint,
                        "pending_logs": pending_logs,
                        "registries": registry_state,
                    },
                    ensure_ascii=False,
                )
            ) from error
        after_record = lifecycle_snapshot(store, fixture)
        if RECORD_HISTORY_ID in after_record["history_ids"]:
            raise AssertionError("record-only delete retained its target history")
        for retained in (ORIGINAL_HISTORY_ID, "history-downstream", UNRELATED_HISTORY_ID):
            if retained not in after_record["history_ids"]:
                raise AssertionError(f"record-only delete removed unrelated history: {retained}")
        if after_record["task"] != before["task"]:
            raise AssertionError("record-only delete changed the newer task identity")
        if not original_path.is_file():
            raise AssertionError("record-only delete removed a generated original")
        screenshots.append(screenshot(browser, viewport_root, "02-record-deleted.png", artifacts_root))

        original_delete_selector = (
            f'[data-canvas-history-id="{ORIGINAL_HISTORY_ID}"] '
            f'[data-history-delete-original="{fixture["original_output_id"]}"]'
        )
        try:
            browser.wait_for(
                f"document.querySelector({json.dumps(original_delete_selector)}) !== null",
                timeout=3,
            )
        except TimeoutError as error:
            dom_state = browser.evaluate(
                """(() => Array.from(document.querySelectorAll('[data-canvas-history-id]')).map(card => ({
                  id: card.dataset.canvasHistoryId || '',
                  record: Boolean(card.querySelector('[data-history-delete="record"]')),
                  original: Array.from(card.querySelectorAll('[data-history-delete-original]')).map(button => button.dataset.historyDeleteOriginal),
                  restore: Array.from(card.querySelectorAll('[data-history-restore-original]')).map(button => button.dataset.historyRestoreOriginal),
                })))()"""
            )
            store_state = [
                {"id": item.get("id"), "outputs": item.get("outputs")}
                for item in store.open_project(fixture["project_id"])["canvas"].get("history", [])
            ]
            browser_state = browser.evaluate(
                "(canvas.history || []).map(log => ({id: log.id, outputs: log.outputs || []}))"
            )
            raise AssertionError(
                json.dumps(
                    {
                        "missing_selector": original_delete_selector,
                        "dom": dom_state,
                        "browser": browser_state,
                        "store": store_state,
                    },
                    ensure_ascii=False,
                )
            ) from error
        browser.native_click(original_delete_selector, accept_dialog=True)
        restore_selector_before_restart = (
            f'[data-canvas-history-id="{ORIGINAL_HISTORY_ID}"] '
            f'[data-history-restore-original="{fixture["original_asset_id"]}"]'
        )
        try:
            browser.wait_for(
                f"document.querySelector({json.dumps(restore_selector_before_restart)})",
                timeout=12,
            )
        except TimeoutError as error:
            browser_log = browser.evaluate(
                f"(canvas.history || []).find(log => log.id === '{ORIGINAL_HISTORY_ID}') || null"
            )
            dom_actions = browser.evaluate(
                f"Array.from(document.querySelectorAll('[data-canvas-history-id=\"{ORIGINAL_HISTORY_ID}\"] button')).map(button => ({{"
                "text: button.textContent.trim(), disabled: button.disabled, "
                "restore: button.dataset.historyRestoreOriginal || '', "
                "original: button.dataset.historyDeleteOriginal || ''}))"
            )
            store_log = next(
                (
                    item
                    for item in store.open_project(fixture["project_id"])["canvas"].get("history", [])
                    if item.get("id") == ORIGINAL_HISTORY_ID
                ),
                None,
            )
            raise AssertionError(
                json.dumps(
                    {
                        "browser_log": browser_log,
                        "dom_actions": dom_actions,
                        "store_log": store_log,
                        "toast": browser.evaluate("document.querySelector('#toast')?.textContent || ''"),
                        "client_revision": browser.evaluate("Number(canvas?.revision || 0)"),
                        "store_revision": int(store.open_project(fixture["project_id"])["canvas"].get("revision") or 0),
                        "recent_requests": browser.request_summaries[-12:],
                    },
                    ensure_ascii=False,
                )
            ) from error
        after_original_delete = lifecycle_snapshot(store, fixture)
        if not original_path.is_file():
            raise AssertionError("occurrence delete removed media still referenced by the project")
        retained_record = store.media_record(fixture["project_id"], fixture["original_asset_id"])
        retained_path = Path(retained_record["path"])
        retained_sha = hashlib.sha256(retained_path.read_bytes()).hexdigest()
        if retained_sha != fixture["original_sha256"] or retained_path.stat().st_size != fixture["original_bytes"]:
            raise AssertionError("occurrence delete changed media retained for another project reference")
        if len(after_original_delete["trash"]) != 1:
            raise AssertionError(f"deleted original trash entry mismatch: {after_original_delete['trash']}")
        trash_item = after_original_delete["trash"][0]
        if trash_item.get("asset_id") != fixture["original_asset_id"]:
            raise AssertionError("deleted output tombstone asset identity changed")
        if (
            trash_item.get("output_id") != fixture["original_output_id"]
            or not str(trash_item.get("deletion_id") or "").startswith("deletion-")
            or trash_item.get("media_retained") is not True
            or trash_item.get("media_filename") != ""
        ):
            raise AssertionError(f"deleted output tombstone identity mismatch: {trash_item}")
        deleted_log = next(
            item for item in store.open_project(fixture["project_id"])["canvas"]["history"]
            if item.get("id") == ORIGINAL_HISTORY_ID
        )
        deleted_tombstones = deleted_log.get("deletedOutputTombstones") or []
        if (
            deleted_log.get("outputs")
            or len(deleted_tombstones) != 1
            or deleted_tombstones[0].get("deleted_output_id") != fixture["original_output_id"]
            or deleted_tombstones[0].get("deletion_id") != trash_item.get("deletion_id")
        ):
            raise AssertionError("occurrence delete did not preserve its stable tombstone identity")
        if int(trash_item.get("expires_at") or 0) - int(trash_item.get("deleted_at") or 0) != MEDIA_TRASH_RETENTION_MS:
            raise AssertionError("deleted original did not receive the exact seven-day retention")
        if after_original_delete["task"] != before["task"]:
            raise AssertionError("original delete changed the newer task identity")
        if after_original_delete["unrelated"] != before["unrelated"]:
            raise AssertionError(
                "original delete changed unrelated work: "
                + json.dumps(
                    {
                        "before": before["unrelated"],
                        "after": after_original_delete["unrelated"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        downstream_after_delete = node_by_id(
            store.open_project(fixture["project_id"])["canvas"], "downstream"
        )
        if completed_output_snapshot(downstream_after_delete) != completed_output_snapshot(before["downstream"]):
            raise AssertionError(
                "original delete cascaded into a completed downstream node: "
                + json.dumps(
                    {
                        "before": before["downstream"],
                        "after": downstream_after_delete,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        screenshots.append(screenshot(browser, viewport_root, "03-original-deleted.png", artifacts_root))
        browser_version = browser.browser_version()
        sessions.append(
            {
                "name": "before-restart",
                "operations": [copy.deepcopy(item) for item in browser.operations],
                "requests": [copy.deepcopy(item) for item in browser.request_summaries],
                "console_errors": [copy.deepcopy(item) for item in browser.console_errors],
            }
        )
        clear_fake_task(app)
        browser.close()
        browser = None
        server.close()
        server = None
        store.flush(fixture["project_id"])
        store.close()

        store = ProjectStore(run_root / "projects", snapshot_delay=0)
        app = load_production_app(run_root / "app-restarted", store)
        set_fake_task(app, "receiving")
        server = AsgiBrowserServer(app)
        server.start()
        browser = NativeBrowser.launch(browser_path)
        browser.set_environment(viewport)
        open_history(browser, server.base_url, fixture["project_id"])
        restore_selector = (
            f'[data-canvas-history-id="{ORIGINAL_HISTORY_ID}"] '
            f'[data-history-restore-original="{fixture["original_asset_id"]}"]'
        )
        browser.wait_for(f"document.querySelector({json.dumps(restore_selector)})", timeout=12)
        screenshots.append(screenshot(browser, viewport_root, "04-after-restart.png", artifacts_root))
        restore_operation = browser.native_click(restore_selector, accept_dialog=True)
        try:
            browser.wait_for(
                f"document.querySelector({json.dumps(original_delete_selector)})",
                timeout=12,
            )
        except TimeoutError as error:
            raise AssertionError(
                json.dumps(
                    {
                        "restore_operation": restore_operation,
                        "trash": store.list_deleted_media(fixture["project_id"]),
                        "media_restored": fixture["original_asset_id"]
                        in {item.get("asset_id") for item in store.list_media(fixture["project_id"])},
                        "browser_log": browser.evaluate(
                            f"(canvas.history || []).find(log => log.id === '{ORIGINAL_HISTORY_ID}') || null"
                        ),
                        "dom_actions": browser.evaluate(
                            f"Array.from(document.querySelectorAll('[data-canvas-history-id=\"{ORIGINAL_HISTORY_ID}\"] button')).map(button => ({{disabled: button.disabled, html: button.outerHTML}}))"
                        ),
                        "requests": browser.request_summaries[-8:],
                    },
                    ensure_ascii=False,
                )
            ) from error
        set_fake_task(app, "persisting")
        browser.wait_for(
            f"(canvas.history || []).some(log => log.id === '{PENDING_HISTORY_ID}' && log.progress?.phase === 'persisting')",
            timeout=12,
        )
        after_restore = lifecycle_snapshot(store, fixture)
        restored_record = store.media_record(fixture["project_id"], fixture["original_asset_id"])
        restored_path = Path(restored_record["path"])
        restored_sha = hashlib.sha256(restored_path.read_bytes()).hexdigest()
        if restored_sha != fixture["original_sha256"] or restored_path.stat().st_size != fixture["original_bytes"]:
            raise AssertionError("restored original bytes differ from the deleted original")
        if after_restore["trash"]:
            raise AssertionError("restore left the output occurrence in project trash")
        restored_log = next(item for item in after_restore["history_ids"] if item == ORIGINAL_HISTORY_ID)
        restored_canvas = store.open_project(fixture["project_id"])["canvas"]
        restored_record_log = next(item for item in restored_canvas["history"] if item.get("id") == restored_log)
        restored_output = restored_record_log.get("outputs") or []
        restored_node = node_by_id(restored_canvas, RESULT_NODE_ID)
        if (
            len(restored_output) != 1
            or restored_output[0].get("output_id") != fixture["original_output_id"]
            or restored_output[0].get("output_order") != fixture["original_output_order"]
            or sum(
                1 for item in restored_node.get("images") or []
                if item.get("output_id") == fixture["original_output_id"]
            ) != 1
        ):
            raise AssertionError("restore did not recover the exact output occurrence identity")
        if after_restore["task"] != before["task"]:
            raise AssertionError("restore changed the newer task identity")
        if completed_output_snapshot(after_restore["downstream"]) != completed_output_snapshot(before["downstream"]):
            raise AssertionError("restore changed the completed downstream node")
        if after_restore["unrelated"] != before["unrelated"]:
            raise AssertionError(
                "restore changed unrelated work: "
                + json.dumps(
                    {
                        "before": before["unrelated"],
                        "after": after_restore["unrelated"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        screenshots.append(screenshot(browser, viewport_root, "05-restored.png", artifacts_root))
        sessions.append(
            {
                "name": "after-restart",
                "operations": [copy.deepcopy(item) for item in browser.operations],
                "requests": [copy.deepcopy(item) for item in browser.request_summaries],
                "console_errors": [copy.deepcopy(item) for item in browser.console_errors],
            }
        )

        operations = [
            {"session": session["name"], **operation}
            for session in sessions
            for operation in session["operations"]
        ]
        requests = [
            {"session": session["name"], **request}
            for session in sessions
            for request in session["requests"]
        ]
        console_errors = [
            {"session": session["name"], **error}
            for session in sessions
            for error in session["console_errors"]
        ]
        unexpected_console = [
            item for item in console_errors
            if item.get("type") == "exception" or item.get("level") == "error"
        ]
        if unexpected_console:
            raise AssertionError(f"unexpected lifecycle console errors: {unexpected_console}")
        if any("delete-with-media" in str(item.get("url") or "") for item in requests):
            raise AssertionError("lifecycle journey used destructive history deletion")
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        write_json(requests_path, requests)
        write_json(console_path, console_errors)
        return (
            {
                "viewport": [width, height],
                "status": "passed",
                "duration_seconds": round(time.monotonic() - started, 3),
                "operations": relative(operations_path, artifacts_root),
                "requests": relative(requests_path, artifacts_root),
                "console_errors": relative(console_path, artifacts_root),
                "screenshots": screenshots,
                "lifecycle": {
                    "project_id": fixture["project_id"],
                    "task_id": TASK_ID,
                    "pending_history_id": PENDING_HISTORY_ID,
                    "result_node_id": RESULT_NODE_ID,
                    "record_history_id": RECORD_HISTORY_ID,
                    "original_history_id": ORIGINAL_HISTORY_ID,
                    "original_asset_id": fixture["original_asset_id"],
                    "original_output_id": fixture["original_output_id"],
                    "deletion_id": trash_item["deletion_id"],
                    "downstream_asset_id": fixture["downstream_asset_id"],
                    "original_sha256": fixture["original_sha256"],
                    "original_bytes": fixture["original_bytes"],
                    "record_removed_only": RECORD_HISTORY_ID not in after_record["history_ids"],
                    "original_retained_after_record_delete": after_record["task"] == before["task"],
                    "task_retained_after_original_delete": after_original_delete["task"] == before["task"],
                    "downstream_retained_after_original_delete": completed_output_snapshot(after_original_delete["downstream"])
                    == completed_output_snapshot(before["downstream"]),
                    "media_retained_after_occurrence_delete": trash_item.get("media_retained") is True,
                    "retained_media_sha256": retained_sha,
                    "output_identity_restored": restored_output[0].get("output_id") == fixture["original_output_id"],
                    "retention_ms": int(trash_item["expires_at"]) - int(trash_item["deleted_at"]),
                    "restart_exposed_restore": True,
                    "restored_sha256": restored_sha,
                    "restored_bytes": restored_path.stat().st_size,
                    "trash_empty_after_restore": not after_restore["trash"],
                    "task_retained_after_restore": after_restore["task"] == before["task"],
                    "downstream_retained_after_restore": completed_output_snapshot(after_restore["downstream"])
                    == completed_output_snapshot(before["downstream"]),
                    "unrelated_retained_after_restore": after_restore["unrelated"] == before["unrelated"],
                    "phase_sequence": ["queued", "provider_generating", "receiving", "persisting"],
                },
            },
            browser_version,
        )
    except Exception as error:
        failure_screenshot = None
        if browser is not None:
            try:
                failure_screenshot = browser.capture_screenshot(viewport_root / "failure-first.png")
            except Exception:
                failure_screenshot = None
        failure = {
            "viewport": [width, height],
            "reason": str(error),
            "failure_screenshot": relative(failure_screenshot, artifacts_root) if failure_screenshot else None,
        }
        raise RuntimeError(json.dumps(failure, ensure_ascii=False)) from error
    finally:
        if app is not None:
            clear_fake_task(app)
        if browser is not None:
            browser.close()
        if server is not None:
            server.close()
        store.close()


def selected_viewports(values: list[str]) -> tuple[tuple[int, int], ...]:
    if not values:
        return VIEWPORTS
    result = []
    for value in values:
        width, separator, height = value.lower().partition("x")
        if not separator:
            raise ValueError(f"invalid viewport: {value}")
        viewport = (int(width), int(height))
        if viewport not in VIEWPORTS:
            raise ValueError(f"unsupported viewport: {value}")
        result.append(viewport)
    return tuple(result)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_root = args.artifacts_dir.resolve()
    if artifacts_root.exists():
        raise RuntimeError(f"browser artifact directory already exists: {artifacts_root}")
    artifacts_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "journey": "generation-lifecycle",
        "status": "running",
        "environment": {
            "os": "Windows",
            "browser": {"name": "Google Chrome", "channel": "stable", "version": ""},
            "dpr": 1,
            "color_scheme": "light",
        },
        "attempts": [],
        "first_failure": None,
    }
    manifest_path = artifacts_root / "manifest.json"
    write_json(manifest_path, manifest)
    browser_path = find_browser("chrome")
    with tempfile.TemporaryDirectory(prefix="firefly-generation-lifecycle-") as temporary:
        for index, viewport in enumerate(selected_viewports(args.viewport)):
            try:
                attempt, version = run_viewport(
                    browser_path,
                    viewport,
                    Path(temporary) / f"run-{index}",
                    artifacts_root,
                )
                manifest["attempts"].append(attempt)
                manifest["environment"]["browser"]["version"] = version.get("product", "")
                write_json(manifest_path, manifest)
            except Exception as error:
                try:
                    failure = json.loads(str(error))
                except json.JSONDecodeError:
                    failure = {"viewport": list(viewport), "reason": str(error)}
                manifest["attempts"].append({"viewport": list(viewport), "status": "failed"})
                manifest["status"] = "failed"
                manifest["first_failure"] = failure
                write_json(manifest_path, manifest)
                raise
    manifest["status"] = "passed"
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
