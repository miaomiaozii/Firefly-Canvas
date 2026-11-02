"""#276 阿里云百炼 qwen-image-2.1-pro/turbo（OpenAI 兼容 /v1/images/generations）接入 TDD。

假服务器 httpx.MockTransport，零真实外连。事实来源：2026-11-02 curl 实测存档
（D:/tmp/mosi-smoke/）：同步出图；size '<W>x<H>'（'W*H' 400）；任意比例；
面积 262144–6553600；n≤4 实测可用；'image' 参考图 URL（单/双均出图）；
未知参数（quality/aspect_ratio/resolution）静默忽略但载荷仍裁剪；
watermark:false/negative_prompt/prompt_extend 接受；/models 含本模型。
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

import main

PROVIDER_ID = 'bailian'
PRO_MODEL = 'qwen-image-2.1-pro'
TURBO_MODEL = 'qwen-image-2.1-turbo'
BASE = 'https://llm-u3cjfye74dk9hoh9.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
FAKE_KEY = 'sk-bailian-test'

CAPS = {
    'media_types': ['image'], 'arbitrary_size': True, 'aspect_ratios': ['*'],
    'resolutions': ['auto', '1k', '2k'], 'max_reference_images': 2,
}


def bailian_record():
    models = [PRO_MODEL, TURBO_MODEL]
    return {
        'id': PROVIDER_ID, 'name': '百炼', 'base_url': BASE, 'protocol': 'openai',
        'image_request_mode': 'openai', 'enabled': True, 'primary': False,
        'image_models': models,
        'image_model_capabilities': {m: dict(CAPS) for m in models},
        'recommended_models': [PRO_MODEL],
    }


def fake_bailian_server(captured):
    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == 'POST' and request.url.path.endswith('/images/generations'):
            body = json.loads(request.content.decode('utf-8'))
            size = body.get('size')
            if size and '*' in str(size):
                # 实测：'1024*1024' → 400 "Field 'size' must use '<width>x<height>'"
                return httpx.Response(400, json={'error': {
                    'message': "Field 'size' must use '<width>x<height>' with positive integers.",
                    'code': 'InvalidParameter'}})
            if size:
                w, h = (int(x) for x in size.split('x'))
                if not 262144 <= w * h <= 6553600:
                    return httpx.Response(400, json={'error': {
                        'message': f'Image area must be between 262144 (512x512) and 6553600 (2560x2560) pixels. Got area={w * h}.',
                        'code': 'InvalidParameter'}})
            n = int(body.get('n') or 1)
            return httpx.Response(200, json={
                'created': 1730000000,
                'data': [{'url': f'https://dashscope.example/out-{i}.png'} for i in range(n)],
                'usage': {'width': 2512, 'height': 1680, 'image_count': n},
            })
        return httpx.Response(404, json={'error': {'message': f'unknown endpoint: {request.url.path}'}})
    return respond


class BailianBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        with open(self.providers_file, 'w', encoding='utf-8') as f:
            json.dump([bailian_record()], f, ensure_ascii=False)
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


class BailianRegistrationTests(BailianBase):
    def test_provider_key_env_convention(self):
        self.assertEqual(main.provider_key_env(PROVIDER_ID), 'API_PROVIDER_BAILIAN_KEY')

    def test_shape_survives_normalize_and_mode_defaults(self):
        provider = main.get_api_provider_exact(PROVIDER_ID)
        self.assertEqual(provider['protocol'], 'openai')
        self.assertEqual(main.effective_image_request_mode(provider, PRO_MODEL), 'openai')
        self.assertFalse(main.detect_image_request_mode(provider['base_url'], provider['image_models']))

    def test_model_specs_registered(self):
        from app.core import model_spec_for
        for model in (PRO_MODEL, TURBO_MODEL):
            spec = model_spec_for(model)
            self.assertTrue(spec, f'{model} 必须登记进全局 model_specs.json')
            self.assertEqual(spec.get('api_type'), 'images_generations')
            limits = spec.get('limits') or {}
            self.assertEqual(limits.get('max_pixels'), 6553600, '实测硬上限 2560²')
            self.assertGreaterEqual(limits.get('max_reference_images', 0), 2, '实测双参考图出图')


class BailianImageEndToEndTests(BailianBase):
    def test_generate_via_fake_server(self):
        from app.services.generation import generate_ai_image
        captured = []

        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(fake_bailian_server(captured))
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', FakeClient):
            image, raw = asyncio.run(generate_ai_image(
                '一只戴宇航员头盔的猫', '1328x1328', 'medium', PRO_MODEL,
                reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
            ))
        self.assertEqual(image['type'], 'url')
        self.assertTrue(image['value'].startswith('https://dashscope.example/'))
        gen = [r for r in captured if r.url.path.endswith('/images/generations')]
        self.assertEqual(len(gen), 1)
        req = gen[0]
        self.assertEqual(req.url.host, 'llm-u3cjfye74dk9hoh9.cn-beijing.maas.aliyuncs.com')
        self.assertEqual(req.headers.get('authorization'), f'Bearer {FAKE_KEY}')
        body = json.loads(req.content.decode('utf-8'))
        self.assertEqual(body['model'], PRO_MODEL)
        self.assertEqual(body['size'], '1328x1328')
        # 载荷卫生：百炼静默忽略但不得泄漏画布专有参数
        for key in ('aspect_ratio', 'resolution'):
            self.assertNotIn(key, body)


class BailianRoutingGateTests(BailianBase):
    def _request(self, refs=(), size='2688x1536', ratio='16:9'):
        from firefly_generation import ImageGenerationRequest
        return ImageGenerationRequest(prompt='猫', size=size, width=None, height=None,
                                      aspect_ratio=ratio, resolution='auto',
                                      reference_asset_ids=refs, media_type='image',
                                      quality='auto', count=1)

    def test_pinned_pro_routes_arbitrary_ratio(self):
        from firefly_generation import route_image_generation
        providers = json.load(open(self.providers_file, encoding='utf-8'))
        route = route_image_generation(self._request(), providers, provider_id=PROVIDER_ID, model=PRO_MODEL)
        self.assertEqual(route.model, PRO_MODEL)

    def test_references_up_to_two_allowed(self):
        from firefly_generation import route_image_generation
        providers = json.load(open(self.providers_file, encoding='utf-8'))
        route = route_image_generation(self._request(refs=('a1', 'a2')), providers,
                                       provider_id=PROVIDER_ID, model=PRO_MODEL)
        self.assertEqual(route.model, PRO_MODEL)


class BailianPayloadHygieneTests(BailianBase):
    def _captured_body(self, image_parameters):
        from app.services.generation import generate_ai_image
        captured = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={'created': 1, 'data': [{'url': 'https://dashscope.example/out.png'}]})

        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(respond)
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', FakeClient):
            asyncio.run(generate_ai_image('猫', '1536x864', 'auto', PRO_MODEL,
                                         reference_images=None, provider_id=PROVIDER_ID,
                                         preserve_model=True, image_parameters=image_parameters))
        body = json.loads([r for r in captured if r.url.path.endswith('/images/generations')][0].content.decode('utf-8'))
        return body

    def test_canvas_ratio_resolution_not_leaked(self):
        body = self._captured_body({'aspect_ratio': '16:9', 'resolution': 'auto'})
        for key in ('aspect_ratio', 'resolution'):
            self.assertNotIn(key, body, f'百炼画布参数不得泄漏进服务商载荷')
        self.assertEqual(body.get('size'), '1536x864')

    def test_oversized_request_clamped_to_bailian_area_limit(self):
        from app.core import clamp_size_to_spec
        limits = {'max_pixels': 6553600, 'min_edge': 256, 'size_multiple': 1}
        w, h = clamp_size_to_spec('4096x2304', limits).split('x')
        self.assertLessEqual(int(w) * int(h), 6553600, '实测 4096² 超面积 400')
        self.assertAlmostEqual(int(w) / int(h), 4096 / 2304, delta=0.05)
        # 面积区间内的请求原样通过（实测 800x2600 OK）
        self.assertEqual(clamp_size_to_spec('800x2600', limits), '800x2600')


if __name__ == '__main__':
    unittest.main()
