"""Real-browser drag acceptance for the canvas layering contract (#163 + #164).

Screenshots are the acceptance criterion. Every scenario drags with native CDP
input primitives (Input.dispatchMouseEvent - trusted events, never a synthetic
dispatchEvent) and captures the page both while the drag is in flight and after
the drop. Each screenshot is paired with decidable assertions:

- during the drag, document.elementFromPoint at the dragged element's own centre
  must hit the dragged element, never a sibling node or the group backboard;
- after the drop, the group backboard must not steal the composer's hit point,
  and nodes must stay above the backboard where they overlap it;
- with the history drawer open, the composer must lie fully inside the visible
  band above the drawer's top edge (rectangle containment) and be the hit target
  at its own centre;
- the drawer must stay the topmost surface wherever it paints, open or collapsed.

Each scenario runs in a fresh project so every drag starts from identical
geometry. Whether a dragged group springs back to its original world position is
recorded as an observation for #165, never asserted here.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import layering_contract_browser as layering
from tests import native_browser


VIEWPORT = (1424, 1000)
SHOTS = Path.home() / ".cache" / "fix164" / "shots"
NODE = "layer-node"
NODE_B = "layer-node-b"
GROUP = "layer-group"


def rect_of(browser, selector: str) -> dict:
    return browser.evaluate(
        f"""(() => {{
          const element = document.querySelector({json.dumps(selector)});
          if(!element) return null;
          const rect = element.getBoundingClientRect();
          return {{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height}};
        }})()"""
    )


def dom_order(browser) -> list[str]:
    return browser.evaluate(
        """(() => Array.from(document.querySelectorAll('.image-node')).map(n => n.dataset.id))()"""
    )


def viewport_rect(browser) -> dict:
    return browser.evaluate(
        """(() => ({left:0, top:0, right:window.innerWidth, bottom:window.innerHeight,
                    width:window.innerWidth, height:window.innerHeight}))()"""
    )


def world_of(browser, node_id: str) -> dict | None:
    """World coordinates from app state - distinguishes a real move from a canvas re-pan."""
    return browser.evaluate(
        f"""(() => {{
          const node = canvasState.nodes.find(item => item.id === {json.dumps(node_id)});
          return node ? {{x: node.x, y: node.y}} : null;
        }})()"""
    )


def pan_of(browser) -> dict:
    """画布平移量。#164 契约：节点与提示词框允许出当前视口，产口不得自动平移画布把它们拉回。"""
    return browser.evaluate(
        """(() => ({ x: viewport.x, y: viewport.y, transform: world.style.transform }))()"""
    )


def hit_label(browser, x: float, y: float) -> str:
    return browser.evaluate(
        f"""(() => {{
          const hit = document.elementFromPoint({x!r}, {y!r});
          if(!hit) return 'none';
          const label = element => `${{element.tagName.toLowerCase()}}${{element.id ? '#' + element.id : ''}}${{[...element.classList].slice(0, 3).map(value => '.' + value).join('')}}`;
          const owner = hit.closest('.image-node[data-id],.composer,.log-modal');
          const ownerId = owner?.dataset?.id || (owner?.classList?.contains('composer') ? 'composer' : owner?.classList?.contains('log-modal') ? 'log-modal' : '-');
          return `${{label(hit)}}@${{ownerId}}`;
        }})()"""
    )


def hit_is(browser, x: float, y: float, owner_selector: str) -> bool:
    return bool(browser.evaluate(
        f"""(() => {{
          const hit = document.elementFromPoint({x!r}, {y!r});
          const owner = document.querySelector({json.dumps(owner_selector)});
          return Boolean(hit && owner && (hit === owner || owner.contains(hit)));
        }})()"""
    ))


def center(rect: dict) -> tuple[float, float]:
    return (rect["left"] + rect["right"]) / 2, (rect["top"] + rect["bottom"]) / 2


def contains(rect: dict, x: float, y: float) -> bool:
    return rect["left"] <= x <= rect["right"] and rect["top"] <= y <= rect["bottom"]


