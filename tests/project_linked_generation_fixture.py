from __future__ import annotations

from pathlib import Path

from tools.native_input_evidence import selector_owner_click_evidence


def write_project_linked_generation_runner(root: Path) -> Path:
    """Write a fake `project_linked_generation_native_browser.py` into the
    quality-gate fixture repository so the gate step can run offline."""
    path = root / "tests" / "project_linked_generation_native_browser.py"
    source = '''import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument("--artifacts-dir", type=Path, required=True)
args = parser.parse_args()
root = args.artifacts_dir
root.mkdir(parents=True, exist_ok=True)

image = Image.new("RGB", (40, 30), (240, 240, 240))
ImageDraw.Draw(image).rectangle((8, 8, 32, 22), fill=(30, 60, 120))
shots = []
for index in range(2):
    screenshot = root / f"{index:02d}-linked.png"
    image.save(screenshot)
    shots.append(screenshot.name)

operations = [
    {"action": "keyboard", "key": "Enter", "trusted": True, "observed_type": "keyup"},
    {"action": "click", "selector": "#newProjectBtn", **__CLICK_EVIDENCE__},
]
(root / "operations.jsonl").write_text(
    "".join(json.dumps(item) + "\\n" for item in operations), encoding="utf-8"
)
(root / "requests.json").write_text('[{"url": "http://localhost/", "status": 200}]', encoding="utf-8")
(root / "console-errors.json").write_text("[]", encoding="utf-8")

manifest = {
    "schema_version": 1,
    "journey": "project-linked-generation",
    "status": "passed",
    "environment": {
        "os": "Windows",
        "browser": {"name": "Google Chrome", "channel": "stable", "version": "fixture"},
        "dpr": 1,
        "color_scheme": "light",
    },
    "attempt": {
        "status": "passed",
        "viewport": [1424, 900],
        "steps": [
            {"name": "linked-inputs-saved", "status": "passed"},
            {"name": "project-reopened", "status": "passed"},
            {"name": "linked-generation-succeeded", "status": "passed"},
            {"name": "provider-input-verified", "status": "passed"},
        ],
        "lifecycle": {
            "provider": "fake-linked-generation",
            "prompt": "upstream first\\n\\nupstream second\\n\\nown prompt",
            "model": "gemini-2.5-flash-image",
            "aspect_ratio": "4:3",
            "resolution": "2K",
            "seed": 19,
            "ui_request_reference_asset_ids": ["first", "second"],
            "expected_reference_asset_ids": ["first", "second"],
            "reference_asset_ids": ["first", "second", "first"],
            "duplicate_reference_preserved": True,
            "ordered_references_preserved": True,
            "all_references_are_data_urls": True,
        },
        "operations": "operations.jsonl",
        "requests": "requests.json",
        "console_errors": "console-errors.json",
        "screenshots": shots,
    },
    "first_failure": None,
}
(root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
print(json.dumps(manifest))
'''
    path.write_text(
        source.replace("__CLICK_EVIDENCE__", repr(selector_owner_click_evidence())),
        encoding="utf-8",
    )
    return path
