"""画布真实会传的各种写法都要落到正确的档位/比例上。

历史 bug（#331 追加修复）：画布不同入口传的分辨率写法不一样（'1K'/'1k'/'low'/'medium'/''/不带），
适配器旧实现只认小写 '1k'/'2k'/'4k'，其余一律回落到 2K=8MP —— 用户选 1K 得到 8MP（两分钟、可能 OOM），
选 4K 又悄悄变成 8MP。比例同理：节点 chip 传的是预设名（wide/square/story），旧解析只认 '16:9'。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import main

PROVIDER_ID = 'comfyui'
COMFYUI_MODEL = 'comfyui-h3-image'


class ComfyTierTests(unittest.TestCase):
    def test_resolution_aliases(self):
        from app.services.comfyui_workflows import COMFYUI_DEFAULT_MP_TIER, comfyui_resolution_tier
        for raw, expected in (
            ('1k', '1k'), ('1K', '1k'), ('low', '1k'), ('低', '1k'),
            ('2k', '2k'), ('2K', '2k'), ('medium', '2k'), ('中', '2k'),
            ('4k', '4k'), ('4K', '4k'), ('high', '4k'), ('高', '4k'), ('best', '4k'),
        ):
            self.assertEqual(comfyui_resolution_tier(raw), expected, raw)
        # 不传/不可识别 → 按画布 size 面积反推，不是无条件回落到 2K
        self.assertEqual(comfyui_resolution_tier('', '1280x720'), '1k')
        self.assertEqual(comfyui_resolution_tier('auto', '1920x1080'), '1k')
        self.assertEqual(comfyui_resolution_tier('', '3840x2160'), '2k')
        self.assertEqual(comfyui_resolution_tier(None, '4096x4096'), '4k')
        self.assertEqual(comfyui_resolution_tier('', ''), COMFYUI_DEFAULT_MP_TIER)

    def test_aspect_preset_words(self):
        from app.services.comfyui_workflows import comfyui_parse_aspect
        self.assertEqual(comfyui_parse_aspect('wide'), (16.0, 9.0))
        self.assertEqual(comfyui_parse_aspect('square'), (1.0, 1.0))
        self.assertEqual(comfyui_parse_aspect('story'), (9.0, 16.0))
        self.assertEqual(comfyui_parse_aspect('ultrawide'), (21.0, 9.0))

    def test_resolution_tier_drives_pixels(self):
        from app.services.comfyui_workflows import comfyui_h3_resolution
        self.assertEqual(comfyui_h3_resolution('wide', '1k'), (2728, 1536))
        self.assertEqual(comfyui_h3_resolution('16:9', '1K'), (2728, 1536))
        self.assertEqual(comfyui_h3_resolution('story', '1k'), (1536, 2728))
        self.assertEqual(comfyui_h3_resolution('1:1', 'low'), (2048, 2048))
        self.assertEqual(comfyui_h3_resolution('1:1', 'high'), (4096, 4096))
        # 不传档位：从 size 面积反推（1280×720 ≈ 0.9MP → H3 地板 4MP 档）
        self.assertEqual(comfyui_h3_resolution('wide', '', '1280x720'), (2728, 1536))


class ComfyCanvasPayloadTests(unittest.TestCase):
    """端到端（假 ComfyUI）：画布各种 payload 变体都要落到节点 4 的正确尺寸。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.temp_dir.name, 'api_providers.json')
        with open(self.providers_file, 'w', encoding='utf-8') as f:
            json.dump([{
                'id': PROVIDER_ID, 'name': 'ComfyUI（本地）', 'base_url': 'http://127.0.0.1:8188',
                'protocol': 'comfyui', 'enabled': True,
                'image_models': [COMFYUI_MODEL], 'recommended_models': [COMFYUI_MODEL],
            }], f, ensure_ascii=False)
        self.patcher = patch.object(main, 'API_PROVIDERS_FILE', self.providers_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _node4_for(self, image_params, size='1280x720'):
        import asyncio
        import httpx
        from app.services import generation

        captured = {}

        def respond(request):
            captured['path'] = request.url.path
            if request.url.path == '/prompt':
                captured['body'] = json.loads(request.content.decode('utf-8'))
                return httpx.Response(200, json={'prompt_id': 'p1', 'node_errors': {}})
            if request.url.path.startswith('/history/'):
                return httpx.Response(200, json={'p1': {
                    'status': {'status_str': 'success', 'completed': True, 'messages': []},
                    'outputs': {'9': {'images': [{'filename': 'x.png', 'subfolder': '', 'type': 'output',
                                                  'metadata': {'width': 1, 'height': 1}}]}},
                }})
            if request.url.path == '/view':
                return httpx.Response(200, content=b'\x89PNG\r\n\x1a\n', headers={'content-type': 'image/png'})
            return httpx.Response(404, json={})

        class FakeClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop('transport', None)
                kwargs['transport'] = httpx.MockTransport(respond)
                super().__init__(*args, **kwargs)

        with patch('httpx.AsyncClient', FakeClient), \
             patch.object(generation, 'COMFYUI_IMAGE_POLL_INTERVAL', 0), \
             patch.object(generation, 'COMFYUI_IMAGE_QUEUE_INTERVAL', 0):
            try:
                asyncio.run(generation.generate_ai_image(
                    'x', size, 'medium', COMFYUI_MODEL,
                    reference_images=None, provider_id=PROVIDER_ID, preserve_model=True,
                    image_parameters=image_params,
                ))
            except Exception:
                pass
        return captured['body']['prompt']['4']['inputs']

    def test_canvas_variants(self):
        cases = [
            ({'aspect_ratio': 'wide', 'resolution': '1k'}, (2728, 1536)),
            ({'aspect_ratio': '16:9', 'resolution': 'low'}, (2728, 1536)),
            ({'aspect_ratio': 'story', 'resolution': '1K'}, (1536, 2728)),
            ({'aspect_ratio': '4:3'}, (2368, 1776)),
        ]
        for params, expected in cases:
            node4 = self._node4_for(params)
            self.assertEqual((node4['width'], node4['height']), expected, params)


if __name__ == '__main__':
    unittest.main()
