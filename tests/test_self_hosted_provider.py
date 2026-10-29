"""#271 自有服务器（OpenAI 兼容）provider 最小接入 —— 文本通路 TDD。

假服务器用 httpx.MockTransport 实现，只暴露 OpenAI 兼容端点
（/v1/chat/completions、/v1/images/generations、/v1/models，/v1/responses 404），
不产生任何真实外连。

provider 记录形态来自 tests/fixtures/self_hosted_provider.json，
与 data/api_providers.json 活样本 provider-ms5fzxu7 同形态
（protocol=openai + image_request_mode=openai + image_model_capabilities 显式声明）。
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

import main
from app.services.conversation import chat_request_latest, chat_text_from_raw, resolve_chat_provider

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'self_hosted_provider.json')
PROVIDER_ID = 'self_hosted'
CHAT_MODEL = 'qwen2.5-72b-instruct'
IMAGE_MODEL = 'flux-dev'
FAKE_BASE = 'http://127.0.0.1:8318/v1'
FAKE_KEY = 'sk-selfhosted-test'


def load_fixture_providers():
    with open(FIXTURE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def make_fake_openai_compat_server(captured):
    """OpenAI 兼容假服务器：vLLM 形态——无 /v1/responses（404 触发既有回退链）。"""

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if request.method == 'POST' and path == '/v1/chat/completions':
            body = json.loads(request.content.decode('utf-8'))
            return httpx.Response(200, json={
                'id': 'chatcmpl-fake-1',
                'object': 'chat.completion',
                'model': body.get('model', CHAT_MODEL),
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '来自假服务器的回复'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 6, 'total_tokens': 9},
            })
        if request.method == 'POST' and path == '/v1/images/generations':
            body = json.loads(request.content.decode('utf-8'))
            return httpx.Response(200, json={'data': [{'url': f'http://127.0.0.1:8318/out/{body.get("model")}.png', 'b64_json': None}]})
        if request.method == 'GET' and path == '/v1/models':
            return httpx.Response(200, json={'object': 'list', 'data': [{'id': CHAT_MODEL}, {'id': IMAGE_MODEL}]})
        # /v1/responses 及其余端点：假服务器不支持
        return httpx.Response(404, json={'error': {'message': f'unknown endpoint: {path}'}})

    return respond


class SelfHostedProviderBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        with open(self.providers_file, 'w', encoding='utf-8') as f:
            json.dump(load_fixture_providers(), f, ensure_ascii=False)
        self._patches = [
            patch.object(main, 'API_PROVIDERS_FILE', self.providers_file),
            patch.object(main, 'provider_env_key_value', return_value=FAKE_KEY),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.temp_dir.cleanup()

    def self_hosted(self):
        return main.get_api_provider_exact(PROVIDER_ID)


class SelfHostedProviderRegistrationTests(SelfHostedProviderBase):
    """248-A 注册样板与启发式免疫：记录形态经 normalize_provider 后不被主机启发式改写。"""

    def test_registry_record_shape_survives_normalize(self):
        provider = self.self_hosted()
        self.assertEqual(provider['protocol'], 'openai')
        self.assertEqual(provider['image_request_mode'], 'openai')
        self.assertEqual(provider['base_url'], FAKE_BASE)
        self.assertIn(CHAT_MODEL, provider['chat_models'])
        self.assertIn(IMAGE_MODEL, provider['image_models'])
        caps = provider['image_model_capabilities'][IMAGE_MODEL]
        self.assertEqual(caps.get('media_types'), ['image'])
        self.assertTrue(caps.get('arbitrary_size'))
        self.assertIn('max_reference_images', caps)

    def test_host_heuristics_do_not_rewrite_self_hosted(self):
        provider = self.self_hosted()
        # comfly 启发式（app/services/generation.py 的 is_comfly_provider：base_url/id 含 'comfly' 子串）
        self.assertNotIn('comfly', provider['base_url'].lower())
        self.assertNotIn('comfly', provider['id'])
        self.assertFalse(main.is_apimart_provider(provider))
        self.assertFalse(main.is_fhl_provider(provider))
        self.assertIsNone(main.locked_recommended_provider_rule(provider['id'], provider['name'], provider['base_url']))
        self.assertFalse(main.detect_image_request_mode(provider['base_url'], provider['image_models']))
        self.assertEqual(main.effective_protocol(provider, IMAGE_MODEL), 'openai')
        self.assertEqual(main.effective_image_request_mode(provider, IMAGE_MODEL), 'openai')

    def test_unknown_provider_does_not_silently_fall_back(self):
        # #248-B 语义：exact 解析未知 id 显式报错，不回退 primary
        with self.assertRaises(HTTPException) as ctx:
            main.get_api_provider_exact('does-not-exist')
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('未找到 API 平台', str(ctx.exception.detail))


class SelfHostedProviderChatEndToEndTests(SelfHostedProviderBase):
    """保命线：假服务器 /v1/chat/completions 端到端出文本。

    调用序列与 app/routers/api_canvas_llm.py 一致：
    resolve_chat_provider → chat_request_latest → chat_text_from_raw。
    """

    def setUp(self):
        super().setUp()
        self.captured = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(make_fake_openai_compat_server(self.captured)))

    def tearDown(self):
        asyncio.run(self.client.aclose())
        super().tearDown()

    def test_chat_end_to_end_text_via_fake_server(self):
        provider = self.self_hosted()

        async def scenario():
            base, hdrs, model = resolve_chat_provider(PROVIDER_ID, CHAT_MODEL, '')
            self.assertEqual(base, FAKE_BASE)
            self.assertEqual(model, CHAT_MODEL)
            raw, fmt = await chat_request_latest(
                self.client, base, hdrs, model,
                [{'role': 'user', 'content': '你好'}],
                stream=False, provider_cfg=provider,
            )
            return raw, fmt

        raw, fmt = asyncio.run(scenario())
        self.assertEqual(fmt, 'chat')
        self.assertEqual(chat_text_from_raw(raw, fmt).strip(), '来自假服务器的回复')
        completions = [r for r in self.captured if r.url.path == '/v1/chat/completions']
        self.assertEqual(len(completions), 1)
        req = completions[0]
        self.assertEqual(req.url.host, '127.0.0.1')
        self.assertEqual(req.headers.get('authorization'), f'Bearer {FAKE_KEY}')
        body = json.loads(req.content.decode('utf-8'))
        self.assertEqual(body['model'], CHAT_MODEL)


if __name__ == '__main__':
    unittest.main()
