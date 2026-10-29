"""fal.ai 平台兼容（image_request_mode='fal'）合同测试：comfly /fal 前缀 + Topaz 高清修复。

覆盖三层：
  1. 配置存活：provider 规整后 model_request_modes / fal_request 不被抹掉。
  2. 请求形态：POST {base}/fal/{endpoint}，body 只有 image_url + 配置参数（无 model/size/quality）。
  3. 队列轮询：status IN_QUEUE → COMPLETED，再 GET 结果；结果里的 image.url 被提取。
"""
from __future__ import annotations

import asyncio
import unittest
import unittest.mock

import app.services.generation as GENERATION
import app.services.media as MEDIA
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
        for name, value in (('_main', _fake_main), ('fal_reference_public_url', _fake_ref_url)):
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

    def test_reference_without_usable_image_fails_before_network(self):
        capture = ProviderContractCapture([SUBMIT_ACK])

        async def exercise():
            with _stubbed_service():
                await GENERATION.generate_fal_queue_image(capture.async_client(), BASE_URL, provider(), MODEL, [{'url': ''}], {})

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(exercise())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn('参考图', raised.exception.detail)
        self.assertEqual(capture.requests, [])


class FalReferencePublicUrlTests(unittest.TestCase):
    """站内参考图（/api/project-library/…）必须公网化后才能交给 fal：上游拉不到本机地址。"""

    CANVAS_LOCAL = '/api/project-library/28f95417ff91/media/2902c86b22f6.png'
    LOOPBACK = f'http://127.0.0.1:3000{CANVAS_LOCAL}'

    def _run(self, ref, *, upload=None, data_url='', public_base='', fn='fal_reference_public_url'):
        saved = {
            'upload_local_video_to_cloud': MEDIA._svc.upload_local_video_to_cloud,
            '_main': MEDIA._svc._main,
            'local_asset_public_url': MEDIA.local_asset_public_url,
        }

        async def _upload(path):
            if upload is None:
                raise AssertionError('公网 URL 不应触发上传')
            return await upload(path)

        def _main(name):
            if name == 'reference_to_data_url':
                def make(ref, max_size=None, max_bytes=None):
                    return data_url
                return make
            if name == 'output_file_from_url':
                return lambda value: 'C:/fake/in.png'
            raise KeyError(name)

        def _public(value):
            return f'{public_base}{value}' if public_base else ''

        try:
            MEDIA._svc.upload_local_video_to_cloud = _upload
            MEDIA._svc._main = _main
            MEDIA.local_asset_public_url = _public
            return asyncio.run(getattr(MEDIA, fn)(ref))
        finally:
            MEDIA._svc.upload_local_video_to_cloud = saved['upload_local_video_to_cloud']
            MEDIA._svc._main = saved['_main']
            MEDIA.local_asset_public_url = saved['local_asset_public_url']

    def test_genuine_public_url_passes_through_without_upload(self):
        self.assertEqual(self._run({'url': 'https://cdn.invalid/a.png'}), 'https://cdn.invalid/a.png')

    def test_canvas_local_path_is_uploaded_to_a_public_host(self):
        async def upload(path):
            self.assertEqual(path, self.CANVAS_LOCAL)
            return {'url': 'https://litterbox.invalid/keep.png'}

        # 高清修复的输入必须保留原分辨率：只能走图床，不能先被 base64 缩放。
        self.assertEqual(self._run({'url': self.CANVAS_LOCAL}, upload=upload), 'https://litterbox.invalid/keep.png')

    def test_loopback_url_is_treated_as_local_and_uploaded(self):
        async def upload(path):
            self.assertEqual(path, self.CANVAS_LOCAL)
            return {'url': 'https://tmp.invalid/x.png'}

        self.assertEqual(self._run({'url': self.LOOPBACK}, upload=upload), 'https://tmp.invalid/x.png')

    def test_upload_failure_falls_back_to_configured_public_base(self):
        async def failing_upload(path):
            from fastapi import HTTPException
            raise HTTPException(status_code=502, detail='云端上传失败')

        self.assertEqual(
            self._run({'url': self.CANVAS_LOCAL}, upload=failing_upload, public_base='https://tunnel.invalid'),
            f'https://tunnel.invalid{self.CANVAS_LOCAL}',
        )

    def test_upload_failure_without_public_base_raises_actionable_error(self):
        async def failing_upload(path):
            from fastapi import HTTPException
            raise HTTPException(status_code=502, detail='云端上传失败')

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            self._run({'url': self.CANVAS_LOCAL}, upload=failing_upload)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn('PUBLIC_MEDIA_BASE_URL', raised.exception.detail)
        # fal 官方 schema 只接受 URL（image_url: type string），不能拿 base64 兜底
        self.assertIn('只接受 URL', raised.exception.detail)

    def test_unrelated_remote_path_is_rejected_not_uploaded(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            self._run({'url': '/somewhere/else.png'})
        self.assertEqual(raised.exception.status_code, 400)

    def test_video_proxy_mode_accepts_the_same_project_library_reference(self):
        """用户真实报错现场：openai-video-proxy 的参考图公网化只认 /output/ 与 /assets/，
        项目库路径会被当成“不是公网 URL”拦下。两个通道必须认同一套站内形态。"""
        async def upload(path):
            self.assertEqual(path, self.CANVAS_LOCAL)
            return {'url': 'https://catbox.invalid/keep.png'}

        self.assertEqual(
            self._run({'url': self.CANVAS_LOCAL}, upload=upload, fn='openai_video_proxy_public_reference_url'),
            'https://catbox.invalid/keep.png',
        )

    def test_video_proxy_mode_without_upload_channel_gives_actionable_error(self):
        async def failing_upload(path):
            from fastapi import HTTPException
            raise HTTPException(status_code=502, detail='云端上传失败')

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            self._run({'url': self.CANVAS_LOCAL}, upload=failing_upload, fn='openai_video_proxy_public_reference_url')
        self.assertIn('图床', raised.exception.detail)


class FalModelSpecRegistrationTests(unittest.TestCase):
    """#267：fal-all（topaz/upscale/image 系列）登记进 model_specs.json —— 注册表缺口。

    改前 model_specs.json 里 "fal" 全部是 "false"/"default" 的子串，topaz 条目为零；
    前端 apiImageAllowedAspectRatios / specModelIds（smart-canvas.js）认不出这个模型家族，
    服务商能力表缺失时比例预校验与模型列表合并都拿不到依据。
    """

    TOPAZ_IMAGE = 'fal-ai/topaz/upscale/image'
    TOPAZ_GENERATIVE = 'fal-ai/topaz/upscale/image/generative'

    def test_both_topaz_models_are_registered(self):
        from app.services.model_specs import get_model_spec
        for model in (self.TOPAZ_IMAGE, self.TOPAZ_GENERATIVE):
            spec = get_model_spec(model)
            self.assertIsNotNone(spec, f'{model} 未登记进 model_specs.json')
            self.assertEqual(spec['api_type'], 'images_generations')
            self.assertTrue(spec['supports_vision'], f'{model} 是 image-to-image，必须声明参考图能力')
            self.assertTrue(spec['async_mode'], f'{model} 是队列任务（提交→轮询→拉取），必须声明异步')

    def test_spec_aspect_ratios_match_the_capability_single_source(self):
        from app.services.model_specs import get_aspect_ratios
        from tools.configure_comfly_fal_topaz import COMFLY_ASPECT_RATIOS
        # tools/configure_comfly_fal_topaz.py 的 COMFLY_ASPECT_RATIOS 是能力单一事实源，spec 必须同集。
        for model in (self.TOPAZ_IMAGE, self.TOPAZ_GENERATIVE):
            self.assertEqual(sorted(get_aspect_ratios(model)), sorted(COMFLY_ASPECT_RATIOS), model)

    def test_spec_does_not_declare_parameters_the_fal_schema_rejects(self):
        """fal 官方 schema 严格：多余字段直接 422（见 fal_submit_body 的 forbidden 断言），
        spec 里若声明 response_format/size/quality 会诱导前端把必拒参数送进请求。"""
        from app.services.model_specs import get_model_spec
        for model in (self.TOPAZ_IMAGE, self.TOPAZ_GENERATIVE):
            params = (get_model_spec(model) or {}).get('parameters') or {}
            for forbidden in ('response_format', 'size', 'quality', 'n', 'image_size'):
                self.assertNotIn(forbidden, params, f'{model} 的 spec 声明了 fal 会 422 的参数 {forbidden}')


class FalQueueUnknownStatusTests(unittest.TestCase):
    """#267：fal 队列未知状态终态化 —— 复用 #235 IMAGE_TASK_UNKNOWN_STATUSES 终态语义，不许另发明。

    改前：status 持续返回 NOT_FOUND/UNKNOWN（任务 ID 在服务商侧不存在/被清理）→ 空转轮询到 25 分钟
    deadline → 502「未在 N 分钟内完成」，用户以为在排队。现在：连续 2 次未知 → 502 终态，
    文案显式带 request_id（可拿去服务商后台查询），单次抖动容忍（出现可识别状态即重置计数）。
    """

    def _run(self, status_payloads):
        """跑一遍提交→轮询；status_payloads 是 status 端点依次返回的 body。"""
        from fastapi import HTTPException
        from tests.provider_contract import ProviderContractCapture
        # 提交 ack + 状态序列；COMPLETED 之后还要一次结果 GET。
        tail = []
        if status_payloads and str(status_payloads[-1].get('status') or '').upper() in {'COMPLETED', 'SUCCEEDED', 'SUCCESS'}:
            tail = [TOPAZ_RESULT]
        capture = ProviderContractCapture([SUBMIT_ACK] + list(status_payloads) + tail)

        async def exercise():
            with _stubbed_service(), unittest.mock.patch.object(GENERATION, 'RESPONSES_POLL_INTERVAL', 0):
                return await GENERATION.generate_fal_queue_image(
                    capture.async_client(), BASE_URL, provider(), MODEL, [dict(REFERENCE)], {'output_format': 'png'})

        try:
            payload = asyncio.run(exercise()).json()
            return payload, capture, None
        except HTTPException as exc:
            return None, capture, exc

    def test_consecutive_unknown_terminalizes_with_request_id(self):
        payload, capture, exc = self._run([{'status': 'NOT_FOUND'}, {'status': 'NOT_FOUND'}])
        self.assertIsNotNone(exc, '连续未知状态必须终态 fail，不能继续空转')
        self.assertIsNone(payload)
        self.assertEqual(exc.status_code, 502)
        self.assertIn('019e06a4-3218', str(exc.detail))          # 带 request_id，可去服务商后台查
        self.assertIn('NOT_FOUND', str(exc.detail))
        self.assertIn('状态未知', str(exc.detail))
        # 恰好 2 次状态查询（提交 1 + 状态 2），没有多烧请求。
        status_gets = [r for r in capture.requests if str(r['method']).upper() == 'GET' and '/status' in str(r['url'])]
        self.assertEqual(len(status_gets), 2)

    def test_single_unknown_blip_then_recognized_status_recovers(self):
        payload, capture, exc = self._run([{'status': 'NOT_FOUND'}, {'status': 'IN_QUEUE'}, {'status': 'COMPLETED'}])
        self.assertIsNone(exc, '单次未知抖动必须容忍（#235 语义：出现可识别状态即重置计数）')
        self.assertEqual(payload['image']['url'], 'https://fal.media/files/lion/out.png')

    def test_empty_status_is_not_unknown_and_keeps_polling(self):
        # 空状态与 #235 一致：不属于 IMAGE_TASK_UNKNOWN_STATUSES，不计数、不终态，继续轮询。
        payload, capture, exc = self._run([{'status': ''}, {'status': 'IN_PROGRESS'}, {'status': 'COMPLETED'}])
        self.assertIsNone(exc)
        self.assertEqual(payload['image']['url'], 'https://fal.media/files/lion/out.png')

    def test_unknown_terminal_uses_the_shared_235_constants(self):
        """不许另发明：终态语义必须挂在 #235 的共享常量上。"""
        from app.core import IMAGE_TASK_UNKNOWN_STATUSES, IMAGE_TASK_UNKNOWN_TERMINAL_AFTER
        self.assertIn('NOT_FOUND', IMAGE_TASK_UNKNOWN_STATUSES)
        self.assertIn('UNKNOWN', IMAGE_TASK_UNKNOWN_STATUSES)
        self.assertEqual(IMAGE_TASK_UNKNOWN_TERMINAL_AFTER, 2)
        # 终态判定用的就是这套常量（同一对象，不是抄了一份字面量）。
        self.assertIs(GENERATION.IMAGE_TASK_UNKNOWN_STATUSES, IMAGE_TASK_UNKNOWN_STATUSES)
        self.assertIs(GENERATION.IMAGE_TASK_UNKNOWN_TERMINAL_AFTER, IMAGE_TASK_UNKNOWN_TERMINAL_AFTER)


class FalErrorDetailTests(unittest.TestCase):
    """中转把 fal 原始错误套在 message 的字符串 JSON 里，错误码必须挖出来才能给人可行动的提示。"""

    class _Response:
        def __init__(self, status_code, text):
            self.status_code = status_code
            self.text = text

    LIVE_503 = ('{"code":"fail_submit_task","message":"{\\"error\\":{\\"message\\":\\"No fal API key is '
                'available in the pool.\\",\\"type\\":\\"api_error\\",\\"param\\":null,\\"code\\":\\"no_upstream_key\\"}}","data":null}')

    def test_nested_upstream_error_code_is_unwrapped_and_explained(self):
        detail = GENERATION.fal_error_detail('comfly', '任务提交', self._Response(503, self.LIVE_503))
        self.assertIn('HTTP 503', detail)
        self.assertIn('no_upstream_key', detail)          # 内层真错误码，不是外层的 fail_submit_task
        self.assertIn('No fal API key is available in the pool.', detail)
        self.assertIn('渠道侧问题', detail)                # 告诉我们不是画布的锅
        self.assertNotIn('fail_submit_task', detail)      # 外层噪声码不应遮住内层

    def test_unparseable_body_keeps_raw_tail_and_generic_hint(self):
        detail = GENERATION.fal_error_detail('comfly', '结果拉取', self._Response(502, '<html>gateway boom</html>'))
        self.assertIn('gateway boom', detail)
        self.assertIn('上游服务不可用', detail)

    def test_schema_rejection_hint_points_at_the_config_knob(self):
        body = '{"error":{"message":"Value error, unexpected key: size","code":"validation_error"}}'
        detail = GENERATION.fal_error_detail('comfly', '任务提交', self._Response(422, body))
        self.assertIn('unexpected key: size', detail)
        self.assertIn('fal_request.params', detail)

    def test_flat_error_shape_still_works(self):
        detail = GENERATION.fal_error_detail('comfly', '任务提交', self._Response(401, '{"message":"invalid token","code":"invalid_api_key"}'))
        self.assertIn('invalid_api_key', detail)
        self.assertIn('API 平台管理', detail)


async def _fake_ref_url(ref):
    # fal 高清修复需要 fal 服务器可拉取的输入图（站内路径必须公网化，见 FalReferencePublicUrlTests）。
    return str((ref or {}).get('url') or '') if isinstance(ref, dict) else str(ref or '')


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
