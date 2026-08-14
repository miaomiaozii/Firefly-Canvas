import copy
import tempfile
import unittest
from pathlib import Path

from firefly_projects import ProjectStore


class HistoryDeletionIsolationTest(unittest.TestCase):
    def test_deleting_old_history_keeps_new_task_on_reused_result_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProjectStore(Path(temporary) / "projects", snapshot_delay=0)
            project = store.create_project("history isolation")
            project_id = project["id"]
            media = store.import_media(
                project_id,
                b"old-generated-original",
                "old.png",
                "image/png",
                {
                    "type": "online_generation",
                    "task_id": "task-old",
                    "result_node_id": "result-shared",
                },
            )
            asset_id = media["asset_id"]
            old_history = {
                "id": "history-old",
                "status": "succeeded",
                "resultNodeId": "result-shared",
                "request": {"task_id": "task-old"},
                "outputs": [{"kind": "image", "asset_id": asset_id}],
            }
            reused_result_node = {
                "id": "result-shared",
                "type": "image",
                "images": [{"asset_id": asset_id}],
                "runPrompt": "old completed generation",
                "generationState": "processing",
                "status": "pending",
            }
            store.apply_mutations(
                project_id,
                [
                    {"op": "upsert_node", "node": reused_result_node},
                    {"op": "set_canvas_meta", "logs": [old_history]},
                ],
                base_revision=0,
            )
            store.begin_image_generation(
                project_id,
                "result-shared",
                {
                    "taskId": "task-new",
                    "kind": "image",
                    "count": 1,
                    "providerId": "fake-provider",
                    "model": "fake-model",
                    "prompt": "new concurrent generation",
                    "pendingHistoryLogId": "pending-generation-task-new",
                },
                base_revision=1,
            )
            store.flush(project_id)

            before = store.open_project(project_id)
            before_node = copy.deepcopy(before["canvas"]["nodes"][0])
            before_media = copy.deepcopy(store.media_record(project_id, asset_id))
            media_path = Path(temporary) / "projects" / project_id / "media" / before_media["filename"]
            before_bytes = media_path.read_bytes()

            store.delete_history_record(
                project_id,
                "history-old",
                base_revision=before["canvas"]["revision"],
            )

            after = store.open_project(project_id)
            after_node = after["canvas"]["nodes"][0]
            self.assertEqual(after["canvas"]["logs"], [])
            self.assertEqual(after_node, before_node)
            self.assertEqual(after_node["pendingTasks"][0]["taskId"], "task-new")
            self.assertEqual(after_node["generationState"], "processing")
            self.assertEqual(after_node["pending"], 1)
            self.assertEqual(store.media_record(project_id, asset_id), before_media)
            self.assertEqual(media_path.read_bytes(), before_bytes)

    def test_deleting_unrelated_completed_record_keeps_two_active_task_cards_and_other_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProjectStore(Path(temporary) / "projects", snapshot_delay=0)
            project = store.create_project("history isolation two active tasks")
            project_id = project["id"]
            media = store.import_media(
                project_id,
                b"old-generated-original",
                "old.png",
                "image/png",
                {
                    "type": "online_generation",
                    "task_id": "task-old",
                    "result_node_id": "result-shared",
                },
            )
            asset_id = media["asset_id"]
            old_history = {
                "id": "history-old",
                "status": "succeeded",
                "resultNodeId": "result-shared",
                "request": {"task_id": "task-old"},
                "outputs": [{"kind": "image", "asset_id": asset_id}],
            }
            kept_history = {
                "id": "history-kept",
                "status": "succeeded",
                "resultNodeId": "result-shared",
                "request": {"task_id": "task-kept"},
                "outputs": [{"kind": "image", "asset_id": asset_id}],
            }
            reused_result_node = {
                "id": "result-shared",
                "type": "image",
                "images": [{"asset_id": asset_id}],
                "runPrompt": "old completed generation",
                "generationState": "processing",
                "status": "pending",
            }
            store.apply_mutations(
                project_id,
                [
                    {"op": "upsert_node", "node": reused_result_node},
                    {"op": "set_canvas_meta", "logs": [old_history, kept_history]},
                ],
                base_revision=0,
            )
            # Two concurrent active generation tasks on the same result node.
            for task_id, revision in (("task-new-a", 1), ("task-new-b", 2)):
                store.begin_image_generation(
                    project_id,
                    "result-shared",
                    {
                        "taskId": task_id,
                        "kind": "image",
                        "count": 1,
                        "providerId": "fake-provider",
                        "model": "fake-model",
                        "prompt": f"concurrent generation {task_id}",
                        "pendingHistoryLogId": f"pending-generation-{task_id}",
                    },
                    base_revision=revision,
                )
            store.flush(project_id)

            before = store.open_project(project_id)
            before_node = copy.deepcopy(before["canvas"]["nodes"][0])
            before_media = copy.deepcopy(store.media_record(project_id, asset_id))
            media_path = Path(temporary) / "projects" / project_id / "media" / before_media["filename"]
            before_bytes = media_path.read_bytes()
            self.assertEqual(
                [item["taskId"] for item in before_node["pendingTasks"]],
                ["task-new-a", "task-new-b"],
            )

            store.delete_history_record(
                project_id,
                "history-old",
                base_revision=before["canvas"]["revision"],
            )

            after = store.open_project(project_id)
            after_node = after["canvas"]["nodes"][0]
            # Only the targeted record disappears; the unrelated completed record survives.
            self.assertEqual([item["id"] for item in after["canvas"]["logs"]], ["history-kept"])
            # Both active task cards, their node projection and originals survive untouched.
            self.assertEqual(after_node, before_node)
            self.assertEqual(
                [item["taskId"] for item in after_node["pendingTasks"]],
                ["task-new-a", "task-new-b"],
            )
            self.assertEqual(after_node["generationState"], "processing")
            self.assertEqual(after_node["pending"], 2)
            self.assertEqual(store.media_record(project_id, asset_id), before_media)
            self.assertEqual(media_path.read_bytes(), before_bytes)


if __name__ == "__main__":
    unittest.main()
