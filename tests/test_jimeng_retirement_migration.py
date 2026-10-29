"""#236 即梦服务商全量退役：遗留 jimengPending 节点打开时定格为失败终态。

验收点（对应 issue）：
- 存量生成记录/节点里的 jimeng 运行时状态在打开项目时被定格为失败终态
  （failedGenerationTasks，message 携带 submit_id，引导重新生成/服务商侧核对）；
- 迁移幂等（二次打开不再变更）；
- 历史记录（canvas history）保留，不做强制删除。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore


def _legacy_jimeng_node():
    return {
        "id": "img-jimeng-1",
        "type": "image",
        "x": 0,
        "y": 0,
        "running": True,
        "pending": 1,
        "queued": False,
        "jimengPending": {
            "submitId": "sub-123",
            "querying": False,
            "queueInfo": {"status": "queued", "index": 3, "length": 10},
        },
        "pendingTasks": [{"taskId": "task-1", "label": "图片生成"}],
    }


class JimengRetirementMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.project = self.store.create_project("即梦遗留数据")

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _write_legacy_canvas(self, node):
        """把 canvas.json 覆盖为带即梦运行时状态的遗留文件（模拟历史导出）。"""
        self.store.open_project(self.project["id"])
        canvas_path = self.root / self.project["id"] / "canvas.json"
        persisted = json.loads(canvas_path.read_text(encoding="utf-8"))
        persisted["nodes"] = [node]
        canvas_path.write_text(json.dumps(persisted, ensure_ascii=False), encoding="utf-8")

    def _reopen_with_fresh_store(self):
        """用新实例强制重新从磁盘读取 canvas.json。"""
        store = ProjectStore(self.root, snapshot_delay=0)
        try:
            return store.open_project(self.project["id"])
        finally:
            store.close()

    def test_open_finalizes_jimeng_pending_node_to_failed_state(self):
        self._write_legacy_canvas(_legacy_jimeng_node())

        opened = self._reopen_with_fresh_store()
        node = next(n for n in opened["canvas"]["nodes"] if n["id"] == "img-jimeng-1")
        self.assertNotIn("jimengPending", node)
        self.assertFalse(node["running"])
        self.assertEqual(node["pending"], 0)
        self.assertFalse(node["queued"])
        self.assertNotIn("pendingTasks", node)
        self.assertTrue(int(node.get("runFinishedAt") or 0) > 0)
        failed = node["failedGenerationTasks"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["taskId"], "task-1")
        self.assertIn("sub-123", failed[0]["error"])

        # 落盘文件里不再含即梦运行时状态，且失败终态已持久化
        persisted_text = (self.root / self.project["id"] / "canvas.json").read_text(encoding="utf-8")
        self.assertNotIn("jimengPending", persisted_text)
        self.assertIn("failedGenerationTasks", persisted_text)

    def test_migration_is_idempotent(self):
        self._write_legacy_canvas(_legacy_jimeng_node())

        first = self._reopen_with_fresh_store()["canvas"]["nodes"][0]
        second = self._reopen_with_fresh_store()["canvas"]["nodes"][0]
        self.assertEqual(first["failedGenerationTasks"], second["failedGenerationTasks"])
        self.assertEqual(len(second["failedGenerationTasks"]), 1)

    def test_node_without_task_id_falls_back_to_submit_id(self):
        node = {
            "id": "img-jimeng-2",
            "type": "image",
            "x": 0,
            "y": 0,
            "running": True,
            "jimengPending": {"submitId": "sub-456"},
        }
        self._write_legacy_canvas(node)

        opened = self._reopen_with_fresh_store()
        migrated = next(n for n in opened["canvas"]["nodes"] if n["id"] == "img-jimeng-2")
        self.assertEqual(migrated["failedGenerationTasks"][0]["taskId"], "sub-456")
        self.assertIn("sub-456", migrated["failedGenerationTasks"][0]["error"])

    def test_history_records_are_preserved(self):
        self._write_legacy_canvas(_legacy_jimeng_node())
        # 写入一条引用即梦节点的生成历史（历史保留，不做强制删除）
        history_path = self.root / self.project["id"] / "history.json"
        history_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "id": "gen-1",
                            "type": "online-image",
                            "status": "succeeded",
                            "result_node_id": "img-jimeng-1",
                            "outputs": [],
                            "jimengPending": {"submitId": "sub-123"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        opened = self._reopen_with_fresh_store()
        history = opened["canvas"]["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], "gen-1")
        self.assertIn("jimengPending", history[0])


if __name__ == "__main__":
    unittest.main()
