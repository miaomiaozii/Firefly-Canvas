"""票 G（#325 / spec P5）：外部 CLI 接管 snippet 端点。

GET /api/projects/{pid}/agent-nodes/external-cli-snippet?node={nid}
返回 codex / claude 两条可复制命令；URL 基址必须取 server 运行时值
（resolve_agent_api_base：FIREFLY_AGENT_API_BASE → FIREFLY_PORT → 3000，禁硬编码）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


class TestExternalCliSnippet(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-g-snippet-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.project_id = self.store.create_project("G Snippet")["id"]

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _make_node(self, node_id):
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": node_id, "type": "smart-agent", "title": "A", "x": 0, "y": 0},
        }], base_revision=0)

    def _get(self, node):
        return self.client.get(
            f"/api/projects/{self.project_id}/agent-nodes/external-cli-snippet",
            params={"node": node},
        )

    def test_snippet_ok_for_existing_node(self):
        self._make_node("g-node-1")
        r = self._get("g-node-1")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        for key in ("mcp_url", "codex_cmd", "claude_cmd"):
            self.assertIn(key, d)
        self.assertIn(f"project={self.project_id}", d["mcp_url"])
        self.assertIn("node=g-node-1", d["mcp_url"])
        self.assertIn(d["mcp_url"], d["codex_cmd"])
        self.assertIn("mcp_servers.firefly.url", d["codex_cmd"])
        self.assertIn("codex", d["codex_cmd"])
        self.assertIn(d["mcp_url"], d["claude_cmd"])
        self.assertIn("claude mcp add", d["claude_cmd"])

    def test_base_url_follows_runtime_env(self):
        """#279 同源要求：基址取运行时 env，不硬编码。"""
        self._make_node("g-node-2")
        with patch.dict(os.environ, {"FIREFLY_AGENT_API_BASE": "http://127.0.0.1:4599"}, clear=False):
            d = self._get("g-node-2").json()
        self.assertTrue(d["mcp_url"].startswith("http://127.0.0.1:4599/api/mcp"), d["mcp_url"])

    def test_base_url_follows_firefly_port_env(self):
        self._make_node("g-node-3")
        env = dict(os.environ)
        env.pop("FIREFLY_AGENT_API_BASE", None)
        with patch.dict(os.environ, {"FIREFLY_PORT": "4600"}, clear=True):
            d = self._get("g-node-3").json()
        self.assertIn(":4600", d["mcp_url"])

    def test_missing_node_param_400(self):
        r = self.client.get(f"/api/projects/{self.project_id}/agent-nodes/external-cli-snippet")
        self.assertEqual(r.status_code, 400)

    def test_unknown_node_404(self):
        r = self._get("nope-node")
        self.assertEqual(r.status_code, 404)

    def test_unknown_project_404(self):
        r = self.client.get(
            "/api/projects/definitely-missing-project/agent-nodes/external-cli-snippet",
            params={"node": "x"},
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
