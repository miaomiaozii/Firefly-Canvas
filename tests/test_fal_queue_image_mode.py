"""fal.ai 平台兼容（image_request_mode='fal'）合同测试：comfly /fal 前缀 + Topaz 高清修复。

覆盖三层：
  1. 配置存活：provider 规整后 model_request_modes / fal_request 不被抹掉。
  2. 请求形态：POST {base}/fal/{endpoint}，body 只有 image_url + 配置参数（无 model/size/quality）。
  3. 队列轮询：status IN_QUEUE → COMPLETED，再 GET 结果；结果里的 image.url 被提取。
"""
from __future__ import annotations

import asyncio
import unittest

import app.services.generation as GENERATION
from app.core import (
    SUPPORTED_IMAGE_REQUEST_MODES,
    effective_image_request_mode,
    fal_request_spec,
    normalize_image_model_capabilities,
    normalize_model_request_modes,
)
from tests.provider_contract import ProviderContractCapture, load_provider_contract

MODEL = 'fal-ai/topaz/upscale/image'
BASE_URL = 'https://ai.comfly.org/v1'
CONTRACT_PLACEHOLDER = 'https://provider.invalid'
REFERENCE = {'url': 'https://refs.invalid/input.png'}

FAL_CAPABILITY = {
    'media_types': ['image'],
    'max_reference_images': 1,
    'arbitrary_size': True,
    'arbitrary_aspect_ratio': True,
    'arbitrary_resolution': True,
    'fal_request': {
        'endpoint': MODEL,
        'path_prefix': 'fal',
        'params': {'output_format': 'png', 'face_enhancement': True, 'crop_to_fill': False},
        'model_by_resolution': {'1k': 'Standard V2', '2k': 'High Fidelity V2', '4k': 'Wonder 3'},
    },
}


def provider():
    return {
        'id': 'comfly-fal',
        'name': 'comfly fal contract',
        'enabled': True,
        'protocol': 'openai',
        'base_url': BASE_URL,
        'image_request_mode': 'openai',
        'image_models': [MODEL],
        'model_request_modes': {MODEL: 'fal'},
        'image_model_capabilities': {MODEL: FAL_CAPABILITY},
    }


# 中转商原样回填 fal 官方域名（教程明说要把 queue.fal.run 换成中转域名）：
# 直连会绕过中转鉴权（fal 官方用 `Authorization: key`），所以代码必须换回自己域名。
SUBMIT_ACK = {
    'request_id': '019e06a4-3218',
    'status_url': f'https://queue.fal.run/fal/{MODEL}/requests/019e06a4-3218/status',
    'response_url': f'https://queue.fal.run/fal/{MODEL}/requests/019e06a4-3218',
}
SUBMIT_ACK_2K = {
    'request_id': '019e06a4-3218',
    'status_url': f'https://queue.fal.run/{MODEL}/requests/019e06a4-3218/status',
    'response_url': f'https://queue.fal.run/{MODEL}/requests/019e06a4-3218',
}
COMPLETED_STATUS = {'status': 'COMPLETED', 'queue_position': 0}
TOPAZ_RESULT = {
    'image': {'url': 'https://fal.media/files/lion/out.png', 'content_type': 'image/png', 'width': 4096, 'height': 2304},
    'timings': {'inference': 7.1},
    'unit_price': '0.025',
}


class _stubbed_service:
    """临时替换 _svc 上的取头/取图协程，退出时原样还原，避免污染同进程其它用例。"""

    def __init__(self):
        self._saved = {}

    def __enter__(self):
        for name, value in (('_main', _fake_main), ('responses_input_image_url', _fake_ref_url)):
            self._saved[name] = getattr(GENERATION._svc, name)
            setattr(GENERATION._svc, name, value)
        return self

    def __exit__(self, exc_type, exc, tb):
        for name, value in self._saved.items():
            setattr(GENERATION._svc, name, value)
        return False


