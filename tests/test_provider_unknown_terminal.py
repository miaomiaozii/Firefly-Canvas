"""#235：provider 未知任务终态化契约测试（先红后绿）。

处方 1：轮询状态机「连续 2 次 provider 明确返回未知/不存在 → 终态 fail，错误文案显式带 task ID」。
处方 2：上游 HTTP 404 → 结构化 not-found 载荷，不再裸抛 httpx 异常。
处方 4：视频轮询全部候选 404 → 显式「服务商侧任务不存在」（带 task ID）。

注：RunningHub 专属段（原处方 3）经维护者裁定不接入，验收作废，此处不设测试。
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_image_task_query
from app.services import generation

UNKNOWN_TASK_ID = 'task-235-unknown'


def _http_response(status_code, method, url, payload):
    return httpx.Response(status_code, request=httpx.Request(method, url), json=payload)


class WaitImageTaskUnknownTerminalTests(unittest.TestCase):
    """wait_for_image_task：连续未知/不存在 → 终态 fail（带 task_id），不再空转到 504。"""

    def _run_wait(self, payloads):
        fetch = AsyncMock(side_effect=payloads) if isinstance(payloads, list) else AsyncMock(return_value=payloads)
        with patch.object(main, 'fetch_image_task_payload', fetch), \
             patch.object(generation, 'IMAGE_TASK_TIMEOUT', 1.0), \
             patch.object(generation, 'IMAGE_POLL_INTERVAL', 0.01):
            result = asyncio.run(generation.wait_for_image_task(None, UNKNOWN_TASK_ID, provider=None))
        return result, fetch

    def test_consecutive_unknown_terminalizes_with_task_id(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run_wait({'status': 'NOT_FOUND'})
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn(UNKNOWN_TASK_ID, str(ctx.exception.detail))

    def test_single_unknown_then_success_still_succeeds(self):
        result, _ = self._run_wait([{'status': 'NOT_FOUND'}, {'status': 'SUCCESS', 'data': {'url': 'http://x/a.png'}}])
        self.assertEqual(result.get('status'), 'SUCCESS')

    def test_running_statuses_keep_polling(self):
        result, _ = self._run_wait([
            {'status': 'PROCESSING'},
            {'status': 'PENDING'},
            {'status': 'SUCCESS'},
        ])
        self.assertEqual(result.get('status'), 'SUCCESS')

    def test_not_found_status_counts_as_unknown(self):
        from app.core import IMAGE_TASK_UNKNOWN_STATUSES, image_task_status
        self.assertIn(image_task_status({'status': 'NOT_FOUND'}), IMAGE_TASK_UNKNOWN_STATUSES)
        self.assertIn('UNKNOWN', IMAGE_TASK_UNKNOWN_STATUSES)


class FetchImageTaskPayload404Tests(unittest.TestCase):
    """fetch_image_task_payload：404 → 结构化 not-found 载荷；其它状态码维持原样抛出。"""

    def test_404_maps_to_structured_not_found_payload(self):
        response = _http_response(404, 'GET', f'http://fake.example/v1/images/tasks/{UNKNOWN_TASK_ID}', {'message': 'task not found'})
        with patch.object(generation, 'httpx_request_with_transient_retries', AsyncMock(return_value=response)), \
             patch.object(main, 'provider_env_key_value', return_value='test-key'):
            payload = asyncio.run(generation.fetch_image_task_payload(None, UNKNOWN_TASK_ID, provider={'id': 'fake', 'base_url': 'http://fake.example/v1'}))
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get('status'), 'NOT_FOUND')
        self.assertIn(UNKNOWN_TASK_ID, str(payload.get('message') or payload))

    def test_non_404_http_error_still_raises(self):
        response = _http_response(500, 'GET', f'http://fake.example/v1/images/tasks/{UNKNOWN_TASK_ID}', {'message': 'boom'})
        with patch.object(generation, 'httpx_request_with_transient_retries', AsyncMock(return_value=response)), \
             patch.object(main, 'provider_env_key_value', return_value='test-key'):
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(generation.fetch_image_task_payload(None, UNKNOWN_TASK_ID, provider={'id': 'fake', 'base_url': 'http://fake.example/v1'}))


class ImageTaskQueryUnknownTerminalTests(unittest.TestCase):
    """/api/image-task-query：通用 provider 未知状态 → fail；进行中状态保持 running。"""

    def setUp(self):
        self.client = TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'})

    def tearDown(self):
        self.client.close()

    def _query(self, provider, body):
        with patch.object(main, 'get_api_provider', return_value=provider):
            return self.client.post('/api/image-task-query', json=body)

    def test_generic_unknown_status_is_failed(self):
        provider = {'id': 'fake-http', 'name': '假在线服务', 'protocol': 'openai', 'base_url': 'http://fake.example/v1'}
        body = {'provider_id': 'fake-http', 'task_id': 'generic-task-unknown'}
        fetch_mock = AsyncMock(return_value={'status': 'NOT_FOUND'})
        with patch.object(api_image_task_query, 'fetch_image_task_payload', fetch_mock):
            response = self._query(provider, body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result.get('status'), 'failed', result)
        self.assertEqual(result.get('task_id'), 'generic-task-unknown')
        fetch_mock.assert_awaited_once()

    def test_generic_running_status_still_running(self):
        provider = {'id': 'fake-http', 'name': '假在线服务', 'protocol': 'openai', 'base_url': 'http://fake.example/v1'}
        body = {'provider_id': 'fake-http', 'task_id': 'generic-task-running'}
        fetch_mock = AsyncMock(return_value={'status': 'PROCESSING'})
        with patch.object(api_image_task_query, 'fetch_image_task_payload', fetch_mock):
            response = self._query(provider, body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get('status'), 'running', response.text)


class VideoTaskNotFoundTests(unittest.TestCase):
    """wait_for_video_task：全部候选 404 → 显式「服务商侧任务不存在」+ task_id（不再裸抛 httpx 异常）。"""

    def test_all_candidates_404_raises_explicit_not_found(self):
        provider = {'id': 'fake-video', 'name': '假视频服务', 'protocol': 'openai', 'base_url': 'http://video.example/v1'}
        calls = []

        async def fake_get(url, headers=None):
            calls.append(url)
            return _http_response(404, 'GET', url, {'message': 'not found'})

        client = AsyncMock()
        client.get = fake_get
        with patch.object(main, 'provider_env_key_value', return_value='test-key'), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(generation.wait_for_video_task(client, provider, UNKNOWN_TASK_ID))
        self.assertEqual(ctx.exception.status_code, 502)
        detail = str(ctx.exception.detail)
        self.assertIn(UNKNOWN_TASK_ID, detail)
        self.assertIn('不存在', detail)
        self.assertEqual(len(calls), 3)

    def test_success_payload_still_returns(self):
        provider = {'id': 'fake-video', 'name': '假视频服务', 'protocol': 'openai', 'base_url': 'http://video.example/v1'}

        async def fake_get(url, headers=None):
            return _http_response(200, 'GET', url, {'data': {'status': 'SUCCESS', 'video_url': 'http://video.example/v.mp4'}})

        client = AsyncMock()
        client.get = fake_get
        with patch.object(main, 'provider_env_key_value', return_value='test-key'):
            raw = asyncio.run(generation.wait_for_video_task(client, provider, UNKNOWN_TASK_ID))
        self.assertEqual(raw['data']['status'], 'SUCCESS')

    def test_mixed_errors_still_raise_last_error(self):
        provider = {'id': 'fake-video', 'name': '假视频服务', 'protocol': 'openai', 'base_url': 'http://video.example/v1'}
        sequence = [
            _http_response(404, 'GET', 'u1', {}),
            _http_response(500, 'GET', 'u2', {}),
            _http_response(500, 'GET', 'u3', {}),
        ]

        async def fake_get(url, headers=None):
            return sequence.pop(0)

        client = AsyncMock()
        client.get = fake_get
        with patch.object(main, 'provider_env_key_value', return_value='test-key'), \
             self.assertRaises(httpx.HTTPStatusError) as ctx:
            asyncio.run(generation.wait_for_video_task(client, provider, UNKNOWN_TASK_ID))
        self.assertEqual(ctx.exception.response.status_code, 500)


if __name__ == '__main__':
    unittest.main()
