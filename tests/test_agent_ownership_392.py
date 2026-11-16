"""#392 stage 1 ownership: real temporary stores/CAS, no providers or server.

Run with FIREFLY_RUNTIME_ROOT and FIREFLY_PROJECTS_ROOT pointing at isolation.
This tree names edited-image writeback _replace_node_image/import_media.
"""
import asyncio
import copy
import io
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP, NODE_TYPE_IMAGE, NODE_TYPE_PROMPT
from firefly_projects import ProjectStore, RevisionConflict

run = asyncio.run
KEYS = ("createdBy", "agentNodeId", "userTouchedAt", "promptEditedByUser")
HUMAN = {"createdBy": "user", "agentNodeId": None, "userTouchedAt": 9876,
         "promptEditedByUser": True, "customField": {"keep": [1, "human"]}}


def ownership(context=""):
    return dict(zip(KEYS, ("agent" if context else "user", context or None, None, False)))


def node(identity, **fields):
    return {"id": identity, "type": NODE_TYPE_IMAGE, "x": 0, "y": 0, **fields}


def upsert(value):
    return {"op": "upsert_node", "node": value}


class Ownership392Tests(unittest.TestCase):
    def setUp(self):
        import main
        from app.routers import api_agent_tools

        self.main, self.tools = main, api_agent_tools
        temp = tempfile.TemporaryDirectory(prefix="e2e-tabbit-a2-392-ownership-")
        self.addCleanup(temp.cleanup)
        self.store = ProjectStore(Path(temp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project("e2e-tabbit-a2-392-ownership")["id"]
        self.start_patch(main, "FIREFLY_PROJECT_STORE", self.store)
        self.broadcast = self.start_patch(self.tools, "_broadcast_canvas_updated", AsyncMock())
        # No lifespan context: do not start application services or agent hosts.
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125")
        self.addCleanup(self.client.close)
        self.addCleanup(self.clear_tasks)

    def start_patch(self, obj, key, value):
        patcher = patch.object(obj, key, value)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def clear_tasks(self):
        with self.tools.CANVAS_TASK_LOCK:
            for key, value in list(self.tools.CANVAS_TASKS.items()):
                if value.get("project_id") == self.pid:
                    self.tools.CANVAS_TASKS.pop(key)

    def canvas(self):
        return self.store.canvas(self.pid)

    def nodes(self):
        return {n["id"]: n for n in self.canvas()["nodes"]}

    def seed(self, *values):
        return self.store.apply_mutations(self.pid, [upsert(n) for n in values],
                                          base_revision=self.canvas()["revision"])

    def assert_metadata(self, actual, expected):
        self.assertEqual({key: actual[key] for key in KEYS},
                         {key: expected[key] for key in KEYS})

    def assert_failure(self, coro, status, code):
        with self.assertRaises(HTTPException) as caught:
            run(coro)
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.detail["code"], code)
        return caught.exception.detail

    @contextmanager
    def race(self, competing):
        """Commit another writer before the first real store CAS, not a fake error."""
        original = self.store.apply_mutations
        attempts, conflicts = [], []

        def interleave(pid, mutations, *, base_revision, **kwargs):
            attempts.append(copy.deepcopy(mutations))
            if len(attempts) == 1:
                original(pid, competing(), base_revision=self.canvas()["revision"])
            try:
                return original(pid, mutations, base_revision=base_revision, **kwargs)
            except RevisionConflict as exc:
                conflicts.append(exc)
                raise

        with patch.object(self.store, "apply_mutations", side_effect=interleave):
            yield attempts
        self.assertEqual(len(conflicts), 1, "the first apply must genuinely conflict")

    def test_create_defaults_follow_context_without_mutating_input(self):
        for context in ("", "owner"):
            with self.subTest(context=context):
                supplied = {"type": NODE_TYPE_PROMPT, "text": "new prompt"}
                before = copy.deepcopy(supplied)
                result = run(self.tools.create_node_core(self.pid, supplied, agent_node_id=context))
                self.assert_metadata(self.nodes()[result["node_id"]], ownership(context))
                self.assertEqual(supplied, before)
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(self.broadcast.await_count, 2)

    def test_create_and_update_reject_each_reserved_key_without_side_effects(self):
        self.seed(node("source", prompt="keep", **ownership("original")))
        before = copy.deepcopy(self.canvas())
        with patch.object(self.store, "apply_mutations", wraps=self.store.apply_mutations) as apply:
            for key in KEYS:
                for value in (None, "forged"):
                    for operation in ("create", "update"):
                        with self.subTest(key=key, value=value, operation=operation):
                            coro = (self.tools.create_node_core(self.pid, {
                                "type": NODE_TYPE_IMAGE, key: value,
                            }, agent_node_id="owner") if operation == "create" else
                                self.tools.update_node_core(self.pid, "source", {"prompt": "bad", key: value}))
                            detail = self.assert_failure(coro, 400, "INVALID_ARGUMENT")
                            self.assertEqual(detail["details"]["rejected_keys"], [key])
                            self.assertEqual(self.canvas(), before)
            apply.assert_not_called()
        self.broadcast.assert_not_awaited()

    def test_rest_create_requires_agent_body_context_not_query(self):
        url = f"/api/projects/{self.pid}/canvas/nodes"
        headers = {"X-Agent-Project": self.pid}
        before = copy.deepcopy(self.canvas())
        for params in ({}, {"agent_node_id": "query-forgery"}):
            response = self.client.post(url, headers=headers, params=params,
                                        json={"node": {"type": NODE_TYPE_IMAGE}})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()
        response = self.client.post(url, headers=headers, json={
            "node": {"type": NODE_TYPE_IMAGE}, "agent_node_id": "body-owner",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_metadata(self.nodes()[response.json()["node_id"]], ownership("body-owner"))

    def test_rest_generation_uses_query_and_rejects_body_only_identity(self):
        before = copy.deepcopy(self.canvas())
        for suffix, core_name, body, args, count in (
            ("generate/trigger", "trigger_generation_core", {"node_id": "source", "n": 3},
             (self.pid, "source"), 3),
            ("canvas/batch_edit", "batch_edit_core", {"node_ids": ["a", "b"], "prompt": "edit"},
             (self.pid, ["a", "b"], "edit"), 2),
        ):
            for context in ("query-owner", ""):
                with self.subTest(tool=suffix, context=context), patch.object(
                    self.tools, core_name, new=AsyncMock(return_value={"ok": True}),
                ) as core, patch.object(self.tools, "_server_confirm_gate", new=AsyncMock()) as gate, patch.object(
                    self.tools, "enforce_generation_limit",
                ) as limit:
                    response = self.client.post(f"/api/projects/{self.pid}/{suffix}",
                        headers={"X-Agent-Project": self.pid}, params={"agent_node_id": context},
                        json={**body, "agent_node_id": "body-forgery"})
                    self.assertEqual(gate.await_count, 1)
                    limit.assert_called_once_with(self.pid, context, count=count)
                    if context:
                        self.assertEqual(response.status_code, 200, response.text)
                        core.assert_awaited_once_with(*args, agent_node_id=context)
                    else:
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertEqual(response.json()["detail"]["code"], "INVALID_ARGUMENT")
                        core.assert_not_awaited()
        self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()

    def test_update_rebuilds_from_latest_human_metadata_after_real_conflict(self):
        self.seed(node("source", prompt="old", **ownership("original")))
        with self.race(lambda: [upsert({**self.nodes()["source"], **HUMAN})]) as attempts:
            run(self.tools.update_node_core(self.pid, "source", {"prompt": "agent edit"}))
        self.assertEqual(len(attempts), 2)
        current = self.nodes()["source"]
        self.assertEqual(current["prompt"], "agent edit")
        self.assert_metadata(current, HUMAN)
        self.assertEqual(current["customField"], HUMAN["customField"])
        self.assertNotIn("customField", attempts[0][0]["node"])
        self.assertEqual(attempts[1][0]["node"]["customField"], HUMAN["customField"])
        self.broadcast.assert_awaited_once_with(self.pid, source="agent")

    def test_update_does_not_resurrect_target_deleted_before_retry(self):
        self.seed(node("source"), node("unrelated"))
        with self.race(lambda: [{"op": "remove_node", "node_id": "source"}]) as attempts:
            self.assert_failure(self.tools.update_node_core(self.pid, "source", {"title": "edit"}),
                                404, "NODE_NOT_FOUND")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(set(self.nodes()), {"unrelated"})
        self.broadcast.assert_not_awaited()

    def test_create_frame_retry_keeps_identity_and_latest_members_geometry_metadata(self):
        self.seed(node("owner", type=NODE_TYPE_AGENT, chatFrameId="frame"), node("first"),
                  node("frame", type=NODE_TYPE_GROUP, items=["first"], w=800, h=360,
                       chatFrame=True, **ownership("original")))
        latest = {**self.nodes()["frame"], **HUMAN, "items": ["first", "concurrent"],
                  "x": 500, "y": 600, "w": 300, "h": 900, "title": "human frame"}
        with self.race(lambda: [upsert(latest), upsert(node("concurrent"))]) as attempts, patch.object(
            self.main, "now_ms", side_effect=[392001, 392002],
        ) as clock:
            result = run(self.tools.create_node_core(self.pid, {"type": NODE_TYPE_IMAGE}, "owner"))
        self.assertEqual(len(attempts), 2)
        first, second = (attempt[0]["node"] for attempt in attempts)
        self.assertEqual((first["id"], first["created_at"]), (second["id"], second["created_at"]))
        self.assertEqual((second["id"], second["created_at"]), (result["node_id"], 392001))
        clock.assert_called_once_with()
        self.assertNotEqual((first["x"], first["y"]), (second["x"], second["y"]))
        self.assertEqual((second["x"], second["y"]), (520, 972))
        self.assert_metadata(second, ownership("owner"))
        self.assertEqual(self.nodes()["frame"], {**latest, "items": [*latest["items"], result["node_id"]]})
        self.assertEqual(set(self.nodes()), {"owner", "frame", "first", "concurrent", result["node_id"]})
        self.broadcast.assert_awaited_once()

    def test_batch_real_core_preserves_existing_and_legacy_metadata_and_forwards_context(self):
        self.seed(node("source", prompt="old", **ownership("old-owner")), node("legacy", prompt="old"))
        expected = {**self.nodes()["source"], **HUMAN}
        legacy = copy.deepcopy(self.nodes()["legacy"])
        with self.race(lambda: [upsert(expected)]) as attempts, patch.object(
            self.tools, "trigger_generation_core", new=AsyncMock(side_effect=[
                {"task_id": "stub-one"}, {"task_id": "stub-two"},
            ]),
        ) as trigger:
            result = run(self.tools.batch_edit_core(self.pid, ["source", "legacy"], " batch ", "context"))
        self.assertEqual(len(attempts), 3)
        self.assertEqual(trigger.await_args_list, [call(self.pid, identity, agent_node_id="context")
                                                 for identity in ("source", "legacy")])
        self.assertEqual(result["tasks"], [{"node_id": "source", "task_id": "stub-one"},
                                            {"node_id": "legacy", "task_id": "stub-two"}])
        for identity, original in (("source", expected), ("legacy", legacy)):
            current = self.nodes()[identity]
            self.assertEqual(current.pop("prompt"), "batch")
            current.pop("updated_at")
            self.assertEqual(current, {k: v for k, v in original.items() if k != "prompt"})

    def test_trigger_real_core_results_follow_context_not_source_ownership(self):
        self.seed(node("source", prompt="generate", **{
            **ownership("source-owner"), "userTouchedAt": 111, "promptEditedByUser": True,
        }))
        source = copy.deepcopy(self.nodes()["source"])
        async def exercise():
            results = [await self.tools.trigger_generation_core(self.pid, "source", context)
                       for context in ("context-owner", "")]
            await asyncio.sleep(0)  # Let the stub background tasks finish before closing the loop.
            return results
        with patch.object(self.main, "route_image_generation", return_value=SimpleNamespace(
            provider_id="offline", model="stub-model",
        )) as route, patch.object(self.main, "load_api_providers", return_value=[]), patch.object(
            self.tools, "_run_agent_generation", new=AsyncMock(),
        ) as worker, patch.object(self.store, "begin_image_generation", return_value={}) as begin:
            results = run(exercise())
        self.assertEqual(route.call_count, 2)
        self.assertEqual(worker.await_count, 2)
        self.assertEqual(begin.call_count, 2)
        for context, result in zip(("context-owner", ""), results):
            self.assert_metadata(self.nodes()[result["result_node_id"]], ownership(context))
            task = self.tools.CANVAS_TASKS[result["task_id"]]
            self.assertEqual((task["project_id"], task["node_id"], task["result_node_id"]),
                             (self.pid, "source", result["result_node_id"]))
        self.assertEqual(self.nodes()["source"], source)
        self.assertEqual(len(self.nodes()), 3)

    def image_fixture(self):
        records = []
        for color in ("red", "green", "blue"):
            with Image.new("RGB", (8, 6), color) as image, io.BytesIO() as data:
                image.save(data, format="PNG")
                records.append(self.store.import_media(self.pid, data.getvalue(),
                                                       f"e2e-tabbit-{color}.png", "image/png"))
        old, concurrent, edited = records
        self.seed(node("source", **ownership("original"), output_asset_id=old["asset_id"], images=[
            {"asset_id": old["asset_id"], "kind": "image", "name": "old.png", "slotNote": "keep"},
        ]), node("unrelated"))
        source = copy.deepcopy(self.nodes()["source"])
        return source, source["images"][0], concurrent, edited

    def replace_image(self, source, picked):
        with Image.new("RGBA", (8, 6), "blue") as image:
            return run(self.tools._replace_node_image(self.pid, source, "source", picked, image,
                                                      "edited.png", {"outpaintSize": {"w": 8, "h": 6}}))

    def test_edited_image_cas_retry_preserves_latest_metadata_and_new_image(self):
        source, picked, concurrent, edited = self.image_fixture()
        extra = {"asset_id": concurrent["asset_id"], "kind": "image", "name": "human.png"}
        latest = {**source, **HUMAN, "images": [picked, extra]}
        with patch.object(self.store, "import_media", return_value=edited) as importer, self.race(
            lambda: [upsert(latest)],
        ) as attempts:
            result = self.replace_image(source, picked)
        self.assertEqual(len(attempts), 2)
        current = self.nodes()["source"]
        self.assert_metadata(current, HUMAN)
        self.assertEqual(current["customField"], HUMAN["customField"])
        self.assertEqual(current["images"], [{**picked, "asset_id": edited["asset_id"], "name": "edited.png"}, extra])
        self.assertEqual(current["output_asset_id"], edited["asset_id"])
        self.assertEqual(current["outpaintSize"], {"w": 8, "h": 6})
        self.assertEqual((result["asset_id"], result["original_asset_id"], result["width"], result["height"]),
                         (edited["asset_id"], picked["asset_id"], 8, 6))
        importer.assert_called_once()
        pid, data, filename, mime = importer.call_args.args
        self.assertEqual((pid, filename, mime), (self.pid, "edited.png", "image/png"))
        with Image.open(io.BytesIO(data)) as decoded:
            self.assertEqual(decoded.size, (8, 6))
        self.broadcast.assert_awaited_once()

    def test_edited_image_first_snapshot_after_import_keeps_new_images_and_metadata(self):
        source, picked, concurrent, edited = self.image_fixture()
        extra = {"asset_id": concurrent["asset_id"], "kind": "image", "name": "human.png"}
        def import_interleave(*_args):
            # The picked image disappeared during encoding/import, before the first build.
            self.seed({**source, **HUMAN, "title": "human title", "images": [extra]})
            return edited
        with patch.object(self.store, "import_media", side_effect=import_interleave):
            self.replace_image(source, picked)
        current = self.nodes()["source"]
        self.assert_metadata(current, HUMAN)
        self.assertEqual((current["title"], current["customField"]), ("human title", HUMAN["customField"]))
        self.assertEqual(current["images"], [{"asset_id": edited["asset_id"], "kind": "image", "name": "edited.png"}, extra])
        self.broadcast.assert_awaited_once()

    def test_edited_image_deleted_source_never_resurrected_in_either_window(self):
        for window in ("import", "cas"):
            with self.subTest(window=window):
                source, picked, _, edited = self.image_fixture()
                unrelated = copy.deepcopy(self.nodes()["unrelated"])
                removal = [{"op": "remove_node", "node_id": "source"}]
                def import_interleave(*_args):
                    if window == "import":
                        self.store.apply_mutations(self.pid, removal, base_revision=self.canvas()["revision"])
                    return edited
                race = self.race(lambda: removal) if window == "cas" else nullcontext()
                with patch.object(self.store, "import_media", side_effect=import_interleave), race:
                    with Image.new("RGBA", (8, 6)) as image:
                        self.assert_failure(self.tools._replace_node_image(
                            self.pid, source, "source", picked, image, "edited.png"), 404, "NODE_NOT_FOUND")
                self.assertEqual(self.nodes(), {"unrelated": unrelated})
                self.broadcast.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
