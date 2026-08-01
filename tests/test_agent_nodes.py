import base64
import asyncio
import io
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


def tiny_png() -> dict:
    stream = io.BytesIO()
    Image.new("RGB", (4, 3), (42, 123, 231)).save(stream, format="PNG")
    return {"type": "b64", "value": base64.b64encode(stream.getvalue()).decode("ascii"), "mime_type": "image/png"}


def tiny_mp4() -> dict:
    # validate_generated_video intentionally checks the container signature, so
    # this tiny deterministic ISO-BMFF fixture is sufficient for adapter tests.
    content = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x00\x00\x08moovvide"
    return {"type": "b64", "value": base64.b64encode(content).decode("ascii"), "mime_type": "video/mp4"}


class ProjectAgentContractTests(unittest.TestCase):
    @staticmethod
    def _reset_agent_runtime_state():
        """Keep process-local Agent registries and locks isolated per test."""
        with main.PROJECT_AGENT_TASKS_LOCK:
            futures = list(main.PROJECT_AGENT_FUTURES.values())
        for future in futures:
            if future is None or future.done():
                continue
            try:
                # Workers should have reached a terminal durable state before
                # the fixture's ProjectStore is closed.  A bounded wait avoids
                # carrying a hung provider call into the next test.
                future.result(timeout=6)
            except Exception:
                future.cancel()
        with main.PROJECT_AGENT_TASKS_LOCK:
            main.PROJECT_AGENT_TASKS.clear()
            main.PROJECT_AGENT_FUTURES.clear()
            main.PROJECT_AGENT_RUNNING_IDS.clear()
        with main.PROJECT_AGENT_EXECUTION_LOCKS_GUARD:
            main.PROJECT_AGENT_EXECUTION_LOCKS.clear()

    def setUp(self):
        self._reset_agent_runtime_state()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()

    def tearDown(self):
        self._reset_agent_runtime_state()
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
        self.temp_dir.cleanup()

    def seed(self):
        project = self.store.create_project("Agent contract")
        image = self.store.import_media(project["id"], b"agent-image", "reference.png", "image/png")
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "text": "一只发光的萤火虫"}},
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "images": [{"asset_id": image["asset_id"], "kind": "image", "name": "reference.png"}]}},
                {"op": "upsert_node", "node": {"id": "agent-1", "type": "smart-agent", "title": "Agent", "agent_plan": None}},
                {"op": "upsert_edge", "edge": {"id": "prompt-agent", "from": "prompt-1", "to": "agent-1", "kind": "input"}},
                {"op": "upsert_edge", "edge": {"id": "image-agent", "from": "image-1", "to": "agent-1", "kind": "input"}},
            ],
            base_revision=0,
        )
        return project, image

    def plan_payload(self, revision, **overrides):
        payload = {
            "goal": "为这张参考图生成一张海报",
            "tasks": [
                {
                    "kind": "image",
                    "model_type": "online-image",
                    "count": 2,
                    "params": {
                        "size": "1024x1024",
                        "width": 1024,
                        "height": 1024,
                        "aspect_ratio": "1:1",
                        "resolution": "1k",
                        "quality": "high",
                        "media_type": "image",
                    },
                }
            ],
            "base_revision": revision,
            "request_id": "agent-plan-request-1",
        }
        payload.update(overrides)
        return payload

    def wait(self, task_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            data = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}")
            if data.status_code == 200 and data.json().get("status") in {"succeeded", "failed"}:
                return data.json()
            time.sleep(0.02)
        self.fail("agent task did not finish")

    def test_plan_is_visible_and_never_calls_adapter_before_confirmation(self):
        self.project, image = self.seed()
        adapter = AsyncMock()
        revision = self.store.canvas(self.project["id"])["revision"]
        with patch.object(main, "agent_image_adapter", adapter), patch.object(main, "agent_video_adapter", adapter):
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision),
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["plan"]["status"], "awaiting_confirmation")
        self.assertEqual(body["plan"]["tasks"][0]["count"], 2)
        self.assertEqual(body["plan"]["tasks"][0]["params"]["size"], "1024x1024")
        self.assertEqual(body["plan"]["inputs"]["prompt"], "一只发光的萤火虫")
        self.assertEqual(body["plan"]["inputs"]["media"][0]["asset_id"], image["asset_id"])
        adapter.assert_not_awaited()
        reopened = self.store.open_project(self.project["id"])["canvas"]
        agent = next(node for node in reopened["nodes"] if node["id"] == "agent-1")
        self.assertEqual(agent["agent_plan"]["plan_id"], body["plan"]["plan_id"])

    def test_concurrent_same_request_id_plan_is_idempotent_after_canvas_cas(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        body = self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}])
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan", json=body), range(2)))
        self.assertTrue(all(response.status_code == 200 for response in responses), [response.text for response in responses])
        self.assertEqual(len({response.json()["plan"]["plan_id"] for response in responses}), 1)

    def test_confirm_executes_only_planned_task_and_is_idempotent(self):
        self.project, image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        image_adapter = AsyncMock(return_value={"images": [tiny_png(), tiny_png()]})
        with patch.object(main, "agent_image_adapter", image_adapter):
            planned = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision),
            ).json()
            execution = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": planned["plan"]["plan_id"], "execution_id": "agent-execution-1"},
            )
            self.assertEqual(execution.status_code, 202, execution.text)
            task_id = execution.json()["tasks"][0]["task_id"]
            duplicate = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": planned["plan"]["plan_id"], "execution_id": "agent-execution-1"},
            )
            self.assertEqual(duplicate.status_code, 202, duplicate.text)
            task = self.wait(task_id)
        self.assertEqual(task["status"], "succeeded", task)
        image_adapter.assert_awaited_once()
        request = image_adapter.await_args.args[0]
        self.assertEqual(request["project_id"], self.project["id"])
        self.assertEqual(request["references"][0]["asset_id"], image["asset_id"])
        self.assertEqual(request["params"]["size"], "1024x1024")
        opened = self.store.open_project(self.project["id"])["canvas"]
        agent = next(node for node in opened["nodes"] if node["id"] == "agent-1")
        tasks = [node for node in opened["nodes"] if node.get("type") == "agent-task"]
        results = [node for node in opened["nodes"] if node.get("agent_task_id") == task_id and node.get("type") == "smart-image"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(agent["agent_plan"]["status"], "completed")
        self.assertEqual(opened["logs"][-1]["type"], "agent-image")

    def test_default_image_adapter_uses_existing_project_generation_boundary(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        generated = AsyncMock(return_value=(tiny_png(), {}))
        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})
        # Do not replace agent_image_adapter: this proves the production seam
        # delegates to the existing route/generator without contacting a real
        # provider.
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", generated):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "default-adapter-execution"},
            )
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        generated.assert_awaited_once()
        opened = self.store.open_project(self.project["id"])["canvas"]
        self.assertEqual(opened["logs"][-1]["type"], "agent-image")

    def test_default_adapter_without_provider_fails_visibly_and_does_not_succeed(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        with patch.object(main, "load_api_providers", return_value=[]):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "default-no-provider"},
            )
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "failed", task)
        self.assertIn("找不到", task["error"])
        self.assertEqual(self.store.open_project(self.project["id"])["canvas"]["logs"][-1]["status"], "failed")

    def test_only_direct_same_project_inputs_are_admitted(self):
        self.project, image = self.seed()
        other = self.store.create_project("Other")
        foreign = self.store.import_media(other["id"], b"foreign", "foreign.png", "image/png")
        revision = self.store.canvas(self.project["id"])["revision"]
        response = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(revision, tasks=[{
                **self.plan_payload(revision)["tasks"][0],
                "reference_asset_ids": [foreign["asset_id"]],
            }]),
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "agent_reference_not_connected")
        # A group edge is intentionally not traversed.  The direct image input
        # remains the only admitted media when planning normally.
        normal = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(revision),
        )
        self.assertEqual(normal.status_code, 200, normal.text)
        inputs = normal.json()["plan"]["inputs"]
        self.assertEqual([item["asset_id"] for item in inputs["media"]], [image["asset_id"]])

    def test_stale_plan_revision_is_409_and_invalid_task_is_rejected(self):
        self.project, _image = self.seed()
        with patch.object(main, "agent_image_adapter", AsyncMock()) as adapter:
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(0),
            )
            self.assertEqual(response.status_code, 409, response.text)
            revision = self.store.canvas(self.project["id"])["revision"]
            bad = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "delete", "count": 1, "params": {}}]),
            )
            self.assertEqual(bad.status_code, 400, bad.text)
            external = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {"url": "https://example.invalid/foreign.png"}}]),
            )
            self.assertEqual(external.status_code, 400, external.text)
            adapter.assert_not_awaited()

    def test_execute_recomputes_hash_after_durable_task_patch(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            opened = self.store.open_project(self.project["id"])["canvas"]
            agent = next(node for node in opened["nodes"] if node["id"] == "agent-1")
            tampered = dict(agent["agent_plan"])
            tampered["tasks"] = [dict(tampered["tasks"][0], params={"quality": "low", "operation": "delete"})]
            self.store.apply_mutations(
                self.project["id"],
                [{"op": "upsert_node", "node": dict(agent, agent_plan=tampered)}],
                base_revision=opened["revision"],
            )
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "tampered-execution"},
            )
        self.assertEqual(response.status_code, 409, response.text)
        adapter.assert_not_awaited()

    def test_unknown_agent_parameter_and_forbidden_value_are_rejected(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        for params in ({"operation": "delete"}, {"action": "photoshop"}, {"projectId": "other"}, {"path": "..\\secret.png"}):
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": params}]),
            )
            self.assertEqual(response.status_code, 400, (params, response.text))

    def test_existing_asset_adapter_output_is_rejected_and_partial_media_is_cleaned(self):
        self.project, image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(return_value={"images": [tiny_png(), {"asset_id": image["asset_id"]}]})
        with patch.object(main, "agent_image_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 2, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "existing-output-execution"},
            )
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "failed", task)
        media = self.store.list_media(self.project["id"])
        self.assertEqual([item["asset_id"] for item in media], [image["asset_id"]])
        self.assertFalse(any(item.get("outputs") for item in self.store.open_project(self.project["id"])["canvas"].get("logs") or [] if item.get("status") == "failed"))

    def test_hung_adapter_times_out_and_releases_task(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]

        async def hung(_request):
            await asyncio.sleep(5)

        adapter = AsyncMock(side_effect=hung)
        with patch.object(main, "agent_image_adapter", adapter), patch.object(main, "PROJECT_AGENT_TASK_TIMEOUT_SECONDS", 1):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "hung-execution"},
            )
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "failed", task)
        self.assertIn("超时", task["error"])
        self.assertEqual(self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/plan").json()["plan"]["status"], "failed")
        adapter.assert_awaited_once()

    def test_project_delete_is_blocked_while_agent_task_runs(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        generated = AsyncMock(return_value=(tiny_png(), {}))

        async def slow(_request, *_args, **_kwargs):
            await asyncio.sleep(3)
            return (tiny_png(), {})

        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", AsyncMock(side_effect=slow)):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "delete-while-running"},
            )
            task_id = response.json()["tasks"][0]["task_id"]
            deleted = self.client.delete(f"/api/project-library/{self.project['id']}")
            self.assertEqual(deleted.status_code, 409, deleted.text)
            self.assertIn("Agent", deleted.json()["detail"])
            task = self.wait(task_id)
        self.assertEqual(task["status"], "succeeded", task)
        deleted = self.client.delete(f"/api/project-library/{self.project['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

    def test_failed_adapter_is_visible_and_not_retried_by_duplicate_execution(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(side_effect=RuntimeError("fake provider failed"))
        with patch.object(main, "agent_image_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "failed-execution"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait(response.json()["tasks"][0]["task_id"])
            duplicate = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "failed-execution"},
            )
        self.assertEqual(task["status"], "failed")
        adapter.assert_awaited_once()
        self.assertIn("fake provider failed", task["error"])
        self.assertEqual(self.store.open_project(self.project["id"])["canvas"]["logs"][-1]["status"], "failed")
        self.assertEqual(duplicate.json()["tasks"][0]["task_id"], task["task_id"])

    def test_video_task_uses_video_adapter_and_archives_video_history(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(return_value={"videos": [tiny_mp4()]})
        with patch.object(main, "agent_video_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{
                    "kind": "video", "model_type": "online-video", "count": 1,
                    "params": {"duration": 4, "aspect_ratio": "16:9"},
                }]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "video-execution"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        adapter.assert_awaited_once()
        request = adapter.await_args.args[0]
        self.assertEqual(request["kind"], "video")
        self.assertEqual(request["params"]["duration"], 4)
        opened = self.store.open_project(self.project["id"])["canvas"]
        result = next(node for node in opened["nodes"] if node.get("agent_task_id") == task["task_id"])
        self.assertEqual(result["outputKind"], "video")
        self.assertEqual(result["images"][0]["kind"], "video")
        self.assertEqual(opened["logs"][-1]["type"], "agent-video")

    def test_default_video_adapter_uses_existing_project_video_executor(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-video", to_dict=lambda: {"provider_id": "fake", "model": "fake-video"})
        executor = AsyncMock(return_value=([tiny_mp4()], {}))
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "preflight_project_video_providers", AsyncMock(return_value=[{"id": "fake", "enabled": True}])), patch.object(main, "route_video_generation", return_value=route), patch.object(main, "execute_project_video_generation", executor):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "video", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "default-video-execution"},
            )
            task = self.wait(response.json()["tasks"][0]["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        executor.assert_awaited_once()
        self.assertEqual(self.store.open_project(self.project["id"])["canvas"]["logs"][-1]["type"], "agent-video")

    def test_multi_task_plan_executes_every_task_and_promotes_params_prompt(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        generated = AsyncMock(return_value=(tiny_png(), {}))
        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", generated):
            planned_response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[
                    {"kind": "prompt", "count": 1, "params": {"prompt": "多任务提示词甲"}},
                    {"kind": "image", "count": 1, "params": {"prompt": "多任务提示词乙"}},
                ]),
            )
            self.assertEqual(planned_response.status_code, 200, planned_response.text)
            plan = planned_response.json()["plan"]
            self.assertEqual(len(plan["tasks"]), 2)
            execute = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "multi-task-execution"},
            )
            self.assertEqual(execute.status_code, 202, execute.text)
            self.assertEqual(execute.json()["status"], "running")
            self.assertEqual(len(execute.json()["tasks"]), 2)
            for task in execute.json()["tasks"]:
                settled = self.wait(task["task_id"])
                self.assertEqual(settled["status"], "succeeded", settled)
            opened = self.store.open_project(self.project["id"])["canvas"]
            tasks = [node for node in opened["nodes"] if node.get("type") == "agent-task"]
            self.assertEqual(len(tasks), 2)
            prompt_nodes = [node for node in opened["nodes"] if node.get("type") == "smart-prompt" and node.get("agent_task_id")]
            self.assertEqual(len(prompt_nodes), 1)
            self.assertIn("多任务提示词甲", str(prompt_nodes[0].get("text") or ""))
            generated.assert_awaited_once()

    def test_prompt_task_creates_unconnected_prompt_node_without_adapter(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        image_adapter = AsyncMock()
        video_adapter = AsyncMock()
        with patch.object(main, "agent_image_adapter", image_adapter), patch.object(main, "agent_video_adapter", video_adapter):
            planned_response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{
                    "kind": "prompt", "count": 1,
                    "prompt": "局部霓虹灯海报，保持留白",
                    "params": {"style": "editorial"},
                }]),
            )
            self.assertEqual(planned_response.status_code, 200, planned_response.text)
            plan = planned_response.json()["plan"]
            self.assertEqual(plan["tasks"][0]["kind"], "prompt")
            self.assertEqual(plan["tasks"][0]["model_type"], "agent-prompt-node")
            execute = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "prompt-execution"},
            )
            self.assertEqual(execute.status_code, 202, execute.text)
            task_id = execute.json()["tasks"][0]["task_id"]
            task = self.wait(task_id)
            duplicate = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "prompt-execution"},
            )
            self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertEqual(task["status"], "succeeded", task)
        image_adapter.assert_not_awaited()
        video_adapter.assert_not_awaited()
        opened = self.store.open_project(self.project["id"])["canvas"]
        result = next(node for node in opened["nodes"] if node.get("agent_task_id") == task_id)
        self.assertEqual(result["type"], "smart-prompt")
        self.assertEqual(result["text"], "局部霓虹灯海报，保持留白")
        self.assertEqual(result["outputKind"], "prompt")
        touching_edges = [
            edge for edge in opened.get("connections") or []
            if edge.get("from") == result["id"] or edge.get("to") == result["id"]
        ]
        self.assertEqual(touching_edges, [])
        self.assertEqual(opened["logs"][-1]["type"], "agent-prompt")
        self.assertEqual(opened["logs"][-1]["prompt_output"]["text"], "局部霓虹灯海报，保持留白")
        with main.PROJECT_AGENT_TASKS_LOCK:
            main.PROJECT_AGENT_TASKS.clear()
            main.PROJECT_AGENT_FUTURES.clear()
        durable = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}")
        self.assertEqual(durable.status_code, 200, durable.text)
        self.assertEqual(durable.json()["status"], "succeeded")
        self.assertEqual(self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/plan").json()["plan"]["status"], "completed")

    def test_concurrent_confirm_with_same_execution_id_runs_adapter_once(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            body = {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "same-execution"}
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: self.client.post(
                    f"/api/project-library/{self.project['id']}/agent/agent-1/execute", json=body), range(2)))
            self.assertTrue(all(response.status_code == 202 for response in responses), [r.text for r in responses])
            task_id = responses[0].json()["tasks"][0]["task_id"]
            task = self.wait(task_id)
        self.assertEqual(task["status"], "succeeded", task)
        adapter.assert_awaited_once()
        self.assertEqual(len({response.json()["tasks"][0]["task_id"] for response in responses}), 1)

    def test_task_status_and_plan_survive_in_memory_reload(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            plan_response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            )
            plan = plan_response.json()["plan"]
            execution = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "reload-execution"},
            )
            task_id = execution.json()["tasks"][0]["task_id"]
            self.assertEqual(self.wait(task_id)["status"], "succeeded")
        with main.PROJECT_AGENT_TASKS_LOCK:
            main.PROJECT_AGENT_TASKS.clear()
            main.PROJECT_AGENT_FUTURES.clear()
        durable_task = self.client.get(
            f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}"
        )
        self.assertEqual(durable_task.status_code, 200, durable_task.text)
        self.assertEqual(durable_task.json()["status"], "succeeded")
        self.assertEqual(durable_task.json()["history_record"]["id"], f"agent-generation-{task_id}")
        durable_plan = self.client.get(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan"
        )
        self.assertEqual(durable_plan.status_code, 200, durable_plan.text)
        self.assertEqual(durable_plan.json()["plan"]["status"], "completed")

    def test_restart_rehydrates_durable_queued_task_once(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        plan = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
        ).json()["plan"]
        execution_id = "restart-queued-execution"
        task = main._agent_execution_tasks_from_plan(plan, execution_id)[0]
        task["status"] = "queued"
        opened = self.store.open_project(self.project["id"])["canvas"]
        agent = next(node for node in opened["nodes"] if node["id"] == "agent-1")
        durable_plan = dict(agent["agent_plan"], status="running", executions=[{
            "execution_id": execution_id, "status": "running", "tasks":[{"task_id":task["task_id"], "result_node_id":task["result_node_id"], "kind":"image", "status":"queued"}],
        }])
        self.store.apply_mutations(
            self.project["id"],
            [
                {"op": "upsert_node", "node": main._agent_task_node(task, agent_id="agent-1", execution_id=execution_id, x=400, y=500)},
                {"op": "upsert_node", "node": main._agent_result_node(task, agent_id="agent-1", execution_id=execution_id, x=724, y=500)},
                {"op": "upsert_node", "node": dict(agent, agent_plan=durable_plan)},
            ],
            base_revision=opened["revision"],
        )
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            with main.PROJECT_AGENT_TASKS_LOCK:
                main.PROJECT_AGENT_TASKS.clear()
                main.PROJECT_AGENT_FUTURES.clear()
                main.PROJECT_AGENT_RUNNING_IDS.clear()
            self.assertEqual(main.recover_project_agent_tasks(), 1)
            recovered = self.wait(task["task_id"])
        self.assertEqual(recovered["status"], "succeeded", recovered)
        adapter.assert_awaited_once()

    def test_restart_does_not_blindly_recall_running_provider_task(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        plan = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
        ).json()["plan"]
        execution_id = "restart-running-execution"
        task = main._agent_execution_tasks_from_plan(plan, execution_id)[0]
        task["status"] = "running"
        task["dispatch_state"] = "dispatched"
        opened = self.store.open_project(self.project["id"])["canvas"]
        agent = next(node for node in opened["nodes"] if node["id"] == "agent-1")
        durable_plan = dict(agent["agent_plan"], status="running", executions=[{
            "execution_id": execution_id, "status": "running", "tasks":[{"task_id":task["task_id"], "result_node_id":task["result_node_id"], "kind":"image", "status":"running"}],
        }])
        self.store.apply_mutations(
            self.project["id"],
            [
                {"op": "upsert_node", "node": main._agent_task_node(task, agent_id="agent-1", execution_id=execution_id, status="running", x=400, y=500)},
                {"op": "upsert_node", "node": main._agent_result_node(task, agent_id="agent-1", execution_id=execution_id, x=724, y=500)},
                {"op": "upsert_node", "node": dict(agent, agent_plan=durable_plan)},
            ],
            base_revision=opened["revision"],
        )
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            with main.PROJECT_AGENT_TASKS_LOCK:
                main.PROJECT_AGENT_TASKS.clear()
                main.PROJECT_AGENT_FUTURES.clear()
                main.PROJECT_AGENT_RUNNING_IDS.clear()
            self.assertEqual(main.recover_project_agent_tasks(), 0)
            status = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task['task_id']}")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "uncertain")
        adapter.assert_not_awaited()
        self.assertEqual(self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/plan").json()["plan"]["status"], "failed")

    def test_duplicate_execute_does_not_recall_durable_running_task_after_memory_reload(self):
        """A retry after process memory loss must require a fresh execution."""
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        plan = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
        ).json()["plan"]
        execution_id = "reload-running-execution"
        task = main._agent_execution_tasks_from_plan(plan, execution_id)[0]
        opened = self.store.open_project(self.project["id"])["canvas"]
        agent = next(node for node in opened["nodes"] if node["id"] == "agent-1")
        durable_plan = dict(agent["agent_plan"], status="running", executions=[{
            "execution_id": execution_id,
            "status": "running",
            "tasks": [{"task_id": task["task_id"], "result_node_id": task["result_node_id"], "kind": "image", "status": "running"}],
        }])
        self.store.apply_mutations(
            self.project["id"],
            [
                {"op": "upsert_node", "node": main._agent_task_node(task, agent_id="agent-1", execution_id=execution_id, status="running", x=400, y=500)},
                {"op": "upsert_node", "node": main._agent_result_node(task, agent_id="agent-1", execution_id=execution_id, x=724, y=500)},
                {"op": "upsert_node", "node": dict(agent, agent_plan=durable_plan)},
            ],
            base_revision=opened["revision"],
        )
        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "agent_image_adapter", adapter):
            with main.PROJECT_AGENT_TASKS_LOCK:
                main.PROJECT_AGENT_TASKS.clear()
                main.PROJECT_AGENT_FUTURES.clear()
                main.PROJECT_AGENT_RUNNING_IDS.clear()
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": execution_id},
            )
            self.assertEqual(response.status_code, 409, response.text)
            status = self.client.get(
                f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task['task_id']}"
            )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "uncertain")
        adapter.assert_not_awaited()
        self.assertEqual(
            self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/plan").json()["plan"]["status"],
            "failed",
        )

    def test_queue_limit_rejects_confirm_before_creating_task_nodes(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        with patch.object(main, "PROJECT_AGENT_MAX_PER_NODE", 0), patch.object(main, "agent_image_adapter", AsyncMock()):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "execution_id": "limited-execution"},
            )
        self.assertEqual(response.status_code, 429, response.text)
        opened = self.store.open_project(self.project["id"])["canvas"]
        self.assertFalse(any(node.get("type") == "agent-task" for node in opened["nodes"]))

    def test_admission_count_scan_runs_off_event_loop_thread(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        worker_thread = {}
        caller_thread = {}

        def fake_counts(_project_id, _agent_id):
            worker_thread["id"] = threading.get_ident()
            return (0, 0, 0)

        original_to_thread = main.asyncio.to_thread

        async def capture_to_thread(func, *args, **kwargs):
            if func is main._agent_admit_execution_sync:
                caller_thread["id"] = threading.get_ident()
            return await original_to_thread(func, *args, **kwargs)

        adapter = AsyncMock(return_value={"images": [tiny_png()]})
        with patch.object(main, "_agent_active_counts", fake_counts), patch.object(
            main.asyncio, "to_thread", capture_to_thread
        ), patch.object(main, "agent_image_adapter", adapter):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "offloop-counts"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(self.wait(response.json()["tasks"][0]["task_id"])["status"], "succeeded")
        self.assertIn("id", caller_thread)
        self.assertIn("id", worker_thread)
        self.assertNotEqual(caller_thread["id"], worker_thread["id"])

    def test_global_queue_limit_is_atomic_across_projects(self):
        """Two projects racing admission may not both pass MAX_PENDING."""
        self.project, _image = self.seed()
        other = self.store.create_project("Agent race project")
        self.store.apply_mutations(
            other["id"],
            [
                {"op": "upsert_node", "node": {"id": "race-prompt", "type": "smart-prompt", "text": "另一张海报"}},
                {"op": "upsert_node", "node": {"id": "race-agent", "type": "smart-agent", "title": "Race Agent", "agent_plan": None}},
                {"op": "upsert_edge", "edge": {"id": "race-prompt-agent", "from": "race-prompt", "to": "race-agent", "kind": "input"}},
            ],
            base_revision=0,
        )
        first_revision = self.store.canvas(self.project["id"])["revision"]
        second_revision = self.store.canvas(other["id"])["revision"]
        first_plan = self.client.post(
            f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
            json=self.plan_payload(first_revision, request_id="global-race-1", tasks=[{"kind": "image", "count": 1, "params": {}}]),
        ).json()["plan"]
        second_plan = self.client.post(
            f"/api/project-library/{other['id']}/agent/race-agent/plan",
            json=self.plan_payload(second_revision, request_id="global-race-2", tasks=[{"kind": "image", "count": 1, "params": {}}]),
        ).json()["plan"]
        release = threading.Event()

        async def hold_provider(_request):
            await asyncio.to_thread(release.wait)
            return {"images": [tiny_png()]}

        requests = [
            (self.project["id"], "agent-1", first_plan),
            (other["id"], "race-agent", second_plan),
        ]

        def submit(item):
            project_id, agent_id, plan = item
            return project_id, self.client.post(
                f"/api/project-library/{project_id}/agent/{agent_id}/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": f"{project_id}-global-race"},
            )

        try:
            with patch.object(main, "agent_image_adapter", side_effect=hold_provider), patch.object(
                main, "PROJECT_AGENT_MAX_PENDING", 1
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(submit, requests))
                status_codes = sorted(response.status_code for _project_id, response in responses)
                self.assertEqual(status_codes, [202, 429], [response.text for _project_id, response in responses])
                accepted_project, accepted = next(item for item in responses if item[1].status_code == 202)
                accepted_agent = "agent-1" if accepted_project == self.project["id"] else "race-agent"
                task_id = accepted.json()["tasks"][0]["task_id"]
                release.set()
                deadline = time.monotonic() + 4
                terminal = None
                while time.monotonic() < deadline:
                    terminal = self.client.get(
                        f"/api/project-library/{accepted_project}/agent/{accepted_agent}/tasks/{task_id}"
                    )
                    if terminal.status_code == 200 and terminal.json().get("status") in {"succeeded", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(terminal)
                self.assertEqual(terminal.status_code, 200, terminal.text)
                self.assertEqual(terminal.json().get("status"), "succeeded", terminal.text)
        finally:
            release.set()

    def test_cancel_stops_queued_task_and_marks_cancelled(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        generated = AsyncMock(return_value=(tiny_png(), {}))

        async def slow(_request, *_args, **_kwargs):
            await asyncio.sleep(4)
            return (tiny_png(), {})

        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", AsyncMock(side_effect=slow)):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "cancel-execution"},
            )
            task_id = response.json()["tasks"][0]["task_id"]
            cancelled = self.client.post(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            deadline = time.monotonic() + 6
            terminal = None
            while time.monotonic() < deadline:
                terminal = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}")
                if terminal.status_code == 200 and terminal.json().get("status") in {"cancelled", "succeeded", "failed"}:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(terminal)
            self.assertEqual(terminal.json().get("status"), "cancelled", terminal.text)
            plan_deadline = time.monotonic() + 8
            plan_state = None
            while time.monotonic() < plan_deadline:
                plan_state = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/plan").json()["plan"]
                if plan_state["status"] != "running":
                    break
                time.sleep(0.05)
            self.assertEqual(plan_state["status"], "cancelled", plan_state)

    def test_cancel_after_terminal_returns_conflict(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        generated = AsyncMock(return_value=(tiny_png(), {}))
        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", generated):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "cancel-terminal"},
            )
            task_id = response.json()["tasks"][0]["task_id"]
            self.wait(task_id)
            cancelled = self.client.post(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}/cancel")
            self.assertEqual(cancelled.status_code, 409, cancelled.text)

    def test_retry_re_runs_failed_task(self):
        self.project, _image = self.seed()
        revision = self.store.canvas(self.project["id"])["revision"]
        route = SimpleNamespace(provider_id="fake", provider_name="Fake", model="fake-model", to_dict=lambda: {"provider_id": "fake", "model": "fake-model"})

        attempt_counter = {"n": 0}

        async def fail_once(prompt, *_args, **_kwargs):
            attempt_counter["n"] += 1
            if attempt_counter["n"] == 1:
                raise RuntimeError("第一次失败")
            return (tiny_png(), {})

        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "route_image_generation", return_value=route), patch.object(main, "generate_ai_image", AsyncMock(side_effect=fail_once)):
            plan = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/plan",
                json=self.plan_payload(revision, tasks=[{"kind": "image", "count": 1, "params": {}}]),
            ).json()["plan"]
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/agent/agent-1/execute",
                json={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "execution_id": "retry-execution"},
            )
            task_id = response.json()["tasks"][0]["task_id"]
            failed = self.wait(task_id)
            self.assertEqual(failed["status"], "failed", failed)
            retried = self.client.post(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}/retry")
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json()["status"], "queued")
            deadline = time.monotonic() + 6
            terminal = None
            while time.monotonic() < deadline:
                terminal = self.client.get(f"/api/project-library/{self.project['id']}/agent/agent-1/tasks/{task_id}")
                if terminal.status_code == 200 and terminal.json().get("status") == "succeeded":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(terminal)
            self.assertEqual(terminal.json().get("status"), "succeeded", terminal.text)


if __name__ == "__main__":
    unittest.main()
