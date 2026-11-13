"""Tests for issue #290: canvas.edit_erase + production local_edit_adapter（bailian qwen-image-edit-max-2026-01-16）。

票面语义：按 prompt 擦除节点图选区——REST 链先裁好选区（ADR 0007：绝不向模型发 mask），
裁剪区 PNG 作为 request['image'] 交给 local-edit adapter；生产 adapter = bailian provider
（qwen-image-edit-max-2026-01-16，model_specs.json img2img + size；调用方式镜像既有图片生成链：
provider_endpoint_url + api_headers + /images/generations，images_api_unsupported 时回退
/images/edits）；模型结果 composite_rect 合成回节点图（原资源保留可恢复）。
与 edit_outpaint 同模式：写操作走 #242 确认门；错误文案带下一步建议（票面规范）。
Mock 边界：零真实网络（adapter 单测 stub http 会话 + provider 查询；E2E 直接替换
main.local_edit_adapter）；图像媒体走 store.import_media 本地字节；
确认门决策走 gate 自身的 confirm handler（同 test_mcp_canvas_edit_outpaint_289.py 先例）。
"""
import base64
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401
from app.core import (BAILIAN_LOCAL_EDIT_MODEL, BAILIAN_PROVIDER_ID,
                      _bailian_local_edit_adapter, wire_local_edit_adapter)
from app.routers import api_mcp
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess
from tests.test_mcp_endpoint import McpTestBase, run_async

#: 节点原图颜色（本地 PNG 字节，走 store.import_media）
SOURCE_COLOR = (200, 30, 30)
#: fake adapter 返回的"模型重绘"颜色（合成回选区后可像素断言）
MODEL_COLOR = (0, 255, 128)


def _png_bytes(size=(16, 16), color=SOURCE_COLOR):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _b64_png(size=(8, 8), color=MODEL_COLOR):
    return base64.b64encode(_png_bytes(size=size, color=color)).decode("ascii")


def _open_asset(store, project_id, asset_id):
    record = store.media_record(project_id, asset_id)
    with open(record["path"], "rb") as f:
        return Image.open(io.BytesIO(f.read()))


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else ("OK" if json_data is not None else "")
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json payload")
        return self._json