class FalQueueModeTests(unittest.TestCase):
    def test_fal_mode_is_supported_and_selected_per_model(self):
        self.assertIn('fal', SUPPORTED_IMAGE_REQUEST_MODES)
        fixture = provider()
        self.assertEqual(effective_image_request_mode(fixture, MODEL), 'fal')
        # 全局形态不被单模型覆盖污染：其它模型仍走 openai。
        self.assertEqual(effective_image_request_mode(fixture, 'nano-banana-pro'), 'openai')

    def test_normalizers_keep_fal_schema_and_mode_map(self):
        capabilities = normalize_image_model_capabilities({MODEL: FAL_CAPABILITY}, [MODEL])
        self.assertEqual(capabilities[MODEL]['fal_request']['endpoint'], MODEL)
        self.assertEqual(capabilities[MODEL]['fal_request']['params']['output_format'], 'png')
        self.assertEqual(
            normalize_model_request_modes({MODEL: 'fal', 'x': 'bogus'}),
            {MODEL: 'fal'},
        )

    def test_fal_queue_paths_strip_v1_and_build_the_three_endpoints(self):
        submit = GENERATION.fal_queue_paths(BASE_URL, MODEL)
        self.assertEqual(submit, 'https://ai.comfly.org/fal/fal-ai/topaz/upscale/image')
        paths = GENERATION.fal_queue_paths(BASE_URL, MODEL, 'req-1')
        self.assertEqual(paths['status'], 'https://ai.comfly.org/fal/fal-ai/topaz/upscale/image/requests/req-1/status')
        self.assertEqual(paths['result'], 'https://ai.comfly.org/fal/fal-ai/topaz/upscale/image/requests/req-1')

    def test_submit_body_carries_only_image_url_and_configured_parameters(self):
        spec = fal_request_spec(provider(), MODEL)
        body = GENERATION.fal_submit_body(MODEL, 'https://fal.media/in.png', spec['params'], '2k', spec['model_by_resolution'])
        self.assertEqual(body['image_url'], 'https://fal.media/in.png')
        self.assertEqual(body['output_format'], 'png')
        # 档位只补服务商没配的字段：2k → High Fidelity V2 + 2×。
        self.assertEqual(body['model'], 'High Fidelity V2')
        self.assertEqual(body['upscale_factor'], 2)
        for forbidden in ('size', 'aspect_ratio', 'resolution', 'quality', 'n'):
            self.assertNotIn(forbidden, body)
        # 服务商显式配了 model/upscale_factor 时档位不得越权覆盖。
        explicit = GENERATION.fal_submit_body(MODEL, 'u', {'model': 'Recovery V2', 'upscale_factor': 4}, '1k', spec['model_by_resolution'])
        self.assertEqual((explicit['model'], explicit['upscale_factor']), ('Recovery V2', 4))

    def test_fal_branch_submits_polls_and_returns_the_upscale_payload(self):
        capture = ProviderContractCapture([SUBMIT_ACK, {'status': 'IN_QUEUE', 'queue_position': 1}, COMPLETED_STATUS, TOPAZ_RESULT])
        fixture = provider()
        spec = fal_request_spec(fixture, MODEL)

        async def exercise():
            with _stubbed_service():
                return await GENERATION.generate_fal_queue_image(
                    capture.async_client(), BASE_URL, fixture, MODEL, [dict(REFERENCE)], spec['params'],
                    prefix=spec['path_prefix'], resolution='1k', model_by_resolution=spec['model_by_resolution'],
                )

        payload = asyncio.run(exercise()).json()
        self.assertEqual(payload['image']['url'], 'https://fal.media/files/lion/out.png')
        requests = [GENERATION.normalize_request(request) if hasattr(GENERATION, 'normalize_request') else request for request in capture.requests]
        methods = [(str(request['method']).upper(), str(request['url'])) for request in requests]
        self.assertEqual(methods[0], ('POST', 'https://ai.comfly.org/fal/fal-ai/topaz/upscale/image'))
        self.assertIn('/requests/019e06a4-3218/status', methods[1][1])
        self.assertTrue(methods[-1][1].endswith(f'/fal/{MODEL}/requests/019e06a4-3218'))
        body = requests[0]['json']
        self.assertEqual(body['model'], 'Standard V2')
        self.assertEqual(body['upscale_factor'], 1)
        self.assertEqual(body['image_url'], 'https://refs.invalid/input.png')

    def test_polling_never_follows_the_echoed_queue_fal_run_urls(self):
        capture = ProviderContractCapture([SUBMIT_ACK, COMPLETED_STATUS, TOPAZ_RESULT])
        fixture = provider()
        spec = fal_request_spec(fixture, MODEL)

        async def exercise():
            with _stubbed_service():
                return await GENERATION.generate_fal_queue_image(
                    capture.async_client(), BASE_URL, fixture, MODEL, [dict(REFERENCE)], spec['params'],
                    prefix=spec['path_prefix'], resolution='1k', model_by_resolution=spec['model_by_resolution'],
                )

        asyncio.run(exercise())
        urls = [str(request['url']) for request in capture.requests]
        self.assertTrue(all(url.startswith('https://ai.comfly.org/fal/') for url in urls), urls)
        self.assertNotIn('queue.fal.run', ' '.join(urls))
        self.assertEqual(urls[1], f'https://ai.comfly.org/fal/{MODEL}/requests/019e06a4-3218/status')
        self.assertEqual(urls[2], f'https://ai.comfly.org/fal/{MODEL}/requests/019e06a4-3218')

    def test_flux_style_images_array_result_is_also_extracted(self):
        flux_result = {'status': 'COMPLETED', 'images': [{'url': 'https://v3b.fal.media/files/b/0a995c44/out.jpg', 'content_type': 'image/jpeg', 'width': 1024, 'height': 768}]}
        capture = ProviderContractCapture([SUBMIT_ACK, COMPLETED_STATUS, flux_result])

        async def exercise():
            with _stubbed_service():
                return await GENERATION.generate_fal_queue_image(capture.async_client(), BASE_URL, provider(), MODEL, [dict(REFERENCE)], {})

        payload = asyncio.run(exercise()).json()
        from app.core import extract_image_flexible
        self.assertEqual(extract_image_flexible(payload)['value'], 'https://v3b.fal.media/files/b/0a995c44/out.jpg')

    def test_missing_reference_image_fails_before_network(self):
        capture = ProviderContractCapture([SUBMIT_ACK])

        async def exercise():
            with _stubbed_service():
                await GENERATION.generate_fal_queue_image(capture.async_client(), BASE_URL, provider(), MODEL, [], {})

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(exercise())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn('参考图', raised.exception.detail)
        self.assertEqual(capture.requests, [])

    def test_errored_status_surfaces_upstream_logs(self):
        capture = ProviderContractCapture([
            SUBMIT_ACK,
            {'status': 'ERRORED', 'logs': ['InputImageValidationError: image is too small']},
        ])

        async def exercise():
            with _stubbed_service():
                await GENERATION.generate_fal_queue_image(capture.async_client(), BASE_URL, provider(), MODEL, [dict(REFERENCE)], {'output_format': 'png'})

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(exercise())
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn('image is too small', raised.exception.detail)

    def test_sync_style_result_without_request_id_is_taken_as_completed(self):
        capture = ProviderContractCapture([TOPAZ_RESULT])

        async def exercise():
            with _stubbed_service():
                return await GENERATION.generate_fal_queue_image(capture.async_client(), BASE_URL, provider(), MODEL, [dict(REFERENCE)], {})

        payload = asyncio.run(exercise()).json()
        self.assertEqual(payload['image']['url'], 'https://fal.media/files/lion/out.png')
        self.assertEqual(len(capture.requests), 1)


