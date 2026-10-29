"""#165 real-browser reproduction: a released group frame must not be pulled back.

The bug: after a group frame (`.image-node.smart-group-node`) is dragged and
released, the local position lives only in `canvasState.nodes` until the
automatic save lands (DEBOUNCED_SAVE_MS = 3000ms). Any remote canvas merge that
runs inside that window (`canvas_updated` broadcast, the 8s meta poll, or a
manual refresh) replaces `canvasState.nodes` with the pre-drag server snapshot,
and the next automatic save persists the stale position.

This runner reproduces exactly that window with native Chrome input:

1. a real group frame from the layering fixture is dragged with native CDP
   input (`Input.dispatchMouseEvent` via `NativeBrowser.native_drag`, never a
   synthetic `dispatchEvent`) and released;
2. before the automatic save can run, the product's own merge entry point
   `window.refreshSmartCanvas()` is called - the same code path a
   `canvas_updated` broadcast reaches (`mergeReloadCanvasNow` ->
   `applyMergedServerCanvas` -> `mergeSmartNodeLists` -> `mergeSmartNode`);
3. the group's world position is asserted to still be the released position,
   and the released position is asserted to be what the automatic save
   persists back to `/api/canvases/<id>`.

Usage: python tests/group_drag_merge_browser.py --artifacts-dir <new-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import layering_contract_browser as layering
from tests import native_browser

VIEWPORT = (1424, 1000)
GROUP = "layer-group"
# Phase A moves the group and lets the automatic save land, so the server
# snapshot has the same node shape the client holds (images + runFinishedAt).
# Phase B is the #165 race: drag again, merge before the automatic save lands.
BASELINE_DX, BASELINE_DY = -120, 80
DRAG_DX, DRAG_DY = -200, 140
# The automatic save is debounced by 3000ms; the merge is triggered well inside
# that window, which is precisely the race #165 describes.
PERSIST_TIMEOUT_S = 25.0

MERGE_SNIPPET = """(() => {
  const result = window.refreshSmartCanvas();
  window.__f165MergeState = 'deferred';
  if (result && typeof result.then === 'function') {
    window.__f165MergeState = 'running';
    result.then(() => { window.__f165MergeState = 'done'; })
          .catch(() => { window.__f165MergeState = 'error'; });
  }
  return window.__f165MergeState;
})()"""


def world_of(browser, node_id: str) -> dict:
    value = browser.evaluate(
        f"""(() => {{
          const node = (window.canvasState?.nodes || []).find(n => n.id === {json.dumps(node_id)});
          return node ? {{x: node.x, y: node.y}} : null;
        }})()"""
    )
    if value is None:
        raise AssertionError(f"node missing in canvasState: {node_id}")
    return {"x": float(value["x"]), "y": float(value["y"])}


def rect_of(browser, selector: str) -> dict:
    value = browser.evaluate(
        f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return null;
          const box = el.getBoundingClientRect();
          return {{left: box.left, top: box.top, right: box.right, bottom: box.bottom}};
        }})()"""
    )
    if value is None:
        raise AssertionError(f"element missing: {selector}")
    return {key: float(value[key]) for key in value}


def server_node(base_url: str, canvas_id: str, node_id: str) -> dict:
    node = server_node_full(base_url, canvas_id, node_id)
    return {"x": float(node.get("x", 0)), "y": float(node.get("y", 0))}


def server_node_full(base_url: str, canvas_id: str, node_id: str) -> dict:
    """Read the persisted node straight from the product API."""
    url = f"{base_url}/api/canvases/{urllib.parse.quote(canvas_id)}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    canvas = payload.get("canvas") or {}
    for node in canvas.get("nodes") or []:
        if node.get("id") == node_id:
            return node
    raise AssertionError(f"node missing in persisted canvas: {node_id}")


