"""#331 本地 ComfyUI（MiniMax-H3-Image GGUF）接入 TDD。

事实来源：维护者本地 ComfyUI 0.45.2 + ComfyUI-GGUF 实测（2026-11-14），见 issue #331。
关键契约（都不在常见文档里，改代码前先读这里）：
- /prompt 收 API 格式图，返回 prompt_id；/history/{id} 轮询；/view 取图。
- 参考图必须先 /upload/image 拿到 input 目录里的名字，再挂到节点 4 的
  `images: {'image_1': ['ref1', 0]}`（autogrow 输入，实测出来的形状）。
- 节点 4（MiniMaxH3ImageConditioning）自己编码 latent，并同时输出 LATENT；
  negative 用 ConditioningZeroOut 零掉同一条 conditioning。
- 尺寸步长 8（作者 ResolutionSelector 习惯），1k/2k/4k 档 = 4/8/16 MP。
- 该接口不幂等（超时也算跑了一轮算力），适配器不重试、不自动重提。

假服务器 httpx.MockTransport，零真实外连。
"""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

import main

PROVIDER_ID = 'comfyui'
COMFYUI_MODEL = 'comfyui-h3-image'
BASE = 'http://127.0.0.1:8188'
# 真实可解码的 PNG（假服务器的 /view 响应体，也是图生图的参考图）。
def _build_png(size=(64, 64), color=(210, 140, 90)):
    from io import BytesIO as _BytesIO
    from PIL import Image as _Image
    buf = _BytesIO()
    _Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


TINY_PNG = _build_png()


def comfyui_record():
    return {
        'id': PROVIDER_ID,
        'name': 'ComfyUI（本地）',
        'base_url': BASE,
        'protocol': 'comfyui',
        'enabled': True,
        'primary': False,
        'image_models': [COMFYUI_MODEL],
        'recommended_models': [COMFYUI_MODEL],
    }


class FakeComfy:
    """最小可用的 ComfyUI 假服务器：/prompt、/history/{id}、/view、/upload/image、/queue。"""

    def __init__(self, *, prompt_status=200, history_steps=None, image_bytes=TINY_PNG):
        self.captured = []
        self.prompt_id = 'prompt-abc-123'
        self.prompt_status = prompt_status
        self.image_bytes = image_bytes
        # 默认：第一次查 history 还在排队，第二次就完成。
        self.history_steps = history_steps if history_steps is not None else [{}, 'done']
        self.history_calls = 0

    def history_body(self):
        index = min(self.history_calls, len(self.history_steps) - 1)
        step = self.history_steps[index]
        self.history_calls += 1
        if step == 'done':
            return {
                self.prompt_id: {
                    'status': {'status_str': 'success', 'completed': True, 'messages': []},
                    'outputs': {'9': {'images': [{'filename': 'canvas_00001_.png', 'subfolder': 'MiniMax-H3-Image', 'type': 'output', 'metadata': {'kind': 'image', 'width': 2720, 'height': 1536}}]}},
                }
            }
        if step == 'error':
            return {
                self.prompt_id: {
                    'status': {
                        'status_str': 'error',
                        'completed': False,
                        'messages': [['execution_error', {'node_id': '7', 'exception_message': 'CUDA out of memory'}]],
                    },
                    'outputs': {},
                }
            }
        return {}

    def respond(self, request):
        self.captured.append(request)
        path = request.url.path
        if request.method == 'POST' and path == '/prompt':
            return httpx.Response(self.prompt_status, json={
                'error': {'type': 'prompt_outputs_failed_validation', 'message': 'Prompt outputs failed validation'},
                'node_errors': {'4': {'class_type': 'MiniMaxH3ImageConditioning', 'errors': [{'message': 'width must be a multiple of 8'}]}},
            }) if self.prompt_status != 200 else httpx.Response(200, json={'prompt_id': self.prompt_id, 'number': 1, 'node_errors': {}})
        if request.method == 'GET' and path.startswith('/history/'):
            return httpx.Response(200, json=self.history_body())
        if request.method == 'GET' and path == '/view':
            return httpx.Response(200, content=self.image_bytes, headers={'content-type': 'image/png'})
        if request.method == 'POST' and path == '/upload/image':
            return httpx.Response(200, json={'name': 'canvas_ref_1.png', 'subfolder': '', 'type': 'input'})
        if request.method == 'GET' and path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        return httpx.Response(404, json={'error': f'unknown endpoint: {path}'})

    def requests_to(self, path, method=None):
        return [r for r in self.captured if r.url.path == path and (method is None or r.method == method)]


class ComfyBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        # 产物洛盘目录：适配器把 4K PNG 写成文件并回 /output/ URL，不再塞 base64 进 JSON
        self.output_dir = os.path.join(self.temp_dir.name, 'output')
        os.makedirs(self.output_dir, exist_ok=True)
        with open(self.providers_file, 'w', encoding='utf-8') as f:
            json.dump([comfyui_record()], f, ensure_ascii=False)
        self._patches = [
            patch.object(main, 'API_PROVIDERS_FILE', self.providers_file),
            patch.object(main, 'OUTPUT_OUTPUT_DIR', self.output_dir),
        ]
        for p in self._patches:
            p.start()

    def run_with_project_scope(self, project_id, fake, coro_factory, **module_patches):
        """项目媒体参考图只能由“所属项目任务”使用（`require_project_media_values_scope`），
        生产路径由请求层设这个上下文变量；单测里要自己设，否则会 400。"""
        from app.core import PROJECT_MEDIA_ADAPTER_PROJECT
        token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
        try:
            return self.run_with(fake, coro_factory, **module_patches)
        finally:
            PROJECT_MEDIA_ADAPTER_PROJECT.reset(token)

    def run_with(self, fake, coro_factory, **module_patches):
        """把 httpx.AsyncClient 换成走假服务器的客户端，并把轮询压到 0 秒。"""
        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(fake.respond)
                super().__init__(*args, **kwargs)

        from app.services import generation
        patches = [
            patch('httpx.AsyncClient', FakeClient),
            patch.object(generation, 'COMFYUI_IMAGE_POLL_INTERVAL', 0),
            patch.object(generation, 'COMFYUI_IMAGE_QUEUE_INTERVAL', 0),
        ]
        patches.extend(patch.object(generation, name, value) for name, value in module_patches.items())
        started = [p.start() for p in patches]
        try:
            return asyncio.run(coro_factory())
        finally:
            for p in reversed(patches):
                p.stop()