async def _fake_ref_url(ref, require_public_url=False):
    # fal 高清修复需要公网可拉取的输入图，回退到本地 data: 会直接 422。
    return str(ref.get('url') or '') if require_public_url else ''


def _fake_main(name):
    def api_headers(json_body=True, provider=None, model=''):
        return {'Authorization': 'Bearer fal-contract-secret'}
    return {'api_headers': api_headers}[name]


if __name__ == '__main__':
    unittest.main()


CONTRACT_BASE_URL = 'https://provider.invalid/v1'
CONTRACT_PARAMETERS = {'width': 2048, 'height': 1152, 'aspect_ratio': '16:9', 'resolution': '2k', 'media_type': 'image'}
CONTRACT_REFERENCES = [{'url': 'https://refs.invalid/a.png'}, {'url': 'https://refs.invalid/b.png'}, {'url': 'https://refs.invalid/a.png'}]


class FalFinalRequestGoldenTests(unittest.TestCase):
    """端到端：画布生成任务 → fal 队列端点的最终请求体必须等于契约 fixture 的 golden。

    契约文件 tests/fixtures/provider_contracts/supported-image-request-families.json
    的 openai-mode:fal.final_request_golden 是这条链路的唯一事实来源。
    """

    def test_canvas_task_posts_the_golden_fal_queue_request(self):
        import main as APP
        from unittest.mock import patch
        from tests.provider_contract import normalize_captured_request

        contract = load_provider_contract(
            __import__('pathlib').Path(__file__).parent / 'fixtures' / 'provider_contracts' / 'supported-image-request-families.json'
        )
        golden = next(item for item in contract['contracts'] if item['key'] == 'openai-mode:fal')['final_request_golden']
        fixture = provider()
        fixture['base_url'] = CONTRACT_BASE_URL
        fixture['image_models'] = [MODEL]
        fixture['available_models'] = {'image_models': [MODEL]}
        capture = ProviderContractCapture([SUBMIT_ACK_2K, COMPLETED_STATUS, TOPAZ_RESULT])

        with (
            patch.object(APP, 'get_api_provider_exact', return_value=fixture),
            patch.object(APP, 'api_headers', return_value={'Authorization': 'Bearer fal-contract-secret'}),
            patch.object(APP.httpx, 'AsyncClient', capture.async_client),
        ):
            image, raw = asyncio.run(APP.generate_ai_image(
                'provider family contract prompt', '2048x1152', 'high', MODEL,
                list(CONTRACT_REFERENCES), fixture['id'], preserve_model=True, image_parameters=CONTRACT_PARAMETERS,
            ))

        posted = [request for request in capture.requests if str(request['method']).upper() == 'POST']
        self.assertEqual(len(posted), 1, capture.requests)
        self.assertEqual(normalize_captured_request(posted[0]), golden)
        self.assertEqual(image['value'], 'https://fal.media/files/lion/out.png')
        self.assertEqual(raw['request_id'], '019e06a4-3218')
