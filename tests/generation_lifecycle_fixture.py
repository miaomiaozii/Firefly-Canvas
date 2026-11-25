"""Create fast generation-lifecycle evidence for quality-gate contract tests."""

from __future__ import annotations

import argparse
import io
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from tests.native_input_fixture import selector_owner_click_evidence


DEFAULT_VARIANT = None
VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
ASSET_ID = "a" * 64
OUTPUT_ID = "output-" + "c" * 32
DELETION_ID = "deletion-" + "d" * 32


# Only the encoded pixels are shared; each candidate still gets its own files.
@lru_cache(maxsize=len(VIEWPORTS))
def _generation_screenshot_png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (15, 23, 42))
    ImageDraw.Draw(image).rectangle((10, 10, 100, 100), fill=(37, 99, 235))
    with io.BytesIO() as output:
        image.save(output, format="PNG")
        return output.getvalue()


def _operation(session: str, sequence: int, selector: str, dialog: bool) -> dict:
    return {
        "session": session,
        "sequence": sequence,
        "action": "click",
        "selector": selector,
        **selector_owner_click_evidence(),
        "observed_button": 0,
        "point": {"x": 20, "y": 20},
        "observed_point": {"x": 20, "y": 20},
        "hit": "button.fixture",
        "observed_target": "button.fixture",
        "focus_after": "button.fixture",
        "unobscured": True,
        "dialog_accepted": dialog,
    }


