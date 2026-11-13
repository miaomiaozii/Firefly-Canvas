"""#330 MiniMax H3 Image（国内站 api.minimax.cn）独立协议 'minimax' TDD。

假服务器（ProviderContractCapture 契约捕获 / httpx.MockTransport），零真实外连。
事实来源：issue #330 维护者实测（2026-11-13，国内站按量 key）：
- 文生图 POST {base}/v1/images/generations；图生图 POST {base}/v1/images/edits，
  按画布是否连接参考图自动切换端点；认证 Authorization: Bearer <key>。
- 请求体：model / prompt / n=1 / size='auto' / aspect_ratio / area / output_format /
  response_format='url'；size 为 auto 时 aspect_ratio/area 生效（WxH 与二者互斥，
  本适配器恒用 auto）；不传 disable_prompt_enhanced（不传即提示词增强恒开）；
  不传 stream（不做 SSE）。图生图参考图 1–16 张，images[].image_url 支持公网
  URL / data URI（本地项目媒体复用画布压缩辅助转 data URI）。
- 响应 data[] 长度恒为 n；只要 1 张成功 HTTP 就是 200，失败项只带 error
  （如 1027 输出图未过内容安全）→ 必须逐项解析，不能只看 HTTP 状态。
- 非幂等：服务端最长等 600 秒，请求发出后即使客户端超时/断连也会完成生成并
  计费 → 客户端 read 超时 ≥600s，严禁任何自动重试/重发，429 只做报错提示。

契约金标准（final_request_golden / reference_edit_golden）钉在
tests/fixtures/provider_contracts/supported-image-request-families.json 的
protocol:minimax 条目里，由 test_provider_contract_registry 保证每条契约都有
可执行测试。
"""
import asyncio
import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from PIL import Image

import main
from app.core import SUPPORTED_PROVIDER_PROTOCOLS, default_api_providers, model_spec_for, normalize_provider
from app.services import generation
from firefly_generation import ImageGenerationRequest, ModelRoutingError, route_image_generation
from tests.provider_contract import ProviderContractCapture, load_provider_contract, normalize_captured_request

PROVIDER_ID = 'minimax'
H3_MODEL = 'MiniMax-H3-Image'
H3_FAST_MODEL = 'MiniMax-H3-Image-Highspeed'
BASE = 'https://api.minimax.cn'
FAKE_KEY = 'sk-minimax-test'
# 契约金标准用共享常量（与 test_provider_contract_families 的 PARAMETERS 同形），跨协议可对照。
CONTRACT_PROMPT = 'provider family contract prompt'
CONTRACT_SIZE = '2048x1152'
CONTRACT_AUTHORIZATION = 'Bearer minimax-h3-contract-secret'
CONTRACT_PARAMETERS = {'width': 2048, 'height': 1152, 'aspect_ratio': '16:9', 'resolution': '2k', 'media_type': 'image'}
CONTRACT_FIXTURE = Path(__file__).parent / 'fixtures' / 'provider_contracts' / 'supported-image-request-families.json'
MINIMAX_CONTRACT = next(
    (c for c in load_provider_contract(CONTRACT_FIXTURE)['contracts'] if c['key'] == 'protocol:minimax'),
    None,
)


def minimax_capabilities():
    # 与内置预设（app/core.py minimax_default_provider）保持一致的 H3 能力位
    return {
        model: {
            'media_types': ['image'],
            'sizes': ['*'],
            'aspect_ratios': ['1:1', '16:9', '9:16', '4:3', '3:4', '4:5', '5:4', '3:2', '2:3', '21:9', '9:21'],
            'resolutions': ['1k', '2k', '4k'],
            'max_reference_images': 16,
        }
        for model in (H3_MODEL, H3_FAST_MODEL)
    }


