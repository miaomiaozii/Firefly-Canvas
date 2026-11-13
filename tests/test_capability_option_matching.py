"""档位值大小写不敏感比对（#331 收尾发现，#330 受影响）。

背景：model_specs.json 给用户看的档位是 `2K`/`4K`，而服务商能力表（`image_model_capabilities`）
里常写小写 `2k`/`4k`。画布把下拉里选中的原始字符串直接塞进请求，精确比对就会把合法请求判成
「不兼容参数」→ 400，用户选 2K/4K 直接失败。这里钉住：大小写不同仍算同一档，非法档位照样拒绝。
"""
import unittest

import main
from firefly_generation import (
    ImageGenerationRequest,
    ModelRoutingError,
    _capability_option_match,
    route_image_generation,
)


class CapabilityOptionMatchTests(unittest.TestCase):
    def test_case_insensitive_hit(self):
        self.assertTrue(_capability_option_match('2K', ['1k', '2k', '4k']))
        self.assertTrue(_capability_option_match('2k', ['1K', '2K', '4K']))
        self.assertTrue(_capability_option_match(' HD ', ['hd']))

    def test_unknown_value_still_rejected(self):
        self.assertFalse(_capability_option_match('8K', ['1k', '2k', '4k']))
        self.assertFalse(_capability_option_match('2.5k', ['1k', '2k', '4k']))

    def test_none_is_not_a_mismatch(self):
        self.assertTrue(_capability_option_match(None, ['1k']))

    def test_empty_capability_list_matches_nothing(self):
        self.assertFalse(_capability_option_match('1k', []))


class MinimaxResolutionRoutingTests(unittest.TestCase):
    """真实回归：#330 的 MiniMax 模型选 2K 会 400，直到这里改成大小写不敏感。"""

    def providers(self):
        return main.load_api_providers()

    def route(self, resolution):
        request = ImageGenerationRequest(
            prompt='三颗草莓微距特写',
            size='1024x1024',
            width=None,
            height=None,
            aspect_ratio='3:2',
            resolution=resolution,
            quality='auto',
            count=1,
            media_type='image',
        )
        return route_image_generation(
            request, self.providers(), provider_id='minimax', model='MiniMax-H3-Image',
        )

    def test_uppercase_resolution_routes(self):
        for resolution in ('1K', '2K', '4K'):
            with self.subTest(resolution=resolution):
                route = self.route(resolution)
                self.assertEqual(route.provider_id, 'minimax')
                self.assertEqual(route.model, 'MiniMax-H3-Image')

    def test_lowercase_resolution_still_routes(self):
        route = self.route('2k')
        self.assertEqual(route.provider_id, 'minimax')

    def test_unsupported_resolution_still_rejected(self):
        with self.assertRaises(ModelRoutingError) as ctx:
            self.route('8K')
        self.assertIn('resolution', ctx.exception.incompatible_parameters)


if __name__ == '__main__':
    unittest.main()
