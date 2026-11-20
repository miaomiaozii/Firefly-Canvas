"""Tests for agent generate/media/viewport tools API endpoints (issue #123)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.routers import api_agent_tools, api_project_library
from firefly_projects import ProjectStore

# Issue #175: the quality gate builds its isolated execution workspace from
# ``git ls-files --cached --others --exclude-standard`` (tools/quality_gate.py:
# create_execution_workspace), so the gitignored ``data/api_providers.json``
# (.gitignore:22) is never copied in. A test that reads the developer's real
# platform config therefore passes locally and 400s in the gate with
# ``image_model_not_found``. This batch pins its own deterministic provider
# config instead; see AgentGenerateToolsIsolationTests for the guard.
FIXTURE_PROVIDERS = [
    {
        "id": "test-other",
        "name": "Other Test Provider",
        "base_url": "https://other.example.invalid/v1",
        "protocol": "openai",
        "enabled": True,
        "image_models": ["other-image-model"],
    },
    {
        "id": "test-offline",
        "name": "Offline Test Provider",
        "base_url": "https://offline.example.invalid/v1",
        "protocol": "openai",
        "enabled": True,
        "image_models": ["test-image-model"],
        "available_models": {"image_models": ["discovered-only"]},
    },
]


class AgentGenerateToolsTests(unittest.TestCase):
    """Test the generate/media/viewport tools API endpoints used by the Pi agent."""

    def setUp(self):
        temp_dir = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="firefly-generate-tools-tests-")))
        self.store = ProjectStore(temp_dir / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.enterContext(patch.object(main, "FIREFLY_PROJECT_STORE", self.store))
        # #175: keep routing independent of the machine's data/api_providers.json.
        providers_file = temp_dir / "api_providers.json"
        providers_file.write_text(json.dumps(FIXTURE_PROVIDERS), encoding="utf-8")
        self.enterContext(patch.object(main, "API_PROVIDERS_FILE", str(providers_file)))
        # Run the real task lifecycle without making provider requests.
        self.enterContext(patch.object(api_project_library, "run_routed_image_candidate",
                                       side_effect=RuntimeError("Offline test provider failure")))
        self.confirm = self.enterContext(patch.object(
            api_agent_tools, "request_confirmation_core", return_value={"approved": True},
        ))

        self.project_id = self.store.create_project("Generate Tools Test")["id"]
        self.actor_id = "agent-1"
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt", "text": "A glowing firefly", "x": 100, "y": 100}},
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "prompt": "A beautiful landscape", "x": 300, "y": 100, "runSettings": {"provider_id": "test-offline"}}},
                {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
                {"op": "upsert_node", "node": {"id": self.actor_id, "type": "smart-agent"}},
            ],
            base_revision=0,
        )

        self.addCleanup(self._clean_project_state)
        # Keep the request loop alive for generation and drain it before removing the store.
        self.client = self.enterContext(TestClient(main.app, base_url="http://127.0.0.1"))

    def _clean_project_state(self):
        with CANVAS_TASK_LOCK:
            for task_id in list(CANVAS_TASKS):
                if CANVAS_TASKS[task_id].get("project_id") == self.project_id:
                    del CANVAS_TASKS[task_id]
        api_agent_tools.reset_generation_counts(self.project_id)
        api_agent_tools.VIEWPORT_STATE.pop(self.project_id, None)

    def _post_tool(self, route, payload):
        return self.client.post(
            f"/api/projects/{self.project_id}/{route}",
            headers={"X-Agent-Project": self.project_id},
            params={"agent_node_id": self.actor_id},
            json=payload,
        )

    def test_trigger_generation(self):
        """generate.trigger starts generation and returns task ID."""
        response = self._post_tool("generate/trigger", {"node_id": "image-1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.confirm.assert_awaited_once()
        self.assertEqual(self.confirm.await_args.args[:3], (self.project_id, self.actor_id, "generate.trigger"))
        data = response.json()
        self.assertIn("task_id", data)
        self.assertTrue(data["task_id"].startswith("agent_img_"))
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["node_id"], "image-1")

    def test_trigger_generation_uses_model_routing(self):
        """#134/#465: route saved parameters within the selected provider (ADR 0006)."""
        revision = int(self.store.open_project(self.project_id)["canvas"].get("revision") or 0)
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {
                "id": "img-routed", "type": "smart-image", "prompt": "a test subject", "x": 0, "y": 0,
                "runSettings": {"provider_id": "test-offline", "routingMode": "auto", "ratio": "wide", "resolution": "2k"},
            }}],
            base_revision=revision,
        )

        with patch.object(main, 'route_image_generation', wraps=main.route_image_generation) as router:
            response = self._post_tool("generate/trigger", {"node_id": "img-routed"})
        self.assertEqual(response.status_code, 200, response.text)

        # Auto routes within the selected provider's checked models, without a model pin.
        router.assert_called_once()
        req, providers = router.call_args.args
        self.assertEqual([p["id"] for p in providers], ["test-offline"])
        self.assertEqual(providers[0]["image_models"], ["test-image-model"])
        self.assertNotIn("available_models", providers[0])
        self.assertEqual(router.call_args.kwargs, {"provider_id": "", "model": ""})
        self.assertEqual(req.size, "2048x1152")
        self.assertEqual(req.aspect_ratio, "16:9")
        self.assertEqual(req.resolution, "2k")
        self.assertEqual(req.prompt, "a test subject")

    def test_trigger_generation_no_prompt(self):
        """generate.trigger returns 400 if node has no prompt."""
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {"id": "empty-1", "type": "smart-image", "x": 0, "y": 0, "runSettings": {"provider_id": "test-offline"}}}],
            base_revision=1,
        )
        response = self._post_tool("generate/trigger", {"node_id": "empty-1"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "NODE_NOT_GENERATABLE")

    def test_trigger_generation_node_not_found(self):
        """generate.trigger returns 404 for missing node."""
        response = self._post_tool("generate/trigger", {"node_id": "nonexistent"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "NODE_NOT_FOUND")

    def test_get_task_status(self):
        """generate.status returns task status."""
        response = self._post_tool("generate/trigger", {"node_id": "image-1"})
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]

        task_url = f"/api/projects/{self.project_id}/generate/tasks/{task_id}"
        response = self.client.get(task_url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIn("status", data)
        if data["status"] == "cleanup_pending":
            # A fast provider failure can be visible before its journal settles.
            wait = self.client.get(f"{task_url}/wait?timeout=5")
            self.assertEqual(wait.status_code, 200, wait.text)
            response = self.client.get(task_url)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
        self.assertIn(data["status"], ["queued", "running", "succeeded", "failed"])

    def test_get_task_status_not_found(self):
        """generate.status returns 404 for missing task."""
        response = self.client.get(f"/api/projects/{self.project_id}/generate/tasks/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_wait_for_task(self):
        """generate.wait waits for task completion or timeout."""
        response = self._post_tool("generate/trigger", {"node_id": "image-1"})
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]

        # Wait with a short timeout
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/{task_id}/wait?timeout=5"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIn("status", data)

    def test_wait_for_task_timeout(self):
        """generate.wait returns timeout status if task doesn't complete."""
        # Create a task that will never complete
        task_id = "agent_img_test_timeout"
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "agent-image",
                "status": "running",
                "project_id": self.project_id,
                "node_id": "image-1",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": "",
            }

        # Wait with a very short timeout
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/{task_id}/wait?timeout=2"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertEqual(data["status"], "timeout")

    def test_media_upload(self):
        """media.upload uploads a media file."""
        from PIL import Image

        # #355: media.upload only reads from the project or user-selected directories.
        temp_image = self.store.root / self.project_id / "test_image.png"
        Image.new("RGB", (1, 1)).save(temp_image)

        response = self._post_tool("media/upload", {"file_path": str(temp_image)})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("asset_id", data)

    def test_media_upload_file_not_found(self):
        """media.upload returns 404 for missing file."""
        missing_image = self.store.root / self.project_id / "missing.png"
        response = self._post_tool("media/upload", {"file_path": str(missing_image)})
        self.assertEqual(response.status_code, 404, response.text)

    def test_media_list(self):
        """media.list lists project media."""
        response = self.client.get(f"/api/projects/{self.project_id}/media/list")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)

    def test_viewport_focus(self):
        """viewport.focus focuses on a node."""
        response = self._post_tool("viewport/focus", {"node_id": "image-1"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["node_id"], "image-1")

    def test_viewport_focus_not_found(self):
        """viewport.focus returns 404 for missing node."""
        response = self._post_tool("viewport/focus", {"node_id": "nonexistent"})
        self.assertEqual(response.status_code, 404, response.text)

    def test_viewport_zoom(self):
        """viewport.zoom sets zoom level."""
        response = self._post_tool("viewport/zoom", {"level": 1.5})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["level"], 1.5)

    def test_viewport_state(self):
        """viewport.state returns current viewport state."""
        focus = self._post_tool("viewport/focus", {"node_id": "image-1"})
        self.assertEqual(focus.status_code, 200, focus.text)
        zoom = self._post_tool("viewport/zoom", {"level": 2.0})
        self.assertEqual(zoom.status_code, 200, zoom.text)

        response = self.client.get(f"/api/projects/{self.project_id}/viewport/state")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["focus_node_id"], "image-1")
        self.assertEqual(data["scale"], 2.0)


