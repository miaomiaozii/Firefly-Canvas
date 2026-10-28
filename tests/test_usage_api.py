"""#258: 用量聚合查询 API 测试（GET /api/project-library/{id}/usage，租户隔离）。

- 无 ops.db/无用量行 = 空状态（200 + turns=0，ADR 0021）
- 项目不存在 = 404
- 租户隔离：其他项目的 usage 行不可见于本项目（每项目独立 ops.db 文件 + project_id 过滤）
- days 查询参数：只统计最近 N 天
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
from firefly_projects import ProjectStore


class ProjectUsageApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)
        self.project_id = self.store.create_project("用量API")["id"]
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

    def test_get_usage_empty(self):
        response = self.client.get(f"/api/project-library/{self.project_id}/usage")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["project_id"], self.project_id)
        self.assertEqual(body["turns"], 0)
        self.assertEqual(body["by_day"], [])
        self.assertEqual(body["by_model"], [])

    def test_get_usage_aggregated(self):
        self.store.record_usage_turn(self.project_id, provider="pi", model="anthropic/a",
                                     usage={"input": 10, "output": 5})
        self.store.record_usage_turn(self.project_id, provider="codex", model="gpt-5",
                                     usage={"prompt_tokens": 4, "completion_tokens": 1})
        response = self.client.get(f"/api/project-library/{self.project_id}/usage")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["turns"], 2)
        self.assertEqual(body["total"]["input_tokens"], 14)
        self.assertEqual(body["total"]["output_tokens"], 6)
        self.assertEqual(len(body["by_model"]), 2)

    def test_get_usage_missing_project_404(self):
        response = self.client.get("/api/project-library/no-such-project-xyz/usage")
        self.assertEqual(response.status_code, 404)

    def test_tenant_isolation_api(self):
        other_id = self.store.create_project("另一个")["id"]
        self.store.record_usage_turn(other_id, provider="pi", model="a", usage={"input": 99})
        body = self.client.get(f"/api/project-library/{self.project_id}/usage").json()
        self.assertEqual(body["turns"], 0)  # 其他项目行不可见
        other = self.client.get(f"/api/project-library/{other_id}/usage").json()
        self.assertEqual(other["turns"], 1)
        self.assertEqual(other["total"]["input_tokens"], 99)

    def test_days_query_param(self):
        self.store.record_usage_turn(self.project_id, provider="pi", model="a", usage={"input": 1})
        body = self.client.get(f"/api/project-library/{self.project_id}/usage", params={"days": 1}).json()
        self.assertEqual(body["turns"], 1)


if __name__ == "__main__":
    unittest.main()