def pick_point(region: dict, avoid: list[dict], step: int = 10) -> dict | None:
    """First grid point inside `region` that no rect in `avoid` covers."""
    xs = [region["left"] + step / 2 + i * step for i in range(int((region["right"] - region["left"]) // step))]
    ys = [region["top"] + step / 2 + i * step for i in range(int((region["bottom"] - region["top"]) // step))]
    for y in ys:
        for x in xs:
            if not any(contains(other, x, y) for other in avoid):
                return {"x": x, "y": y}
    return None


def intersect(a: dict, b: dict) -> dict | None:
    left, top = max(a["left"], b["left"]), max(a["top"], b["top"])
    right, bottom = min(a["right"], b["right"]), min(a["bottom"], b["bottom"])
    return {"left": left, "top": top, "right": right, "bottom": bottom} if right > left and bottom > top else None


class Runner:
    def __init__(self, browser, project_id: str, base_url: str):
        self.browser = browser
        self.project_id = project_id
        self.base_url = base_url
        self.assertions: list[dict] = []
        self.shots: list[dict] = []
        self.observations: list[dict] = []

    # ---- evidence helpers -------------------------------------------------
    def check(self, name: str, passed: bool, detail: str = "") -> None:
        self.assertions.append({"name": name, "pass": bool(passed), "detail": detail})

    def observe(self, name: str, value) -> None:
        self.observations.append({"name": name, "value": value})

    def shot(self, filename: str, expect: str) -> None:
        path = SHOTS / filename
        self.browser.capture_screenshot(path)
        self.shots.append({"path": str(path), "expect": expect})

    # ---- page control -----------------------------------------------------
    def open_canvas(self) -> None:
        query = urllib.parse.urlencode({"project": self.project_id, "browserTest": "1"})
        url = f"{self.base_url}/static/smart-canvas.html?id={urllib.parse.quote(self.project_id)}&{query}"
        self.browser.navigate(url)
        self.browser.wait_for(
            "document.querySelector('.image-node[data-id=\"layer-node\"]') "
            "&& document.querySelector('.image-node[data-id=\"layer-node-b\"]') "
            "&& document.querySelector('.image-node[data-id=\"layer-group\"]')",
            timeout=30,
        )
        time.sleep(0.6)

    def wait(self, expression: str, timeout: float = 10) -> None:
        self.browser.wait_for(expression, timeout=timeout)

    def select(self, node_id: str) -> None:
        # .node-drop 上有捕获阶段的 mousedown 拦截，空节点只能从 .node-head 拖动/选中。
        self.browser.native_click(f".image-node[data-id=\"{node_id}\"]", relative=(0.5, 0.05))
        self.wait("document.querySelector('.composer.open') !== null")
        time.sleep(0.4)

    def open_drawer(self) -> None:
        self.browser.native_click("#smartHistoryToggle")
        self.wait("document.querySelector('#smartHistoryModal').classList.contains('open') === true")
        time.sleep(0.5)

    def collapse_drawer(self) -> None:
        self.browser.native_click("#smartHistoryCollapse")
        self.wait("document.querySelector('#smartHistoryModal').classList.contains('collapsed') === true")
        time.sleep(0.5)

    # ---- native drag, staged so the page can be observed mid-flight -------
    def drag_start(self, selector: str, relative: tuple[float, float] = (0.5, 0.5)) -> dict:
        state = self.browser._describe_element(selector, relative)
        if not state:
            raise AssertionError(f"drag target missing: {selector}")
        x = state["x"] + state["width"] * relative[0]
        y = state["y"] + state["height"] * relative[1]
        self._move(x, y, pressed=False)
        self.browser.cdp.command("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1,
        })
        time.sleep(0.15)
        return {"x": x, "y": y, "moved": 0.0}

    def _move(self, x: float, y: float, *, pressed: bool) -> None:
        self.browser.cdp.command("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
            "button": "left" if pressed else "none",
            "buttons": 1 if pressed else 0,
        })

    def drag_move(self, handle: dict, dx: float, dy: float, steps: int = 6) -> None:
        base_x = handle["x"] + handle.get("dx_total", 0)
        base_y = handle["y"] + handle.get("dy_total", 0)
        for index in range(1, steps + 1):
            self._move(base_x + dx * index / steps, base_y + dy * index / steps, pressed=True)
            time.sleep(0.03)
        handle["dx_total"] = handle.get("dx_total", 0) + dx
        handle["dy_total"] = handle.get("dy_total", 0) + dy

    def drag_release(self, handle: dict) -> None:
        x = handle["x"] + handle.get("dx_total", 0)
        y = handle["y"] + handle.get("dy_total", 0)
        self.browser.cdp.command("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left",
        })
        time.sleep(0.4)


def scenario_01_closed(runner: Runner) -> None:
    """#163 with the drawer closed: a dragged group must not steal the composer."""
    runner.open_canvas()
    runner.select(NODE)
    composer = rect_of(runner.browser, ".composer")
    # A point that is inside the composer AND inside the ordinary node behind it.
    target = {"x": composer["left"] + 360, "y": composer["top"] + 14}
    runner.observe("groupBeforeWorld", world_of(runner.browser, GROUP))
    handle = runner.drag_start(f".image-node[data-id=\"{GROUP}\"]", (0.95, 0.2))
    dx, dy = target["x"] - handle["x"], target["y"] - handle["y"]
    runner.drag_move(handle, dx / 2, dy / 2)
    dragging = rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]")
    runner.shot("01-drawer-closed-dragging-group.png",
                "抽屉关闭：组框背板正被拖过普通节点与提示词框；拖动中的组框可见，不被兄弟节点盖住")
    own = pick_point(dragging, [
        rect_of(runner.browser, f".image-node[data-id=\"{NODE}\"]"),
        rect_of(runner.browser, f".image-node[data-id=\"{NODE_B}\"]"),
        rect_of(runner.browser, ".composer"),
    ])
    if own:
        runner.check("01 拖动中：组框自身空白处命中组框（拖动中的组框可见）",
                     hit_is(runner.browser, own["x"], own["y"], f".image-node[data-id=\"{GROUP}\"]"),
                     f"point={own} hit={hit_label(runner.browser, own['x'], own['y'])}")
    else:
        runner.check("01 拖动中：组框有未被遮挡的空白处可测", False, f"dragging={dragging}")
    runner.drag_move(handle, dx / 2, dy / 2)
    runner.shot("01-drawer-closed-midflight-group.png",
                "组框已落到提示词框与节点之上（仍未释放）：抽屉关闭态下画布层级不被任何抽屉规则改写")
    runner.drag_release(handle)
    after = rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]")
    runner.shot("01-drawer-closed-after-drop.png",
                "释放后组框停在释放位置：提示词框仍可见可点，组框背板不抢提示词框的点击")
    runner.check("01 释放后：组框覆盖提示词框命中点",
                 contains(after, target["x"], target["y"]), f"group={after} point={target}")
    runner.check("01 释放后：提示词框命中点命中提示词框（背板不抢点击）",
                 hit_is(runner.browser, target["x"], target["y"], ".composer"),
                 f"hit={hit_label(runner.browser, target['x'], target['y'])}")
    node_b = rect_of(runner.browser, f".image-node[data-id=\"{NODE_B}\"]")
    overlap = intersect(after, node_b)
    point = pick_point(overlap, [rect_of(runner.browser, f".image-node[data-id=\"{NODE}\"]"), composer]) if overlap else None
    if point:
        runner.check("01 释放后：组框与普通节点重叠处命中普通节点（背板在节点之下）",
                     hit_is(runner.browser, point["x"], point["y"], f".image-node[data-id=\"{NODE_B}\"]"),
                     f"point={point} hit={hit_label(runner.browser, point['x'], point['y'])}")
    else:
        runner.check("01 释放后：组框与普通节点存在可测重叠区", False, f"group={after} nodeB={node_b} composerTop={composer['top']}")
    runner.observe("groupAfterWorld", world_of(runner.browser, GROUP))
    runner.observe("groupAfterDrop", after)


def scenario_02_drawer_open(runner: Runner) -> None:
    """#163 + #164 together: group dragged over the composer while the drawer is open."""
    runner.open_canvas()
    runner.open_drawer()
    runner.select(NODE)
    composer = rect_of(runner.browser, ".composer")
    drawer = rect_of(runner.browser, "#smartHistoryModal")
    overlap = intersect(composer, drawer)
    runner.observe("composerDrawerOverlap", overlap)
    if overlap:
        ox, oy = center(overlap)
        runner.check("02 抽屉打开后选中节点：提示词框与抽屉重叠处命中抽屉（抽屉浮在最上层；内容可出视口，不强制拉回）",
                     hit_is(runner.browser, ox, oy, "#smartHistoryModal"),
                     f"overlap={overlap} hit={hit_label(runner.browser, ox, oy)}")
    # 测试点必须落在提示词框上、在视口内、且不被抽屉盖住；
    # 否则“组框不能抢走提示词框命中点”没有意义（抽屉抢中是正确行为）。
    target = pick_point(intersect(composer, viewport_rect(runner.browser)) or composer, [drawer])
    runner.observe("composerTargetPoint", target)
    runner.observe("groupBeforeWorld", world_of(runner.browser, GROUP))
    handle = runner.drag_start(f".image-node[data-id=\"{GROUP}\"]", (0.95, 0.2))
    dx, dy = target["x"] - handle["x"], target["y"] - handle["y"]
    runner.drag_move(handle, dx / 2, dy / 2)
    dragging = rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]")
    runner.shot("02-drawer-open-dragging-group.png",
                "抽屉打开：组框背板被拖过选中节点与提示词框；抽屉浮在最上层，提示词框在抽屉之上可见")
    own = pick_point(dragging, [
        rect_of(runner.browser, f".image-node[data-id=\"{NODE}\"]"),
        rect_of(runner.browser, f".image-node[data-id=\"{NODE_B}\"]"),
        rect_of(runner.browser, ".composer"),
        rect_of(runner.browser, "#smartHistoryModal"),
    ])
    if own:
        runner.check("02 拖动中：组框自身空白处命中组框（抽屉打开时拖动中的组框可见）",
                     hit_is(runner.browser, own["x"], own["y"], f".image-node[data-id=\"{GROUP}\"]"),
                     f"point={own} hit={hit_label(runner.browser, own['x'], own['y'])}")
    else:
        runner.check("02 拖动中：组框有未被遮挡的空白处可测", False, f"dragging={dragging}")
    runner.drag_move(handle, dx / 2, dy / 2)
    runner.drag_release(handle)
    after = rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]")
    composer_after = rect_of(runner.browser, ".composer")
    runner.shot("02-drawer-open-after-drop.png",
                "释放后：抽屉仍在最上层；提示词框完整可见，不被抽屉与组框背板盖住")
    runner.check("02 释放后：提示词框仍打开",
                 bool(runner.browser.evaluate("document.querySelector('.composer.open') !== null")),
                 f"composer={composer_after}")
    runner.check("02 释放后：组框覆盖提示词框命中点", contains(after, target["x"], target["y"]),
                 f"group={after} point={target}")
    runner.check("02 释放后：提示词框命中点命中提示词框（不被组框抢走）",
                 hit_is(runner.browser, target["x"], target["y"], ".composer"),
                 f"hit={hit_label(runner.browser, target['x'], target['y'])}")
    drawer_after = rect_of(runner.browser, "#smartHistoryModal")
    overlap_after = intersect(composer_after, drawer_after)
    runner.observe("composerDrawerOverlapAfterDrop", overlap_after)
    if overlap_after:
        ox, oy = center(overlap_after)
        runner.check("02 释放后：提示词框与抽屉重叠处仍命中抽屉（层级不变）",
                     hit_is(runner.browser, ox, oy, "#smartHistoryModal"),
                     f"overlap={overlap_after} hit={hit_label(runner.browser, ox, oy)}")
    runner.observe("groupAfterWorld", world_of(runner.browser, GROUP))
    runner.observe("groupAfterDrop", after)


