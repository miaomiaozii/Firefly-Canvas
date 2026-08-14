"""Validate and explicitly accept Issue #34 desktop UI integrity evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINES = ROOT / "tests" / "baselines" / "ui-integrity"
EXPECTED_VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
EXPECTED_SCENES = (
    "canvas-composer",
    "composer-size",
    "composer-flow",
    "create-menu",
    "history",
    "generation-detail",
    "settings",
    "shortcuts",
)
REQUIRED_FLAGS = (
    "no_horizontal_scroll",
    "critical_surfaces_in_viewport",
    "critical_controls_unobscured",
    "top_chrome_single_row",
    "composer_parameters_reachable",
    "history_meaning_reachable",
    "current_selection_visible",
    "selection_actions_reachable",
    "history_image_first_fixed",
    "generation_detail_complete",
    "prompt_flow_context_complete",
    "composer_world_anchor_and_wheel",
    "history_detail_zoom_and_escape",
    "prompt_flow_media_adaptive",
    "settings_width_used",
    "icon_controls_named",
    "stable_tooltips",
    "explicit_overflow_policy",
    "compact_geometry_contract",
)
PROVIDER_MARKERS = ("/image-generation-tasks", "/api/generate", "/api/canvas-llm")
# Chrome may rerasterize a composited surface by a few channel values despite identical
# geometry. Exact hashes remain the fast path. This fallback caps total scope, error
# energy, strong outliers, and coherent filled regions so layout/style changes still fail.
SCREENSHOT_SAMPLE_SCALE = 0.5
SCREENSHOT_MAX_CHANGED_RATIO = 0.20
SCREENSHOT_MAX_MEAN_PEAK_DELTA = 0.30
SCREENSHOT_MAX_STRONG_RATIO = 0.012
SCREENSHOT_MAX_VERY_STRONG_RATIO = 0.0015
SCREENSHOT_MAX_PEAK_DELTA = 48
SCREENSHOT_STRONG_DELTA = 4
SCREENSHOT_VERY_STRONG_DELTA = 16
SCREENSHOT_MIN_COHERENT_COMPONENT = 200
SCREENSHOT_MIN_LINEAR_COMPONENT = 40
SCREENSHOT_MAX_COMPONENT_DENSITY = 0.35
SCREENSHOT_EDGE_RASTER_MAX_DELTA = 16
SCREENSHOT_GEOMETRY_DELTA = 32
SCREENSHOT_GEOMETRY_MIN_LENGTH = 20
SCREENSHOT_GEOMETRY_MAX_THICKNESS = 2
SCREENSHOT_GEOMETRY_MIN_OVERLAP = 0.80
SCREENSHOT_GEOMETRY_MAX_SEPARATION = 96
EXPECTED_NODE_ACTION_IDS = (
    "preview",
    "crop",
    "outpaint",
    "mask",
    "brush",
    "grid",
    "send-ps",
    "download",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def screenshot_key(viewport: tuple[int, int], scene: str) -> str:
    return f"{viewport[0]}x{viewport[1]}/{scene}.png"


def artifact_path(root: Path, relative: object, label: str) -> Path:
    resolved_root = root.resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{label} is outside the evidence root") from error
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {relative}")
    return candidate


def _validate_selection_actions(geometry: list[dict]) -> None:
    scenes = {str(item.get("scene") or ""): item for item in geometry if isinstance(item, dict)}
    expected = list(EXPECTED_NODE_ACTION_IDS)
    for scene_name in ("canvas-composer", "composer-size"):
        scene = scenes.get(scene_name) or {}
        selection = scene.get("selectionActions")
        if not isinstance(selection, dict):
            raise ValueError(f"{scene_name} geometry is missing the selection-actions contract")
        if selection.get("expectedActionIds") != expected or selection.get("actualActionIds") != expected:
            raise ValueError(f"{scene_name} geometry does not cover the exact selection-action set")
        actions = selection.get("actions")
        if not isinstance(actions, list) or [item.get("action") for item in actions if isinstance(item, dict)] != expected:
            raise ValueError(f"{scene_name} geometry has incomplete selection-action evidence")
        if not all(
            isinstance(item, dict)
            and item.get("visible") is True
            and item.get("inViewport") is True
            and item.get("hit") is True
            for item in actions
        ):
            raise ValueError(f"{scene_name} has an unreachable selection action")
        if (
            selection.get("allReachable") is not True
            or scene.get("selectionMenuAvoidsName") is not True
            or scene.get("promptSurfacesOpaque") is not True
            or scene.get("decorativeEnglishAbsent") is not True
            or scene.get("passed") is not True
        ):
            raise ValueError(f"{scene_name} did not fail closed on its selection, image-name, or prompt-surface contract")


def _rect_from(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    keys = ("left", "top", "right", "bottom", "width", "height")
    try:
        rect = {key: float(value[key]) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    if any(number != number or abs(number) == float("inf") for number in rect.values()):
        return None
    if rect["right"] < rect["left"] or rect["bottom"] < rect["top"]:
        return None
    if abs(rect["width"] - (rect["right"] - rect["left"])) > 1.5:
        return None
    if abs(rect["height"] - (rect["bottom"] - rect["top"])) > 1.5:
        return None
    return rect


def _rect_contains(outer: dict[str, float], inner: dict[str, float], tolerance: float = 1.5) -> bool:
    return (
        inner["left"] >= outer["left"] - tolerance
        and inner["top"] >= outer["top"] - tolerance
        and inner["right"] <= outer["right"] + tolerance
        and inner["bottom"] <= outer["bottom"] + tolerance
        and inner["width"] > 0
        and inner["height"] > 0
    )


def _validate_history_preview_geometry(multi_output: dict) -> None:
    geometry = multi_output.get("previewGeometry")
    if not isinstance(geometry, dict):
        raise ValueError("history preview geometry is missing raw rectangles")
    viewport = _rect_from(geometry.get("viewportRect"))
    card = _rect_from(geometry.get("cardRect"))
    thumb = _rect_from(geometry.get("thumbRect"))
    gallery = _rect_from(geometry.get("galleryRect"))
    strip = _rect_from(geometry.get("stripRect"))
    previews = geometry.get("previews")
    if not all((viewport, card, thumb, gallery, strip)) or not isinstance(previews, list) or len(previews) != 8:
        raise ValueError("history preview geometry is incomplete")
    if not _rect_contains(viewport, card) or not _rect_contains(viewport, thumb):
        raise ValueError("history preview geometry is outside the viewport")
    if abs(thumb["height"] - 168) > 1 or thumb["width"] < 168:
        raise ValueError("history preview geometry shrank the main thumbnail")
    if not _rect_contains(thumb, gallery, 1.5):
        raise ValueError("history preview geometry does not fill the main thumbnail")
    if abs(gallery["width"] - (thumb["width"] - 2)) > 2 or abs(gallery["height"] - (thumb["height"] - 2)) > 2:
        raise ValueError("history preview geometry shrank the main gallery")
    if not _rect_contains(thumb, strip, 1.5):
        raise ValueError("history preview overlay is outside the main thumbnail")
    if strip["top"] >= gallery["bottom"] - 1 or strip["left"] < gallery["left"] - 7:
        raise ValueError("history preview overlay does not float over the main image")

    style = geometry.get("stripStyle")
    if not isinstance(style, dict) or not (
        all(float(style.get(key) or 0) <= 0.1 for key in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"))
        and all(float(style.get(key) or 0) <= 0.1 for key in ("borderTop", "borderRight", "borderBottom", "borderLeft"))
        and float(style.get("borderRadius") or 0) <= 0.1
        and float(style.get("backgroundAlpha") or 0) <= 0.001
        and str(style.get("boxShadow") or "none") in {"none", ""}
        and str(style.get("overflowX") or "") == "auto"
        and str(style.get("overflowY") or "") == "hidden"
    ):
        raise ValueError("history preview overlay retains an outer frame")
    try:
        if float(geometry.get("stripZIndex", 0)) <= float(geometry.get("galleryZIndex", 0)):
            raise ValueError("history preview overlay is not above the main image")
    except (TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("history preview overlay"):
            raise
        raise ValueError("history preview geometry is missing z-index evidence") from error

    grid = geometry.get("grid")
    if not isinstance(grid, dict) or grid.get("columns") != 8 or grid.get("rows") != 1:
        raise ValueError("history preview geometry is not a single 8-up row")
    if abs(float(grid.get("columnGap") or 0) - 3) > 1:
        raise ValueError("history preview geometry changed the thumbnail spacing")
    if geometry.get("stripScrollable") is not True or geometry.get("stripWheelShift") is not True:
        raise ValueError("history preview strip is not scroll-reachable without a scrollbar")
    ordered = []
    for expected_index, item in enumerate(previews):
        if not isinstance(item, dict) or item.get("outputIndex") != expected_index or item.get("decoded") is not True:
            raise ValueError("history preview geometry has an undiscoverable output")
        rect = _rect_from(item.get("rect"))
        if rect is None or rect["width"] < 28 or rect["height"] < 23 or abs(rect["width"] - 30) > 1.5 or abs(rect["height"] - 25) > 1.5:
            raise ValueError("history preview geometry contains an out-of-bounds thumbnail")
        if str(item.get("ariaLabel") or "").find(str(expected_index + 1)) < 0 or "8" not in str(item.get("ariaLabel") or ""):
            raise ValueError("history preview geometry is missing output labels")
        ordered.append(rect)
    if max(item["top"] for item in ordered) - min(item["top"] for item in ordered) > 1.5:
        raise ValueError("history preview geometry has uneven grid rows")
    for left, right in zip(ordered, ordered[1:]):
        if abs((right["left"] - left["right"]) - 3) > 1.5:
            raise ValueError("history preview geometry changed the column spacing")

    scrollbars = geometry.get("scrollbars")
    if not isinstance(scrollbars, dict) or any(
        not isinstance(scrollbars.get(name), dict)
        or scrollbars[name].get("scrollbarWidth") != "none"
        or scrollbars[name].get("webkitDisplay") != "none"
        for name in ("list", "gallery", "strip")
    ):
        raise ValueError("history preview surfaces expose a visible scrollbar")


def _validate_detail_output_strip(detail: dict) -> None:
    style = detail.get("outputStripStyle")
    if not isinstance(style, dict):
        raise ValueError("generation-detail output strip is missing raw style evidence")
    try:
        frame_absent = (
            all(float(style.get(key) or 0) <= 0.1 for key in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"))
            and all(float(style.get(key) or 0) <= 0.1 for key in ("borderTop", "borderRight", "borderBottom", "borderLeft"))
            and float(style.get("borderRadius") or 0) <= 0.1
            and float(style.get("backgroundAlpha") or 0) <= 0.001
            and str(style.get("boxShadow") or "none") in {"none", ""}
            and style.get("overflowX") == "visible"
            and style.get("overflowY") == "visible"
        )
        grid = (
            style.get("columns") == 4
            and style.get("rows") == 2
            and abs(float(style.get("columnGap") or 0) - 7) <= 1
            and abs(float(style.get("rowGap") or 0) - 7) <= 1
        )
        scrollbar_hidden = style.get("scrollbarWidth") == "none" and style.get("webkitDisplay") == "none"
    except (TypeError, ValueError) as error:
        raise ValueError("generation-detail output strip has invalid raw style evidence") from error
    if not frame_absent:
        raise ValueError("generation-detail output strip retains an outer frame")
    if not grid:
        raise ValueError("generation-detail output strip is not a 4x2 grid")
    if not scrollbar_hidden:
        raise ValueError("generation-detail output strip exposes a visible scrollbar")


def _validate_scene_facts(geometry: list[dict]) -> None:
    scenes = {str(item.get("scene") or ""): item for item in geometry if isinstance(item, dict)}
    flow = (scenes.get("composer-flow") or {}).get("promptFlowContext")
    if not isinstance(flow, dict) or not (
        flow.get("centered") is True
        and flow.get("contextAbovePrompt") is True
        and flow.get("recentCount") == 2
        and flow.get("referenceCount") == 22
        and flow.get("promptOpaque") is True
        and flow.get("recentNoScroll") is True
        and flow.get("mediaAdaptive") is True
        and float(flow.get("mediaRatioRange") or 0) > 0.5
        and flow.get("sameEditor") is True
        and str(flow.get("promptText") or "").strip()
    ):
        raise ValueError("composer-flow geometry is missing the complete creative-context contract")

    composer_anchor = (scenes.get("composer-size") or {}).get("composerAnchor")
    if not isinstance(composer_anchor, dict) or not (
        float(composer_anchor.get("scale") or 0) > 0
        and abs(float(composer_anchor.get("gapWorld") or 0) - 14) <= 1
        and float(composer_anchor.get("centerDeltaWorld", 99)) <= 1
    ):
        raise ValueError("composer-size geometry is missing the world-anchored editor contract")

    history_scene = scenes.get("history") or {}
    history = history_scene.get("historyFacts")
    multi_output = history_scene.get("multiOutputFacts")
    before_assets = multi_output.get("outputAssetIdsBefore") if isinstance(multi_output, dict) else None
    before_output_ids = multi_output.get("outputIdsBefore") if isinstance(multi_output, dict) else None
    canvas_before_assets = multi_output.get("canvasAssetIdsBefore") if isinstance(multi_output, dict) else None
    canvas_before_output_ids = multi_output.get("canvasOutputIdsBefore") if isinstance(multi_output, dict) else None
    after_delete_assets = multi_output.get("outputAssetIdsAfterDelete") if isinstance(multi_output, dict) else None
    after_delete_output_ids = multi_output.get("outputIdsAfterDelete") if isinstance(multi_output, dict) else None
    canvas_after_delete_assets = multi_output.get("canvasAssetIdsAfterDelete") if isinstance(multi_output, dict) else None
    canvas_after_delete_output_ids = multi_output.get("canvasOutputIdsAfterDelete") if isinstance(multi_output, dict) else None
    detail_after_delete_assets = multi_output.get("detailAssetIdsAfterDelete") if isinstance(multi_output, dict) else None
    detail_after_delete_output_ids = multi_output.get("detailOutputIdsAfterDelete") if isinstance(multi_output, dict) else None
    after_restore_assets = multi_output.get("outputAssetIdsAfterRestore") if isinstance(multi_output, dict) else None
    after_restore_output_ids = multi_output.get("outputIdsAfterRestore") if isinstance(multi_output, dict) else None
    dom_restore_assets = multi_output.get("domAssetIdsAfterRestore") if isinstance(multi_output, dict) else None
    dom_restore_output_ids = multi_output.get("domOutputIdsAfterRestore") if isinstance(multi_output, dict) else None
    preview_restore_output_ids = multi_output.get("previewOutputIdsAfterRestore") if isinstance(multi_output, dict) else None
    canvas_restore_assets = multi_output.get("canvasAssetIdsAfterRestore") if isinstance(multi_output, dict) else None
    canvas_restore_output_ids = multi_output.get("canvasOutputIdsAfterRestore") if isinstance(multi_output, dict) else None
    canvas_dom_restore_assets = multi_output.get("canvasDomAssetIdsAfterRestore") if isinstance(multi_output, dict) else None
    canvas_dom_restore_output_ids = multi_output.get("canvasDomOutputIdsAfterRestore") if isinstance(multi_output, dict) else None
    deleted_asset = multi_output.get("deletedAssetId") if isinstance(multi_output, dict) else None
    deleted_output_id = multi_output.get("deletedOutputId") if isinstance(multi_output, dict) else None
    expected_remaining_assets = (
        before_assets[:3] + before_assets[4:]
        if isinstance(before_assets, list) and len(before_assets) == 8
        else None
    )
    expected_remaining_output_ids = (
        before_output_ids[:3] + before_output_ids[4:]
        if isinstance(before_output_ids, list) and len(before_output_ids) == 8
        else None
    )
    identity_order_preserved = bool(
        isinstance(before_assets, list)
        and isinstance(before_output_ids, list)
        and len(before_assets) == 8
        and len(before_output_ids) == 8
        and len(set(before_assets)) == 8
        and len(set(before_output_ids)) == 8
        and all(str(asset_id or "").strip() for asset_id in before_assets)
        and all(str(output_id or "").strip() for output_id in before_output_ids)
        and canvas_before_assets == before_assets
        and canvas_before_output_ids == before_output_ids
        and deleted_asset == before_assets[3]
        and deleted_output_id == before_output_ids[3]
        and after_delete_assets == expected_remaining_assets
        and after_delete_output_ids == expected_remaining_output_ids
        and canvas_after_delete_assets == expected_remaining_assets
        and canvas_after_delete_output_ids == expected_remaining_output_ids
        and detail_after_delete_assets == expected_remaining_assets
        and detail_after_delete_output_ids == expected_remaining_output_ids
        and after_restore_assets == before_assets
        and after_restore_output_ids == before_output_ids
        and dom_restore_assets == before_assets
        and dom_restore_output_ids == before_output_ids
        and preview_restore_output_ids == before_output_ids
        and canvas_restore_assets == before_assets
        and canvas_restore_output_ids == before_output_ids
        and canvas_dom_restore_assets == before_assets
        and canvas_dom_restore_output_ids == before_output_ids
        and multi_output.get("selectionOutputIdAfterRestore") == before_output_ids[4]
    )
    if isinstance(multi_output, dict):
        _validate_history_preview_geometry(multi_output)
    if not isinstance(history, dict) or not isinstance(multi_output, dict) or not (
        history.get("fixedHeight") is True
        and history.get("noResizeHandle") is True
        and float(history.get("thumbHeight") or 0) >= 168
        and float(history.get("thumbRatio") or 0) > 1.05
        and int(history.get("imageCount") or 0) >= 3
        and history.get("scanThumbnailsFillFrame") is True
        and history.get("titleThemeColor") is True
        and history.get("multiOutputGallery") is True
        and history.get("multiOutputIndependentDeleteRestore") is True
        and history.get("rootScrollStable") is True
        and float(history.get("titleFont") or 0) >= 16
        and float(history.get("promptFont") or 0) >= 13
        and history.get("overlayDownload") is True
        and history.get("overlayDelete") is False
        and multi_output.get("outputCount") == 8
        and multi_output.get("downloadCount") == 8
        and multi_output.get("previewCount") == 8
        and multi_output.get("previewsDiscoverable") is True
        and multi_output.get("galleryScrollable") is True
        and multi_output.get("fixedScanFrames") is True
        and multi_output.get("historyCardFullyVisible") is True
        and multi_output.get("mainThumbnailUnshrunk") is True
        and multi_output.get("leftBottomAligned") is True
        and multi_output.get("previewOverlay") is True
        and multi_output.get("outerFrameAbsent") is True
        and multi_output.get("eightPreviewGridFits") is True
        and multi_output.get("noVisibleScrollbars") is True
        and multi_output.get("unifiedRadii") is True
        and multi_output.get("dangerousGroupDeletePresent") is True
        and multi_output.get("independentDelete") is True
        and multi_output.get("singleAssetSynchronized") is True
        and multi_output.get("restored") is True
        and multi_output.get("identityOrderPreserved") is True
        and identity_order_preserved
        and multi_output.get("rootScrollStable") is True
        and multi_output.get("previewSelectsWithoutOpeningDetail") is True
        and multi_output.get("selectedPreviewDecoded") is True
        and str(multi_output.get("selectedPreviewSrc") or "").strip()
    ):
        raise ValueError("history geometry is missing the fixed image-first contract")

    detail_scene = scenes.get("generation-detail") or {}
    detail = detail_scene.get("detailFacts")
    clipboard = detail_scene.get("clipboardFacts")
    post_delete = detail.get("postDelete") if isinstance(detail, dict) else None
    if isinstance(detail, dict):
        _validate_detail_output_strip(detail)
    post_delete_synchronized = bool(
        isinstance(post_delete, dict)
        and post_delete.get("deletedAssetId") == deleted_asset
        and post_delete.get("deletedOutputId") == deleted_output_id
        and post_delete.get("expectedRemainingAssetIds") == expected_remaining_assets
        and post_delete.get("expectedRemainingOutputIds") == expected_remaining_output_ids
        and post_delete.get("historyAssetIds") == expected_remaining_assets
        and post_delete.get("historyDomAssetIds") == expected_remaining_assets
        and post_delete.get("historyPreviewAssetIds") == expected_remaining_assets
        and post_delete.get("detailAssetIds") == expected_remaining_assets
        and post_delete.get("canvasAssetIds") == expected_remaining_assets
        and post_delete.get("canvasDomAssetIds") == expected_remaining_assets
        and post_delete.get("historyOutputIds") == expected_remaining_output_ids
        and post_delete.get("historyDomOutputIds") == expected_remaining_output_ids
        and post_delete.get("historyPreviewOutputIds") == expected_remaining_output_ids
        and post_delete.get("detailOutputIds") == expected_remaining_output_ids
        and post_delete.get("canvasOutputIds") == expected_remaining_output_ids
        and post_delete.get("canvasDomOutputIds") == expected_remaining_output_ids
        and post_delete.get("historySelectedOutputId") == before_output_ids[4]
        and post_delete.get("detailSelectedOutputId") == before_output_ids[4]
        and post_delete.get("canvasSelectedOutputId") == before_output_ids[4]
        and post_delete.get("historySelectedAssetId") == before_assets[4]
        and post_delete.get("detailSelectedAssetId") == before_assets[4]
        and post_delete.get("actionAssetIds") == [before_assets[4]] * 4
        and post_delete.get("actionOutputIds") == [before_output_ids[4]] * 4
        and post_delete.get("detailStayedOpen") is True
        and post_delete.get("destructiveGroupDeleteHiddenWhileRestorable") is True
        and post_delete.get("singleAssetSynchronized") is True
    ) if isinstance(before_assets, list) and len(before_assets) == 8 else False
    if not isinstance(detail, dict) or not isinstance(clipboard, dict) or not (
        str(detail.get("model") or "").strip()
        and str(detail.get("time") or "").strip()
        and str(detail.get("prompt") or "").strip()
        and detail.get("downloadVisible") is True
        and detail.get("copyVisible") is True
        and detail.get("titleThemeColor") is True
        and detail.get("englishKickerAbsent") is True
        and detail.get("outputCount") == 8
        and detail.get("selectedOutputIndex") == "3"
        and detail.get("selectedImageDecoded") is True
        and int(detail.get("imageWidth") or 0) > 0
        and str(detail.get("imageSrc") or "").strip()
        and detail.get("matchesSelectedPreview") is True
        and str(detail.get("imageSrc") or "") == str(multi_output.get("selectedPreviewSrc") or "")
        and "4" in str(detail.get("outputPosition") or "")
        and "8" in str(detail.get("outputPosition") or "")
        and detail.get("allOutputsDiscoverable") is True
        and detail.get("actionsBoundToCurrent") is True
        and detail.get("downloadSourceMatches") is True
        and detail.get("keyboardSwitch") is True
        and detail.get("deleteVisible") is True
        and detail.get("outputGridRows") is True
        and detail.get("noVisibleScrollbar") is True
        and detail.get("outputStripOuterFrameAbsent") is True
        and post_delete_synchronized
        and float(detail.get("viewportWidthShare") or 0) >= 0.8
        and float(detail.get("viewportHeightShare") or 0) >= 0.8
        and detail.get("sharedPreviewFrame") is True
        and float(detail.get("wheelZoom") or 0) > 1
        and abs(float((detail.get("dragPan") or {}).get("x") or 0)) >= 20
        and abs(float((detail.get("dragPan") or {}).get("y") or 0)) >= 10
        and detail.get("escapeKeptHistory") is True
        and clipboard.get("png") is True
        and clipboard.get("longest") == 2048
        and clipboard.get("matchesSelectedOutput") is True
        and clipboard.get("centerPixel") == [156, 112, 64, 255]
    ):
        raise ValueError("generation-detail geometry is missing metadata or 2K clipboard evidence")

    duplicate_scene = scenes.get("duplicate-occurrence-check")
    if duplicate_scene is not None:
        duplicate = duplicate_scene.get("duplicateOccurrenceFacts")
        before = duplicate.get("before") if isinstance(duplicate, dict) else None
        after_delete = duplicate.get("afterDelete") if isinstance(duplicate, dict) else None
        after_restore = duplicate.get("afterRestore") if isinstance(duplicate, dict) else None
        before_history = before.get("history") if isinstance(before, dict) else None
        before_canvas = before.get("canvas") if isinstance(before, dict) else None
        before_assets = [item.get("assetId") for item in before_history] if isinstance(before_history, list) else []
        before_output_ids = [item.get("outputId") for item in before_history] if isinstance(before_history, list) else []
        expected_after_output_ids = before_output_ids[:2] + before_output_ids[3:] if len(before_output_ids) == 4 else []
        if not (
            isinstance(duplicate, dict)
            and len(before_history) == 4
            and before_canvas == before_history
            and len(set(before_output_ids)) == 4
            and all(str(output_id or "").strip() for output_id in before_output_ids)
            and len(set(before_assets)) == 3
            and before_assets[0] == before_assets[2]
            and duplicate.get("deletedAssetId") == before_assets[2]
            and duplicate.get("deletedOutputId") == before_output_ids[2]
            and after_delete.get("historyOutputIds") == expected_after_output_ids
            and after_delete.get("canvasOutputIds") == expected_after_output_ids
            and after_delete.get("previewOutputIds") == expected_after_output_ids
            and after_delete.get("selectedOutputId") == before_output_ids[3]
            and after_delete.get("mediaReadable") is True
            and after_delete.get("survivingDecoded") is True
            and str(after_delete.get("restoreDeletionId") or "").strip()
            and after_restore.get("historyOutputIds") == before_output_ids
            and after_restore.get("canvasOutputIds") == before_output_ids
            and after_restore.get("previewOutputIds") == before_output_ids
            and after_restore.get("selectedOutputId") == before_output_ids[3]
            and duplicate.get("exactDelete") is True
            and duplicate.get("mediaReadable") is True
            and duplicate.get("exactRestore") is True
        ):
            raise ValueError("duplicate output occurrence evidence is incomplete")

    for scene_name in ("settings", "shortcuts"):
        drawer_state = (scenes.get(scene_name) or {}).get("historyDrawerState")
        try:
            drawer_closed = bool(
                isinstance(drawer_state, dict)
                and drawer_state.get("open") is False
                and drawer_state.get("ariaExpanded") == "false"
                and drawer_state.get("visibility") == "hidden"
                and abs(float(drawer_state.get("opacity") or 0)) <= 0.01
                and drawer_state.get("pointerEvents") == "none"
            )
        except (TypeError, ValueError):
            drawer_closed = False
        if not drawer_closed:
            raise ValueError(f"{scene_name} history drawer remained open")

    settings = (scenes.get("settings") or {}).get("settingsWidth")
    if not isinstance(settings, dict) or not (
        abs(float(settings.get("panelWidth") or 0) - float(settings.get("expectedPanelWidth") or 0)) <= 2
        and float(settings.get("shellWidth") or 0) >= float(settings.get("frameWidth") or 0) - 42
        and settings.get("embeddedScaleOptOut") is True
        and settings.get("scaledClassPresent") is False
        and settings.get("bodyTransform") in {"none", "matrix(1, 0, 0, 1, 0, 0)"}
        and settings.get("iframeHorizontal") is False
    ):
        raise ValueError("settings geometry is missing the unscaled full-width contract")


def _environment_contract(environment: dict) -> dict:
    browser = environment.get("browser") or {}
    return {
        "os": environment.get("os"),
        "browser": {
            "name": browser.get("name"),
            "channel": browser.get("channel"),
            "version": browser.get("version"),
        },
        "dpr": environment.get("dpr"),
        "color_scheme": environment.get("color_scheme"),
        "locale": environment.get("locale"),
        "timezone": environment.get("timezone"),
        "fonts": environment.get("fonts"),
        "provider": environment.get("provider"),
        "animations": environment.get("animations"),
    }


def _validate_environment(environment: dict) -> None:
    contract = _environment_contract(environment)
    browser = contract["browser"]
    if browser["name"] != "Google Chrome" or browser["channel"] != "stable" or not browser["version"]:
        raise ValueError("evidence must identify stable Google Chrome")
    if contract["os"] != "Windows":
        raise ValueError("UI integrity baseline requires the fixed Windows environment")
    if contract["dpr"] != 1 or contract["color_scheme"] != "light":
        raise ValueError("UI integrity evidence requires DPR 1 and light color scheme")
    if contract["locale"] != "zh-CN" or contract["timezone"] != "Asia/Shanghai":
        raise ValueError("UI integrity evidence requires the fixed zh-CN locale and Asia/Shanghai timezone")
    if contract["provider"] != "offline-none":
        raise ValueError("UI integrity evidence must remain offline with no provider calls")
    if contract["animations"] != "disabled-for-baseline":
        raise ValueError("UI integrity evidence must disable animations for stable screenshots")
    fonts = contract["fonts"]
    expected_fonts = {"Inter", "JetBrains Mono", "Space Grotesk"}
    if (
        not isinstance(fonts, dict)
        or set(fonts) != expected_fonts
        or not all(value is True for value in fonts.values())
    ):
        raise ValueError("UI integrity evidence is missing the fixed font-availability contract")


def _request_is_offline(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    raw_url = str(item.get("url") or "").strip()
    if not raw_url:
        return False
    parsed = urlparse(raw_url)
    if parsed.scheme in {"data", "blob", "about"}:
        return True
    if parsed.scheme not in {"http", "https"}:
        return False
    return (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


def validate_candidate(root: Path) -> dict:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing manifest.json")
    manifest = load_json(manifest_path)
    if manifest.get("journey") != "desktop-ui-integrity" or manifest.get("status") != "passed":
        raise ValueError("UI integrity manifest is not passed")
    if manifest.get("first_failure") is not None:
        raise ValueError("passed UI integrity evidence cannot retain or overwrite a first failure")
    environment = manifest.get("environment") or {}
    _validate_environment(environment)
    attempts = manifest.get("attempts") or []
    actual = [tuple(item.get("viewport") or ()) for item in attempts]
    if tuple(actual) != EXPECTED_VIEWPORTS:
        raise ValueError("evidence must cover all five required desktop viewports in canonical order")

    operation_count = 0
    duplicate_occurrence_evidence_count = 0
    screenshot_entries: list[dict] = []
    for attempt in attempts:
        if attempt.get("status") != "passed":
            raise ValueError("every viewport attempt must pass")
        viewport = tuple(attempt.get("viewport") or ())
        lifecycle = attempt.get("lifecycle") or {}
        if not all(lifecycle.get(key) is True for key in REQUIRED_FLAGS):
            raise ValueError("lifecycle is missing a required UI integrity assertion")
        if lifecycle.get("provider_calls") != 0:
            raise ValueError("UI integrity journey recorded a provider call")

        console_path = artifact_path(root, attempt.get("console_errors"), "console evidence")
        operations_path = artifact_path(root, attempt.get("operations"), "native operations evidence")
        requests_path = artifact_path(root, attempt.get("requests"), "requests evidence")
        geometry_path = artifact_path(root, attempt.get("geometry"), "geometry evidence")
        if load_json(console_path):
            raise ValueError("console evidence is non-empty")
        requests = load_json(requests_path)
        if not isinstance(requests, list) or any(not _request_is_offline(item) for item in requests) or any(
            any(marker in str(item.get("url") or "") for marker in PROVIDER_MARKERS)
            for item in requests
            if isinstance(item, dict)
        ):
            raise ValueError("UI integrity journey contains a non-loopback or provider request")
        geometry = load_json(geometry_path)
        geometry_scenes = [item.get("scene") for item in geometry] if isinstance(geometry, list) else []
        canonical_geometry = geometry[: len(EXPECTED_SCENES)] if isinstance(geometry, list) else []
        extra_geometry = geometry[len(EXPECTED_SCENES) :] if isinstance(geometry, list) else []
        expected_extra_scenes = ["duplicate-occurrence-check"] if viewport == (800, 600) else []
        if (
            not isinstance(geometry, list)
            or geometry_scenes[: len(EXPECTED_SCENES)] != list(EXPECTED_SCENES)
            or [item.get("scene") for item in extra_geometry] != expected_extra_scenes
        ):
            raise ValueError("geometry evidence must cover every canonical UI scene and the required duplicate occurrence check")
        if any(item.get("passed") is not True for item in canonical_geometry):
            raise ValueError("a UI geometry scene did not pass")
        duplicate_occurrence_evidence_count += sum(
            item.get("scene") == "duplicate-occurrence-check" for item in extra_geometry
        )
        _validate_selection_actions(geometry)
        _validate_scene_facts(geometry)

        operations = [
            json.loads(line)
            for line in operations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        operation_count += len(operations)
        trusted_clicks = {
            str(item.get("selector") or "")
            for item in operations
            if has_trusted_selector_owner_click(item, button="left")
        }
        required_clicks = {
            ".model-control > .smart-pill",
            ".size-picker-control > .smart-pill",
            "#smartLogToggle",
            "#composerFlowBtn",
            '[data-canvas-log-id="integrity-success-one"] [data-log-output-preview="3"]',
            '.smart-log-detail-output[data-output-index="1"]',
            ".smart-log-detail-copy-2k",
            ".smart-log-detail-delete-original",
            "#smartSettingsLink",
            "#smartShortcutToggle",
            "#assetToggle",
        }
        if not required_clicks.issubset(trusted_clicks):
            raise ValueError("missing trusted native access to a critical UI surface")
        context_clicks = [
            item
            for item in operations
            if item.get("selector") == "#shell"
            and has_trusted_selector_owner_click(item, button="right")
        ]
        if not context_clicks:
            raise ValueError("missing trusted native create-menu activation")
        if not any(
            item.get("blank_canvas") is True
            and str(item.get("blocked_selector") or "").strip()
            and not item.get("blocking_hit")
            and isinstance(item.get("candidate_relative"), list)
            and len(item["candidate_relative"]) == 2
            and isinstance(item.get("hit_ancestry"), list)
            and any("#world" in str(ancestor) for ancestor in item["hit_ancestry"])
            and "#shell" in " ".join(str(ancestor) for ancestor in item["hit_ancestry"])
            and item.get("observed_target") == item.get("scan_hit")
            for item in context_clicks
        ):
            raise ValueError("missing trusted native create-menu activation at an unblocked canvas point")
        for key in (" ", "ArrowLeft", "ArrowRight", "Enter", "Escape"):
            if not any(
                item.get("action") == "keyboard"
                and item.get("key") == key
                and item.get("trusted") is True
                and item.get("observed_type") == "keyup"
                for item in operations
            ):
                raise ValueError(f"missing trusted native {key} evidence")
        required_wheels = {
            ".size-picker-control.pinned .smart-popover",
            "#promptInput",
            ".composer-head",
            "#smartLogModal .log-list",
            ".smart-log-detail-stage",
        }
        trusted_wheels = {
            str(item.get("selector") or "")
            for item in operations
            if item.get("action") == "wheel"
            and item.get("trusted") is True
            and item.get("observed_type") == "wheel"
        }
        if not required_wheels.issubset(trusted_wheels):
            raise ValueError("missing trusted native wheel ownership, history navigation, or preview zoom evidence")
        if not any(
            item.get("action") == "wheel"
            and item.get("selector") == "#smartLogModal .log-list"
            and item.get("trusted") is True
            and abs(float(item.get("delta_x") or 0)) > 0
            and abs(float(item.get("delta_y") or 0)) <= 0.1
            for item in operations
        ):
            raise ValueError("missing trusted horizontal history navigation without a visible scrollbar")
        if not any(
            item.get("action") == "drag"
            and item.get("selector") == ".smart-log-detail-stage"
            and item.get("trusted") is True
            for item in operations
        ):
            raise ValueError("missing trusted native generation-detail pan evidence")

        screenshots = attempt.get("screenshots") or []
        if [item.get("scene") for item in screenshots if isinstance(item, dict)] != list(EXPECTED_SCENES):
            raise ValueError("each viewport needs exactly the canonical screenshot matrix")
        for item in screenshots:
            scene = str(item.get("scene") or "")
            path = artifact_path(root, item.get("path"), f"screenshot {viewport} {scene}")
            image_bytes = path.read_bytes()
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.size != viewport or image.format != "PNG" or image.mode != "RGB":
                    raise ValueError(f"screenshot must be a viewport-sized RGB PNG: {viewport} {scene}")
                image.load()
            image_sha256 = sha256_bytes(image_bytes)
            snapshot_path = root / ".candidate-snapshots" / f"{image_sha256}.png"
            if snapshot_path.is_file():
                if snapshot_path.read_bytes() != image_bytes:
                    raise ValueError("candidate screenshot snapshot hash collision")
            else:
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_snapshot = snapshot_path.with_name(
                    f".{snapshot_path.name}.{os.getpid()}.tmp"
                )
                try:
                    temporary_snapshot.write_bytes(image_bytes)
                    os.replace(temporary_snapshot, snapshot_path)
                finally:
                    temporary_snapshot.unlink(missing_ok=True)
            screenshot_entries.append(
                {
                    "viewport": list(viewport),
                    "scene": scene,
                    "source": str(path.relative_to(root).as_posix()),
                    "snapshot": str(snapshot_path.relative_to(root).as_posix()),
                    "key": screenshot_key(viewport, scene),
                    "sha256": image_sha256,
                }
            )
    if duplicate_occurrence_evidence_count != 1:
        raise ValueError("evidence must contain exactly one real duplicate output occurrence check")
    return {
        "status": "passed",
        "viewports": len(attempts),
        "operations": operation_count,
        "screenshots": len(screenshot_entries),
        "environment": _environment_contract(environment),
        "entries": screenshot_entries,
    }


def _load_accepted_baseline(baselines: Path) -> dict:
    manifest_path = baselines / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            "accepted UI baseline is missing; a user must explicitly run the accept command with --accepted-by and --reason"
        )
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError("UI baseline manifest has an unsupported schema version")
    if manifest.get("contract") != "desktop-ui-integrity" or manifest.get("accepted") is not True:
        raise ValueError("UI baseline manifest is not explicitly accepted")
    return manifest


def _write_difference(candidate: bytes, baseline: bytes, output: Path) -> None:
    with Image.open(io.BytesIO(candidate)) as candidate_image, Image.open(io.BytesIO(baseline)) as baseline_image:
        if candidate_image.size != baseline_image.size:
            return
        difference = ImageChops.difference(candidate_image.convert("RGB"), baseline_image.convert("RGB"))
        output.parent.mkdir(parents=True, exist_ok=True)
        difference.save(output, format="PNG")


def _difference_components(peak_difference: Image.Image, threshold: int) -> list[dict]:
    width, height = peak_difference.size
    active = {
        index
        for index, value in enumerate(peak_difference.tobytes())
        if value > threshold
    }
    components: list[dict] = []
    while active:
        start = active.pop()
        stack = [start]
        size = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while stack:
            index = stack.pop()
            size += 1
            x = index % width
            y = index // width
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
                for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = neighbor_y * width + neighbor_x
                    if neighbor in active:
                        active.remove(neighbor)
                        stack.append(neighbor)
        component_w = max_x - min_x + 1
        component_h = max_y - min_y + 1
        components.append({
            "size": size,
            "width": component_w,
            "height": component_h,
            "density": size / (component_w * component_h),
            "bbox": (min_x, min_y, max_x + 1, max_y + 1),
        })
    return components


def _dense_strong_component_facts(
    peak_difference: Image.Image,
    threshold: int = SCREENSHOT_STRONG_DELTA,
) -> tuple[bool, bool]:
    dense_components = []
    for component in _difference_components(peak_difference, threshold):
        linear = (
            min(component["width"], component["height"]) <= 2
            and component["size"] >= SCREENSHOT_MIN_LINEAR_COMPONENT
        )
        coherent = (
            component["size"] >= SCREENSHOT_MIN_COHERENT_COMPONENT
            and component["density"] > SCREENSHOT_MAX_COMPONENT_DENSITY
        )
        if linear or coherent:
            dense_components.append(component)
    if not dense_components:
        return False, False
    width, height = peak_difference.size
    edge_only = all(
        component["bbox"][0] <= 0
        or component["bbox"][1] <= 0
        or component["bbox"][2] >= width
        or component["bbox"][3] >= height
        for component in dense_components
    )
    return True, edge_only


def _has_dense_strong_component(peak_difference: Image.Image, threshold: int = SCREENSHOT_STRONG_DELTA) -> bool:
    dense, _ = _dense_strong_component_facts(peak_difference, threshold)
    return dense


def _has_paired_linear_displacement(peak_difference: Image.Image) -> bool:
    """Detect a compact control edge moving by one or more whole pixels.

    Raster noise may form one short dense component.  A real translated border
    produces two parallel, similarly-sized high-contrast lines: the vacated
    edge and the new edge.  Detect that paired geometry at full resolution
    before the antialiasing-tolerant sampled comparison runs.
    """
    lines = []
    for component in _difference_components(peak_difference, SCREENSHOT_GEOMETRY_DELTA):
        width = component["width"]
        height = component["height"]
        if component["density"] < 0.70:
            continue
        if width <= SCREENSHOT_GEOMETRY_MAX_THICKNESS and height >= SCREENSHOT_GEOMETRY_MIN_LENGTH:
            orientation = "vertical"
        elif height <= SCREENSHOT_GEOMETRY_MAX_THICKNESS and width >= SCREENSHOT_GEOMETRY_MIN_LENGTH:
            orientation = "horizontal"
        else:
            continue
        lines.append({**component, "orientation": orientation})
    for index, first in enumerate(lines):
        for second in lines[index + 1 :]:
            if first["orientation"] != second["orientation"]:
                continue
            first_box = first["bbox"]
            second_box = second["bbox"]
            if first["orientation"] == "vertical":
                overlap = max(0, min(first_box[3], second_box[3]) - max(first_box[1], second_box[1]))
                length = min(first["height"], second["height"])
                separation = abs((first_box[0] + first_box[2]) / 2 - (second_box[0] + second_box[2]) / 2)
            else:
                overlap = max(0, min(first_box[2], second_box[2]) - max(first_box[0], second_box[0]))
                length = min(first["width"], second["width"])
                separation = abs((first_box[1] + first_box[3]) / 2 - (second_box[1] + second_box[3]) / 2)
            if (
                length > 0
                and overlap / length >= SCREENSHOT_GEOMETRY_MIN_OVERLAP
                and 2 < separation <= SCREENSHOT_GEOMETRY_MAX_SEPARATION
            ):
                return True
    return False


def _compare_screenshots(candidate: bytes, baseline: bytes) -> dict:
    with Image.open(io.BytesIO(candidate)) as candidate_image, Image.open(io.BytesIO(baseline)) as baseline_image:
        if candidate_image.format != "PNG" or baseline_image.format != "PNG":
            return {
                "equivalent": False,
                "reason": "format",
                "candidate_format": candidate_image.format,
                "baseline_format": baseline_image.format,
            }
        if candidate_image.mode != "RGB" or baseline_image.mode != "RGB":
            return {
                "equivalent": False,
                "reason": "mode",
                "candidate_mode": candidate_image.mode,
                "baseline_mode": baseline_image.mode,
            }
        if candidate_image.size != baseline_image.size:
            return {
                "equivalent": False,
                "reason": "dimensions",
                "candidate_size": list(candidate_image.size),
                "baseline_size": list(baseline_image.size),
            }
        full_difference = ImageChops.difference(candidate_image, baseline_image)
        full_red, full_green, full_blue = full_difference.split()
        full_peak_difference = ImageChops.lighter(
            ImageChops.lighter(full_red, full_green), full_blue,
        )
        paired_linear_displacement = _has_paired_linear_displacement(full_peak_difference)
        if SCREENSHOT_SAMPLE_SCALE != 1:
            sample_size = (
                max(1, round(candidate_image.width * SCREENSHOT_SAMPLE_SCALE)),
                max(1, round(candidate_image.height * SCREENSHOT_SAMPLE_SCALE)),
            )
            candidate_sampled = candidate_image.resize(sample_size, Image.LANCZOS)
            baseline_sampled = baseline_image.resize(sample_size, Image.LANCZOS)
        else:
            candidate_sampled = candidate_image
            baseline_sampled = baseline_image
        difference = ImageChops.difference(candidate_sampled, baseline_sampled)
        red, green, blue = difference.split()
        peak_difference = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        histogram = peak_difference.histogram()
        total_pixels = candidate_sampled.width * candidate_sampled.height
        changed_pixels = total_pixels - histogram[0]
        strong_pixels = sum(histogram[SCREENSHOT_STRONG_DELTA + 1 :])
        very_strong_pixels = sum(histogram[SCREENSHOT_VERY_STRONG_DELTA + 1 :])
        peak_delta = next((value for value in range(255, -1, -1) if histogram[value]), 0)
        changed_bbox = list(peak_difference.getbbox()) if changed_pixels else None
        mean_peak_delta = sum(value * count for value, count in enumerate(histogram)) / total_pixels
        changed_ratio = changed_pixels / total_pixels
        strong_ratio = strong_pixels / total_pixels
        very_strong_ratio = very_strong_pixels / total_pixels
        within_scalar_budget = (
            changed_ratio <= SCREENSHOT_MAX_CHANGED_RATIO
            and mean_peak_delta <= SCREENSHOT_MAX_MEAN_PEAK_DELTA
            and strong_ratio <= SCREENSHOT_MAX_STRONG_RATIO
            and very_strong_ratio <= SCREENSHOT_MAX_VERY_STRONG_RATIO
            and peak_delta <= SCREENSHOT_MAX_PEAK_DELTA
        )
        if within_scalar_budget:
            dense_strong_component, dense_components_touch_only_edges = _dense_strong_component_facts(
                peak_difference
            )
        else:
            dense_strong_component = None
            dense_components_touch_only_edges = None
        edge_rasterization_only = bool(
            dense_strong_component is True
            and dense_components_touch_only_edges is True
            and peak_delta <= SCREENSHOT_EDGE_RASTER_MAX_DELTA
        )
        equivalent = (
            within_scalar_budget
            and (dense_strong_component is False or edge_rasterization_only)
            and paired_linear_displacement is False
        )
        return {
            "equivalent": equivalent,
            "reason": "strict-raster-budget" if equivalent else "visual-change",
            "sample_scale": SCREENSHOT_SAMPLE_SCALE,
            "changed_pixels": changed_pixels,
            "changed_ratio": changed_ratio,
            "mean_peak_delta": mean_peak_delta,
            "strong_pixels": strong_pixels,
            "strong_ratio": strong_ratio,
            "very_strong_pixels": very_strong_pixels,
            "very_strong_ratio": very_strong_ratio,
            "peak_delta": peak_delta,
            "changed_bbox": changed_bbox,
            "dense_strong_component": dense_strong_component,
            "dense_components_touch_only_edges": dense_components_touch_only_edges,
            "edge_rasterization_only": edge_rasterization_only,
            "paired_linear_displacement": paired_linear_displacement,
            "thresholds": {
                "max_changed_ratio": SCREENSHOT_MAX_CHANGED_RATIO,
                "max_mean_peak_delta": SCREENSHOT_MAX_MEAN_PEAK_DELTA,
                "max_strong_ratio": SCREENSHOT_MAX_STRONG_RATIO,
                "max_very_strong_ratio": SCREENSHOT_MAX_VERY_STRONG_RATIO,
                "max_peak_delta": SCREENSHOT_MAX_PEAK_DELTA,
                "strong_delta": SCREENSHOT_STRONG_DELTA,
                "very_strong_delta": SCREENSHOT_VERY_STRONG_DELTA,
                "min_coherent_component": SCREENSHOT_MIN_COHERENT_COMPONENT,
                "max_component_density": SCREENSHOT_MAX_COMPONENT_DENSITY,
                "edge_raster_max_delta": SCREENSHOT_EDGE_RASTER_MAX_DELTA,
                "geometry_delta": SCREENSHOT_GEOMETRY_DELTA,
                "geometry_min_length": SCREENSHOT_GEOMETRY_MIN_LENGTH,
                "geometry_max_thickness": SCREENSHOT_GEOMETRY_MAX_THICKNESS,
                "geometry_min_overlap": SCREENSHOT_GEOMETRY_MIN_OVERLAP,
                "geometry_max_separation": SCREENSHOT_GEOMETRY_MAX_SEPARATION,
            },
        }


def validate(root: Path, baselines: Path = DEFAULT_BASELINES) -> dict:
    root = root.resolve()
    baselines = baselines.resolve()
    candidate = validate_candidate(root)
    baseline = _load_accepted_baseline(baselines)
    if baseline.get("environment") != candidate["environment"]:
        raise ValueError("candidate environment does not exactly match the accepted UI baseline environment")
    baseline_rows = baseline.get("screenshots")
    if not isinstance(baseline_rows, list) or not all(isinstance(item, dict) for item in baseline_rows):
        raise ValueError("accepted UI baseline screenshots must be a list of objects")
    baseline_entries = {
        (tuple(item.get("viewport") or ()), str(item.get("scene") or "")): item
        for item in baseline_rows
    }
    candidate_keys = {(tuple(item["viewport"]), item["scene"]) for item in candidate["entries"]}
    if len(baseline_entries) != len(baseline_rows) or set(baseline_entries) != candidate_keys:
        raise ValueError("accepted UI baseline does not cover the canonical viewport/scene matrix")

    mismatches: list[str] = []
    mismatch_details: list[dict] = []
    tolerated_differences: list[dict] = []
    for item in candidate["entries"]:
        identity = (tuple(item["viewport"]), item["scene"])
        accepted = baseline_entries[identity]
        baseline_path = artifact_path(baselines, accepted.get("path"), f"accepted baseline {item['key']}")
        candidate_path = artifact_path(root, item.get("snapshot"), f"candidate screenshot snapshot {item['key']}")
        baseline_bytes = baseline_path.read_bytes()
        if sha256_bytes(baseline_bytes) != accepted.get("sha256"):
            raise ValueError(f"accepted baseline file is missing or does not match its manifest: {item['key']}")
        candidate_bytes = candidate_path.read_bytes()
        if sha256_bytes(candidate_bytes) != item.get("sha256"):
            raise ValueError(f"candidate screenshot changed after collection: {item['key']}")
        if item.get("sha256") != accepted.get("sha256"):
            comparison = _compare_screenshots(candidate_bytes, baseline_bytes)
            details = {"key": item["key"], **comparison}
            if comparison["equivalent"]:
                tolerated_differences.append(details)
            else:
                mismatches.append(item["key"])
                mismatch_details.append(details)
                _write_difference(candidate_bytes, baseline_bytes, root / "baseline-diffs" / item["key"])
    if mismatches:
        write_json(root / "baseline-diffs" / "metrics.json", mismatch_details)
        raise ValueError("UI screenshot baseline mismatch: " + ", ".join(mismatches))
    return {
        "status": "passed",
        "viewports": candidate["viewports"],
        "operations": candidate["operations"],
        "screenshots": candidate["screenshots"],
        "comparison": "sha256-or-strict-raster-v1",
        "tolerated_screenshot_differences": tolerated_differences,
        "baseline": str((baselines / "manifest.json").resolve()),
    }


def accept_baseline(
    root: Path,
    baselines: Path = DEFAULT_BASELINES,
    *,
    accepted_by: str,
    reason: str,
    replace: bool = False,
) -> dict:
    root = root.resolve()
    baselines = baselines.resolve()
    accepted_by = accepted_by.strip()
    reason = reason.strip()
    if not accepted_by or not reason:
        raise ValueError("baseline acceptance requires non-empty accepted_by and reason")
    candidate = validate_candidate(root)
    if baselines.exists() and not replace:
        raise ValueError("an accepted baseline already exists; pass --replace only after explicit user review")

    baselines.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ui-integrity-baseline-", dir=baselines.parent))
    backup = baselines.parent / f".{baselines.name}.backup"
    try:
        screenshots = []
        for item in candidate["entries"]:
            source = artifact_path(root, item.get("snapshot"), f"candidate screenshot snapshot {item['key']}")
            source_bytes = source.read_bytes()
            if sha256_bytes(source_bytes) != item.get("sha256"):
                raise ValueError(f"candidate screenshot changed after collection: {item['key']}")
            destination = temporary / item["key"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_bytes)
            screenshots.append(
                {
                    "viewport": item["viewport"],
                    "scene": item["scene"],
                    "path": item["key"],
                    "sha256": sha256(destination),
                }
            )
        manifest = {
            "schema_version": 1,
            "contract": "desktop-ui-integrity",
            "accepted": True,
            "accepted_by": accepted_by,
            "reason": reason,
            "accepted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "environment": candidate["environment"],
            "screenshots": screenshots,
        }
        write_json(temporary / "manifest.json", manifest)
        if backup.exists():
            shutil.rmtree(backup)
        if baselines.exists():
            os.replace(baselines, backup)
        try:
            os.replace(temporary, baselines)
        except Exception:
            if backup.exists() and not baselines.exists():
                os.replace(backup, baselines)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return {
            "status": "accepted",
            "baseline": str((baselines / "manifest.json").resolve()),
            "screenshots": len(screenshots),
            "accepted_by": accepted_by,
            "reason": reason,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def configured_baselines() -> Path:
    configured = os.environ.get("FIREFLY_UI_INTEGRITY_BASELINES", "").strip()
    return Path(configured) if configured else DEFAULT_BASELINES


def validate_ui_integrity_artifacts(root: Path) -> dict:
    return validate(root, configured_baselines())


def failure_artifact(root: Path, relative: object, label: str) -> Path:
    return artifact_path(root, relative, label)


def failed_ui_integrity_evidence(root: Path) -> dict:
    root = root.resolve()
    manifest = root / "manifest.json"
    summary = {"status": "unvalidated", "root": str(root), "manifest": str(manifest)}
    if not manifest.is_file():
        return summary
    try:
        payload = load_json(manifest)
        failure = payload.get("first_failure") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and payload.get("status") == "passed"
            and "first_failure" in payload
            and failure is None
        ):
            summary.update({"status": "candidate", "candidate": validate_candidate(root)})
            return summary
        if not isinstance(failure, dict):
            raise ValueError("first_failure is missing")
        viewport = failure.get("viewport")
        if not isinstance(viewport, list) or tuple(viewport) not in EXPECTED_VIEWPORTS:
            raise ValueError("first_failure has a non-canonical viewport")
        if not str(failure.get("message") or "").strip():
            raise ValueError("first_failure message is missing")
        operations = failure_artifact(root, failure.get("operations"), "failure operations")
        requests = failure_artifact(root, failure.get("requests"), "failure requests")
        console = failure_artifact(root, failure.get("console_errors"), "failure console errors")
        screenshot = failure_artifact(root, failure.get("failure_screenshot"), "failure screenshot")
        operation_rows = [
            json.loads(line)
            for line in operations.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not all(isinstance(item, dict) for item in operation_rows):
            raise ValueError("failure operations must contain JSON objects")
        if not isinstance(load_json(requests), list) or not isinstance(load_json(console), list):
            raise ValueError("failure requests and console evidence must contain JSON lists")
        with Image.open(screenshot) as image:
            if image.size != tuple(viewport):
                raise ValueError("failure screenshot does not match its viewport")
        summary.update(
            {
                "status": "captured",
                "first_failure": failure,
                "artifacts": {
                    "operations": str(operations),
                    "requests": str(requests),
                    "console_errors": str(console),
                    "failure_screenshot": str(screenshot),
                },
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        summary["status"] = "invalid"
        summary["artifact_error"] = str(error)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("candidate", "validate"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--artifacts-dir", type=Path, required=True)
        subparser.add_argument("--baselines-dir", type=Path, default=DEFAULT_BASELINES)
    accept = subparsers.add_parser("accept")
    accept.add_argument("--artifacts-dir", type=Path, required=True)
    accept.add_argument("--baselines-dir", type=Path, default=DEFAULT_BASELINES)
    accept.add_argument("--accepted-by", required=True)
    accept.add_argument("--reason", required=True)
    accept.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "candidate":
            summary = validate_candidate(args.artifacts_dir)
        elif args.command == "validate":
            summary = validate(args.artifacts_dir, args.baselines_dir)
        else:
            summary = accept_baseline(
                args.artifacts_dir,
                args.baselines_dir,
                accepted_by=args.accepted_by,
                reason=args.reason,
                replace=args.replace,
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
