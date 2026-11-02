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
        'image_model_capabilities': {m: {'media_types': ['image'], 'arbitrary_size': True, 'aspect_ratios': ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9', '9:21'], 'resolutions': ['auto', '1k', '2k'], 'max_reference_images': 0} for m in models},
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


class MossRoutingGateTests(MossBase):
    """#274 补丁：预检能力门必须按 MOSS 真实能力放行画布请求（尺寸/比例/档位任意，参考图拒绝）。"""

    def _request(self, refs=(), size='1536x864', ratio='16:9'):
        from firefly_generation import ImageGenerationRequest
        return ImageGenerationRequest(prompt='猫', size=size, width=None, height=None,
                                      aspect_ratio=ratio, resolution='auto',
                                      reference_asset_ids=refs, media_type='image',
                                      quality='auto', count=1)

    def test_pinned_moss_routes_ratio_request(self):
        from firefly_generation import route_image_generation
        providers = json.load(open(self.providers_file, encoding='utf-8'))
        route = route_image_generation(self._request(), providers, provider_id='moss', model=MOSS_MODEL)
        self.assertEqual(route.model, MOSS_MODEL)

    def test_reference_image_rejected_with_clear_incompatibles(self):
        from firefly_generation import ModelRoutingError, route_image_generation
        providers = json.load(open(self.providers_file, encoding='utf-8'))
        try:
            route_image_generation(self._request(refs=('asset-1',)), providers, provider_id='moss', model=MOSS_MODEL)
        except ModelRoutingError as exc:
            self.assertEqual(list(exc.incompatible_parameters), ['reference_count'])
        else:
            self.fail('参考图请求必须被拒，且只能因 reference_count')


class MossPayloadHygieneTests(MossBase):
    def _captured_body(self, image_parameters):
        from app.services.generation import generate_ai_image
        captured = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={'created': 1, 'data': [{'url': 'https://api.mosi.cn/out/x.png'}]})

        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(respond)
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', FakeClient):
            asyncio.run(generate_ai_image('猫', '1536x864', 'auto', MOSS_MODEL,
                                         reference_images=None, provider_id=PROVIDER_ID,
                                         preserve_model=True, image_parameters=image_parameters))
        body = json.loads([r for r in captured if r.url.path == '/v1/images/generations'][0].content.decode('utf-8'))
        return body

    def test_canvas_ratio_resolution_not_leaked(self):
        body = self._captured_body({'aspect_ratio': '16:9', 'resolution': 'auto'})
        for key in ('aspect_ratio', 'resolution', 'quality'):
            self.assertNotIn(key, body, f'MOSS 不收 {key}，必须按 unsupported_params 裁剪')
        self.assertEqual(body.get('size'), '1536x864')
        self.assertEqual(body.get('output_watermark'), {'enabled': False})

    def test_oversized_request_clamped_to_moss_limits(self):
        from app.core import clamp_size_to_spec
        limits = {'max_edge': 2048, 'min_edge': 256, 'max_pixels': 2097152, 'size_multiple': 16}
        w, h = clamp_size_to_spec('4096x2304', limits).split('x')
        self.assertLessEqual(int(w), 2048)
        self.assertLessEqual(int(w) * int(h), 2097152)
        self.assertEqual(int(w) % 16, 0)
        self.assertEqual(int(h) % 16, 0)
        # 保持比例（误差 ≤ 1 个步进）
        self.assertAlmostEqual(int(w) / int(h), 4096 / 2304, delta=0.05)
        self.assertEqual(clamp_size_to_spec('1536x864', limits), '1536x864')


if __name__ == '__main__':
    unittest.main()