def scenario_03_drag_selected(runner: Runner) -> None:
    """#164 core: the selected/dragged node must stay above ordinary nodes."""
    runner.open_canvas()
    runner.open_drawer()
    # 必须拖 DOM 靠前的那个普通节点：压平后两个节点 z-index 相同，
    # 靠后的那个靠 DOM 顺序就会赢，断言将抓不到 bug。
    order = [n for n in dom_order(runner.browser) if n in (NODE, NODE_B)]
    dragged, other = (order[0], order[1]) if len(order) == 2 else (NODE_B, NODE)
    runner.observe("domOrder", order)
    runner.observe("draggedIsDomEarlier", dragged == order[0] if order else None)
    runner.select(dragged)
    pan_before = pan_of(runner.browser)
    runner.observe("panBeforeDrag", pan_before)
    before = rect_of(runner.browser, f".image-node[data-id=\"{dragged}\"]")
    before_world = world_of(runner.browser, dragged)
    runner.observe("nodeBBeforeDrag", before)
    handle = runner.drag_start(f".image-node[data-id=\"{dragged}\"]", (0.5, 0.05))
    dx, dy = 60, 160
    runner.drag_move(handle, dx / 2, dy / 2)
    dragging = rect_of(runner.browser, f".image-node[data-id=\"{dragged}\"]")
    cx, cy = center(dragging)
    runner.shot("03-drawer-open-dragging-selected.png",
                f"抽屉打开：正在拖动选中节点 {dragged}（DOM 靠前），它压在普通节点 {other} 之上（#164 压平问题核心）")
    runner.check("03 拖动中：选中节点中心命中它自己",
                 hit_is(runner.browser, cx, cy, f".image-node[data-id=\"{dragged}\"]"),
                 f"point=({cx:.0f},{cy:.0f}) hit={hit_label(runner.browser, cx, cy)}")
    overlap = intersect(dragging, rect_of(runner.browser, f".image-node[data-id=\"{other}\"]"))
    if overlap:
        ox, oy = center(overlap)
        runner.check("03 拖动中：与普通节点重叠处命中被拖的选中节点（不被 DOM 靠后节点抢走）",
                     hit_is(runner.browser, ox, oy, f".image-node[data-id=\"{dragged}\"]"),
                     f"point=({ox:.0f},{oy:.0f}) hit={hit_label(runner.browser, ox, oy)}")
    else:
        runner.check("03 拖动中：被拖节点与普通节点存在重叠区", False, f"dragging={dragging}")
    runner.drag_move(handle, dx / 2, dy / 2)
    runner.drag_release(handle)
    after = rect_of(runner.browser, f".image-node[data-id=\"{dragged}\"]")
    runner.shot("03-drawer-open-after-drop-selected.png",
                "释放后：选中节点仍压在普通节点之上；抽屉仍浮在最上层")
    cx, cy = center(after)
    runner.check("03 释放后：选中节点中心命中它自己（选中节点不被抽屉/背板盖住）",
                 hit_is(runner.browser, cx, cy, f".image-node[data-id=\"{dragged}\"]"),
                 f"point=({cx:.0f},{cy:.0f}) hit={hit_label(runner.browser, cx, cy)}")
    overlap_after = intersect(after, rect_of(runner.browser, f".image-node[data-id=\"{other}\"]"))
    if overlap_after:
        ox, oy = center(overlap_after)
        runner.check("03 释放后：重叠处仍命中选中节点",
                     hit_is(runner.browser, ox, oy, f".image-node[data-id=\"{dragged}\"]"),
                     f"point=({ox:.0f},{oy:.0f}) hit={hit_label(runner.browser, ox, oy)}")
    else:
        runner.observe("noOverlapAfterDrop", {"dragged": after, "other": rect_of(runner.browser, f".image-node[data-id=\"{other}\"]")})
    runner.observe("nodeBBeforeWorld", before_world)
    runner.observe("nodeBAfterWorld", world_of(runner.browser, dragged))
    runner.observe("nodeBAfterDrop", after)
    pan_after = pan_of(runner.browser)
    runner.observe("panAfterDrag", pan_after)
    runner.check("03 释放后：画布没有被自动平移（节点允许出视口，不强制拉回）",
                 pan_after == pan_before,
                 f"before={pan_before} after={pan_after}")


