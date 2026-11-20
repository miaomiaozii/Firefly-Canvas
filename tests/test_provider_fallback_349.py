"""#349：请求里指定了服务商，就不能悄悄换成别家。

票面三条验收：
- 未知 provider_id → 4xx + 中文错误，而不是用别的服务商发请求；
- 找不到时报错要说清是哪个服务商；
- 前端不再默认 comfly（或至少显示真实服务商）。

写法说明：这一票的接缝（seam）是「按 id 解析服务商」这个函数 + 四个入口的路由行为。
路由测试只把服务商表换成内存里的假表，不碰网络，也不碰 data/api_providers.json。
"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_asset_library, api_canvas_llm, api_canvas_video, api_image_task_query
from app.services import providers as providers_svc

UNKNOWN_ID = 'no-such-provider-349'

PROVIDERS = [
    {'id': 'alpha', 'name': '甲平台', 'base_url': 'https://alpha.test/v1', 'enabled': True,
     'primary': True, 'chat_models': ['m-chat'], 'image_models': ['m-img'], 'video_models': ['m-vid']},
    {'id': 'beta', 'name': '乙平台', 'base_url': 'https://beta.test/v1', 'enabled': True,
     'primary': False, 'chat_models': ['m-chat'], 'image_models': ['m-img'], 'video_models': ['m-vid']},
    {'id': 'off', 'name': '停用平台', 'base_url': 'https://off.test/v1', 'enabled': False,
     'primary': False, 'chat_models': [], 'image_models': [], 'video_models': []},
]


class RequiredProviderLookupTests(unittest.TestCase):
    """解析层：未知 id 必须炸，空值按主服务商走但要留痕。"""

    def setUp(self):
        patcher = patch.object(main, 'load_api_providers', lambda: [dict(p) for p in PROVIDERS])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_id_raises_with_the_offending_id(self):
        with self.assertRaises(HTTPException) as ctx:
            providers_svc.get_api_provider_required(UNKNOWN_ID)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(UNKNOWN_ID, str(ctx.exception.detail))

    def test_unknown_id_does_not_silently_become_primary(self):
        # 关键回归：旧 get_api_provider 会把认不出的 id 换成主服务商，请求照发。
        provider = None
        try:
            provider = providers_svc.get_api_provider_required(UNKNOWN_ID)
        except HTTPException:
            pass
        if provider is not None:
            self.fail(f'未知服务商 {UNKNOWN_ID} 被换成了 {provider.get("id")}，不该放行')

    def test_empty_id_uses_primary_when_allowed(self):
        provider = providers_svc.get_api_provider_required('')
        self.assertEqual(provider['id'], 'alpha')

    def test_empty_id_can_be_rejected_when_caller_needs_the_real_one(self):
        # 查询任务这类入口：不知道任务属于哪家就不能瞎猜。
        with self.assertRaises(HTTPException) as ctx:
            providers_svc.get_api_provider_required('', allow_primary_when_empty=False)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('未指定', str(ctx.exception.detail))

    def test_disabled_provider_is_reported_by_name(self):
        with self.assertRaises(HTTPException) as ctx:
            providers_svc.get_api_provider_required('off')
        self.assertIn('停用平台', str(ctx.exception.detail))

    def test_id_is_matched_case_insensitively_and_trimmed(self):
        provider = providers_svc.get_api_provider_required('  BETA ')
        self.assertEqual(provider['id'], 'beta')


class EndpointBehaviourTests(unittest.TestCase):
    """生成、查询和认证都不能替用户挑另一家服务商。"""

    @classmethod
    def setUpClass(cls):
        # #342：全局 /api 同源守卫要求回环 Host
        cls.client = TestClient(main.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
        cls.addClassCleanup(cls.client.close)

    def _assert_rejected_without_upstream(self, url, payload, upstream_owner, upstream_name, *, field='provider_id'):
        for provider_id, detail in ((UNKNOWN_ID, f'未找到 API 平台：{UNKNOWN_ID}'),
                                    ('', '未指定 API 平台'), ('  ', '未指定 API 平台'),
                                    (None, '未指定 API 平台')):
            with self.subTest(url=url, provider_id=provider_id), ExitStack() as stack:
                upstream = stack.enter_context(patch.object(upstream_owner, upstream_name,
                    AsyncMock(side_effect=AssertionError('不该发上游请求'))))
                primary = {**PROVIDERS[0], 'protocol': 'apimart', 'api_key': 'fixture-key'}
                stack.enter_context(patch.object(main, 'load_api_providers', return_value=[primary, *PROVIDERS[1:]]))
                if upstream_owner is api_asset_library:
                    item = {'id': 'asset-349', 'kind': 'image', 'url': 'https://example.test/p.jpg',
                            'registrations': {'apimart': {'task_id': 'task-349'}}}
                    library = {'libraries': [{'categories': [{'items': [item]}]}]}
                    stack.enter_context(patch.object(api_asset_library, 'load_asset_library', return_value=library))
                    save = stack.enter_context(patch.object(api_asset_library, 'save_asset_library'))
                body = {**payload, field: provider_id} if provider_id is not None else payload
                response = self.client.post(url, json=body)
                upstream.assert_not_called()
                if upstream_owner is api_asset_library:
                    save.assert_not_called()
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn(detail, response.json()['detail'])

    def test_image_task_query_requires_known_provider(self):
        self._assert_rejected_without_upstream('/api/image-task-query', {'task_id': 'task-349'},
                                               api_image_task_query, 'fetch_image_task_payload')

    def test_canvas_video_requires_known_provider(self):
        self._assert_rejected_without_upstream('/api/canvas-video', {'model': 'm-vid', 'prompt': 'x'},
                                               api_canvas_video, 'execute_canvas_video')

    def test_canvas_llm_requires_known_provider(self):
        self._assert_rejected_without_upstream('/api/canvas-llm', {'model': 'm-chat', 'message': 'x'},
                                               api_canvas_llm, 'chat_request_latest', field='provider')

    def test_register_avatar_requires_known_provider(self):
        self._assert_rejected_without_upstream('/api/asset-library/items/asset-349/register-avatar', {},
                                               api_asset_library, 'upload_media_for_apimart')

    def test_avatar_status_requires_known_provider(self):
        self._assert_rejected_without_upstream('/api/asset-library/items/asset-349/avatar-status', {},
                                               api_asset_library, 'check_apimart_avatar_task')

    def test_canvas_video_uses_explicit_non_primary_provider(self):
        with patch.object(main, 'load_api_providers', return_value=PROVIDERS), \
             patch.object(api_canvas_video, 'execute_canvas_video', new_callable=AsyncMock) as upstream:
            upstream.return_value = {'status': 'fixture-ok'}
            response = self.client.post('/api/canvas-video', json={
                'provider_id': 'beta', 'model': 'm-vid', 'prompt': 'x',
            })
        self.assertEqual(response.status_code, 200, response.text)
        upstream.assert_awaited_once()
        self.assertEqual(upstream.await_args.args[1]['id'], 'beta')


class NoHardcodedComflyTests(unittest.TestCase):
    """守住票面那句「前端不再默认 comfly」+ 请求模型不带 comfly 默认值。"""

    def test_canvas_task_frontend_has_no_comfly_default(self):
        src = (ROOT / 'static/js/canvas-generation-tasks.js').read_text(encoding='utf-8')
        # 只看代码：注释里提到 `|| 'comfly'` 是为了说明改了什么，不算残留。
        code = '\n'.join(line.split('//', 1)[0] for line in src.splitlines())
        self.assertNotIn("|| 'comfly'", code, '前端不该再拿 comfly 当兜底服务商')

    def test_request_models_do_not_default_to_comfly(self):
        src = (ROOT / 'app/core.py').read_text(encoding='utf-8')
        for model in ('CanvasLLMRequest', 'CanvasVideoRequest', 'ImageTaskQueryRequest'):
            block = src.split(f'class {model}(', 1)[1].split('\nclass ', 1)[0]
            self.assertNotIn("'comfly'", block, f'{model} 的字段不该默认 comfly')

    def test_canvas_entry_routers_use_the_strict_lookup(self):
        for rel in ('app/routers/api_canvas_llm.py', 'app/routers/api_canvas_video.py',
                    'app/routers/api_image_task_query.py', 'app/routers/api_asset_library.py'):
            src = (ROOT / rel).read_text(encoding='utf-8')
            self.assertNotIn('get_api_provider(', src.replace('get_api_provider_required(', '').replace('get_api_provider_exact(', ''),
                             f'{rel} 还在用会静默换服务商的 get_api_provider')


if __name__ == '__main__':
    unittest.main()
