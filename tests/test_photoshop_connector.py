import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "tools" / "photoshop-asset-connector"


class PhotoshopConnectorSurfaceTests(unittest.TestCase):
    def test_surface_is_only_connection_round_trip_and_status(self):
        index = (CONNECTOR / "index.html").read_text(encoding="utf-8").lower()
        self.assertIn('value="127.0.0.1:3000"', index)
        for marker in ("connect", "send", "return", "status", "project", "source"):
            self.assertIn(marker, index)
        app = (CONNECTOR / "js" / "app.js").read_text(encoding="utf-8").lower()
        self.assertIn("schedulereconnect", app)
        self.assertIn("/api/config", app)
        self.assertIn("state.host = ''", app)
        for forbidden in ("generate", "agent", "comfyui", "asset-library", "全局资产", "生成"):
            self.assertNotIn(forbidden, index)
        app = (CONNECTOR / "js" / "app.js").read_text(encoding="utf-8").lower()
        self.assertIn("opensocket", app)
        self.assertIn("roundtripqueue", app)
        self.assertIn("drainroundtripqueue", app)
        self.assertIn("request_not_found", app)
        canvas = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8").lower()
        self.assertIn("send-ps", canvas)
        self.assertIn("asset_id", canvas)
        self.assertIn("/photoshop/round-trips", canvas)

    def test_legacy_generation_and_asset_modules_are_removed(self):
        for filename in ("agent.js", "generate.js", "sources.js", "ui.js", "socket.js"):
            self.assertFalse((CONNECTOR / "js" / filename).exists(), filename)
        manifest = json.loads((CONNECTOR / "manifest.json").read_text(encoding="utf-8"))
        domains = manifest["requiredPermissions"]["network"]["domains"]
        self.assertNotEqual(domains, "all")
        self.assertNotIn("all", domains if isinstance(domains, list) else [domains])
        entries = [d if isinstance(d, str) else d.get("domain", "") for d in domains]
        self.assertTrue(any("127.0.0.1" in e for e in entries), entries)
        self.assertTrue(any("localhost" in e for e in entries), entries)

    def test_connector_documents_protocol_boundary(self):
        readme = (CONNECTOR / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("127.0.0.1", readme)
        self.assertIn("request_id", readme)
        self.assertIn("derived_from", readme)
        self.assertNotIn("comfyui", readme)


if __name__ == "__main__":
    unittest.main()
