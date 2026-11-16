#387：删除带 Agent 会话的项目后，宿主不得在原 live 目录重建 agent-events/；
# 残留目录不得让删除/清理永久 409（ProjectNotFound 是 ProjectStoreError 子类，
# 路由把「残留但无 project.json」的目录转成 409）。
# 验收（GitHub #387 + review 17:30）：带真实对话的项目删除后原目录不再出现
# agent-events/；再次删除/清理不返回 409。live 复测另跑（隔离实例），本文件锁语义。
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectNotFound, ProjectStore, ProjectStoreError  # noqa: E402


class TestLeftoverProjectDirectory(unittest.TestCase):
    """store 层：live 目录只剩 agent-events/ 残留时的行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = ProjectStore(root=self.root / "library", snapshot_delay=0.0)
        self.pid = self.store.create_project("Leftover Target")["id"]

    def tearDown(self):
        self._tmp.cleanup()

    def _make_leftover(self):
        """模拟：项目已入回收区，宿主又把 live 目录的 agent-events/ 写回来了。"""
        trash = self.store.project_trash_root / self.pid
        live = self.store.root / self.pid
        assert trash.is_dir() and not (live / "project.json").is_file()
        (live / "agent-events").mkdir(parents=True, exist_ok=True)
        (live / "agent-events" / f"{self.pid}.jsonl").write_text(
            '{"seq":1}\n', encoding="utf-8")
        return live

    def test_second_trash_removes_leftover_not_stuck_409(self):
        self.store.trash_project(self.pid)
        live = self._make_leftover()
        # 再次删除：语义上项目确实不在库 → ProjectNotFound（路由 404），
        # 且残留目录被顺手清掉——不留「怎么删都删不掉」的死角。
        with self.assertRaises(ProjectNotFound):
            self.store.trash_project(self.pid)
        self.assertFalse(live.exists(),
                         "#387：残留 live 目录（仅 agent-events/）必须在删除路径被清除")
        # 清掉残留后再删一次 → 仍是干净的 NotFound，不再有任何 409 素材
        with self.assertRaises(ProjectNotFound):
            self.store.trash_project(self.pid)

    def test_restore_not_blocked_by_leftover(self):
        self.store.trash_project(self.pid)
        self._make_leftover()
        # 恢复：目标处只有残留垃圾 → 清掉残留并成功恢复（旧行为=永久 409 素材：
        # 「项目库中已存在同名项目，无法恢复」）
        record = self.store.restore_project(self.pid)
        self.assertEqual(record["id"], self.pid)
        self.assertTrue((self.store.root / self.pid / "project.json").is_file())

    def test_real_project_still_protected(self):
        # 防误伤：live 目录里有 project.json 的正常项目不会被残留清理逻辑碰到。
        self.assertTrue((self.store.root / self.pid / "project.json").is_file())
        record = self.store.trash_project(self.pid)
        self.assertEqual(record["id"], self.pid)


class TestPersistEventGuardsProjectGone(unittest.TestCase):
    """agent_host 层：项目目录不存在（已移入回收区）时 _persist_event 不再建 agent-events/。"""

    def setUp(self):
        from app.services.agent_host import AgentHostManager, AgentSession

        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "proj-1"
        self.dir.mkdir()
        self.manager = AgentHostManager()
        self._patch = patch.object(self.manager, "_get_project_dir",
                                   lambda project_id: str(self.dir))
        self._patch.start()
        self.session = AgentSession(project_id="proj-1", node_id="n1", agent_cli="pi")

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_writes_while_project_present(self):
        (self.dir / "project.json").write_text("{}", encoding="utf-8")
        self.manager._persist_event(self.session, {"type": "agent_start"})
        self.assertTrue((self.dir / "agent-events" / "n1.jsonl").is_file())

    def test_no_rebuild_after_project_dir_gone(self):
        # 项目已移走（目录整体消失）：不得再 makedirs 把 agent-events/ 写回原位。
        import shutil
        shutil.rmtree(self.dir)
        self.manager._persist_event(self.session, {"type": "agent_start"})
        self.assertFalse((self.dir / "agent-events").exists(),
                         "#387：项目目录不存在时 _persist_event 必须跳过（不重建 live 目录）")
        self.assertFalse(self.dir.exists(),
                         "#387：连项目目录本身都不应被建回来")


if __name__ == "__main__":
    unittest.main()
