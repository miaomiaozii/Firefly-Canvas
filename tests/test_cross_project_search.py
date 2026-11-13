"""票 F（#324 / ADR-0026）：跨项目搜索。

- CrossProjectSearch 纯单元：字段白名单/snippet/cap/8MB 跳过/mtime 惰性失效/LRU。
- REST 端点 GET /api/search/projects：分组命中、空查询、422。
范围钉：只扫 canvas.json 字段+项目名，不读 agent-events。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from firefly_search import CrossProjectSearch


def _make_project(root: Path, project_id: str, name: str, nodes, updated_at: int = 1000) -> Path:
    directory = root / project_id
    directory.mkdir(parents=True)
    (directory / "project.json").write_text(
        json.dumps({"id": project_id, "name": name, "updated_at": updated_at}, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "canvas.json").write_text(
        json.dumps({"format": "firefly-canvas-v1", "id": project_id, "nodes": nodes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


class TestCrossProjectSearchUnit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-search-unit-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_field_whitelist_and_grouping(self):
        _make_project(self.root, "p1", "青柠项目", [
            {"id": "n1", "type": "smart-image", "title": "标题含关键词alpha", "runPrompt": "prompt one"},
            {"id": "n2", "type": "smart-text", "text": "正文 beta 出现"},
            {"id": "n3", "type": "smart-image", "runPrompt": "url 字段不应搜 http://alpha-notindexed.example"},
        ])
        _make_project(self.root, "p2", "另一个", [
            {"id": "n9", "type": "smart-agent", "title": "gamma agent 节点"},
        ], updated_at=2000)
        search = CrossProjectSearch(self.root)
        result = search.search("关键词")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["projects"][0]["project_id"], "p1")
        self.assertEqual(result["projects"][0]["hits"][0]["node_id"], "n1")
        result = search.search("ALPHA")  # 大小写不敏感；n3 runPrompt 含 alpha-notindexed
        self.assertEqual([hit["node_id"] for p in result["projects"] for hit in p["hits"]], ["n1", "n3"])
        result = search.search("alpha-notindexed")
        self.assertEqual(result["total"], 1)  # runPrompt 在白名单内，n3 命中

    def test_project_name_hit(self):
        _make_project(self.root, "p1", "芒果奶昔", [])
        search = CrossProjectSearch(self.root)
        result = search.search("芒果")
        self.assertEqual(result["projects"][0]["hits"][0]["kind"], "project")
        self.assertEqual(result["projects"][0]["hits"][0]["field"], "name")

    def test_per_project_cap_and_total_cap(self):
        nodes = [{"id": f"n{i}", "type": "smart-text", "text": "needle here"} for i in range(30)]
        _make_project(self.root, "p1", "cap-proj", nodes)
        search = CrossProjectSearch(self.root)
        result = search.search("needle")
        self.assertLessEqual(len(result["projects"][0]["hits"]), 20)

    def test_large_canvas_skipped_with_meta(self):
        directory = _make_project(self.root, "p1", "huge", [{"id": "n1", "text": "needle"}])
        canvas_path = directory / "canvas.json"
        payload = json.loads(canvas_path.read_text(encoding="utf-8"))
        payload["padding"] = "x" * (9 * 1024 * 1024)
        canvas_path.write_text(json.dumps(payload), encoding="utf-8")
        search = CrossProjectSearch(self.root, max_canvas_bytes=8 * 1024 * 1024)
        result = search.search("needle")
        self.assertEqual(result["total"], 0)
        self.assertIn("p1", result["skipped_large"])

    def test_mtime_invalidation(self):
        directory = _make_project(self.root, "p1", "fresh", [{"id": "n1", "text": "旧内容 stale"}])
        search = CrossProjectSearch(self.root)
        self.assertEqual(search.search("stale")["total"], 1)
        self.assertEqual(search.search("fresh2")["total"], 0)
        canvas_path = directory / "canvas.json"
        payload = json.loads(canvas_path.read_text(encoding="utf-8"))
        payload["nodes"][0]["text"] = "新内容 fresh2"
        canvas_path.write_text(json.dumps(payload), encoding="utf-8")
        stat = canvas_path.stat()
        os.utime(canvas_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertEqual(search.search("fresh2")["total"], 1)
        self.assertEqual(search.search("stale")["total"], 0)

    def test_empty_query(self):
        _make_project(self.root, "p1", "anything", [])
        search = CrossProjectSearch(self.root)
        self.assertEqual(search.search("  ")["projects"], [])

    def test_broken_canvas_ignored(self):
        directory = _make_project(self.root, "p1", "broken", [])
        (directory / "canvas.json").write_text("{not json", encoding="utf-8")
        search = CrossProjectSearch(self.root)
        self.assertEqual(search.search("broken")["total"], 1)  # 项目名仍可命中
        self.assertEqual(search.search("anything")["total"], 0)


class TestSearchEndpoint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-search-api-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.project_a = self.store.create_project("搜索甲")["id"]
        self.project_b = self.store.create_project("搜索乙")["id"]
        self.store.apply_mutations(self.project_a, [{
            "op": "upsert_node",
            "node": {"id": "sa-1", "type": "smart-image", "title": "西瓜节点A", "x": 0, "y": 0},
        }], base_revision=0)
        self.store.apply_mutations(self.project_b, [{
            "op": "upsert_node",
            "node": {"id": "sb-1", "type": "smart-text", "title": "西瓜节点B", "x": 0, "y": 0},
        }], base_revision=0)
        # snapshot_delay=0 时无自动 flush（_schedule_snapshot 直接 return），搜索读磁盘现物
        self.store.flush(self.project_a)
        self.store.flush(self.project_b)

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    def test_cross_project_hits_grouped(self):
        r = self.client.get("/api/search/projects", params={"q": "西瓜"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        ids = sorted(p["project_id"] for p in data["projects"])
        self.assertEqual(ids, sorted([self.project_a, self.project_b]))
        self.assertEqual(data["total"], 2)

    def test_project_name_hit_via_endpoint(self):
        r = self.client.get("/api/search/projects", params={"q": "搜索甲"})
        data = r.json()
        self.assertEqual([p["project_id"] for p in data["projects"]], [self.project_a])

    def test_empty_query_422(self):
        r = self.client.get("/api/search/projects", params={"q": ""})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
