"""Exercise project image and video generation in real headless Chrome.

The browser uses a temporary project library and in-process fake adapters.  It
still goes through the shipped composer, project task endpoints, task polling,
media archiving, and canvas/history updates.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import urllib.parse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "generation_browser_client.js"
VIEWPORTS = [(1408, 710), (500, 749)]


def tiny_png_base64() -> str:
    output = io.BytesIO()
    Image.new("RGB", (5, 3), (38, 190, 133)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def small_structural_mp4_base64() -> str:
    def box(kind: bytes, payload: bytes) -> bytes:
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    hdlr = box(b"hdlr", b"\x00\x00\x00\x00\x00\x00\x00\x00vide\x00\x00\x00\x00")
    content = ftyp + box(b"moov", box(b"trak", box(b"mdia", hdlr))) + box(b"mdat", b"\x00")
    return base64.b64encode(content).decode("ascii")


IMAGE_PROVIDERS = [
    {
        "id": "browser-image",
        "name": "Browser image adapter",
        "enabled": True,
        "protocol": "openai",
        "image_models": ["browser-image-model"],
        "image_model_capabilities": {
            "browser-image-model": {
                "sizes": ["1024x1024"],
                "aspect_ratios": ["1:1"],
                "resolutions": ["1k"],
                "max_reference_images": 0,
            }
        },
    },
    {
        "id": "browser-video",
        "name": "Browser video adapter",
        "enabled": True,
        "protocol": "openai",
        "video_models": ["browser-video-model"],
        "video_model_capabilities": {
            "browser-video-model": {
                "sizes": ["1280x720"],
                "aspect_ratios": ["16:9"],
                "resolutions": ["720p"],
                "qualities": ["high"],
                "min_duration": 5,
                "max_duration": 5,
                "max_image_references": 0,
                "max_video_references": 0,
                "max_audio_references": 0,
            }
        },
    },
]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Generation browser project")
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "browser-image-node",
                    "type": "smart-image",
                    "x": 80,
                    "y": 120,
                    "w": 260,
                    "h": 220,
                    "images": [],
                    "runSettings": {
                        "engine": "api",
                        "apiKind": "image",
                        "routingMode": "manual",
                        "provider_id": "browser-image",
                        "model": "browser-image-model",
                        "ratio": "square",
                        "resolution": "1k",
                        "quality": "high",
                        "count": 1,
                    },
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "browser-video-node",
                    "type": "smart-image",
                    "x": 430,
                    "y": 120,
                    "w": 260,
                    "h": 220,
                    "images": [],
                    "runSettings": {
                        "engine": "api",
                        "apiKind": "video",
                        "videoRoutingMode": "manual",
                        "videoProvider": "browser-video",
                        "videoModel": "browser-video-model",
                        "videoAspect": "16:9",
                        "videoResolution": "720p",
                        "videoDuration": 5,
                        "videoQuality": "high",
                        "videoGenerateAudio": False,
                        "videoCameraFixed": False,
                    },
                },
            },
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    module.load_api_providers = lambda: IMAGE_PROVIDERS
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-test-key"
    module.AI_BASE_URL = "https://browser-generation.invalid"
    image_value = tiny_png_base64()
    video_value = small_structural_mp4_base64()

    async def fake_image(*_args, **_kwargs):
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    async def fake_video(*_args, **_kwargs):
        return ([{"type": "b64", "value": video_value, "mime_type": "video/mp4"}], {"id": "browser-video"})

    module.generate_ai_image = fake_image
    module.generate_project_video = fake_video
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    import subprocess

    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-generation-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": project_id, "browserTest": "1"})
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    process = subprocess.Popen(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        return server.wait_for_result(before, timeout=120)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        harness.remove_profile(profile)


def main():
    browser = harness.find_browser()
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-generation-browser-library-") as root_text:
        root = Path(root_text)
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(root / f"projects-{index}", snapshot_delay=0)
            project_id = seed_store(store)
            app, module = load_app(root / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
                result["pythonChecks"] = {
                    "imageAssetArchived": any(
                        item.get("kind") == "image"
                        for item in store.list_media(project_id)
                        if item.get("source", {}).get("type") == "online_generation"
                    ),
                    "videoAssetArchived": any(
                        item.get("kind") == "video"
                        for item in store.list_media(project_id)
                        if item.get("source", {}).get("type") == "online_generation"
                    ),
                }
                result["pass"] = result.get("pass") and all(result["pythonChecks"].values())
                if not result.get("pass"):
                    raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
            finally:
                server.close()
                try:
                    module.FIREFLY_PROJECT_STORE.close()
                except Exception:
                    pass
                if module.FIREFLY_PROJECT_STORE is not store:
                    store.close()
    summary = {
        "surface": "project-generation",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
