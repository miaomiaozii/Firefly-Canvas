"""#381：#349 没盖到的那些入口，同样不许把「认不出的服务商」静默换成主平台。

票面三条验收：
- 每个改动的入口：未知 id → 一次上游请求都不发出，错误里带那个 id；
- 全仓再扫 get_api_provider( 的调用点，列出仍未改的并说明为什么排除（这里把它钉成门禁）；
- 请求模型不带 'comfly' 默认值。

接缝还是 #349 那个：「按 id 解析服务商」。服务层是 `_svc._main(name)` 在调用时查 main 的
globals，所以 patch main.<name> 就能换表；路由模块是 import 时把名字绑进自己 globals 的，
要 patch 路由模块自己的属性。测试只换内存里的假服务商表，不碰网络，也不碰 data/api_providers.json。
"""
import asyncio
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.core import (AssetLibraryClassifyRequest, LocalAssetCaptionRequest,
                      LocalAssetClassifyRequest, LocalAssetUrlImportRequest, OnlineImageRequest)
from app.routers import api_local_assets
from app.services import conversation as conversation_svc
from app.services import generation as generation_svc
from app.services import misc as misc_svc

UNKNOWN_ID = 'no-such-provider-381'

PROVIDERS = [
    {'id': 'alpha', 'name': '甲平台', 'base_url': 'https://alpha.test/v1', 'enabled': True,
     'primary': True, 'chat_models': ['m-chat'], 'image_models': ['m-img'], 'video_models': ['m-vid']},
    {'id': 'beta', 'name': '乙平台', 'base_url': 'https://beta.test/v1', 'enabled': True,
     'primary': False, 'chat_models': ['m-chat'], 'image_models': ['m-img'], 'video_models': ['m-vid']},
]

# 一个能生图的启用平台都没有：pick_chat_image_provider 会落到最后的兜底分支。
PROVIDERS_WITHOUT_IMAGE_MODELS = [
    {'id': 'alpha', 'name': '甲平台', 'base_url': 'https://alpha.test/v1', 'enabled': True,
     'primary': True, 'chat_models': ['m-chat'], 'image_models': [], 'video_models': []},
]


