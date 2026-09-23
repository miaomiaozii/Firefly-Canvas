import threading
import unittest
import tempfile
from pathlib import Path

from firefly_projects import ProjectStore, RevisionConflict


class ProjectStoreEdgeCasesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="edge-cases-")
        self.root = Path(self.tmp.name)
        self.store = ProjectStore(self.root / "projects", snapshot_delay=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_name_with_special_characters_roundtrips(self):
        name = "中文·标点！Emoji🎨引号\"斜杠\\/换行\n制表\tUnicode☕"
        project = self.store.create_project(name)
        stored = self.store.open_project(project["id"])["project"]
        self.assertEqual(stored["name"], name[:60])

    def test_very_long_project_name_is_truncated(self):
        project = self.store.create_project("x" * 5000)
        self.assertEqual(len(project["name"]), 60)

    def test_node_with_special_character_text_roundtrips(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        text = "🚀火箭\n换行\"引号'单引号\\反斜杠\tTab【中文】%{json}"
        self.store.apply_mutations(
            pid,
            [{"op": "upsert_node", "node": {"id": "n1", "type": "prompt", "text": text, "x": 0, "y": 0}}],
            base_revision=0,
        )
        canvas = self.store.canvas(pid)
        self.assertEqual(canvas["nodes"][0]["text"], text)

    def test_huge_node_text_roundtrips(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        big = ("段落" * 500) + ("\n" + "x" * 1000) * 20
        self.store.apply_mutations(
            pid,
            [{"op": "upsert_node", "node": {"id": "big", "type": "prompt", "text": big, "x": 0, "y": 0}}],
            base_revision=0,
        )
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["nodes"][0]["text"]), len(big))
        self.assertEqual(canvas["nodes"][0]["text"], big)

    def test_concurrent_mutations_with_retry_all_land(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        errors = []

        def worker(offset):
            try:
                for i in range(10):
                    while True:
                        try:
                            self.store.apply_mutations(
                                pid,
                                [{"op": "upsert_node", "node": {"id": f"n{offset}-{i}", "type": "image", "x": i, "y": offset}}],
                                base_revision=self.store.canvas(pid)["revision"],
                            )
                            break
                        except RevisionConflict:
                            continue
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["nodes"]), 40)

    def test_stale_revision_raises_conflict_and_keeps_canvas_intact(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        rev0 = self.store.canvas(pid)["revision"]
        self.store.apply_mutations(
            pid, [{"op": "upsert_node", "node": {"id": "a", "type": "prompt", "text": "a"}}], base_revision=rev0
        )
        with self.assertRaises(RevisionConflict):
            self.store.apply_mutations(
                pid,
                [{"op": "upsert_node", "node": {"id": "b", "type": "prompt", "text": "b"}}],
                base_revision=rev0,
            )
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["nodes"]), 1)
        self.assertEqual(canvas["nodes"][0]["id"], "a")

    def test_delete_and_recreate_same_node_id(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        self.store.apply_mutations(
            pid, [{"op": "upsert_node", "node": {"id": "n1", "type": "prompt", "text": "v1"}}], base_revision=0
        )
        rev = self.store.canvas(pid)["revision"]
        # 删除最后一个节点属于有意清空，需显式 force_empty=True（空画布覆盖防护）。
        self.store.apply_mutations(pid, [{"op": "remove_node", "node_id": "n1"}], base_revision=rev, force_empty=True)
        rev = self.store.canvas(pid)["revision"]
        self.store.apply_mutations(
            pid, [{"op": "upsert_node", "node": {"id": "n1", "type": "prompt", "text": "v2"}}], base_revision=rev
        )
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["nodes"]), 1)
        self.assertEqual(canvas["nodes"][0]["text"], "v2")

    def test_empty_canvas_roundtrips(self):
        project = self.store.create_project("edge")
        canvas = self.store.canvas(project["id"])
        self.assertEqual(canvas["nodes"], [])
        self.assertEqual(canvas["connections"], [])

    def test_edge_roundtrips_and_remove_edge_cleans(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        self.store.apply_mutations(
            pid,
            [{"op": "upsert_node", "node": {"id": "a", "type": "prompt"}}, {"op": "upsert_node", "node": {"id": "b", "type": "image"}}],
            base_revision=0,
        )
        rev = self.store.canvas(pid)["revision"]
        self.store.apply_mutations(
            pid,
            [{"op": "upsert_edge", "edge": {"from": "a", "to": "b", "kind": "input"}}],
            base_revision=rev,
        )
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["connections"]), 1)
        rev = canvas["revision"]
        self.store.apply_mutations(pid, [{"op": "remove_edge", "edge_id": "a->b::"}], base_revision=rev)
        canvas = self.store.canvas(pid)
        self.assertEqual(len(canvas["connections"]), 0)

    def test_media_with_unicode_filename(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        record = self.store.import_media(pid, b"fake-png-bytes", "照片🎨图.png", "image/png")
        stored = self.store.media_record(pid, record["asset_id"])
        self.assertEqual(stored["name"], "照片🎨图.png")
        self.assertEqual(stored["mime_type"], "image/png")

    def test_history_logs_roundtrip_via_set_canvas_meta(self):
        project = self.store.create_project("edge")
        pid = project["id"]
        self.store.apply_mutations(
            pid,
            [{"op": "set_canvas_meta", "history": [{"id": "log-1", "text": "历史🎯记录", "type": "info", "at": 1}]}],
            base_revision=0,
        )
        canvas = self.store.canvas(pid)
        self.assertTrue(any("历史🎯记录" in (r.get("text") or "") for r in canvas.get("history") or []))


if __name__ == "__main__":
    unittest.main()
