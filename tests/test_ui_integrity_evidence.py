from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from tests.ui_integrity_fixture import write_valid_ui_integrity_artifact
from tests.ui_integrity_native_browser import capture_setup_failure
from tools.ui_integrity_evidence import (
    EXPECTED_NODE_ACTION_IDS,
    EXPECTED_SCENES,
    EXPECTED_VIEWPORTS,
    accept_baseline,
    chrome_major,
    failed_ui_integrity_evidence,
    validate,
    validate_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
REPO_UI_BASELINE_MANIFEST = ROOT / "tests" / "baselines" / "ui-integrity" / "manifest.json"


def product_toolbar_action_ids() -> list[str]:
    """Read the node toolbar action keys straight out of the product source.

    Independent of the validator's declared list, so it can witness drift in
    either direction (issue #176).
    """
    source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
    start = source.index("function smartNodeToolbarHtml(")
    array_start = re.compile(r"const actions\s*=\s*\[").search(source, start)
    assert array_start is not None
    array = source[array_start.end() : source.index("]", array_start.end())]
    return re.findall(r"\{\s*key\s*:\s*'([^']+)'", array)


class UiIntegrityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = self.root / "artifacts"
        self.baselines = self.root / "baselines"
        write_valid_ui_integrity_artifact(self.artifacts)

    def tearDown(self):
        self.temp.cleanup()

    def accept(self):
        return accept_baseline(
            self.artifacts,
            self.baselines,
            accepted_by="fixture-reviewer",
            reason="unit-test accepted visual contract",
        )

    def test_cli_exposes_the_baseline_acceptance_entry_point(self):
        """Issue #183: a stale baseline must be refreshable through the CLI.

        The acceptance routine is only useful if a human can reach it, so the
        subcommand itself is locked here: it writes the 40 candidate PNGs plus a
        manifest that records the Chrome major the baseline was captured with.
        """
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "ui_integrity_evidence.py"),
                "accept",
                "--artifacts-dir",
                str(self.artifacts),
                "--baselines-dir",
                str(self.baselines),
                "--accepted-by",
                "cli-guard-test",
                "--reason",
                "issue #183 baseline refresh entry",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["status"], "accepted")
        manifest = json.loads((self.baselines / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(manifest["accepted"], True)
        self.assertEqual(manifest["accepted_by"], "cli-guard-test")
        self.assertEqual(
            manifest["accepted_browser_versions"],
            [chrome_major(manifest["environment"]["browser"]["version"])],
        )
        self.assertEqual(
            len(manifest["screenshots"]), len(EXPECTED_VIEWPORTS) * len(EXPECTED_SCENES)
        )
        for entry in manifest["screenshots"]:
            self.assertTrue((self.baselines / entry["path"]).is_file(), entry["path"])

    def test_cli_validate_command_consumes_the_accepted_baseline(self):
        accepted = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "ui_integrity_evidence.py"),
                "accept",
                "--artifacts-dir",
                str(self.artifacts),
                "--baselines-dir",
                str(self.baselines),
                "--accepted-by",
                "cli-guard-test",
                "--reason",
                "issue #183 baseline refresh entry",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "ui_integrity_evidence.py"),
                "validate",
                "--artifacts-dir",
                str(self.artifacts),
                "--baselines-dir",
                str(self.baselines),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "passed")

    def test_candidate_is_valid_but_cannot_validate_without_explicitly_accepted_baseline(self):
        candidate = validate_candidate(self.artifacts)
        self.assertEqual(candidate["viewports"], 5)
        self.assertEqual(candidate["screenshots"], len(EXPECTED_VIEWPORTS) * len(EXPECTED_SCENES))
        with self.assertRaisesRegex(ValueError, "explicitly run the accept command"):
            validate(self.artifacts, self.baselines)
        self.assertFalse(self.baselines.exists())

    def test_explicit_acceptance_copies_an_auditable_baseline_and_validation_passes(self):
        accepted = self.accept()
        self.assertEqual(accepted["status"], "accepted")
        manifest = json.loads((self.baselines / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["accepted"])
        self.assertEqual(manifest["accepted_by"], "fixture-reviewer")
        self.assertEqual(manifest["reason"], "unit-test accepted visual contract")
        self.assertEqual(len(manifest["screenshots"]), len(EXPECTED_VIEWPORTS) * len(EXPECTED_SCENES))
        self.assertEqual(validate(self.artifacts, self.baselines)["status"], "passed")

    def test_candidate_source_replacement_after_collection_cannot_change_frozen_validation(self):
        self.accept()
        collected = validate_candidate(self.artifacts)
        snapshot_image = self.artifacts / collected["entries"][-1]["snapshot"]
        snapshot_bytes = snapshot_image.read_bytes()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        Image.new("RGB", (800, 600), (255, 0, 0)).save(candidate_image, format="PNG")

        with mock.patch("tools.ui_integrity_evidence.validate_candidate", return_value=collected):
            self.assertEqual(validate(self.artifacts, self.baselines)["status"], "passed")
        self.assertEqual(snapshot_image.read_bytes(), snapshot_bytes)

    def test_candidate_collected_from_unreviewed_bytes_cannot_be_swapped_back_to_baseline(self):
        self.accept()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        baseline_image = self.baselines / "800x600" / "shortcuts.png"
        Image.new("RGB", (800, 600), (255, 0, 0)).save(candidate_image, format="PNG")
        collected = validate_candidate(self.artifacts)
        snapshot_image = self.artifacts / collected["entries"][-1]["snapshot"]
        snapshot_image.write_bytes(baseline_image.read_bytes())

        with mock.patch("tools.ui_integrity_evidence.validate_candidate", return_value=collected):
            with self.assertRaisesRegex(ValueError, "candidate screenshot changed after collection"):
                validate(self.artifacts, self.baselines)

    def test_candidate_mode_or_alpha_change_is_rejected(self):
        self.accept()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            source.convert("RGBA").save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "RGB PNG|baseline mismatch"):
            validate(self.artifacts, self.baselines)

    def test_unknown_baseline_schema_is_rejected(self):
        self.accept()
        manifest_path = self.baselines / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 999
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "schema version"):
            validate(self.artifacts, self.baselines)

    def test_low_amplitude_rasterization_noise_within_strict_budget_is_tolerated(self):
        self.accept()
        baseline_manifest = (self.baselines / "manifest.json").read_bytes()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            changed = source.convert("RGB")
        original = changed.getpixel((20, 20))
        changed.paste(tuple(min(255, channel + 1) for channel in original), (20, 20, 160, 160))
        changed.save(candidate_image, format="PNG")

        result = validate(self.artifacts, self.baselines)

        self.assertEqual(result["status"], "passed")
        tolerated = result["tolerated_screenshot_differences"][0]
        self.assertEqual(tolerated["key"], "800x600/shortcuts.png")
        self.assertEqual(tolerated["changed_bbox"], [10, 10, 80, 80])
        self.assertEqual(tolerated["thresholds"]["max_peak_delta"], 48)
        self.assertEqual((self.baselines / "manifest.json").read_bytes(), baseline_manifest)

    def test_low_amplitude_edge_rasterization_within_budget_is_tolerated(self):
        self.accept()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            changed = source.convert("RGB")
        pixels = changed.load()
        for y in range(changed.height - 18, changed.height):
            for x in range(300):
                pixels[x, y] = tuple(min(255, channel + 6) for channel in pixels[x, y])
        changed.save(candidate_image, format="PNG")

        result = validate(self.artifacts, self.baselines)

        self.assertEqual(result["status"], "passed")
        tolerated = result["tolerated_screenshot_differences"][0]
        self.assertEqual(tolerated["key"], "800x600/shortcuts.png")
        self.assertEqual(tolerated["thresholds"]["edge_raster_max_delta"], 16)

    def test_low_amplitude_dense_interior_change_is_rejected(self):
        self.accept()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            changed = source.convert("RGB")
        pixels = changed.load()
        for y in range(100, 140):
            for x in range(100, 140):
                pixels[x, y] = tuple(min(255, channel + 6) for channel in pixels[x, y])
        changed.save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            validate(self.artifacts, self.baselines)

    def test_coherent_visual_change_inside_global_budget_is_rejected(self):
        self.accept()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            changed = source.convert("RGB")
        original = changed.getpixel((20, 20))
        changed.paste(tuple(min(255, channel + 10) for channel in original), (20, 20, 60, 60))
        changed.save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            validate(self.artifacts, self.baselines)

    def test_one_pixel_geometry_shift_is_rejected(self):
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            baseline = source.convert("RGB")
        background = baseline.getpixel((20, 20))
        foreground = tuple(min(255, channel + 20) for channel in background)
        baseline.paste(foreground, (100, 100, 200, 180))
        baseline.save(candidate_image, format="PNG")
        self.accept()

        shifted = baseline.copy()
        shifted.paste(background, (100, 100, 200, 180))
        shifted.paste(foreground, (101, 100, 201, 180))
        shifted.save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            validate(self.artifacts, self.baselines)

    def test_one_pixel_shift_of_compact_icon_control_is_rejected(self):
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        with Image.open(candidate_image) as source:
            baseline = source.convert("RGB")
        background = baseline.getpixel((20, 20))
        foreground = tuple(min(255, channel + 100) for channel in background)
        baseline.paste(foreground, (100, 100, 128, 101))
        baseline.paste(foreground, (100, 127, 128, 128))
        baseline.paste(foreground, (100, 100, 101, 128))
        baseline.paste(foreground, (127, 100, 128, 128))
        baseline.save(candidate_image, format="PNG")
        self.accept()

        shifted = baseline.copy()
        shifted.paste(background, (100, 100, 128, 128))
        shifted.paste(foreground, (101, 100, 129, 101))
        shifted.paste(foreground, (101, 127, 129, 128))
        shifted.paste(foreground, (101, 100, 102, 128))
        shifted.paste(foreground, (128, 100, 129, 128))
        shifted.save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            validate(self.artifacts, self.baselines)

    def test_visual_failure_writes_a_diff_without_overwriting_the_accepted_baseline(self):
        self.accept()
        baseline_manifest = (self.baselines / "manifest.json").read_bytes()
        baseline_image = self.baselines / "800x600" / "shortcuts.png"
        baseline_bytes = baseline_image.read_bytes()
        candidate_image = self.artifacts / "viewport-800x600" / "screenshots" / "shortcuts.png"
        Image.new("RGB", (800, 600), (255, 0, 0)).save(candidate_image, format="PNG")

        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            validate(self.artifacts, self.baselines)

        self.assertEqual((self.baselines / "manifest.json").read_bytes(), baseline_manifest)
        self.assertEqual(baseline_image.read_bytes(), baseline_bytes)
        self.assertTrue((self.artifacts / "baseline-diffs" / "800x600" / "shortcuts.png").is_file())

    def test_acceptance_requires_identity_reason_and_explicit_replace(self):
        with self.assertRaisesRegex(ValueError, "accepted_by and reason"):
            accept_baseline(self.artifacts, self.baselines, accepted_by="", reason="")
        self.accept()
        original = (self.baselines / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "--replace"):
            accept_baseline(
                self.artifacts,
                self.baselines,
                accepted_by="fixture-reviewer",
                reason="unreviewed overwrite",
            )
        self.assertEqual((self.baselines / "manifest.json").read_bytes(), original)

    def test_passed_unaccepted_evidence_is_reported_as_a_valid_candidate(self):
        captured = failed_ui_integrity_evidence(self.artifacts)
        self.assertEqual(captured["status"], "candidate")
        self.assertEqual(captured["candidate"]["status"], "passed")
        self.assertEqual(captured["candidate"]["viewports"], 5)

    def test_failed_evidence_requires_complete_in_root_first_failure_artifacts(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        attempt = manifest["attempts"][0]
        manifest["status"] = "failed"
        manifest["first_failure"] = {
            "viewport": attempt["viewport"],
            "type": "AssertionError",
            "message": "first geometry failure",
            "operations": attempt["operations"],
            "requests": attempt["requests"],
            "console_errors": attempt["console_errors"],
            "failure_screenshot": attempt["screenshots"][0]["path"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        captured = failed_ui_integrity_evidence(self.artifacts)
        self.assertEqual(captured["status"], "captured")
        self.assertTrue(Path(captured["artifacts"]["failure_screenshot"]).is_file())

        manifest["first_failure"]["operations"] = "../escaped.jsonl"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        invalid = failed_ui_integrity_evidence(self.artifacts)
        self.assertEqual(invalid["status"], "invalid")
        self.assertIn("outside", invalid["artifact_error"])

    def test_missing_geometry_or_accessibility_contract_is_rejected(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["attempts"][0]["lifecycle"]["icon_controls_named"] = False
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "required UI integrity assertion"):
            validate_candidate(self.artifacts)

    def test_selection_actions_require_the_exact_complete_reachable_set(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        scene = next(item for item in geometry if item["scene"] == "canvas-composer")
        scene["selectionActions"]["actualActionIds"].pop()
        scene["selectionActions"]["actions"].pop()
        scene["selectionActions"]["allReachable"] = True
        scene["passed"] = True
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "exact selection-action set"):
            validate_candidate(self.artifacts)

    def test_prompt_opacity_chinese_headings_and_theme_titles_fail_closed(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        composer = next(item for item in geometry if item["scene"] == "canvas-composer")
        composer["promptSurfacesOpaque"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "prompt-surface contract"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        history["historyFacts"]["titleThemeColor"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed image-first contract"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["detailFacts"]["englishKickerAbsent"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata or 2K"):
            validate_candidate(self.artifacts)

    def test_scene_specific_facts_are_recomputed_instead_of_trusting_lifecycle_booleans(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        history["historyFacts"]["overlayDelete"] = True
        history["historyFacts"]["scanThumbnailsFillFrame"] = False
        history["passed"] = True
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "fixed image-first contract"):
            validate_candidate(self.artifacts)

    def test_multi_output_preview_and_detail_switching_fail_closed(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        history["multiOutputFacts"]["previewsDiscoverable"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed image-first contract"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["detailFacts"]["selectedImageDecoded"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata or 2K"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        history["multiOutputFacts"]["selectedPreviewDecoded"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed image-first contract"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["clipboardFacts"]["matchesSelectedOutput"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata or 2K"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["detailFacts"]["downloadSourceMatches"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata or 2K"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        history["multiOutputFacts"]["outputAssetIdsAfterRestore"] = [
            "output-1", "output-2", "output-4", "output-3",
            "output-5", "output-6", "output-7", "output-8",
        ]
        history["multiOutputFacts"]["identityOrderPreserved"] = True
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed image-first contract"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["detailFacts"]["postDelete"]["canvasAssetIds"] = [
            "output-1", "output-2", "output-3", "output-4", "output-6", "output-7", "output-8"
        ]
        detail["detailFacts"]["postDelete"]["singleAssetSynchronized"] = True
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata or 2K"):
            validate_candidate(self.artifacts)

    def test_settings_and_shortcuts_reject_a_history_drawer_left_open(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        shortcuts = next(item for item in geometry if item["scene"] == "shortcuts")
        shortcuts["passed"] = True
        shortcuts["historyDrawerState"].update(
            {
                "open": True,
                "ariaExpanded": "true",
                "visibility": "visible",
                "opacity": 1,
                "pointerEvents": "auto",
            }
        )
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "history drawer remained open"):
            validate_candidate(self.artifacts)

    def test_duplicate_occurrence_evidence_is_recomputed_from_raw_output_ids(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        attempt = next(item for item in manifest["attempts"] if item["viewport"] == [800, 600])
        geometry_path = self.artifacts / attempt["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        duplicate = next(item for item in geometry if item["scene"] == "duplicate-occurrence-check")
        facts = duplicate["duplicateOccurrenceFacts"]
        facts["exactDelete"] = True
        facts["mediaReadable"] = True
        facts["exactRestore"] = True
        facts["afterDelete"]["historyOutputIds"] = [
            "dup-occ-1", "dup-occ-2", "dup-occ-3"
        ]
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "duplicate output occurrence"):
            validate_candidate(self.artifacts)

    def test_history_preview_overlay_geometry_is_recomputed_from_raw_rectangles(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        multi_output = history["multiOutputFacts"]
        multi_output.update(
            {
                "previewsDiscoverable": True,
                "eightPreviewGridFits": True,
                "previewOverlay": True,
                "outerFrameAbsent": True,
            }
        )
        last_preview = multi_output["previewGeometry"]["previews"][-1]["rect"]
        last_preview.update(
            {
                "left": 190,
                "right": 220,
            }
        )
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "preview geometry|fixed image-first contract"):
            validate_candidate(self.artifacts)

    def test_history_preview_strip_requires_real_scroll_without_scrollbar(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        history = next(item for item in geometry if item["scene"] == "history")
        multi_output = history["multiOutputFacts"]
        multi_output.update(
            {
                "previewsDiscoverable": True,
                "eightPreviewGridFits": True,
                "previewOverlay": True,
                "outerFrameAbsent": True,
            }
        )
        multi_output["previewGeometry"]["stripWheelShift"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "scroll-reachable"):
            validate_candidate(self.artifacts)

    def test_generation_detail_output_strip_outer_frame_is_recomputed_from_raw_style(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        detail = next(item for item in geometry if item["scene"] == "generation-detail")
        detail["detailFacts"]["outputStripOuterFrameAbsent"] = True
        detail["detailFacts"]["outputStripStyle"]["borderTop"] = 1
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "output strip retains an outer frame"):
            validate_candidate(self.artifacts)

    def test_new_flow_preview_and_composer_facts_fail_closed(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        flow = next(item for item in geometry if item["scene"] == "composer-flow")
        flow["promptFlowContext"]["recentNoScroll"] = False
        geometry_path.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "creative-context contract"):
            validate_candidate(self.artifacts)

    def test_critical_click_evidence_requires_a_semantic_click_after_mouseup(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        operations_path = self.artifacts / manifest["attempts"][0]["operations"]
        operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines()]
        settings_click = next(
            item for item in operations if item.get("selector") == "#smartSettingsLink"
        )
        settings_click["observed_type"] = "mouseup"
        operations_path.write_text(
            "".join(json.dumps(item) + "\n" for item in operations),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "critical UI surface"):
            validate_candidate(self.artifacts)

    def test_create_menu_evidence_requires_a_semantic_contextmenu_event(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        operations_path = self.artifacts / manifest["attempts"][0]["operations"]
        operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines()]
        context_click = next(
            item
            for item in operations
            if item.get("selector") == "#shell" and item.get("button") == "right"
        )
        context_click["observed_type"] = "mouseup"
        operations_path.write_text(
            "".join(json.dumps(item) + "\n" for item in operations),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "trusted native create-menu activation"):
            validate_candidate(self.artifacts)

    def test_create_menu_evidence_rejects_a_right_click_that_hit_a_node(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        operations_path = self.artifacts / manifest["attempts"][0]["operations"]
        operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines()]
        context_click = next(
            item
            for item in operations
            if item.get("selector") == "#shell" and item.get("button") == "right"
        )
        context_click.update(
            {
                "blank_canvas": False,
                "blocking_hit": ".image-node",
                "observed_target": "img.node-img",
                "hit_ancestry": ["img.node-img", "div.image-node"],
            }
        )
        operations_path.write_text(
            "".join(json.dumps(item) + "\n" for item in operations),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "unblocked canvas point"):
            validate_candidate(self.artifacts)

    def test_native_wheel_drag_and_offline_requests_are_fail_closed(self):
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        attempt = manifest["attempts"][0]
        operations_path = self.artifacts / attempt["operations"]
        operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines()]
        operations = [item for item in operations if item.get("action") != "drag"]
        operations_path.write_text("".join(json.dumps(item) + "\n" for item in operations), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "native generation-detail pan"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        requests_path = self.artifacts / manifest["attempts"][0]["requests"]
        requests_path.write_text(json.dumps([{"url": "https://provider.example/v1/models"}]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-loopback"):
            validate_candidate(self.artifacts)

    def test_passed_candidate_cannot_retain_a_first_failure(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["first_failure"] = {"message": "hidden retry"}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "cannot retain or overwrite a first failure"):
            validate_candidate(self.artifacts)

    def test_candidate_rejects_a_missing_fixed_font(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["environment"]["fonts"]["JetBrains Mono"] = False
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "fixed font-availability contract"):
            validate_candidate(self.artifacts)

    # --- #180: the accepted baseline pins the environment, except an explicitly
    # allowed stable-Chrome version bump ---

    def _patch_candidate_environment(self, mutate) -> None:
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest["environment"])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def _allow_baseline_browser_versions(self, majors: list[int]) -> None:
        manifest_path = self.baselines / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["accepted_browser_versions"] = majors
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def _patch_baseline_environment(self, mutate) -> None:
        manifest_path = self.baselines / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest["environment"])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_accepted_baseline_tolerates_an_allowed_stable_chrome_version_bump(self):
        self.accept()
        self._allow_baseline_browser_versions([151, 155])
        self._patch_candidate_environment(
            lambda environment: environment["browser"].__setitem__("version", "Chrome/155.0.8059.40")
        )
        self.assertEqual(validate(self.artifacts, self.baselines)["status"], "passed")

    def test_accepted_baseline_still_requires_the_same_browser_name_and_channel(self):
        self.accept()
        self._allow_baseline_browser_versions([151, 155])
        self._patch_baseline_environment(
            lambda environment: environment["browser"].__setitem__("channel", "dev")
        )
        with self.assertRaisesRegex(ValueError, "browser name and channel"):
            validate(self.artifacts, self.baselines)

        self._patch_baseline_environment(
            lambda environment: environment["browser"].__setitem__("channel", "stable")
        )
        self._patch_baseline_environment(
            lambda environment: environment["browser"].__setitem__("name", "Microsoft Edge")
        )
        with self.assertRaisesRegex(ValueError, "browser name and channel"):
            validate(self.artifacts, self.baselines)

    def test_accepted_baseline_still_requires_every_other_environment_field(self):
        self.accept()
        self._allow_baseline_browser_versions([151, 155])
        self._patch_baseline_environment(lambda environment: environment.__setitem__("locale", "en-US"))
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            validate(self.artifacts, self.baselines)

        self._patch_baseline_environment(lambda environment: environment.__setitem__("locale", "zh-CN"))
        self._patch_baseline_environment(lambda environment: environment.__setitem__("dpr", 2))
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            validate(self.artifacts, self.baselines)

        self._patch_baseline_environment(lambda environment: environment.__setitem__("dpr", 1))
        self._patch_baseline_environment(lambda environment: environment.__setitem__("animations", "enabled"))
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            validate(self.artifacts, self.baselines)

    def test_accepted_baseline_rejects_a_chrome_major_outside_the_allow_list(self):
        self.accept()
        self._allow_baseline_browser_versions([151, 155])
        self._patch_candidate_environment(
            lambda environment: environment["browser"].__setitem__("version", "Chrome/160.0.0.0")
        )
        with self.assertRaisesRegex(ValueError, "outside the accepted UI baseline browser versions"):
            validate(self.artifacts, self.baselines)

    def test_baseline_without_an_explicit_allow_list_stays_exact_on_version(self):
        self.accept()
        manifest_path = self.baselines / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("accepted_browser_versions")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self._patch_candidate_environment(
            lambda environment: environment["browser"].__setitem__("version", "Chrome/155.0.8059.40")
        )
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            validate(self.artifacts, self.baselines)

    def test_accept_baseline_records_the_captured_chrome_major_as_the_default_allow_list(self):
        self.accept()
        manifest = json.loads((self.baselines / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["accepted_browser_versions"], [151])

    def test_repo_ui_baseline_declares_an_explicit_browser_version_allow_list(self):
        manifest = json.loads(REPO_UI_BASELINE_MANIFEST.read_text(encoding="utf-8"))
        majors = manifest.get("accepted_browser_versions")
        self.assertIsInstance(majors, list)
        self.assertTrue(majors, "the accepted UI baseline must declare a non-empty version allow list")
        self.assertTrue(all(isinstance(item, int) and item > 0 for item in majors))
        self.assertIn(chrome_major(manifest["environment"]["browser"]["version"]), majors)

    def test_setup_failure_always_creates_self_contained_first_failure_artifacts(self):
        attempt, failure = capture_setup_failure(
            self.root / "setup-failure",
            (800, 600),
            RuntimeError("browser setup failed"),
        )

        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(failure["message"], "browser setup failed")
        root = self.root / "setup-failure"
        self.assertEqual((root / failure["operations"]).read_text(encoding="utf-8"), "")
        self.assertEqual(json.loads((root / failure["requests"]).read_text(encoding="utf-8")), [])
        self.assertEqual(json.loads((root / failure["console_errors"]).read_text(encoding="utf-8")), [])
        with Image.open(root / failure["failure_screenshot"]) as image:
            self.assertEqual(image.size, (800, 600))

    def test_candidate_and_baseline_paths_cannot_escape_their_roots(self):
        manifest_path = self.artifacts / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outside_operations = self.root / "outside.jsonl"
        outside_operations.write_text("{}\n", encoding="utf-8")
        manifest["attempts"][0]["operations"] = "../outside.jsonl"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the evidence root"):
            validate_candidate(self.artifacts)

        write_valid_ui_integrity_artifact(self.artifacts)
        self.accept()
        baseline_manifest_path = self.baselines / "manifest.json"
        baseline = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
        outside_image = self.root / "outside.png"
        Image.new("RGB", (1920, 1080), (1, 2, 3)).save(outside_image, format="PNG")
        baseline["screenshots"][0]["path"] = "../outside.png"
        baseline_manifest_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the evidence root"):
            validate(self.artifacts, self.baselines)

    def test_declared_node_action_manifest_matches_the_product_toolbar(self):
        """#176: the declared selection-action list must match what the product renders."""
        self.assertEqual(list(EXPECTED_NODE_ACTION_IDS), product_toolbar_action_ids())

    def test_fixture_actual_action_ids_are_read_from_the_product_toolbar(self):
        """#176: evidence must not compare the declared list against itself."""
        drifted = [action for action in product_toolbar_action_ids() if action != "detail"]
        with mock.patch(
            "tests.ui_integrity_fixture.product_node_action_ids", return_value=drifted
        ):
            write_valid_ui_integrity_artifact(self.artifacts)
        manifest = json.loads((self.artifacts / "manifest.json").read_text(encoding="utf-8"))
        geometry_path = self.artifacts / manifest["attempts"][0]["geometry"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        scene = next(item for item in geometry if item["scene"] == "canvas-composer")
        self.assertEqual(scene["selectionActions"]["actualActionIds"], drifted)
        self.assertNotEqual(
            scene["selectionActions"]["expectedActionIds"],
            scene["selectionActions"]["actualActionIds"],
        )
        with self.assertRaisesRegex(ValueError, "exact selection-action set"):
            validate_candidate(self.artifacts)


if __name__ == "__main__":
    unittest.main()
