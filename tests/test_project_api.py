import asyncio
import tempfile
import sys
import unittest
import io
import json
import hashlib
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image

import main
from firefly_projects import ProjectStore


class ProjectApiContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0.05)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_startup_entry_is_project_list_and_empty_library_has_no_default(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("你的项目", response.text)
        self.assertIn("一个项目，一张画布", response.text)
        self.assertEqual(self.client.get("/api/project-library").json(), {"projects": []})
        self.assertEqual(self.client.get("/api/projects").json(), {"projects": []})
        self.assertEqual(self.client.post("/api/projects", json={"name": "legacy"}).status_code, 410)
        self.assertEqual(self.client.post("/api/canvases", json={"title": "legacy"}).status_code, 410)

    def test_create_open_and_incrementally_save_unique_core_canvas(self):
        created = self.client.post("/api/project-library", json={"name": "光影实验"})
        self.assertEqual(created.status_code, 200)
        project = created.json()["project"]
        self.assertEqual(project["id"], project["canvas_id"])

        opened = self.client.get(f"/api/project-library/{project['id']}")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json()["canvas"]["kind"], "core")
        compatibility = self.client.get(f"/api/canvases/{project['canvas_id']}")
        self.assertEqual(compatibility.status_code, 200)
        self.assertEqual(compatibility.json()["canvas"]["project_id"], project["id"])
        legacy_write = self.client.put(
            f"/api/canvases/{project['canvas_id']}",
            json={
                "title": "绕过", "nodes": [{"id": "bad", "images": [{"url": "file:///outside.png"}]}],
                "connections": [], "viewport": {}, "logs": [], "settings": {},
            },
        )
        self.assertEqual(legacy_write.status_code, 410)

        changed = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"mutations": [
                {"op": "upsert_node", "node": {"id": "n1", "type": "prompt", "x": 20, "y": 30}},
                {"op": "set_viewport", "viewport": {"x": 5, "y": 6, "scale": 1.25}},
            ], "base_revision": 0},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["applied"], 2)
        self.assertTrue(changed.json()["saved"])
        saved = self.client.post(f"/api/project-library/{project['id']}/flush")
        self.assertEqual(saved.status_code, 200)
        reloaded = self.client.get(f"/api/project-library/{project['id']}").json()["canvas"]
        self.assertEqual(reloaded["nodes"], [{"id": "n1", "type": "prompt", "x": 20, "y": 30}])
        self.assertNotIn("viewport", reloaded)
        self.assertNotIn("settings", reloaded)

        media = self.store.import_media(project["id"], b"owned-image", "owned.png", "image/png")
        media_response = self.client.get(f"/api/project-library/{project['id']}/media/{media['asset_id']}")
        self.assertEqual(media_response.status_code, 200)
        self.assertEqual(media_response.content, b"owned-image")

        source = Path(self.temp_dir.name) / "source.png"
        source.write_bytes(b"imported-through-api")
        previous_input_dir = main.OUTPUT_INPUT_DIR
        storage_input = Path(self.temp_dir.name) / "configured-input"
        storage_input.mkdir()
        storage_source = storage_input / "source.png"
        storage_source.write_bytes(b"storage-source")
        main.OUTPUT_INPUT_DIR = str(storage_input)
        try:
            self.assertEqual(
                main.project_media_source_path("/api/storage-files/upload/source.png"),
                str(storage_source),
            )
        finally:
            main.OUTPUT_INPUT_DIR = previous_input_dir
        previous_resolver = main.project_media_source_path
        main.project_media_source_path = lambda _url: str(source)
        try:
            imported = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={"source_url": "/assets/source.png", "name": "source.png", "mime_type": "image/png"},
            )
        finally:
            main.project_media_source_path = previous_resolver
        self.assertEqual(imported.status_code, 202)
        task_id = imported.json()["task"]["id"]
        deadline = time.monotonic() + 3
        task = imported.json()["task"]
        while task.get("status") == "pending" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = self.client.get(
                f"/api/project-library/{project['id']}/media/import/{task_id}"
            ).json()["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["media"]["sha256"], hashlib.sha256(b"imported-through-api").hexdigest())

    def test_unknown_project_and_invalid_mutation_are_rejected(self):
        self.assertEqual(self.client.get("/api/project-library/missing").status_code, 404)
        project = self.client.post("/api/project-library", json={"name": "边界"}).json()["project"]
        response = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"base_revision": 0, "mutations": [{"op": "replace_everything"}]},
        )
        self.assertEqual(response.status_code, 400)

    def test_media_import_restarts_after_an_application_lifespan_stops(self):
        project = self.store.create_project("重复 lifespan 后导入")
        source = Path(self.temp_dir.name) / "after-lifespan.png"
        source.write_bytes(b"after-lifespan")
        startup_side_effects = (
            patch.object(main, "sync_static_html_versions"),
            patch.object(main, "migrate_asset_library_into_dirs"),
            patch.object(main, "migrate_double_extension_uploads"),
            patch.object(main, "migrate_mislabeled_image_extensions"),
        )
        for mocked in startup_side_effects:
            mocked.start()
        try:
            asyncio.run(main.startup_event())
            asyncio.run(main.shutdown_event())
        finally:
            for mocked in reversed(startup_side_effects):
                mocked.stop()

        with patch.object(main, "project_media_source_path", return_value=str(source)):
            response = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={"source_url": "/assets/after-lifespan.png", "mime_type": "image/png"},
            )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIn("task", response.json())
        task = response.json()["task"]
        deadline = time.monotonic() + 3
        while task.get("status") == "pending" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = self.client.get(
                f"/api/project-library/{project['id']}/media/import/{task['id']}"
            ).json()["task"]
        self.assertEqual(task.get("status"), "completed", task)

    def test_shutdown_reports_closed_owner_loop_without_clearing_unfinished_worker_state(self):
        closed_loop = asyncio.new_event_loop()
        runner = main.ProjectConversationTaskRunner()
        worker = closed_loop.create_task(asyncio.sleep(60))
        handle = closed_loop.create_future()
        task_key = ("closed-loop-project", "conversation-node", "request-1")
        runner.loop = closed_loop
        runner.queue = asyncio.Queue()
        runner.workers = [worker]
        runner.handles.add(handle)
        runner.accepting = True
        with main.PROJECT_CONVERSATION_TASKS_LOCK:
            main.PROJECT_CONVERSATION_TASKS[task_key] = handle
        closed_loop.close()
        main.PROJECT_CONVERSATION_RUNNER = runner

        try:
            with self.assertRaisesRegex(RuntimeError, "runner 状态已保留"):
                asyncio.run(main.shutdown_event())
            self.assertIs(main.PROJECT_CONVERSATION_RUNNER, runner)
            self.assertFalse(runner.accepting)
            self.assertEqual(runner.workers, [worker])
            self.assertIn(handle, runner.handles)
            self.assertIsNotNone(runner.queue)
            self.assertIs(runner.loop, closed_loop)
            with main.PROJECT_CONVERSATION_TASKS_LOCK:
                self.assertIs(main.PROJECT_CONVERSATION_TASKS.get(task_key), handle)
        finally:
            # The deliberately closed owner loop cannot finish this synthetic task.
            worker._log_destroy_pending = False
            worker.get_coro().close()
            with main.PROJECT_CONVERSATION_TASKS_LOCK:
                main.PROJECT_CONVERSATION_TASKS.pop(task_key, None)
            runner.workers.clear()
            runner.handles.clear()
            runner.queue = None
            runner.loop = None
            main.PROJECT_CONVERSATION_RUNNER = None

    def test_shutdown_cancels_conversation_workers_on_their_running_lifespan_loop(self):
        foreign_loop = asyncio.new_event_loop()
        runner = main.ProjectConversationTaskRunner()
        worker_started = threading.Event()
        worker_stopped = threading.Event()
        worker_holder = []

        async def foreign_worker():
            worker_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                worker_stopped.set()

        def run_foreign_loop():
            asyncio.set_event_loop(foreign_loop)
            worker_holder.append(foreign_loop.create_task(foreign_worker()))
            foreign_loop.run_forever()

        loop_thread = threading.Thread(target=run_foreign_loop)
        loop_thread.start()
        self.assertTrue(worker_started.wait(1))
        runner.loop = foreign_loop
        runner.queue = asyncio.Queue()
        runner.workers = worker_holder
        runner.accepting = True
        main.PROJECT_CONVERSATION_RUNNER = runner

        try:
            asyncio.run(main.shutdown_event())
            self.assertTrue(worker_stopped.wait(1), "foreign-loop worker leaked after shutdown")
        finally:
            foreign_loop.call_soon_threadsafe(foreign_loop.stop)
            loop_thread.join(1)
            foreign_loop.close()

        self.assertFalse(loop_thread.is_alive())
        self.assertTrue(worker_holder[0].cancelled())
        self.assertEqual(runner.workers, [])

    def test_media_import_reuses_source_task_and_enforces_project_backpressure(self):
        project = self.client.post("/api/project-library", json={"name": "队列"}).json()["project"]
        first_source = Path(self.temp_dir.name) / "first.bin"
        second_source = Path(self.temp_dir.name) / "second.bin"
        first_source.write_bytes(b"first")
        second_source.write_bytes(b"second")
        gate = threading.Event()
        previous_resolver = main.project_media_source_path
        previous_importer = self.store.import_media_file
        previous_limit = main.PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT
        main.project_media_source_path = lambda url: str(first_source if url.endswith("first") else second_source)
        def blocked_import(*args, **kwargs):
            gate.wait(3)
            return previous_importer(*args, **kwargs)
        self.store.import_media_file = blocked_import
        main.PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT = 1
        try:
            first = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={"source_url": "/assets/first"},
            )
            duplicate = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={"source_url": "/assets/first"},
            )
            with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                main.PROJECT_MEDIA_IMPORT_TASKS[first.json()["task"]["id"]]["updated_at"] = 1
            overflow = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={"source_url": "/assets/second"},
            )
            delete_while_importing = self.client.delete(f"/api/project-library/{project['id']}")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(duplicate.status_code, 202)
            self.assertEqual(first.json()["task"]["id"], duplicate.json()["task"]["id"])
            self.assertEqual(overflow.status_code, 429)
            self.assertEqual(delete_while_importing.status_code, 409)
            self.assertEqual(
                self.client.get(
                    f"/api/project-library/{project['id']}/media/import/{first.json()['task']['id']}"
                ).status_code,
                200,
            )
        finally:
            gate.set()
            self.store.import_media_file = previous_importer
            main.project_media_source_path = previous_resolver
            main.PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT = previous_limit
        task_id = first.json()["task"]["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.client.get(f"/api/project-library/{project['id']}/media/import/{task_id}").json()["task"]
            if task["status"] != "pending":
                break
            time.sleep(0.02)
        self.assertEqual(task["status"], "completed")

    def test_concurrent_media_import_dedupe_keeps_strict_expected_kind(self):
        project = self.client.post("/api/project-library", json={"name": "严格导入去重"}).json()["project"]
        source = Path(self.temp_dir.name) / "invalid-frame.png"
        source.write_bytes(b"not-a-real-png")
        gate = threading.Event()
        importer_entered = threading.Event()
        previous_resolver = main.project_media_source_path
        previous_importer = self.store.import_media_file
        loose_id = ""
        main.project_media_source_path = lambda _url: str(source)

        def blocked_import(*args, **kwargs):
            importer_entered.set()
            gate.wait(3)
            return previous_importer(*args, **kwargs)

        self.store.import_media_file = blocked_import
        try:
            endpoint = f"/api/project-library/{project['id']}/media/import"
            loose = self.client.post(endpoint, json={
                "source_url": "/assets/invalid-frame.png",
                "mime_type": "image/png",
            })
            self.assertEqual(loose.status_code, 202, loose.text)
            self.assertTrue(importer_entered.wait(1))
            strict = self.client.post(endpoint, json={
                "source_url": "/assets/invalid-frame.png",
                "mime_type": "image/png",
                "expected_kind": "image",
            })
            self.assertEqual(strict.status_code, 202, strict.text)
            loose_id = loose.json()["task"]["id"]
            strict_id = strict.json()["task"]["id"]
            self.assertNotEqual(strict_id, loose_id)

            deadline = time.monotonic() + 2
            strict_task = strict.json()["task"]
            while strict_task.get("status") == "pending" and time.monotonic() < deadline:
                time.sleep(0.02)
                strict_task = self.client.get(
                    f"/api/project-library/{project['id']}/media/import/{strict_id}"
                ).json()["task"]
            self.assertEqual(strict_task["status"], "failed", strict_task)
            self.assertIn("有效图片", strict_task["error"])
        finally:
            gate.set()
            with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                loose_future = main.PROJECT_MEDIA_IMPORT_FUTURES.get(loose_id)
            if loose_future is not None:
                loose_future.result(timeout=3)
            self.store.import_media_file = previous_importer
            main.project_media_source_path = previous_resolver

    def test_project_delete_and_media_import_reservation_share_one_lifecycle_lock(self):
        project = self.store.create_project("生命周期锁")
        delete_entered = threading.Event()
        release_delete = threading.Event()
        reserve_finished = threading.Event()
        reserve_errors = []
        original_trash = self.store.trash_project

        def slow_trash(project_id):
            delete_entered.set()
            self.assertTrue(release_delete.wait(2))
            return original_trash(project_id)

        def delete_project():
            main.project_lifecycle_store_call("trash_project", project["id"], block_active_import=True)

        def reserve_import():
            try:
                main.reserve_project_media_import(project["id"], "source-key")
            except Exception as exc:
                reserve_errors.append(exc)
            finally:
                reserve_finished.set()

        with patch.object(self.store, "trash_project", side_effect=slow_trash):
            deleter = threading.Thread(target=delete_project)
            reserver = threading.Thread(target=reserve_import)
            deleter.start()
            self.assertTrue(delete_entered.wait(1))
            reserver.start()
            self.assertFalse(reserve_finished.wait(0.1))
            release_delete.set()
            deleter.join(2)
            reserver.join(2)

        self.assertFalse(deleter.is_alive())
        self.assertFalse(reserver.is_alive())
        self.assertEqual(len(reserve_errors), 1)
        self.assertIsInstance(reserve_errors[0], main.ProjectNotFound)

    def test_project_media_catalog_preserves_source_and_serves_derived_preview(self):
        project = self.client.post("/api/project-library", json={"name": "媒体归属"}).json()["project"]
        source = Path(self.temp_dir.name) / "external-original.png"
        image = Image.new("RGB", (640, 320), (21, 94, 117))
        image.save(source, format="PNG")
        previous_resolver = main.project_media_source_path
        main.project_media_source_path = lambda _url: str(source)
        try:
            imported = self.client.post(
                f"/api/project-library/{project['id']}/media/import",
                json={
                    "source_url": "/assets/staged-original.png",
                    "source_locator": "file:///external/original.png",
                    "name": "原始素材.png",
                    "mime_type": "image/png",
                },
            )
        finally:
            main.project_media_source_path = previous_resolver

        self.assertEqual(imported.status_code, 202)
        task_id = imported.json()["task"]["id"]
        deadline = time.monotonic() + 3
        task = imported.json()["task"]
        while task.get("status") == "pending" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = self.client.get(
                f"/api/project-library/{project['id']}/media/import/{task_id}"
            ).json()["task"]
        self.assertEqual(task["status"], "completed")

        catalog = self.client.get(f"/api/project-library/{project['id']}/media")
        self.assertEqual(catalog.status_code, 200)
        item = catalog.json()["items"][0]
        self.assertEqual(item["asset_id"], task["media"]["asset_id"])
        self.assertEqual(item["kind"], "image")
        self.assertEqual(item["source"]["locator"], "file:///external/original.png")
        self.assertEqual(item["original_url"], f"/api/project-library/{project['id']}/media/{item['asset_id']}")
        self.assertEqual(item["preview_url"], f"/api/project-library/{project['id']}/media/{item['asset_id']}/preview")

        preview = self.client.get(item["preview_url"] + "?w=128")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/webp")
        with Image.open(io.BytesIO(preview.content)) as derived:
            self.assertLessEqual(max(derived.size), 128)

        other = self.client.post("/api/project-library", json={"name": "隔离项目"}).json()["project"]
        self.assertEqual(self.client.get(f"/api/project-library/{other['id']}/media").json(), {"items": []})

    def test_project_media_copy_to_global_streams_all_supported_media_kinds(self):
        project = self.client.post("/api/project-library", json={"name": "全局副本"}).json()["project"]
        records = [
            self.store.import_media(project["id"], b"image", "image.png", "image/png"),
            self.store.import_media(project["id"], b"video", "video.mp4", "video/mp4"),
            self.store.import_media(project["id"], b"audio", "audio.mp3", "audio/mpeg"),
        ]
        global_root = Path(self.temp_dir.name) / "global-media"
        with patch.object(main, "LOCAL_UPLOAD_DIR", str(global_root)):
            responses = [
                self.client.post(f"/api/project-library/{project['id']}/media/{record['asset_id']}/copy-to-global")
                for record in records
            ]
            global_items = self.client.get("/api/local-assets").json()["items"]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual({item["kind"] for item in global_items}, {"image", "video", "audio"})
        self.assertTrue(all(Path(global_root, item["file"]).is_file() for item in global_items))

    def test_project_media_previews_for_different_assets_build_in_parallel(self):
        projects = [self.store.create_project(name) for name in ("预览甲", "预览乙")]
        records = [
            self.store.import_media(project["id"], f"video-{index}".encode(), f"video-{index}.mp4", "video/mp4")
            for index, project in enumerate(projects)
        ]
        barrier = threading.Barrier(2)
        errors = []

        def fake_video_preview(_path, _width):
            barrier.wait(2)
            return Image.new("RGB", (320, 180), (8, 24, 40))

        def build(project, record):
            try:
                main.build_project_media_preview(project["id"], record["asset_id"], 128)
            except Exception as exc:
                errors.append(exc)

        with patch.object(main, "generate_video_preview_image", side_effect=fake_video_preview):
            threads = [threading.Thread(target=build, args=pair) for pair in zip(projects, records)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(4)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_stale_browser_revision_is_rejected_with_latest_canvas(self):
        project = self.client.post("/api/project-library", json={"name": "并发"}).json()["project"]
        first = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"base_revision": 0, "mutations": [{"op": "upsert_node", "node": {"id": "n1"}}]},
        )
        self.assertEqual(first.status_code, 200)
        stale = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"base_revision": 0, "mutations": [{"op": "upsert_node", "node": {"id": "stale"}}]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual([node["id"] for node in stale.json()["detail"]["canvas"]["nodes"]], ["n1"])

    def test_project_history_delete_returns_the_new_revision(self):
        project = self.client.post("/api/project-library", json={"name": "历史"}).json()["project"]
        saved = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"base_revision": 0, "mutations": [{"op": "set_canvas_meta", "logs": [{"id": "log-1"}]}]},
        )
        self.assertEqual(saved.status_code, 200)
        deleted = self.client.post(
            f"/api/project-library/{project['id']}/history/delete",
            json={"base_revision": 1, "log_id": "log-1"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["canvas"]["revision"], 2)
        self.assertEqual(deleted.json()["canvas"]["logs"], [])
        project_dir = self.store.root / project["id"]
        self.assertEqual(json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))["revision"], 2)
        self.assertEqual(json.loads((project_dir / "history.json").read_text(encoding="utf-8"))["records"], [])
        self.assertEqual(self.client.post(f"/api/canvases/{project['id']}/touch").status_code, 410)
        self.assertEqual(
            self.client.post(
                f"/api/canvases/{project['id']}/logs/delete",
                json={"log_id": "log-1", "base_updated_at": 0},
            ).status_code,
            410,
        )

    def test_fly_package_can_be_imported_from_empty_library(self):
        files = {
            "project.json": json.dumps({"format": "firefly-project-v1", "name": "包项目"}).encode(),
            "canvas.json": json.dumps({"format": "firefly-canvas-v1", "title": "包画布", "nodes": [], "connections": []}).encode(),
        }
        manifest = {
            "format": "firefly-package-v1",
            "version": 1,
            "files": [{"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()} for path, content in files.items()],
        }
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for path, content in files.items():
                archive.writestr(path, content)
        response = self.client.post(
            "/api/project-library/import",
            files={"file": ("project.fly", package.getvalue(), "application/zip")},
        )
        self.assertEqual(response.status_code, 200)
        project = response.json()["project"]
        self.assertEqual(project["id"], project["canvas_id"])
        self.assertEqual(self.client.get("/api/project-library").json()["projects"][0]["id"], project["id"])

    def test_fly_export_import_and_project_trash_lifecycle(self):
        project = self.client.post("/api/project-library", json={"name": "生命周期"}).json()["project"]
        media = self.store.import_media(project["id"], b"lifecycle-media", "result.mp4", "video/mp4")
        saved = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"base_revision": 0, "mutations": [
                {"op": "upsert_node", "node": {"id": "result", "images": [{"asset_id": media["asset_id"]}]}},
                {"op": "set_canvas_meta", "logs": [{"id": "run", "outputs": [{"asset_id": media["asset_id"]}]}]},
            ]},
        )
        self.assertEqual(saved.status_code, 200)

        exported = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn(".fly", exported.headers.get("content-disposition", ""))
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertIn("manifest.json", archive.namelist())
            self.assertIn(f"media/{media['filename']}", archive.namelist())

        imported = self.client.post(
            "/api/project-library/import",
            files={"file": ("lifecycle.fly", exported.content, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertNotEqual(imported.json()["project"]["id"], project["id"])

        trashed = self.client.delete(f"/api/project-library/{project['id']}")
        self.assertEqual(trashed.status_code, 200)
        self.assertNotIn(project["id"], [item["id"] for item in self.client.get("/api/project-library").json()["projects"]])
        self.assertEqual(self.client.get("/api/project-library/trash").json()["projects"][0]["id"], project["id"])

        restored = self.client.post(f"/api/project-library/trash/{project['id']}/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}").status_code, 200)

        self.assertEqual(self.client.delete(f"/api/project-library/{project['id']}").status_code, 200)
        purged = self.client.delete(f"/api/project-library/trash/{project['id']}")
        self.assertEqual(purged.status_code, 200)
        self.assertEqual(self.client.get("/api/project-library/trash").json(), {"projects": []})

    def test_fly_and_project_trash_endpoints_reject_cross_origin_requests(self):
        project = self.store.create_project("同源边界")
        hostile = {"origin": "https://hostile.example"}

        self.assertEqual(self.client.get("/api/project-library", headers=hostile).status_code, 403)
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/export", headers=hostile).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/project-library/{project['id']}", headers=hostile).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/project-library/trash", headers=hostile).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/project-library/import",
                headers=hostile,
                files={"file": ("hostile.fly", b"not-a-package", "application/zip")},
            ).status_code,
            403,
        )
        self.assertEqual(self.store.open_project(project["id"])["project"]["name"], "同源边界")
        self.store.trash_project(project["id"])
        self.assertEqual(
            self.client.post(f"/api/project-library/trash/{project['id']}/restore", headers=hostile).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/project-library/trash/{project['id']}", headers=hostile).status_code,
            403,
        )
        self.assertEqual(self.store.list_trashed_projects()[0]["id"], project["id"])

        forged_host = {"host": "hostile.example", "origin": "http://hostile.example"}
        self.assertEqual(self.client.get("/api/project-library", headers=forged_host).status_code, 403)
        self.assertIn('uvicorn.run(app, host="127.0.0.1"', Path(main.__file__).read_text(encoding="utf-8"))

    def test_fly_upload_is_limited_before_and_during_multipart_spooling(self):
        missing_content_type = self.client.post("/api/project-library/import", content=b"not-multipart")
        missing_boundary = self.client.post(
            "/api/project-library/import",
            content=b"not-a-valid-multipart-body",
            headers={"content-type": "multipart/form-data"},
        )
        self.assertEqual(missing_content_type.status_code, 400)
        self.assertEqual(missing_boundary.status_code, 400)

        with (
            patch.object(main, "FLY_MAX_REQUEST_BYTES", 1),
            patch.object(main.LimitedFlyMultipartParser, "parse", side_effect=AssertionError("parser must not run")),
        ):
            rejected_by_header = self.client.post(
                "/api/project-library/import",
                files={"file": ("too-large.fly", b"tiny", "application/zip")},
            )
        self.assertEqual(rejected_by_header.status_code, 413)

        with patch.object(main, "FLY_MAX_PACKAGE_BYTES", 8):
            rejected_while_streaming = self.client.post(
                "/api/project-library/import",
                files={"file": ("too-large.fly", b"123456789", "application/zip")},
            )
        self.assertEqual(rejected_while_streaming.status_code, 413)
        self.assertIn("20GB", rejected_while_streaming.json()["detail"])


if __name__ == "__main__":
    unittest.main()