def build_fixture(root: Path, variant: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lifecycle = {
        "project_id": "project-fixture",
        "task_id": "task-new",
        "pending_history_id": "pending-generation-task-new",
        "result_node_id": "result-shared",
        "record_history_id": "history-record-only",
        "original_history_id": "history-original",
        "original_asset_id": ASSET_ID,
        "original_output_id": OUTPUT_ID,
        "deletion_id": DELETION_ID,
        "downstream_asset_id": "b" * 64,
        "original_sha256": ASSET_ID,
        "retained_media_sha256": ASSET_ID,
        "restored_sha256": ASSET_ID,
        "original_bytes": 86,
        "restored_bytes": 86,
        "retention_ms": 604_800_000,
        "phase_sequence": ["queued", "provider_generating", "receiving", "persisting"],
        "record_cancel_preserved": True,
        "record_removed_only": True,
        "original_retained_after_record_delete": True,
        "task_retained_after_original_delete": True,
        "downstream_retained_after_original_delete": True,
        "media_retained_after_occurrence_delete": True,
        "output_identity_restored": True,
        "restart_exposed_restore": True,
        "trash_empty_after_restore": True,
        "task_retained_after_restore": True,
        "downstream_retained_after_restore": True,
        "unrelated_retained_after_restore": True,
    }
    attempts = []
    for width, height in VIEWPORTS:
        prefix = f"viewport-{width}x{height}"
        folder = root / prefix
        folder.mkdir()
        operations = [
            _operation("before-restart", 1, "#smartHistoryToggle", False),
            _operation(
                "before-restart",
                2,
                '[data-canvas-history-id="history-record-only"] [data-history-delete="record"]',
                False,
            ),
            _operation("before-restart", 3, '.ff-dialog-card[role="alertdialog"] .ff-dialog-actions button:first-child', False),
            _operation("before-restart", 4, '[data-canvas-history-id="history-record-only"] [data-history-delete="record"]', False),
            _operation("before-restart", 5, '.ff-dialog-card[role="alertdialog"] .ff-dialog-btn-primary', False),
            _operation(
                "before-restart",
                6,
                f'[data-canvas-history-id="{lifecycle["original_history_id"]}"] [data-history-delete-original="{lifecycle["original_output_id"]}"]',
                False,
            ),
            _operation("after-restart", 1, "#smartHistoryToggle", False),
            _operation("after-restart", 2, "#smartTrashOpenBtn", False),
            _operation(
                "after-restart",
                3,
                f'[data-trash-asset="{ASSET_ID}"]'
                f'[data-trash-deletion="{DELETION_ID}"] [data-trash-restore]',
                False,
            ),
        ]
        (folder / "operations.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in operations),
            encoding="utf-8",
        )
        requests = [
            {
                "session": "before-restart",
                "method": "Network.requestWillBeSent",
                "url": "http://127.0.0.1/api/project-library/project-fixture/history/delete",
            },
            {
                "session": "before-restart",
                "url": "http://127.0.0.1/api/project-library/project-fixture/history/delete",
                "status": 200,
            },
            {
                "session": "before-restart",
                "url": "http://127.0.0.1/api/project-library/project-fixture/original/delete",
                "status": 200,
            },
            {
                "session": "after-restart",
                "url": "http://127.0.0.1/api/project-library/project-fixture/original/restore",
                "status": 200,
            },
        ]
        (folder / "requests.json").write_text(json.dumps(requests), encoding="utf-8")
        (folder / "console-errors.json").write_text("[]\n", encoding="utf-8")
        screenshots = []
        png = _generation_screenshot_png(width, height)
        for index in range(1, 6):
            name = f"0{index}-evidence.png"
            (folder / name).write_bytes(png)
            screenshots.append(f"{prefix}/{name}")
        attempts.append(
            {
                "viewport": [width, height],
                "status": "passed",
                "operations": f"{prefix}/operations.jsonl",
                "requests": f"{prefix}/requests.json",
                "console_errors": f"{prefix}/console-errors.json",
                "screenshots": screenshots,
                "lifecycle": dict(lifecycle),
            }
        )
    manifest = {
        "schema_version": 1,
        "journey": "generation-lifecycle",
        "status": "passed",
        "environment": {
            "os": "Windows",
            "browser": {
                "name": "Google Chrome",
                "channel": "stable",
                "version": "Chrome/fixture",
            },
            "dpr": 1,
            "color_scheme": "light",
        },
        "attempts": attempts,
        "first_failure": None,
    }
    if variant == "bad-retention":
        manifest["attempts"][0]["lifecycle"]["retention_ms"] = 1
    elif variant == "bad-hash":
        manifest["attempts"][0]["lifecycle"]["restored_sha256"] = "0" * 64
    elif variant == "bad-output-identity":
        manifest["attempts"][0]["lifecycle"]["original_output_id"] = "output-index-0"
    elif variant == "short-phase-chain":
        manifest["attempts"][0]["lifecycle"]["phase_sequence"].pop()
    elif variant == "missing-dialog":
        path = root / "viewport-1920x1080" / "operations.jsonl"
        items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        items[4]["selector"] = ".missing-confirm-button"
        path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    elif variant == "missing-cancel":
        manifest["attempts"][0]["lifecycle"]["record_cancel_preserved"] = False
    elif variant == "duplicate-record-request":
        path = root / "viewport-1920x1080" / "requests.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        items.append(dict(items[0]))
        path.write_text(json.dumps(items), encoding="utf-8")
    elif variant == "destructive-api":
        path = root / "viewport-1920x1080" / "requests.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        items[0]["url"] += "-with-media"
        path.write_text(json.dumps(items), encoding="utf-8")
    elif variant is not None:
        raise ValueError(f"unknown lifecycle fixture variant: {variant}")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def write_generation_lifecycle_runner(repo_root: Path, variant: str | None = None) -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    frozen_click = repr(selector_owner_click_evidence())
    source = source.replace(
        "from tests.native_input_fixture import selector_owner_click_evidence\n",
        "def selector_owner_click_evidence():\n"
        f"    return dict({frozen_click})\n",
        1,
    )
    source = source.replace("DEFAULT_VARIANT = None", f"DEFAULT_VARIANT = {variant!r}", 1)
    (repo_root / "tests" / "generation_lifecycle_browser.py").write_text(source, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    arguments = parser.parse_args()
    build_fixture(arguments.artifacts_dir, DEFAULT_VARIANT)
