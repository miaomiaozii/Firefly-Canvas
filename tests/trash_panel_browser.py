"""Native-browser evidence runner for GitHub issues #210 and #211 (回收区面板).

#210 — the trash list must be sorted newest deletion first.
#211 — the trash panel must use the site's theme variables (dark-theme ready).

Journey (real headless Chrome via CDP against an in-process ASGI server):
  1. Seed a project with three image nodes, each holding one imported image.
  2. Delete the nodes one by one through the node × button (native clicks).
     The app schedules each orphaned image into the project trash (回收区)
     with a distinct ``deleted_at``.
  3. Open the trash panel through ``#smartTrashOpenBtn`` and assert that both
     the API response and the rendered DOM list are in ``deleted_at``
     descending order (screenshot evidence included).
  4. Apply the app's own dark theme (``window.StudioTheme.set('dark')``),
     assert the panel's computed styles track the current theme variables,
     and screenshot.

Evidence contract: writes ``manifest.json``, ``operations.jsonl``,
``console-errors.json``, ``requests.json`` and ``screenshots/*.png`` under
``--artifacts-dir``. Use a FRESH ``--artifacts-dir`` for every run.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser  # noqa: E402

CLIENT_SCRIPT = ROOT / "tests" / "trash_panel_browser_client.js"
VIEWPORT = (1424, 900)

# (node_id, displayed asset name, png colour) — deleted in this order, so the
# expected trash order (newest first) is the REVERSE of this list.
NODES = (
    ("trash-node-a", "第一张图", (200, 30, 30)),
    ("trash-node-b", "第二张图", (30, 160, 200)),
    ("trash-node-c", "第三张图", (30, 30, 200)),
)
EXPECTED_TRASH_NAMES = [name for _, name, _ in reversed(NODES)]


def solid_png(color: tuple[int, int, int], size: tuple[int, int]) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def seed(store: ProjectStore) -> tuple[str, dict[str, str]]:
    project = store.create_project("回收区面板验收")
    project_id = project["id"]
    nodes: list[dict] = []
    asset_by_node: dict[str, str] = {}
    for index, (node_id, name, color) in enumerate(NODES):
        media = store.import_media(project_id, solid_png(color, (160, 120)), f"{name}.png", "image/png")
        asset_by_node[node_id] = media["asset_id"]
        nodes.append(
            {
                "op": "upsert_node",
                "node": {
                    "id": node_id,
                    "type": "smart-image",
                    "name": name,
                    "x": 60 + index * 360,
                    "y": 160,
                    "w": 320,
                    "h": 240,
                    "images": [{"asset_id": media["asset_id"], "kind": "image", "name": f"{name}.png"}],
                },
            }
        )
    store.apply_mutations(project_id, nodes, base_revision=0)
    store.flush(project_id)
    return project_id, asset_by_node


def canvas_nodes(store: ProjectStore, project_id: str) -> list:
    return store.open_project(project_id)["canvas"].get("nodes") or []


def wait_condition(predicate, timeout: float, interval: float = 0.4) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError("condition was not reached in time")


def wait_node_clickable(browser: NativeBrowser, selector: str, timeout: float = 20) -> None:
    expression = (
        "(function(){var el=document.querySelector(" + json.dumps(selector) + ");"
        "if(!el)return{ok:false,reason:'missing'};"
        "var r=el.getBoundingClientRect();"
        "if(r.width<=0||r.height<=0)return{ok:false,reason:'invisible'};"
        "var hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);"
        "return{ok:!hit||hit===el||el.contains(hit)};})()"
    )
    wait_condition(lambda: (browser.evaluate(expression) or {}).get("ok"), timeout)


def wait_node_images_painted(browser: NativeBrowser, timeout: float = 30) -> None:
    expression = (
        "Array.from(document.querySelectorAll('.image-node[data-id^=\"trash-node-\"]'))"
        ".filter(function(n){var i=n.querySelector('img');return i&&i.naturalWidth>0;}).length"
    )
    wait_condition(lambda: int(browser.evaluate(expression) or 0) == len(NODES), timeout)


def delete_node_ui(browser: NativeBrowser, node_id: str) -> None:
    """Delete one image node the way a user does: select it, click its × ."""
    selector = f'.image-node[data-id="{node_id}"]'
    wait_node_clickable(browser, selector)
    browser.native_click(selector)
    delete_selector = f'{selector} .floating-node-actions .node-delete'
    expression = (
        "(() => { const b = document.querySelector("
        + json.dumps(delete_selector)
        + "); return Boolean(b && getComputedStyle(b).pointerEvents === 'auto'); })()"
    )
    browser.wait_for(expression, timeout=20)
    browser.native_click(delete_selector)


def trash_items(browser: NativeBrowser, count: int, timeout: float = 30) -> list:
    """Wait for the trash panel list to render `count` items, return DOM rows."""
    expression = "window.__trashPanelTest.trashItemsRendered(" + str(count) + ")"
    wait_condition(lambda: (browser.evaluate(expression) or {}).get("rendered"), timeout)
    return browser.evaluate("window.__trashPanelTest.readTrashDom()") or []


def fetch_trash_api(server, project_id: str) -> list:
    url = f"{server.base_url}/api/project-library/{urllib.parse.quote(project_id)}/trash/media"
    # The production app guards /api/project-library/* with an Origin/Referer
    # check (protect_project_library_origin in main.py); send the page origin
    # the same way the in-app browser fetch does.
    request = urllib.request.Request(url, headers={"Origin": server.base_url})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8")).get("items") or []


def write_json(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(artifacts: pathlib.Path, entry: str) -> dict:
    steps: dict[str, bool] = {}
    assertions: list[dict] = []
    screenshots: list[str] = []
    failure: dict | None = None
    operations: list = []
    console_errors: list = []
    requests: list = []
    shots_dir = artifacts / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="firefly-trash-panel-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id, asset_by_node = seed(store)
            app = harness.load_production_app(root / "runtime-root", store)
            with AsgiBrowserServer(app) as server:
                browser = NativeBrowser.launch(find_browser())
                try:
                    browser.set_environment(VIEWPORT)
                    url = (
                        f"{server.base_url}/static/smart-canvas.html"
                        f"?id={urllib.parse.quote(project_id)}"
                        f"&project={urllib.parse.quote(project_id)}&project_mode=1"
                    )
                    browser.navigate(url)
                    browser.wait_for(
                        'document.querySelectorAll(".image-node[data-id^=\\"trash-node-\\"]").length === '
                        + str(len(NODES)),
                        timeout=30,
                    )
                    wait_node_images_painted(browser)
                    browser.evaluate(CLIENT_SCRIPT.read_text(encoding="utf-8"))
                    time.sleep(0.5)  # let the click-suppression window lapse

                    if entry in ("order", "both"):
                        for index, (node_id, _name, _color) in enumerate(NODES, start=1):
                            delete_node_ui(browser, node_id)
                            wait_condition(
                                lambda: not any(n.get("id") == node_id for n in canvas_nodes(store, project_id)),
                                30,
                            )
                            # The app archives the orphaned image ~2.5s after the
                            # node deletion; wait for the server-side trash entry.
                            wait_condition(
                                lambda: len(store.list_deleted_media(project_id)) == index,
                                30,
                            )
                        steps["nodes-deleted-and-archived"] = True
                        time.sleep(0.5)

                        # The trash open button lives in the 生成历史 drawer header
                        # (#smartHistoryModal), which is visibility:hidden +
                        # pointer-events:none until opened. Open the drawer the way a
                        # user does, then require the button to be physically
                        # unobscured before clicking it — trashOpenButtonVisible()
                        # alone only tests the hidden attribute, which passes even
                        # with the drawer closed.
                        wait_node_clickable(browser, "#smartHistoryToggle")
                        browser.native_click("#smartHistoryToggle")
                        wait_condition(
                            lambda: (browser.evaluate(
                                'document.getElementById("smartHistoryModal").classList.contains("open")'
                            ) or False),
                            30,
                        )
                        wait_condition(
                            lambda: (browser.evaluate("window.__trashPanelTest.trashOpenButtonVisible()") or False),
                            30,
                        )
                        wait_node_clickable(browser, "#smartTrashOpenBtn")
                        browser.native_click("#smartTrashOpenBtn")
                        dom_rows = trash_items(browser, len(NODES))
                        api_items = fetch_trash_api(server, project_id)
                        browser.capture_screenshot(shots_dir / "01-trash-panel-light.png")
                        screenshots.append("screenshots/01-trash-panel-light.png")

                        def deleted_at_by_asset(items):
                            return {str(item.get("asset_id")): int(item.get("deleted_at") or 0) for item in items}

                        api_order_ok = [item["deleted_at"] for item in api_items] == sorted(
                            (item["deleted_at"] for item in api_items), reverse=True
                        )
                        api_names = [str(item.get("name")) for item in api_items]
                        dom_order_ok = [str(row["asset_id"]) for row in dom_rows] == [
                            asset for asset in (asset_by_node[n] for n, _n, _c in reversed(NODES))
                        ]
                        dom_names = [str(row["name"]) for row in dom_rows]
                        stamp = deleted_at_by_asset(api_items)
                        dom_stamps = [stamp.get(str(row["asset_id"]), 0) for row in dom_rows]
                        assertions.append(
                            {
                                "name": "API trash list is in deleted_at descending order (newest first)",
                                "expected": f"names {EXPECTED_TRASH_NAMES}, deleted_at non-increasing",
                                "actual": {"names": api_names, "deleted_at": [item["deleted_at"] for item in api_items]},
                                "passed": api_order_ok and api_names == EXPECTED_TRASH_NAMES,
                            }
                        )
                        assertions.append(
                            {
                                "name": "rendered DOM list matches the newest-first order",
                                "expected": f"dom names {EXPECTED_TRASH_NAMES}",
                                "actual": {"names": dom_names, "deleted_at": dom_stamps, "sub": [row["sub"] for row in dom_rows]},
                                "passed": dom_order_ok and dom_names == EXPECTED_TRASH_NAMES
                                and dom_stamps == sorted(dom_stamps, reverse=True),
                            }
                        )
                        steps["trash-order"] = api_order_ok and dom_order_ok
                        if not steps["trash-order"]:
                            raise AssertionError(
                                "trash list is not newest-first: api_names="
                                + json.dumps(api_names, ensure_ascii=False)
                                + " dom_names="
                                + json.dumps(dom_names, ensure_ascii=False)
                            )

                    if entry in ("theme", "both"):
                        if entry == "both":
                            time.sleep(0.3)
                        browser.evaluate("window.StudioTheme.set('dark')")
                        browser.wait_for('document.documentElement.classList.contains("theme-dark")', timeout=10)
                        time.sleep(0.6)
                        browser.capture_screenshot(shots_dir / "02-trash-panel-dark.png")
                        screenshots.append("screenshots/02-trash-panel-dark.png")
                        styles = browser.evaluate("window.__trashPanelTest.readThemeStyles()") or {}
                        theme_class = styles.get("theme_class")
                        variables = styles.get("vars") or {}
                        elements = styles.get("elements") or {}
                        expected_track = {
                            "item_border": "--line",
                            "name_color": "--text",
                            "sub_color": "--muted",
                            "icon_color": "--muted",
                            "thumb_border": "--line",
                            "thumb_bg": "--soft",
                        }
                        mismatches = {
                            prop: {"element": elements.get(prop), "variable": variables.get(var)}
                            for prop, var in expected_track.items()
                            if elements.get(prop) != variables.get(var)
                        }
                        assertions.append(
                            {
                                "name": "trash panel computed styles track the current theme variables in dark mode",
                                "expected": {prop: f"var({var})" for prop, var in expected_track.items()},
                                "actual": {"theme": theme_class, "vars": variables, "elements": elements},
                                "passed": theme_class == "dark" and not mismatches,
                            }
                        )
                        steps["trash-theme"] = theme_class == "dark" and not mismatches
                        if not steps["trash-theme"]:
                            raise AssertionError(
                                "trash panel does not follow theme variables: theme="
                                + str(theme_class)
                                + " mismatches="
                                + json.dumps(mismatches, ensure_ascii=False)
                            )
                finally:
                    operations = list(browser.operations)
                    console_errors = list(browser.console_errors)
                    requests = list(browser.request_summaries)
                    browser.close()
    except Exception as error:  # noqa: BLE001 — evidence must capture any failure
        failure = {"message": str(error), "traceback": traceback.format_exc()}
        traceback.print_exc()

    if entry == "order":
        steps = {k: v for k, v in steps.items() if k in ("nodes-deleted-and-archived", "trash-order")}
    if entry == "theme":
        steps = {k: v for k, v in steps.items() if k == "trash-theme"}
    passed = failure is None and all(steps.values())
    write_json(artifacts / "manifest.json", {
        "schema_version": 1,
        "journey": "trash-panel",
        "entry": entry,
        "status": "passed" if passed else "failed",
        "first_failure": failure,
        "viewport": list(VIEWPORT),
        "steps": [{"name": name, "status": "passed" if ok else "failed"} for name, ok in steps.items()],
        "assertions": assertions,
        "screenshots": screenshots,
    })
    (artifacts / "operations.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations), encoding="utf-8"
    )
    write_json(artifacts / "console-errors.json", console_errors)
    write_json(artifacts / "requests.json", requests)
    print(json.dumps({"entry": entry, "status": "passed" if passed else "failed",
                      "steps": steps, "failure": (failure or {}).get("message")}, ensure_ascii=False))
    return {"entry": entry, "passed": passed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    parser.add_argument("--entry", choices=("order", "theme", "both"), default="both")
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    result = run(artifacts, args.entry)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
