"""Native Chrome evidence for the project-list -> core-canvas journey."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests.conversation_browser import image_bytes, load_production_app
from tests.native_browser import AsgiBrowserServer, NativeBrowser, VIEWPORTS, find_browser


FONT_FAMILIES = ("Inter", "JetBrains Mono", "Space Grotesk")
RUNTIME_NODE_FIELDS = {
    "pending", "running", "queued", "pendingTasks", "jimengPending",
    "runStartedAt", "runFinishedAt", "runElapsedMs", "runTimerHidden",
    "_runMetaTargetId", "conversationSending", "agentPlanning", "agentExecuting",
}


def durable_nodes(nodes: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in node.items() if key not in RUNTIME_NODE_FIELDS}
        for node in nodes
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    return parser.parse_args(argv)


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Native Browser Evidence")
    media = store.import_media(
        project["id"],
        image_bytes("NATIVE", (46, 92, 138)),
        "native-evidence.png",
        "image/png",
        {"type": "native-browser-fixture", "name": "native-evidence.png"},
    )
    image = {"asset_id": media["asset_id"], "kind": "image", "name": "native-evidence.png"}
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "seed-prompt",
                    "type": "smart-prompt",
                    "x": 120,
                    "y": 120,
                    "w": 316,
                    "h": 96,
                    "text": "native evidence prompt",
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "seed-image",
                    "type": "smart-image",
                    "x": 500,
                    "y": 120,
                    "w": 316,
                    "h": 194,
                    "images": [image],
                },
            },
            {"op": "upsert_edge", "edge": {"from": "seed-prompt", "to": "seed-image", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def seed_cleanup_preview(store: ProjectStore) -> tuple[str, str]:
    project = store.create_project("Cleanup Preview Evidence")
    media = store.import_media(
        project["id"],
        image_bytes("CLEANUP", (126, 74, 42)),
        "failed-cleanup.png",
        "image/png",
        {"type": "online_generation", "lifecycle": "failed"},
    )
    return str(project["id"]), str(media["asset_id"])


def project_tree_snapshot(project_root: Path) -> dict:
    return {
        path.relative_to(project_root).as_posix(): {
            "kind": "directory" if path.is_dir() else "file",
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
        }
        for path in sorted(project_root.rglob("*"))
    }


def write_json(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def project_scope_snapshot(store: ProjectStore, project_id: str) -> dict:
    project = store.open_project(project_id)
    media = {}
    for summary in sorted(store.list_media(project_id), key=lambda item: item["asset_id"]):
        asset_id = summary["asset_id"]
        record = store.media_record(project_id, asset_id)
        path = Path(record.pop("path"))
        media[asset_id] = {
            "record": record,
            "path": str(path),
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "canvas": project["canvas"],
        "history": list(project["canvas"].get("logs") or []),
        "media": media,
    }


def write_source_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> dict:
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG", compress_level=9)
    content = path.read_bytes()
    return {
        "name": path.name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "source_width": size[0],
        "source_height": size[1],
    }


def run_image_organization(
    browser: NativeBrowser,
    *,
    store: ProjectStore,
    base_url: str,
    viewport: tuple[int, int],
    viewport_root: Path,
    artifacts_root: Path,
) -> tuple[dict, list[str]]:
    width, height = viewport
    project_name = f"T06 organization {width}x{height}"
    project = store.create_project(project_name)
    project_id = str(project["id"])
    canvas_id = str(project["canvas_id"])
    source_root = viewport_root / "organization-sources"
    source_root.mkdir(parents=True, exist_ok=False)
    sources = [
        write_source_image(source_root / "landscape.png", (600, 400), (201, 72, 66)),
        write_source_image(source_root / "portrait.png", (400, 600), (61, 126, 194)),
        write_source_image(source_root / "square.png", (512, 512), (69, 151, 111)),
    ]
    source_paths = tuple(source_root / source["name"] for source in sources)
    operation_start = len(browser.operations)

    browser.navigate(
        f"{base_url}/static/smart-canvas.html?id={canvas_id}&project={project_id}&project_mode=1"
    )
    browser.wait_for(
        "typeof canvas !== 'undefined' && canvas?.format === 'firefly-canvas-v1' "
        "&& Array.isArray(nodes) && document.querySelector('#projectSaveState')?.dataset.state === 'saved'",
        timeout=30,
    )
    browser.native_click("#canvasEmptyImport")
    upload_selector = 'body > input[type="file"]:not(#fileInput)'
    browser.wait_for(f"document.querySelector({json.dumps(upload_selector)})")
    browser.native_upload(upload_selector, source_paths[:2])
    browser.wait_for(
        "nodes.filter(node => node.type === 'smart-image' && (node.images || []).length === 2 "
        "&& node.images.every(image => Boolean(image.asset_id))).length === 1",
        timeout=30,
    )
    multi_id = str(
        browser.evaluate(
            "nodes.find(node => node.type === 'smart-image' && (node.images || []).length === 2)?.id || ''"
        )
        or ""
    )
    if not multi_id:
        raise AssertionError("native import did not create the ordinary multi-image node")
    multi_selector = f'.image-node[data-id={json.dumps(multi_id)}]'
    ordinary_ratios = browser.evaluate(
        f"""(() => {{
          const node = nodes.find(item => item.id === {json.dumps(multi_id)});
          const element = document.querySelector({json.dumps(multi_selector)});
          const thumbs = [...element.querySelectorAll('.thumb-item')];
          return (node.images || []).map((image, index) => {{
            const rect = thumbs[index]?.getBoundingClientRect();
            return {{asset_id:image.asset_id || '', name:image.name || '', display_ratio:rect ? rect.width / rect.height : 0}};
          }});
        }})()"""
    )
    if not isinstance(ordinary_ratios, list) or len(ordinary_ratios) != 2:
        raise AssertionError(f"ordinary multi-image geometry is incomplete: {ordinary_ratios!r}")

    browser.native_drag(multi_selector, dx=-48, dy=-24, relative=(0.03, 0.3))
    blank_point = browser.evaluate(
        """(() => {
          const shell = document.querySelector('#shell').getBoundingClientRect();
          const blocked = [...document.querySelectorAll('.image-node,.smart-top-left,.smart-minimap,#composer')]
            .map(element => element.getBoundingClientRect())
            .filter(rect => rect.width > 0 && rect.height > 0);
          for (const ry of [0.64, 0.54, 0.44, 0.74, 0.92, 0.88]) {
            for (const rx of [0.72, 0.82, 0.62, 0.9]) {
              const x = shell.left + shell.width * rx;
              const y = shell.top + shell.height * ry;
              if (!blocked.some(rect => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom)) {
                return {x, y, shell:{x:shell.left,y:shell.top,width:shell.width,height:shell.height}};
              }
            }
          }
          return null;
        })()"""
    )
    if not isinstance(blank_point, dict):
        raise AssertionError("native create menu has no unobscured canvas point")
    shell_rect = blank_point["shell"]
    browser.native_click(
        "#shell",
        relative=(
            (blank_point["x"] - shell_rect["x"]) / shell_rect["width"],
            (blank_point["y"] - shell_rect["y"]) / shell_rect["height"],
        ),
        button="right",
    )
    browser.wait_for("document.querySelector('#createMenu')?.classList.contains('open') === true")
    browser.native_click('#createMenu [data-create-type="image"]')
    browser.wait_for(
        f"nodes.some(node => node.type === 'smart-image' && node.id !== {json.dumps(multi_id)} "
        "&& !(node.images || []).length)"
    )
    square_id = str(
        browser.evaluate(
            f"nodes.find(node => node.type === 'smart-image' && node.id !== {json.dumps(multi_id)} "
            "&& !(node.images || []).length)?.id || ''"
        )
        or ""
    )
    if not square_id:
        raise AssertionError("native create menu did not create the second image node")
    square_selector = f'.image-node[data-id={json.dumps(square_id)}]'
    square_drop_selector = f"{square_selector} .node-drop"
    square_drop_point = browser.evaluate(
        f"""
        (() => {{
          const element = document.querySelector({json.dumps(square_drop_selector)});
          if (!element) return null;
          const rect = element.getBoundingClientRect();
          for (const yFraction of [0.15, 0.3, 0.5, 0.7, 0.85]) {{
            for (const xFraction of [0.85, 0.7, 0.5, 0.3, 0.15]) {{
              const x = rect.left + rect.width * xFraction;
              const y = rect.top + rect.height * yFraction;
              const hit = document.elementFromPoint(x, y);
              if (hit === element || element.contains(hit)) return {{xFraction, yFraction}};
            }}
          }}
          return null;
        }})()
        """
    )
    if not isinstance(square_drop_point, dict):
        raise AssertionError("square image drop area has no unobscured native click point")
    browser.native_click(
        square_drop_selector,
        relative=(square_drop_point["xFraction"], square_drop_point["yFraction"]),
    )
    browser.wait_for(f"document.querySelector({json.dumps(upload_selector)})")
    browser.native_upload(upload_selector, source_paths[2:])
    browser.wait_for(
        f"(nodes.find(node => node.id === {json.dumps(square_id)})?.images || []).length === 1 "
        f"&& Boolean(nodes.find(node => node.id === {json.dumps(square_id)})?.images?.[0]?.asset_id)",
        timeout=30,
    )
    square_drag_distance = 54
    square_drag_dy = 26
    square_drag_point = browser.evaluate(
        f"""
        (() => {{
          const element = document.querySelector({json.dumps(square_selector)});
          if (!element) return null;
          const rect = element.getBoundingClientRect();
          for (const yFraction of [0.08, 0.18, 0.32, 0.5, 0.72]) {{
            for (const xFraction of [0.85, 0.72, 0.58, 0.42, 0.25]) {{
              const x = rect.left + rect.width * xFraction;
              const y = rect.top + rect.height * yFraction;
              const hit = document.elementFromPoint(x, y);
              const dragX = [{square_drag_distance}, {-square_drag_distance}].find(delta =>
                x + delta >= 0
                && x + delta < innerWidth
                && y + {square_drag_dy} >= 0
                && y + {square_drag_dy} < innerHeight
              );
              if (dragX !== undefined && (hit === element || element.contains(hit))) {{
                return {{xFraction, yFraction, dragX}};
              }}
            }}
          }}
          return null;
        }})()
        """
    )
    if not isinstance(square_drag_point, dict):
        raise AssertionError("square image node has no unobscured native drag point")
    browser.native_drag(
        square_selector,
        dx=square_drag_point["dragX"],
        dy=square_drag_dy,
        relative=(square_drag_point["xFraction"], square_drag_point["yFraction"]),
    )

    selection_box = browser.evaluate(
        """(() => {
          const shell = document.querySelector('#shell');
          const rect = shell.getBoundingClientRect();
          const find = (xFractions, yFractions, requireBlank) => {
            for (const yFraction of yFractions) {
              for (const xFraction of xFractions) {
                const x = rect.left + rect.width * xFraction;
                const y = rect.top + rect.height * yFraction;
                const hit = document.elementFromPoint(x, y);
                const blocked = requireBlank && hit?.closest(
                  '.image-node,#composer,.smart-top-left,.smart-toolbar,.smart-minimap,#createMenu'
                );
                if (hit && shell.contains(hit) && !blocked) return {x, y, xFraction, yFraction};
              }
            }
            return null;
          };
          return {
            start: find([0.02, 0.05, 0.1, 0.15], [0.08, 0.15, 0.25, 0.35], true),
            end: find([0.95, 0.9, 0.85, 0.8], [0.98, 0.95, 0.9, 0.82, 0.74, 0.66], false),
          };
        })()"""
    )
    if not isinstance(selection_box, dict) or not selection_box.get("start") or not selection_box.get("end"):
        raise AssertionError("native box selection has no unobscured shell endpoints")
    selection_start = selection_box["start"]
    selection_end = selection_box["end"]
    browser.native_drag(
        "#shell",
        dx=float(selection_end["x"]) - float(selection_start["x"]),
        dy=float(selection_end["y"]) - float(selection_start["y"]),
        relative=(selection_start["xFraction"], selection_start["yFraction"]),
        modifiers=("Control",),
        release_selector="#shell",
    )
    selected = browser.evaluate("[...selectedIds].sort()")
    if selected != sorted((multi_id, square_id)):
        raise AssertionError(f"native box selection did not select both image nodes: {selected!r}")
    browser.native_press("g", modifiers=("Control",))
    try:
        browser.wait_for(
            "nodes.filter(node => node.type === 'smart-group').length === 1 "
            "&& nodes.filter(node => node.type === 'smart-image').length === 0"
        )
    except Exception as error:
        key_state = browser.evaluate(
            "({active:document.activeElement?.tagName+'#'+(document.activeElement?.id||''), "
            "selected:[...selectedIds].sort(), groups:nodes.filter(node=>node.type==='smart-group').length, "
            "images:nodes.filter(node=>node.type==='smart-image').length})"
        )
        raise AssertionError(f"native group shortcut did not create a smart group: {key_state!r}") from error
    group_id = str(browser.evaluate("nodes.find(node => node.type === 'smart-group')?.id || ''") or "")
    group_selector = f'.image-node.smart-group-node[data-id={json.dumps(group_id)}]'
    browser.evaluate(
        "(async () => { await new Promise(resolve => requestAnimationFrame(() => "
        "requestAnimationFrame(resolve))); return true; })()",
        await_promise=True,
    )
    group_select_point = browser.evaluate(
        f"""(() => {{
          const element = document.querySelector({json.dumps(group_selector)});
          const rect = element?.getBoundingClientRect();
          if (!rect) return null;
          for (const yFraction of [0.08, 0.18, 0.5, 0.82, 0.94]) {{
            for (const xFraction of [0.96, 0.82, 0.5, 0.18, 0.04]) {{
              const hit = document.elementFromPoint(rect.left + rect.width * xFraction, rect.top + rect.height * yFraction);
              const blocked = hit?.closest('.smart-node-floating-menu,.node-resize-handle,button,input,select,textarea,a');
              if ((hit === element || element.contains(hit)) && !blocked) return {{xFraction, yFraction}};
            }}
          }}
          return null;
        }})()"""
    )
    if not isinstance(group_select_point, dict):
        group_surface_state = browser.evaluate(
            f"""(() => {{
              const element = document.querySelector({json.dumps(group_selector)});
              const composer = document.querySelector('#composer');
              const handle = element?.querySelector('.node-resize-handle');
              const asRect = target => {{
                const rect = target?.getBoundingClientRect();
                return rect ? {{left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom}} : null;
              }};
              return {{
                group:asRect(element),
                composer:asRect(composer),
                handle:asRect(handle),
                selectedId,
                selectedIds:[...selectedIds],
                composerStyle:{{left:composer?.style.left, top:composer?.style.top, maxWidth:composer?.style.maxWidth}},
                worldTransform:getComputedStyle(world).transform,
                viewport:{{innerWidth:window.innerWidth, innerHeight:window.innerHeight, shellWidth:shell.clientWidth, shellHeight:shell.clientHeight}},
              }};
            }})()"""
        )
        raise AssertionError(
            f"group node has no unobscured native selection surface: {group_surface_state!r}"
        )
    browser.native_click(
        group_selector,
        relative=(group_select_point["xFraction"], group_select_point["yFraction"]),
    )
    browser.wait_for(f"selectedId === {json.dumps(group_id)}")
    browser.wait_for("imageClickTimer === null && composerUpdateTimer === 0")
    browser.wait_for("!document.body.classList.contains('smart-node-drag-settling')")
    browser.wait_for(
        "(() => { const rect=document.querySelector('#composer')?.getBoundingClientRect(); "
        "const expected=window.innerWidth <= 900 ? 579 : Math.min(719, window.innerWidth - 72); "
        "return Boolean(rect && rect.width >= expected); })()"
    )
    previous_composer_rect = None
    stable_composer_samples = 0
    for _ in range(30):
        composer_rect = browser.evaluate(
            "(() => { const rect=document.querySelector('#composer')?.getBoundingClientRect(); "
            "return rect ? {left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom} : null; })()"
        )
        if previous_composer_rect and all(
            abs(float(composer_rect[key]) - float(previous_composer_rect[key])) <= 0.5
            for key in ("left", "top", "right", "bottom")
        ):
            stable_composer_samples += 1
            if stable_composer_samples >= 3:
                break
        else:
            stable_composer_samples = 0
        previous_composer_rect = composer_rect
        time.sleep(0.05)
    else:
        raise AssertionError("composer geometry did not settle before native group resize")
    group_ratios = browser.evaluate(
        f"""(() => {{
          const node = nodes.find(item => item.id === {json.dumps(group_id)});
          const element = document.querySelector({json.dumps(group_selector)});
          const thumbs = [...element.querySelectorAll('.thumb-item, .smart-group-single-thumb')];
          return (node.images || []).map((image, index) => {{
            const rect = thumbs[index]?.getBoundingClientRect();
            return {{asset_id:image.asset_id || '', name:image.name || '', display_ratio:rect ? rect.width / rect.height : 0}};
          }});
        }})()"""
    )
    group_size_before = browser.evaluate(
        f"(() => {{ const r=document.querySelector({json.dumps(group_selector)}).getBoundingClientRect(); "
        "return {width:r.width,height:r.height}; })()"
    )
    group_resize_layout = browser.evaluate(
        f"""(() => {{
          const element = document.querySelector({json.dumps(group_selector)});
          const group = element.getBoundingClientRect();
          const composer = document.querySelector('#composer').getBoundingClientRect();
          const targetRight = Math.min(innerWidth - 18, composer.right + 34);
          let dragPoint = null;
          for (const yFraction of [0.08, 0.18, 0.5, 0.82, 0.94]) {{
            for (const xFraction of [0.96, 0.82, 0.5, 0.18, 0.04]) {{
              const x = group.left + group.width * xFraction;
              const y = group.top + group.height * yFraction;
              if (document.elementFromPoint(x, y) === element) {{
                dragPoint = {{xFraction, yFraction}};
                break;
              }}
            }}
            if (dragPoint) break;
          }}
          return {{
            dragPoint,
            moveX: targetRight - group.right,
            resizeX: 6,
            groupBefore:{{left:group.left,top:group.top,right:group.right,bottom:group.bottom}},
            composer:{{left:composer.left,top:composer.top,right:composer.right,bottom:composer.bottom}},
            composerMetrics:{{
              rectHeight:composer.height,
              offsetHeight:document.querySelector('#composer').offsetHeight,
              scrollHeight:document.querySelector('#composer').scrollHeight,
              updateTimer:composerUpdateTimer,
              imageTimerActive:imageClickTimer !== null,
            }},
            targetRight,
          }};
        }})()"""
    )
    if not isinstance(group_resize_layout.get("dragPoint"), dict):
        raise AssertionError("group node has no unobscured native drag surface")
    try:
        browser.native_drag(
            group_selector,
            dx=float(group_resize_layout["moveX"]),
            dy=0,
            relative=(
                group_resize_layout["dragPoint"]["xFraction"],
                group_resize_layout["dragPoint"]["yFraction"],
            ),
            release_selector=group_selector,
        )
    except RuntimeError as error:
        raise AssertionError(
            f"native group drag failed after stable layout {group_resize_layout!r}"
        ) from error
    resize_handle_evidence = browser.evaluate(
        f"""(() => {{
          const group = document.querySelector({json.dumps(group_selector)});
          const handle = document.querySelector({json.dumps(f'{group_selector} .node-resize-handle')});
          const composer = document.querySelector('#composer');
          const rect = handle?.getBoundingClientRect();
          if (!rect) return {{visible:false}};
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          const box = element => {{ const value=element?.getBoundingClientRect(); return value ? {{left:value.left,top:value.top,right:value.right,bottom:value.bottom}} : null; }};
          return {{
            visible:hit === handle || handle.contains(hit),
            group:box(group),
            handle:box(handle),
            composer:box(composer),
            viewport:{{width:window.innerWidth,height:window.innerHeight}},
            hit:hit ? `${{hit.tagName.toLowerCase()}}${{hit.id ? `#${{hit.id}}` : ''}}.${{String(hit.className || '')}}` : '',
          }};
        }})()"""
    )
    if resize_handle_evidence.get("visible") is not True:
        raise AssertionError(
            f"group move did not expose its native resize handle: layout={group_resize_layout}, after={resize_handle_evidence}"
        )
    browser.native_drag(
        f"{group_selector} .node-resize-handle",
        dx=-6,
        dy=-6,
        relative=(0.5, 0.5),
        release_selector="#shell",
    )
    group_size_after = browser.evaluate(
        f"(() => {{ const r=document.querySelector({json.dumps(group_selector)}).getBoundingClientRect(); "
        "return {width:r.width,height:r.height}; })()"
    )
    organized_screenshot = browser.capture_screenshot(viewport_root / "03-image-organized.png")

    browser.native_press("g", modifiers=("Control", "Shift"))
    browser.wait_for(
        "nodes.filter(node => node.type === 'smart-group').length === 0 "
        "&& nodes.filter(node => node.type === 'smart-image' && (node.images || []).length === 1).length === 3"
    )
    ungrouped = browser.evaluate(
        """(() => nodes.filter(node => node.type === 'smart-image' && (node.images || []).length === 1).map(node => {
          const element = document.querySelector(`.image-node[data-id="${CSS.escape(node.id)}"]`);
          const image = element?.querySelector('.image-wrap, .thumb-item');
          const nodeRect = element?.getBoundingClientRect();
          const imageRect = image?.getBoundingClientRect();
          return {
            node_id:node.id,
            asset_id:node.images[0]?.asset_id || '',
            name:node.images[0]?.name || '',
            display_ratio:imageRect ? imageRect.width / imageRect.height : 0,
            rect:nodeRect ? {x:nodeRect.x,y:nodeRect.y,width:nodeRect.width,height:nodeRect.height} : {},
            clipped:Boolean(nodeRect && imageRect && (
              imageRect.left < nodeRect.left - 1 || imageRect.top < nodeRect.top - 1
              || imageRect.right > nodeRect.right + 1 || imageRect.bottom > nodeRect.bottom + 1
            )),
          };
        }))()"""
    )
    if not isinstance(ungrouped, list) or len(ungrouped) != 3:
        raise AssertionError(f"ungroup did not release three independent image nodes: {ungrouped!r}")
    ungrouped_screenshot = browser.capture_screenshot(viewport_root / "04-image-ungrouped.png")

    browser.native_press("Escape")
    controls_reachable = bool(
        browser.evaluate(
            "(() => ['#smartSaveButton','.smart-back'].every(selector => { "
            "const r=document.querySelector(selector)?.getBoundingClientRect(); "
            "return r && r.width>0 && r.height>0 && r.left>=0 && r.top>=0 "
            "&& r.right<=innerWidth && r.bottom<=innerHeight; }))()"
        )
    )
    page_horizontal_overflow = bool(
        browser.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    )
    browser.native_click("#smartSaveButton")
    browser.wait_for(
        "document.querySelector('#projectSaveState')?.dataset.state === 'saved' "
        "&& document.querySelector('#smartSaveButton')?.dataset.busy !== '1'",
        timeout=30,
    )
    durable_before_reopen = project_scope_snapshot(store, project_id)
    media_before = {
        asset_id: item["content_sha256"] for asset_id, item in durable_before_reopen["media"].items()
    }
    if len(media_before) != 3:
        raise AssertionError(f"organization project did not own three originals: {sorted(media_before)}")
    browser.native_click(".smart-back")
    browser.wait_for("location.pathname === '/'", timeout=30)
    browser.wait_for("document.querySelectorAll('#projectGrid .project-card').length >= 1")
    card_index = browser.evaluate(
        f"[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
        f"card.querySelector('h2')?.textContent === {json.dumps(project_name)})"
    )
    if not isinstance(card_index, int) or card_index < 0:
        raise AssertionError("organized project was not visible after close")
    browser.native_click(f"#projectGrid .project-card:nth-child({card_index + 1}) .project-open")
    browser.wait_for(
        f"location.pathname === '/static/smart-canvas.html' "
        f"&& new URLSearchParams(location.search).get('project') === {json.dumps(project_id)} "
        "&& document.querySelectorAll('.image-node[data-id]').length === 3",
        timeout=30,
    )
    browser.wait_for("document.querySelector('#projectSaveState')?.dataset.state === 'saved'", timeout=30)
    durable_after_reopen = project_scope_snapshot(store, project_id)
    media_after = {
        asset_id: item["content_sha256"] for asset_id, item in durable_after_reopen["media"].items()
    }
    reopened_nodes = list((durable_after_reopen.get("canvas") or {}).get("nodes") or [])
    reopened_layout = sorted(
        (
            str((node.get("images") or [{}])[0].get("asset_id") or ""),
            float(node.get("x") or 0),
            float(node.get("y") or 0),
            float(node.get("w") or 0),
            float(node.get("h") or 0),
        )
        for node in reopened_nodes
        if node.get("type") == "smart-image" and len(node.get("images") or []) == 1
    )
    expected_layout = sorted(
        (
            str((node.get("images") or [{}])[0].get("asset_id") or ""),
            float(node.get("x") or 0),
            float(node.get("y") or 0),
            float(node.get("w") or 0),
            float(node.get("h") or 0),
        )
        for node in ((durable_before_reopen.get("canvas") or {}).get("nodes") or [])
        if node.get("type") == "smart-image" and len(node.get("images") or []) == 1
    )
    layout_preserved = len(reopened_layout) == len(expected_layout) and all(
        reopened[0] == expected[0]
        and all(abs(actual - wanted) <= 0.01 for actual, wanted in zip(reopened[1:], expected[1:]))
        for reopened, expected in zip(reopened_layout, expected_layout)
    )
    reopened_preserved = media_after == media_before and layout_preserved
    if not reopened_preserved:
        raise AssertionError(
            "reopened organization project changed media identity or world layout: "
            f"expected={expected_layout!r}, actual={reopened_layout!r}"
        )

    media_records = {item["record"]["name"]: (asset_id, item) for asset_id, item in durable_before_reopen["media"].items()}
    assets = []
    for source in sources:
        matched = media_records.get(source["name"])
        if not matched:
            raise AssertionError(f"uploaded source is missing from project media: {source['name']}")
        asset_id, media_item = matched
        assets.append({"asset_id": asset_id, **source})
        if media_item["content_sha256"] != source["sha256"]:
            raise AssertionError(f"uploaded source bytes changed: {source['name']}")

    asset_id_by_name = {asset["name"]: asset["asset_id"] for asset in assets}
    for ratio in [*ordinary_ratios, *group_ratios, *ungrouped]:
        if not ratio.get("asset_id"):
            ratio["asset_id"] = asset_id_by_name.get(str(ratio.get("name") or ""), "")

    source_ratio_by_asset = {
        asset["asset_id"]: asset["source_width"] / asset["source_height"] for asset in assets
    }
    for label, ratios in (
        ("ordinary", ordinary_ratios),
        ("group", group_ratios),
        ("ungroup", [{"asset_id": item["asset_id"], "display_ratio": item["display_ratio"]} for item in ungrouped]),
    ):
        for ratio in ratios:
            source_ratio = source_ratio_by_asset.get(str(ratio.get("asset_id") or ""))
            if source_ratio is None or abs(float(ratio.get("display_ratio") or 0) - source_ratio) > 0.03:
                raise AssertionError(f"{label} display changed source aspect ratio: {ratio!r}")

    operation_path = viewport_root / "organization-operations.jsonl"
    organization_operations = [
        {**operation, "sequence": sequence}
        for sequence, operation in enumerate(browser.operations[operation_start:], start=1)
    ]
    operation_path.write_text(
        "".join(json.dumps(operation, ensure_ascii=False) + "\n" for operation in organization_operations),
        encoding="utf-8",
    )
    return (
        {
            "status": "passed",
            "project_id": project_id,
            "reopened_project_id": project_id,
            "operations": relative(operation_path, artifacts_root),
            "assets": assets,
            "media_before": media_before,
            "media_after": media_after,
            "reopened_media_hashes": media_after,
            "ordinary_ratios": ordinary_ratios,
            "group_ratios": group_ratios,
            "ungroup_ratios": [
                {"asset_id": item["asset_id"], "display_ratio": item["display_ratio"]}
                for item in ungrouped
            ],
            "group_size_before": group_size_before,
            "group_size_after": group_size_after,
            "ungrouped": ungrouped,
            "controls_reachable": controls_reachable,
            "page_horizontal_overflow": page_horizontal_overflow,
            "reopened_preserved": reopened_preserved,
            "organized_screenshot": relative(organized_screenshot, artifacts_root),
            "ungrouped_screenshot": relative(ungrouped_screenshot, artifacts_root),
        },
        [relative(organized_screenshot, artifacts_root), relative(ungrouped_screenshot, artifacts_root)],
    )


def unexpected_console_errors(errors: list[dict]) -> list[dict]:
    unexpected = []
    for error in errors:
        entry = (error.get("params") or {}).get("entry") or {}
        known_favicon_404 = (
            error.get("method") == "Log.entryAdded"
            and entry.get("source") == "network"
            and urllib.parse.urlsplit(entry.get("url") or "").path == "/favicon.ico"
            and "404" in (entry.get("text") or "")
        )
        if not known_favicon_404:
            unexpected.append(error)
    return unexpected


def run_project_roundtrip(
    browser: NativeBrowser,
    *,
    store: ProjectStore,
    base_url: str,
    viewport: tuple[int, int],
    viewport_root: Path,
    artifacts_root: Path,
) -> tuple[dict, str]:
    width, height = viewport
    project_name = f"T05 roundtrip {width}x{height}"
    browser.navigate(base_url + "/")
    browser.wait_for("document.querySelector('#showCreate') && document.querySelector('#projectName')")
    browser.native_click("#showCreate")
    browser.wait_for("document.querySelector('#createDialog')?.open === true")
    browser.native_click("#projectName")
    browser.native_type(project_name)
    browser.native_click('#createForm button[type="submit"]')
    browser.wait_for(
        "location.pathname === '/static/smart-canvas.html' && new URLSearchParams(location.search).has('project')",
        timeout=30,
    )
    identity = browser.evaluate(
        "(() => { const params = new URLSearchParams(location.search); "
        "return {project_id: params.get('project'), canvas_id: params.get('id')}; })()"
    )
    project_id = str((identity or {}).get("project_id") or "")
    canvas_id = str((identity or {}).get("canvas_id") or "")
    if not project_id or not canvas_id:
        raise AssertionError(f"project create did not expose durable identity: {identity!r}")

    browser.wait_for(
        "typeof canvas !== 'undefined' && canvas?.format === 'firefly-canvas-v1' && Array.isArray(nodes) "
        "&& document.querySelector('#projectSaveState')?.dataset.state === 'saved'",
        timeout=30,
    )
    browser.native_click("#shell", relative=(0.48, 0.45), button="right")
    browser.wait_for("document.querySelector('#createMenu')?.classList.contains('open') === true")
    browser.native_click('#createMenu [data-create-type="prompt"]')
    browser.wait_for("document.querySelectorAll('.image-node[data-id]').length === 1")
    node_id = str(browser.evaluate("document.querySelector('.image-node[data-id]')?.dataset.id || ''") or "")
    if not node_id:
        raise AssertionError("prompt creation did not expose a durable node identity")
    node_selector = f'.image-node[data-id={json.dumps(node_id)}]'
    browser.native_click(f"{node_selector} .prompt-node-text")
    browser.native_type("durable project roundtrip")
    position_before = browser.evaluate(
        f"(() => {{ const node = nodes.find(item => item.id === {json.dumps(node_id)}); "
        "return node ? {x: Number(node.x), y: Number(node.y)} : null; })()"
    )
    # Prompt controls consume mousedown; the left padding is the stable node drag surface.
    browser.native_drag(node_selector, dx=52, dy=34, relative=(0.03, 0.3))
    position_after_drag = browser.evaluate(
        f"(() => {{ const node = nodes.find(item => item.id === {json.dumps(node_id)}); "
        "return node ? {x: Number(node.x), y: Number(node.y)} : null; })()"
    )
    if not isinstance(position_before, dict) or not isinstance(position_after_drag, dict):
        raise AssertionError("project roundtrip could not observe node positions")

    browser.native_click("#smartSaveButton")
    browser.wait_for(
        "document.querySelector('#projectSaveState')?.dataset.state === 'saved' "
        "&& document.querySelector('#smartSaveButton')?.dataset.busy !== '1'",
        timeout=30,
    )
    saved_project = browser.evaluate(
        f"fetch('/api/project-library/{project_id}').then(response => {{ "
        "if (!response.ok) throw new Error(`project snapshot ${response.status}`); return response.json(); })",
        await_promise=True,
    )
    revision_after_save = int(((saved_project or {}).get("canvas") or {}).get("revision") or 0)
    if revision_after_save < 1:
        raise AssertionError(f"project save did not advance revision: {revision_after_save}")

    browser.native_click(".smart-back")
    browser.wait_for("location.pathname === '/'", timeout=30)
    browser.wait_for("document.querySelectorAll('#projectGrid .project-card').length >= 1")

    referenced = store.import_media(
        project_id,
        image_bytes("USER", (62, 115, 188)),
        "user-original.png",
        "image/png",
        {"type": "user-import", "locator": "file:///D:/fixtures/user-original.png"},
    )
    generated = store.import_media(
        project_id,
        image_bytes("GENERATED", (180, 88, 72)),
        "generated-original.png",
        "image/png",
        {"type": "generation", "provider_id": "fixture", "model": "fixture-image", "task_id": "t05-task"},
    )
    unreferenced = store.import_media(
        project_id,
        image_bytes("OWNED", (86, 151, 104)),
        "project-owned-unreferenced.png",
        "image/png",
        {"type": "user-save", "locator": "file:///D:/fixtures/project-owned-unreferenced.png"},
    )
    source_before_scope = store.open_project(project_id)
    store.apply_mutations(
        project_id,
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "t05-user-original",
                    "type": "smart-image",
                    "x": 520,
                    "y": 180,
                    "w": 316,
                    "h": 194,
                    "images": [
                        {
                            "asset_id": referenced["asset_id"],
                            "kind": "image",
                            "name": "user-original.png",
                            "natural_w": 480,
                            "natural_h": 320,
                        }
                    ],
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "t05-generated-original",
                    "type": "smart-image",
                    "x": 900,
                    "y": 180,
                    "w": 316,
                    "h": 194,
                    "images": [
                        {
                            "asset_id": generated["asset_id"],
                            "kind": "image",
                            "name": "generated-original.png",
                            "natural_w": 480,
                            "natural_h": 320,
                        }
                    ],
                },
            },
            {"op": "upsert_edge", "edge": {"from": node_id, "to": "t05-generated-original", "kind": "input"}},
            {
                "op": "set_canvas_meta",
                "logs": [
                    {
                        "id": "t05-generation-record",
                        "task_id": "t05-task",
                        "output_asset_id": generated["asset_id"],
                    }
                ],
            },
        ],
        base_revision=int(source_before_scope["canvas"]["revision"]),
    )
    store.flush(project_id)
    scoped_source = store.open_project(project_id)
    generated_node_before_browser = next(
        (
            node
            for node in (scoped_source["canvas"].get("nodes") or [])
            if node.get("id") == "t05-generated-original"
        ),
        None,
    )
    if not generated_node_before_browser:
        raise AssertionError("generated original is unavailable before the browser save")
    generated_position_before = {
        "x": generated_node_before_browser.get("x"),
        "y": generated_node_before_browser.get("y"),
    }

    source_card_index = browser.evaluate(
        f"[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
        f"card.querySelector('h2')?.textContent === {json.dumps(project_name)})"
    )
    if not isinstance(source_card_index, int) or source_card_index < 0:
        raise AssertionError("augmented source project is unavailable for browser persistence")
    browser.native_click(f"#projectGrid .project-card:nth-child({source_card_index + 1}) .project-open")
    generated_selector = '.image-node[data-id="t05-generated-original"]'
    browser.wait_for(
        f"location.pathname === '/static/smart-canvas.html' "
        f"&& new URLSearchParams(location.search).get('project') === {json.dumps(project_id)} "
        f"&& document.querySelector({json.dumps(generated_selector)}) "
        "&& document.querySelector('#projectSaveState')?.dataset.state === 'saved'",
        timeout=30,
    )
    browser.native_drag(generated_selector, dx=31, dy=19, relative=(0.03, 0.3))
    browser.native_click("#smartSaveButton")
    browser.wait_for("document.querySelector('#projectSaveState')?.dataset.state === 'saved'", timeout=30)
    browser.native_click(".smart-back")
    browser.wait_for("location.pathname === '/'", timeout=30)
    browser.wait_for(
        f"[...document.querySelectorAll('#projectGrid .project-card')].some(card => "
        f"card.querySelector('h2')?.textContent === {json.dumps(project_name)})",
        timeout=30,
    )

    scoped_source = store.open_project(project_id)
    source_canvas = scoped_source["canvas"]
    source_nodes = list(source_canvas.get("nodes") or [])
    source_connections = list(source_canvas.get("connections") or [])
    source_logs = list(source_canvas.get("logs") or [])
    source_media = {item["asset_id"]: store.media_record(project_id, item["asset_id"]) for item in store.list_media(project_id)}
    media_asset_ids = sorted(source_media)
    expected_asset_ids = sorted([referenced["asset_id"], generated["asset_id"], unreferenced["asset_id"]])
    if media_asset_ids != expected_asset_ids:
        raise AssertionError(f"project media scope drifted: {media_asset_ids!r}")
    referenced_ids = {
        image.get("asset_id")
        for node in source_nodes
        for image in (node.get("images") or [])
        if isinstance(image, dict)
    }
    if referenced["asset_id"] not in referenced_ids or generated["asset_id"] not in referenced_ids:
        raise AssertionError("project media originals are not referenced by durable nodes")
    if unreferenced["asset_id"] in referenced_ids:
        raise AssertionError("project-owned unreferenced media unexpectedly became node-owned")
    history_output_asset_id = str((source_logs[0] if source_logs else {}).get("output_asset_id") or "")
    if history_output_asset_id != generated["asset_id"]:
        raise AssertionError("generation record lost its generated original identity")
    generated_node_after_browser = next(
        (node for node in source_nodes if node.get("id") == "t05-generated-original"),
        None,
    )
    generated_position_after = {
        "x": (generated_node_after_browser or {}).get("x"),
        "y": (generated_node_after_browser or {}).get("y"),
    }
    expected_generated_position = {
        "x": generated_position_before["x"] + 31,
        "y": generated_position_before["y"] + 19,
    }
    if generated_position_after != expected_generated_position:
        raise AssertionError(
            f"browser save did not preserve generated-node movement: "
            f"{expected_generated_position!r} != {generated_position_after!r}"
        )
    source_scope = project_scope_snapshot(store, project_id)

    source_card_index = browser.evaluate(
        f"[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
        f"card.querySelector('h2')?.textContent === {json.dumps(project_name)})"
    )
    if not isinstance(source_card_index, int) or source_card_index < 0:
        raise AssertionError("saved source project is unavailable for native duplication")
    card_count_before_duplicate = int(browser.evaluate("document.querySelectorAll('#projectGrid .project-card').length") or 0)
    browser.native_click(
        f"#projectGrid .project-card:nth-child({source_card_index + 1}) .project-duplicate"
    )
    browser.wait_for(
        f"document.querySelectorAll('#projectGrid .project-card').length === {card_count_before_duplicate + 1}",
        timeout=30,
    )
    duplicate_name = f"{project_name[:57]} 副本"
    duplicate_summary = browser.evaluate(
        "fetch('/api/project-library').then(response => response.json()).then(payload => "
        f"payload.projects.find(project => project.name === {json.dumps(duplicate_name)}) || null)",
        await_promise=True,
    )
    duplicate_project_id = str((duplicate_summary or {}).get("id") or "")
    duplicate_canvas_id = str((duplicate_summary or {}).get("canvas_id") or "")
    if not duplicate_project_id or duplicate_project_id == project_id:
        raise AssertionError(f"native duplication did not create a new project identity: {duplicate_summary!r}")
    duplicated = store.open_project(duplicate_project_id)
    duplicate_canvas = duplicated["canvas"]
    duplicate_media = {
        item["asset_id"]: store.media_record(duplicate_project_id, item["asset_id"])
        for item in store.list_media(duplicate_project_id)
    }
    duplicate_media_asset_ids = sorted(duplicate_media)
    duplicate_provenance_preserved = all(
        duplicate_media[asset_id].get("source") == source_media[asset_id].get("source")
        for asset_id in media_asset_ids
    )
    duplicate_paths_independent = all(
        duplicate_media[asset_id]["path"] != source_media[asset_id]["path"]
        and Path(duplicate_media[asset_id]["path"]).read_bytes() == Path(source_media[asset_id]["path"]).read_bytes()
        for asset_id in media_asset_ids
    )
    duplicate_nodes = list(duplicate_canvas.get("nodes") or [])
    duplicate_runtime_isolated = all(
        not (RUNTIME_NODE_FIELDS & set(node))
        for node in duplicate_nodes
    )
    if (
        duplicate_nodes != durable_nodes(source_nodes)
        or duplicate_canvas.get("connections") != source_connections
        or duplicate_canvas.get("logs") != source_logs
        or duplicate_media_asset_ids != media_asset_ids
        or not duplicate_provenance_preserved
        or not duplicate_paths_independent
        or not duplicate_runtime_isolated
    ):
        raise AssertionError("native project duplication did not preserve its durable project scope")
    duplicate_scope = project_scope_snapshot(store, duplicate_project_id)

    export_path = viewport_root / "project-roundtrip.fly"
    export_result = store.export_fly_to(project_id, export_path)
    with zipfile.ZipFile(export_path, "r") as archive:
        export_manifest = json.loads(archive.read("manifest.json"))
        exported_project = json.loads(archive.read("project.json"))
        export_media_index = json.loads(archive.read("media/index.json"))
        exported_canvas = json.loads(archive.read("canvas.json"))
        exported_history = json.loads(archive.read("history.json"))
        export_members = set(archive.namelist())
        export_declared = {item["path"]: item for item in export_manifest.get("files") or []}
        export_manifest_verified = set(export_declared) == export_members - {"manifest.json"} and all(
            export_declared[name].get("size") == len(archive.read(name))
            and export_declared[name].get("sha256") == hashlib.sha256(archive.read(name)).hexdigest()
            for name in export_declared
        )
        export_media_bytes_preserved = all(
            archive.read(f"media/{(export_media_index.get('items') or {})[asset_id]['filename']}")
            == Path(source_media[asset_id]["path"]).read_bytes()
            for asset_id in media_asset_ids
        )
    export_media_asset_ids = sorted((export_media_index.get("items") or {}).keys())
    export_history_preserved = exported_history.get("records") == source_scope["history"]
    if (
        export_result.get("project_id") != project_id
        or export_manifest.get("project_id") != project_id
        or exported_project.get("id") != project_id
        or export_media_asset_ids != media_asset_ids
        or exported_canvas.get("nodes") != source_nodes
        or exported_canvas.get("connections") != source_connections
        or not export_history_preserved
        or not export_manifest_verified
        or not export_media_bytes_preserved
    ):
        raise AssertionError("project export did not preserve the source project scope")

    trashed = store.trash_project(duplicate_project_id)
    source_project_preserved = project_scope_snapshot(store, project_id) == source_scope
    restored = store.restore_project(duplicate_project_id)
    restored_duplicate_preserved = project_scope_snapshot(store, duplicate_project_id) == duplicate_scope
    if not source_project_preserved or not restored_duplicate_preserved:
        raise AssertionError("duplicate trash and restore changed a project scope")

    card_index = browser.evaluate(
        f"[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
        f"card.querySelector('h2')?.textContent === {json.dumps(project_name)})"
    )
    if not isinstance(card_index, int) or card_index < 0:
        raise AssertionError("saved project did not return to the project list")
    browser.native_click(f"#projectGrid .project-card:nth-child({card_index + 1}) .project-open")
    browser.wait_for(
        f"location.pathname === '/static/smart-canvas.html' "
        f"&& new URLSearchParams(location.search).get('project') === {json.dumps(project_id)} "
        f"&& document.querySelector({json.dumps(node_selector)})",
        timeout=30,
    )
    browser.wait_for("document.querySelector('#projectSaveState')?.dataset.state === 'saved'", timeout=30)
    reopened_identity = browser.evaluate(
        "(() => { const params = new URLSearchParams(location.search); "
        "return {project_id: params.get('project'), canvas_id: params.get('id')}; })()"
    )
    reopened_project = browser.evaluate(
        f"fetch('/api/project-library/{project_id}').then(response => {{ "
        "if (!response.ok) throw new Error(`project reopen ${response.status}`); return response.json(); })",
        await_promise=True,
    )
    reopened_canvas = (reopened_project or {}).get("canvas") or {}
    reopened_nodes = list(reopened_canvas.get("nodes") or [])
    reopened_connections = list(reopened_canvas.get("connections") or [])
    reopened_history = list(reopened_canvas.get("logs") or [])
    reopened_scope = project_scope_snapshot(store, project_id)
    reopened_canvas_preserved = (
        reopened_nodes == source_nodes
        and reopened_connections == source_connections
        and list(reopened_canvas.get("logs") or []) == source_logs
    )
    reopened_history_preserved = reopened_history == source_scope["history"]
    reopened_media_preserved = reopened_scope["media"] == source_scope["media"]
    source_project_preserved = source_project_preserved and reopened_scope == source_scope
    if not all(
        (
            reopened_canvas_preserved,
            reopened_history_preserved,
            reopened_media_preserved,
            source_project_preserved,
        )
    ):
        raise AssertionError("reopened project did not preserve its complete durable scope")
    reopened_node = next((node for node in reopened_nodes if node.get("id") == node_id), None)
    if not reopened_node or reopened_node.get("text") != "durable project roundtrip":
        raise AssertionError("reopened project did not preserve prompt content")
    position_after = {"x": reopened_node.get("x"), "y": reopened_node.get("y")}
    if position_after != position_after_drag:
        raise AssertionError(
            f"reopened project changed saved position: {position_after_drag!r} != {position_after!r}"
        )
    revision_after_save = int(source_canvas.get("revision") or 0)
    revision_after_reopen = int(reopened_canvas.get("revision") or 0)
    if revision_after_reopen != revision_after_save:
        raise AssertionError(
            f"reopened revision drifted: {revision_after_save} != {revision_after_reopen}"
        )
    screenshot = browser.capture_screenshot(viewport_root / "00-project-roundtrip.png")
    return (
        {
            "status": "passed",
            "project_id": project_id,
            "canvas_id": canvas_id,
            "reopened_project_id": str((reopened_identity or {}).get("project_id") or ""),
            "node_id": node_id,
            "node_count": len(reopened_nodes),
            "position_before": position_before,
            "position_after": position_after,
            "revision_after_save": revision_after_save,
            "revision_after_reopen": revision_after_reopen,
            "save_status": str(browser.evaluate("document.querySelector('#projectSaveState')?.dataset.state || ''")),
            "browser_augmented_save": True,
            "generated_position_before": generated_position_before,
            "generated_position_after": generated_position_after,
            "connection_count": len(reopened_connections),
            "media_asset_ids": media_asset_ids,
            "referenced_asset_id": referenced["asset_id"],
            "generated_asset_id": generated["asset_id"],
            "unreferenced_asset_id": unreferenced["asset_id"],
            "history_output_asset_id": history_output_asset_id,
            "duplicate_via_native_ui": True,
            "duplicate_project_id": duplicate_project_id,
            "duplicate_canvas_id": duplicate_canvas_id,
            "duplicate_node_count": len(duplicate_canvas.get("nodes") or []),
            "duplicate_connection_count": len(duplicate_canvas.get("connections") or []),
            "duplicate_media_asset_ids": duplicate_media_asset_ids,
            "duplicate_provenance_preserved": duplicate_provenance_preserved,
            "duplicate_paths_independent": duplicate_paths_independent,
            "export_project_id": str(export_result.get("project_id") or ""),
            "export_media_asset_ids": export_media_asset_ids,
            "export_package": relative(export_path, artifacts_root),
            "export_manifest_verified": export_manifest_verified,
            "export_media_bytes_preserved": export_media_bytes_preserved,
            "export_history_preserved": export_history_preserved,
            "trashed_project_id": str(trashed.get("id") or ""),
            "restored_project_id": str(restored.get("id") or ""),
            "source_project_preserved": source_project_preserved,
            "restored_duplicate_preserved": restored_duplicate_preserved,
            "reopened_canvas_preserved": reopened_canvas_preserved,
            "reopened_history_preserved": reopened_history_preserved,
            "reopened_media_preserved": reopened_media_preserved,
        },
        relative(screenshot, artifacts_root),
    )


def run_viewport(
    browser_path: Path,
    viewport: tuple[int, int],
    run_root: Path,
    artifacts_root: Path,
) -> tuple[dict, dict]:
    width, height = viewport
    viewport_root = artifacts_root / f"viewport-{width}x{height}"
    viewport_root.mkdir(parents=True, exist_ok=False)
    operations_path = viewport_root / "operations.jsonl"
    console_path = viewport_root / "console-errors.json"
    requests_path = viewport_root / "requests.json"
    operations_path.write_text("", encoding="utf-8")
    write_json(console_path, [])
    write_json(requests_path, [])
    screenshots: list[str] = []
    browser = None
    store = None
    server = None
    started = time.monotonic()
    try:
        store = ProjectStore(run_root / "projects", snapshot_delay=0)
        project_id = seed_store(store)
        cleanup_project_id, cleanup_asset_id = seed_cleanup_preview(store)
        cleanup_project_root = store.root / cleanup_project_id
        cleanup_before = project_tree_snapshot(cleanup_project_root)
        app = load_production_app(run_root / "app", store)
        server = AsgiBrowserServer(app)
        server.start()
        browser = NativeBrowser.launch(browser_path)
        browser.set_environment(viewport)
        project_roundtrip, roundtrip_screenshot = run_project_roundtrip(
            browser,
            store=store,
            base_url=server.base_url,
            viewport=viewport,
            viewport_root=viewport_root,
            artifacts_root=artifacts_root,
        )
        screenshots.append(roundtrip_screenshot)
        image_organization, organization_screenshots = run_image_organization(
            browser,
            store=store,
            base_url=server.base_url,
            viewport=viewport,
            viewport_root=viewport_root,
            artifacts_root=artifacts_root,
        )
        screenshots.extend(organization_screenshots)
        browser.navigate(server.base_url + "/")
        browser.wait_for("document.querySelectorAll('#projectGrid .project-card').length >= 1")
        cleanup_index = browser.evaluate(
            "[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
            "card.querySelector('h2')?.textContent === 'Cleanup Preview Evidence')"
        )
        if not isinstance(cleanup_index, int) or cleanup_index < 0:
            raise AssertionError("cleanup project is unavailable from the project list")
        cleanup_operation = browser.native_click(
            f"#projectGrid .project-card:nth-child({cleanup_index + 1}) .project-cleanup-preview"
        )
        browser.wait_for(
            "document.querySelector('#cleanupPreviewDialog')?.open === true "
            "&& document.querySelector('#cleanupCandidateCount')?.textContent === '1' "
            "&& document.querySelectorAll('#cleanupCandidateList .cleanup-candidate').length === 1 "
            "&& document.querySelector('#cleanupCandidateList img')?.complete === true "
            "&& document.querySelector('#cleanupCandidateList img')?.naturalWidth > 0",
            timeout=30,
        )
        cleanup_observed = browser.evaluate(
            "(() => ({"
            "count:document.querySelector('#cleanupCandidateCount')?.textContent || '',"
            "source:document.querySelector('.cleanup-candidate span')?.textContent || '',"
            "scope:document.querySelector('#cleanupPermanentScope')?.textContent || '',"
            "thumbnail:document.querySelector('.cleanup-candidate img')?.currentSrc || ''"
            "}))()"
        )
        cleanup_screenshot = browser.capture_screenshot(viewport_root / "cleanup-preview.png")
        screenshots.append(relative(cleanup_screenshot, artifacts_root))
        cleanup_after = project_tree_snapshot(cleanup_project_root)
        if cleanup_after != cleanup_before:
            raise AssertionError("cleanup preview mutated the project tree")
        if "失败" not in str(cleanup_observed.get("source") or ""):
            raise AssertionError(f"cleanup source category is missing: {cleanup_observed!r}")
        if "不会删除任何内容" not in str(cleanup_observed.get("scope") or ""):
            raise AssertionError(f"cleanup permanent scope is unclear: {cleanup_observed!r}")
        browser.native_click("#closeCleanupPreview")
        browser.wait_for("document.querySelector('#cleanupPreviewDialog')?.open === false")
        cleanup_preview = {
            "project_id": cleanup_project_id,
            "candidate_asset_id": cleanup_asset_id,
            "candidate_count": cleanup_observed.get("count"),
            "source": cleanup_observed.get("source"),
            "scope": cleanup_observed.get("scope"),
            "thumbnail": cleanup_observed.get("thumbnail"),
            "project_tree_unchanged": True,
            "operation": cleanup_operation,
            "screenshot": relative(cleanup_screenshot, artifacts_root),
        }
        # T16: the confirmed cleanup execution deletes exactly the previewed
        # candidate and touches nothing else in the project tree.
        browser.native_click(
            f"#projectGrid .project-card:nth-child({cleanup_index + 1}) .project-cleanup-preview"
        )
        browser.wait_for(
            "document.querySelector('#cleanupPreviewDialog')?.open === true "
            "&& document.querySelector('#cleanupCandidateCount')?.textContent === '1' "
            "&& document.querySelector('#confirmCleanup')?.hidden === false "
            "&& document.querySelector('#confirmCleanup')?.disabled === false",
            timeout=30,
        )
        confirm_operation = browser.native_click("#confirmCleanup")
        browser.wait_for(
            "document.querySelector('#confirmCleanup')?.hidden === true "
            "&& document.querySelector('#cleanupConfirmArea')?.hidden === true "
            "&& document.querySelector('.cleanup-empty')?.textContent?.includes('已永久删除')",
            timeout=30,
        )
        confirm_observed = browser.evaluate(
            "(() => ({"
            "note:document.querySelector('#cleanupConfirmNote')?.textContent || '',"
            "done:document.querySelector('.cleanup-empty')?.textContent || ''"
            "}))()"
        )
        cleanup_confirm_screenshot = browser.capture_screenshot(viewport_root / "cleanup-confirm.png")
        screenshots.append(relative(cleanup_confirm_screenshot, artifacts_root))
        browser.native_click("#closeCleanupPreview")
        browser.wait_for("document.querySelector('#cleanupPreviewDialog')?.open === false")
        cleanup_after_confirm = project_tree_snapshot(cleanup_project_root)

        def file_only(snapshot):
            return {
                name: value for name, value in snapshot.items()
                if value.get("kind") == "file"
            }

        before_files = file_only(cleanup_before)
        after_files = file_only(cleanup_after_confirm)
        removed = set(before_files) - set(after_files)
        added = set(after_files) - set(before_files)
        changed = {
            name for name in set(before_files) & set(after_files)
            if before_files[name] != after_files[name]
        }
        expected_removed = f"media/{cleanup_asset_id}.png"
        if removed != {expected_removed} or added or changed != {"media/index.json"}:
            raise AssertionError(
                f"cleanup confirm mutated unexpected paths: "
                f"removed={sorted(removed)} added={sorted(added)} changed={sorted(changed)}"
            )
        if "无法撤销" not in str(confirm_observed.get("note") or ""):
            raise AssertionError(f"cleanup confirm note is unclear: {confirm_observed!r}")
        if "1" not in str(confirm_observed.get("done") or ""):
            raise AssertionError(f"cleanup confirm done message is missing: {confirm_observed!r}")
        cleanup_confirm = {
            "project_id": cleanup_project_id,
            "candidate_asset_id": cleanup_asset_id,
            "removed_original": expected_removed,
            "project_tree_delta": {
                "removed": sorted(removed),
                "added": sorted(added),
                "changed": sorted(changed),
            },
            "note": confirm_observed.get("note"),
            "done": confirm_observed.get("done"),
            "operation": confirm_operation,
            "screenshot": relative(cleanup_confirm_screenshot, artifacts_root),
        }
        seed_index = browser.evaluate(
            "[...document.querySelectorAll('#projectGrid .project-card')].findIndex(card => "
            "card.querySelector('h2')?.textContent === 'Native Browser Evidence')"
        )
        if not isinstance(seed_index, int) or seed_index < 0:
            raise AssertionError("seed project is unavailable from the project list")
        open_operation = browser.native_click(
            f"#projectGrid .project-card:nth-child({seed_index + 1}) .project-open"
        )
        browser.wait_for("location.pathname.endsWith('/static/smart-canvas.html')")
        browser.wait_for("document.querySelector('[data-id=\"seed-prompt\"] .prompt-node-text')")
        environment = browser.evaluate(
            """(async () => {
              await Promise.all([
                document.fonts.load('400 12px "JetBrains Mono"'),
                document.fonts.load('500 12px "Space Grotesk"'),
              ]);
              await document.fonts.ready;
              return {
                width: innerWidth,
                height: innerHeight,
                dpr: devicePixelRatio,
                color_scheme: matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'other',
                theme: document.documentElement.getAttribute('data-theme') || document.body.getAttribute('data-theme') || 'light',
                fonts: {
                  'Inter': document.fonts.check('12px Inter'),
                  'JetBrains Mono': document.fonts.check('12px "JetBrains Mono"'),
                  'Space Grotesk': document.fonts.check('12px "Space Grotesk"'),
                },
              };
            })()""",
            await_promise=True,
        )
        expected_environment = {
            "width": width,
            "height": height,
            "dpr": 1,
            "color_scheme": "light",
            "theme": "light",
        }
        for key, expected in expected_environment.items():
            if environment.get(key) != expected:
                raise AssertionError(f"environment {key}={environment.get(key)!r}, expected {expected!r}")
        if not all(environment.get("fonts", {}).get(font) for font in FONT_FAMILIES):
            raise AssertionError(f"fixed browser fonts unavailable: {environment.get('fonts')}")

        loaded = browser.capture_screenshot(viewport_root / "01-loaded.png")
        screenshots.append(relative(loaded, artifacts_root))

        prompt_node = '[data-id="seed-prompt"]'
        before_drag = browser.evaluate(
            "(() => { const r = document.querySelector('[data-id=\"seed-prompt\"]').getBoundingClientRect(); return {x:r.x,y:r.y}; })()"
        )
        browser.native_click(prompt_node, relative=(0.5, 0.02))
        browser.native_drag(prompt_node, dx=36, dy=24, steps=6, relative=(0.5, 0.02))
        after_drag = browser.evaluate(
            "(() => { const r = document.querySelector('[data-id=\"seed-prompt\"]').getBoundingClientRect(); return {x:r.x,y:r.y}; })()"
        )
        if abs(after_drag["x"] - before_drag["x"]) < 10 or abs(after_drag["y"] - before_drag["y"]) < 10:
            raise AssertionError("native drag did not move the prompt node")

        prompt_text = '[data-id="seed-prompt"] .prompt-node-text'
        browser.native_click(prompt_text)
        browser.native_press("a", modifiers=("Control",))
        browser.native_type("native evidence updated")
        prompt_value = browser.evaluate(
            "document.querySelector('[data-id=\"seed-prompt\"] .prompt-node-text').value"
        )
        if prompt_value != "native evidence updated":
            raise AssertionError(f"native keyboard input did not update prompt: {prompt_value!r}")

        browser.native_click(prompt_node, relative=(0.5, 0.02))
        browser.native_press("Delete")
        browser.wait_for("!document.querySelector('[data-id=\"seed-prompt\"]')")
        browser.native_press("z", modifiers=("Control",))
        browser.wait_for("document.querySelector('[data-id=\"seed-prompt\"]')")
        restored = browser.capture_screenshot(viewport_root / "02-restored.png")
        screenshots.append(relative(restored, artifacts_root))

        if not open_operation.get("trusted") or not all(
            operation.get("trusted") for operation in browser.operations
        ):
            raise AssertionError(f"untrusted browser input observed: {browser.operations}")
        if any(operation.get("unobscured") is False for operation in browser.operations):
            raise AssertionError(f"occluded browser input target observed: {browser.operations}")
        console_errors = browser.console_errors
        unexpected_errors = unexpected_console_errors(console_errors)
        if unexpected_errors:
            raise AssertionError(f"unexpected browser console errors: {unexpected_errors}")

        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in browser.operations),
            encoding="utf-8",
        )
        write_json(console_path, console_errors)
        write_json(requests_path, browser.request_summaries)
        attempt = {
            "viewport": [width, height],
            "observed": environment,
            "status": "passed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "project_roundtrip": project_roundtrip,
            "image_organization": image_organization,
            "cleanup_preview": cleanup_preview,
            "cleanup_confirm": cleanup_confirm,
            "operations": relative(operations_path, artifacts_root),
            "console_errors": relative(console_path, artifacts_root),
            "unexpected_console_errors": len(unexpected_errors),
            "requests": relative(requests_path, artifacts_root),
            "screenshots": screenshots,
        }
        return attempt, browser.browser_version()
    except Exception as error:
        failure_screenshot = None
        console_errors = []
        requests = []
        if browser is not None:
            try:
                failure_screenshot = browser.capture_screenshot(viewport_root / "failure-first.png")
            except Exception:
                failure_screenshot = None
            console_errors = browser.console_errors
            requests = browser.request_summaries
            operations_path.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in browser.operations),
                encoding="utf-8",
            )
        write_json(console_path, console_errors)
        write_json(requests_path, requests)
        failure = {
            "viewport": [width, height],
            "reason": str(error),
            "failure_screenshot": relative(failure_screenshot, artifacts_root) if failure_screenshot else None,
            "operations": relative(operations_path, artifacts_root),
            "console_errors": relative(console_path, artifacts_root),
            "requests": relative(requests_path, artifacts_root),
        }
        raise RuntimeError(json.dumps(failure, ensure_ascii=False)) from error
    finally:
        if browser is not None:
            browser.close()
        if server is not None:
            server.close()
        if store is not None:
            store.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts_root = args.artifacts_dir.resolve()
    if artifacts_root.exists():
        raise RuntimeError(f"browser artifact directory already exists: {artifacts_root}")
    artifacts_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "journey": "core-canvas",
        "status": "running",
        "environment": {
            "os": "Windows",
            "browser": {"name": "Google Chrome", "channel": "stable", "version": ""},
            "dpr": 1,
            "color_scheme": "light",
            "fonts": list(FONT_FAMILIES),
        },
        "attempts": [],
        "first_failure": None,
    }
    manifest_path = artifacts_root / "manifest.json"
    write_json(manifest_path, manifest)
    browser_path = find_browser("chrome")
    with tempfile.TemporaryDirectory(prefix="firefly-core-canvas-native-") as root:
        with ProcessPoolExecutor(max_workers=len(VIEWPORTS)) as executor:
            attempts = [
                (
                    viewport,
                    executor.submit(
                        run_viewport,
                        browser_path,
                        viewport,
                        Path(root) / f"run-{index}",
                        artifacts_root,
                    ),
                )
                for index, viewport in enumerate(VIEWPORTS)
            ]
            for viewport, future in attempts:
                try:
                    attempt, version = future.result()
                    manifest["attempts"].append(attempt)
                    manifest["environment"]["browser"]["version"] = version.get("product", "")
                    write_json(manifest_path, manifest)
                except Exception as error:
                    try:
                        failure = json.loads(str(error))
                    except json.JSONDecodeError:
                        failure = {"viewport": list(viewport), "reason": str(error)}
                    manifest["attempts"].append({"viewport": list(viewport), "status": "failed"})
                    manifest["status"] = "failed"
                    manifest["first_failure"] = failure
                    write_json(manifest_path, manifest)
                    print(json.dumps(manifest, ensure_ascii=False), flush=True)
                    return 1
    manifest["status"] = "passed"
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
