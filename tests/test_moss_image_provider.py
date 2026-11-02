"""#274 MOSS 生图 API（api.mosi.cn，OpenAI 兼容 /v1/images/generations）接入 TDD。

假服务器 httpx.MockTransport，零真实外连。事实来源：用户提供的 MOSS 文档
（2026-11-02）：n 仅 1；response_format 仅 url；支持 negative_prompt；
output_watermark {enabled:bool}（默认开，须传 enabled:false）；
size 每边 256-2048 且 16 倍数、总像素 ≤2097152；无参考图（纯文生图）。
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

import main

PROVIDER_ID = 'moss'
MOSS_MODEL = 'moss-image-sft-1.0'
BASE = 'https://api.mosi.cn/v1'
FAKE_KEY = 'sk-moss-test'


def moss_record():
    models = [MOSS_MODEL, 'moss-image-base-1.0']
    return {
        'id': PROVIDER_ID, 'name': 'MOSS', 'base_url': BASE, 'protocol': 'openai',
        'image_request_mode': 'openai', 'enabled': True, 'primary': False,
        'image_models': models,
        'image_model_capabilities': {m: {'media_types': ['image'], 'sizes': ['1024x1024'], 'max_reference_images': 0} for m in models},
        'recommended_models': [MOSS_MODEL],
    }


def fake_moss_server(captured):
    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == 'POST' and request.url.path == '/v1/images/generations':
            body = json.loads(request.content.decode('utf-8'))
            # MOSS 实测（2026-11-02 curl 双组对照）：quality 字段 → 400 invalid_field_value
            if 'quality' in body:
                return httpx.Response(400, json={'error': {'message': 'invalid image generation request', 'code': 'invalid_field_value'}})
            return httpx.Response(200, json={
                'created': 1730000000,
                'data': [{'url': f'https://api.mosi.cn/out/{body.get("model")}.png'}],
                'usage': {'generated_images': 1, 'width': 1024, 'height': 1024},
            })
        return httpx.Response(404, json={'error': {'message': f'unknown endpoint: {request.url.path}'}})
    return respond


class MossBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        with open(self.providers_file, 'w', encoding='utf-8') as f:
            json.dump([moss_record()], f, ensure_ascii=False)
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


class MossRegistrationTests(MossBase):
    def test_provider_key_env_convention(self):
        # provider id 'moss' → 约定密钥名 API_PROVIDER_MOSS_KEY（无代码特判）
        self.assertEqual(main.provider_key_env(PROVIDER_ID), 'API_PROVIDER_MOSS_KEY')

    def test_shape_survives_normalize_and_mode_defaults(self):
        provider = main.get_api_provider_exact(PROVIDER_ID)
        self.assertEqual(provider['protocol'], 'openai')
        self.assertEqual(main.effective_image_request_mode(provider, MOSS_MODEL), 'openai')
        self.assertFalse(main.detect_image_request_mode(provider['base_url'], provider['image_models']))

    def test_model_specs_registered(self):
        from app.core import model_spec_for
        spec = model_spec_for(MOSS_MODEL)
        self.assertTrue(spec, 'MOSS 模型必须登记进全局 model_specs.json')
        self.assertEqual(spec.get('api_type'), 'images_generations')
        self.assertEqual((spec.get('request_params') or {}).get('output_watermark'), {'enabled': False})


class MossImageEndToEndTests(MossBase):
    def test_generate_via_fake_server(self):
        from app.services.generation import generate_ai_image
        captured = []

        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(fake_moss_server(captured))
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', FakeClient):
            image, raw = asyncio.run(generate_ai_image(
                '一只戴宇航员头盔的猫', '1024x1024', 'medium', MOSS_MODEL,
                reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
            ))
        self.assertEqual(image, {'type': 'url', 'value': f'https://api.mosi.cn/out/{MOSS_MODEL}.png'})
        self.assertEqual(raw['usage']['generated_images'], 1)
        gen = [r for r in captured if r.url.path == '/v1/images/generations']
        self.assertEqual(len(gen), 1)
        req = gen[0]
        self.assertEqual(req.url.host, 'api.mosi.cn')
        self.assertEqual(req.headers.get('authorization'), f'Bearer {FAKE_KEY}')
        body = json.loads(req.content.decode('utf-8'))
        self.assertEqual(body['model'], MOSS_MODEL)
        self.assertEqual(body['prompt'], '一只戴宇航员头盔的猫')
        self.assertEqual(body['size'], '1024x1024')
        # MOSS 特有：关闭可见水印（经 model_specs request_params 注入）
        self.assertEqual(body.get('output_watermark'), {'enabled': False})
        # MOSS 实测：quality 被服务商拒收（400 invalid_field_value），必须按能力表裁剪
        self.assertNotIn('quality', body)

    def test_spec_request_params_flow_into_route(self):
        from app.core import route_image_from_spec
        spec = {'parameters': {'size': {}}, 'request_params': {'output_watermark': {'enabled': False}}}
        route = route_image_from_spec(MOSS_MODEL, '1024x1024', spec)
        self.assertEqual(route['params'].get('output_watermark'), {'enabled': False})


if __name__ == '__main__':
    unittest.main()
