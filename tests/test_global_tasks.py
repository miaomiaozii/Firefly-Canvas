"""issue #262：全局跨项目任务总控台（/api/global-tasks）。

数据面 = ADR 0021 tasks 租户语义（内存 CANVAS_TASKS + canvas.history 持久化终态）
+ usage 租户（#258，ops.db usage_turns，经 result_node_id 归属成本）。
逐任务取消复用 /api/project-library/{pid}/image-generation-tasks/{tid}/cancel
（前端 controlProjectImageTask 的同一端点），本票不新增取消面。
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from firefly_projects import ProjectStore


def make_history_record(task_id, project_id, *, status='succeeded', provider='p1',
                        model='m1', node='n1', prompt='demo prompt', run_ms=1234,
                        error=''):
    created = 1_700_000_000_000
    completed = created + run_ms
    return {
        'id': f'generation-{task_id}',
        'task_id': task_id,
        'idempotency_key': task_id,
        'type': 'online-image',
        'status': status,
        'prompt': prompt,
        'created_at': created,
        'createdAt': created,
        'completed_at': completed,
        'runMs': run_ms,
        'requested_provider_id': provider,
        'requested_model': model,
        'actual_provider_id': provider,
        'actual_provider_name': 'Provider One',
        'actual_model': model,
        'model': model,
        'route': {'provider_id': provider, 'model': model, 'provider_name': 'Provider One'},
        'result_node_id': node,
        'outputs': [],
        'error': error,
    }


class GlobalTasksConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0.05)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    def tearDown(self):
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.clear()
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _create_project(self, name):
        response = self.client.post("/api/project-library", json={"name": name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def _add_history(self, project_id, record):
        opened = self.store.open_project(project_id)
        canvas = opened["canvas"]
        history = list(canvas.get("history") or [])
        history.append(record)
        response = self.client.patch(
            f"/api/project-library/{project_id}/canvas",
            json={
                "mutations": [{"op": "set_canvas_meta", "history": history}],
                "base_revision": int(canvas.get("revision") or 0),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_empty_console_has_no_tasks_and_no_projects(self):
        data = self.client.get("/api/global-tasks").json()
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["projects"], [])
        self.assertIsInstance(data["generated_at"], int)

    def test_lists_durable_terminal_tasks_across_projects(self):
        p1 = self._create_project("Alpha")
        p2 = self._create_project("Beta")
        self._add_history(p1["id"], make_history_record("t1", p1["id"], status="succeeded", prompt="alpha image"))
        self._add_history(p2["id"], make_history_record("t2", p2["id"], status="failed", error="provider 500"))
        data = self.client.get("/api/global-tasks").json()
        by_id = {t["task_id"]: t for t in data["tasks"]}
        self.assertEqual(set(by_id), {"t1", "t2"})
        row = by_id["t1"]
        self.assertEqual(row["project_id"], p1["id"])
        self.assertEqual(row["project_name"], "Alpha")
        self.assertEqual(row["kind"], "image")
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["provider"], "p1")
        self.assertEqual(row["model"], "m1")
        self.assertEqual(row["result_node_id"], "n1")
        self.assertEqual(row["created_at"], 1_700_000_000_000)
        self.assertEqual(row["finished_at"], 1_700_000_001_234)
        self.assertAlmostEqual(row["duration_sec"], 1.234, places=3)
        self.assertEqual(by_id["t2"]["status"], "failed")
        self.assertEqual(by_id["t2"]["error"], "provider 500")
        self.assertFalse(by_id["t2"]["cancellable"])
        # 项目行：reveal 需要 id/name/canvas_id，成本汇总来自 usage 租户
        projects = {p["id"]: p for p in data["projects"]}
        self.assertEqual({p["name"] for p in projects.values()}, {"Alpha", "Beta"})
        self.assertEqual(projects[p1["id"]]["canvas_id"], p1["id"])
        self.assertEqual(projects[p1["id"]]["usage"]["turns"], 0)

    def test_usage_cost_and_tokens_attributed_by_result_node(self):
        p1 = self._create_project("Alpha")
        self._add_history(p1["id"], make_history_record("t1", p1["id"], node="n1"))
        self.store.record_usage_turn(
            p1["id"], provider="openai", model="gpt-x", node_id="n1",
            usage={"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        )
        data = self.client.get("/api/global-tasks").json()
        row = next(t for t in data["tasks"] if t["task_id"] == "t1")
        self.assertAlmostEqual(row["cost_usd"], 0.001, places=6)
        self.assertEqual(row["tokens"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        project = next(p for p in data["projects"] if p["id"] == p1["id"])
        self.assertEqual(project["usage"]["turns"], 1)
        self.assertAlmostEqual(project["usage"]["cost_usd"], 0.001, places=6)

    def test_task_without_node_usage_has_no_cost(self):
        p1 = self._create_project("Alpha")
        self._add_history(p1["id"], make_history_record("t1", p1["id"], node="n1"))
        # 用量记在别的节点上 → 不归因给本任务
        self.store.record_usage_turn(
            p1["id"], provider="openai", model="gpt-x", node_id="other",
            usage={"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.002},
        )
        data = self.client.get("/api/global-tasks").json()
        row = next(t for t in data["tasks"] if t["task_id"] == "t1")
        self.assertIsNone(row["cost_usd"])
        self.assertIsNone(row["tokens"])
        project = next(p for p in data["projects"] if p["id"] == p1["id"])
        self.assertAlmostEqual(project["usage"]["cost_usd"], 0.002, places=6)

    def test_live_inmemory_task_shown_and_cancel_reuses_project_endpoint(self):
        p1 = self._create_project("Alpha")
        pid = p1["id"]
        opened = self.store.open_project(pid)
        canvas = opened["canvas"]
        response = self.client.patch(
            f"/api/project-library/{pid}/canvas",
            json={
                "mutations": [
                    {"op": "upsert_node", "node": {"id": "n1", "type": "prompt", "x": 0, "y": 0,
                                                 "pendingTasks": [{"taskId": "live-1", "count": 1}], "pending": 1}},
                    {"op": "set_canvas_meta", "history": []},
                ],
                "base_revision": int(canvas.get("revision") or 0),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        now = time.time()
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS["live-1"] = {
                "id": "live-1",
                "type": "project-online-image",
                "project_id": pid,
                "result_node_id": "n1",
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "result": None,
                "error": "",
                "provider_id": "prov",
                "model": "mdl",
                "route": {"provider_id": "prov", "provider_name": "Prov", "model": "mdl"},
                "request_payload": {
                    "prompt": "live task",
                    "result_node_id": "n1",
                    "routing_mode": "auto",
                    "provider_id": "prov",
                    "model": "mdl",
                    "base_revision": 0,
                },
            }
        data = self.client.get("/api/global-tasks").json()
        row = next(t for t in data["tasks"] if t["task_id"] == "live-1")
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["kind"], "image")
        self.assertEqual(row["provider"], "prov")
        self.assertTrue(row["cancellable"])
        self.assertGreaterEqual(row["duration_sec"], 0.0)
        # 逐任务取消复用现有项目任务端点（前端 controlProjectImageTask 同一入口）
        cancel = self.client.post(f"/api/project-library/{pid}/image-generation-tasks/live-1/cancel")
        self.assertEqual(cancel.status_code, 200, cancel.text)
        data = self.client.get("/api/global-tasks").json()
        row = next(t for t in data["tasks"] if t["task_id"] == "live-1")
        self.assertEqual(row["status"], "cancelled")
        self.assertFalse(row["cancellable"])
        self.assertEqual(row["error"], "任务已取消")

    def test_live_task_overrides_stale_durable_record(self):
        p1 = self._create_project("Alpha")
        self._add_history(p1["id"], make_history_record("t1", p1["id"], status="failed", error="stale"))
        now = time.time()
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS["t1"] = {
                "id": "t1",
                "type": "project-online-image",
                "project_id": p1["id"],
                "status": "running",
                "created_at": now,
                "updated_at": now,
            }
        data = self.client.get("/api/global-tasks").json()
        rows = [t for t in data["tasks"] if t["task_id"] == "t1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "running")
        self.assertEqual(rows[0]["error"], "")
        self.assertTrue(rows[0]["cancellable"])

    def test_status_project_and_limit_filters(self):
        p1 = self._create_project("Alpha")
        p2 = self._create_project("Beta")
        self._add_history(p1["id"], make_history_record("t1", p1["id"], status="succeeded"))
        self._add_history(p1["id"], make_history_record("t2", p1["id"], status="failed", error="x"))
        self._add_history(p2["id"], make_history_record("t3", p2["id"], status="cancelled"))
        failed = self.client.get("/api/global-tasks", params={"status": "failed"}).json()
        self.assertEqual([t["task_id"] for t in failed["tasks"]], ["t2"])
        active = self.client.get("/api/global-tasks", params={"status": "active"}).json()
        self.assertEqual(active["tasks"], [])
        only_p1 = self.client.get("/api/global-tasks", params={"project_id": p1["id"]}).json()
        self.assertEqual({t["task_id"] for t in only_p1["tasks"]}, {"t1", "t2"})
        limited = self.client.get("/api/global-tasks", params={"limit": 2}).json()
        self.assertLessEqual(len(limited["tasks"]), 2)

    def test_tasks_sorted_by_created_desc(self):
        p1 = self._create_project("Alpha")
        record_old = make_history_record("old", p1["id"])
        record_new = make_history_record("new", p1["id"])
        record_new["created_at"] = record_old["created_at"] + 5_000
        record_new["createdAt"] = record_new["created_at"]
        record_new["completed_at"] = record_new["created_at"] + record_new["runMs"]
        self._add_history(p1["id"], record_old)
        self._add_history(p1["id"], record_new)
        data = self.client.get("/api/global-tasks").json()
        self.assertEqual([t["task_id"] for t in data["tasks"]], ["new", "old"])


if __name__ == '__main__':
    unittest.main()
