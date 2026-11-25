"""孤儿排队记录（node.pendingTasks）的服务端自动清理。

样本来自真实画布 D:/ff-qa/cleanup-de568/canvas.json.before：节点上挂着带 download 快照的
pendingTasks 条目，但它的 history 记录已被删掉、下载队列也没了，界面一直「排队 0s」。
孤儿 = 下载队列、CANVAS_TASKS、history 三处都找不到；任一处还有就不能动。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_project_library
from firefly_projects import ProjectStore

NODE_ID = "smart_xrla9u1uojnr"
ORPHAN_ID = "project_img_de56806a1f1e4daab904b5f6f5987e9d"


def _pending(task_id: str) -> dict:
    return {
        "taskId": task_id,
        "kind": "image",
        "count": 2,
        "providerId": "provider-mwiob4sh",
        "model": "gemini-3.1-flash-image-preview",
        "size": "3840x1648",
        "idempotencyKey": f"browser:key:{task_id}",
        "requestPayload": {"prompt": "sample", "provider_id": "provider-mwiob4sh", "n": 2,
                           "result_node_id": NODE_ID},
        "route": {"provider_id": "provider-mwiob4sh", "model": "gemini-3.1-flash-image-preview"},
        "createdAt": 1795578512.2207713,
        "download_generation": "3fad9fd321084b2497820ddb4c8e802f",
        "download": {"history_id": "generation-768c7078f5da4c1088b3351f00287bfd",
                     "task_id": task_id, "node_id": NODE_ID, "status": "download_failed", "items": []},
    }


class OrphanPendingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.project_id = self.store.create_project("孤儿排队")["id"]
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {"id": NODE_ID, "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        self.memory_ids = []

    def tearDown(self):
        with main.CANVAS_TASK_LOCK:
            for task_id in self.memory_ids:
                main.CANVAS_TASKS.pop(task_id, None)
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _add_pending(self, task_id: str):
        revision = int(self.store.open_project(self.project_id)["canvas"].get("revision") or 0)
        self.store.begin_image_generation(self.project_id, NODE_ID, _pending(task_id), base_revision=revision)
        self.store.flush(self.project_id)

    def _pending_ids(self):
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == NODE_ID)
        return {item["taskId"] for item in node.get("pendingTasks") or []}

    def _open(self, queued=None):
        queue = MagicMock()
        queue.find_task.side_effect = lambda _project_id, task_id: (queued or {}).get(task_id)
        broadcast = AsyncMock()
        with patch.object(api_project_library, "downloads", return_value=queue), \
                patch.object(main.manager, "broadcast_canvas_updated", broadcast):
            response = self.client.get(f"/api/project-library/{self.project_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), broadcast

    def test_orphan_pending_is_terminated_and_broadcast(self):
        self._add_pending(ORPHAN_ID)

        opened, broadcast = self._open()

        self.assertNotIn(ORPHAN_ID, self._pending_ids())
        node = next(item for item in opened["canvas"]["nodes"] if item["id"] == NODE_ID)
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(node.get("pending"), 0)
        records = [item for item in opened["canvas"]["history"] if item.get("task_id") == ORPHAN_ID]
        # 终态记录让已打开浏览器的 canvas_updated 合并把本地那份 pendingTasks 一起丢掉。
        self.assertEqual([item["status"] for item in records], ["failed"])
        self.assertEqual(records[0]["termination_reason"], "orphan_pending")
        broadcast.assert_awaited_once()
        self.assertEqual(broadcast.await_args.args[0], self.project_id)
        self.assertEqual(broadcast.await_args.args[1], int(opened["canvas"]["updated_at"]))
        reopened = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        try:
            persisted = reopened.open_project(self.project_id)["canvas"]
        finally:
            reopened.close()
        self.assertFalse(any(item.get("pendingTasks") for item in persisted["nodes"]))

    def test_tasks_known_to_any_source_are_retained(self):
        in_memory = "project_img_" + "a" * 32
        in_queue = "project_img_" + "b" * 32
        in_history = "project_img_" + "c" * 32
        agent = "agent_img_" + "d" * 32
        for task_id in (in_memory, in_queue, in_history, agent):
            self._add_pending(task_id)
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[in_memory] = {"id": in_memory, "type": "project-online-image",
                                            "project_id": self.project_id, "status": "running"}
        self.memory_ids.append(in_memory)
        history = list(self.store._histories.get(self.project_id, []))
        history.append({"id": "generation-" + "e" * 32, "task_id": in_history, "status": "succeeded",
                        "result_node_id": NODE_ID, "outputs": []})
        self.store._histories[self.project_id] = history

        _opened, broadcast = self._open(queued={in_queue: {"task_id": in_queue, "status": "download_failed"}})

        self.assertEqual(self._pending_ids(), {in_memory, in_queue, in_history, agent})
        broadcast.assert_not_awaited()

    def test_cross_origin_open_does_not_write(self):
        self._add_pending(ORPHAN_ID)
        broadcast = AsyncMock()
        with patch.object(main.manager, "broadcast_canvas_updated", broadcast):
            response = self.client.get(f"/api/project-library/{self.project_id}",
                                       headers={"origin": "https://evil.invalid"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn(ORPHAN_ID, self._pending_ids())
        broadcast.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