class ProviderTableCase(unittest.TestCase):
    """把服务商表换成内存假表；上游一律装成「一发就炸」。"""

    def setUp(self):
        self.upstream_image = AsyncMock(side_effect=AssertionError('不该发上游生图请求'))
        self.upstream_chat = AsyncMock(side_effect=AssertionError('不该发上游聊天请求'))
        patchers = [
            patch.object(main, 'load_api_providers', lambda: [dict(p) for p in PROVIDERS]),
            patch.object(main, 'run_provider_generation_with_backoff', self.upstream_image),
            patch.object(main, 'chat_request_latest', self.upstream_chat),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

    def assert_rejected_with_the_id(self, awaitable, label):
        try:
            asyncio.run(awaitable)
        except HTTPException as exc:
            self.assertEqual(exc.status_code, 400, f'{label} 该是 400，实际 {exc.status_code}')
            self.assertIn(UNKNOWN_ID, str(exc.detail), f'{label} 的错误里没说是哪个服务商：{exc.detail}')
            return
        except AssertionError as exc:
            self.fail(f'{label} 走到了上游（{exc}），说明没在解析服务商时就拦住')
        self.fail(f'{label} 用未知服务商 {UNKNOWN_ID} 竟然放行，等于还在静默换平台')


class RequestModelDefaultsTests(unittest.TestCase):
    """票面：请求模型去掉 'comfly' 默认值。"""

    def test_no_request_model_defaults_to_comfly(self):
        for model in (OnlineImageRequest, LocalAssetCaptionRequest, LocalAssetClassifyRequest,
                      LocalAssetUrlImportRequest, AssetLibraryClassifyRequest):
            fields = model.model_fields
            key = 'provider_id' if 'provider_id' in fields else 'provider'
            self.assertEqual(fields[key].default, '',
                             f'{model.__name__}.{key} 还默认 comfly，等于把「没选」写成「选了 comfly」')

    def test_generation_entrypoints_no_longer_default_to_comfly(self):
        src = (ROOT / 'app/services/generation.py').read_text(encoding='utf-8')
        code = '\n'.join(line.split('#', 1)[0] for line in src.splitlines())
        self.assertNotIn("provider_id='comfly'", code, 'generate_ai_image 的默认服务商不该再写 comfly')
        self.assertNotIn("provider_id or 'comfly'", code, '日志标签不该拿 comfly 充数')


class ServiceEntryLookupTests(ProviderTableCase):
    """每个改过的入口：未知 id 就地 400，错误带 id，且一次上游都不发。"""

    def test_online_image_entry(self):
        payload = OnlineImageRequest(prompt='画一只萤火虫', provider_id=UNKNOWN_ID, model='m-img')
        self.assert_rejected_with_the_id(misc_svc.build_online_image_result(payload), '/api/online-image')

    def test_caption_entry(self):
        self.assert_rejected_with_the_id(
            generation_svc.caption_image_with_provider('x.png', '描述图片', UNKNOWN_ID, 'm-chat'),
            '本地素材反推提示词')

    def test_classify_entry(self):
        self.assert_rejected_with_the_id(
            generation_svc.classify_image_with_provider('x.png', UNKNOWN_ID),
            '素材智能分类')

    def test_chat_reply_entry(self):
        payload = main.CanvasLLMRequest(message='你好', provider=UNKNOWN_ID, model='m-chat')
        self.assert_rejected_with_the_id(
            conversation_svc.build_chat_text_reply(payload, {'messages': []}), '聊天回复')

    def test_agent_router_entry(self):
        payload = main.CanvasLLMRequest(message='画一只萤火虫', provider=UNKNOWN_ID, model='m-chat')
        self.assert_rejected_with_the_id(
            conversation_svc.decide_chat_agent_action(payload, {'messages': []}, []), 'Agent 意图路由')

    def test_resolve_chat_provider_entry(self):
        try:
            conversation_svc.resolve_chat_provider(UNKNOWN_ID, 'm-chat', '')
        except HTTPException as exc:
            self.assertIn(UNKNOWN_ID, str(exc.detail))
            return
        self.fail('resolve_chat_provider 认不出服务商却放行了')

    def test_pick_chat_image_provider_tail_does_not_invent_a_provider(self):
        # 没有任何「启用且有图片模型」的平台时才走兜底：以前拿 comfly 顶上再静默换主平台。
        with patch.object(main, 'load_api_providers',
                          lambda: [dict(p) for p in PROVIDERS_WITHOUT_IMAGE_MODELS]):
            try:
                picked = conversation_svc.pick_chat_image_provider(UNKNOWN_ID, '')
            except HTTPException as exc:
                self.assertIn(UNKNOWN_ID, str(exc.detail))
                return
            self.fail(f'空手生图还挑出了 {picked.get("id")}，等于凭空造了个平台')

    def test_generate_ai_image_rejects_unknown_provider(self):
        self.assert_rejected_with_the_id(
            main.generate_ai_image('画一只萤火虫', '1024x1024', 'auto', 'm-img', [], UNKNOWN_ID),
            'generate_ai_image')

    def test_preserve_model_path_still_rejects_empty_provider(self):
        # preserve_model 这条路一直是 exact（空值也拒），本票没放宽它。
        try:
            asyncio.run(main.generate_ai_image('画一只萤火虫', '1024x1024', 'auto', 'm-img', [],
                                               '', preserve_model=True))
        except HTTPException as exc:
            self.assertEqual(exc.status_code, 400)
            return
        self.fail('保模型路径用空服务商也放行了')

    def test_known_provider_still_resolves_for_these_entries(self):
        # 反向确认：不是把门焊死，认得出的（带空格、换大小写）还是那一家，而不是主平台。
        with patch.object(main, 'load_api_providers', lambda: [dict(p) for p in PROVIDERS]), \
             patch.object(main, 'provider_env_key_value', lambda pid: 'test-key'):
            self.assertEqual(conversation_svc.resolve_chat_provider(' BETA ', 'm-chat', '')[0],
                             'https://beta.test/v1')


class OnlineImageHttpTests(ProviderTableCase):
    """HTTP 层面：错误体里得带上是哪个服务商，而且上游一次都没发。"""

    def test_online_image_endpoint_400_names_the_unknown_provider(self):
        client = TestClient(main.app)
        response = client.post('/api/online-image',
                               json={'prompt': '画一只萤火虫', 'provider_id': UNKNOWN_ID, 'model': 'm-img'})
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertIn(UNKNOWN_ID, response.text, f'错误体里没说是哪个服务商：{response.text[:300]}')
        self.assertEqual(self.upstream_image.await_count, 0, '已经往上游发请求了')

    def test_online_image_endpoint_still_works_without_a_provider(self):
        # 没指定≠报错：该按主平台走。假上游故意炸，只为看它有没有走到上游边界。
        client = TestClient(main.app, raise_server_exceptions=False)
        response = client.post('/api/online-image', json={'prompt': '画一只萤火虫', 'model': 'm-img'})
        self.assertNotEqual(response.status_code, 400,
                            f'没指定服务商不该被当成错误：{response.text[:300]}')
        self.assertEqual(self.upstream_image.await_count, 1, '“空值→主平台”这条路没走到上游')


class ConversationNodeAdapterTests(unittest.TestCase):
    """对话节点：没存服务商就传空，不再把 'comfly' 写进配置或请求。"""

    def _code(self, rel):
        src = (ROOT / rel).read_text(encoding='utf-8')
        return '\n'.join(line.split('#', 1)[0] for line in src.splitlines())

    def test_no_or_comfly_fallback_left_in_the_conversation_chain(self):
        for rel in ('app/services/agents.py', 'app/routers/startup.py',
                    'app/routers/api_project_library.py', 'app/services/conversation.py'):
            self.assertNotIn("or 'comfly'", self._code(rel), f'{rel} 还在拿 comfly 当兜底')

    def test_node_adapter_keeps_empty_provider(self):
        # 存上去的必须是空，不是 'comfly'：写死一个不存在的平台，下次跑只能靠后端瞎换。
        src = self._code('app/routers/api_project_library.py')
        self.assertIn("stored_adapter.get('provider') or ''", src,
                      '存对话节点适配配置时不该把没选写成 comfly')


class OldLookupCallSiteScanTests(unittest.TestCase):
    """票面验收第二条：全仓扫 get_api_provider( ，剩下的必须只有点名排除那一处。

    排除理由（review 时已核对）：app/routers/api_project_library.py:1204 取回失败任务时
    自己比对 candidate['id']，认不出就退回「按记录里的原平台尝试」，不会发给别家。
    """

    PATTERN = re.compile(r"(?<![_A-Za-z])get_api_provider\(")
    ALLOWED = {'app/services/providers.py', 'app/routers/api_project_library.py'}

    def test_only_documented_sites_still_call_the_silent_lookup(self):
        offenders = {}
        for path in (ROOT / 'app').rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if rel in self.ALLOWED:
                continue
            for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if self.PATTERN.search(line.split('#', 1)[0]):
                    offenders[rel] = lineno
        self.assertEqual(offenders, {},
                         '这些地方还在用会静默换服务商的 get_api_provider：' + ', '.join(
                             f'{rel}:{lineno}' for rel, lineno in sorted(offenders.items())))


if __name__ == '__main__':
    unittest.main()
