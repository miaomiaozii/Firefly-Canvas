from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.long_content_fixture import write_long_content_artifacts
from tools.long_content_evidence import validate_long_content_artifacts


class LongContentEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        write_long_content_artifacts(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def manifest(self) -> dict:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, payload: dict) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_accepts_complete_five_viewport_evidence(self) -> None:
        summary = validate_long_content_artifacts(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["viewports"], 5)
        self.assertEqual(summary["screenshots"], 25)

    def test_rejects_missing_focus_restoration_after_virtualization(self) -> None:
        manifest = self.manifest()
        manifest["attempts"][0]["lifecycle"]["history_focus_restored_after_virtualization"] = False
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "preservation assertion"):
            validate_long_content_artifacts(self.root)

    def test_rejects_generation_side_effects(self) -> None:
        manifest = self.manifest()
        attempt = manifest["attempts"][0]
        request_path = self.root / attempt["requests"]
        request_path.write_text(
            json.dumps([{"url":"http://127.0.0.1/api/projects/p/image-generation-tasks"}]),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "generation request"):
            validate_long_content_artifacts(self.root)

    def test_rejects_missing_native_keyboard_activation(self) -> None:
        manifest = self.manifest()
        operations_path = self.root / manifest["attempts"][0]["operations"]
        operations = [
            json.loads(line)
            for line in operations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        operations = [item for item in operations if not (item.get("action") == "keyboard" and item.get("key") == "Enter")]
        operations_path.write_text("\n".join(json.dumps(item) for item in operations) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "keyboard activation"):
            validate_long_content_artifacts(self.root)


if __name__ == "__main__":
    unittest.main()