class ComfyRegistrationTests(ComfyBase):
    def test_protocol_registered_and_pinned(self):
        from app.core import FIXED_PROTOCOL_PROVIDER_IDS, SUPPORTED_PROVIDER_PROTOCOLS, provider_protocol
        self.assertIn('comfyui', SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertIn('comfyui', FIXED_PROTOCOL_PROVIDER_IDS)
        provider = main.get_api_provider_exact(PROVIDER_ID)
        self.assertEqual(provider_protocol(provider), 'comfyui')

    def test_normalize_defaults_base_url(self):
        from app.core import COMFYUI_DEFAULT_BASE_URL, normalize_provider
        provider = normalize_provider({'id': 'comfyui', 'name': '本地', 'protocol': 'comfyui'})
        self.assertEqual(provider['protocol'], 'comfyui')
        self.assertEqual(provider['base_url'], COMFYUI_DEFAULT_BASE_URL)

    def test_model_override_cannot_change_protocol(self):
        from app.core import normalize_provider
        provider = normalize_provider({
            'id': 'comfyui', 'name': '本地', 'protocol': 'comfyui',
            'model_protocols': {COMFYUI_MODEL: 'openai'},
        })
        self.assertEqual(provider['protocol'], 'comfyui')

    def test_model_spec_registered(self):
        from app.core import model_spec_for
        spec = model_spec_for(COMFYUI_MODEL)
        self.assertTrue(spec, '本地 H3 模型必须登记进 model_specs.json')
        self.assertEqual(spec.get('api_type'), 'comfyui_h3_images')
        self.assertEqual((spec.get('limits') or {}).get('n'), 1)
        self.assertEqual((spec.get('limits') or {}).get('max_reference_images'), 16)


class ComfyResolutionTests(unittest.TestCase):
    def test_matches_published_size_table(self):
        from app.services.comfyui_workflows import comfyui_h3_resolution
        rows = [
            ('4k', '1:1', (4096, 4096)),
            ('4k', '3:2', (5016, 3344)),
            ('4k', '2:3', (3344, 5016)),
            ('4k', '4:3', (4728, 3544)),
            ('4k', '3:4', (3544, 4728)),
            ('4k', '16:9', (5464, 3072)),
            ('4k', '9:16', (3072, 5464)),
            ('4k', '21:9', (6256, 2680)),
            ('2k', '1:1', (2896, 2896)),
            ('2k', '16:9', (3864, 2176)),
            ('1k', '1:1', (2048, 2048)),
        ]
        for tier, ratio, expected in rows:
            self.assertEqual(comfyui_h3_resolution(ratio, tier), expected, f'{tier} {ratio}')

    def test_every_side_is_multiple_of_eight(self):
        from app.services.comfyui_workflows import comfyui_h3_resolution
        for tier in ('1k', '2k', '4k'):
            for ratio in ('1:1', '16:9', '9:16', '4:3', '3:4', '4:5', '5:4', '3:2', '2:3', '21:9', '9:21'):
                width, height = comfyui_h3_resolution(ratio, tier)
                self.assertEqual(width % 8, 0, f'{tier} {ratio} {width}')
                self.assertEqual(height % 8, 0, f'{tier} {ratio} {height}')

    def test_unknown_tier_derives_from_size_area(self):
        from app.services.comfyui_workflows import COMFYUI_MP_TIERS, comfyui_h3_resolution
        # 不传档位时按 size 面积反推（1536×1024 ≈ 1.5MP → H3 地板 4MP 档），不再无条件落到 2K
        width, height = comfyui_h3_resolution('', '', '1536x1024')
        self.assertAlmostEqual(width * height / (COMFYUI_MP_TIERS['1k'] * 1024 * 1024), 1.0, places=1)
        self.assertAlmostEqual((width / height) / 1.5, 1.0, places=2)

    def test_parse_aspect_accepts_colon_x_slash(self):
        from app.services.comfyui_workflows import comfyui_parse_aspect
        self.assertEqual(comfyui_parse_aspect('3:2'), (3.0, 2.0))
        self.assertEqual(comfyui_parse_aspect('3x2'), (3.0, 2.0))
        self.assertEqual(comfyui_parse_aspect('3/2'), (3.0, 2.0))
        self.assertEqual(comfyui_parse_aspect(''), (0.0, 0.0))
        self.assertEqual(comfyui_parse_aspect('abc'), (0.0, 0.0))


class ComfyEditSizingTests(unittest.TestCase):
    """图生图尺寸策略必须跟官方 edit 工作流：参考图 scale total pixels 到面积档，再对齐 32。"""

    def test_reference_is_resized_to_tier_not_to_1536(self):
        from app.services.comfyui_workflows import comfyui_edit_target_size
        # 用户实际素材 3166×3173，1K 档 = 4 MP → 两边应落在 2048 附近且齐 32
        width, height = comfyui_edit_target_size(3166, 3173, '1K')
        self.assertEqual((width, height), (2048, 2048))
        self.assertAlmostEqual(width * height / (4 * 1024 * 1024), 1.0, places=1)

    def test_output_size_follows_reference_aspect(self):
        from app.services.comfyui_workflows import comfyui_edit_target_size
        width, height = comfyui_edit_target_size(4000, 2000, '2K')
        self.assertEqual(width % 32, 0)
        self.assertEqual(height % 32, 0)
        self.assertAlmostEqual(width / height, 2.0, places=1)
        self.assertAlmostEqual(width * height / (8 * 1024 * 1024), 1.0, places=1)

    def test_megapixels_tiered(self):
        from app.services.comfyui_workflows import comfyui_edit_megapixels
        self.assertEqual(comfyui_edit_megapixels('1K'), 4.0)
        self.assertEqual(comfyui_edit_megapixels('2K'), 8.0)
        self.assertEqual(comfyui_edit_megapixels('4K'), 16.0)


class ComfyWorkflowTests(unittest.TestCase):
    def test_text_to_image_graph(self):
        from app.services.comfyui_workflows import COMFYUI_H3_T2I_WORKFLOW, comfyui_h3_workflow
        graph = comfyui_h3_workflow('一只猫', 2896, 2896, seed=42)
        node4 = graph['4']['inputs']
        self.assertEqual(node4['prompt'], '一只猫')
        self.assertEqual((node4['width'], node4['height']), (2896, 2896))
        self.assertNotIn('images', node4)
        self.assertEqual(graph['7']['inputs']['seed'], 42)
        self.assertEqual(graph['7']['inputs']['steps'], 6)
        self.assertEqual(graph['7']['inputs']['cfg'], 1.0)
        self.assertEqual(graph['7']['inputs']['sampler_name'], 'euler')
        self.assertEqual(graph['7']['inputs']['scheduler'], 'simple')
        # 模板本身没被就地改坏
        self.assertEqual(COMFYUI_H3_T2I_WORKFLOW['4']['inputs']['prompt'], '')
        self.assertEqual(COMFYUI_H3_T2I_WORKFLOW['7']['inputs']['seed'], 0)

    def test_reference_images_wire_autogrow_input(self):
        from app.services.comfyui_workflows import COMFYUI_MAX_REFERENCES, comfyui_h3_workflow
        graph = comfyui_h3_workflow('改成油画', 2048, 2048, reference_names=['a.png', 'b.png'])
        self.assertEqual(graph['4']['inputs']['images'], {'image_1': ['ref1', 0], 'image_2': ['ref2', 0]})
        self.assertEqual(graph['ref1']['class_type'], 'LoadImage')
        self.assertEqual(graph['ref1']['inputs']['image'], 'a.png')
        many = comfyui_h3_workflow('x', 2048, 2048, reference_names=[f'{i}.png' for i in range(40)])
        self.assertEqual(len(many['4']['inputs']['images']), COMFYUI_MAX_REFERENCES)

    def test_blank_reference_names_ignored(self):
        from app.services.comfyui_workflows import comfyui_h3_workflow
        graph = comfyui_h3_workflow('x', 2048, 2048, reference_names=['', '  ', None])
        self.assertNotIn('images', graph['4']['inputs'])


class ComfyEndToEndTests(ComfyBase):
    def test_text_to_image_roundtrip(self):
        from app.services.generation import generate_ai_image
        fake = FakeComfy()

        image, raw = self.run_with(fake, lambda: generate_ai_image(
            '一只戴宇航员头盔的猫', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
            image_parameters={'aspect_ratio': '1:1', 'resolution': '1K'},
        ))
        # 8MP 档默认 -> 1K 档 2048²；宽高以 ComfyUI 产物元数据为准（节点会对齐到自己的步长）
        self.assertEqual(image['type'], 'url', '4K PNG 动辄 20MB+，必须洛盘回 URL 而不是 base64')
        self.assertTrue(image['value'].startswith('/'), image['value'])
        self.assertTrue(image['value'].endswith('.png'), image['value'])
        written = raw['output_path']
        self.assertTrue(os.path.isfile(written), written)
        with open(written, 'rb') as handle:
            self.assertEqual(handle.read(), TINY_PNG)
        self.assertEqual((raw['width'], raw['height']), (2720, 1536))
        self.assertEqual((raw['requested_width'], raw['requested_height']), (2048, 2048))
        # 只提交一次，绝不自动重试
        self.assertEqual(len(fake.requests_to('/prompt', 'POST')), 1)
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        self.assertEqual(body['prompt']['4']['inputs']['prompt'], '一只戴宇航员头盔的猫')
        self.assertEqual(body['prompt']['4']['inputs']['width'], 2048)
        self.assertEqual(body['prompt']['9']['class_type'], 'SaveImage')
        # 画布档位不得泄漏成 quality（本地工作流没有这个概念）
        self.assertNotIn('quality', json.dumps(body))
        view = fake.requests_to('/view')[0].url.params
        self.assertEqual(view['filename'], 'canvas_00001_.png')
        self.assertEqual(view['subfolder'], 'MiniMax-H3-Image')
        self.assertEqual(view['type'], 'output')

    def test_image_to_image_uploads_reference_first(self):
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        reference = {'url': 'data:image/png;base64,' + base64.b64encode(TINY_PNG).decode('ascii')}

        image, raw = self.run_with(fake, lambda: generate_ai_image(
            '把它改成油画', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
            image_parameters={'aspect_ratio': '16:9', 'resolution': '2K'},
        ))
        self.assertTrue(image['value'])
        self.assertEqual(image['type'], 'url')
        self.assertEqual(raw['references'], 1)
        uploads = fake.requests_to('/upload/image', 'POST')
        self.assertEqual(len(uploads), 1)
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        self.assertEqual(body['prompt']['4']['inputs']['images'], {'image_1': ['ref1', 0]})
        self.assertEqual(body['prompt']['ref1']['inputs']['image'], 'canvas_ref_1.png')
        # 选了具体比例就必须听画布的：本地节点不要求参考图与画布同比例
        # （实测方形参考图 + 16:9 画布能正常出 2720×1536，主体无拉伸）
        node4 = body['prompt']['4']['inputs']
        self.assertEqual((node4['width'], node4['height']), (3864, 2176))

    def test_auto_ratio_follows_reference_shape(self):
        """画布没选比例（auto）时才跟参考图走——对应官方 edit 模板的 GetImageSize 写法。"""
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        reference = {'url': 'data:image/png;base64,' + base64.b64encode(_build_png()).decode('ascii')}

        _, raw = self.run_with(fake, lambda: generate_ai_image(
            '把它改成油画', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
            image_parameters={'aspect_ratio': 'auto', 'resolution': '2K'},
        ))
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        node4 = body['prompt']['4']['inputs']
        self.assertEqual(node4['width'], node4['height'])
        self.assertEqual((node4['width'], node4['height']), (raw['requested_width'], raw['requested_height']))

    def test_reference_prompt_gets_picture_prefix(self):
        """没有 <Picture n> 时自动加 `Edit <Picture 1>. `：节点约定参考图要在提示词里点名。"""
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        reference = {'url': 'data:image/png;base64,' + base64.b64encode(TINY_PNG).decode('ascii')}
        _, raw = self.run_with(fake, lambda: generate_ai_image(
            '让它变成白天', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
            image_parameters={'aspect_ratio': '1:1', 'resolution': '1K'},
        ))
        self.assertTrue(raw['prompt_prefixed'])
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        self.assertEqual(body['prompt']['4']['inputs']['prompt'], 'Edit <Picture 1>. 让它变成白天')

    def test_prompt_with_picture_token_is_left_alone(self):
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        reference = {'url': 'data:image/png;base64,' + base64.b64encode(TINY_PNG).decode('ascii')}
        _, raw = self.run_with(fake, lambda: generate_ai_image(
            'Swap the sky in <Picture 1> to daylight', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
            image_parameters={'aspect_ratio': '1:1', 'resolution': '1K'},
        ))
        self.assertFalse(raw['prompt_prefixed'])
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        self.assertEqual(body['prompt']['4']['inputs']['prompt'], 'Swap the sky in <Picture 1> to daylight')

    def test_validation_error_surfaces_node_message(self):
        from fastapi import HTTPException
        from app.services.generation import generate_ai_image
        fake = FakeComfy(prompt_status=400)

        with self.assertRaises(HTTPException) as ctx:
            self.run_with(fake, lambda: generate_ai_image(
                'x', '1024x1024', 'medium', COMFYUI_MODEL,
                reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
            ))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn('width must be a multiple of 8', ctx.exception.detail)
        self.assertIn('节点 4', ctx.exception.detail)

    def test_execution_error_surfaces_exception_message(self):
        from fastapi import HTTPException
        from app.services.generation import generate_ai_image
        fake = FakeComfy(history_steps=['error'])

        with self.assertRaises(HTTPException) as ctx:
            self.run_with(fake, lambda: generate_ai_image(
                'x', '1024x1024', 'medium', COMFYUI_MODEL,
                reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
            ))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn('CUDA out of memory', ctx.exception.detail)

    def test_unreachable_comfyui_is_503_and_not_retried(self):
        from fastapi import HTTPException
        from app.services.generation import generate_ai_image

        def boom(request):
            raise httpx.ConnectError('connection refused', request=request)

        class DeadClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(boom)
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', DeadClient):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(generate_ai_image(
                    'x', '1024x1024', 'medium', COMFYUI_MODEL,
                    reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
                ))
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn('ComfyUI', ctx.exception.detail)