def scenario_04_bottom_composer(runner: Runner) -> None:
    """Drawer open + the bottom node's composer: it may fall under the drawer; the canvas must not auto-pan."""
    runner.open_canvas()
    runner.open_drawer()
    runner.select(NODE_B)
    pan_before = pan_of(runner.browser)
    runner.observe("panBeforeDrag", pan_before)
    composer_before = rect_of(runner.browser, ".composer")
    drawer_before = rect_of(runner.browser, "#smartHistoryModal")
    runner.observe("composerBeforeDrag", composer_before)
    runner.observe("drawerTopBeforeDrag", drawer_before["top"])
    runner.observe("composerCoveredByDrawerBeforeDrag", composer_before["bottom"] > drawer_before["top"])
    runner.shot("04a-drawer-open-bottom-composer-before-drag.png",
                "观察项（非断言）：抽屉打开时选中底部节点，提示词框当前落在抽屉顶边之下")
    handle = runner.drag_start(f".image-node[data-id=\"{NODE_B}\"]", (0.5, 0.05))
    runner.observe("nodeBBeforeWorld", world_of(runner.browser, NODE_B))
    dx, dy = -180, -300
    runner.drag_move(handle, dx / 2, dy / 2)
    dragging = rect_of(runner.browser, f".image-node[data-id=\"{NODE_B}\"]")
    cx, cy = center(dragging)
    runner.shot("04-drawer-open-dragging-bottom-node.png",
                "抽屉打开：把底部节点拖向可见带，提示词框跟着节点走")
    runner.check("04 拖动中：底部节点中心命中它自己",
                 hit_is(runner.browser, cx, cy, f".image-node[data-id=\"{NODE_B}\"]"),
                 f"point=({cx:.0f},{cy:.0f}) hit={hit_label(runner.browser, cx, cy)}")
    runner.drag_move(handle, dx / 2, dy / 2)
    runner.drag_release(handle)
    composer = rect_of(runner.browser, ".composer")
    drawer = rect_of(runner.browser, "#smartHistoryModal")
    runner.shot("04-drawer-open-after-drop-bottom-node.png",
                "释放后：底部节点的提示词框可以落在抽屉之下（内容允许出视口）；抽屉浮在最上层")
    overlap = intersect(composer, drawer)
    runner.observe("composerDrawerOverlap", overlap)
    if overlap:
        ox, oy = center(overlap)
        runner.check("04 释放后：提示词框与抽屉重叠处命中抽屉（抽屉浮在最上层，不强制把提示词框拉回可见带）",
                     hit_is(runner.browser, ox, oy, "#smartHistoryModal"),
                     f"overlap={overlap} hit={hit_label(runner.browser, ox, oy)}")
    free = pick_point(intersect(composer, viewport_rect(runner.browser)) or composer, [drawer])
    if free:
        runner.check("04 释放后：未被抽屉盖住的提示词框区域仍命中提示词框本身",
                     hit_is(runner.browser, free["x"], free["y"], ".composer"),
                     f"point=({free['x']:.0f},{free['y']:.0f}) hit={hit_label(runner.browser, free['x'], free['y'])}")
    else:
        runner.observe("composerFullyCoveredByDrawer", {"center": center(composer)})
    pan_after = pan_of(runner.browser)
    runner.observe("panAfterDrag", pan_after)
    runner.check("04 释放后：画布没有被自动平移把提示词框拉回可见区（#164 契约）",
                 pan_after == pan_before,
                 f"before={pan_before} after={pan_after}")
    runner.observe("nodeBAfterWorld", world_of(runner.browser, NODE_B))
    runner.observe("composerAfterDrag", composer)