def minimax_record(**overrides):
    provider = {
        'id': PROVIDER_ID, 'name': 'MiniMax（海螺）', 'base_url': BASE, 'protocol': 'minimax',
        'image_request_mode': 'openai', 'enabled': True, 'primary': False,
        'image_generation_endpoint': '', 'image_edit_endpoint': '',
        'image_models': [H3_MODEL, H3_FAST_MODEL],
        'image_model_capabilities': minimax_capabilities(),
        'chat_models': [], 'video_models': [],
    }
    provider.update(overrides)
    return provider


def ok_response(url='https://cdn.minimaxi.com/generated/contract.png'):
    return {
        'created': 1730000000,
        'data': [{'url': url, 'size': '2048x1152', 'revised_prompt': f'{CONTRACT_PROMPT}（增强）'}],
        'output_format': 'png',
        'usage': {'generated_images': 1},
        'request_id': 'contract-request',
    }


class _FakeAsyncClient(httpx.AsyncClient):
    """MockTransport 假客户端：逐次捕获出站请求，零真实外连。"""
    handler = None

    def __init__(self, *args, **kwargs):
        kwargs.pop('transport', None)
        kwargs['transport'] = httpx.MockTransport(type(self).handler)
        super().__init__(*args, **kwargs)


class MinimaxFakeClient:
    """把被测代码里的 httpx.AsyncClient 换成 _FakeAsyncClient（假服务器）。"""

    def __init__(self, respond):
        self.captured = []

        def handle(request: httpx.Request) -> httpx.Response:
            self.captured.append(request)
            return respond(request)

        self._handle = handle

    def __enter__(self):
        _FakeAsyncClient.handler = self._handle
        patcher = patch('httpx.AsyncClient', _FakeAsyncClient)
        patcher.start()
        self._patcher = patcher
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        _FakeAsyncClient.handler = None


class MinimaxBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        with open(providers_file, 'w', encoding='utf-8') as f:
            json.dump([minimax_record()], f, ensure_ascii=False)
        self._patches = [
            patch.object(main, 'API_PROVIDERS_FILE', providers_file),
            patch.object(main, 'provider_env_key_value', return_value=FAKE_KEY),
            patch.object(main, 'OUTPUT_DIR', os.path.join(self.temp_dir.name, 'output')),
        ]
        for p in self._patches:
            p.start()
        os.makedirs(os.path.join(self.temp_dir.name, 'output'), exist_ok=True)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.temp_dir.cleanup()

    def write_local_reference(self, filename='ref.png', color='red', size=(64, 64)):
        path = os.path.join(self.temp_dir.name, 'output', filename)
        Image.new('RGB', size, color).save(path, 'PNG')
        return {'url': f'/output/{filename}'}

    def generate(self, respond, references=None, parameters=CONTRACT_PARAMETERS, model=H3_MODEL, size=CONTRACT_SIZE, prompt=CONTRACT_PROMPT):
        """经完整入口 generate_ai_image 发一次真实（假服务器）请求。"""
        with MinimaxFakeClient(respond) as fake:
            result = asyncio.run(generation.generate_ai_image(
                prompt, size, 'auto', model,
                reference_images=references, provider_id=PROVIDER_ID,
                preserve_model=True, image_parameters=parameters,
            ))
        return (result, fake.captured)

    def generate_expect_error(self, respond, exception_type, references=None, parameters=CONTRACT_PARAMETERS):
        """错误路径：断言异常类型，返回 (异常, 出站请求次数)。"""
        with MinimaxFakeClient(respond) as fake:
            with self.assertRaises(exception_type) as ctx:
                asyncio.run(generation.generate_ai_image(
                    CONTRACT_PROMPT, CONTRACT_SIZE, 'auto', H3_MODEL,
                    reference_images=references, provider_id=PROVIDER_ID,
                    preserve_model=True, image_parameters=parameters,
                ))
        return (ctx.exception, len(fake.captured))


