import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
from firefly_projects import InvalidMutation, ProjectNotFound, ProjectStore, ProjectStoreError


class ProjectHistoryContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
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

    def _seed_projects(self):
        project = self.store.create_project("历史项目")
        other_project = self.store.create_project("另一个项目")
        original = self.store.import_media(
            project["id"],
            b"generated-original",
            "generated.png",
            "image/png",
            {"type": "online_generation", "name": "generated.png"},
        )
        reference = self.store.import_media(
            project["id"],
            b"reference",
            "reference.png",
            "image/png",
            {"type": "user_import", "name": "reference.png"},
        )
        downstream_media = self.store.import_media(
            project["id"],
            b"downstream-result",
            "downstream.png",
            "image/png",
            {"type": "online_generation", "name": "downstream.png"},
        )
        other_media = self.store.import_media(
            other_project["id"],
            b"other-project-result",
            "other.png",
            "image/png",
            {"type": "online_generation", "name": "other.png"},
        )

        generator = {
            "id": "generator",
            "type": "smart-image",
            "x": 40,
            "y": 50,
            "images": [{"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"}],
            "runPrompt": "保留的提示词",
            "runModelPrompt": "model prompt",
            "runPromptRefs": [{"asset_id": reference["asset_id"], "kind": "image", "name": "reference.png"}],
            "runInputRefs": [
                {"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"},
                {"asset_id": reference["asset_id"], "kind": "image", "name": "reference.png"},
            ],
            "runSettings": {"model": "offline-test-model", "size": "1024x1024"},
            "runAt": 123,
            "runFinishedAt": 456,
        }
        downstream = {
            "id": "downstream",
            "type": "smart-image",
            "x": 420,
            "y": 50,
            "images": [{"asset_id": downstream_media["asset_id"], "kind": "image", "name": "downstream.png"}],
            "runInputRefs": [{"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"}],
        }
        history = {
            "id": "history-original",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "保留的提示词",
            "executed_prompt": "model prompt",
            "params": {"model": "offline-test-model", "size": "1024x1024"},
            "references": [{"asset_id": reference["asset_id"], "kind": "image", "name": "reference.png"}],
            "outputs": [{"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"}],
            "result_node_id": "generator",
        }
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": generator},
                {"op": "upsert_node", "node": downstream},
                {"op": "upsert_edge", "edge": {"id": "generator-downstream", "from": "generator", "to": "downstream", "kind": "flow"}},
                {"op": "set_canvas_meta", "logs": [history]},
            ],
            base_revision=0,
        )
        other_history = {
            "id": "history-other-project",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "另一个项目的历史",
            "outputs": [{"asset_id": other_media["asset_id"], "kind": "image", "name": "other.png"}],
        }
        self.store.apply_mutations(
            other_project["id"],
            [{"op": "set_canvas_meta", "logs": [other_history]}],
            base_revision=0,
        )
        return project, other_project, original, reference, downstream_media, other_media

    def test_delete_original_is_project_scoped_reversible_and_non_cascading(self):
        project, other_project, original, reference, downstream_media, other_media = self._seed_projects()

        project_view = self.client.get(f"/api/project-library/{project['id']}")
        other_view = self.client.get(f"/api/project-library/{other_project['id']}")
        self.assertEqual(project_view.status_code, 200)
        self.assertEqual([item["id"] for item in project_view.json()["canvas"]["logs"]], ["history-original"])
        self.assertEqual([item["id"] for item in other_view.json()["canvas"]["logs"]], ["history-other-project"])

        deleted_record = self.client.post(
            f"/api/project-library/{project['id']}/history/delete",
            json={"log_id": "history-original", "base_revision": 1},
        )
        self.assertEqual(deleted_record.status_code, 200, deleted_record.text)
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/media/{original['asset_id']}").status_code,
            200,
        )
        preview = Path(self.temp_dir.name) / "projects" / project["id"] / "previews" / f"{original['asset_id']}-512.webp"
        preview.write_bytes(b"derived-preview")

        deleted = self.client.post(
            f"/api/project-library/{project['id']}/original/delete-project-wide",
            json={"asset_id": original["asset_id"], "base_revision": 2},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        payload = deleted.json()
        canvas = payload["canvas"]
        self.assertEqual(canvas["revision"], 3)
        media_url = f"/api/project-library/{project['id']}/media/{original['asset_id']}"
        self.assertEqual(self.client.get(media_url).status_code, 404)
        deleted_download = self.client.get("/api/download-output", params={"url": media_url})
        self.assertEqual(deleted_download.status_code, 404, deleted_download.text)
        self.assertEqual(self.client.get(f"/api/project-library/{other_project['id']}/media/{other_media['asset_id']}").status_code, 200)

        nodes = {node["id"]: node for node in canvas["nodes"]}
        self.assertEqual(nodes["generator"]["images"], [])
        self.assertEqual(nodes["generator"]["runPrompt"], "保留的提示词")
        self.assertEqual(nodes["generator"]["runModelPrompt"], "model prompt")
        self.assertEqual(nodes["generator"]["runSettings"], {"model": "offline-test-model", "size": "1024x1024"})
        self.assertEqual(nodes["generator"]["runPromptRefs"], [{"asset_id": reference["asset_id"], "kind": "image", "name": "reference.png"}])
        self.assertEqual(nodes["generator"]["runInputRefs"], [{"asset_id": reference["asset_id"], "kind": "image", "name": "reference.png"}])
        self.assertEqual(nodes["generator"]["needsRegeneration"], True)
        self.assertEqual(nodes["generator"]["generationState"], "ready")
        self.assertEqual(nodes["downstream"]["images"][0]["asset_id"], downstream_media["asset_id"])
        self.assertEqual(nodes["downstream"].get("runInputRefs"), [])
        self.assertEqual(canvas["connections"], [{"id": "generator-downstream", "from": "generator", "to": "downstream", "kind": "flow"}])

        trash_index = Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "index.json"
        self.assertTrue(trash_index.is_file())
        trash = json.loads(trash_index.read_text(encoding="utf-8"))
        self.assertEqual(len(trash["items"]), 1)
        trash_item = trash["items"][0]
        self.assertEqual(trash_item["asset_id"], original["asset_id"])
        self.assertEqual(trash_item["expires_at"] - trash_item["deleted_at"], 7 * 24 * 60 * 60 * 1000)
        self.assertIn("snapshot_filename", trash_item["undo"])
        self.assertNotIn("nodes", trash_item["undo"])
        self.assertNotIn("history", trash_item["undo"])
        undo_snapshot = Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "undo" / trash_item["undo"]["snapshot_filename"]
        self.assertTrue(undo_snapshot.is_file())
        self.assertTrue((Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "media" / original["filename"]).is_file())
        self.assertTrue((Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "previews" / preview.name).is_file())

        restored = self.client.post(
            f"/api/project-library/{project['id']}/original/restore-project-wide",
            json={"asset_id": original["asset_id"], "base_revision": 3},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        restored_canvas = restored.json()["canvas"]
        self.assertEqual(restored_canvas["revision"], 4)
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/media/{original['asset_id']}").status_code,
            200,
        )
        restored_nodes = {node["id"]: node for node in restored_canvas["nodes"]}
        self.assertEqual(restored_nodes["generator"]["images"][0]["asset_id"], original["asset_id"])
        self.assertNotIn("needsRegeneration", restored_nodes["generator"])
        self.assertEqual(restored_canvas["logs"], [])
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/trash/media").json()["items"], [])

        deleted_again = self.client.post(
            f"/api/project-library/{project['id']}/original/delete-project-wide",
            json={"asset_id": original["asset_id"], "base_revision": 4},
        )
        self.assertEqual(deleted_again.status_code, 200, deleted_again.text)
        purged = self.client.delete(f"/api/project-library/{project['id']}/trash/media")
        self.assertEqual(purged.status_code, 200, purged.text)
        self.assertEqual(purged.json()["removed"], 1)
        self.assertFalse(undo_snapshot.exists())
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/media/{original['asset_id']}").status_code,
            404,
        )

    def test_delete_and_restore_cover_scalar_and_string_list_media_references(self):
        project, _other_project, original, reference, downstream_media, _other_media = self._seed_projects()
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "agent-consumer",
                        "type": "agent-task",
                        "status": "succeeded",
                        "images": [{"asset_id": downstream_media["asset_id"]}],
                        "reference_asset_ids": [original["asset_id"], reference["asset_id"]],
                        "params": {
                            "reference_asset_ids": [original["asset_id"], reference["asset_id"]],
                        },
                    },
                },
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "local-edit-consumer",
                        "type": "local-edit",
                        "images": [{"asset_id": downstream_media["asset_id"]}],
                        "original_asset_id": original["asset_id"],
                        "reference_asset_ids": [original["asset_id"], reference["asset_id"]],
                        "output_asset_id": downstream_media["asset_id"],
                    },
                },
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "anonymous-list-consumer",
                        "type": "smart-image",
                        "images": [{"asset_id": downstream_media["asset_id"]}],
                        "anonymous_refs": [
                            {"label": "first", "reference": {"asset_id": original["asset_id"]}},
                            {"label": "second", "reference": {"asset_id": reference["asset_id"]}},
                        ],
                        "task_refs": [
                            {"taskId": "shared-task", "outputIndex": 0, "reference": {"asset_id": reference["asset_id"]}},
                            {"taskId": "shared-task", "outputIndex": 1, "reference": {"asset_id": original["asset_id"]}},
                        ],
                        "duplicate_direct_refs": [
                            {"asset_id": original["asset_id"], "role": "first"},
                            {"asset_id": original["asset_id"], "role": "second"},
                        ],
                        "replaceable_direct_refs": [
                            {"id": "stable-slot", "asset_id": original["asset_id"], "role": "old"},
                        ],
                        "asset_ids": [original["asset_id"], original["asset_id"], reference["asset_id"]],
                        "identical_anonymous_refs": [
                            {"reference": {"asset_id": original["asset_id"]}},
                            {"reference": {"asset_id": original["asset_id"]}},
                        ],
                        "nested_direct_ref": {"asset_id": original["asset_id"], "label": "old"},
                    },
                },
            ],
            base_revision=1,
        )

        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=2,
        )
        deleted_nodes = {node["id"]: node for node in deleted["canvas"]["nodes"]}
        self.assertEqual(deleted_nodes["agent-consumer"]["reference_asset_ids"], [reference["asset_id"]])
        self.assertEqual(deleted_nodes["agent-consumer"]["params"]["reference_asset_ids"], [reference["asset_id"]])
        self.assertNotIn("original_asset_id", deleted_nodes["local-edit-consumer"])
        self.assertEqual(deleted_nodes["local-edit-consumer"]["reference_asset_ids"], [reference["asset_id"]])
        self.assertEqual(deleted_nodes["local-edit-consumer"]["output_asset_id"], downstream_media["asset_id"])
        self.assertEqual(deleted_nodes["local-edit-consumer"]["images"][0]["asset_id"], downstream_media["asset_id"])
        self.assertEqual(
            deleted_nodes["anonymous-list-consumer"]["anonymous_refs"],
            [
                {"label": "first"},
                {"label": "second", "reference": {"asset_id": reference["asset_id"]}},
            ],
        )
        self.assertEqual(
            deleted_nodes["anonymous-list-consumer"]["task_refs"],
            [
                {"taskId": "shared-task", "outputIndex": 0, "reference": {"asset_id": reference["asset_id"]}},
                {"taskId": "shared-task", "outputIndex": 1},
            ],
        )
        self.assertEqual(deleted_nodes["anonymous-list-consumer"]["duplicate_direct_refs"], [])
        self.assertEqual(deleted_nodes["anonymous-list-consumer"]["replaceable_direct_refs"], [])
        self.assertEqual(deleted_nodes["anonymous-list-consumer"]["asset_ids"], [reference["asset_id"]])
        self.assertEqual(deleted_nodes["anonymous-list-consumer"]["identical_anonymous_refs"], [{}, {}])
        self.assertNotIn("nested_direct_ref", deleted_nodes["anonymous-list-consumer"])

        later_local_edit = copy.deepcopy(deleted_nodes["local-edit-consumer"])
        later_local_edit["original_asset_id"] = reference["asset_id"]
        later_anonymous = copy.deepcopy(deleted_nodes["anonymous-list-consumer"])
        later_anonymous["anonymous_refs"].reverse()
        later_anonymous["anonymous_refs"][1]["label"] = "renamed-first"
        later_anonymous["replaceable_direct_refs"] = [
            {"id": "stable-slot", "asset_id": reference["asset_id"], "role": "new"},
        ]
        later_anonymous["nested_direct_ref"] = {"label": "new"}
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": later_local_edit},
                {"op": "upsert_node", "node": later_anonymous},
            ],
            base_revision=3,
        )

        restored = self.store.restore_deleted_original_project_wide(
            project["id"], original["asset_id"], base_revision=4,
        )
        restored_nodes = {node["id"]: node for node in restored["canvas"]["nodes"]}
        self.assertEqual(
            restored_nodes["agent-consumer"]["reference_asset_ids"],
            [original["asset_id"], reference["asset_id"]],
        )
        self.assertEqual(
            restored_nodes["agent-consumer"]["params"]["reference_asset_ids"],
            [original["asset_id"], reference["asset_id"]],
        )
        self.assertEqual(restored_nodes["local-edit-consumer"]["original_asset_id"], reference["asset_id"])
        self.assertEqual(
            restored_nodes["local-edit-consumer"]["reference_asset_ids"],
            [original["asset_id"], reference["asset_id"]],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["anonymous_refs"],
            [
                {"label": "second", "reference": {"asset_id": reference["asset_id"]}},
                {"label": "renamed-first"},
                {"label": "first", "reference": {"asset_id": original["asset_id"]}},
            ],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["task_refs"],
            [
                {"taskId": "shared-task", "outputIndex": 0, "reference": {"asset_id": reference["asset_id"]}},
                {"taskId": "shared-task", "outputIndex": 1, "reference": {"asset_id": original["asset_id"]}},
            ],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["duplicate_direct_refs"],
            [
                {"asset_id": original["asset_id"], "role": "first"},
                {"asset_id": original["asset_id"], "role": "second"},
            ],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["replaceable_direct_refs"],
            [{"id": "stable-slot", "asset_id": reference["asset_id"], "role": "new"}],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["asset_ids"],
            [original["asset_id"], original["asset_id"], reference["asset_id"]],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["identical_anonymous_refs"],
            [
                {"reference": {"asset_id": original["asset_id"]}},
                {"reference": {"asset_id": original["asset_id"]}},
            ],
        )
        self.assertEqual(
            restored_nodes["anonymous-list-consumer"]["nested_direct_ref"],
            {"asset_id": original["asset_id"], "label": "new"},
        )

    def test_expired_original_trash_is_physically_cleaned_after_seven_days(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        preview = Path(self.temp_dir.name) / "projects" / project["id"] / "previews" / f"{original['asset_id']}-512.webp"
        preview.write_bytes(b"derived-preview")
        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=1,
        )
        self.assertEqual(deleted["canvas"]["revision"], 2)
        self.store.flush(project["id"])
        trash_index = Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "index.json"
        payload = json.loads(trash_index.read_text(encoding="utf-8"))
        payload["items"][0]["expires_at"] = payload["items"][0]["deleted_at"] - 1
        trash_index.write_text(json.dumps(payload), encoding="utf-8")
        undo_snapshot = Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "undo" / payload["items"][0]["undo"]["snapshot_filename"]

        self.assertEqual(self.store.cleanup_expired_deleted_media(project["id"]), 1)
        self.assertEqual(json.loads(trash_index.read_text(encoding="utf-8")), {"items": []})
        self.assertFalse(undo_snapshot.exists())
        self.assertFalse((Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "media" / original["filename"]).exists())
        self.assertFalse((Path(self.temp_dir.name) / "projects" / project["id"] / "trash" / "previews" / preview.name).exists())

    def test_history_delete_with_media_removes_all_outputs_in_one_revision(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        second = self.store.import_media(
            project["id"],
            b"generated-original-two",
            "generated-two.png",
            "image/png",
            {"type": "online_generation", "name": "generated-two.png"},
        )
        opened = self.store.open_project(project["id"])
        generator = next(node for node in opened["canvas"]["nodes"] if node["id"] == "generator")
        generator["images"].append({"asset_id": second["asset_id"], "kind": "image", "name": "generated-two.png"})
        history = opened["canvas"]["logs"][0]
        history["outputs"].append({"asset_id": second["asset_id"], "kind": "image", "name": "generated-two.png"})
        changed = self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": generator},
                {"op": "set_canvas_meta", "logs": [history]},
            ],
            base_revision=1,
        )
        self.assertEqual(changed["revision"], 2)

        response = self.client.post(
            f"/api/project-library/{project['id']}/history/delete-with-media",
            json={"log_id": "history-original", "base_revision": 2},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["canvas"]["revision"], 3)
        self.assertEqual(len(payload["deleted"]), 2)
        self.assertEqual(len(payload["removed_files"]), 2)
        self.assertEqual(payload["canvas"]["logs"], [])
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/media/{original['asset_id']}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/project-library/{project['id']}/media/{second['asset_id']}").status_code,
            404,
        )
        deleted_node = next(node for node in payload["canvas"]["nodes"] if node["id"] == "generator")
        self.assertEqual(deleted_node["images"], [])
        self.assertTrue(deleted_node["needsRegeneration"])
        self.assertEqual(len(self.client.get(f"/api/project-library/{project['id']}/trash/media").json()["items"]), 2)

    def test_delete_distinguishes_output_owner_and_reference_consumer_and_restore_merges_later_edits(self):
        project, _other_project, original, _reference, downstream_media, _other_media = self._seed_projects()
        downstream_history = {
            "id": "history-downstream",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "下游提示词",
            "references": [{"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"}],
            "outputs": [{"asset_id": downstream_media["asset_id"], "kind": "image", "name": "downstream.png"}],
            "result_node_id": "downstream",
        }
        self.store.apply_mutations(
            project["id"],
            [{"op": "set_canvas_meta", "logs": [downstream_history]}],
            base_revision=1,
        )

        deleted = self.client.post(
            f"/api/project-library/{project['id']}/original/delete-project-wide",
            json={"asset_id": original["asset_id"], "base_revision": 2},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        deleted_canvas = deleted.json()["canvas"]
        deleted_nodes = {node["id"]: node for node in deleted_canvas["nodes"]}
        deleted_history = {record["id"]: record for record in deleted_canvas["logs"]}

        self.assertEqual(deleted_history["history-original"]["status"], "original_deleted")
        self.assertEqual(deleted_history["history-original"]["outputs"], [])
        self.assertEqual(deleted_history["history-downstream"]["status"], "succeeded")
        self.assertEqual(deleted_history["history-downstream"]["outputs"][0]["asset_id"], downstream_media["asset_id"])
        self.assertEqual(deleted_history["history-downstream"]["references"], [])
        self.assertEqual(
            deleted_history["history-downstream"]["deletedReferenceTombstones"][0]["reason"],
            "upstream_media_deleted",
        )
        self.assertTrue(deleted_nodes["generator"]["needsRegeneration"])
        self.assertNotIn("needsRegeneration", deleted_nodes["downstream"])
        self.assertEqual(deleted_nodes["downstream"]["images"][0]["asset_id"], downstream_media["asset_id"])
        self.assertEqual(deleted_nodes["downstream"]["runInputRefs"], [])
        self.assertEqual(
            deleted_nodes["downstream"]["deletedInputTombstones"][0]["reason"],
            "upstream_media_deleted",
        )

        generator_edit = dict(deleted_nodes["generator"])
        generator_edit.update({"x": 901, "y": 902, "runPrompt": "删除之后修改的提示词"})
        downstream_edit = dict(deleted_nodes["downstream"])
        downstream_edit.update({"x": 777, "userNote": "删除之后的下游编辑"})
        consumer_edit = dict(deleted_history["history-downstream"])
        consumer_edit["prompt"] = "删除之后修改的下游历史提示词"
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": generator_edit},
                {"op": "upsert_node", "node": downstream_edit},
                {"op": "set_canvas_meta", "logs": [consumer_edit]},
            ],
            base_revision=3,
        )
        removed_producer_history = self.client.post(
            f"/api/project-library/{project['id']}/history/delete",
            json={"log_id": "history-original", "base_revision": 4},
        )
        self.assertEqual(removed_producer_history.status_code, 200, removed_producer_history.text)

        restored = self.client.post(
            f"/api/project-library/{project['id']}/original/restore-project-wide",
            json={"asset_id": original["asset_id"], "base_revision": 5},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        restored_canvas = restored.json()["canvas"]
        restored_nodes = {node["id"]: node for node in restored_canvas["nodes"]}
        restored_history = {record["id"]: record for record in restored_canvas["logs"]}

        self.assertNotIn("history-original", restored_history)
        self.assertEqual(restored_history["history-downstream"]["prompt"], "删除之后修改的下游历史提示词")
        self.assertEqual(restored_history["history-downstream"]["references"][0]["asset_id"], original["asset_id"])
        self.assertNotIn("deletedReferenceTombstones", restored_history["history-downstream"])
        self.assertEqual(restored_nodes["generator"]["x"], 901)
        self.assertEqual(restored_nodes["generator"]["y"], 902)
        self.assertEqual(restored_nodes["generator"]["runPrompt"], "删除之后修改的提示词")
        self.assertEqual(restored_nodes["generator"]["images"][0]["asset_id"], original["asset_id"])
        self.assertNotIn("needsRegeneration", restored_nodes["generator"])
        self.assertEqual(restored_nodes["downstream"]["x"], 777)
        self.assertEqual(restored_nodes["downstream"]["userNote"], "删除之后的下游编辑")
        self.assertEqual(restored_nodes["downstream"]["runInputRefs"][0]["asset_id"], original["asset_id"])
        self.assertNotIn("deletedInputTombstones", restored_nodes["downstream"])

    def test_delete_only_resets_the_true_producer_when_history_output_was_reused_on_canvas(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        producer_owned_by_history = {
            "id": "generator",
            "type": "smart-image",
            "x": 40,
            "y": 50,
            "images": [
                {"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"},
            ],
        }
        reused_from_history = {
            "id": "history-reuse",
            "type": "smart-image",
            "x": 760,
            "y": 50,
            "title": "Image",
            "created_at": 789,
            "images": [
                {"asset_id": original["asset_id"], "kind": "image", "name": "generated.png"},
            ],
        }
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": producer_owned_by_history},
                {"op": "upsert_node", "node": reused_from_history},
            ],
            base_revision=1,
        )

        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=2,
        )["canvas"]
        nodes = {node["id"]: node for node in deleted["nodes"]}

        self.assertEqual(nodes["generator"]["images"], [])
        self.assertTrue(nodes["generator"]["needsRegeneration"])
        self.assertEqual(
            nodes["generator"]["deletedOutputTombstones"][0]["deleted_asset_id"],
            original["asset_id"],
        )
        self.assertEqual(nodes["history-reuse"]["images"], [])
        self.assertNotIn("needsRegeneration", nodes["history-reuse"])
        self.assertNotIn("generationState", nodes["history-reuse"])
        self.assertNotIn("deletedOutputTombstones", nodes["history-reuse"])
        self.assertEqual(
            nodes["history-reuse"]["deletedInputTombstones"][0]["deleted_asset_id"],
            original["asset_id"],
        )

    def test_delete_commit_is_durable_without_a_followup_flush(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        self.store.flush(project["id"])

        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=1,
        )
        self.assertEqual(deleted["canvas"]["revision"], 2)

        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            recovered = reopened.open_project(project["id"])["canvas"]
            recovered_nodes = {node["id"]: node for node in recovered["nodes"]}
            self.assertEqual(recovered["revision"], 2)
            self.assertEqual(recovered_nodes["generator"]["images"], [])
            self.assertTrue(recovered_nodes["generator"]["needsRegeneration"])
            self.assertEqual(recovered["logs"][0]["status"], "original_deleted")
            with self.assertRaises(ProjectNotFound):
                reopened.media_record(project["id"], original["asset_id"])
        finally:
            reopened.close()

    def test_corrupt_canvas_after_delete_recovers_the_committed_cross_file_revision(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        self.store.flush(project["id"])
        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=1,
        )
        self.assertEqual(deleted["canvas"]["revision"], 2)

        project_dir = Path(self.temp_dir.name) / "projects" / project["id"]
        (project_dir / "canvas.json").write_text("{broken", encoding="utf-8")

        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            recovered = reopened.open_project(project["id"])["canvas"]
            recovered_nodes = {node["id"]: node for node in recovered["nodes"]}
            recovered_history = {record["id"]: record for record in recovered["logs"]}

            self.assertEqual(recovered["revision"], 2)
            self.assertEqual(recovered_nodes["generator"]["images"], [])
            self.assertTrue(recovered_nodes["generator"]["needsRegeneration"])
            self.assertEqual(recovered_history["history-original"]["status"], "original_deleted")
            self.assertEqual(recovered_history["history-original"]["outputs"], [])
            direct_asset_refs = []

            def collect_direct_asset_refs(value):
                if isinstance(value, dict):
                    direct_asset_refs.extend(
                        child for key, child in value.items()
                        if key == "asset_id" and isinstance(child, str)
                    )
                    for child in value.values():
                        collect_direct_asset_refs(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_direct_asset_refs(child)

            collect_direct_asset_refs(recovered)
            self.assertNotIn(original["asset_id"], direct_asset_refs)
            with self.assertRaises(ProjectNotFound):
                reopened.media_record(project["id"], original["asset_id"])
            self.assertEqual(len(list(project_dir.glob("canvas.json.bak*"))), 1)
        finally:
            reopened.close()

    def test_delete_rejects_project_trash_symlink_without_writing_outside_project(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        project_dir = Path(self.temp_dir.name) / "projects" / project["id"]
        outside = Path(self.temp_dir.name) / "outside-project"
        outside.mkdir()
        undo_link = project_dir / "trash" / "undo"
        try:
            os.symlink(outside, undo_link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlink is unavailable: {exc}")

        with self.assertRaises(ProjectStoreError):
            self.store.delete_generated_original_project_wide(
                project["id"], original["asset_id"], base_revision=1,
            )

        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(
            self.store.media_record(project["id"], original["asset_id"])["asset_id"],
            original["asset_id"],
        )
        self.assertEqual(self.store.list_deleted_media(project["id"]), [])
        self.assertFalse((project_dir / "trash" / "original-media-transaction.json").exists())

    def test_interrupted_delete_recovers_the_whole_project_to_one_consistent_revision(self):
        class SimulatedProcessCrash(BaseException):
            pass

        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        self.store.flush(project["id"])

        def crash_after_media_index(checkpoint):
            if checkpoint == "after_media_index":
                raise SimulatedProcessCrash()

        self.store._original_transaction_checkpoint = crash_after_media_index
        with self.assertRaises(SimulatedProcessCrash):
            self.store.delete_generated_original_project_wide(
                project["id"], original["asset_id"], base_revision=1,
            )

        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            recovered = reopened.open_project(project["id"])["canvas"]
            recovered_nodes = {node["id"]: node for node in recovered["nodes"]}
            self.assertEqual(recovered["revision"], 1)
            self.assertEqual(recovered_nodes["generator"]["images"][0]["asset_id"], original["asset_id"])
            self.assertNotIn("needsRegeneration", recovered_nodes["generator"])
            self.assertEqual(recovered["logs"][0]["status"], "succeeded")
            self.assertEqual(reopened.media_record(project["id"], original["asset_id"])["asset_id"], original["asset_id"])
            self.assertEqual(reopened.list_deleted_media(project["id"]), [])
        finally:
            reopened.close()

    def test_interrupted_restore_recovers_the_whole_project_to_the_deleted_revision(self):
        class SimulatedProcessCrash(BaseException):
            pass

        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        self.store.flush(project["id"])
        self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=1,
        )

        def crash_after_canvas(checkpoint):
            if checkpoint == "after_canvas":
                raise SimulatedProcessCrash()

        self.store._original_transaction_checkpoint = crash_after_canvas
        with self.assertRaises(SimulatedProcessCrash):
            self.store.restore_deleted_original_project_wide(
                project["id"], original["asset_id"], base_revision=2,
            )

        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            recovered = reopened.open_project(project["id"])["canvas"]
            recovered_nodes = {node["id"]: node for node in recovered["nodes"]}
            self.assertEqual(recovered["revision"], 2)
            self.assertEqual(recovered_nodes["generator"]["images"], [])
            self.assertTrue(recovered_nodes["generator"]["needsRegeneration"])
            self.assertEqual(recovered["logs"][0]["status"], "original_deleted")
            with self.assertRaises(ProjectNotFound):
                reopened.media_record(project["id"], original["asset_id"])
            self.assertEqual(reopened.list_deleted_media(project["id"])[0]["asset_id"], original["asset_id"])
        finally:
            reopened.close()

    def test_committed_delete_journal_rolls_forward_after_process_exit(self):
        class SimulatedProcessCrash(BaseException):
            pass

        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        self.store.flush(project["id"])

        def crash_after_commit(checkpoint):
            if checkpoint == "after_commit":
                raise SimulatedProcessCrash()

        self.store._original_transaction_checkpoint = crash_after_commit
        with self.assertRaises(SimulatedProcessCrash):
            self.store.delete_generated_original_project_wide(
                project["id"], original["asset_id"], base_revision=1,
            )

        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            recovered = reopened.open_project(project["id"])["canvas"]
            generator = next(node for node in recovered["nodes"] if node["id"] == "generator")
            self.assertEqual(recovered["revision"], 2)
            self.assertEqual(generator["images"], [])
            self.assertTrue(generator["needsRegeneration"])
            with self.assertRaises(ProjectNotFound):
                reopened.media_record(project["id"], original["asset_id"])
            self.assertEqual(reopened.list_deleted_media(project["id"])[0]["asset_id"], original["asset_id"])
        finally:
            reopened.close()

    def test_regeneration_supersedes_deleted_producer_state_without_losing_undo_media(self):
        project, _other_project, original, _reference, regenerated_media, _other_media = self._seed_projects()
        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=1,
        )
        self.assertTrue(
            next(node for node in deleted["canvas"]["nodes"] if node["id"] == "generator")["needsRegeneration"],
        )

        registered = self.store.begin_image_generation(
            project["id"],
            "generator",
            {"taskId": "regenerate-task", "kind": "image", "count": 1, "referenceAssetIds": []},
            base_revision=2,
        )
        regenerated = self.store.complete_image_generation(
            project["id"],
            "generator",
            [{"asset_id": regenerated_media["asset_id"], "kind": "image", "name": "regenerated.png"}],
            {
                "id": "history-regenerated",
                "type": "online-image",
                "status": "succeeded",
                "prompt": "重新生成",
                "outputs": [{"asset_id": regenerated_media["asset_id"], "kind": "image"}],
                "result_node_id": "generator",
            },
        )
        self.assertEqual(regenerated["revision"], registered["revision"] + 1)
        regenerated_node = next(node for node in regenerated["nodes"] if node["id"] == "generator")
        self.assertNotIn("needsRegeneration", regenerated_node)
        self.assertNotIn("generationState", regenerated_node)
        self.assertNotIn("resultDeleted", regenerated_node)
        self.assertNotIn("deletedOutputTombstones", regenerated_node)

        restored = self.store.restore_deleted_original_project_wide(
            project["id"], original["asset_id"], base_revision=regenerated["revision"],
        )
        restored_node = next(node for node in restored["canvas"]["nodes"] if node["id"] == "generator")
        self.assertEqual(
            [image["asset_id"] for image in restored_node["images"]],
            [regenerated_media["asset_id"]],
        )
        self.assertEqual(
            self.store.media_record(project["id"], original["asset_id"])["asset_id"],
            original["asset_id"],
        )
        restored_history = {record["id"]: record for record in restored["canvas"]["logs"]}
        self.assertEqual(restored_history["history-original"]["outputs"][0]["asset_id"], original["asset_id"])

        deleted_restored_original = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=restored["canvas"]["revision"],
        )["canvas"]
        current_generator = next(
            node for node in deleted_restored_original["nodes"] if node["id"] == "generator"
        )
        self.assertEqual(
            [image["asset_id"] for image in current_generator["images"]],
            [regenerated_media["asset_id"]],
        )
        self.assertNotIn("needsRegeneration", current_generator)
        self.assertNotIn("deletedOutputTombstones", current_generator)

    def test_multi_output_original_delete_keeps_remaining_outputs_visible_and_restorable(self):
        project = self.store.create_project("八输出原件")
        media = [
            self.store.import_media(
                project["id"],
                f"output-{index}".encode(),
                f"output-{index}.png",
                "image/png",
                {"type": "online_generation", "name": f"output-{index}.png"},
            )
            for index in range(8)
        ]
        outputs = [
            {"asset_id": item["asset_id"], "kind": "image", "name": f"output-{index}.png"}
            for index, item in enumerate(media)
        ]
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "multi-output-node",
                        "type": "smart-image",
                        "images": copy.deepcopy(outputs),
                        "runPrompt": "一次生成八张",
                    },
                },
                {
                    "op": "set_canvas_meta",
                    "logs": [
                        {
                            "id": "multi-output-log",
                            "type": "online-image",
                            "status": "succeeded",
                            "outputs": copy.deepcopy(outputs),
                            "result_node_id": "multi-output-node",
                        }
                    ],
                },
            ],
            base_revision=0,
        )

        before = self.store.canvas(project["id"])
        selected_output_id = before["logs"][0]["outputs"][3]["output_id"]
        deletion = self.store.delete_generated_original(
            project["id"], media[3]["asset_id"], base_revision=1,
            log_id="multi-output-log",
            output_id=selected_output_id,
            output_index=3,
        )
        deleted = deletion["canvas"]
        deleted_record = deleted["logs"][0]
        deleted_node = deleted["nodes"][0]
        expected_remaining = [item["asset_id"] for item in media[:3] + media[4:]]
        self.assertEqual(
            [item["asset_id"] for item in deleted_record["outputs"]],
            expected_remaining,
        )
        self.assertEqual(deleted_record["status"], "succeeded")
        self.assertNotIn("originalDeleted", deleted_record)
        self.assertEqual(
            deleted_record["deletedOutputTombstones"][0]["deleted_asset_id"],
            media[3]["asset_id"],
        )
        self.assertEqual(
            [item["asset_id"] for item in deleted_node["images"]],
            expected_remaining,
        )
        self.assertNotIn("needsRegeneration", deleted_node)
        self.assertNotIn("resultDeleted", deleted_node)

        restored = self.store.restore_deleted_original(
            project["id"], media[3]["asset_id"], base_revision=2,
            deletion_id=deletion["deleted"]["deletion_id"],
        )["canvas"]
        restored_record = restored["logs"][0]
        restored_node = restored["nodes"][0]
        self.assertEqual(
            [item["asset_id"] for item in restored_record["outputs"]],
            [item["asset_id"] for item in media],
        )
        self.assertEqual(
            [item["asset_id"] for item in restored_node["images"]],
            [item["asset_id"] for item in media],
        )
        self.assertEqual(restored_record["status"], "succeeded")
        self.assertNotIn("deletedOutputTombstones", restored_record)

    def test_restore_preserves_tasks_started_after_output_deletion(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        before_delete = self.store.canvas(project["id"])
        output_id = before_delete["logs"][0]["outputs"][0]["output_id"]

        deletion = self.store.delete_generated_original(
            project["id"],
            original["asset_id"],
            base_revision=1,
            log_id="history-original",
            output_id=output_id,
            output_index=0,
        )
        started = self.store.begin_image_generation(
            project["id"],
            "generator",
            {"taskId": "task-started-after-delete", "count": 2},
            base_revision=deletion["canvas"]["revision"],
        )

        restored = self.store.restore_deleted_original(
            project["id"],
            original["asset_id"],
            base_revision=started["revision"],
            deletion_id=deletion["deleted"]["deletion_id"],
        )["canvas"]

        generator = next(node for node in restored["nodes"] if node["id"] == "generator")
        self.assertEqual(
            [task["taskId"] for task in generator["pendingTasks"]],
            ["task-started-after-delete"],
        )
        self.assertEqual(generator["pending"], 2)
        self.assertEqual(generator["images"][0]["output_id"], output_id)

    def test_multi_output_delete_with_duplicate_asset_ids_removes_only_the_selected_output(self):
        project = self.store.create_project("重复引用四输出")
        media = [
            self.store.import_media(
                project["id"],
                f"dup-{index}".encode(),
                f"dup-{index}.png",
                "image/png",
                {"type": "online_generation", "name": f"dup-{index}.png"},
            )
            for index in range(4)
        ]
        a, b, c, _d = (item["asset_id"] for item in media)
        outputs = [
            {"asset_id": asset_id, "kind": "image", "name": "dup.png"}
            for asset_id in (a, b, a, c)
        ]
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "dup-output-node",
                        "type": "smart-image",
                        "images": copy.deepcopy(outputs),
                        "runPrompt": "重复引用",
                    },
                },
                {
                    "op": "set_canvas_meta",
                    "logs": [
                        {
                            "id": "dup-output-log",
                            "type": "online-image",
                            "status": "succeeded",
                            "outputs": copy.deepcopy(outputs),
                            "result_node_id": "dup-output-node",
                        }
                    ],
                },
            ],
            base_revision=0,
        )

        before_delete = self.store.canvas(project["id"])
        history_output_ids = [item.get("output_id") for item in before_delete["logs"][0]["outputs"]]
        node_output_ids = [item.get("output_id") for item in before_delete["nodes"][0]["images"]]
        self.assertEqual(len(set(history_output_ids)), 4)
        self.assertTrue(all(history_output_ids))
        self.assertEqual(node_output_ids, history_output_ids)
        selected_output_id = history_output_ids[2]

        response = self.client.post(
            f"/api/project-library/{project['id']}/original/delete",
            json={
                "asset_id": a,
                "base_revision": 1,
                "log_id": "dup-output-log",
                "output_id": selected_output_id,
                "output_index": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        deletion = response.json()
        deleted = deletion["canvas"]
        deleted_record = deleted["logs"][0]
        deleted_node = deleted["nodes"][0]
        self.assertEqual(
            [item["asset_id"] for item in deleted_record["outputs"]],
            [a, b, c],
        )
        self.assertEqual(deleted_record["status"], "succeeded")
        self.assertNotIn("originalDeleted", deleted_record)
        self.assertEqual(
            deleted_record["deletedOutputTombstones"][0]["deleted_asset_id"], a,
        )
        self.assertEqual(
            deleted_record["deletedOutputTombstones"][0]["deleted_output_id"],
            selected_output_id,
        )
        deletion_id = deletion["deleted"]["deletion_id"]
        self.assertEqual(
            deleted_record["deletedOutputTombstones"][0]["deletion_id"],
            deletion_id,
        )
        self.assertEqual(
            [item["asset_id"] for item in deleted_node["images"]],
            [a, b, c],
        )
        self.assertNotIn("needsRegeneration", deleted_node)
        self.assertEqual(
            self.store.media_record(project["id"], a)["asset_id"],
            a,
        )
        media_response = self.client.get(
            f"/api/project-library/{project['id']}/media/{a}"
        )
        self.assertEqual(media_response.status_code, 200, media_response.text)
        self.assertEqual(media_response.content, b"dup-0")

        missing_restore_identity = self.client.post(
            f"/api/project-library/{project['id']}/original/restore",
            json={"asset_id": a, "base_revision": 2},
        )
        self.assertEqual(missing_restore_identity.status_code, 422, missing_restore_identity.text)
        with self.assertRaises(InvalidMutation):
            self.store.restore_deleted_original(
                project["id"], a, base_revision=2,
            )
        self.assertEqual(self.store.canvas(project["id"]), deleted)

        restore_response = self.client.post(
            f"/api/project-library/{project['id']}/original/restore",
            json={
                "asset_id": a,
                "base_revision": 2,
                "deletion_id": deletion_id,
            },
        )
        self.assertEqual(restore_response.status_code, 200, restore_response.text)
        restored = restore_response.json()["canvas"]
        restored_record = restored["logs"][0]
        restored_node = restored["nodes"][0]
        self.assertEqual(
            [item["asset_id"] for item in restored_record["outputs"]],
            [a, b, a, c],
        )
        self.assertEqual(
            [item["asset_id"] for item in restored_node["images"]],
            [a, b, a, c],
        )
        self.assertEqual(restored_record["status"], "succeeded")
        self.assertNotIn("deletedOutputTombstones", restored_record)

    def test_occurrence_delete_uses_output_identity_when_history_reuses_a_producer_node(self):
        project = self.store.create_project("复用结果节点输出身份")
        first = self.store.import_media(
            project["id"], b"first", "first.png", "image/png",
            {"type": "online_generation", "name": "first.png"},
        )
        second = self.store.import_media(
            project["id"], b"second", "second.png", "image/png",
            {"type": "online_generation", "name": "second.png"},
        )
        first_output = {"asset_id": first["asset_id"], "kind": "image", "name": "first.png"}
        second_output = {"asset_id": second["asset_id"], "kind": "image", "name": "second.png"}
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {
                    "id": "reused-node", "type": "smart-image",
                    "images": [copy.deepcopy(first_output), copy.deepcopy(second_output)],
                }},
                {"op": "set_canvas_meta", "logs": [
                    {
                        "id": "first-log", "status": "succeeded",
                        "outputs": [copy.deepcopy(first_output)],
                        "resultNodeId": "reused-node",
                    },
                    {
                        "id": "second-log", "status": "succeeded",
                        "outputs": [copy.deepcopy(second_output)],
                        "resultNodeId": "reused-node",
                    },
                ]},
            ],
            base_revision=0,
        )
        before = self.store.canvas(project["id"])
        first_id = before["logs"][0]["outputs"][0]["output_id"]
        second_id = before["logs"][1]["outputs"][0]["output_id"]
        self.assertEqual(
            [item["output_id"] for item in before["nodes"][0]["images"]],
            [first_id, second_id],
        )

        deleted = self.store.delete_generated_original(
            project["id"], second["asset_id"], base_revision=1,
            log_id="second-log", output_id=second_id, output_index=0,
        )
        self.assertEqual(
            [item["output_id"] for item in deleted["canvas"]["nodes"][0]["images"]],
            [first_id],
        )
        restored = self.store.restore_deleted_original(
            project["id"], second["asset_id"], base_revision=2,
            deletion_id=deleted["deleted"]["deletion_id"],
        )["canvas"]
        self.assertEqual(
            [item["output_id"] for item in restored["nodes"][0]["images"]],
            [first_id, second_id],
        )
        deleted_first = self.store.delete_generated_original(
            project["id"], first["asset_id"], base_revision=3,
            log_id="first-log", output_id=first_id, output_index=0,
        )
        self.assertEqual(
            [item["output_id"] for item in deleted_first["canvas"]["nodes"][0]["images"]],
            [second_id],
        )
        restored_first = self.store.restore_deleted_original(
            project["id"], first["asset_id"], base_revision=4,
            deletion_id=deleted_first["deleted"]["deletion_id"],
        )["canvas"]
        self.assertEqual(
            [item["output_id"] for item in restored_first["nodes"][0]["images"]],
            [first_id, second_id],
        )

    def test_duplicate_occurrences_can_be_deleted_and_restored_out_of_order(self):
        project = self.store.create_project("重复输出逆序恢复")
        shared = self.store.import_media(
            project["id"], b"shared-bytes", "shared.png", "image/png",
            {"type": "online_generation", "name": "shared.png"},
        )
        outputs = [
            {"asset_id": shared["asset_id"], "kind": "image", "name": f"shared-{index}.png"}
            for index in range(2)
        ]
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {
                    "id": "shared-node", "type": "smart-image", "images": copy.deepcopy(outputs),
                }},
                {"op": "set_canvas_meta", "logs": [{
                    "id": "shared-log", "status": "succeeded", "outputs": copy.deepcopy(outputs),
                    "result_node_id": "shared-node",
                }]},
            ],
            base_revision=0,
        )
        opened = self.store.canvas(project["id"])
        first_id, second_id = [item["output_id"] for item in opened["logs"][0]["outputs"]]
        first = self.store.delete_generated_original(
            project["id"], shared["asset_id"], base_revision=1,
            log_id="shared-log", output_id=first_id, output_index=0,
        )
        second = self.store.delete_generated_original(
            project["id"], shared["asset_id"], base_revision=2,
            log_id="shared-log", output_id=second_id, output_index=0,
        )
        self.assertEqual(second["canvas"]["logs"][0]["outputs"], [])
        first_restored = self.store.restore_deleted_original(
            project["id"], shared["asset_id"], base_revision=3,
            deletion_id=first["deleted"]["deletion_id"],
        )["canvas"]
        self.assertEqual(
            [item["output_id"] for item in first_restored["logs"][0]["outputs"]],
            [first_id],
        )
        self.assertEqual(self.store.media_record(project["id"], shared["asset_id"])["asset_id"], shared["asset_id"])
        second_restored = self.store.restore_deleted_original(
            project["id"], shared["asset_id"], base_revision=4,
            deletion_id=second["deleted"]["deletion_id"],
        )["canvas"]
        self.assertEqual(
            [item["output_id"] for item in second_restored["logs"][0]["outputs"]],
            [first_id, second_id],
        )
        self.assertEqual(self.client.get(
            f"/api/project-library/{project['id']}/media/{shared['asset_id']}"
        ).content, b"shared-bytes")

    def test_output_identity_mismatch_fails_closed_without_mutating_project(self):
        project = self.store.create_project("输出身份冲突")
        media = self.store.import_media(
            project["id"], b"identity", "identity.png", "image/png",
            {"type": "online_generation", "name": "identity.png"},
        )
        output = {"asset_id": media["asset_id"], "kind": "image", "name": "identity.png"}
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {
                    "id": "identity-node", "type": "smart-image", "images": [copy.deepcopy(output)],
                }},
                {"op": "set_canvas_meta", "logs": [{
                    "id": "identity-log", "status": "succeeded", "outputs": [copy.deepcopy(output)],
                    "result_node_id": "identity-node",
                }]},
            ],
            base_revision=0,
        )
        before = self.store.canvas(project["id"])
        response = self.client.post(
            f"/api/project-library/{project['id']}/original/delete",
            json={
                "asset_id": media["asset_id"],
                "base_revision": 1,
                "log_id": "identity-log",
                "output_id": "output-00000000000000000000000000000000",
                "output_index": 0,
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.store.canvas(project["id"]), before)
        self.assertEqual(self.store.media_record(project["id"], media["asset_id"])["asset_id"], media["asset_id"])

    def test_occurrence_delete_does_not_mutate_another_history_record_sharing_the_asset(self):
        project = self.store.create_project("跨记录共享原件")
        media = self.store.import_media(
            project["id"], b"shared", "shared.png", "image/png",
            {"type": "online_generation", "name": "shared.png"},
        )
        output = {"asset_id": media["asset_id"], "kind": "image", "name": "shared.png"}
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {
                    "id": "owner-one", "type": "smart-image", "images": [copy.deepcopy(output)],
                }},
                {"op": "upsert_node", "node": {
                    "id": "owner-two", "type": "smart-image", "images": [copy.deepcopy(output)],
                }},
                {"op": "set_canvas_meta", "logs": [
                    {"id": "log-one", "status": "succeeded", "outputs": [copy.deepcopy(output)], "result_node_id": "owner-one"},
                    {"id": "log-two", "status": "succeeded", "outputs": [copy.deepcopy(output)], "result_node_id": "owner-two"},
                ]},
            ],
            base_revision=0,
        )
        before = self.store.canvas(project["id"])
        selected_output_id = before["logs"][0]["outputs"][0]["output_id"]
        untouched_log = copy.deepcopy(before["logs"][1])
        untouched_node = copy.deepcopy(before["nodes"][1])
        deleted = self.store.delete_generated_original(
            project["id"],
            media["asset_id"],
            base_revision=1,
            log_id="log-one",
            output_id=selected_output_id,
            output_index=0,
        )["canvas"]
        self.assertEqual(deleted["logs"][1], untouched_log)
        self.assertEqual(deleted["nodes"][1], untouched_node)
        self.assertEqual(self.store.media_record(project["id"], media["asset_id"])["asset_id"], media["asset_id"])

    def test_current_output_delete_requires_complete_identity_and_project_wide_delete_is_explicit(self):
        project = self.store.create_project("重复引用旧语义")
        media = [
            self.store.import_media(
                project["id"],
                f"old-{index}".encode(),
                f"old-{index}.png",
                "image/png",
                {"type": "online_generation", "name": f"old-{index}.png"},
            )
            for index in range(4)
        ]
        a, b, c, _d = (item["asset_id"] for item in media)
        outputs = [
            {"asset_id": asset_id, "kind": "image", "name": "old.png"}
            for asset_id in (a, b, a, c)
        ]
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "old-output-node",
                        "type": "smart-image",
                        "images": copy.deepcopy(outputs),
                        "runPrompt": "旧语义",
                    },
                },
                {
                    "op": "set_canvas_meta",
                    "logs": [
                        {
                            "id": "old-output-log",
                            "type": "online-image",
                            "status": "succeeded",
                            "outputs": copy.deepcopy(outputs),
                            "result_node_id": "old-output-node",
                        }
                    ],
                },
            ],
            base_revision=0,
        )

        before = self.store.canvas(project["id"])
        missing_identity = self.client.post(
            f"/api/project-library/{project['id']}/original/delete",
            json={"asset_id": a, "base_revision": 1},
        )
        self.assertEqual(missing_identity.status_code, 422, missing_identity.text)
        self.assertEqual(self.store.canvas(project["id"]), before)
        self.assertEqual(self.store.media_record(project["id"], a)["asset_id"], a)

        with self.assertRaises(InvalidMutation):
            self.store.delete_generated_original(
                project["id"], a, base_revision=1,
            )
        self.assertEqual(self.store.canvas(project["id"]), before)

        deleted = self.store.delete_generated_original_project_wide(
            project["id"], a, base_revision=1,
        )["canvas"]
        deleted_record = deleted["logs"][0]
        deleted_node = deleted["nodes"][0]
        self.assertEqual(
            [item["asset_id"] for item in deleted_record["outputs"]],
            [b, c],
        )
        self.assertEqual(
            [item["asset_id"] for item in deleted_node["images"]],
            [b, c],
        )
        self.assertEqual(deleted_record["status"], "succeeded")

    def test_delete_old_original_preserves_new_task_on_reused_result_node(self):
        project, _other_project, original, _reference, regenerated_media, _other_media = self._seed_projects()
        registered = self.store.begin_image_generation(
            project["id"],
            "generator",
            {
                "taskId": "task-new",
                "kind": "image",
                "count": 1,
                "providerId": "fake-provider",
                "model": "fake-model",
                "referenceAssetIds": [],
            },
            base_revision=1,
        )
        before_node = next(node for node in registered["nodes"] if node["id"] == "generator")

        deleted = self.store.delete_generated_original_project_wide(
            project["id"], original["asset_id"], base_revision=2,
        )["canvas"]
        deleted_node = next(node for node in deleted["nodes"] if node["id"] == "generator")

        self.assertEqual(deleted_node["images"], [])
        self.assertEqual(deleted_node["pendingTasks"], before_node["pendingTasks"])
        self.assertEqual(deleted_node["pending"], before_node["pending"])
        self.assertEqual(deleted_node["running"], before_node["running"])
        self.assertNotIn("needsRegeneration", deleted_node)
        self.assertNotIn("resultDeleted", deleted_node)

        completed = self.store.complete_image_generation(
            project["id"],
            "generator",
            [{"asset_id": regenerated_media["asset_id"], "kind": "image", "name": "regenerated.png"}],
            {
                "id": "history-new",
                "type": "online-image",
                "status": "succeeded",
                "prompt": "新任务完成",
                "outputs": [{"asset_id": regenerated_media["asset_id"], "kind": "image"}],
                "result_node_id": "generator",
                "task_id": "task-new",
            },
        )
        completed_node = next(node for node in completed["nodes"] if node["id"] == "generator")
        self.assertEqual(
            [image["asset_id"] for image in completed_node["images"]],
            [regenerated_media["asset_id"]],
        )
        self.assertNotIn("pendingTasks", completed_node)

    def test_destructive_history_delete_preserves_new_task_on_reused_result_node(self):
        project, _other_project, original, _reference, _regenerated_media, _other_media = self._seed_projects()
        registered = self.store.begin_image_generation(
            project["id"],
            "generator",
            {
                "taskId": "task-new",
                "kind": "image",
                "count": 1,
                "providerId": "fake-provider",
                "model": "fake-model",
                "referenceAssetIds": [],
            },
            base_revision=1,
        )
        before_node = next(node for node in registered["nodes"] if node["id"] == "generator")

        response = self.client.post(
            f"/api/project-library/{project['id']}/history/delete-with-media",
            json={"log_id": "history-original", "base_revision": 2},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        deleted_node = next(node for node in payload["canvas"]["nodes"] if node["id"] == "generator")
        self.assertEqual(deleted_node["images"], [])
        self.assertEqual(deleted_node["pendingTasks"], before_node["pendingTasks"])
        self.assertEqual(deleted_node["pending"], before_node["pending"])
        self.assertNotIn("generator", payload["reset_node_ids"])
        self.assertNotIn("needsRegeneration", deleted_node)

    def test_delete_is_blocked_while_an_active_generation_task_references_the_original(self):
        project, _other_project, original, _reference, downstream_media, _other_media = self._seed_projects()
        registered = self.store.begin_media_generation(
            project["id"],
            "downstream",
            {
                "taskId": "active-reference-task",
                "kind": "image",
                "count": 1,
                "referenceAssetIds": [original["asset_id"]],
            },
            base_revision=1,
            media_kind="image",
        )
        self.assertEqual(registered["revision"], 2)

        current = self.store.canvas(project["id"])
        original_output = next(
            record for record in current["logs"] if record["id"] == "history-original"
        )["outputs"][0]
        blocked = self.client.post(
            f"/api/project-library/{project['id']}/original/delete",
            json={
                "asset_id": original["asset_id"],
                "base_revision": 2,
                "log_id": "history-original",
                "output_id": original_output["output_id"],
                "output_index": 0,
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("生成任务", str(blocked.json().get("detail")))
        self.assertIn("active-reference-task", str(blocked.json().get("detail")))
        self.assertEqual(
            self.store.media_record(project["id"], original["asset_id"])["asset_id"],
            original["asset_id"],
        )

        completed = self.store.complete_media_generation(
            project["id"],
            "downstream",
            [{"asset_id": downstream_media["asset_id"], "kind": "image", "name": "downstream.png"}],
            {
                "id": "history-active-completed",
                "type": "online-image",
                "status": "succeeded",
                "prompt": "任务完成",
                "references": [{"asset_id": original["asset_id"], "kind": "image"}],
                "outputs": [{"asset_id": downstream_media["asset_id"], "kind": "image"}],
                "result_node_id": "downstream",
            },
            media_kind="image",
        )
        self.assertEqual(completed["logs"][-1]["id"], "history-active-completed")


if __name__ == "__main__":
    unittest.main()