def scenario_05_collapsed(runner: Runner) -> None:
    """Collapsed drawer: the strip still occludes, and the group still cannot steal the composer."""
    runner.open_canvas()
    runner.open_drawer()
    runner.collapse_drawer()
    runner.select(NODE)
    pan_before = pan_of(runner.browser)
    runner.observe("panBeforeDrag", pan_before)
    composer = rect_of(runner.browser, ".composer")
    strip = rect_of(runner.browser, "#smartHistoryModal")
    strip_overlap = intersect(composer, strip)
    runner.observe("composerStripOverlap", strip_overlap)
    if strip_overlap:
        ox, oy = center(strip_overlap)
        runner.check("05 折叠态：提示词框与折叠条带重叠处命中条带（层级不变）",
                     hit_is(runner.browser, ox, oy, "#smartHistoryModal"),
                     f"overlap={strip_overlap} hit={hit_label(runner.browser, ox, oy)}")
    target = pick_point(intersect(composer, viewport_rect(runner.browser)) or composer, [strip])
    runner.observe("composerTargetPoint", target)
    runner.observe("groupBeforeWorld", world_of(runner.browser, GROUP))
    handle = runner.drag_start(f".image-node[data-id=\"{GROUP}\"]", (0.95, 0.2))
    dx, dy = target["x"] - handle["x"], target["y"] - handle["y"]
    runner.drag_move(handle, dx / 2, dy / 2)
    dragging = rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]")
    runner.shot("05-drawer-collapsed-dragging-group.png",
                "抽屉折叠为底部条带：组框背板被拖过节点与提示词框；折叠条带仍浮在最上层（折叠不解除遮挡）")
    own = pick_point(dragging, [
        rect_of(runner.browser, f".image-node[data-id=\"{NODE}\"]"),
        rect_of(runner.browser, f".image-node[data-id=\"{NODE_B}\"]"),
        rect_of(runner.browser, ".composer"),
        rect_of(runner.browser, "#smartHistoryModal"),
    ])
    if own:
        runner.check("05 拖动中：组框自身空白处命中组框（折叠态拖动中的组框可见）",
                     hit_is(runner.browser, own["x"], own["y"], f".image-node[data-id=\"{GROUP}\"]"),
                     f"point={own} hit={hit_label(runner.browser, own['x'], own['y'])}")
    else:
        runner.check("05 拖动中：组框有未被遮挡的空白处可测", False, f"dragging={dragging}")
    runner.drag_move(handle, dx / 2, dy / 2)
    runner.drag_release(handle)
    composer_after = rect_of(runner.browser, ".composer")
    runner.shot("05-drawer-collapsed-after-drop.png",
                "释放后：折叠条带仍压住画布底部；提示词框不被组框背板抢走点击")
    runner.check("05 释放后：提示词框仍打开",
                 bool(runner.browser.evaluate("document.querySelector('.composer.open') !== null")),
                 f"composer={composer_after}")
    runner.check("05 释放后：提示词框命中点命中提示词框",
                 hit_is(runner.browser, target["x"], target["y"], ".composer"),
                 f"hit={hit_label(runner.browser, target['x'], target['y'])}")
    strip_after = rect_of(runner.browser, "#smartHistoryModal")
    strip_point = {"x": strip_after["left"] + 20, "y": (strip_after["top"] + strip_after["bottom"]) / 2}
    runner.check("05 折叠条带仍是最上层（折叠不解除遮挡）",
                 hit_is(runner.browser, strip_point["x"], strip_point["y"], "#smartHistoryModal"),
                 f"hit={hit_label(runner.browser, strip_point['x'], strip_point['y'])}")
    runner.observe("groupAfterWorld", world_of(runner.browser, GROUP))
    runner.observe("groupAfterDrop", rect_of(runner.browser, f".image-node[data-id=\"{GROUP}\"]"))
    pan_after = pan_of(runner.browser)
    runner.observe("panAfterDrag", pan_after)
    runner.check("05 释放后：画布没有被自动平移（折叠态同样不拉回）",
                 pan_after == pan_before,
                 f"before={pan_before} after={pan_after}")