class Runner:
    def __init__(self, browser, project_id: str, base_url: str):
        self.browser = browser
        self.project_id = project_id
        self.base_url = base_url
        self.assertions: list[dict] = []
        self.shots: list[dict] = []
        self.observations: list[dict] = []

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        self.assertions.append({"name": name, "pass": bool(passed), "detail": detail})

    def observe(self, name: str, value) -> None:
        self.observations.append({"name": name, "value": value})

    def shot(self, artifacts: Path, filename: str, expect: str) -> None:
        path = artifacts / filename
        self.browser.capture_screenshot(path)
        self.shots.append({"path": str(path), "expect": expect})

    def open_canvas(self) -> None:
        query = urllib.parse.urlencode({"project": self.project_id, "browserTest": "1"})
        url = (
            f"{self.base_url}/static/smart-canvas.html"
            f"?id={urllib.parse.quote(self.project_id)}&{query}"
        )
        self.browser.navigate(url)
        self.browser.wait_for(
            "document.querySelector('.image-node[data-id=\"layer-group\"].smart-group-node') "
            "&& window.canvasState?.nodes?.length > 0",
            timeout=30,
        )
        time.sleep(0.8)

    def wait_server_position(self, node_id: str, expected: dict, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        current = server_node(self.base_url, self.project_id, node_id)
        while time.time() < deadline:
            current = server_node(self.base_url, self.project_id, node_id)
            if current == expected:
                return current
            time.sleep(0.5)
        return current


def run_scenario(runner: Runner, artifacts: Path) -> None:
    browser = runner.browser
    runner.open_canvas()

    before_world = world_of(browser, GROUP)
    before_server = server_node(runner.base_url, runner.project_id, GROUP)
    runner.observe("worldBeforeDrag", before_world)
    runner.observe("serverBeforeDrag", before_server)
    runner.check("00 拖动前：本地与服务器快照一致（起点干净）", before_world == before_server,
                 f"local={before_world} server={before_server}")

    # Phase A: drag once and let the automatic save land, so the persisted group
    # frame carries the same media/run fields the live node has. This is the
    # steady state a real project is in when #165 fires.
    baseline_drag = browser.native_drag(f".image-node[data-id=\"{GROUP}\"]",
                                       dx=BASELINE_DX, dy=BASELINE_DY,
                                       relative=(0.95, 0.2))
    baseline_world = world_of(browser, GROUP)
    runner.observe("baselineDragNativeEvents", baseline_drag)
    runner.observe("worldAfterBaselineRelease", baseline_world)
    baseline_server = runner.wait_server_position(GROUP, baseline_world)
    runner.observe("serverAfterBaselineSave", baseline_server)
    runner.check("0A 基线：自动保存已把第一次拖动落盘（服务器快照=本地）",
                 baseline_server == baseline_world,
                 f"local={baseline_world} server={baseline_server}")
    runner.observe("serverNodeShapeAfterBaselineSave",
                   server_node_full(runner.base_url, runner.project_id, GROUP))

    before_world = baseline_world
    before_server = baseline_server

    # Phase B: native drag of the group frame's own backboard, then release.
    drag = browser.native_drag(f".image-node[data-id=\"{GROUP}\"]",
                             dx=DRAG_DX, dy=DRAG_DY,
                             relative=(0.95, 0.2))
    released_world = world_of(browser, GROUP)
    runner.observe("dragNativeEvents", drag)
    runner.observe("worldAfterRelease", released_world)
    runner.observe("groupRectAfterRelease", rect_of(browser, f".image-node[data-id=\"{GROUP}\"]"))
    runner.shot(artifacts, "01-after-native-drag-release.png",
                "原生拖拽释放后：组框停在新位置（自动保存尚未落盘）")
    moved = abs(released_world["x"] - before_world["x"]) + abs(released_world["y"] - before_world["y"])
    runner.check("01 释放后：组框确实被拖动（世界位移 > 60px）", moved > 60,
                 f"before={before_world} released={released_world} moved={moved:.1f}")

    # The server still holds the pre-drag snapshot: the automatic save (3000ms
    # debounce) has not run yet.
    stale_server = server_node(runner.base_url, runner.project_id, GROUP)
    runner.observe("serverAfterReleaseBeforeMerge", stale_server)
    runner.check("01 释放后：服务器快照仍是拖动前位置（竞态窗口已打开）",
                 stale_server == before_world, f"server={stale_server} before={before_world}")

    # Same code path a `canvas_updated` broadcast reaches, reached through the
    # product's own exported entry point instead of a synthetic event.
    merge_state = browser.evaluate(MERGE_SNIPPET)
    runner.observe("mergeCallState", merge_state)
    browser.wait_for(
        "window.__f165MergeState === 'done' || window.__f165MergeState === 'deferred'",
        timeout=20,
    )
    if browser.evaluate("window.__f165MergeState") == "deferred":
        # mergeReloadCanvasNow deferred itself; let the deferred reload fire.
        time.sleep(2.0)
    merged_world = world_of(browser, GROUP)
    runner.observe("worldAfterMerge", merged_world)
    runner.observe("groupRectAfterMerge", rect_of(browser, f".image-node[data-id=\"{GROUP}\"]"))
    runner.shot(artifacts, "02-after-remote-merge.png",
                "远端合并之后：组框应仍停在释放位置（#165 会在这里弹回）")
    runner.check(
        "02 合并后：组框世界坐标不被远端快照弹回（仍等于释放时位置）",
        merged_world == released_world,
        f"released=({released_world['x']:.1f},{released_world['y']:.1f}) "
        f"merged=({merged_world['x']:.1f},{merged_world['y']:.1f})",
    )

    # The automatic save must persist the released position, not the merged one.
    persisted = runner.wait_server_position(GROUP, released_world, timeout=PERSIST_TIMEOUT_S)
    runner.observe("serverAfterAutosave", persisted)
    runner.check(
        "03 自动保存后：释放位置已落盘（服务器快照等于释放位置）",
        persisted == released_world,
        f"released=({released_world['x']:.1f},{released_world['y']:.1f}) "
        f"persisted=({persisted['x']:.1f},{persisted['y']:.1f})",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()
    artifacts: Path = args.artifacts_dir
    if artifacts.exists():
        raise SystemExit(f"artifacts dir must not exist yet: {artifacts}")
    artifacts.mkdir(parents=True)

    browser_exec = native_browser.find_browser("chrome")
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-group-merge-") as root_text:
        root = Path(root_text)
        store = ProjectStore(root / "projects", snapshot_delay=0)
        app, module = layering.load_app(root / "runtime", store)
        server = native_browser.AsgiBrowserServer(app)
        server.start()
        browser = native_browser.NativeBrowser.launch(browser_exec)
        try:
            browser.set_environment(VIEWPORT)
            project_id = layering.seed_store(store)
            print(f"scenario group-drag-merge project={project_id}", flush=True)
            runner = Runner(browser, project_id, server.base_url)
            try:
                run_scenario(runner, artifacts)
            except Exception as error:  # keep the evidence from the steps that did run
                runner.assertions.append({"name": "场景执行异常", "pass": False,
                                          "detail": repr(error)[:600]})
            results.append({
                "scenario": "group-drag-merge",
                "assertions": runner.assertions,
                "screenshots": runner.shots,
                "observations": runner.observations,
                "pass": all(item["pass"] for item in runner.assertions),
            })
        finally:
            browser.close()
            server.close()
            try:
                module.FIREFLY_PROJECT_STORE.close()
            except Exception:
                pass
            if module.FIREFLY_PROJECT_STORE is not store:
                store.close()

    summary = {
        "surface": "group-drag-merge",
        "issue": "#165",
        "browser": str(browser_exec),
        "viewport": VIEWPORT,
        "scenarios": results,
        "pass": all(item["pass"] for item in results),
    }
    (artifacts / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
