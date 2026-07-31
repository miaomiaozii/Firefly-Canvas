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
from firefly_projects import ProjectNotFound, ProjectStore, ProjectStoreError


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
            f"/api/project-library/{project['id']}/original/delete",
            json={"asset_id": original["asset_id"], "base_revision": 2},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        payload = deleted.json()
        canvas = payload["canvas"]
        self.assertEqual(canvas["revision"], 3)
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/media/{original['asset_id']}").status_code, 404)
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
            f"/api/project-library/{project['id']}/original/restore",
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
            f"/api/project-library/{project['id']}/original/delete",
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

    def test_expired_original_trash_is_physically_cleaned_after_seven_days(self):
        project, _other_project, original, _reference, _downstream_media, _other_media = self._seed_projects()
        preview = Path(self.temp_dir.name) / "projects" / project["id"] / "previews" / f"{original['asset_id']}-512.webp"
        preview.write_bytes(b"derived-preview")
        deleted = self.store.delete_generated_original(project["id"], original["asset_id"], base_revision=1)
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
            f"/api/project-library/{project['id']}/original/delete",
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
            f"/api/project-library/{project['id']}/original/restore",
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

        deleted = self.store.delete_generated_original(
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

        deleted = self.store.delete_generated_original(
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
        deleted = self.store.delete_generated_original(
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
            self.store.delete_generated_original(
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
            self.store.delete_generated_original(
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
        self.store.delete_generated_original(project["id"], original["asset_id"], base_revision=1)

        def crash_after_canvas(checkpoint):
            if checkpoint == "after_canvas":
                raise SimulatedProcessCrash()

        self.store._original_transaction_checkpoint = crash_after_canvas
        with self.assertRaises(SimulatedProcessCrash):
            self.store.restore_deleted_original(
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
            self.store.delete_generated_original(
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
        deleted = self.store.delete_generated_original(
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

        restored = self.store.restore_deleted_original(
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

        deleted_restored_original = self.store.delete_generated_original(
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

        blocked = self.client.post(
            f"/api/project-library/{project['id']}/original/delete",
            json={"asset_id": original["asset_id"], "base_revision": 2},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("生成任务", str(blocked.json().get("detail")))
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