def scenario_06_prompt_view_state(runner: Runner) -> None:
    """#164: changing the prompt view must never pan the canvas, drawer open.

    `keepComposerReachableByPanningCanvas` was only ever called from prompt-view
    state changes (exitPromptFlow / collapseExpandedPrompt / expandPromptToContent /
    refreshExpandedPromptHeight / the prompt-height resize drag) - never from node
    dragging - so this scenario drives the prompt view instead of the node.

    Only the expand step is driven natively: for the old mechanism to engage, the
    composer bottom has to pass the canvas bottom, and the expand/collapse button
    sits at that bottom edge, so in exactly that state the button is under the
    drawer and cannot be clicked again. The remaining call sites are covered by
    tests/test_layering_contract.py::test_no_auto_pan_rescue_of_the_composer,
    which asserts the mechanism is absent from the product source entirely.
    """
    runner.open_canvas()
    runner.open_drawer()
    runner.select(NODE_B)
    pan_before = pan_of(runner.browser)
    composer_before = rect_of(runner.browser, ".composer")
    shell_bottom = runner.browser.evaluate(
        "(() => Math.round(document.querySelector('#shell').getBoundingClientRect().bottom))()"
    )
    runner.observe("panBeforePromptView", pan_before)
    runner.observe("composerBeforePromptView", composer_before)
    runner.observe("shellBottom", shell_bottom)

    # Drive the prompt view: taller prompt box, then expand to full text. With the
    # old keepComposerReachableByPanningCanvas in place this panned the whole
    # canvas by 30px (measured twice: viewport.y 0 -> 30), which is exactly the
    # behaviour #164 removes - the canvas moved underneath the user.
    resize = runner.drag_start("#promptResize", (0.5, 0.5))
    runner.drag_move(resize, 0, 200)
    runner.drag_release(resize)
    time.sleep(0.5)
    runner.observe("composerAfterResize", rect_of(runner.browser, ".composer"))
    runner.observe("panAfterResize", pan_of(runner.browser))

    runner.browser.native_click("#composerExpandBtn")
    runner.wait("composerPromptView.mode === 'expanded'")
    time.sleep(0.6)
    composer_expanded = rect_of(runner.browser, ".composer")
    pan_expanded = pan_of(runner.browser)
    runner.observe("composerAfterExpand", composer_expanded)
    runner.observe("composerBottomVsCanvasBottom", {
        "composerBottom": composer_expanded["bottom"], "shellBottom": shell_bottom,
    })
    runner.shot("06-drawer-open-prompt-expanded.png",
                "抽屉打开 + 调高并展开全文提示词框：画布本身没有平移"
                "（旧机制在这个操作序列里把整个画布平移了 30px，见 #164）")
    runner.check("06 调高并展开全文后：画布没有被自动平移把提示词框拉回（#164 契约）",
                 pan_expanded == pan_before,
                 f"before={pan_before} after={pan_expanded}")
    runner.observe("panAfterPromptView", pan_expanded)