class ComfyReferenceResolutionTests(ComfyBase):
    """参考图来源解析：画布给的真实形态是项目媒体相对 URL。"""

    def make_ref_file(self):
        path = os.path.join(self.temp_dir.name, '6.jpg')
        with open(path, 'wb') as handle:
            handle.write(TINY_PNG)
        return path

    def test_project_media_url_resolves_in_process_without_http(self):
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        ref_path = self.make_ref_file()
        calls = []

        def fake_store_call(name, project_id, asset_id):
            calls.append((name, project_id, asset_id))
            return {'path': ref_path, 'kind': 'image', 'name': '6.jpg', 'mime_type': 'image/jpeg'}

        reference = {'url': '/api/project-library/2c92ddd4e4345a8560e389d41b580/media/e416d06420', 'kind': 'image', 'name': '6.jpg'}
        with patch.object(main, 'project_store_call', fake_store_call):
            image, raw = self.run_with_project_scope(
                '2c92ddd4e4345a8560e389d41b580',
                fake,
                lambda: generate_ai_image(
                    '把产品放进客厅', '1024x1024', 'medium', COMFYUI_MODEL,
                    reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
                    image_parameters={'aspect_ratio': '1:1', 'resolution': '1K'},
                ),
            )
        # 关键：不再回打自己的 HTTP（以前这里会拿 403 直接把任务弄挂）
        self.assertEqual(fake.requests_to('/api/project-library/2c92ddd4e4345a8560e389d41b580/media/e416d06420'), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'media_record')
        self.assertEqual(len(fake.requests_to('/upload/image', 'POST')), 1)
        body = json.loads(fake.requests_to('/prompt', 'POST')[0].content.decode('utf-8'))
        self.assertEqual(body['prompt']['4']['inputs']['images'], {'image_1': ['ref1', 0]})
        self.assertEqual(raw['references'], 1)
        self.assertEqual(image['type'], 'url')

    def test_reference_dict_path_field_wins(self):
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        ref_path = self.make_ref_file()
        reference = {'url': '/api/project-library/p/media/a', 'path': ref_path}
        image, raw = self.run_with_project_scope('p', fake, lambda: generate_ai_image(
            'x', '1024x1024', 'medium', COMFYUI_MODEL,
            reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
        ))
        self.assertEqual(raw['references'], 1)
        self.assertTrue(image['value'])

    def test_unresolvable_reference_is_400_not_httpx_error(self):
        """解析不到就给一句人话，不能把 httpx 异常抛到画布上。"""
        from fastapi import HTTPException
        from app.services.generation import generate_ai_image
        fake = FakeComfy()
        reference = {'url': '/api/project-library/p/media/missing', 'kind': 'image'}

        def missing_store_call(name, project_id, asset_id):
            raise RuntimeError('项目媒体不存在')

        with patch.object(main, 'project_store_call', missing_store_call):
            with self.assertRaises(HTTPException) as ctx:
                self.run_with_project_scope('p', fake, lambda: generate_ai_image(
                    'x', '1024x1024', 'medium', COMFYUI_MODEL,
                    reference_images=[reference], provider_id=PROVIDER_ID, preserve_model=True,
                ))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('参考图无法交给本地 ComfyUI', ctx.exception.detail)
        # 未提交任何工作流
        self.assertEqual(fake.requests_to('/prompt', 'POST'), [])


class ComfyQueueHygieneTests(ComfyBase):
    """超时/取消必须撤单：ComfyUI 是单队列，一张卡死的图会堵住用户后面所有任务。"""

    def test_timeout_cancels_queue_item(self):
        from fastapi import HTTPException
        from app.services.generation import generate_ai_image
        fake = FakeComfy(history_steps=[{}])

        with self.assertRaises(HTTPException) as ctx:
            self.run_with(
                fake,
                lambda: generate_ai_image(
                    'x', '1024x1024', 'medium', COMFYUI_MODEL,
                    reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
                ),
                COMFYUI_IMAGE_TASK_TIMEOUT=0,
            )
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertIn('已从队列撤单', ctx.exception.detail)
        deletes = [r for r in fake.requests_to('/queue', 'POST')]
        self.assertEqual(len(deletes), 1, '超时后必须把任务从 ComfyUI 队列里删掉')
        self.assertEqual(json.loads(deletes[0].content.decode('utf-8')), {'delete': [fake.prompt_id]})


if __name__ == '__main__':
    unittest.main()
