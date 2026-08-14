from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.composer_flow_fixture import write_composer_flow_runner
from tools.composer_flow_evidence import validate_composer_flow_artifacts


class ComposerFlowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-composer-flow-evidence-")
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        runner = write_composer_flow_runner(self.root)
        self.artifacts = self.root / "artifacts"
        process = subprocess.run(
            [sys.executable, str(runner), "--artifacts-dir", str(self.artifacts)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_complete_five_viewport_native_journey(self):
        summary = validate_composer_flow_artifacts(self.artifacts)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["viewports"], 5)
        self.assertEqual(summary["screenshots"], 15)

    def test_rejects_missing_native_expand_control(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        operations = self.artifacts / manifest["attempts"][0]["operations"]
        items = [json.loads(line) for line in operations.read_text(encoding="utf-8").splitlines()]
        operations.write_text(
            "".join(json.dumps(item) + "\n" for item in items if item.get("selector") != "#composerExpandBtn"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "expand/focus"):
            validate_composer_flow_artifacts(self.artifacts)

    def test_rejects_untrusted_wheel_evidence(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        operations = self.artifacts / manifest["attempts"][0]["operations"]
        items = [json.loads(line) for line in operations.read_text(encoding="utf-8").splitlines()]
        next(item for item in items if item.get("action") == "wheel")["trusted"] = False
        operations.write_text(
            "".join(json.dumps(item) + "\n" for item in items),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "trusted native focus-mode scrolling"):
            validate_composer_flow_artifacts(self.artifacts)

    def test_rejects_unexpected_generation_request(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        requests = self.artifacts / manifest["attempts"][0]["requests"]
        requests.write_text(
            json.dumps([{"url": "http://127.0.0.1/api/project-image-generation/p1/tasks"}]),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "unexpected generation request"):
            validate_composer_flow_artifacts(self.artifacts)

    def test_rejects_escape_that_generated_or_called_provider(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["attempts"][2]["lifecycle"]["escape_generated"] = True
        manifest["attempts"][2]["lifecycle"]["provider_calls"] = 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "side-effect"):
            validate_composer_flow_artifacts(self.artifacts)


if __name__ == "__main__":
    unittest.main()