class MinimaxRegistrationTests(MinimaxBase):
    def test_protocol_supported(self):
        self.assertIn('minimax', SUPPORTED_PROVIDER_PROTOCOLS)

    def test_contract_fixture_present(self):
        self.assertIsNotNone(MINIMAX_CONTRACT, 'protocol:minimax 契约必须登记进 supported-image-request-families.json')
        self.assertIn('protocol:minimax', load_provider_contract(CONTRACT_FIXTURE)['family_keys'])

    def test_builtin_preset_registered(self):
        presets = {p['id']: p for p in default_api_providers()}
        self.assertIn('minimax', presets)
        preset = presets['minimax']
        self.assertEqual(preset['protocol'], 'minimax')
        self.assertEqual(preset['base_url'], BASE)
        self.assertEqual(preset['image_models'], [H3_MODEL, H3_FAST_MODEL])
        self.assertEqual(preset['image_generation_endpoint'], '')
        self.assertEqual(preset['image_edit_endpoint'], '')
        # 能力位与 H3 契约一致，且与测试夹具同源
        self.assertEqual(preset['image_model_capabilities'], minimax_capabilities())
        caps = preset['image_model_capabilities'][H3_MODEL]
        self.assertEqual(caps['resolutions'], ['1k', '2k', '4k'])
        self.assertIn('16:9', caps['aspect_ratios'])
        self.assertEqual(caps['max_reference_images'], 16)

    def test_preset_injected_for_fresh_install(self):
        merged = main.merge_default_api_providers([])
        minimax = next(p for p in merged if p['id'] == 'minimax')
        self.assertEqual(minimax['base_url'], BASE)
        self.assertEqual(minimax['image_models'], [H3_MODEL, H3_FAST_MODEL])

    def test_model_specs_registered(self):
        for model in (H3_MODEL, H3_FAST_MODEL):
            spec = model_spec_for(model)
            self.assertTrue(spec, f'{model} 必须登记进全局 model_specs.json')
            self.assertEqual(spec.get('api_type'), 'minimax_images')
            self.assertTrue(spec.get('supports_vision'), '图生图能力位必须标记支持参考图')
            params = spec.get('parameters') or {}
            self.assertEqual(params['resolution']['options'], ['1K', '2K', '4K'])
            self.assertIn('16:9', params['aspect_ratio']['options'])
            self.assertEqual(params['output_format']['options'], ['png', 'jpeg', 'webp'])

    def test_routing_preflight_passes_canvas_request(self):
        provider = normalize_provider(minimax_record())
        request = ImageGenerationRequest(prompt=CONTRACT_PROMPT, size=CONTRACT_SIZE, width=2048, height=1152,
                                         aspect_ratio='16:9', resolution='2k', reference_asset_ids=(),
                                         media_type='image', quality='auto', count=1)
        route = route_image_generation(request, [provider], provider_id=PROVIDER_ID, model=H3_MODEL)
        self.assertEqual(route.protocol, 'minimax')
        self.assertEqual(route.model, H3_MODEL)
        self.assertEqual(route.request, request.to_dict())

    def test_routing_preflight_rejects_out_of_range_ratio(self):
        provider = normalize_provider(minimax_record())
        request = ImageGenerationRequest(prompt=CONTRACT_PROMPT, size='1024x1024', width=1024, height=1024,
                                         aspect_ratio='1:4', resolution='1k', reference_asset_ids=(),
                                         media_type='image', quality='auto', count=1)
        try:
            route_image_generation(request, [provider], provider_id=PROVIDER_ID, model=H3_MODEL)
        except ModelRoutingError as exc:
            self.assertIn('aspect_ratio', list(exc.incompatible_parameters))
        else:
            self.fail('1:4 超出 H3 的 1:3–3:1 上限，预检必须拒绝')

    def test_routing_preflight_rejects_too_many_references(self):
        provider = normalize_provider(minimax_record())
        request = ImageGenerationRequest(prompt=CONTRACT_PROMPT, size='1024x1024', width=1024, height=1024,
                                         aspect_ratio='1:1', resolution='1k',
                                         reference_asset_ids=tuple(f'asset-{i}' for i in range(17)),
                                         media_type='image', quality='auto', count=1)
        try:
            route_image_generation(request, [provider], provider_id=PROVIDER_ID, model=H3_MODEL)
        except ModelRoutingError as exc:
            self.assertIn('reference_count', list(exc.incompatible_parameters))
        else:
            self.fail('H3 参考图上限 16 张，第 17 张必须被预检拒绝')


