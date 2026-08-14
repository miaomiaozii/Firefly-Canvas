import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.complete_generation_lifecycle_evidence import (
    REQUIRED_BOOLEANS,
    REQUIRED_STEPS,
    failed_complete_generation_lifecycle_evidence,
    validate_complete_generation_lifecycle_artifacts,
)
from tools.native_input_evidence import selector_owner_click_evidence


class CompleteGenerationLifecycleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-complete-lifecycle-evidence-")
        self.root = Path(self.temp_dir.name)
        (self.root / "screenshots").mkdir()
        self.lifecycle = {
            **{key: True for key in REQUIRED_BOOLEANS},
            "project_id": "project-1",
            "failed_task_id": "task-failed",
            "retry_task_id": "task-retry",
            "cancel_task_id": "task-cancel",
            "download_task_id": "task-download",
            "record_log_id": "record-old",
            "original_log_id": "record-original",
            "record_asset_id": "asset-record",
            "original_asset_id": "asset-original",
            "status_card_task_ids": ["task-failed", "task-download", "task-cancel"],
            "terminal_record_counts": {
                "task-failed": 1,
                "task-retry": 1,
                "task-cancel": 1,
                "task-download": 1,
            },
            "download_record_log_id": "record-download",
            "download_asset_id": "asset-download",
            "download_request_path": "/api/project-library/project-1/media/asset-download/preview",
            "retention_ms": 604_800_000,
            "original_sha256": "a" * 64,
            "restored_sha256": "a" * 64,
            "generated_sha256": "b" * 64,
            "download_sha256": "b" * 64,
            "original_bytes": 75,
            "restored_bytes": 75,
            "generated_bytes": 76,
            "download_bytes": 76,
            "adapter_attempts": [
                "NATIVE_OLD_RECORD",
                "NATIVE_OLD_ORIGINAL",
                "NATIVE_FAILURE",
                "NATIVE_DOWNLOAD",
                "NATIVE_CANCEL",
                "NATIVE_FAILURE",
            ],
        }
        selectors = [
            "#smartLogToggle",
            '[data-canvas-log-id="record-old"] [data-log-delete="record"]',
            '[data-canvas-log-id="record-original"] [data-log-delete-original="0"]',
            '[data-log-cancel-task="task-cancel"]',
            "#smartLogToggle",
            '[data-log-retry-task="task-failed"]',
            '[data-canvas-log-id="record-download"] [data-log-download-original]',
            "#smartLogToggle",
            '[data-log-restore-original="asset-original"]',
        ]
        (self.root / "operations.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "action": "click",
                        "selector": selector,
                        **selector_owner_click_evidence(),
                    }
                )
                + "\n"
                for selector in selectors
            ),
            encoding="utf-8",
        )
        requests = [
            {"url": "http://127.0.0.1/api/project-library/project-1/history/delete", "status": 200},
            {"url": "http://127.0.0.1/api/project-library/project-1/original/delete", "status": 200},
            {"url": "http://127.0.0.1/api/project-library/project-1/original/restore", "status": 200},
            {"url": "http://127.0.0.1/api/project-library/project-1/image-generation-tasks/task-cancel/cancel", "status": 200},
            {"url": "http://127.0.0.1/api/project-library/project-1/image-generation-tasks/task-failed/retry", "status": 202},
            {"url": "http://127.0.0.1/api/project-library/project-1/media/asset-download/preview", "status": 200},
        ]
        (self.root / "requests.json").write_text(json.dumps(requests), encoding="utf-8")
        (self.root / "console-errors.json").write_text("[]", encoding="utf-8")
        screenshots = []
        for index in range(6):
            path = self.root / "screenshots" / f"{index}.png"
            image = Image.new("RGB", (1424, 900), "white")
            ImageDraw.Draw(image).rectangle((20 + index, 20, 200, 100), fill="black")
            image.save(path)
            screenshots.append(path.relative_to(self.root).as_posix())
        self.manifest = {
            "schema_version": 1,
            "journey": "complete-generation-lifecycle",
            "status": "passed",
            "environment": {
                "os": "Windows",
                "browser": {"name": "Google Chrome", "channel": "stable", "version": "Chrome/140"},
                "dpr": 1,
                "color_scheme": "light",
            },
            "attempt": {
                "status": "passed",
                "viewport": [1424, 900],
                "steps": [{"name": name, "status": "passed"} for name in REQUIRED_STEPS],
                "lifecycle": self.lifecycle,
                "operations": "operations.jsonl",
                "requests": "requests.json",
                "console_errors": "console-errors.json",
                "screenshots": screenshots,
            },
            "first_failure": None,
        }
        self.write_manifest()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_validates_complete_generation_lifecycle_identity_and_byte_evidence(self):
        result = validate_complete_generation_lifecycle_artifacts(self.root)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["step_count"], 8)

    def test_rejects_click_evidence_without_a_browser_semantic_event(self):
        operations_path = self.root / "operations.jsonl"
        operations = [
            json.loads(line)
            for line in operations_path.read_text(encoding="utf-8").splitlines()
        ]
        operations[0]["observed_type"] = "mouseup"
        operations_path.write_text(
            "".join(json.dumps(item) + "\n" for item in operations),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "native UI operations"):
            validate_complete_generation_lifecycle_artifacts(self.root)

    def test_rejects_status_card_identity_claim_without_matching_cards(self):
        self.lifecycle["status_card_task_ids"] = ["task-failed", "task-failed", "task-cancel"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "status-card"):
            validate_complete_generation_lifecycle_artifacts(self.root)

    def test_failed_evidence_preserves_first_failure(self):
        self.manifest["status"] = "failed"
        self.manifest["attempt"]["status"] = "failed"
        self.manifest["first_failure"] = {"step": "seven-day-restore", "error": "restore failed"}
        self.write_manifest()
        result = failed_complete_generation_lifecycle_evidence(self.root)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["first_failure"]["step"], "seven-day-restore")


if __name__ == "__main__":
    unittest.main()