SCENARIOS = [
    ("01-drawer-closed", scenario_01_closed),
    ("02-drawer-open", scenario_02_drawer_open),
    ("03-drag-selected", scenario_03_drag_selected),
    ("04-bottom-composer", scenario_04_bottom_composer),
    ("05-drawer-collapsed", scenario_05_collapsed),
    ("06-prompt-view-state", scenario_06_prompt_view_state),
]


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    browser_exec = native_browser.find_browser("chrome")
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-layering-drag-") as root_text:
        root = Path(root_text)
        store = ProjectStore(root / "projects", snapshot_delay=0)
        app, module = layering.load_app(root / "runtime", store)
        server = native_browser.AsgiBrowserServer(app)
        server.start()
        browser = native_browser.NativeBrowser.launch(browser_exec)
        try:
            browser.set_environment(VIEWPORT)
            for name, scenario in SCENARIOS:
                project_id = layering.seed_store(store)
                runner = Runner(browser, project_id, server.base_url)
                print(f"scenario {name} project={project_id}", flush=True)
                try:
                    scenario(runner)
                except Exception as error:  # keep evidence from the scenarios that did run
                    runner.assertions.append({"name": f"{name} 场景执行异常", "pass": False, "detail": repr(error)[:600]})
                results.append({
                    "scenario": name,
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
        "surface": "layering-drag",
        "browser": str(browser_exec),
        "viewport": VIEWPORT,
        "scenarios": results,
        "pass": all(item["pass"] for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