class MinimaxRequestShapeTests(MinimaxBase):
    """契约金标准执行测试（protocol:minimax 的 execution_tests 指向这里）。"""

    def _capture_final_request(self, references=None, parameters=CONTRACT_PARAMETERS, response_payload=None):
        capture = ProviderContractCapture(response_payload or ok_response())
        edit_tokens = iter(['data:image/png;base64,REF-A', 'data:image/png;base64,REF-B', 'data:image/png;base64,REF-A'])
        with (
            patch.object(main, 'get_api_provider_exact', return_value=normalize_provider(minimax_record())),
            patch.object(main, 'api_headers', return_value={'Authorization': CONTRACT_AUTHORIZATION}),
            patch.object(main, 'reference_to_data_url', side_effect=lambda ref, **kwargs: next(edit_tokens)),
            patch.object(main.httpx, 'AsyncClient', capture.async_client),
        ):
            result = asyncio.run(generation.generate_ai_image(
                CONTRACT_PROMPT, CONTRACT_SIZE, 'auto', H3_MODEL, references, PROVIDER_ID,
                preserve_model=True, image_parameters=parameters,
            ))
        return (result, capture)

    def test_text_to_image_reaches_the_final_request(self):
        (image_data, raw), capture = self._capture_final_request()
        self.assertEqual(normalize_captured_request(capture.only_request()), MINIMAX_CONTRACT['final_request_golden'])
        body = capture.only_request()['json']
        # 互斥/恒开字段：不传 disable_prompt_enhanced（不传即增强恒开）、不传 stream、不传 quality
        self.assertNotIn('disable_prompt_enhanced', body)
        self.assertNotIn('stream', body)
        self.assertNotIn('quality', body)
        self.assertNotIn('images', body)
        self.assertEqual(image_data, {'type': 'url', 'value': 'https://cdn.minimaxi.com/generated/contract.png'})
        self.assertEqual(raw['request_id'], 'contract-request')

    def test_edit_request_uses_ordered_data_uri_references(self):
        references = [{'url': '/output/a.png'}, {'url': '/output/b.png'}, {'url': '/output/a.png'}]
        (image_data, _raw), capture = self._capture_final_request(
            references=references, response_payload=ok_response('https://cdn.minimaxi.com/edited.png'))
        self.assertEqual(normalize_captured_request(capture.only_request()), MINIMAX_CONTRACT['reference_edit_golden'])
        body = capture.only_request()['json']
        self.assertNotIn('disable_prompt_enhanced', body)
        self.assertNotIn('stream', body)
        # 参考图按调用顺序进入 images[].image_url，且为 data URI
        self.assertTrue(all(item['image_url'].startswith('data:image/') for item in body['images']))
        self.assertEqual(image_data['value'], 'https://cdn.minimaxi.com/edited.png')

    def test_text_to_image_defaults_when_parameters_missing(self):
        (_image_data, _raw), captured = self.generate(
            lambda request: httpx.Response(200, json=ok_response()), parameters={})
        body = json.loads(captured[0].content.decode('utf-8'))
        self.assertEqual(body['size'], 'auto')
        self.assertEqual(body['area'], '4K')  # 缺省档位 4K
        self.assertEqual(body['output_format'], 'png')  # 缺省格式 png
        self.assertNotIn('aspect_ratio', body)
        self.assertNotIn('disable_prompt_enhanced', body)
        self.assertNotIn('stream', body)

    def test_out_of_range_aspect_ratio_fails_fast_without_request(self):
        # 1:4 超出 H3 的 1:3–3:1：发请求前就报 400（非幂等接口，不浪费一次计费）
        with self.assertRaises(HTTPException) as ctx:
            self.generate(
                lambda request: httpx.Response(200, json=ok_response()),
                parameters={'aspect_ratio': '1:4', 'resolution': '1k'},
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('1:4', str(ctx.exception.detail))

    def test_local_reference_converted_to_real_data_uri(self):
        # 不打桩：真实走 reference_to_data_url（画布现有压缩辅助）把本地媒体转 data URI
        ref = self.write_local_reference('real.png', color='blue')
        (_image_data, _raw), captured = self.generate(
            lambda request: httpx.Response(200, json=ok_response()), references=[ref])
        body = json.loads(captured[0].content.decode('utf-8'))
        image_url = body['images'][0]['image_url']
        self.assertTrue(image_url.startswith('data:image/'), f'参考图必须是 data URI，实际：{image_url[:40]}')
        mime, payload = image_url.split(',', 1)
        decoded = base64.b64decode(payload)
        self.assertTrue(decoded, 'data URI 的 base64 载荷不能为空')
        with Image.open(io.BytesIO(decoded)) as probe:
            self.assertEqual(probe.size, (64, 64))
        self.assertIn(mime, {'data:image/png;base64', 'data:image/jpeg;base64'})


class MinimaxEndpointOverrideTests(MinimaxBase):
    def test_overrides_honored_for_local_deployment(self):
        # 兼容口：本地部署可覆盖端点（provider 的 image_generation_endpoint / image_edit_endpoint 字段），
        # 相对路径拼 base 的 scheme+netloc，绝对路径原样使用——不硬编码路径。
        record = minimax_record(image_generation_endpoint='/local-deploy/generate',
                                image_edit_endpoint='http://127.0.0.1:8901/local-edit')
        path = os.path.join(self.temp_dir.name, 'override-providers.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([record], f, ensure_ascii=False)
        with patch.object(main, 'API_PROVIDERS_FILE', path):
            (_image_data, _raw), captured = self.generate(
                lambda request: httpx.Response(200, json=ok_response()))
            self.assertEqual(str(captured[0].url), 'https://api.minimax.cn/local-deploy/generate')
            ref = self.write_local_reference('ref.png')
            (_image_data, _raw), captured = self.generate(
                lambda request: httpx.Response(200, json=ok_response()), references=[ref])
            self.assertEqual(str(captured[0].url), 'http://127.0.0.1:8901/local-edit')


class MinimaxResponseParsingTests(MinimaxBase):
    def test_mixed_success_and_failure_items(self):
        # data[] 混合成功/失败：成功项照常返回，失败项（1027 内容安全）汇总进错误信息
        raw = {
            'created': 1,
            'data': [
                {'url': 'https://cdn.minimaxi.com/ok.png', 'size': '1024x1024', 'revised_prompt': 'x'},
                {'error': {'code': 1027, 'message': 'content safety'}},
            ],
            'output_format': 'png',
        }
        image_data, errors = generation._minimax_collect_result('MiniMax（海螺）', raw)
        self.assertEqual(image_data, {'type': 'url', 'value': 'https://cdn.minimaxi.com/ok.png'})
        self.assertEqual(len(errors), 1)
        self.assertIn('1027', errors[0])
        self.assertIn('内容安全', errors[0])
        self.assertIn('content safety', errors[0])

    def test_b64_success_item_maps_mime_from_output_format(self):
        raw = {'data': [{'b64_json': 'aGVsbG8=', 'size': '1024x1024'}], 'output_format': 'jpeg'}
        image_data, errors = generation._minimax_collect_result('MiniMax', raw)
        self.assertEqual(image_data, {'type': 'b64', 'value': 'aGVsbG8=', 'mime_type': 'image/jpeg'})
        self.assertEqual(errors, [])

    def test_all_items_failed_raises_aggregated_error(self):
        # HTTP 200 但 data[0] 只带 error（如 1027）：不能当成功，逐项错误汇总进报错
        def respond(request):
            return httpx.Response(200, json={
                'created': 1,
                'data': [{'error': {'code': 1027, 'message': 'output image not pass content safety'}}],
                'output_format': 'png',
            })
        exc, calls = self.generate_expect_error(respond, HTTPException)
        self.assertEqual(calls, 1)  # 全失败也不得重发
        self.assertEqual(exc.status_code, 502)
        detail = str(exc.detail)
        self.assertIn('1027', detail)
        self.assertIn('内容安全', detail)

    def test_no_data_items_raises(self):
        exc, calls = self.generate_expect_error(lambda request: httpx.Response(200, json={'created': 1, 'data': []}), HTTPException)
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 502)


class MinimaxErrorMappingTests(MinimaxBase):
    def _generate_with(self, status, payload):
        return self.generate_expect_error(lambda request: httpx.Response(status, json=payload), HTTPException)

    def test_api_key_invalid_401_2049(self):
        exc, calls = self._generate_with(401, {'code': 2049, 'message': 'invalid api key'})
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 401)
        self.assertIn('API Key 无效', str(exc.detail))
        self.assertIn('2049', str(exc.detail))

    def test_insufficient_balance_402_1008(self):
        exc, calls = self._generate_with(402, {'code': 1008, 'message': 'insufficient balance'})
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 402)
        self.assertIn('余额不足', str(exc.detail))

    def test_tier_missing_model_402_2068(self):
        exc, calls = self._generate_with(402, {'code': 2068, 'message': 'model not in plan'})
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 402)
        self.assertIn('订阅档位不含该模型', str(exc.detail))

    def test_mplan_usage_cap_429_2056(self):
        exc, calls = self._generate_with(429, {'code': 2056, 'message': 'usage limit'})
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 429)
        self.assertIn('M Plan', str(exc.detail))
        self.assertIn('用量上限', str(exc.detail))

    def test_base_resp_shape_supported(self):
        # 兼容 base_resp 形态（MiniMax 系接口常见错误结构）
        exc, calls = self._generate_with(402, {'base_resp': {'status_code': 1008, 'status_msg': 'insufficient balance'}})
        self.assertEqual(calls, 1)
        self.assertIn('余额不足', str(exc.detail))

    def test_unknown_422_keeps_generic_validation_text(self):
        exc, calls = self._generate_with(422, {'code': 0, 'message': 'invalid params: aspect_ratio'})
        self.assertEqual(calls, 1)
        self.assertEqual(exc.status_code, 422)
        self.assertIn('参数校验失败', str(exc.detail))


