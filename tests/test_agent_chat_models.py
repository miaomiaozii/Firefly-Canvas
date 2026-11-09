"""Tests for Issue #285: Agent 模型下拉是图片模型池且选择不生效。

覆盖两处修复：
1. GET /agent-nodes/chat-models：只读解析 pi 会话模型池（models.json + settings.json，
   FIREFLY_PI_AGENT_DIR 覆盖目录），返回 provider/modelId 形式 + current。
2. POST /agent-nodes/{node_id}/model：WS 会话不在持久画布 nodes 里时不再 404
   （用户实测 "Node not found: agent_probe2"），会话 set_model 先行，持久化 best-effort。
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import install_stub_pi_cli, restore_stub_pi_cli


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def setUpModule():
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


MODELS_FIXTURE = {
    "providers": {
        "bailian": {
            "baseUrl": "https://dashscope.example/compatible-mode/v1",
            "api": "openai-completions",
            "models": [
                {"id": "qwen-plus", "name": "Qwen Plus"},                          # 无 type → 按 chat（pi 缺省）
                {"id": "qwen3-max", "name": "Qwen3 Max"},                          # 无 type → chat
                {"id": "qwen-image-2.1-pro", "name": "Qwen Image 2.1 Pro", "type": "image"},  # 图像端点 → 必须剔除
                {"id": "sentiment-classifier", "type": "classifier"},               # classifier → 必须剔除
            ],
        },
        "vllm-remote-nvfp4": {
            "baseUrl": "http://127.0.0.1:8080/v1",
            "api": "openai-completions",
            "models": [
                {"id": "qwen3.8-27b-quasar-nvfp4"},                                 # 无 type → chat
                {"id": "explicit-chat", "name": "Explicit Chat", "type": "chat"},   # 显式 chat → 收录
            ],
        },
    }
}


class AgentChatModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-chat-models-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8123")
        self.project = self.store.create_project("Chat Models Test")
        self.project_id = self.project["id"]
        # pi agent 目录夹具
        self.agent_dir = Path(self.temp_dir.name) / "pi-agent"
        self.agent_dir.mkdir()
        (self.agent_dir / "models.json").write_text(json.dumps(MODELS_FIXTURE), encoding="utf-8")
        (self.agent_dir / "settings.json").write_text(
            json.dumps({"defaultProvider": "bailian", "defaultModel": "qwen-plus"}), encoding="utf-8"
        )
        self.previous_env = os.environ.get("FIREFLY_PI_AGENT_DIR")
        os.environ["FIREFLY_PI_AGENT_DIR"] = str(self.agent_dir)

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        if self.previous_env is None:
            os.environ.pop("FIREFLY_PI_AGENT_DIR", None)
        else:
            os.environ["FIREFLY_PI_AGENT_DIR"] = self.previous_env
        self.temp_dir.cleanup()

    def test_chat_models_endpoint_lists_provider_scoped_ids(self):
        response = self.client.get(f"/api/projects/{self.project_id}/agent-nodes/chat-models")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ids = [m["id"] for m in payload["models"]]
        # chat 模型（含无 type 缺省 + 显式 chat）都在池里。
        self.assertIn("bailian/qwen-plus", ids)
        self.assertIn("bailian/qwen3-max", ids)
        self.assertIn("vllm-remote-nvfp4/qwen3.8-27b-quasar-nvfp4", ids)
        self.assertIn("vllm-remote-nvfp4/explicit-chat", ids)
        self.assertEqual(payload["current"], "bailian/qwen-plus")

    def test_chat_models_endpoint_excludes_image_and_classifier(self):
        """#285 核心：图片模型不得混入——image/classifier 类型的模型不进会话池。"""
        response = self.client.get(f"/api/projects/{self.project_id}/agent-nodes/chat-models")
        self.assertEqual(response.status_code, 200, response.text)
        ids = [m["id"] for m in response.json()["models"]]
        self.assertNotIn("bailian/qwen-image-2.1-pro", ids, "图像端点混入会话池（选中即无响应）")
        self.assertNotIn("bailian/sentiment-classifier", ids, "classifier 混入会话池")
        # 其余 chat 模型不受影响（不是把所有 bailian 都剔了）。
        self.assertIn("bailian/qwen-plus", ids)

    def test_set_model_reflected_in_healthz_session(self):
        """#285 回归钉：选择后 /api/healthz 的 sessions.model 变化（只读投影，无真实 spawn）。"""
        node_id = "agent_healthz_probe"
        response = self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes/{node_id}/model",
            json={"model": "bailian/qwen3-max"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        health = self.client.get("/api/healthz")
        self.assertEqual(health.status_code, 200, health.text)
        sessions = health.json()["agents"]["sessions"]
        match = [s for s in sessions if s["node_id"] == node_id]
        self.assertEqual(len(match), 1, f"healthz 应含 {node_id} 会话：{sessions}")
        self.assertEqual(match[0]["model"], "bailian/qwen3-max")

    def test_chat_models_endpoint_missing_dir_returns_empty(self):
        os.environ["FIREFLY_PI_AGENT_DIR"] = str(self.agent_dir.parent / "nope")
        response = self.client.get(f"/api/projects/{self.project_id}/agent-nodes/chat-models")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"models": [], "current": ""})

    def test_set_model_on_session_without_persisted_node_does_not_404(self):
        """#285 核心回归：持久画布里没有该节点（WS spawn 会话）时不得 404。"""
        node_id = "agent_probe2"
        session = agent_host_manager.get_session(node_id) if hasattr(agent_host_manager, "get_session") else None
        response = self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes/{node_id}/model",
            json={"model": "bailian/qwen-plus"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["model"], "bailian/qwen-plus")
        self.assertFalse(payload["persisted"], "未持久化节点应记 persisted=False 而非报错")
        # 会话内存态已更新（下次 spawn 生效）
        live = None
        for existing in getattr(agent_host_manager, "sessions", {}).values():
            if existing.node_id == node_id:
                live = existing
        self.assertIsNotNone(live, "端点应已创建/取回会话")
        self.assertEqual(live.model, "bailian/qwen-plus")


if __name__ == "__main__":
    unittest.main()
