from __future__ import annotations

from pathlib import Path

from tools.native_input_evidence import selector_owner_click_evidence


def write_edge_compatibility_runner(root: Path) -> Path:
    """Write a fake `edge_compatibility_browser.py` into the quality-gate
    fixture repository so the release-gate step can run offline."""
    path = root / "tests" / "edge_compatibility_browser.py"
    source = '''import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument("--artifacts-dir", type=Path, required=True)
args = parser.parse_args()
root = args.artifacts_dir
root.mkdir(parents=True, exist_ok=True)

image = Image.new("RGB", (40, 30), (250, 250, 250))
ImageDraw.Draw(image).rectangle((6, 6, 34, 24), fill=(10, 90, 160))
shots = []
for index in range(2):
    screenshot = root / f"{index:02d}-edge.png"
    image.save(screenshot)
    shots.append(screenshot.name)

operations = [
    {"action": "keyboard", "key": "Enter", "trusted": True, "observed_type": "keyup"},
    {"action": "click", "selector": "#runBtn", **__CLICK_EVIDENCE__},
]
(root / "operations.jsonl").write_text(
    "".join(json.dumps(item) + "\\n" for item in operations), encoding="utf-8"
)
(root / "requests.json").write_text("[]", encoding="utf-8")
(root / "console-errors.json").write_text("[]", encoding="utf-8")

manifest = {
    "schema_version": 1,
    "journey": "edge-compatibility",
    "status": "passed",
    "environment": {
        "os": "Windows",
        "browser": {"name": "Microsoft Edge", "channel": "stable", "version": "fixture"},
        "dpr": 1,
        "color_scheme": "light",
    },
    "attempt": {
        "status": "passed",
        "viewport": [1440, 900],
        "steps": [
            {"name": "edge-launched", "status": "passed"},
            {"name": "edge-canvas-interacted", "status": "passed"},
            {"name": "edge-generation-succeeded", "status": "passed"},
        ],
        "lifecycle": {
            "provider": "fake-edge-generation",
            "model": "gemini-2.5-flash-image",
            "prompt": "edge compatibility generation",
            "aspect_ratio": "1:1",
            "resolution": "2K",
            "generation_succeeded": True,
            "provider_calls": 1,
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
