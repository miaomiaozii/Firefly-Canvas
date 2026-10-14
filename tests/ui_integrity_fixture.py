from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from tests.native_input_fixture import selector_owner_click_evidence
from tools.ui_integrity_evidence import (
    EXPECTED_NODE_ACTION_IDS,
    EXPECTED_SCENES,
    EXPECTED_VIEWPORTS,
    REQUIRED_FLAGS,
    product_node_action_ids,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def operation(action: str, selector: str = "", **values) -> dict:
    item = {"action": action, "selector": selector, "trusted": True}
    if action == "click":
        button = str(values.get("button") or "left")
        item.update(selector_owner_click_evidence(button=button))
    elif action == "wheel":
        item.update({"observed_type": "wheel"})
    elif action == "drag":
        item.update({"observed_type": "mouseup", "observed_within_selector": True})
    item.update(values)
    return item


def history_preview_geometry(viewport: tuple[int, int]) -> dict:
    card_top = max(20, viewport[1] - 290)
    thumb_top = card_top + 6
    gallery_top = thumb_top + 1
    strip_top = gallery_top + 109
    preview_rects = []
    for index in range(8):
        left = 45 + index * 33
        top = strip_top
        preview_rects.append(
            {
                "outputIndex": index,
                "ariaLabel": f"第 {index + 1} 张，共 8 张",
                "decoded": True,
                "radius": 6,
                "rect": {
                    "left": left,
                    "top": top,
                    "right": left + 30,
                    "bottom": top + 25,
                    "width": 30,
                    "height": 25,
                },
            }
        )
    return {
        "viewportRect": {
            "left": 0,
            "top": 0,
            "right": viewport[0],
            "bottom": viewport[1],
            "width": viewport[0],
            "height": viewport[1],
        },
        "listRect": {
            "left": 8,
            "top": card_top - 7,
            "right": viewport[0] - 8,
            "bottom": viewport[1] - 5,
            "width": viewport[0] - 16,
            "height": viewport[1] - card_top + 2,
        },
        "cardRect": {"left": 34, "top": card_top, "right": 272, "bottom": card_top + 274, "width": 238, "height": 274},
        "thumbRect": {"left": 40, "top": thumb_top, "right": 268, "bottom": thumb_top + 168, "width": 228, "height": 168},
        "galleryRect": {"left": 41, "top": gallery_top, "right": 267, "bottom": gallery_top + 166, "width": 226, "height": 166},
        "stripRect": {"left": 45, "top": strip_top, "right": 267, "bottom": strip_top + 25, "width": 222, "height": 25},
        "galleryZIndex": 0,
        "stripZIndex": 4,
        "stripClientWidth": 222,
        "stripScrollWidth": 261,
        "stripScrollable": True,
        "stripWheelShift": True,
        "stripScrollBefore": 0,
        "stripScrollAfter": 39,
        "grid": {"columns": 8, "rows": 1, "columnGap": 3, "rowGap": 0},
        "previews": preview_rects,
        "thumbRadius": 6,
        "galleryRadius": 6,
        "stripStyle": {
            "paddingTop": 0,
            "paddingRight": 0,
            "paddingBottom": 0,
            "paddingLeft": 0,
            "borderTop": 0,
            "borderRight": 0,
            "borderBottom": 0,
            "borderLeft": 0,
            "borderRadius": 0,
            "backgroundAlpha": 0,
            "boxShadow": "none",
            "overflowX": "auto",
            "overflowY": "hidden",
        },
        "scrollbars": {
            "list": {"scrollbarWidth": "none", "webkitDisplay": "none"},
            "gallery": {"scrollbarWidth": "none", "webkitDisplay": "none"},
            "strip": {"scrollbarWidth": "none", "webkitDisplay": "none"},
        },
    }


def write_ui_integrity_runner(root: Path) -> Path:
    path = root / "tests" / "ui_integrity_native_browser.py"
    source_root = Path(__file__).resolve().parents[1]
    path.write_text(
        f'''import argparse, sys\nfrom pathlib import Path\nsys.path.insert(0, {str(source_root)!r})\nfrom tests.ui_integrity_fixture import write_valid_ui_integrity_artifact\np=argparse.ArgumentParser(); p.add_argument("--artifacts-dir",type=Path,required=True); a=p.parse_args(); write_valid_ui_integrity_artifact(a.artifacts_dir)\n''',
        encoding="utf-8",
    )
    return path


def write_valid_ui_integrity_artifact(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    attempts = []
    for viewport_index, viewport in enumerate(EXPECTED_VIEWPORTS):
        width, height = viewport
        attempt_root = root / f"viewport-{width}x{height}"
        screenshot_root = attempt_root / "screenshots"
        screenshot_root.mkdir(parents=True, exist_ok=True)
        screenshots = []
        for scene_index, scene in enumerate(EXPECTED_SCENES):
            path = screenshot_root / f"{scene}.png"
            Image.new(
                "RGB",
                viewport,
                (
                    20 + viewport_index * 20,
                    35 + scene_index * 18,
                    70 + viewport_index * 8 + scene_index,
                ),
            ).save(path, format="PNG")
            screenshots.append({"scene": scene, "path": path.relative_to(root).as_posix()})

        operations = [
            operation("click", ".model-control > .smart-pill"),
            operation("click", ".size-picker-control > .smart-pill"),
            operation("click", "#smartHistoryToggle"),
            operation("click", "#composerFlowBtn"),
            operation("click", '[data-canvas-history-id="integrity-success-one"] [data-history-output-preview="3"]'),
            operation("click", '.smart-log-detail-output[data-output-index="1"]'),
            operation("click", ".smart-log-detail-copy-2k"),
            operation("click", ".smart-log-detail-delete-original"),
            operation("click", "#smartSettingsLink"),
            operation("click", "#smartShortcutToggle"),
            operation("click", "#assetToggle"),
            operation(
                "click",
                "#shell",
                button="right",
                blank_canvas=True,
                candidate_index=0,
                candidate_relative=[0.72, 0.64],
                scan_hit="div#world.world",
                hit_ancestry=["div#world.world", "div#shell"],
                blocked_selector=(
                    ".image-node,.composer,.smart-top-left,.smart-toolbar,.smart-minimap,#createMenu"
                ),
                blocking_hit="",
                observed_target="div#world.world",
            ),
            operation("keyboard", key=" ", observed_type="keyup"),
            operation("keyboard", key="ArrowLeft", observed_type="keyup"),
            operation("keyboard", key="ArrowRight", observed_type="keyup"),
            operation("keyboard", key="Enter", observed_type="keyup"),
            operation("keyboard", key="Escape", observed_type="keyup"),
            operation("wheel", ".size-picker-control.pinned .smart-popover"),
            operation("wheel", "#promptInput"),
            operation("wheel", ".composer-head"),
            operation("wheel", "#smartHistoryModal .log-list", delta_x=240, delta_y=0),
            operation("wheel", ".smart-log-detail-stage"),
            operation("drag", ".smart-log-detail-stage"),
        ]
        operations_path = attempt_root / "operations.jsonl"
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        write_json(attempt_root / "console-errors.json", [])
        write_json(attempt_root / "requests.json", [])
        # #176: expected is the declared contract, actual is read out of the
        # product toolbar source. Echoing the same constant into both fields made
        # the validator's exact-set check unfalsifiable.
        observed_action_ids = product_node_action_ids()
        selection_actions = {
            "expectedActionIds": list(EXPECTED_NODE_ACTION_IDS),
            "actualActionIds": observed_action_ids,
            "actions": [
                {"action": action_id, "visible": True, "inViewport": True, "hit": True}
                for action_id in observed_action_ids
            ],
            "allReachable": True,
        }
        panel_width = min(1560, width - 36)
        scene_facts = {
            "composer-flow": {
                "promptFlowContext": {
                    "centered": True,
                    "contextAbovePrompt": True,
                    "recentCount": 2,
                    "referenceCount": 22,
                    "promptOpaque": True,
                    "recentNoScroll": True,
                    "mediaAdaptive": True,
                    "mediaRatioRange": 1.0,
                    "sameEditor": True,
                    "promptText": "fixture prompt",
                }
            },
            "composer-size": {
                "composerAnchor": {"scale": 1.15, "gapWorld": 14, "centerDeltaWorld": 0}
            },
            "history": {
                "historyFacts": {
                    "fixedHeight": True,
                    "noResizeHandle": True,
                    "thumbHeight": 168,
                    "thumbRatio": 1.5,
                    "imageCount": 3,
                    "scanThumbnailsFillFrame": True,
                    "titleThemeColor": True,
                    "multiOutputGallery": True,
                    "multiOutputIndependentDeleteRestore": True,
                    "rootScrollStable": True,
                    "titleFont": 16,
                    "promptFont": 13,
                    "overlayDownload": True,
                    "overlayDelete": False,
                },
                "multiOutputFacts": {
                    "outputCount": 8,
                    "downloadCount": 8,
                    "previewCount": 8,
                    "previewsDiscoverable": True,
                    "galleryScrollable": True,
                    "fixedScanFrames": True,
                    "historyCardFullyVisible": True,
                    "mainThumbnailUnshrunk": True,
                    "leftBottomAligned": True,
                    "previewOverlay": True,
                    "outerFrameAbsent": True,
                    "eightPreviewGridFits": True,
                    "noVisibleScrollbars": True,
                    "unifiedRadii": True,
                    "dangerousGroupDeletePresent": True,
                    "previewGeometry": history_preview_geometry(viewport),
                    "independentDelete": True,
                    "singleAssetSynchronized": True,
                    "restored": True,
                    "outputAssetIdsBefore": [f"asset-{index}" for index in range(1, 9)],
                    "outputIdsBefore": [f"occ-{index}" for index in range(1, 9)],
                    "canvasAssetIdsBefore": [f"asset-{index}" for index in range(1, 9)],
                    "canvasOutputIdsBefore": [f"occ-{index}" for index in range(1, 9)],
                    "deletedAssetId": "asset-4",
                    "deletedOutputId": "occ-4",
                    "outputAssetIdsAfterDelete": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                    "outputIdsAfterDelete": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                    "canvasAssetIdsAfterDelete": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                    "canvasOutputIdsAfterDelete": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                    "detailAssetIdsAfterDelete": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                    "detailOutputIdsAfterDelete": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                    "outputAssetIdsAfterRestore": [f"asset-{index}" for index in range(1, 9)],
                    "outputIdsAfterRestore": [f"occ-{index}" for index in range(1, 9)],
                    "domAssetIdsAfterRestore": [f"asset-{index}" for index in range(1, 9)],
                    "domOutputIdsAfterRestore": [f"occ-{index}" for index in range(1, 9)],
                    "previewOutputIdsAfterRestore": [f"occ-{index}" for index in range(1, 9)],
                    "canvasAssetIdsAfterRestore": [f"asset-{index}" for index in range(1, 9)],
                    "canvasOutputIdsAfterRestore": [f"occ-{index}" for index in range(1, 9)],
                    "canvasDomAssetIdsAfterRestore": [f"asset-{index}" for index in range(1, 9)],
                    "canvasDomOutputIdsAfterRestore": [f"occ-{index}" for index in range(1, 9)],
                    "selectionAssetIdAfterRestore": "asset-5",
                    "selectionOutputIdAfterRestore": "occ-5",
                    "identityOrderPreserved": True,
                    "rootScrollStable": True,
                    "previewSelectsWithoutOpeningDetail": True,
                    "selectedPreviewDecoded": True,
                    "selectedPreviewSrc": "/api/project-library/project-fixture/media/output-4",
                },
            },
            "generation-detail": {
                "detailFacts": {
                    "model": "fixture model",
                    "time": "2026-08-11 10:00",
                    "prompt": "fixture prompt",
                    "downloadVisible": True,
                    "copyVisible": True,
                    "titleThemeColor": True,
                    "englishKickerAbsent": True,
                    "outputCount": 8,
                    "selectedOutputIndex": "3",
                    "imageWidth": 2200,
                    "imageSrc": "/api/project-library/project-fixture/media/output-4",
                    "selectedImageDecoded": True,
                    "matchesSelectedPreview": True,
                    "outputPosition": "第 4 张，共 8 张",
                    "allOutputsDiscoverable": True,
                    "actionsBoundToCurrent": True,
                    "downloadSourceMatches": True,
                    "keyboardSwitch": True,
                    "deleteVisible": True,
                    "outputGridRows": True,
                    "noVisibleScrollbar": True,
                    "outputStripOuterFrameAbsent": True,
                    "outputStripStyle": {
                        "paddingTop": 0,
                        "paddingRight": 0,
                        "paddingBottom": 0,
                        "paddingLeft": 0,
                        "borderTop": 0,
                        "borderRight": 0,
                        "borderBottom": 0,
                        "borderLeft": 0,
                        "borderRadius": 0,
                        "backgroundAlpha": 0,
                        "boxShadow": "none",
                        "overflowX": "visible",
                        "overflowY": "visible",
                        "scrollbarWidth": "none",
                        "webkitDisplay": "none",
                        "columns": 4,
                        "rows": 2,
                        "columnGap": 7,
                        "rowGap": 7,
                    },
                    "postDelete": {
                        "deletedAssetId": "asset-4",
                        "deletedOutputId": "occ-4",
                        "expectedRemainingAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "expectedRemainingOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "historyAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "historyOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "historyDomAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "historyDomOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "historyPreviewAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "historyPreviewOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "historySelectedAssetId": "asset-5",
                        "historySelectedOutputId": "occ-5",
                        "detailAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "detailOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "detailSelectedAssetId": "asset-5",
                        "detailSelectedOutputId": "occ-5",
                        "actionAssetIds": ["asset-5", "asset-5", "asset-5", "asset-5"],
                        "actionOutputIds": ["occ-5", "occ-5", "occ-5", "occ-5"],
                        "detailStayedOpen": True,
                        "canvasAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "canvasOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "canvasDomAssetIds": ["asset-1", "asset-2", "asset-3", "asset-5", "asset-6", "asset-7", "asset-8"],
                        "canvasDomOutputIds": ["occ-1", "occ-2", "occ-3", "occ-5", "occ-6", "occ-7", "occ-8"],
                        "canvasSelectedOutputId": "occ-5",
                        "destructiveGroupDeleteHiddenWhileRestorable": True,
                        "singleAssetSynchronized": True,
                    },
                    "viewportWidthShare": 0.84,
                    "viewportHeightShare": 0.85,
                    "sharedPreviewFrame": True,
                    "wheelZoom": 1.12,
                    "dragPan": {"x": 54, "y": 28},
                    "escapeKeptHistory": True,
                },
                "clipboardFacts": {"png": True, "width": 2048, "height": 1117, "longest": 2048, "centerPixel": [156, 112, 64, 255], "matchesSelectedOutput": True},
            },
            "settings": {
                "historyDrawerState": {
                    "open": False,
                    "ariaExpanded": "false",
                    "visibility": "hidden",
                    "opacity": 0,
                    "pointerEvents": "none",
                },
                "settingsWidth": {
                    "panelWidth": panel_width,
                    "expectedPanelWidth": panel_width,
                    "frameWidth": panel_width,
                    "shellWidth": panel_width - 40,
                    "embeddedScaleOptOut": True,
                    "scaledClassPresent": False,
                    "bodyTransform": "none",
                    "iframeHorizontal": False,
                }
            },
            "shortcuts": {
                "historyDrawerState": {
                    "open": False,
                    "ariaExpanded": "false",
                    "visibility": "hidden",
                    "opacity": 0,
                    "pointerEvents": "none",
                },
            },
        }
        geometry_items = [
            {
                "scene": scene,
                "passed": True,
                **(
                    {
                        "selectionActions": selection_actions,
                        "selectionMenuAvoidsName": True,
                        "promptSurfacesOpaque": True,
                        "decorativeEnglishAbsent": True,
                    }
                    if scene in {"canvas-composer", "composer-size"}
                    else {}
                ),
                **scene_facts.get(scene, {}),
            }
            for scene in EXPECTED_SCENES
        ]
        if viewport == (800, 600):
            geometry_items.append(
                {
                    "scene": "duplicate-occurrence-check",
                    "duplicateOccurrenceFacts": {
                        "before": {
                            "history": [
                                {"assetId": "asset-a", "outputId": "dup-occ-1"},
                                {"assetId": "asset-b", "outputId": "dup-occ-2"},
                                {"assetId": "asset-a", "outputId": "dup-occ-3"},
                                {"assetId": "asset-c", "outputId": "dup-occ-4"},
                            ],
                            "canvas": [
                                {"assetId": "asset-a", "outputId": "dup-occ-1"},
                                {"assetId": "asset-b", "outputId": "dup-occ-2"},
                                {"assetId": "asset-a", "outputId": "dup-occ-3"},
                                {"assetId": "asset-c", "outputId": "dup-occ-4"},
                            ],
                            "selectedOutputId": "dup-occ-3",
                        },
                        "deletedAssetId": "asset-a",
                        "deletedOutputId": "dup-occ-3",
                        "afterDelete": {
                            "historyOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-4"],
                            "canvasOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-4"],
                            "previewOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-4"],
                            "selectedOutputId": "dup-occ-4",
                            "mediaReadable": True,
                            "survivingDecoded": True,
                            "restoreDeletionId": "deletion-fixture",
                        },
                        "afterRestore": {
                            "historyOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-3", "dup-occ-4"],
                            "canvasOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-3", "dup-occ-4"],
                            "previewOutputIds": ["dup-occ-1", "dup-occ-2", "dup-occ-3", "dup-occ-4"],
                            "selectedOutputId": "dup-occ-4",
                        },
                        "exactDelete": True,
                        "mediaReadable": True,
                        "exactRestore": True,
                    },
                }
            )
        write_json(attempt_root / "geometry.json", geometry_items)
        lifecycle = {key: True for key in REQUIRED_FLAGS}
        lifecycle["provider_calls"] = 0
        attempts.append(
            {
                "status": "passed",
                "viewport": list(viewport),
                "lifecycle": lifecycle,
                "screenshots": screenshots,
                "operations": operations_path.relative_to(root).as_posix(),
                "console_errors": (attempt_root / "console-errors.json").relative_to(root).as_posix(),
                "requests": (attempt_root / "requests.json").relative_to(root).as_posix(),
                "geometry": (attempt_root / "geometry.json").relative_to(root).as_posix(),
            }
        )
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "journey": "desktop-ui-integrity",
            "status": "passed",
            "environment": {
                "os": "Windows",
                "browser": {"name": "Google Chrome", "channel": "stable", "version": "151.0.7922.77"},
                "dpr": 1,
                "color_scheme": "light",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "fonts": {"Inter": True, "JetBrains Mono": True, "Space Grotesk": True},
                "provider": "offline-none",
                "animations": "disabled-for-baseline",
            },
            "attempts": attempts,
            "first_failure": None,
        },
    )