class _FakeSession:
    """零真实外连的 http 会话：post 按序弹出预备响应并记录调用；get 返回 download 响应。"""

    def __init__(self, responses=None, download=None):
        self.responses = list(responses or [])
        self.download = download
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return _FakeResponse(200, {"data": [{"b64_json": _b64_png()}]})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.download or _FakeResponse(200, content=b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_provider():
    return {
        "id": "bailian",
        "name": "百炼",
        "base_url": "https://bailian.test/compatible-mode/v1",
        "protocol": "openai",
    }


#: 区分“用默认 fake provider”与“未注册（None）”
_UNSET = object()


def _run_adapter(request, session, provider=_UNSET):
    """跑 _bailian_local_edit_adapter：stub http 会话 + provider 查询 + api_headers（零网络）。"""
    with mock.patch("app.core._bailian_http_session", return_value=session), \
         mock.patch("app.services.providers.get_api_provider_exact",
                    return_value=_fake_provider() if provider is _UNSET else provider), \
         mock.patch("app.services.providers.api_headers",
                    return_value={"Accept": "application/json",
                                  "Authorization": "Bearer test-key",
                                  "Content-Type": "application/json"}):
        return _bailian_local_edit_adapter(request)


class BailianAdapterTests(unittest.TestCase):
    """_bailian_local_edit_adapter 单测：fake http 响应 → 模型字节（无真实网络/凭据）。"""

    def test_b64_response_returns_decoded_model_bytes(self):
        session = _FakeSession(responses=[
            _FakeResponse(200, {"data": [{"b64_json": _b64_png(size=(8, 8), color=MODEL_COLOR)}]})
        ])
        request = {"prompt": "erase the red cup", "image": _png_bytes(size=(8, 8)),
                   "selection": [4, 4, 12, 12], "size": "8x8"}
        result = _run_adapter(request, session)
        img = Image.open(io.BytesIO(result))
        self.assertEqual(img.size, (8, 8))
        # adapter 返回模型原样字节（未做 as_rgba，合成由 core 负责）：RGB PNG 往返
        self.assertEqual(img.getpixel((0, 0)), MODEL_COLOR)
        # 单发 /images/generations；body 遵循既有图片生成约定（data URL + size + b64_json）
        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://bailian.test/compatible-mode/v1/images/generations")
        self.assertIn("Authorization", kwargs["headers"])
        body = kwargs["json"]
        self.assertEqual(body["model"], BAILIAN_LOCAL_EDIT_MODEL)
        self.assertEqual(body["prompt"], "erase the red cup")
        self.assertEqual(body["size"], "8x8")
        self.assertEqual(body["response_format"], "b64_json")
        self.assertEqual(len(body["image"]), 1)
        self.assertTrue(body["image"][0].startswith("data:image/png;base64,"))
        # ADR 0007：请求里绝不带 mask
        self.assertNotIn("mask", body)

    def test_url_response_downloads_image_bytes(self):
        download_bytes = _png_bytes(size=(4, 4), color=(9, 9, 9))
        session = _FakeSession(
            responses=[_FakeResponse(200, {"data": [{"url": "https://img.test/out.png"}]})],
            download=_FakeResponse(200, content=download_bytes))
        result = _run_adapter({"prompt": "erase x", "image": _png_bytes(size=(4, 4))}, session)
        self.assertEqual(result, download_bytes)
        self.assertEqual([c[0] for c in session.calls], ["POST", "GET"])
        self.assertEqual(session.calls[1][1], "https://img.test/out.png")

    def test_falls_back_to_openai_edits_when_images_api_unsupported(self):
        session = _FakeSession(responses=[
            _FakeResponse(400, text="Error: images api is not supported for this platform"),
            _FakeResponse(200, {"data": [{"b64_json": _b64_png(size=(8, 8))}]}),
        ])
        result = _run_adapter({"prompt": "erase x", "image": _png_bytes(size=(8, 8))}, session)
        self.assertEqual(Image.open(io.BytesIO(result)).size, (8, 8))
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1], "https://bailian.test/compatible-mode/v1/images/generations")
        method, url, kwargs = session.calls[1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/images/edits"))
        self.assertIn("files", kwargs)  # multipart（镜像 post_openai_edits 模式）

    def test_http_error_raises_with_status(self):
        session = _FakeSession(responses=[_FakeResponse(500, text="internal boom")])
        with self.assertRaises(RuntimeError) as ctx:
            _run_adapter({"prompt": "erase x", "image": _png_bytes()}, session)
        self.assertIn("500", str(ctx.exception))

    def test_rejects_empty_prompt_and_empty_image(self):
        session = _FakeSession()
        with self.assertRaises(ValueError):
            _run_adapter({"prompt": "  ", "image": _png_bytes()}, session)
        with self.assertRaises(ValueError):
            _run_adapter({"prompt": "erase x", "image": b""}, session)
        self.assertEqual(session.calls, [])  # 校验失败不触网

    def test_unregistered_provider_raises(self):
        session = _FakeSession()
        with self.assertRaises(RuntimeError) as ctx:
            _run_adapter({"prompt": "erase x", "image": _png_bytes()}, session, provider=None)
        self.assertIn("未注册", str(ctx.exception))
        self.assertEqual(session.calls, [])


class WireLocalEditAdapterTests(unittest.TestCase):
    """wire_local_edit_adapter：凭据存在才替换 app_main.local_edit_adapter（对 fake main 断言，不动 main 本体）。"""

    def test_replaces_adapter_when_credential_present(self):
        fake_main = types.SimpleNamespace(local_edit_adapter=lambda request: b"offline")
        with mock.patch("app.services.providers.provider_env_key_value", return_value="sk-test"):
            self.assertTrue(wire_local_edit_adapter(fake_main))
        self.assertIs(fake_main.local_edit_adapter, _bailian_local_edit_adapter)

    def test_keeps_offline_adapter_without_credential(self):
        sentinel = lambda request: b"offline"
        fake_main = types.SimpleNamespace(local_edit_adapter=sentinel)
        with mock.patch("app.services.providers.provider_env_key_value", return_value=None):
            self.assertFalse(wire_local_edit_adapter(fake_main))
        self.assertIs(fake_main.local_edit_adapter, sentinel)

    def test_falls_back_to_process_env_when_lookup_crashes(self):
        fake_main = types.SimpleNamespace(local_edit_adapter=lambda request: b"offline")

        def _boom(provider_id):
            raise TypeError("main half-initialized")

        with mock.patch("app.services.providers.provider_env_key_value", side_effect=_boom), \
             mock.patch.dict(os.environ, {"API_PROVIDER_BAILIAN_KEY": "sk-env"}):
            self.assertTrue(wire_local_edit_adapter(fake_main))
        self.assertIs(fake_main.local_edit_adapter, _bailian_local_edit_adapter)


class EditEraseSurfaceTests(McpTestBase):
    """声明表机制：edit_erase 已注册（25 项，票F 后）、严格 schema、确认门 + impl 接线（同 289 先例）。"""

    def test_tool_registered_gated_and_wired(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        self.assertEqual(len(tools), 25)
        self.assertIn("canvas.edit_erase", tools)
        schema = tools["canvas.edit_erase"]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema.get("additionalProperties"))
        self.assertEqual(schema["required"], ["node_id", "rect", "prompt"])
        rect = schema["properties"]["rect"]
        self.assertEqual(rect["required"], ["x", "y", "width", "height"])
        self.assertFalse(rect.get("additionalProperties"))
        # #242 语义：写操作必须声明确认门
        self.assertIn("确认门", tools["canvas.edit_erase"]["description"])
        self.assertIn("canvas.edit_erase", api_mcp.CONFIRMATION_GATED_TOOLS)
        self.assertIn("canvas.edit_erase", api_mcp.TOOL_IMPLS)

    def test_bailian_model_constants(self):
        # model_specs.json 登记的编辑模型（img2img + size），provider 常量
        from app.core import model_spec_for
        spec = model_spec_for(BAILIAN_LOCAL_EDIT_MODEL)
        self.assertIsNotNone(spec, "qwen-image-edit-max-2026-01-16 must be in model_specs.json")
        self.assertEqual(BAILIAN_PROVIDER_ID, "bailian")


class EditEraseGateTests(McpTestBase):
    """canvas.edit_erase 经 #242 确认门 + core：批准（选区合成回贴）/ 拒绝 / 各类校验错误（下一步建议）。"""

    def _setup(self):
        self.open_tab()
        record = self.store.import_media(self.project_id, _png_bytes(size=(16, 16)), "media.png", "image/png")
        asset_id = record["asset_id"]
        canvas = self.store.canvas(self.project_id)
        node = {
            "id": "img-1", "type": "smart-image", "title": "img-1", "x": 0, "y": 0,
            "images": [{"asset_id": asset_id, "kind": "image", "name": "media.png"}],
        }
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        # materialize the agent node + its session（同 289 _setup 先例）
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=83))
        agent_node_id = self.materialized_agent_node()["id"]
        session = run_async(agent_host_manager.get_or_create_session(
            self.project_id, agent_node_id))
        session.process = FakeAgentProcess(script=[])
        return session, agent_node_id, asset_id

    def _run_gated_erase(self, approve, args, adapter_error=None):
        session, agent_node_id, asset_id = self._setup()
        from app.routers.api_mcp import _impl_edit_erase

        captured = []
        adapter_requests = []
        outcome = {}
        saved_adapter = main.local_edit_adapter

        def fake_adapter(request):
            adapter_requests.append(request)
            if adapter_error is not None:
                raise adapter_error
            return _png_bytes(size=(16, 16), color=MODEL_COLOR)

        main.local_edit_adapter = fake_adapter
        try:
            async def scenario():
                async def decide_handler(sess, event):
                    captured.append(event)
                    if event.get("type") == "confirm" and approve is not None:
                        for handler in list(sess.confirm_handlers):
                            await handler(event["confirm_id"], approve)
                agent_host_manager.on_event(session, decide_handler)
                try:
                    outcome["result"] = await _impl_edit_erase(
                        self.project_id, agent_node_id, args)
                except Exception as exc:  # noqa: BLE001 — gate raises McpToolError / impl raises ValueError
                    outcome["error"] = exc
                finally:
                    session.event_handlers.remove(decide_handler)

            run_async(scenario())
        finally:
            main.local_edit_adapter = saved_adapter
        confirms = [e for e in captured if e.get("type") == "confirm"]
        self.assertEqual(len(confirms), 1, f"gate must dispatch exactly one confirm event, got {captured}")
        self.assertEqual(confirms[0]["tool"], "canvas.edit_erase")
        return (outcome.get("error") or outcome.get("result")), confirms, adapter_requests, asset_id

    def _node(self, node_id="img-1"):
        canvas = self.store.canvas(self.project_id)
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        self.assertIsNotNone(node, node_id)
        return node

    def test_approval_erases_selection_and_composites_back(self):
        result, _confirms, adapter_requests, original = self._run_gated_erase(
            True, {"node_id": "img-1",
                   "rect": {"x": 4, "y": 4, "width": 8, "height": 8},
                   "prompt": "erase the red cup"})
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result.get("success"), result)
        self.assertNotEqual(result["asset_id"], original)
        self.assertEqual(result["original_asset_id"], original)
        self.assertEqual((result["width"], result["height"]), (16, 16))  # 合成保持原图尺寸
        self.assertEqual(result["selection"], [4, 4, 12, 12])
        self.assertIn("canvas.view", result["next_step"])
        # 节点图被替换；原资源保留在资源库可恢复
        self.assertEqual(self._node()["images"][0]["asset_id"], result["asset_id"])
        self.assertIsNotNone(self.store.media_record(self.project_id, original))
        # adapter 请求契约：裁剪区字节 + prompt + 归一化选区 + size（ADR 0007：无 mask 字段）
        self.assertEqual(len(adapter_requests), 1)
        req = adapter_requests[0]
        self.assertEqual(req["mode"], "modify")
        self.assertEqual(req["prompt"], "erase the red cup")
        self.assertEqual(req["selection"], [4, 4, 12, 12])
        self.assertEqual(req["size"], "8x8")
        self.assertTrue(isinstance(req["image"], (bytes, bytearray)) and req["image"])
        self.assertNotIn("mask", req)
        # 像素断言：选区内 = 模型重绘色，选区外 = 原图像素
        img = _open_asset(self.store, self.project_id, result["asset_id"])
        self.assertEqual(img.size, (16, 16))
        self.assertEqual(img.getpixel((4, 4)), MODEL_COLOR + (255,))    # 选区左上
        self.assertEqual(img.getpixel((11, 11)), MODEL_COLOR + (255,))  # 选区右下
        self.assertEqual(img.getpixel((0, 0)), SOURCE_COLOR + (255,))   # 选区外
        self.assertEqual(img.getpixel((3, 7)), SOURCE_COLOR + (255,))
        self.assertEqual(img.getpixel((15, 15)), SOURCE_COLOR + (255,))

    def test_rejection_leaves_node_untouched_and_never_calls_adapter(self):
        outcome, _confirms, adapter_requests, original = self._run_gated_erase(
            False, {"node_id": "img-1",
                    "rect": {"x": 4, "y": 4, "width": 8, "height": 8},
                    "prompt": "erase the red cup"})
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("拒绝", str(outcome))
        self.assertEqual(self._node()["images"][0]["asset_id"], original)
        self.assertEqual(adapter_requests, [])  # 拒绝发生在模型调用之前

    def test_empty_prompt_is_error_with_next_step(self):
        outcome, _confirms, adapter_requests, original = self._run_gated_erase(
            True, {"node_id": "img-1",
                   "rect": {"x": 4, "y": 4, "width": 8, "height": 8}, "prompt": "  "})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("prompt", str(outcome))
        self.assertIn("canvas.view", str(outcome))  # 下一步建议
        self.assertEqual(self._node()["images"][0]["asset_id"], original)
        self.assertEqual(adapter_requests, [])

    def test_rect_out_of_bounds_is_error_with_next_step(self):
        outcome, _confirms, adapter_requests, original = self._run_gated_erase(
            True, {"node_id": "img-1",
                   "rect": {"x": 4, "y": 4, "width": 20, "height": 8}, "prompt": "erase x"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("越界", str(outcome))
        self.assertIn("canvas.get_node", str(outcome))  # 下一步建议
        self.assertEqual(self._node()["images"][0]["asset_id"], original)
        self.assertEqual(adapter_requests, [])

    def test_non_positive_rect_is_error_with_next_step(self):
        outcome, _confirms, adapter_requests, _original = self._run_gated_erase(
            True, {"node_id": "img-1",
                   "rect": {"x": 4, "y": 4, "width": 0, "height": 8}, "prompt": "erase x"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("canvas.get_node", str(outcome))
        self.assertEqual(adapter_requests, [])

    def test_non_dict_rect_is_error_with_next_step(self):
        outcome, _confirms, adapter_requests, _original = self._run_gated_erase(
            True, {"node_id": "img-1", "rect": "4,4,8,8", "prompt": "erase x"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("rect", str(outcome))
        self.assertIn("canvas.get_node", str(outcome))
        self.assertEqual(adapter_requests, [])

    def test_missing_node_is_error_with_next_step(self):
        outcome, _confirms, _adapter_requests, _original = self._run_gated_erase(
            True, {"node_id": "ghost",
                   "rect": {"x": 4, "y": 4, "width": 8, "height": 8}, "prompt": "erase x"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("ghost", str(outcome))
        self.assertIn("canvas.list_nodes", str(outcome))  # 下一步建议

    def test_node_without_image_is_error_with_next_step(self):
        canvas = self.store.canvas(self.project_id)
        node = {"id": "empty-1", "type": "smart-image", "title": "empty", "x": 0, "y": 0}
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        outcome, _confirms, _adapter_requests, _original = self._run_gated_erase(
            True, {"node_id": "empty-1",
                   "rect": {"x": 4, "y": 4, "width": 8, "height": 8}, "prompt": "erase x"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("media.upload", str(outcome))

    def test_adapter_failure_is_error_with_next_step_and_untouched_node(self):
        outcome, _confirms, _adapter_requests, original = self._run_gated_erase(
            True, {"node_id": "img-1",
                   "rect": {"x": 4, "y": 4, "width": 8, "height": 8}, "prompt": "erase x"},
            adapter_error=RuntimeError("qwen-image-edit 调用失败（HTTP 500）"))
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("局部编辑模型调用失败", str(outcome))
        self.assertIn("bailian", str(outcome))  # 下一步建议：检查凭据/网络
        self.assertEqual(self._node()["images"][0]["asset_id"], original)


if __name__ == "__main__":
    unittest.main()
