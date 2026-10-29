"""Native-browser evidence runner for GitHub issues #207 and #208 (回收区预览).

#208 — the trash preview must be able to serve the generated ORIGINAL, not
       only the preview image: ``?view=original`` returns the original bytes
       and the response header ``X-Canvas-Media-View`` says which one was
       actually served (``original`` / ``preview`` fallback).
#207 — the trash preview must open in the unified 「生成详情」 panel
       (``#smartHistoryLightbox``, the #87 component shared with canvas
       thumbnails / image-history drawer) with a 恢复 action — not the
       retired custom ``#smartTrashPreview`` panel.

Journey (real headless Chrome via CDP against an in-process ASGI server):
  1. Seed a project with one smart-image node whose media is a generated
     original (``source.type=online_generation``) plus a preview file, so the
     real deletion flow can move it to the trash (回收区).
  2. In the page, delete it through the app's own ``deleteProjectOriginalWide``
     action (the UI's 项目范围删原图 path, smart-canvas.js) — no store
     shortcuts.
  3. Open the trash panel (``#smartTrashOpenBtn``), wait for the rendered
     item, screenshot.
  4. Click the item's thumbnail → the preview opens in
     ``#smartHistoryLightbox``; assert the unified panel is open, the custom
     panel is absent from the DOM, the image requests the original
     (``view=original``), and screenshot.
  5. Byte-level checks in the page: ``?view=original`` serves exactly the
     original PNG bytes with header ``original``; ``?view=preview`` serves
     the preview file the trash record references (compared against the
     actual file on disk, since the app generates previews as
     ``{asset_id}-{width}.webp``) and it is not the original bytes.
  6. Close the panel; assert the injected 恢复 button is cleaned up.
  7. Server-side: ``/api/download-output`` with the trash preview URL (the
     panel's 下载原图 path) serves the original bytes and names the download
     after the trash record, not the URL tail ``preview``.

Evidence contract: writes ``manifest.json`` and ``screenshots/*.png`` under
``--artifacts-dir``. Use a FRESH ``--artifacts-dir`` for every run (existing
dir -> SystemExit).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser

CLIENT_TEMPLATE = ROOT / "tests" / "trash_preview_browser_client.js"
VIEWPORT = (1440, 900)
ORIGINAL_NAME = "trash-original.png"
PREVIEW_NAME = "trash-preview.png"
ORIGINAL_SIZE = (192, 120)


def make_original_png() -> bytes:
    """Distinctive original: magenta block + green band + ORIGINAL text."""
    img = Image.new("RGB", ORIGINAL_SIZE, (206, 47, 96))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 84, ORIGINAL_SIZE[0], ORIGINAL_SIZE[1]], fill=(22, 163, 74))
    draw.text((8, 8), "ORIGINAL", fill=(255, 255, 255))
    draw.text((8, 92), ORIGINAL_NAME, fill=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def make_preview_png() -> bytes:
    """Visibly different preview: blue block + PREVIEW text."""
    img = Image.new("RGB", ORIGINAL_SIZE, (30, 94, 200))
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), "PREVIEW", fill=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def seed_project(store: ProjectStore, store_root: Path) -> tuple[str, str]:
    project = store.create_project("Trash preview browser")
    pid = project["id"]
    media = store.import_media(
        pid,
        make_original_png(),
        ORIGINAL_NAME,
        "image/png",
        {"type": "online_generation", "name": ORIGINAL_NAME},
    )
    asset_id = str(media["asset_id"])
    store.apply_mutations(
        pid,
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "trash-gen-node",
                    "type": "smart-image",
                    "x": 80,
                    "y": 80,
                    "w": 340,
                    "h": 220,
                    "images": [{"asset_id": asset_id, "kind": "image", "name": ORIGINAL_NAME}],
                },
            }
        ],
        base_revision=0,
    )
    previews = store_root / pid / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{asset_id}-preview.png").write_bytes(make_preview_png())
    store.flush(pid)
    return pid, asset_id


def console_errors(browser: NativeBrowser) -> list[dict]:
    browser.cdp.drain(0.5)
    entries = []
    for event in list(browser.cdp.events):
        if event.get("method") != "Log.entryAdded":
            continue
        params = event.get("params") or {}
        entry = params.get("entry") or {}
        if entry.get("level") in ("error", "warning"):
            entries.append({"level": entry.get("level"), "text": str(entry.get("text") or "")[:500]})
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-Chrome evidence for issues #207/#208 (trash preview).")
    parser.add_argument("--artifacts-dir", type=Path, required=True, help="fresh dir for manifest + screenshots")
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    if artifacts.exists():
        raise SystemExit(f"--artifacts-dir must be fresh (new dir): {artifacts}")
    artifacts.mkdir(parents=True)
    shots_dir = artifacts / "screenshots"
    shots_dir.mkdir()

    original_bytes = make_original_png()
    template = (
        CLIENT_TEMPLATE.read_text(encoding="utf-8")
        .replace("__PROJECT_ID__", "PROJECT_ID_PLACEHOLDER_GUARD")
    )
    original_b64 = base64.b64encode(original_bytes).decode("ascii")

    chrome = find_browser()
    result = {
        "issues": ["#207", "#208"],
        "browser": str(chrome),
        "viewport": list(VIEWPORT),
        "stages": {},
        "download": None,
        "console": [],
        "errors": [],
        "pass": False,
    }

    with tempfile.TemporaryDirectory(prefix="firefly-trashpreview-projects-") as root_text:
        store_root = Path(root_text)
        store = ProjectStore(store_root, snapshot_delay=0)
        try:
            pid, asset_id = seed_project(store, store_root)
            template = (
                template.replace("PROJECT_ID_PLACEHOLDER_GUARD", pid)
                .replace("__ASSET_ID__", asset_id)
                .replace("__ORIGINAL_B64__", original_b64)
            )
            app = harness.load_production_app(Path(root_text) / "runtime", store)
            with AsgiBrowserServer(app) as server:
                browser = NativeBrowser.launch(chrome)
                try:
                    browser.set_environment(VIEWPORT)
                    query = urllib.parse.urlencode({"project": pid, "browserTest": "1"})
                    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(pid)}&{query}"
                    browser.navigate(url)
                    result["stages"]["boot"] = browser.evaluate(template, await_promise=True)
                    for stage in ("s1_boot", "s2_delete", "s3_open_panel"):
                        result["stages"][stage] = browser.evaluate(
                            f"window.__trashPreviewDriver.{stage}()", await_promise=True
                        )
                    browser.park_pointer()
                    browser.capture_screenshot(shots_dir / "1-trash-panel.png")
                    result["stages"]["s4_preview"] = browser.evaluate(
                        "window.__trashPreviewDriver.s4_preview()", await_promise=True
                    )
                    browser.park_pointer()
                    browser.capture_screenshot(shots_dir / "2-generation-detail-original.png")
                    for stage in ("s5_bytes", "s6_close"):
                        result["stages"][stage] = browser.evaluate(
                            f"window.__trashPreviewDriver.{stage}()", await_promise=True
                        )

                    # Server-side: the panel's 下载原图 path goes through /api/download-output.
                    trash_url = f"/api/project-library/{pid}/trash/media/{asset_id}/preview?view=original"
                    query = urllib.parse.urlencode({"url": trash_url, "name": "preview.png"})
                    with urllib.request.urlopen(server.base_url + "/api/download-output?" + query, timeout=30) as resp:
                        download_status = resp.status
                        download_disposition = resp.headers.get("Content-Disposition", "")
                        download_body = resp.read()
                    result["download"] = {
                        "status": download_status,
                        "contentDisposition": download_disposition,
                        "bytesMatchOriginal": download_body == original_bytes,
                    }

                    # Server-side: ?view=preview must serve the preview file the trash
                    # record references (disk comparison vs the bytes the page fetched,
                    # because the endpoint requires a session cookie), not the original.
                    trash_index = json.loads((store_root / pid / "trash" / "index.json").read_text(encoding="utf-8"))
                    trash_item = next((i for i in trash_index.get("items", []) if i.get("asset_id") == asset_id), {})
                    preview_names = [n for n in (trash_item.get("preview_filenames") or []) if isinstance(n, str) and n.strip()]
                    disk_preview = None
                    disk_name = preview_names[0] if preview_names else None
                    if disk_name:
                        candidate = store_root / pid / "trash" / "previews" / disk_name
                        if candidate.is_file():
                            disk_preview = candidate.read_bytes()
                    preview_b64_page = str(((s5_bytes_stage := result["stages"]["s5_bytes"]).get("preview") or {}).get("bytesB64") or "")
                    preview_stage = (s5_bytes_stage.get("preview") or {})
                    result["previewCheck"] = {
                        "status": preview_stage.get("status"),
                        "header": preview_stage.get("header"),
                        "diskFile": disk_name,
                        "bytesMatchDisk": (
                            disk_preview is not None
                            and bool(preview_b64_page)
                            and base64.b64encode(disk_preview).decode("ascii") == preview_b64_page
                        ),
                        "bytesMatchOriginal": preview_stage.get("bytesMatchOriginal"),
                    }
                finally:
                    result["console"] = console_errors(browser)
                    browser.close()
        finally:
            store.close()

    checks: list[tuple[str, bool]] = []

    def check(name: str, value: bool, actual=None) -> None:
        checks.append((name, bool(value)))
        if not value:
            result["errors"].append(f"{name}: actual={json.dumps(actual, ensure_ascii=False)[:400] if actual is not None else None}")

    s1 = result["stages"].get("s1_boot") or {}
    s2 = result["stages"].get("s2_delete") or {}
    s3 = result["stages"].get("s3_open_panel") or {}
    s4 = result["stages"].get("s4_preview") or {}
    s5 = result["stages"].get("s5_bytes") or {}
    s6 = result["stages"].get("s6_close") or {}
    dl = result["download"] or {}

    check("boot: driver installed", (result["stages"].get("boot") or {}).get("installed") is True, result["stages"].get("boot"))
    check("s1: canvas node + image loaded", s1.get("node") is True and bool(s1.get("imgNatural")), s1)
    check("s2: trash button visible after delete", s2.get("trashBtnVisible") is True, s2)
    check("s2: node image removed", s2.get("nodeImagesLeft") == 0, s2)
    check("s3: trash item rendered", bool(s3.get("itemCount")), s3)
    check("s3: item thumb uses preview image", "view=preview" in str(s3.get("thumbSrc") or ""), s3)
    check("s4: unified generation-detail panel open", s4.get("lightboxOpen") is True, s4)
    check("s4: custom trash panel absent from DOM", s4.get("customPanelAbsent") is True, s4)
    img_src = str(s4.get("imgSrc") or "")
    check("s4: detail image requests original (view=original)", "/trash/media/" in img_src and "view=original" in img_src, s4)
    check("s4: restore button present in detail actions", s4.get("restoreBtnPresent") is True, s4)
    original_probe = s5.get("original") or {}
    preview_probe = s5.get("preview") or {}
    check("s5: view=original -> 200 + header original", original_probe.get("status") == 200 and original_probe.get("header") == "original", original_probe)
    check("s5: view=original bytes == original PNG", original_probe.get("bytesMatchOriginal") is True, original_probe)
    check("s5: view=preview -> 200 + header preview", preview_probe.get("status") == 200 and preview_probe.get("header") == "preview", preview_probe)
    check("s5: view=preview not original bytes", preview_probe.get("bytesMatchOriginal") is False, preview_probe)
    pcheck = result.get("previewCheck") or {}
    check("preview: serves the trash record's preview file from disk", pcheck.get("bytesMatchDisk") is True and bool(pcheck.get("diskFile")), pcheck)
    check("preview: not the original bytes", pcheck.get("bytesMatchOriginal") is False, pcheck)
    check("s6: panel closed + restore button cleaned up", s6.get("closed") is True and s6.get("restoreBtnCleaned") is True, s6)
    check("download: 200", dl.get("status") == 200, dl)
    check("download: bytes == original PNG", dl.get("bytesMatchOriginal") is True, dl)
    disposition = str(dl.get("contentDisposition") or "")
    check("download: filename from trash record", ORIGINAL_NAME in disposition and "preview.png" not in disposition, dl)
    check("console: no page errors", not any(item["level"] == "error" for item in result["console"]), result["console"])

    result["checks"] = [{"name": name, "pass": passed} for name, passed in checks]
    result["pass"] = all(passed for _, passed in checks)

    (artifacts / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("issues", "pass", "errors", "checks")}, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
