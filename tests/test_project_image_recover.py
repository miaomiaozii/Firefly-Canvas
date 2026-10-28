"""#234：项目图片任务手动取回契约测试（三层断裂）。

- 层3（根因）：失败路径必须把上游任务 id（upstream_task_id）写进内存任务与
  持久化历史记录——前端取回按钮与 GET 主动重查都依赖它。
- 层2：GET 端点对 failed/timeout 且有上游 id 的记录主动查询上游成品（取回语义），
  查到则写回 succeeded 终态并落画布；查不到则保持失败记录。
- 重启：服务重启（内存注册表清空）后，凭持久化记录仍可取回。
- /api/image-task-query：重构后端点契约不变（全局库落图）。
"""
import base64
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_image_task_query
from app.routers import api_project_library
from firefly_projects import ProjectStore

UPSTREAM_TASK_ID = 'upstream-task-abc-123'


def tiny_png_base64(color=(240, 120, 60)):
    buffer = io.BytesIO()
    Image.new('RGB', (3, 2), color).save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


class UpstreamImageError(Exception):
    """模拟 provider 生成失败且携带上游任务 id（适配器的真实形状）。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
        self.status_code = 502
        self.upstream_task_id = UPSTREAM_TASK_ID


def upstream_success_payload():
    return {'id': 'upstream-req-1', 'data': [{'b64_json': tiny_png_base64()}]}


class ProjectImageRecoverContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / 'projects'
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'})
        self.providers = [
            {
                'id': 'fake-http',
                'name': '假在线服务',
                'enabled': True,
                'protocol': 'openai',
                'image_models': ['fake-square'],
                'image_model_capabilities': {
                    'fake-square': {'sizes': ['1024x1024'], 'aspect_ratios': ['1:1'], 'resolutions': ['1k'], 'max_reference_images': 0},
                },
            }
        ]

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.PROJECT_IMAGE_GENERATION_ORPHANS.clear()
        self.temp_dir.cleanup()

    def wait_for_task(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.client.get(f'/api/canvas-image-tasks/{task_id}').json()
            if task['status'] in {'succeeded', 'failed', 'cancelled', 'timeout'}:
                return task
            time.sleep(0.02)
        self.fail(f'image task did not finish: {task}')

    def create_failing_project(self):
        project = self.store.create_project('取回契约')
        self.store.apply_mutations(
            project['id'],
            [{'op': 'upsert_node', 'node': {'id': 'result-node', 'type': 'smart-image', 'x': 10, 'y': 10, 'images': [], 'pending': 1}}],
            base_revision=0,
        )
        return project

    def submit_failing_task(self, project):
        """提交一个 provider 首查必失败（携带上游任务 id）的任务，返回 (task_id, 终态任务)。"""
        failing_generate = AsyncMock(side_effect=UpstreamImageError(f'上游生图失败：task_id={UPSTREAM_TASK_ID}'))
        payload = {
            'prompt': '失败后取回', 'routing_mode': 'auto', 'size': '1024x1024',
            'width': 1024, 'height': 1024, 'aspect_ratio': '1:1', 'resolution': '1k',
            'reference_asset_ids': [], 'media_type': 'image', 'result_node_id': 'result-node',
            'base_revision': 1,
        }
        with patch.object(main, 'load_api_providers', return_value=self.providers), patch.object(main, 'generate_ai_image', failing_generate):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            task_id = response.json()['task_id']
            task = self.wait_for_task(task_id)
        self.assertEqual(task['status'], 'failed', task)
        return task_id, task

    def get_project_task(self, project_id, task_id, query_mock=None):
        url = f'/api/project-library/{project_id}/image-generation-tasks/{task_id}'
        if query_mock is None:
            return self.client.get(url)
        with patch.object(api_image_task_query, 'fetch_image_task_payload', query_mock):
            return self.client.get(url)

    def canvas_node(self, project_id, node_id='result-node'):
        canvas = self.store.open_project(project_id)['canvas']
        return next(item for item in canvas['nodes'] if item['id'] == node_id)

    def history_records(self, project_id, task_id):
        canvas = self.store.open_project(project_id)['canvas']
        return [item for item in canvas['history'] if str(item.get('task_id') or '') == task_id]

    def test_failed_task_persists_upstream_task_id_for_recovery(self):
        """层3：失败后内存任务与持久化历史都携带上游任务 id（前端按钮/重启取回的根）。"""
        project = self.create_failing_project()
        task_id, _ = self.submit_failing_task(project)
        response = self.client.get(f"/api/project-library/{project['id']}/image-generation-tasks/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'failed')
        self.assertEqual(body.get('upstream_task_id'), UPSTREAM_TASK_ID)
        records = self.history_records(project['id'], task_id)
        self.assertEqual(records[-1]['status'], 'failed')
        self.assertEqual(records[-1].get('upstream_task_id'), UPSTREAM_TASK_ID)

    def test_manual_recovery_queries_upstream_and_lands_on_canvas(self):
        """层2：GET 对失败记录主动查上游，查到成品写回 succeeded 终态并落画布。"""
        project = self.create_failing_project()
        task_id, _ = self.submit_failing_task(project)
        query_mock = AsyncMock(return_value=upstream_success_payload())
        response = self.get_project_task(project['id'], task_id, query_mock)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'succeeded', body)
        self.assertEqual(len(body['result']['images']), 1)
        self.assertEqual(body['result']['result_node_id'], 'result-node')
        self.assertEqual(body['result']['final_node']['id'], 'result-node')
        self.assertEqual(len(body['result']['final_node']['images']), 1)
        query_mock.assert_awaited_once()
        # 落画布持久化：节点带上成品，历史写回 succeeded 终态（含 outputs）
        node = self.canvas_node(project['id'])
        self.assertEqual(len(node['images']), 1)
        succeeded = [item for item in self.history_records(project['id'], task_id) if item.get('status') == 'succeeded']
        self.assertEqual(len(succeeded), 1, succeeded)
        self.assertEqual(len(succeeded[0]['outputs']), 1)
        self.assertEqual(succeeded[0]['result_node_id'], 'result-node')
        self.assertTrue(succeeded[0].get('recovered'))

    def test_recovery_survives_restart_with_persisted_upstream_id(self):
        """重启：内存注册表清空后，GET 凭持久化记录（含上游 id）仍能主动取回落画布。"""
        project = self.create_failing_project()
        task_id, _ = self.submit_failing_task(project)
        self.store.flush(project['id'])
        # 模拟服务重启：内存注册表清空，只剩持久化 store
        main.CANVAS_TASKS.clear()
        query_mock = AsyncMock(return_value=upstream_success_payload())
        response = self.get_project_task(project['id'], task_id, query_mock)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'succeeded', body)
        self.assertEqual(len(body['result']['images']), 1)
        query_mock.assert_awaited_once()
        node = self.canvas_node(project['id'])
        self.assertEqual(len(node['images']), 1)
        succeeded = [item for item in self.history_records(project['id'], task_id) if item.get('status') == 'succeeded']
        self.assertEqual(len(succeeded), 1)

    def test_recovery_keeps_failed_when_upstream_still_running(self):
        """负向：上游仍在生成中——保持 failed 记录，不落半程终态。"""
        project = self.create_failing_project()
        task_id, _ = self.submit_failing_task(project)
        query_mock = AsyncMock(return_value={'status': 'processing'})
        response = self.get_project_task(project['id'], task_id, query_mock)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'failed')
        query_mock.assert_awaited_once()
        self.assertEqual(len(self.canvas_node(project['id']).get('images') or []), 0)
        self.assertEqual([item for item in self.history_records(project['id'], task_id) if item.get('status') == 'succeeded'], [])

    def test_recovery_keeps_failed_when_upstream_reports_failure(self):
        """负向：上游明确失败——保持 failed 记录。"""
        project = self.create_failing_project()
        task_id, _ = self.submit_failing_task(project)
        query_mock = AsyncMock(return_value={'status': 'FAILURE', 'message': '上游仍在失败'})
        response = self.get_project_task(project['id'], task_id, query_mock)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'failed')
        query_mock.assert_awaited_once()
        self.assertEqual([item for item in self.history_records(project['id'], task_id) if item.get('status') == 'succeeded'], [])

    def test_image_task_query_endpoint_still_saves_to_global_library(self):
        """/api/image-task-query 重构后契约不变：查到成品落全局库并广播。"""
        query_mock = AsyncMock(return_value=upstream_success_payload())
        with patch.object(api_image_task_query, 'fetch_image_task_payload', query_mock):
            response = self.client.post('/api/image-task-query', json={'provider_id': 'fake-http', 'task_id': UPSTREAM_TASK_ID})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'succeeded', body)
        self.assertEqual(len(body['images']), 1)
        query_mock.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