class AgentGenerateToolsIsolationTests(unittest.TestCase):
    """#175 guard: the batch above must not depend on machine platform config.

    ``tools/quality_gate.py`` builds its isolated execution workspace with
    ``git ls-files --cached --others --exclude-standard``, which never copies the
    gitignored ``data/api_providers.json``. Pointing ``FIREFLY_RUNTIME_ROOT`` at
    an empty directory reproduces exactly that condition (``DATA_DIR`` then has
    no provider config), so this guard turns red the moment a test in this file
    starts reading the developer's real platform config again.
    """

    NESTED_ENV_FLAG = "FIREFLY_AGENT_GENERATE_TOOLS_NESTED"

    def test_batch_passes_without_api_providers_file(self):
        if os.environ.get(self.NESTED_ENV_FLAG):
            self.skipTest("nested guard run: this test is the guard itself")
        with tempfile.TemporaryDirectory(prefix="firefly-no-provider-config-") as empty_root:
            self.assertFalse((Path(empty_root) / "data" / "api_providers.json").exists())
            env = dict(os.environ)
            env["FIREFLY_RUNTIME_ROOT"] = empty_root
            # #329 守卫（main.py，11-15 起）要求隔离实例同时显式 FIREFLY_PROJECTS_ROOT，
            # 否则拒启 exit=2——本测试写于守卫之前，补齐口径（隔离项目库同样落 empty_root）。
            env["FIREFLY_PROJECTS_ROOT"] = str(Path(empty_root) / "projects")
            env[self.NESTED_ENV_FLAG] = "1"
            process = subprocess.run(
                [sys.executable, "-m", "unittest", "-b", "tests.test_agent_generate_tools"],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
            )
        output = (process.stdout + process.stderr).decode("utf-8", errors="replace")
        self.assertEqual(
            process.returncode,
            0,
            "tests/test_agent_generate_tools.py is not isolated from "
            f"data/api_providers.json:\n{output[-4000:]}",
        )