class MinimaxNoRetryTests(MinimaxBase):
    def test_429_not_retried_by_backoff_helper(self):
        # 429 经 run_provider_generation_with_backoff（连发任务的共享出口）也只发一次：
        # 非幂等协议显式排除在 429 退避重试之外。
        with MinimaxFakeClient(lambda request: httpx.Response(429, json={'code': 2056, 'message': 'usage limit'})) as fake:
            with patch.object(generation.asyncio, 'sleep', new=AsyncMock()):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(generation.run_provider_generation_with_backoff(
                        PROVIDER_ID, generation.generate_ai_image,
                        CONTRACT_PROMPT, CONTRACT_SIZE, 'auto', H3_MODEL, None, PROVIDER_ID,
                        preserve_model=True, image_parameters=CONTRACT_PARAMETERS,
                    ))
        self.assertEqual(len(fake.captured), 1, '429 必须只发一次请求，严禁退避重发')
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn('M Plan', str(ctx.exception.detail))

    def test_connection_error_not_retried(self):
        # 模拟连接错误：整个链路只发一次请求（重试 = 再扣一次费）。
        def respond(request):
            raise httpx.ConnectError('connection refused')

        _exc, calls = self.generate_expect_error(respond, httpx.ConnectError)
        self.assertEqual(calls, 1)

    def test_read_timeout_not_below_server_ceiling(self):
        # 服务端最长等 600 秒：客户端 read 超时下限 600s
        self.assertGreaterEqual(generation.MINIMAX_IMAGE_READ_TIMEOUT, 600.0)


if __name__ == '__main__':
    unittest.main()
