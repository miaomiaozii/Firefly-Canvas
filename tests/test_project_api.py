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
from app.project_sessions import register_project_session, unregister_project_session
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

    def test_cleanup_preview_api_returns_read_only_candidates_for_a_closed_project(self):
        project = self.store.create_project("已关闭项目")
        candidate = self.store.import_media(
            project["id"],
            b"preview-candidate",
            "failed.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        project_dir = self.store.root / project["id"]
        before = {
            path.relative_to(project_dir).as_posix(): (
                path.stat().st_mtime_ns,
                path.read_bytes() if path.is_file() else None,
            )
            for path in sorted(project_dir.rglob("*"))
        }

        response = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        )

        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["preview"]
        self.assertEqual(preview["project_id"], project["id"])
        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(preview["candidates"][0]["asset_id"], candidate["asset_id"])
        self.assertEqual(
            preview["candidates"][0]["thumbnail_url"],
            f"/api/project-library/{project['id']}/media/{candidate['asset_id']}",
        )
        after = {
            path.relative_to(project_dir).as_posix(): (
                path.stat().st_mtime_ns,
                path.read_bytes() if path.is_file() else None,
            )
            for path in sorted(project_dir.rglob("*"))
        }
        self.assertEqual(after, before)
        self.assertEqual(list((project_dir / "previews").iterdir()), [])

        thumbnail = self.client.get(preview["candidates"][0]["thumbnail_url"])
        self.assertEqual(thumbnail.status_code, 200, thumbnail.text)
        after_thumbnail = {
            path.relative_to(project_dir).as_posix(): (
                path.stat().st_mtime_ns,
                path.read_bytes() if path.is_file() else None,
            )
            for path in sorted(project_dir.rglob("*"))
        }
        self.assertEqual(after_thumbnail, before)

    def test_cleanup_preview_api_does_not_prune_terminal_conversation_sidecars(self):
        project = self.store.create_project("终态对话 sidecar")
        request_id = "cleanup-terminal-sidecar"
        self.store.apply_mutations(project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "conversation",
                "type": "smart-conversation",
                "messages": [
                    {
                        "id": "user",
                        "role": "user",
                        "status": "completed",
                        "request_id": request_id,
                    },
                    {
                        "id": "assistant",
                        "role": "assistant",
                        "status": "completed",
                        "request_id": request_id,
                    },
                ],
            },
        }], base_revision=0)
        self.store.flush(project["id"])
        sidecar_root = self.store.root / project["id"] / ".conversation-tasks"
        sidecar_root.mkdir()
        sidecar = sidecar_root / "terminal.json"
        sidecar.write_text(json.dumps({
            "project_id": project["id"],
            "node_id": "conversation",
            "request_id": request_id,
            "state": "ready",
            "ready": {
                "user_message_id": "user",
                "assistant": {"id": "assistant", "request_id": request_id},
            },
        }, ensure_ascii=False), encoding="utf-8")
        before = (sidecar.read_bytes(), sidecar.stat().st_mtime_ns)

        response = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(sidecar.is_file())
        self.assertEqual((sidecar.read_bytes(), sidecar.stat().st_mtime_ns), before)

    def test_cleanup_preview_api_rejects_active_image_generation_before_store_analysis(self):
        project = self.store.create_project("生成中项目")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS["cleanup-blocker"] = {
                "id": "cleanup-blocker",
                "type": "project-online-image",
                "project_id": project["id"],
                "status": "running",
            }
        try:
            with patch.object(
                self.store,
                "preview_project_cleanup",
                wraps=self.store.preview_project_cleanup,
            ) as preview:
                response = self.client.get(
                    f"/api/project-library/{project['id']}/cleanup-preview"
                )
        finally:
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop("cleanup-blocker", None)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("图片正在生成", response.json()["detail"])
        preview.assert_not_called()

    def test_cleanup_preview_api_does_not_prune_expired_photoshop_state(self):
        project = self.store.create_project("过期 Photoshop 状态")
        request_id = "cleanup-expired-photoshop"
        record = {
            "request_id": request_id,
            "project_id": project["id"],
            "state": "pending",
            "expires_at_ms": main.now_ms() - 1,
        }
        with main.PHOTOSHOP_ROUND_TRIP_LOCK:
            main.PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id] = record.copy()
        try:
            response = self.client.get(
                f"/api/project-library/{project['id']}/cleanup-preview"
            )
            with main.PHOTOSHOP_ROUND_TRIP_LOCK:
                after = main.PHOTOSHOP_ROUND_TRIP_REQUESTS.get(request_id)
        finally:
            with main.PHOTOSHOP_ROUND_TRIP_LOCK:
                main.PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(after, record)

    def test_cleanup_preview_api_rechecks_runtime_owners_after_store_analysis(self):
        project = self.store.create_project("分析期间出现 owner")
        original_preview = self.store.preview_project_cleanup

        def admit_owner_during_analysis(project_id):
            result = original_preview(project_id)
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS["late-generation-owner"] = {
                    "id": "late-generation-owner",
                    "type": "project-online-image",
                    "project_id": project_id,
                    "status": "running",
                }
            return result

        with patch.object(
            self.store,
            "preview_project_cleanup",
            side_effect=admit_owner_during_analysis,
        ):
            try:
                response = self.client.get(
                    f"/api/project-library/{project['id']}/cleanup-preview"
                )
            finally:
                with main.CANVAS_TASK_LOCK:
                    main.CANVAS_TASKS.pop("late-generation-owner", None)

        self.assertEqual(response.status_code, 409)
        self.assertIn("生成", response.json()["detail"])

    def test_cleanup_preview_api_rejects_pending_photoshop_round_trip(self):
        project = self.store.create_project("Photoshop 往返中")
        request_id = "cleanup-photoshop-blocker"
        with main.PHOTOSHOP_ROUND_TRIP_LOCK:
            main.PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id] = {
                "request_id": request_id,
                "project_id": project["id"],
                "source_node_id": "source",
                "source_media_id": "media",
                "state": "pending",
                "expires_at_ms": main.now_ms() + 60_000,
            }
        try:
            response = self.client.get(
                f"/api/project-library/{project['id']}/cleanup-preview"
            )
        finally:
            with main.PHOTOSHOP_ROUND_TRIP_LOCK:
                main.PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Photoshop", response.json()["detail"])

    def test_cleanup_preview_api_rejects_each_existing_runtime_owner(self):
        def assert_blocked(project_id, expected_detail):
            response = self.client.get(
                f"/api/project-library/{project_id}/cleanup-preview"
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn(expected_detail, response.json()["detail"])

        importing = self.store.create_project("导入中项目")
        with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            main.PROJECT_MEDIA_IMPORT_TASKS["cleanup-import"] = {
                "id": "cleanup-import",
                "project_id": importing["id"],
                "status": "pending",
            }
        try:
            assert_blocked(importing["id"], "媒体正在导入")
        finally:
            with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                main.PROJECT_MEDIA_IMPORT_TASKS.pop("cleanup-import", None)

        pending_handoff = self.store.create_project("待处理生成 handoff")
        pending_media = self.store.import_media(
            pending_handoff["id"],
            b"pending-generation-handoff",
            "pending.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        with main.CANVAS_TASK_LOCK:
            main.PROJECT_IMAGE_GENERATION_ORPHANS[pending_handoff["id"]] = {
                pending_media["asset_id"]
            }
        try:
            assert_blocked(pending_handoff["id"], "待处理")
        finally:
            with main.CANVAS_TASK_LOCK:
                main.PROJECT_IMAGE_GENERATION_ORPHANS.pop(pending_handoff["id"], None)

        video = self.store.create_project("视频生成中项目")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS["cleanup-video"] = {
                "id": "cleanup-video",
                "type": "project-online-video",
                "project_id": video["id"],
                "status": "running",
            }
        try:
            assert_blocked(video["id"], "视频正在生成")
        finally:
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop("cleanup-video", None)

        conversation = self.store.create_project("对话中项目")
        self.store.apply_mutations(conversation["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "conversation",
                "type": "smart-conversation",
                "messages": [{"id": "user", "role": "user", "status": "pending"}],
            },
        }], base_revision=0)
        assert_blocked(conversation["id"], "对话任务")

        durable_agent = self.store.create_project("持久 Agent 执行中项目")
        self.store.apply_mutations(durable_agent["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "durable-agent-task",
                "type": "agent-task",
                "status": "queued",
            },
        }], base_revision=0)
        assert_blocked(durable_agent["id"], "Agent 任务")

        agent = self.store.create_project("Agent 执行中项目")
        with main.PROJECT_AGENT_TASKS_LOCK:
            main.PROJECT_AGENT_TASKS["cleanup-agent"] = {
                "id": "cleanup-agent",
                "project_id": agent["id"],
                "status": "running",
            }
        try:
            assert_blocked(agent["id"], "Agent 任务")
        finally:
            with main.PROJECT_AGENT_TASKS_LOCK:
                main.PROJECT_AGENT_TASKS.pop("cleanup-agent", None)

    def test_cleanup_preview_api_rejects_an_export_in_progress(self):
        project = self.store.create_project("导出中项目")
        export_entered = threading.Event()
        release_export = threading.Event()
        export_result = []
        original_export = self.store.export_fly_to

        def blocked_export(project_id, path):
            export_entered.set()
            self.assertTrue(release_export.wait(2))
            return original_export(project_id, path)

        def run_export():
            export_result.append(
                self.client.get(f"/api/project-library/{project['id']}/export")
            )

        with patch.object(self.store, "export_fly_to", side_effect=blocked_export):
            thread = threading.Thread(target=run_export)
            thread.start()
            self.assertTrue(export_entered.wait(1))
            started = time.monotonic()
            response = self.client.get(
                f"/api/project-library/{project['id']}/cleanup-preview"
            )
            elapsed = time.monotonic() - started
            release_export.set()
            thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("正在导出", response.json()["detail"])
        self.assertLess(elapsed, 0.5)
        self.assertEqual(export_result[0].status_code, 200, export_result[0].text)

    def test_cleanup_execute_api_permanently_deletes_confirmed_candidates(self):
        project = self.store.create_project("API 永久清理")
        candidate = self.store.import_media(
            project["id"],
            b"api-cleanup-candidate",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        owned = self.store.import_media(
            project["id"],
            b"api-owned-media",
            "owned.png",
            "image/png",
            source_info={"locator": "https://example.test/owned.png", "type": "remote_url"},
        )
        preview = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        ).json()["preview"]
        payload = {
            "revision": preview["revision"],
            "ownership_proof": preview["ownership_proof"],
            "candidate_asset_ids": [item["asset_id"] for item in preview["candidates"]],
        }
        project_dir = self.store.root / project["id"]
        files_before = {
            path.relative_to(project_dir).as_posix(): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }

        response = self.client.post(
            f"/api/project-library/{project['id']}/cleanup-execute",
            json=payload,
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertEqual(result["project_id"], project["id"])
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["deleted"][0]["asset_id"], candidate["asset_id"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(
            self.client.get(
                f"/api/project-library/{project['id']}/media/{candidate['asset_id']}"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/project-library/{project['id']}/media/{owned['asset_id']}"
            ).status_code,
            200,
        )
        index = json.loads(
            (project_dir / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(index["items"]), {owned["asset_id"]})
        files_after = {
            path.relative_to(project_dir).as_posix(): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(
            sorted(set(files_before) ^ set(files_after)),
            [f"media/{candidate['asset_id']}.png"],
        )
        unchanged = set(files_before) & set(files_after)
        self.assertTrue(all(
            files_before[name] == files_after[name]
            for name in unchanged
            if name != "media/index.json"
        ))
        self.assertFalse((project_dir / "trash" / "original-media-transaction.json").exists())
        again = self.client.post(
            f"/api/project-library/{project['id']}/cleanup-execute",
            json=payload,
        )
        self.assertEqual(again.status_code, 409, again.text)
        self.assertIn("重新预览", again.json()["detail"])

    def test_cleanup_execute_api_rejects_runtime_owner_before_store_analysis(self):
        project = self.store.create_project("执行前 owner")
        candidate = self.store.import_media(
            project["id"],
            b"preexisting-owner-candidate",
            "preexisting-owner.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        preview = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        ).json()["preview"]
        candidate_path = (
            self.store.root / project["id"] / "media" / f"{candidate['asset_id']}.png"
        )
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS["cleanup-execute-blocker"] = {
                "id": "cleanup-execute-blocker",
                "type": "project-online-image",
                "project_id": project["id"],
                "status": "running",
            }
        try:
            with patch.object(
                self.store,
                "execute_project_cleanup",
                wraps=self.store.execute_project_cleanup,
            ) as execute:
                response = self.client.post(
                    f"/api/project-library/{project['id']}/cleanup-execute",
                    json={
                        "revision": preview["revision"],
                        "ownership_proof": preview["ownership_proof"],
                        "candidate_asset_ids": [candidate["asset_id"]],
                    },
                )
        finally:
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop("cleanup-execute-blocker", None)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("图片正在生成", response.json()["detail"])
        execute.assert_not_called()
        self.assertTrue(candidate_path.is_file())

    def test_cleanup_execute_lease_rejects_late_owner_without_partial_delete(self):
        project = self.store.create_project("执行期间 owner")
        candidate = self.store.import_media(
            project["id"],
            b"late-owner-candidate",
            "late-owner.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        ).json()["preview"]
        candidate_path = (
            self.store.root / project["id"] / "media" / f"{candidate['asset_id']}.png"
        )
        execute_entered = threading.Event()
        release_execute = threading.Event()
        responses = []
        original_execute = self.store.execute_project_cleanup

        def blocked_execute(project_id, **kwargs):
            execute_entered.set()
            self.assertTrue(release_execute.wait(2))
            return original_execute(project_id, **kwargs)

        def run_cleanup():
            responses.append(self.client.post(
                f"/api/project-library/{project['id']}/cleanup-execute",
                json={
                    "revision": preview["revision"],
                    "ownership_proof": preview["ownership_proof"],
                    "candidate_asset_ids": [candidate["asset_id"]],
                },
            ))

        session_token = object()
        with patch.object(
            self.store,
            "execute_project_cleanup",
            side_effect=blocked_execute,
        ):
            thread = threading.Thread(target=run_cleanup)
            thread.start()
            self.assertTrue(execute_entered.wait(1))
            try:
                with self.assertRaisesRegex(RuntimeError, "永久清理"):
                    register_project_session(session_token, project["id"])
                export_response = self.client.get(
                    f"/api/project-library/{project['id']}/export"
                )
                self.assertEqual(export_response.status_code, 409, export_response.text)
                self.assertIn("永久清理", export_response.json()["detail"])
                download_response = self.client.get(
                    f"/api/project-library/{project['id']}/media/{candidate['asset_id']}"
                )
                self.assertEqual(download_response.status_code, 409, download_response.text)
                self.assertIn("永久清理", download_response.json()["detail"])
                self.assertTrue(candidate_path.is_file())
            finally:
                unregister_project_session(session_token)
                release_execute.set()
                thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        self.assertFalse(candidate_path.exists())
        register_project_session(session_token, project["id"])
        unregister_project_session(session_token)

    def test_cleanup_execute_api_rejects_drift_with_repreview_detail(self):
        project = self.store.create_project("执行漂移")
        candidate = self.store.import_media(
            project["id"],
            b"execute-drift",
            "drift.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        ).json()["preview"]
        payload = {
            "revision": preview["revision"],
            "ownership_proof": preview["ownership_proof"],
            "candidate_asset_ids": [item["asset_id"] for item in preview["candidates"]],
        }
        base_revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.store.apply_mutations(project["id"], [{
            "op": "upsert_node",
            "node": {"id": "drift", "type": "prompt", "x": 1, "y": 2},
        }], base_revision=base_revision)

        response = self.client.post(
            f"/api/project-library/{project['id']}/cleanup-execute",
            json=payload,
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("重新预览", response.json()["detail"])
        self.assertTrue(
            (self.store.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file()
        )
        index = json.loads(
            (self.store.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertIn(candidate["asset_id"], index["items"])

    def test_cleanup_execute_api_rejects_unbound_or_arbitrary_payloads(self):
        project = self.store.create_project("无效执行负载")
        candidate = self.store.import_media(
            project["id"],
            b"payload-guard",
            "guard.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        endpoint = f"/api/project-library/{project['id']}/cleanup-execute"
        # 从未预览过的候选不能直接提交。
        unbound = self.client.post(endpoint, json={
            "revision": 0, "ownership_proof": "b" * 64, "candidate_asset_ids": ["c" * 64],
        })
        self.assertEqual(unbound.status_code, 409, unbound.text)
        self.assertIn("重新预览", unbound.json()["detail"])
        # 任意路径、未知字段、缺失字段和类型错误一律 400。
        invalid_payloads = [
            {"revision": 0, "ownership_proof": "b" * 64, "candidate_asset_ids": ["../../outside.png"]},
            {"revision": 0, "ownership_proof": "b" * 64, "candidate_asset_ids": [], "path": "C:/evil"},
            {"revision": 0, "candidate_asset_ids": []},
            {"revision": "0", "ownership_proof": 123, "candidate_asset_ids": "x"},
            ["not", "an", "object"],
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(endpoint, json=payload)
                self.assertEqual(response.status_code, 400, response.text)
        missing = self.client.post("/api/project-library/nope/cleanup-execute", json={
            "revision": 0, "ownership_proof": "b" * 64, "candidate_asset_ids": [],
        })
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertTrue(
            (self.store.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file()
        )
        index = json.loads(
            (self.store.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertIn(candidate["asset_id"], index["items"])

    def test_cleanup_execute_api_rejects_each_existing_runtime_owner(self):
        def assert_blocked(project_id, expected_detail):
            with patch.object(
                self.store,
                "execute_project_cleanup",
                wraps=self.store.execute_project_cleanup,
            ) as execute:
                response = self.client.post(
                    f"/api/project-library/{project_id}/cleanup-execute",
                    json={"revision": 0, "ownership_proof": "a" * 64, "candidate_asset_ids": []},
                )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn(expected_detail, response.json()["detail"])
            execute.assert_not_called()

        importing = self.store.create_project("执行导入中")
        with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            main.PROJECT_MEDIA_IMPORT_TASKS["execute-import"] = {
                "id": "execute-import",
                "project_id": importing["id"],
                "status": "pending",
            }
        try:
            assert_blocked(importing["id"], "媒体正在导入")
        finally:
            with main.PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                main.PROJECT_MEDIA_IMPORT_TASKS.pop("execute-import", None)

        image = self.store.create_project("执行图片生成中")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS["execute-image"] = {
                "id": "execute-image",
                "type": "project-online-image",
                "project_id": image["id"],
                "status": "running",
            }
        try:
            assert_blocked(image["id"], "图片正在生成")
        finally:
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop("execute-image", None)

        video = self.store.create_project("执行视频生成中")
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS["execute-video"] = {
                "id": "execute-video",
                "type": "project-online-video",
                "project_id": video["id"],
                "status": "running",
            }
        try:
            assert_blocked(video["id"], "视频正在生成")
        finally:
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop("execute-video", None)

        handoff = self.store.create_project("执行 handoff 中")
        with main.CANVAS_TASK_LOCK:
            main.PROJECT_IMAGE_GENERATION_ORPHANS[handoff["id"]] = {"a" * 64}
        try:
            assert_blocked(handoff["id"], "待处理")
        finally:
            with main.CANVAS_TASK_LOCK:
                main.PROJECT_IMAGE_GENERATION_ORPHANS.pop(handoff["id"], None)

        conversation = self.store.create_project("执行对话中")
        self.store.apply_mutations(conversation["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "conversation",
                "type": "smart-conversation",
                "messages": [{"id": "user", "role": "user", "status": "pending"}],
            },
        }], base_revision=0)
        assert_blocked(conversation["id"], "对话任务")

        agent = self.store.create_project("执行 Agent 中")
        with main.PROJECT_AGENT_TASKS_LOCK:
            main.PROJECT_AGENT_TASKS["execute-agent"] = {
                "id": "execute-agent",
                "project_id": agent["id"],
                "status": "running",
            }
        try:
            assert_blocked(agent["id"], "Agent 任务")
        finally:
            with main.PROJECT_AGENT_TASKS_LOCK:
                main.PROJECT_AGENT_TASKS.pop("execute-agent", None)

        photoshop = self.store.create_project("执行 PS 中")
        with main.PHOTOSHOP_ROUND_TRIP_LOCK:
            main.PHOTOSHOP_ROUND_TRIP_REQUESTS["execute-ps"] = {
                "request_id": "execute-ps",
                "project_id": photoshop["id"],
                "state": "pending",
                "expires_at_ms": main.now_ms() + 60_000,
            }
        try:
            assert_blocked(photoshop["id"], "Photoshop")
        finally:
            with main.PHOTOSHOP_ROUND_TRIP_LOCK:
                main.PHOTOSHOP_ROUND_TRIP_REQUESTS.pop("execute-ps", None)

    def test_cleanup_execute_api_requires_project_websocket_sessions_to_close(self):
        project = self.store.create_project("执行中的项目")
        candidate = self.store.import_media(
            project["id"],
            b"session-guard",
            "session.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        preview = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        ).json()["preview"]
        payload = {
            "revision": preview["revision"],
            "ownership_proof": preview["ownership_proof"],
            "candidate_asset_ids": [item["asset_id"] for item in preview["candidates"]],
        }
        endpoint = f"/api/project-library/{project['id']}/cleanup-execute"

        with self.client.websocket_connect(
            f"/ws/stats?client_id=canvas_execute&project_id={project['id']}"
        ) as socket:
            socket.receive_json()
            blocked = self.client.post(endpoint, json=payload)
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("仍在打开", blocked.json()["detail"])
        self.assertTrue(
            (self.store.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file()
        )

        released = self.client.post(endpoint, json=payload)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["result"]["deleted_count"], 1)

    def test_cleanup_execute_api_rejects_an_export_in_progress(self):
        project = self.store.create_project("执行导出中")
        export_entered = threading.Event()
        release_export = threading.Event()
        export_result = []
        original_export = self.store.export_fly_to

        def blocked_export(project_id, path):
            export_entered.set()
            self.assertTrue(release_export.wait(2))
            return original_export(project_id, path)

        def run_export():
            export_result.append(
                self.client.get(f"/api/project-library/{project['id']}/export")
            )

        with patch.object(self.store, "export_fly_to", side_effect=blocked_export):
            with patch.object(
                self.store,
                "execute_project_cleanup",
                wraps=self.store.execute_project_cleanup,
            ) as execute:
                thread = threading.Thread(target=run_export)
                thread.start()
                self.assertTrue(export_entered.wait(1))
                started = time.monotonic()
                response = self.client.post(
                    f"/api/project-library/{project['id']}/cleanup-execute",
                    json={"revision": 0, "ownership_proof": "a" * 64, "candidate_asset_ids": []},
                )
                elapsed = time.monotonic() - started
                release_export.set()
                thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("正在导出", response.json()["detail"])
        self.assertLess(elapsed, 0.5)
        self.assertEqual(export_result[0].status_code, 200, export_result[0].text)
        execute.assert_not_called()

    def test_project_library_marks_cleanup_available_only_after_canvas_session_closes(self):
        project = self.store.create_project("项目列表关闭状态")

        with self.client.websocket_connect(
            f"/ws/stats?client_id=canvas_list&project_id={project['id']}"
        ) as socket:
            socket.receive_json()
            opened = self.client.get("/api/project-library").json()["projects"]
            opened_project = next(item for item in opened if item["id"] == project["id"])
            self.assertFalse(opened_project["cleanup_preview_available"])

        closed = self.client.get("/api/project-library").json()["projects"]
        closed_project = next(item for item in closed if item["id"] == project["id"])
        self.assertTrue(closed_project["cleanup_preview_available"])

    def test_cleanup_preview_api_requires_project_websocket_sessions_to_close(self):
        project = self.store.create_project("打开中的项目")
        endpoint = f"/api/project-library/{project['id']}/cleanup-preview"

        with self.client.websocket_connect(
            f"/ws/stats?client_id=canvas_cleanup&project_id={project['id']}"
        ) as socket:
            socket.receive_json()
            blocked = self.client.get(endpoint)
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("仍在打开", blocked.json()["detail"])

        released = self.client.get(endpoint)
        self.assertEqual(released.status_code, 200, released.text)

    def test_duplicate_project_api_preserves_project_owned_media_and_scope(self):
        source = self.store.create_project("API 原项目")
        media = self.store.import_media(
            source["id"],
            b"unreferenced-project-media",
            "owned.png",
            "image/png",
            source_info={"locator": "https://example.test/owned.png?credential=hidden", "type": "remote_url"},
        )

        response = self.client.post(f"/api/project-library/{source['id']}/duplicate")

        self.assertEqual(response.status_code, 200)
        duplicate = response.json()["project"]
        self.assertNotEqual(duplicate["id"], source["id"])
        self.assertEqual(duplicate["name"], "API 原项目 副本")
        copied_media = self.client.get(f"/api/project-library/{duplicate['id']}/media").json()["items"]
        self.assertEqual([item["asset_id"] for item in copied_media], [media["asset_id"]])
        self.assertEqual(copied_media[0]["source"]["locator"], "https://example.test/owned.png")
        self.assertEqual(
            self.client.get(f"/api/project-library/{duplicate['id']}/media/{media['asset_id']}").content,
            b"unreferenced-project-media",
        )
        self.assertEqual(self.client.delete(f"/api/project-library/{duplicate['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/project-library/{source['id']}").status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/project-library/{source['id']}/media/{media['asset_id']}").content,
            b"unreferenced-project-media",
        )

    def test_download_output_serves_project_media_as_an_attachment(self):
        project = self.store.create_project("下载项目媒体")
        media = self.store.import_media(project["id"], b"project-image", "generated.png", "image/png")
        media_url = f"/api/project-library/{project['id']}/media/{media['asset_id']}"

        response = self.client.get(
            "/api/download-output",
            params={"url": media_url, "name": "cat.png"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"project-image")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("cat.png", response.headers["content-disposition"])
        preview_response = self.client.get(
            "/api/download-output",
            params={"url": f"{media_url}/preview?w=512", "name": "preview.png"},
        )
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.content, b"project-image")
        self.assertIn("attachment", preview_response.headers["content-disposition"])
        self.assertIn("preview.png", preview_response.headers["content-disposition"])
        self.assertEqual(
            self.client.get(
                "/api/download-output",
                params={"url": f"{media_url}/thumbnail", "name": "thumbnail.png"},
            ).status_code,
            400,
        )

    def test_active_project_media_download_blocks_cleanup_until_response_finishes(self):
        from starlette.responses import FileResponse

        project = self.store.create_project("下载 owner")
        media = self.store.import_media(
            project["id"],
            b"active-download-candidate",
            "active-download.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        media_url = f"/api/project-library/{project['id']}/media/{media['asset_id']}"
        candidate_path = (
            self.store.root / project["id"] / "media" / f"{media['asset_id']}.png"
        )
        response_started = threading.Event()
        allow_response = threading.Event()
        responses = []
        original_call = FileResponse.__call__

        async def blocking_file_response(response, scope, receive, send):
            response_started.set()
            await asyncio.to_thread(allow_response.wait, 5)
            return await original_call(response, scope, receive, send)

        with patch.object(FileResponse, "__call__", blocking_file_response):
            thread = threading.Thread(
                target=lambda: responses.append(
                    self.client.get(
                        "/api/download-output",
                        params={"url": media_url, "name": "active-download.png"},
                    )
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(response_started.wait(5), "project media response did not start")
            try:
                blocked = self.client.get(
                    f"/api/project-library/{project['id']}/cleanup-preview"
                )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertIn("下载", blocked.json()["detail"])
                self.assertTrue(candidate_path.is_file())
            finally:
                allow_response.set()
                thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        self.assertEqual(responses[0].content, b"active-download-candidate")
        released = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        )
        self.assertEqual(released.status_code, 200, released.text)

    def test_failed_project_media_response_releases_download_owner(self):
        from starlette.responses import FileResponse

        project = self.store.create_project("下载异常释放")
        media = self.store.import_media(
            project["id"],
            b"failed-download-candidate",
            "failed-download.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        media_url = f"/api/project-library/{project['id']}/media/{media['asset_id']}"

        async def fail_file_response(_response, _scope, _receive, _send):
            raise RuntimeError("simulated client disconnect")

        with patch.object(FileResponse, "__call__", fail_file_response):
            with self.assertRaisesRegex(RuntimeError, "simulated client disconnect"):
                self.client.get(media_url)

        released = self.client.get(
            f"/api/project-library/{project['id']}/cleanup-preview"
        )
        self.assertEqual(released.status_code, 200, released.text)

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


class MediaCleanupEndpointTests(unittest.TestCase):
    """issue #56：未使用媒体检测 / 回收区端点契约。"""

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

    def _project(self, name="清理项目"):
        return self.client.post("/api/project-library", json={"name": name}).json()["project"]

    def _import(self, project_id, data, name, mime="image/png"):
        return self.store.import_media(project_id, data, name, mime)

    def _patch_canvas(self, project_id, mutations, base_revision):
        return self.client.patch(
            f"/api/project-library/{project_id}/canvas",
            json={"mutations": mutations, "base_revision": base_revision},
        )

    def test_media_unused_lists_only_unreferenced_assets(self):
        project = self._project()
        used = self._import(project["id"], b"used-bytes", "used.png")
        unused = self._import(project["id"], b"unused-bytes", "unused.png")
        # 画布节点引用 used（嵌套 asset_id），不引用 unused
        changed = self._patch_canvas(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "smart-image", "images": [{"asset_id": used["asset_id"], "url": ""}]}},
        ], 0)
        self.assertEqual(changed.status_code, 200)

        response = self.client.get(f"/api/project-library/{project['id']}/media/unused")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual([item["asset_id"] for item in items], [unused["asset_id"]])
        self.assertEqual(items[0]["kind"], "image")
        self.assertEqual(items[0]["name"], "unused.png")

    def test_media_unused_empty_when_all_referenced(self):
        project = self._project()
        a = self._import(project["id"], b"a-bytes", "a.png")
        b = self._import(project["id"], b"b-bytes", "b.png")
        # 两个节点分别引用 a / b，含 T3 风格的局部编辑引用字段
        changed = self._patch_canvas(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "smart-image", "images": [{"asset_id": a["asset_id"]}]}},
            {"op": "upsert_node", "node": {"id": "n2", "type": "smart-local-edit", "input_asset_id": b["asset_id"]}},
        ], 0)
        self.assertEqual(changed.status_code, 200)
        items = self.client.get(f"/api/project-library/{project['id']}/media/unused").json()["items"]
        self.assertEqual(items, [])

    def test_media_unused_excludes_hard_deleted_asset(self):
        project = self._project()
        orphan = self._import(project["id"], b"orphan-bytes", "orphan.png")
        revision = self.client.get(f"/api/project-library/{project['id']}").json()["canvas"]["revision"]
        deleted = self.client.request(
            "DELETE",
            f"/api/project-library/{project['id']}/media/{orphan['asset_id']}",
            json={"base_revision": revision},
        )
        self.assertIn(deleted.status_code, (200, 404))
        items = self.client.get(f"/api/project-library/{project['id']}/media/unused").json()["items"]
        self.assertEqual(items, [])

    def test_trash_media_and_unused_404_for_missing_project(self):
        self.assertEqual(self.client.get("/api/project-library/nope/media/unused").status_code, 404)
        self.assertEqual(self.client.get("/api/project-library/nope/trash/media").status_code, 404)

    def test_trash_media_listing_shape_and_purge_by_asset(self):
        project = self._project()
        trash_dir = Path(self.temp_dir.name) / "projects" / project["id"] / "trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        far_future = 4102444800000  # 2100-01-01，不会被 7 天自动清理移除
        (trash_dir / "index.json").write_text(
            json.dumps({"items": [
                {"asset_id": "a1", "deletion_id": "d1", "deleted_at": 1, "expires_at": far_future, "name": "原图A"},
                {"asset_id": "a2", "deletion_id": "d2", "deleted_at": 1, "expires_at": far_future, "name": "原图B"},
            ]}),
            encoding="utf-8",
        )
        listed = self.client.get(f"/api/project-library/{project['id']}/trash/media")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["asset_id"] for item in listed.json()["items"]], ["a1", "a2"])

        purged = self.client.delete(f"/api/project-library/{project['id']}/trash/media", params={"asset_id": "a1"})
        self.assertEqual(purged.status_code, 200)
        remaining = self.client.get(f"/api/project-library/{project['id']}/trash/media").json()["items"]
        self.assertEqual([item["asset_id"] for item in remaining], ["a2"])

    def test_archive_media_moves_unreferenced_to_trash(self):
        project = self._project()
        asset = self._import(project["id"], b"arc-bytes", "arc.png")
        response = self.client.post(
            f"/api/project-library/{project['id']}/media/{asset['asset_id']}/archive",
            json={"reason": "node-delete"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "archived": asset["asset_id"]})
        unused = self.client.get(f"/api/project-library/{project['id']}/media/unused").json()["items"]
        self.assertEqual([item["asset_id"] for item in unused], [])
        trash = self.client.get(f"/api/project-library/{project['id']}/trash/media").json()["items"]
        self.assertEqual([item["asset_id"] for item in trash], [asset["asset_id"]])
        self.assertEqual(trash[0]["reason"], "node-delete")

    def test_archive_media_referenced_returns_409(self):
        project = self._project()
        asset = self._import(project["id"], b"arc-ref-bytes", "arc-ref.png")
        changed = self._patch_canvas(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "smart-image", "images": [{"asset_id": asset["asset_id"]}]}},
        ], 0)
        self.assertEqual(changed.status_code, 200)
        response = self.client.post(
            f"/api/project-library/{project['id']}/media/{asset['asset_id']}/archive",
            json={"reason": "node-delete"},
        )
        self.assertEqual(response.status_code, 409)
        # 仍保留在库（不出现在未使用列表，因为被节点引用）
        unused = self.client.get(f"/api/project-library/{project['id']}/media/unused").json()["items"]
        self.assertNotIn(asset["asset_id"], [item["asset_id"] for item in unused])
        self.assertTrue((Path(self.temp_dir.name) / "projects" / project["id"] / "media").is_dir())

    def test_archive_media_missing_project_or_asset_404(self):
        self.assertEqual(
            self.client.post("/api/project-library/nope/media/x/archive", json={}).status_code,
            404,
        )
        project = self._project()
        self.assertEqual(
            self.client.post(f"/api/project-library/{project['id']}/media/nope/archive", json={}).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