class AgentGhostWritebackGuardTests(unittest.TestCase):
    """#375: agent 生成写回必须走 require_pending——被 Ctrl+Z 撤掉的占位结果卡
    不得在任务终态写回时复活（无守卫时 complete_media_generation 会在 (0,0) 新建幽灵节点）。"""

    TINY_PNG = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-ghost-guard-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _history(self, task_id):
        return {"id": "gen-" + task_id, "task_id": task_id, "type": "online-image",
                "status": "succeeded", "prompt": "ghost", "request_payload": {"prompt": "ghost"},
                "outputs": [], "result_node_id": "gone-card"}

    def test_writeback_to_removed_card_rejected_and_no_ghost_node(self):
        from firefly_projects import InvalidMutation
        project = self.store.create_project("ghost guard")
        self.store.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "src", "type": "smart-image", "images": []}},
        ], base_revision=0)
        media = self.store.import_media(project["id"], self.TINY_PNG, "result.png", "image/png")
        task_id = "agent_img_ghost_task"
        with self.assertRaises(InvalidMutation):
            self.store.complete_image_generation(
                project["id"], "gone-card",
                [{"asset_id": media["asset_id"], "kind": "image"}],
                self._history(task_id), task_id=task_id, require_pending=True,
            )
        canvas = self.store.open_project(project["id"])["canvas"]
        self.assertNotIn("gone-card", {n.get("id") for n in canvas.get("nodes") or []},
                         "require_pending 写回被拒后画布不得出现幽灵结果卡")

    def test_writeback_to_card_without_pending_task_rejected(self):
        from firefly_projects import InvalidMutation
        project = self.store.create_project("ghost guard 2")
        self.store.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "gone-card", "type": "smart-image", "images": []}},
        ], base_revision=0)
        media = self.store.import_media(project["id"], self.TINY_PNG, "result.png", "image/png")
        with self.assertRaises(InvalidMutation):
            self.store.complete_image_generation(
                project["id"], "gone-card",
                [{"asset_id": media["asset_id"], "kind": "image"}],
                self._history("agent_img_ghost_task_2"), task_id="agent_img_ghost_task_2",
                require_pending=True,
            )
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(n for n in canvas["nodes"] if n["id"] == "gone-card")
        self.assertEqual(node.get("images") or [], [], "无 pendingTask 的卡不得被写入结果图")


if __name__ == "__main__":
    unittest.main()
