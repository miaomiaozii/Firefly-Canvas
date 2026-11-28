"""Tests for issue #266: canvas.view / canvas.view_media（只读视图工具面扩面）。

ComfyArt §59 语义（docs/reference-comfyart-canvas.md §59.4）：
- canvas.view：整页/选区导出图片喂模型（contact sheet，base64 PNG，
  max_px = min(1568, args.max_px || 1024)，PNG 不超过 1 MiB（#352）；慢操作独立 90s 超时契约）
- canvas.view_media：单节点媒体查看（图像下缩放 / 视频抽帧拼图，
  复用 generate_video_preview_image 链路）

票面规范：工具参数 additionalProperties:false + 公共参数片段 + 错误文案带下一步建议。
#352：canvas.view / canvas.view_media 成功结果以 MCP image 内容块返回（content[0]），
text 块仅含元数据，禁止包含 image_data_url 或图片 base64。
Mock 边界：无网络（媒体走 store.import_media 本地字节，视频抽帧用 monkeypatch 替身，
不依赖本机 ffmpeg/联网）。慢超时契约用缩小的超时值验证 wait_for 接线。
"""
import base64
import io
import json
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401
from app.routers import api_mcp
from tests.test_mcp_endpoint import McpTestBase


def _png_bytes(size=(8, 8), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _image_blocks(result):
    return [b for b in result["content"] if b.get("type") == "image"]


def _noise_png(size=(1536, 1536)):
    image = Image.frombytes("RGB", size, random.Random(352).randbytes(size[0] * size[1] * 3))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _assert_bounded_preview(test, result, data, max_px=1024):
    test.assertFalse(result.get("isError"), data)
    test.assertEqual([block["type"] for block in result["content"]], ["image", "text"])
    block = _image_blocks(result)[0]
    test.assertEqual(block["mimeType"], "image/png")
    text = result["content"][1]["text"]
    for forbidden in ("image_data_url", "base64", "data:image", block["data"]):
        test.assertNotIn(forbidden, text)
    raw = base64.b64decode(block["data"], validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        test.assertEqual(image.format, "PNG")
        test.assertEqual(image.size, (data["width"], data["height"]))
        test.assertGreaterEqual(min(image.size), 1)
        test.assertLessEqual(max(image.size), max_px)
    test.assertLessEqual(len(raw), 1048576)
    test.assertLessEqual(len(block["data"]), 1398104)


class CanvasViewSurfaceTests(McpTestBase):
    """声明表机制：新工具必须进 api_mcp.TOOLS 声明表（ADR 0022）。"""

    def test_view_tools_declared_with_strict_schemas(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        for name in ("canvas.view", "canvas.view_media"):
            self.assertIn(name, tools)
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema.get("additionalProperties"),
                             f"{name} 必须声明 additionalProperties:false")
        view = tools["canvas.view"]["inputSchema"]
        self.assertEqual(view["required"], ["scope"])
        self.assertIn("page", view["properties"]["scope"]["enum"])
        self.assertIn("selection", view["properties"]["scope"]["enum"])
        self.assertIn("node_ids", view["properties"])
        self.assertIn("max_px", view["properties"])
        media = tools["canvas.view_media"]["inputSchema"]
        self.assertEqual(media["required"], ["node_id"])
        self.assertIn("max_px", media["properties"])
        self.assertIn("frames", media["properties"])
        for schema in (view, media):
            maximum = schema["properties"]["max_px"]
            self.assertEqual(maximum["maximum"], 1568)
            self.assertIn("1 MiB", maximum["description"])
        # 慢操作 90s 超时契约必须写进工具描述（ComfyArt 慢工具超时模式）
        for name in ("canvas.view", "canvas.view_media"):
            self.assertIn("90", tools[name]["description"])
        # 只读工具不走确认门（#242 语义：写操作才需确认）
        self.assertNotIn("确认门", tools["canvas.view"]["description"])

    def test_view_tools_registered_in_impls_and_slow_timeouts(self):
        self.assertIn("canvas.view", api_mcp.TOOL_IMPLS)
        self.assertIn("canvas.view_media", api_mcp.TOOL_IMPLS)
        # 慢操作独立超时契约（90s，区别于 generate.wait 的 600s 契约）
        self.assertEqual(api_mcp.SLOW_TOOL_TIMEOUTS["canvas.view"], 90.0)
        self.assertEqual(api_mcp.SLOW_TOOL_TIMEOUTS["canvas.view_media"], 90.0)
        # generate.wait 类工具不进慢超时表
        self.assertNotIn("generate.wait", api_mcp.SLOW_TOOL_TIMEOUTS)


class CanvasViewTests(McpTestBase):
    """canvas.view 行为：整页/选区 contact sheet + 错误文案带下一步。"""

    def setUp(self):
        super().setUp()
        self.open_tab()

    def add_media_node(self, node_id, png=None, kind="image", name="media.png"):
        record = self.store.import_media(self.project_id, png or _png_bytes(), name,
                                         "image/png" if kind == "image" else "video/mp4")
        canvas = self.store.canvas(self.project_id)
        node = {
            "id": node_id, "type": "smart-image", "title": node_id, "x": 0, "y": 0,
            "images": [{"asset_id": record["asset_id"], "kind": kind, "name": name}],
        }
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        return record["asset_id"]

    def call(self, name, args):
        result = self.tool_result(self.tool_call(name, args, project=self.project_id))
        if result.get("isError"):
            return result, None
        texts = [b for b in result["content"] if b.get("type") == "text"]
        return result, json.loads(texts[0]["text"])

    def call_error(self, name, args):
        result = self.tool_result(self.tool_call(name, args, project=self.project_id))
        self.assertTrue(result.get("isError"), f"expected isError, got {result}")
        return result["content"][0]["text"]

    def test_view_empty_canvas_is_error_with_next_step(self):
        text = self.call_error("canvas.view", {"scope": "page"})
        self.assertIn("media.upload", text)  # 下一步建议

    def test_view_bad_scope_is_error_with_next_step(self):
        self.add_media_node("img-a")
        text = self.call_error("canvas.view", {"scope": "bogus"})
        self.assertIn("page", text)

    def test_view_page_returns_contact_sheet(self):
        self.add_media_node("img-a")
        self.add_media_node("img-b", png=_png_bytes(color=(30, 200, 30)))
        result, data = self.call("canvas.view", {"scope": "page"})
        self.assertFalse(result.get("isError"), data)
        images = _image_blocks(result)
        self.assertEqual(len(images), 1, result["content"])
        self.assertEqual(images[0]["mimeType"], "image/png")
        self.assertEqual(result["content"][0]["type"], "image")
        self.assertNotIn("image_data_url", data)
        self.assertEqual(data["node_count"], 2)
        self.assertLessEqual(data["width"], 1568)
        self.assertLessEqual(data["height"], 1568)
        img = Image.open(io.BytesIO(base64.b64decode(images[0]["data"])))
        self.assertLessEqual(max(img.size), 1568)
        self.assertEqual({item["node_id"] for item in data["items"]}, {"img-a", "img-b"})

    def test_view_text_block_contains_no_base64_blob(self):
        # #352 验收：文本部分只含元数据，无 base64 图片串
        self.add_media_node("img-a", png=_png_bytes(size=(512, 512)))
        result, data = self.call("canvas.view", {"scope": "page"})
        self.assertFalse(result.get("isError"), data)
        text_blob = "".join(b.get("text", "") for b in result["content"] if b.get("type") == "text")
        self.assertNotIn("base64", text_blob)
        self.assertNotIn("data:image", text_blob)

    def test_view_selection_only_selected_nodes(self):
        self.add_media_node("img-a")
        self.add_media_node("img-b")
        result, data = self.call("canvas.view", {"scope": "selection", "node_ids": ["img-b"]})
        self.assertFalse(result.get("isError"), data)
        self.assertEqual(data["node_count"], 1)
        self.assertEqual([item["node_id"] for item in data["items"]], ["img-b"])

    def test_view_selection_missing_node_is_error_with_next_step(self):
        self.add_media_node("img-a")
        text = self.call_error("canvas.view", {"scope": "selection", "node_ids": ["ghost"]})
        self.assertIn("ghost", text)
        self.assertIn("canvas.list_nodes", text)

    def test_view_max_px_clamped_to_1568(self):
        self.add_media_node("img-a", png=_png_bytes(size=(4096, 4096)))
        for tool, args in (
            ("canvas.view", {"scope": "page", "max_px": 99999}),
            ("canvas.view_media", {"node_id": "img-a", "max_px": 99999}),
        ):
            with self.subTest(tool=tool):
                result, data = self.call(tool, args)
                _assert_bounded_preview(self, result, data, max_px=1568)
                self.assertEqual(max(data["width"], data["height"]), 1568)
                if tool == "canvas.view":
                    self.assertEqual(data["max_px"], 1568)

    def test_view_high_entropy_previews_obey_image_budget(self):
        source = _noise_png()
        asset_id = self.add_media_node("preview-352", png=source)
        source_path = Path(self.store.media_record(self.project_id, asset_id)["path"])
        before_canvas = self.store.canvas(self.project_id)
        for tool, args in (
            ("canvas.view", {"scope": "page"}),
            ("canvas.view_media", {"node_id": "preview-352"}),
        ):
            with self.subTest(tool=tool):
                result, data = self.call(tool, args)
                self.assertEqual(source_path.read_bytes(), source)
                self.assertEqual(self.store.canvas(self.project_id), before_canvas)
                _assert_bounded_preview(self, result, data)
                self.assertLess(max(data["width"], data["height"]), 1024)
                self.assertGreaterEqual(max(data["width"], data["height"]), 512)
                if tool == "canvas.view":
                    self.assertEqual(data["max_px"], 1024)
                else:
                    self.assertEqual(data["width"], data["height"])
                    self.assertEqual(data["asset_id"], asset_id)

    def test_view_high_entropy_video_previews_obey_image_budget(self):
        source = b"preview-352-video-source"
        asset_id = self.add_media_node("video-352", png=source, kind="video", name="clip.mp4")
        source_path = Path(self.store.media_record(self.project_id, asset_id)["path"])
        with Image.open(io.BytesIO(_noise_png())) as frame:
            with patch("app.routers.api_agent_tools._video_frame_or_none", return_value=frame), \
                 patch("app.services.canvas_view.video_duration_seconds", return_value=8.0), \
                 patch("app.services.generation.generate_video_preview_image", side_effect=lambda *a: frame.copy()):
                for tool, args in (
                    ("canvas.view", {"scope": "page"}),
                    ("canvas.view_media", {"node_id": "video-352", "frames": 4}),
                ):
                    with self.subTest(tool=tool):
                        result, data = self.call(tool, args)
                        self.assertEqual(source_path.read_bytes(), source)
                        _assert_bounded_preview(self, result, data)
                        if tool == "canvas.view_media":
                            self.assertEqual(data["kind"], "video")
                            self.assertEqual(data["frames"], 4)
                            self.assertEqual(data["asset_id"], asset_id)
                        else:
                            self.assertEqual(data["items"][0]["kind"], "video")

    def test_view_selection_without_node_ids_is_error(self):
        self.add_media_node("img-a")
        text = self.call_error("canvas.view", {"scope": "selection"})
        self.assertIn("node_ids", text)

    def test_view_slow_timeout_contract(self):
        # canvas.view 走独立 wait_for 慢超时（90s 契约；测试缩短到 0.05s 验证接线）
        import asyncio

        original_timeouts = dict(api_mcp.SLOW_TOOL_TIMEOUTS)
        original_core = api_mcp.canvas_view_core
        api_mcp.SLOW_TOOL_TIMEOUTS = dict(original_timeouts, **{"canvas.view": 0.05})

        async def slow_core(project_id, *a, **k):
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

        api_mcp.canvas_view_core = slow_core
        try:
            text = self.call_error("canvas.view", {"scope": "page"})
            self.assertIn("超时", text)
            self.assertIn("max_px", text)  # 下一步建议
        finally:
            api_mcp.SLOW_TOOL_TIMEOUTS = original_timeouts
            api_mcp.canvas_view_core = original_core


class CanvasViewMediaTests(McpTestBase):
    """canvas.view_media 行为：单节点媒体查看（图像下缩放 / 视频抽帧拼图）。"""

    def setUp(self):
        super().setUp()
        self.open_tab()

    def add_media_node(self, node_id, png=None, kind="image", name="media.png"):
        record = self.store.import_media(self.project_id, png or _png_bytes(), name,
                                         "image/png" if kind == "image" else "video/mp4")
        canvas = self.store.canvas(self.project_id)
        node = {
            "id": node_id, "type": "smart-image", "title": node_id, "x": 0, "y": 0,
            "images": [{"asset_id": record["asset_id"], "kind": kind, "name": name}],
        }
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        return record["asset_id"]

    def call(self, name, args):
        result = self.tool_result(self.tool_call(name, args, project=self.project_id))
        if result.get("isError"):
            return result, None
        texts = [b for b in result["content"] if b.get("type") == "text"]
        return result, json.loads(texts[0]["text"])

    def call_error(self, name, args):
        result = self.tool_result(self.tool_call(name, args, project=self.project_id))
        self.assertTrue(result.get("isError"), f"expected isError, got {result}")
        return result["content"][0]["text"]

    def test_view_media_image_returns_downscaled_png(self):
        self.add_media_node("img-a", png=_png_bytes(size=(64, 32)))
        result, data = self.call("canvas.view_media", {"node_id": "img-a"})
        _assert_bounded_preview(self, result, data)
        self.assertEqual(data["kind"], "image")
        images = _image_blocks(result)
        self.assertEqual(len(images), 1, result["content"])
        img = Image.open(io.BytesIO(base64.b64decode(images[0]["data"])))
        self.assertLessEqual(max(img.size), 1024)
        self.assertEqual(img.size[0] >= 1, True)
        self.assertEqual(img.size, (64, 32))

    def test_view_media_missing_node_is_error_with_next_step(self):
        text = self.call_error("canvas.view_media", {"node_id": "ghost"})
        self.assertIn("ghost", text)
        self.assertIn("canvas.list_nodes", text)

    def test_view_media_node_without_media_is_error_with_next_step(self):
        canvas = self.store.canvas(self.project_id)
        node = {"id": "empty-node", "type": "smart-image", "title": "empty", "x": 0, "y": 0}
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        text = self.call_error("canvas.view_media", {"node_id": "empty-node"})
        self.assertIn("media.upload", text)

    def test_view_media_video_contact_sheet(self):
        # 抽帧器 monkeypatch（mock 网络/不依赖本机 ffmpeg）
        from app.services import canvas_view

        self.add_media_node("vid-a", png=b"fake-video-bytes", kind="video", name="clip.mp4")
        calls = []
        original_sheet = canvas_view.video_contact_sheet

        def fake_sheet(path, frame_fn, duration, frames, cell_w, max_px=1024):
            calls.append((duration, frames))
            tiles = [(f"t{i}", Image.new("RGB", (16, 16), ((i + 1) * 40, 0, 0)))
                     for i in range(frames)]
            return canvas_view.compose_contact_sheet(tiles, max_px)

        canvas_view.video_contact_sheet = fake_sheet
        try:
            result, data = self.call("canvas.view_media", {"node_id": "vid-a", "frames": 4})
            self.assertFalse(result.get("isError"), data)
            self.assertEqual(data["kind"], "video")
            self.assertEqual(data["frames"], 4)
            self.assertGreaterEqual(len(calls), 1)
            images = _image_blocks(result)
            self.assertEqual(len(images), 1, result["content"])  # #352：image 内容块
            img = Image.open(io.BytesIO(base64.b64decode(images[0]["data"])))
            self.assertLessEqual(max(img.size), 1024)
        finally:
            canvas_view.video_contact_sheet = original_sheet

    def test_view_media_video_all_frames_fail_is_error_with_next_step(self):
        from app.services import canvas_view

        self.add_media_node("vid-a", png=b"fake-video-bytes", kind="video", name="clip.mp4")
        original_sheet = canvas_view.video_contact_sheet
        canvas_view.video_contact_sheet = lambda *a, **k: None
        try:
            text = self.call_error("canvas.view_media", {"node_id": "vid-a"})
            self.assertIn("ffmpeg", text)  # 下一步建议：检查 ffmpeg
        finally:
            canvas_view.video_contact_sheet = original_sheet

    def test_video_duration_and_frame_times_are_deterministic(self):
        from app.services import canvas_view

        self.assertEqual(canvas_view.video_frame_times(None, 4), [0.5, 1.5, 2.5, 3.5])
        times = canvas_view.video_frame_times(10.0, 4)
        self.assertEqual(len(times), 4)
        for t in times:
            self.assertGreaterEqual(t, 0.0)
            self.assertLess(t, 10.0)
        # 超上限帧数被钳制
        self.assertEqual(len(canvas_view.video_frame_times(100.0, 99)), canvas_view.MAX_VIDEO_FRAMES)


if __name__ == "__main__":
    unittest.main()
