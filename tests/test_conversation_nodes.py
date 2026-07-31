import asyncio
import base64
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from firefly_projects import ProjectStore


class ConversationNodeApiContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0.01)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.static_version_patch = patch.object(main, "sync_static_html_versions")
        self.static_version_patch.start()
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.static_version_patch.stop()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _seed_conversation_canvas(self, *, connected=True):
        project = self.store.create_project("对话节点项目")
        media = [
            self.store.import_media(project["id"], b"image-bytes", "reference.png", "image/png"),
            self.store.import_media(project["id"], b"video-bytes", "reference.mp4", "video/mp4"),
            self.store.import_media(project["id"], b"audio-bytes", "reference.mp3", "audio/mpeg"),
        ]
        source_nodes = [
            {
                "id": "image-source",
                "type": "smart-image",
                "x": 40,
                "y": 40,
                "images": [{"asset_id": media[0]["asset_id"], "kind": "image", "name": "reference.png"}],
            },
            {
                "id": "video-source",
                "type": "smart-image",
                "x": 40,
                "y": 260,
                "images": [{"asset_id": media[1]["asset_id"], "kind": "video", "name": "reference.mp4"}],
            },
            {
                "id": "audio-source",
                "type": "smart-image",
                "x": 40,
                "y": 480,
                "images": [{"asset_id": media[2]["asset_id"], "kind": "audio", "name": "reference.mp3"}],
            },
        ]
        conversation = {
            "id": "conversation-1",
            "type": "smart-conversation",
            "x": 420,
            "y": 120,
            "w": 360,
            "h": 420,
            "title": "Conversation",
            "messages": [],
        }
        prompt = {
            "id": "prompt-1",
            "type": "smart-prompt",
            "x": 850,
            "y": 120,
            "text": "只保存创作指令，不保存聊天历史",
        }
        mutations = [{"op": "upsert_node", "node": node} for node in [*source_nodes, conversation, prompt]]
        if connected:
            mutations.extend(
                {
                    "op": "upsert_edge",
                    "edge": {"id": f"{source['id']}-conversation", "from": source["id"], "to": conversation["id"], "kind": "input"},
                }
                for source in source_nodes
            )
        self.store.apply_mutations(project["id"], mutations, base_revision=0)
        self.store.flush(project["id"])
        return project, media, conversation, prompt

    def _wait_for_task(self, endpoint, request_id, *, timeout=3.0):
        deadline = time.monotonic() + timeout
        response = None
        while time.monotonic() < deadline:
            response = self.client.get(f"{endpoint}/{request_id}")
            if response.status_code == 200 and response.json().get("status") in {"completed", "failed"}:
                return response
            time.sleep(0.02)
        self.fail(f"conversation task did not finish: {response.text if response is not None else 'no response'}")

    def test_project_conversation_persists_multiturn_text_and_connected_multimodal_references(self):
        project, media, conversation, prompt = self._seed_conversation_canvas()
        adapter_requests = []

        async def fake_adapter(request):
            adapter_requests.append(request)
            return {"text": f"假适配器回复第 {len(request['messages'])} 次", "model": "offline-fake"}

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            first = self.client.post(endpoint, json={"text": "请先分析这些参考素材", "provider": "fake", "request_id": "request-multiturn-1"})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["status"], "pending")
            self._wait_for_task(endpoint, "request-multiturn-1")
            second = self.client.post(endpoint, json={"text": "再给我一个可编辑的方向", "provider": "fake", "request_id": "request-multiturn-2"})
            self.assertEqual(second.status_code, 200, second.text)
            self._wait_for_task(endpoint, "request-multiturn-2")

        self.assertEqual(len(adapter_requests), 2)
        self.assertEqual(
            [(item["asset_id"], item["kind"]) for item in adapter_requests[0]["references"]],
            [(media[0]["asset_id"], "image"), (media[1]["asset_id"], "video"), (media[2]["asset_id"], "audio")],
        )
        self.assertEqual([item["text"] for item in adapter_requests[1]["messages"]], [
            "请先分析这些参考素材",
            "假适配器回复第 1 次",
            "再给我一个可编辑的方向",
        ])

        opened = self.client.get(f"/api/project-library/{project['id']}").json()["canvas"]
        conversation_node = next(node for node in opened["nodes"] if node["id"] == conversation["id"])
        self.assertEqual([message["role"] for message in conversation_node["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(conversation_node["conversation_adapter"]["provider"], "fake")
        self.assertFalse(any("adapter" in message for message in conversation_node["messages"]))
        self.assertEqual(conversation_node["messages"][0]["text"], "请先分析这些参考素材")
        self.assertEqual(conversation_node["messages"][0]["references"], [
            {"asset_id": media[0]["asset_id"], "kind": "image", "name": "reference.png"},
            {"asset_id": media[1]["asset_id"], "kind": "video", "name": "reference.mp4"},
            {"asset_id": media[2]["asset_id"], "kind": "audio", "name": "reference.mp3"},
        ])
        prompt_node = next(node for node in opened["nodes"] if node["id"] == prompt["id"])
        self.assertEqual(prompt_node, prompt)

        persisted = json.loads((self.store.root / project["id"] / "canvas.json").read_text(encoding="utf-8"))
        encoded = json.dumps(persisted, ensure_ascii=False)
        self.assertNotIn("/api/project-library/", encoded)
        self.assertNotIn("data:", encoded)
        self.assertNotIn("base64", encoded.lower())

    def test_conversation_adapter_fields_are_bounded_and_node_level_for_500_node_canvas(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        for field, value in (
            ("provider", "p" * 81),
            ("model", "m" * 201),
            ("ms_model", "s" * 201),
            ("system_prompt", "x" * 12001),
        ):
            response = self.client.post(endpoint, json={"text": "bounded", "request_id": f"bound-{field}", field: value})
            self.assertEqual(response.status_code, 422, (field, response.text))

        nodes = [
            {
                "id": f"capacity-conversation-{index}", "type": "smart-conversation",
                "x": index * 4, "y": index * 2, "messages": [],
                "conversation_adapter": {"provider": "comfly", "model": "", "ms_model": "", "system_prompt": ""},
            }
            for index in range(500)
        ]
        capacity_project = self.store.create_project("500 conversation nodes")
        self.store.apply_mutations(
            capacity_project["id"],
            [{"op": "upsert_node", "node": node} for node in nodes],
            base_revision=0,
        )
        self.store.flush(capacity_project["id"])
        persisted = (self.store.root / capacity_project["id"] / "canvas.json").read_bytes()
        self.assertEqual(len(self.store.canvas(capacity_project["id"])["nodes"]), 500)
        self.assertLess(len(persisted), 512 * 1024)

    def test_legacy_message_level_adapter_remains_compatible(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "legacy-adapter"
        user = {
            "id": "legacy-user", "role": "user", "status": "pending", "request_id": request_id,
            "text": "旧消息", "references": [], "adapter": {
                "provider": "legacy-provider", "model": "legacy-model",
                "ms_model": "legacy-ms", "system_prompt": "legacy-system",
            },
        }
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        captured = []
        async def fake_adapter(request):
            captured.append(request)
            return {"text": "兼容回复"}
        self.assertTrue(asyncio.run(main.run_project_conversation_task(project["id"], conversation["id"], request_id, adapter=fake_adapter)))
        self.assertEqual(captured[0]["provider"], "legacy-provider")
        self.assertEqual(captured[0]["model"], "legacy-model")
        self.assertEqual(captured[0]["ms_model"], "legacy-ms")
        self.assertEqual(captured[0]["system_prompt"], "legacy-system")

    def test_conversation_only_uses_directly_connected_project_media_and_ignores_injected_foreign_refs(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        other_project = self.store.create_project("另一个项目")
        other_media = self.store.import_media(other_project["id"], b"foreign", "foreign.png", "image/png")
        adapter_requests = []

        async def fake_adapter(request):
            adapter_requests.append(request)
            return {"text": "没有隐式参考", "model": "offline-fake"}

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            response = self.client.post(endpoint, json={
                "text": "只看当前消息",
                "references": [{"asset_id": other_media["asset_id"], "kind": "image"}],
                "request_id": "request-foreign-reference",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self._wait_for_task(endpoint, "request-foreign-reference")
        self.assertEqual(adapter_requests[0]["references"], [])
        self.assertEqual(adapter_requests[0]["messages"][0]["references"], [])

    def test_conversation_ignores_media_reached_through_a_group(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        group = {
            "id": "media-group",
            "type": "smart-group",
            "x": 220,
            "y": 120,
            "w": 320,
            "h": 240,
            "items": ["image-source"],
        }
        current_revision = self.store.canvas(project["id"])["revision"]
        self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": group},
                {"op": "upsert_edge", "edge": {"id": "group-conversation", "from": group["id"], "to": conversation["id"], "kind": "input"}},
            ],
            base_revision=current_revision,
        )
        self.store.flush(project["id"])
        adapter_requests = []

        async def fake_adapter(request):
            adapter_requests.append(request)
            return {"text": "只接受直连素材", "model": "offline-fake"}

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            response = self.client.post(endpoint, json={"text": "不要读取分组成员", "request_id": "request-group-reference"})
        self.assertEqual(response.status_code, 200, response.text)
        self._wait_for_task(endpoint, "request-group-reference")
        self.assertEqual(adapter_requests[0]["references"], [])

    def test_project_conversation_adapter_does_not_duplicate_current_message(self):
        project, media, conversation, _prompt = self._seed_conversation_canvas()
        captured = []

        async def fake_canvas_llm(payload):
            captured.append(payload)
            return {"text": "offline adapter", "model": "offline-fake", "raw_usage": None}

        request = {
            "project_id": project["id"],
            "node_id": conversation["id"],
            "message": "当前问题",
            "current_message_id": "current",
            "messages": [
                {"id": "old-user", "role": "user", "text": "上一轮问题"},
                {"id": "old-assistant", "role": "assistant", "text": "上一轮回答"},
                {"id": "current", "role": "user", "text": "当前问题"},
            ],
            "references": [{"asset_id": media[2]["asset_id"], "kind": "audio", "name": "旁白.mp3"}],
            "provider": "fake",
        }
        with patch.object(main, "canvas_llm", side_effect=fake_canvas_llm):
            result = asyncio.run(main.project_conversation_adapter(request))

        self.assertEqual(result["text"], "offline adapter")
        self.assertEqual([item["content"] for item in captured[0].messages], ["上一轮问题", "上一轮回答"])
        self.assertIn("当前问题", captured[0].message)
        self.assertNotIn("旁白.mp3", captured[0].message)
        self.assertTrue(captured[0].audios[0].startswith("data:audio/mpeg;base64,"))
        self.assertEqual(captured[0].audio_references[0]["asset_id"], media[2]["asset_id"])
        self.assertTrue(captured[0].audio_references[0]["url"].startswith("data:audio/mpeg;base64,"))

    def test_default_canvas_llm_adapter_reports_unsupported_audio(self):
        audio_data = "data:audio/mpeg;base64," + base64.b64encode(b"unsupported-audio").decode("ascii")
        payload = main.CanvasLLMRequest(
            message="听取参考音频",
            provider="offline-default",
            audios=[audio_data],
            audio_references=[{
                "asset_id": "audio-1",
                "kind": "audio",
                "name": "旁白.mp3",
                "url": audio_data,
            }],
        )
        with patch.object(main, "get_api_provider", return_value={"id": "offline-default"}):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.canvas_llm(payload))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail["code"], "unsupported_audio_input")

    def test_audio_capable_canvas_adapter_receives_asset_id_content_part(self):
        captured = {}

        class FakeResponse:
            content = b'{"choices":[{"message":{"content":"audio heard"}}]}'

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "已听取音频"}}]}

        class FakeAsyncClient:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

            async def post(self, _url, *, headers, json):
                captured.update({"headers": headers, "json": json})
                return FakeResponse()

        audio_data = "data:audio/mpeg;base64," + base64.b64encode(b"audio-payload").decode("ascii")
        payload = main.CanvasLLMRequest(
            message="请听取参考音频",
            provider="audio-capable",
            audio_references=[{
                "asset_id": "audio-42",
                "kind": "audio",
                "name": "旁白.mp3",
                "url": audio_data,
            }],
        )
        with patch.object(main, "get_api_provider", return_value={"id": "audio-capable", "chat_audio_input": True}), \
             patch.object(main, "resolve_chat_provider", return_value=("https://example.invalid/v1", {}, "audio-model")), \
             patch.object(main.httpx, "AsyncClient", FakeAsyncClient):
            result = asyncio.run(main.canvas_llm(payload))

        self.assertEqual(result["text"], "已听取音频")
        parts = captured["json"]["messages"][-1]["content"]
        audio_part = next(part for part in parts if part.get("type") == "audio_reference")
        self.assertEqual(audio_part["audio_reference"]["asset_id"], "audio-42")
        self.assertEqual(audio_part["audio_reference"]["url"], audio_data)

    def test_public_canvas_llm_rejects_internal_project_media_handles(self):
        payload = main.CanvasLLMRequest(message="不得泄漏", provider="fake", images=["project-media://project-1/image-1"])
        with self.assertRaises(main.HTTPException) as raised:
            asyncio.run(main.canvas_llm(payload))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail["code"], "project_media_handle_requires_project_task")

    def test_project_media_handles_are_resolved_before_fake_http_provider_boundary(self):
        project, media, conversation, _prompt = self._seed_conversation_canvas()
        captured = {}

        class FakeResponse:
            content = b'{"choices":[{"message":{"content":"media readable"}}]}'
            def raise_for_status(self): return None
            def json(self): return {"choices": [{"message": {"content": "素材可读"}}]}

        class FakeAsyncClient:
            def __init__(self, *_args, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def post(self, _url, *, headers, json):
                captured["json"] = json
                return FakeResponse()

        request = {
            "project_id": project["id"], "node_id": conversation["id"],
            "request_id": "media-boundary", "message": "读取三种素材",
            "messages": [{"id": "current", "role": "user", "text": "读取三种素材"}],
            "current_message_id": "current", "provider": "media-capable",
            "references": [
                {"asset_id": media[0]["asset_id"], "kind": "image", "name": "reference.png"},
                {"asset_id": media[1]["asset_id"], "kind": "video", "name": "reference.mp4"},
                {"asset_id": media[2]["asset_id"], "kind": "audio", "name": "reference.mp3"},
            ],
        }
        with patch.object(main, "get_api_provider", return_value={"id": "media-capable", "chat_audio_input": True}), \
             patch.object(main, "resolve_chat_provider", return_value=("https://example.invalid/v1", {}, "media-model")), \
             patch.object(main.httpx, "AsyncClient", FakeAsyncClient):
            result = asyncio.run(main.project_conversation_adapter(request))

        self.assertEqual(result["text"], "素材可读")
        encoded = json.dumps(captured["json"], ensure_ascii=False)
        self.assertNotIn("project-media://", encoded)
        self.assertIn("data:", encoded)
        parts = captured["json"]["messages"][-1]["content"]
        image_url = next(part["image_url"]["url"] for part in parts if part.get("type") == "image_url")
        self.assertEqual(base64.b64decode(image_url.split(",", 1)[1]), b"image-bytes")
        video_url = next(part["video_url"]["url"] for part in parts if part.get("type") == "video_url")
        self.assertEqual(base64.b64decode(video_url.split(",", 1)[1]), b"video-bytes")
        audio = next(part for part in parts if part.get("type") == "audio_reference")
        self.assertTrue(audio["audio_reference"]["url"].startswith("data:audio/mpeg;base64,"))
        self.assertEqual(base64.b64decode(audio["audio_reference"]["url"].split(",", 1)[1]), b"audio-bytes")
        canvas_encoded = json.dumps(self.store.canvas(project["id"]), ensure_ascii=False)
        self.assertNotIn("data:", canvas_encoded)
        self.assertNotIn("base64", canvas_encoded.lower())

    def test_conversation_media_count_budget_rejects_whole_request_before_adapter_call(self):
        project, media, conversation, _prompt = self._seed_conversation_canvas()
        second_image = self.store.import_media(project["id"], b"second-image", "second.png", "image/png")
        calls = []
        async def must_not_call(payload): calls.append(payload)
        request = {
            "project_id": project["id"], "node_id": conversation["id"], "request_id": "media-count",
            "message": "超量", "messages": [], "provider": "fake",
            "references": [
                {"asset_id": media[0]["asset_id"], "kind": "image"},
                {"asset_id": second_image["asset_id"], "kind": "image"},
            ],
        }
        with patch.object(main, "PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES", 1), \
             patch.object(main, "canvas_llm", side_effect=must_not_call):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.project_conversation_adapter(request))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail["code"], "conversation_media_count_exceeded")
        self.assertEqual(calls, [])

    def test_conversation_media_byte_budgets_reject_before_base64_and_bound_four_workers(self):
        project, media, conversation, _prompt = self._seed_conversation_canvas()
        second_image = self.store.import_media(project["id"], b"second-image", "second.png", "image/png")
        request = {
            "project_id": project["id"], "node_id": conversation["id"], "request_id": "media-bytes",
            "message": "超量", "messages": [], "provider": "fake",
            "references": [{"asset_id": media[0]["asset_id"], "kind": "image"}],
        }
        encoded = []
        with patch.object(main, "PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES", 5), \
             patch.object(main, "reference_to_data_url", side_effect=lambda *args, **kwargs: encoded.append(args)):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.project_conversation_adapter(request))
        self.assertEqual(raised.exception.detail["code"], "conversation_media_file_too_large")
        self.assertEqual(encoded, [])

        request["references"].append({"asset_id": second_image["asset_id"], "kind": "image"})
        with patch.object(main, "PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES", 100), \
             patch.object(main, "PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES", 15), \
             patch.object(main, "reference_to_data_url", side_effect=lambda *args, **kwargs: encoded.append(args)):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.project_conversation_adapter(request))
        self.assertEqual(raised.exception.detail["code"], "conversation_media_total_too_large")
        self.assertEqual(encoded, [])
        self.assertLessEqual(
            main.PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES * main.ProjectConversationTaskRunner.WORKER_COUNT,
            128 * 1024 * 1024,
        )

    def test_conversation_image_dimension_budget_rejects_before_pixel_load(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        one_pixel_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        image = self.store.import_media(project["id"], one_pixel_png, "pixel.png", "image/png")
        request = {
            "project_id": project["id"], "node_id": conversation["id"], "request_id": "media-pixels",
            "message": "像素上限", "messages": [], "provider": "fake",
            "references": [{"asset_id": image["asset_id"], "kind": "image"}],
        }
        calls = []
        with patch.object(main, "PROJECT_CONVERSATION_IMAGE_MAX_PIXELS", 0), \
             patch.object(main, "canvas_llm", side_effect=lambda payload: calls.append(payload)):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.project_conversation_adapter(request))
        self.assertEqual(raised.exception.detail["code"], "conversation_image_dimensions_exceeded")
        self.assertEqual(calls, [])

    def test_pending_request_survives_concurrent_canvas_edit_and_is_idempotent(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        adapter_started = threading.Event()
        release_adapter = threading.Event()
        adapter_requests = []

        async def fake_adapter(request):
            adapter_requests.append(request)
            adapter_started.set()
            await asyncio.to_thread(release_adapter.wait, 3)
            return {"text": "并发编辑后仍完成", "model": "offline-fake"}

        request_id = "request-concurrent-1"
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        status_endpoint = f"{endpoint}/{request_id}"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            first = self.client.post(endpoint, json={"text": "先持久化再调用", "request_id": request_id})
            self.assertIn(first.status_code, {200, 202}, first.text)
            self.assertEqual(first.json()["status"], "pending")
            self.assertTrue(adapter_started.wait(2))

            latest = self.client.get(f"/api/project-library/{project['id']}").json()["canvas"]
            unrelated = {
                "id": "edited-during-conversation",
                "type": "smart-prompt",
                "x": 1000,
                "y": 1000,
                "w": 220,
                "h": 120,
                "text": "并发编辑保留",
            }
            edit = self.client.patch(
                f"/api/project-library/{project['id']}/canvas",
                json={"mutations": [{"op": "upsert_node", "node": unrelated}], "base_revision": latest["revision"]},
            )
            self.assertEqual(edit.status_code, 200, edit.text)
            release_adapter.set()

            completed = None
            for _ in range(100):
                completed = self.client.get(status_endpoint)
                if completed.status_code == 200 and completed.json().get("status") == "completed":
                    break
                time.sleep(0.02)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.json().get("status"), "completed", completed.text)

            duplicate = self.client.post(endpoint, json={"text": "先持久化再调用", "request_id": request_id})
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertEqual(duplicate.json().get("status"), "completed")

        self.assertEqual(len(adapter_requests), 1)
        opened = self.client.get(f"/api/project-library/{project['id']}").json()["canvas"]
        node = next(item for item in opened["nodes"] if item["id"] == conversation["id"])
        self.assertIn(unrelated, opened["nodes"])
        self.assertEqual([item.get("request_id") for item in node["messages"] if item.get("request_id") == request_id].count(request_id), 2)
        self.assertTrue(any(item.get("status") == "completed" for item in node["messages"] if item.get("request_id") == request_id))

    def test_adapter_ready_result_survives_more_than_eight_merge_conflicts_without_rebilling(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "request-ready-after-conflicts"
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        adapter_calls = []
        conflicts = 0
        adapter_ready = threading.Event()
        original_project_store_call = main.project_store_call

        async def fake_adapter(request):
            adapter_calls.append(request)
            adapter_ready.set()
            return {"text": "只计费一次的回复", "model": "offline-fake"}

        def conflict_after_adapter(method, *args, **kwargs):
            nonlocal conflicts
            if method == "apply_mutations" and adapter_ready.is_set() and conflicts < 9:
                conflicts += 1
                project_id = args[0] if args else kwargs.get("project_id")
                raise main.RevisionConflict(self.store.canvas(project_id))
            return original_project_store_call(method, *args, **kwargs)

        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter), \
             patch.object(main, "project_store_call", side_effect=conflict_after_adapter):
            started = self.client.post(endpoint, json={"text": "冲突后继续合并", "request_id": request_id})
            self.assertEqual(started.status_code, 200, started.text)
            deadline = time.monotonic() + 5
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = main.conversation_task_snapshot(self.store.canvas(project["id"]), conversation["id"], request_id)
                if snapshot and snapshot["status"] == "completed":
                    break
                time.sleep(0.02)

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["assistant"]["text"], "只计费一次的回复")
        self.assertEqual(conflicts, 9)
        self.assertEqual(len(adapter_calls), 1)

    def test_merge_conflict_exhaustion_keeps_ready_sidecar_for_retry_without_rebilling(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "request-merge-conflict-exhausted"
        user = {
            "id": "merge-conflict-user",
            "role": "user",
            "status": "pending",
            "request_id": request_id,
            "text": "冲突耗尽仍保留恢复记录",
            "references": [],
        }
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        calls = []

        async def fake_adapter(_request):
            calls.append(True)
            return {"text": "只调用一次"}

        with patch.object(main, "merge_project_conversation_result", return_value=None):
            completed = asyncio.run(main.run_project_conversation_task(
                project["id"], conversation["id"], request_id, adapter=fake_adapter,
            ))
        self.assertFalse(completed)
        self.assertEqual(len(calls), 1)
        sidecar = main.project_conversation_durable_path(project["id"], conversation["id"], request_id)
        self.assertTrue(sidecar.exists())
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "ready")
        self.assertEqual(payload["ready"]["assistant"]["text"], "只调用一次")

    def test_same_node_is_fifo_and_second_adapter_history_contains_first_assistant(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        first_started = threading.Event()
        release_first = threading.Event()
        requests = []

        async def fake_adapter(request):
            requests.append(request)
            if request["request_id"] == "fifo-1":
                first_started.set()
                await asyncio.to_thread(release_first.wait, 3)
                return {"text": "第一轮回答"}
            return {"text": "第二轮回答"}

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            self.assertEqual(self.client.post(endpoint, json={"text": "第一轮", "request_id": "fifo-1"}).status_code, 200)
            self.assertTrue(first_started.wait(2))
            self.assertEqual(self.client.post(endpoint, json={"text": "第二轮", "request_id": "fifo-2"}).status_code, 200)
            time.sleep(0.1)
            self.assertEqual([item["request_id"] for item in requests], ["fifo-1"])
            release_first.set()
            self._wait_for_task(endpoint, "fifo-1", timeout=5)
            self._wait_for_task(endpoint, "fifo-2", timeout=5)

        self.assertEqual([item["request_id"] for item in requests], ["fifo-1", "fifo-2"])
        self.assertEqual([item["text"] for item in requests[1]["messages"]], ["第一轮", "第一轮回答", "第二轮"])

    def test_polling_later_pending_after_restart_schedules_earlier_fifo_turn(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        for request_id, message_id, text in (("restart-fifo-1", "restart-fifo-user-1", "先前轮"), ("restart-fifo-2", "restart-fifo-user-2", "后续轮")):
            asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], {
                "id": message_id, "role": "user", "status": "pending", "request_id": request_id,
                "text": text, "references": [], "adapter": {"provider": "fake"},
            }))
        calls = []
        async def fake_adapter(request):
            calls.append(request)
            return {"text": "先前回答" if request["request_id"].endswith("1") else "后续回答"}
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            polled = self.client.get(f"{endpoint}/restart-fifo-2")
            self.assertEqual(polled.status_code, 200)
            deadline = time.monotonic() + 5
            second = None
            while time.monotonic() < deadline:
                second = main.conversation_task_snapshot(self.store.canvas(project["id"]), conversation["id"], "restart-fifo-2")
                if second and second["status"] == "completed": break
                time.sleep(0.02)
        self.assertEqual([item["request_id"] for item in calls], ["restart-fifo-1", "restart-fifo-2"])
        self.assertEqual([item["text"] for item in calls[1]["messages"]], ["先前轮", "先前回答", "后续轮"])

    def test_restart_recovery_does_not_fill_node_cap_with_later_fifo_turns(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_ids = [f"restart-cap-{index}" for index in range(1, 6)]
        for index, request_id in enumerate(request_ids, 1):
            asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], {
                "id": f"restart-cap-user-{index}",
                "role": "user",
                "status": "pending",
                "request_id": request_id,
                "text": f"重启恢复第{index}轮",
                "references": [],
                "adapter": {"provider": "fake"},
            }))

        runner = main.PROJECT_CONVERSATION_RUNNER
        node_key = (project["id"], conversation["id"])

        async def hold_node_gate():
            gate = asyncio.Lock()
            await gate.acquire()
            runner.node_locks[node_key] = gate
            return gate

        gate = asyncio.run_coroutine_threadsafe(hold_node_gate(), runner.loop).result(timeout=2)
        calls = []

        async def fake_adapter(request):
            calls.append(request["request_id"])
            return {"text": f"{request['request_id']}完成"}

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        try:
            with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
                for request_id in request_ids[1:]:
                    response = self.client.get(f"{endpoint}/{request_id}")
                    self.assertEqual(response.status_code, 200, response.text)
                runner.loop.call_soon_threadsafe(gate.release)
                deadline = time.monotonic() + 8
                statuses = {}
                while time.monotonic() < deadline:
                    statuses = {
                        request_id: self.client.get(f"{endpoint}/{request_id}").json().get("status")
                        for request_id in request_ids
                    }
                    if all(status in {"completed", "failed"} for status in statuses.values()):
                        break
                    time.sleep(0.02)
        finally:
            if gate.locked():
                runner.loop.call_soon_threadsafe(gate.release)

        self.assertTrue(all(status == "completed" for status in statuses.values()), {"statuses": statuses, "calls": calls})
        self.assertEqual(calls, request_ids)

    def test_different_conversation_nodes_can_run_in_parallel(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        other = {**conversation, "id": "conversation-2", "messages": [], "x": 900}
        revision = self.store.canvas(project["id"])["revision"]
        self.store.apply_mutations(project["id"], [{"op": "upsert_node", "node": other}], base_revision=revision)
        self.store.flush(project["id"])
        both_started = threading.Event()
        started = set()
        release = threading.Event()

        async def fake_adapter(request):
            started.add(request["node_id"])
            if len(started) == 2: both_started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "并行完成"}

        first_endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        second_endpoint = f"/api/project-library/{project['id']}/conversation/{other['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            self.client.post(first_endpoint, json={"text": "节点一", "request_id": "parallel-1"})
            self.client.post(second_endpoint, json={"text": "节点二", "request_id": "parallel-2"})
            self.assertTrue(both_started.wait(2), started)
            release.set()
            self._wait_for_task(first_endpoint, "parallel-1", timeout=5)
            self._wait_for_task(second_endpoint, "parallel-2", timeout=5)

    def test_ready_record_survives_simulated_restart_and_merges_without_adapter_recall(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "restart-ready"
        user = {"id": "restart-user", "role": "user", "status": "pending", "request_id": request_id, "text": "重启前请求", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        ready = {"user_message_id": "restart-user", "assistant": {"id": "restart-assistant", "role": "assistant", "status": "completed", "request_id": request_id, "text": "重启前已付费结果", "references": [], "created_at": 1}}
        main.save_project_conversation_durable(project["id"], conversation["id"], request_id, {"state": "ready", "ready": ready})
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        calls = []

        async def must_not_call(request):
            calls.append(request)
            raise AssertionError("adapter must not be called after restart")

        self.assertTrue(asyncio.run(main.run_project_conversation_task(project["id"], conversation["id"], request_id, adapter=must_not_call)))
        self.assertEqual(calls, [])
        snapshot = main.conversation_task_snapshot(self.store.canvas(project["id"]), conversation["id"], request_id)
        self.assertEqual(snapshot["assistant"]["text"], "重启前已付费结果")
        self.assertFalse(main.project_conversation_durable_path(project["id"], conversation["id"], request_id).exists())

    def test_ready_record_rebinds_to_new_pending_user_after_old_message_was_removed(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "restart-ready-rebind"
        old_user = {"id": "old-user", "role": "user", "status": "pending", "request_id": request_id, "text": "旧请求", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], old_user))
        assistant = {"id": "ready-assistant", "role": "assistant", "status": "completed", "request_id": request_id, "text": "已付费结果", "references": [], "created_at": 1}
        main.save_project_conversation_durable(project["id"], conversation["id"], request_id, {
            "state": "ready", "ready": {"user_message_id": old_user["id"], "assistant": assistant},
        })
        current = self.store.canvas(project["id"])
        node = main.conversation_node_by_id(current, conversation["id"])
        replacement = {**node, "messages": [item for item in node["messages"] if item.get("id") != old_user["id"]]}
        self.store.apply_mutations(project["id"], [{"op": "upsert_node", "node": replacement}], base_revision=current["revision"])
        self.store.flush(project["id"])
        new_user = {"id": "new-user", "role": "user", "status": "pending", "request_id": request_id, "text": "重试请求", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], new_user))
        main.PROJECT_CONVERSATION_TASKS.clear()
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        calls = []

        async def must_not_call(request):
            calls.append(request)
            raise AssertionError("ready result should be rebound without adapter recall")

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        started = self.client.get(f"{endpoint}/{request_id}")
        self.assertEqual(started.status_code, 200, started.text)
        with patch.object(main, "project_conversation_adapter", side_effect=must_not_call):
            snapshot_response = self._wait_for_task(endpoint, request_id)
        self.assertEqual(calls, [])
        snapshot = snapshot_response.json()
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["message"]["id"], "new-user")
        self.assertEqual(snapshot["assistant"]["id"], "ready-assistant")
        self.assertFalse(main.project_conversation_durable_path(project["id"], conversation["id"], request_id).exists())

    def test_dispatching_record_after_restart_fails_uncertain_without_rebilling(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "restart-uncertain"
        user = {"id": "uncertain-user", "role": "user", "status": "pending", "request_id": request_id, "text": "可能已计费", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        main.save_project_conversation_durable(project["id"], conversation["id"], request_id, {"state": "dispatching", "user_message_id": "uncertain-user"})
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        calls = []

        async def must_not_call(request): calls.append(request)
        self.assertTrue(asyncio.run(main.run_project_conversation_task(project["id"], conversation["id"], request_id, adapter=must_not_call)))
        self.assertEqual(calls, [])
        snapshot = main.conversation_task_snapshot(self.store.canvas(project["id"]), conversation["id"], request_id)
        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("不会自动重试", snapshot["assistant"]["text"])
        self.assertFalse(main.project_conversation_durable_path(project["id"], conversation["id"], request_id).exists())

    def test_fly_export_excludes_local_conversation_recovery_sidecars(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        main.save_project_conversation_durable(
            project["id"], conversation["id"], "export-active",
            {"state": "dispatching", "user_message_id": "user-active"},
        )
        package = Path(self.temp_dir.name) / "conversation.fly"
        self.store.export_fly_to(project["id"], package)
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
        self.assertFalse(any("conversation-tasks" in name for name in names), names)
        imported = self.store.import_fly(package.read_bytes())
        imported_root = self.store.root / imported["id"]
        self.assertFalse((imported_root / ".conversation-tasks").exists())

    def test_conversation_sidecar_rejects_reparse_directory_without_external_write(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        project_root = self.store.root / project["id"]
        external = Path(self.temp_dir.name) / "external-sidecars"
        external.mkdir()
        redirect = project_root / ".conversation-tasks"
        try:
            redirect.symlink_to(external, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Windows symlink creation unavailable: {exc}")
        with self.assertRaises(main.ProjectStoreError):
            main.save_project_conversation_durable(
                project["id"], conversation["id"], "reparse-write",
                {"state": "ready", "ready": {"assistant": {"text": "不得外写"}}},
            )
        self.assertEqual(list(external.iterdir()), [])

    def test_atomic_sidecar_tmp_is_ignored_and_cleaned_for_lifecycle_checks(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        sidecar_root = self.store.root / project["id"] / ".conversation-tasks"
        sidecar_root.mkdir()
        temporary = sidecar_root / "partial-write.abc123.tmp"
        temporary.write_text('{"state":"ready"', encoding="utf-8")
        exported = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertFalse(temporary.exists())

    def test_malformed_official_sidecar_conservatively_blocks_lifecycle(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "malformed-official-sidecar"
        main.save_project_conversation_durable(
            project["id"], conversation["id"], request_id,
            {"state": "ready", "ready": {"assistant": {"id": "a", "text": "paid"}}},
        )
        sidecar = main.project_conversation_durable_path(project["id"], conversation["id"], request_id)
        sidecar.write_text("{", encoding="utf-8")
        blocked = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertTrue(sidecar.exists())
        sidecar.unlink()
        allowed = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_active_conversation_blocks_export_and_project_trash_until_terminal(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        started = threading.Event(); release = threading.Event()
        async def blocking_adapter(_request):
            started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "终态回复"}
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=blocking_adapter):
            self.client.post(endpoint, json={"text": "生命周期阻断", "request_id": "lifecycle-active"})
            self.assertTrue(started.wait(2))
            exported = self.client.get(f"/api/project-library/{project['id']}/export")
            trashed = self.client.delete(f"/api/project-library/{project['id']}")
            self.assertEqual(exported.status_code, 409, exported.text)
            self.assertEqual(trashed.status_code, 409, trashed.text)
            release.set()
            self._wait_for_task(endpoint, "lifecycle-active")
        exported = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        trashed = self.client.delete(f"/api/project-library/{project['id']}")
        self.assertEqual(trashed.status_code, 200, trashed.text)

    def test_terminal_status_visibility_synchronously_cleans_recovery_sidecar(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        request_id = "terminal-cleanup-race"
        cleanup_started = threading.Event()
        release_worker_cleanup = threading.Event()
        cleanup_calls = 0
        cleanup_lock = threading.Lock()
        original_cleanup = main.forget_project_conversation_durable

        def delay_first_worker_cleanup(*args):
            nonlocal cleanup_calls
            with cleanup_lock:
                cleanup_calls += 1
                call_number = cleanup_calls
            if call_number == 1:
                cleanup_started.set()
                release_worker_cleanup.wait(3)
            return original_cleanup(*args)

        async def fake_adapter(_request):
            return {"text": "终态已写入"}

        try:
            with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter), \
                 patch.object(main, "forget_project_conversation_durable", side_effect=delay_first_worker_cleanup):
                started = self.client.post(endpoint, json={"text": "制造清理窗口", "request_id": request_id})
                self.assertEqual(started.status_code, 200, started.text)
                self.assertTrue(cleanup_started.wait(2))
                terminal = self.client.get(f"{endpoint}/{request_id}")
                self.assertEqual(terminal.status_code, 200, terminal.text)
                self.assertEqual(terminal.json()["status"], "completed")
                exported = self.client.get(f"/api/project-library/{project['id']}/export")
                self.assertEqual(exported.status_code, 200, exported.text)
        finally:
            release_worker_cleanup.set()

    def test_terminal_canvas_cleans_matching_stale_ready_sidecar_without_status_poll(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "stale-ready-after-terminal-canvas"
        user = {
            "id": "stale-ready-user",
            "role": "user",
            "status": "pending",
            "request_id": request_id,
            "text": "终态已落盘但sidecar未删",
            "references": [],
        }
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        assistant = {
            "id": "stale-ready-assistant",
            "role": "assistant",
            "status": "completed",
            "request_id": request_id,
            "text": "终态回复",
            "references": [],
            "created_at": 1,
        }
        ready = {"user_message_id": user["id"], "assistant": assistant}
        main.save_project_conversation_durable(
            project["id"], conversation["id"], request_id,
            {"state": "ready", "ready": ready},
        )
        merged = asyncio.run(main.merge_project_conversation_result(
            project["id"], conversation["id"], request_id,
            user_message_id=user["id"],
            assistant=assistant,
        ))
        self.assertEqual(merged["status"], "completed")
        sidecar = main.project_conversation_durable_path(project["id"], conversation["id"], request_id)
        self.assertTrue(sidecar.is_file())
        main.PROJECT_CONVERSATION_TASKS.clear()
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()

        exported = self.client.get(f"/api/project-library/{project['id']}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertFalse(sidecar.exists())
        trashed = self.client.delete(f"/api/project-library/{project['id']}")
        self.assertEqual(trashed.status_code, 200, trashed.text)

    def test_restart_ready_sidecar_blocks_export_trash_and_purge_until_merged(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "lifecycle-restart-ready"
        user = {"id": "lifecycle-user", "role": "user", "status": "pending", "request_id": request_id, "text": "已付费待恢复", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        ready = {"user_message_id": "lifecycle-user", "assistant": {"id": "lifecycle-assistant", "role": "assistant", "status": "completed", "request_id": request_id, "text": "已付费结果", "references": [], "created_at": 1}}
        main.save_project_conversation_durable(project["id"], conversation["id"], request_id, {"state": "ready", "ready": ready})
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/export").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/project-library/{project['id']}").status_code, 409)

        # Simulate a legacy/direct move that bypassed the API guard; purge must still preserve recovery state.
        self.store.trash_project(project["id"])
        self.assertEqual(self.client.delete(f"/api/project-library/trash/{project['id']}").status_code, 409)
        restored = self.client.post(f"/api/project-library/trash/{project['id']}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        calls = []
        async def must_not_call(request): calls.append(request)
        self.assertTrue(asyncio.run(main.run_project_conversation_task(project["id"], conversation["id"], request_id, adapter=must_not_call)))
        self.assertEqual(calls, [])
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/export").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/project-library/{project['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/project-library/trash/{project['id']}").status_code, 200)

    def test_persisted_canvas_pending_without_registry_or_sidecar_blocks_lifecycle(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        request_id = "canvas-only-pending"
        user = {"id": "canvas-only-user", "role": "user", "status": "pending", "request_id": request_id, "text": "待恢复", "references": []}
        asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], user))
        main.PROJECT_CONVERSATION_TASKS.clear()
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        self.assertFalse(main.project_conversation_durable_path(project["id"], conversation["id"], request_id).exists())
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/export").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/project-library/{project['id']}").status_code, 409)
        asyncio.run(main.merge_project_conversation_result(
            project["id"], conversation["id"], request_id,
            user_message_id="canvas-only-user", error=RuntimeError("明确失败终态"),
        ))
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/export").status_code, 200)

    def test_node_queue_cap_rejects_without_calling_adapter(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        started = threading.Event()
        release = threading.Event()
        calls = []
        async def fake_adapter(request):
            calls.append(request)
            started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "完成"}
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "PROJECT_CONVERSATION_MAX_PER_NODE", 1), patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            self.assertEqual(self.client.post(endpoint, json={"text": "占满", "request_id": "cap-1"}).status_code, 200)
            self.assertTrue(started.wait(2))
            rejected = self.client.post(endpoint, json={"text": "拒绝", "request_id": "cap-2"})
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertEqual(rejected.json()["detail"]["code"], "conversation_queue_node_full")
            release.set()
            self._wait_for_task(endpoint, "cap-1")
        self.assertEqual(len(calls), 1)

    def test_admission_counts_persisted_pending_after_restart_before_accepting_new_slot(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        for index in range(1, 5):
            asyncio.run(main.persist_project_conversation_pending(project["id"], conversation["id"], {
                "id": f"persisted-cap-user-{index}",
                "role": "user",
                "status": "pending",
                "request_id": f"persisted-cap-{index}",
                "text": f"持久化占位{index}",
                "references": [],
            }))
        # A restart loses the in-memory task and ready registries, but must not
        # make the four canvas-persisted pending turns invisible to admission.
        main.PROJECT_CONVERSATION_TASKS.clear()
        main.PROJECT_CONVERSATION_READY_RESULTS.clear()
        calls = []

        async def must_not_call(request):
            calls.append(request)
            raise AssertionError("the fifth pending turn must be rejected before adapter dispatch")

        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=must_not_call):
            fifth = self.client.post(endpoint, json={"text": "超出持久化节点上限", "request_id": "persisted-cap-5"})
        self.assertEqual(fifth.status_code, 429, fifth.text)
        self.assertEqual(fifth.json()["detail"]["code"], "conversation_queue_node_full")
        self.assertEqual(calls, [])
        pending = [
            item for item in main.conversation_node_by_id(self.store.canvas(project["id"]), conversation["id"])["messages"]
            if item.get("role") == "user" and item.get("status") == "pending"
        ]
        self.assertEqual(len(pending), 4)

    def test_global_admission_lock_serializes_cross_project_posts(self):
        first_project, _m1, first_conversation, _p1 = self._seed_conversation_canvas(connected=False)
        second_project, _m2, second_conversation, _p2 = self._seed_conversation_canvas(connected=False)
        first_endpoint = f"/api/project-library/{first_project['id']}/conversation/{first_conversation['id']}/messages"
        second_endpoint = f"/api/project-library/{second_project['id']}/conversation/{second_conversation['id']}/messages"
        barrier = threading.Barrier(2)
        initial_scans = 0
        scan_lock = threading.Lock()
        original_pending_keys = main.conversation_pending_task_keys

        def synchronized_pending_keys(*args, **kwargs):
            nonlocal initial_scans
            if kwargs.get("include_all_projects"):
                with scan_lock:
                    initial_scans += 1
                    scan_number = initial_scans
                if scan_number <= 2:
                    barrier.wait(3)
            return original_pending_keys(*args, **kwargs)

        started = threading.Event()
        release = threading.Event()

        async def blocking_adapter(_request):
            started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "只接受一个全局槽位"}

        responses = []

        def post(endpoint, request_id):
            responses.append((endpoint, request_id, self.client.post(endpoint, json={"text": "并发全局槽位", "request_id": request_id})))

        with patch.object(main, "PROJECT_CONVERSATION_MAX_PENDING", 1), \
             patch.object(main, "conversation_pending_task_keys", side_effect=synchronized_pending_keys), \
             patch.object(main, "project_conversation_adapter", side_effect=blocking_adapter):
            threads = [
                threading.Thread(target=post, args=(first_endpoint, "global-race-1")),
                threading.Thread(target=post, args=(second_endpoint, "global-race-2")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertTrue(started.wait(2))
            release.set()
            accepted = next((item for item in responses if item[2].status_code == 200), None)
            self.assertIsNotNone(accepted, responses)
            self._wait_for_task(accepted[0], accepted[1], timeout=5)

        self.assertEqual(sorted(response.status_code for _endpoint, _request_id, response in responses), [200, 429])
        self.assertEqual(
            sum(
                1
                for project, conversation in ((first_project, first_conversation), (second_project, second_conversation))
                for item in main.conversation_node_by_id(self.store.canvas(project["id"]), conversation["id"])["messages"]
                if item.get("role") == "user" and item.get("status") == "completed"
            ),
            1,
        )

    def test_project_and_global_queue_caps_return_specific_429_codes(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        other = {**conversation, "id": "conversation-cap-2", "messages": [], "x": 900}
        revision = self.store.canvas(project["id"])["revision"]
        self.store.apply_mutations(project["id"], [{"op": "upsert_node", "node": other}], base_revision=revision)
        self.store.flush(project["id"])
        release = threading.Event()
        started = threading.Event()
        async def blocking_adapter(_request):
            started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "完成"}
        first = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        second = f"/api/project-library/{project['id']}/conversation/{other['id']}/messages"
        with patch.object(main, "PROJECT_CONVERSATION_MAX_PER_PROJECT", 1), patch.object(main, "project_conversation_adapter", side_effect=blocking_adapter):
            self.client.post(first, json={"text": "项目占位", "request_id": "project-cap-1"})
            self.assertTrue(started.wait(2))
            rejected = self.client.post(second, json={"text": "项目拒绝", "request_id": "project-cap-2"})
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.json()["detail"]["code"], "conversation_queue_project_full")
            release.set()
            self._wait_for_task(first, "project-cap-1")

        other_project, _m, other_conversation, _p = self._seed_conversation_canvas(connected=False)
        release.clear(); started.clear()
        other_endpoint = f"/api/project-library/{other_project['id']}/conversation/{other_conversation['id']}/messages"
        with patch.object(main, "PROJECT_CONVERSATION_MAX_PENDING", 1), patch.object(main, "project_conversation_adapter", side_effect=blocking_adapter):
            self.client.post(first, json={"text": "全局占位", "request_id": "global-cap-1"})
            self.assertTrue(started.wait(2))
            rejected = self.client.post(other_endpoint, json={"text": "全局拒绝", "request_id": "global-cap-2"})
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.json()["detail"]["code"], "conversation_queue_global_full")
            release.set()
            self._wait_for_task(first, "global-cap-1")

    def test_node_deleted_before_queued_turn_starts_is_terminal_and_cleans_task_state(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        first_started = threading.Event()
        release = threading.Event()
        calls = []
        async def fake_adapter(request):
            calls.append(request["request_id"])
            first_started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "第一轮结束"}
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            self.client.post(endpoint, json={"text": "先运行", "request_id": "delete-first"})
            self.assertTrue(first_started.wait(2))
            self.client.post(endpoint, json={"text": "不应调用", "request_id": "delete-queued"})
            current = self.store.canvas(project["id"])
            self.store.apply_mutations(project["id"], [{"op": "remove_node", "node_id": conversation["id"]}], base_revision=current["revision"])
            self.store.flush(project["id"])
            release.set()
            deadline = time.monotonic() + 3
            key = (project["id"], conversation["id"], "delete-queued")
            while time.monotonic() < deadline and key in main.PROJECT_CONVERSATION_TASKS:
                time.sleep(0.02)
        self.assertEqual(calls, ["delete-first"])
        self.assertNotIn((project["id"], conversation["id"], "delete-queued"), main.PROJECT_CONVERSATION_TASKS)
        self.assertIsNone(main.project_conversation_ready_result(project["id"], conversation["id"], "delete-queued"))
        self.assertFalse(main.project_conversation_durable_path(project["id"], conversation["id"], "delete-queued").exists())

    def test_message_deleted_before_queued_turn_starts_is_terminal_and_cleans_task_state(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        first_started = threading.Event(); release = threading.Event(); calls = []
        async def fake_adapter(request):
            calls.append(request["request_id"]); first_started.set()
            await asyncio.to_thread(release.wait, 3)
            return {"text": "第一轮结束"}
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            self.client.post(endpoint, json={"text": "先运行", "request_id": "remove-message-first"})
            self.assertTrue(first_started.wait(2))
            second = self.client.post(endpoint, json={"text": "删除此消息", "request_id": "remove-message-queued"})
            self.assertEqual(second.status_code, 200)
            current = self.store.canvas(project["id"])
            node = main.conversation_node_by_id(current, conversation["id"])
            updated = {**node, "messages": [item for item in node["messages"] if item.get("request_id") != "remove-message-queued"]}
            self.store.apply_mutations(project["id"], [{"op": "upsert_node", "node": updated}], base_revision=current["revision"])
            self.store.flush(project["id"])
            release.set()
            key = (project["id"], conversation["id"], "remove-message-queued")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and key in main.PROJECT_CONVERSATION_TASKS:
                time.sleep(0.02)
        self.assertEqual(calls, ["remove-message-first"])
        self.assertNotIn(key, main.PROJECT_CONVERSATION_TASKS)
        self.assertIsNone(main.project_conversation_ready_result(*key))
        self.assertFalse(main.project_conversation_durable_path(*key).exists())

    def test_worker_exception_with_merge_none_persists_uncertain_recovery_marker(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"

        async def explode(*_args, **_kwargs):
            raise RuntimeError("worker path exploded")

        runner = main.PROJECT_CONVERSATION_RUNNER
        try:
            with patch.object(main, "run_project_conversation_task", side_effect=explode), \
                 patch.object(main, "merge_project_conversation_result", return_value=None):
                started = self.client.post(endpoint, json={"text": "必须保留恢复记录", "request_id": "worker-merge-none"})
                self.assertEqual(started.status_code, 200, started.text)
                runner.accepting = False
                sidecar = main.project_conversation_durable_path(project["id"], conversation["id"], "worker-merge-none")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not sidecar.exists():
                    time.sleep(0.02)
                self.assertTrue(sidecar.exists(), "worker failure must leave a durable recovery record")
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertEqual(payload["state"], "ready")
                self.assertIn("不确定", payload["ready"]["error"])
        finally:
            runner.accepting = False

    def test_cross_loop_shutdown_waits_for_real_conversation_worker_finally(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        adapter_started = threading.Event()
        adapter_cancelled = threading.Event()

        async def blocking_adapter(_request):
            adapter_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.2)
                adapter_cancelled.set()

        with patch.object(main, "project_conversation_adapter", side_effect=blocking_adapter):
            started = self.client.post(endpoint, json={
                "text": "关闭时等待 worker finally",
                "request_id": "request-real-worker-shutdown",
            })
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(adapter_started.wait(2))
            runner = main.PROJECT_CONVERSATION_RUNNER

            asyncio.run(main.shutdown_event())

        self.assertTrue(adapter_cancelled.is_set(), "shutdown returned before the foreign worker finished")
        self.assertTrue(all(worker.done() for worker in runner.workers) or runner.workers == [])
        self.assertIsNone(runner.queue)
        self.assertIsNone(runner.loop)

    def test_shutdown_keeps_runner_state_until_cancel_resistant_worker_finishes(self):
        project, _media, conversation, _prompt = self._seed_conversation_canvas(connected=False)
        endpoint = f"/api/project-library/{project['id']}/conversation/{conversation['id']}/messages"
        adapter_started = threading.Event()
        cancellation_seen = threading.Event()
        cleanup_finished = threading.Event()

        async def cancel_resistant_adapter(_request):
            cancelled = False
            try:
                adapter_started.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = True
                cancellation_seen.set()
            finally:
                if cancelled:
                    await asyncio.sleep(2.6)
                    cleanup_finished.set()
            return {"text": "取消后完成清理"}

        with patch.object(main, "project_conversation_adapter", side_effect=cancel_resistant_adapter):
            started = self.client.post(endpoint, json={
                "text": "关闭时保留runner状态",
                "request_id": "request-cancel-resistant-shutdown",
            })
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(adapter_started.wait(2))
            runner = main.PROJECT_CONVERSATION_RUNNER
            task_key = (project["id"], conversation["id"], "request-cancel-resistant-shutdown")
            shutdown_done = threading.Event()
            shutdown_errors = []

            def run_shutdown():
                try:
                    asyncio.run(main.shutdown_event())
                except Exception as exc:
                    shutdown_errors.append(exc)
                finally:
                    shutdown_done.set()

            shutdown_thread = threading.Thread(target=run_shutdown, name="issue13-shutdown-contract")
            shutdown_thread.start()
            try:
                self.assertTrue(cancellation_seen.wait(2))
                time.sleep(2.1)
                self.assertFalse(shutdown_done.is_set(), "shutdown returned while the worker was still in finally")
                self.assertIsNotNone(runner.queue)
                self.assertIsNotNone(runner.loop)
                self.assertIn(task_key, main.PROJECT_CONVERSATION_TASKS)
                shutdown_thread.join(6)
                self.assertFalse(shutdown_thread.is_alive())
            finally:
                cleanup_finished.wait(5)
                shutdown_thread.join(6)

        self.assertEqual(shutdown_errors, [])
        self.assertTrue(cleanup_finished.is_set())
        self.assertIsNone(runner.queue)
        self.assertIsNone(runner.loop)
        self.assertNotIn(task_key, main.PROJECT_CONVERSATION_TASKS)
        revision_after_shutdown = self.store.canvas(project["id"])["revision"]
        time.sleep(0.4)
        self.assertEqual(self.store.canvas(project["id"])["revision"], revision_after_shutdown)

    def test_conversation_task_failure_is_persisted_and_projects_do_not_share_request_registry(self):
        failed_project, _failed_media, failed_conversation, _failed_prompt = self._seed_conversation_canvas(connected=False)
        completed_project, _completed_media, completed_conversation, _completed_prompt = self._seed_conversation_canvas(connected=False)
        adapter_requests = []

        async def fake_adapter(request):
            adapter_requests.append(request)
            if request["project_id"] == failed_project["id"]:
                raise RuntimeError("fake adapter failed deterministically")
            return {"text": "另一个项目仍可完成", "model": "offline-fake"}

        request_id = "request-project-isolation"
        failed_endpoint = f"/api/project-library/{failed_project['id']}/conversation/{failed_conversation['id']}/messages"
        completed_endpoint = f"/api/project-library/{completed_project['id']}/conversation/{completed_conversation['id']}/messages"
        with patch.object(main, "project_conversation_adapter", side_effect=fake_adapter):
            failed = self.client.post(failed_endpoint, json={"text": "这轮应失败", "request_id": request_id})
            completed = self.client.post(completed_endpoint, json={"text": "这轮应完成", "request_id": request_id})
            self.assertEqual(failed.status_code, 200, failed.text)
            self.assertEqual(completed.status_code, 200, completed.text)
            failed_result = self._wait_for_task(failed_endpoint, request_id)
            completed_result = self._wait_for_task(completed_endpoint, request_id)

        self.assertEqual(failed_result.json()["status"], "failed", failed_result.text)
        self.assertEqual(completed_result.json()["status"], "completed", completed_result.text)
        self.assertEqual({request["project_id"] for request in adapter_requests}, {failed_project["id"], completed_project["id"]})
        self.assertFalse(any(key[0] in {failed_project["id"], completed_project["id"]} for key in main.PROJECT_CONVERSATION_TASKS))


if __name__ == "__main__":
    unittest.main()
