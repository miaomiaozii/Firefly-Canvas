"""Issue #32: five-viewport native Composer expand/focus-mode journey."""
from __future__ import annotations

import argparse
import contextlib
import html
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser

VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
MEDIUM_PROMPT = "\n".join(f"构图段落 {index}：保留主体、光线与空间层次。" for index in range(1, 8))
LONG_PROMPT = "心流长提示词：" + "沿着完整创作意图继续书写，不触发任何生成。" * 160


@contextlib.contextmanager
def launched_native_browser(chrome: Path, viewport: tuple[int, int]):
    browser = NativeBrowser.launch(chrome)
    try:
        browser.set_environment(viewport)
        yield browser
    finally:
        browser.close()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_project(store: ProjectStore, name: str) -> str:
    project = store.create_project(name)
    media = store.import_media(
        project["id"],
        harness.image_bytes("FLOW", (56, 82, 132)),
        "composer-flow.png",
        "image/png",
        {"type": "composer-flow-browser-fixture", "name": "composer-flow.png"},
    )
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "flow-source",
                    "type": "smart-prompt",
                    "x": 90,
                    "y": 120,
                    "w": 316,
                    "h": 110,
                    "text": "来源提示词关系必须保留",
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "flow-target",
                    "type": "smart-image",
                    "x": 500,
                    "y": 120,
                    "w": 316,
                    "h": 194,
                    "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "composer-flow.png"}],
                    "text": MEDIUM_PROMPT,
                    "html": html.escape(MEDIUM_PROMPT).replace("\n", "<br>"),
                    "promptDraftText": MEDIUM_PROMPT,
                    "promptDraftHtml": html.escape(MEDIUM_PROMPT).replace("\n", "<br>"),
                    "runSettings": {"promptH": 84},
                },
            },
            {"op": "upsert_edge", "edge": {"from": "flow-source", "to": "flow-target", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def project_draft(store: ProjectStore, project_id: str) -> str:
    project = store.open_project(project_id)
    nodes = (project.get("canvas") or {}).get("nodes") or []
    node = next(item for item in nodes if item.get("id") == "flow-target")
    return str(node.get("promptDraftText") or node.get("text") or "")


def wait_for_saved_draft(store: ProjectStore, project_id: str, expected: str, timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if project_draft(store, project_id) == expected:
            return
        time.sleep(0.1)
    actual = project_draft(store, project_id)
    raise TimeoutError(
        "Composer draft did not autosave to the project store: "
        f"expected_len={len(expected)}, actual_len={len(actual)}, actual_suffix={actual[-24:]!r}"
    )


def run_viewport(
    *,
    chrome: Path,
    server: AsgiBrowserServer,
    store: ProjectStore,
    artifacts_root: Path,
    viewport: tuple[int, int],
) -> dict:
    width, height = viewport
    attempt_root = artifacts_root / f"viewport-{width}x{height}"
    screenshot_root = attempt_root / "screenshots"
    screenshot_root.mkdir(parents=True, exist_ok=False)
    project_name = f"Composer Flow {width}x{height}"
    project_id = seed_project(store, project_name)
    screenshots: list[str] = []
    lifecycle: dict = {"project_id": project_id, "viewport": [width, height]}

    def relative(path: Path) -> str:
        return path.resolve().relative_to(artifacts_root.resolve()).as_posix()

    with launched_native_browser(chrome, viewport) as browser:
        def snap(name: str) -> None:
            path = browser.capture_screenshot(screenshot_root / f"{len(screenshots) + 1:02d}-{name}.png")
            screenshots.append(relative(path))

        browser.navigate(server.base_url + "/")

        def open_project_from_list() -> None:
            browser.wait_for(
                f"[...document.querySelectorAll('#projectGrid .project-card h2')].some(item => item.textContent === {json.dumps(project_name)})",
                timeout=20,
            )
            card_index = browser.evaluate(
                f"[...document.querySelectorAll('#projectGrid .project-card h2')].findIndex(item => item.textContent === {json.dumps(project_name)})"
            )
            browser.native_click(f"#projectGrid .project-card:nth-child({card_index + 1}) .project-open")
            browser.wait_for("document.querySelector('[data-id=\"flow-target\"]')", timeout=20)

        open_project_from_list()
        browser.native_click('[data-id="flow-target"]', relative=(0.5, 0.08))
        browser.wait_for("document.querySelector('#composer.open') && composerUpdateTimer === 0", timeout=20)
        browser.wait_for("document.querySelector('#composerExpandBtn') && document.querySelector('#composerFlowBtn')")

        initial = browser.evaluate(
            "(() => { const input=document.querySelector('#promptInput'); const run=document.querySelector('#runBtn').getBoundingClientRect(); "
            "return {height:input.getBoundingClientRect().height, editor:input, run:{left:run.left,top:run.top,right:run.right,bottom:run.bottom}}; })()"
        )
        pan_before_expand = browser.evaluate("({x: viewport.x, y: viewport.y})")
        browser.native_click("#composerExpandBtn")
        browser.wait_for("composerPromptView.mode === 'expanded'", timeout=10)
        expanded_before = browser.evaluate("document.querySelector('#promptInput').getBoundingClientRect().height")
        # #164: canvas content (nodes, the Composer) may leave the viewport. The product
        # must NOT pan the canvas to pull the Composer back; the user pans to reach it,
        # so the test pans the same way instead of waiting for an automatic rescue.
        pan_after_expand = browser.evaluate("({x: viewport.x, y: viewport.y})")
        if pan_after_expand != pan_before_expand:
            raise AssertionError(
                "the canvas was auto-panned to keep the Composer in view, which #164 removed: "
                f"{pan_before_expand} -> {pan_after_expand}"
            )

        def run_btn_visible() -> bool:
            return browser.evaluate(
                "(() => { const r=document.querySelector('#runBtn').getBoundingClientRect(); "
                "return r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight; })()"
            )

        user_pan_required = not run_btn_visible()
        lifecycle["user_pan_required_to_reach_run_btn"] = user_pan_required

        def pan_canvas(dy: int) -> None:
            """Pan the canvas the way a user does: left-drag on empty canvas.

            The start point is found by hit-testing, because the shell is covered by
            toolbars and nodes at the usual anchor points.
            """
            point = browser.evaluate(
                """(() => {
                  const shell = document.querySelector('#shell');
                  const r = shell.getBoundingClientRect();
                  for (let y = r.top + 8; y < r.bottom - 8; y += 16) {
                    for (let x = r.left + 8; x < r.right - 8; x += 16) {
                      const hit = document.elementFromPoint(x, y);
                      if (hit && (hit === shell || hit.id === 'world' || hit.classList.contains('canvas-grid'))) {
                        return {x: Math.round(x), y: Math.round(y)};
                      }
                    }
                  }
                  return null;
                })()"""
            )
            if not point:
                raise AssertionError("no empty canvas point found to start a user pan")
            send = lambda payload: browser.cdp.command("Input.dispatchMouseEvent", payload)
            send({"type": "mouseMoved", "x": point["x"], "y": point["y"]})
            send({"type": "mousePressed", "x": point["x"], "y": point["y"],
                  "button": "left", "buttons": 1, "clickCount": 1})
            moved = 0.0
            for _ in range(6):
                moved += dy / 6
                send({"type": "mouseMoved", "x": point["x"], "y": point["y"] + moved,
                      "button": "left", "buttons": 1})
            send({"type": "mouseReleased", "x": point["x"], "y": point["y"] + moved,
                  "button": "left", "buttons": 0, "clickCount": 1})
            time.sleep(0.2)

        for _ in range(8):
            if run_btn_visible():
                break
            pan_canvas(-120)
        if not run_btn_visible():
            raise AssertionError(
                "#runBtn is still outside the viewport after panning the canvas the way a user would"
            )
        lifecycle["viewport_pan_after_user_pan"] = browser.evaluate("({x: viewport.x, y: viewport.y})")
        snap("expanded")

        browser.native_drag("#promptResize", dx=0, dy=64, steps=6, release_selector="#shell")
        expanded_after = browser.evaluate("document.querySelector('#promptInput').getBoundingClientRect().height")
        if expanded_after < expanded_before + 30:
            raise AssertionError(f"expanded Composer did not accept additional manual height: {expanded_before} -> {expanded_after}")
        browser.native_click("#composerExpandBtn")
        browser.wait_for("composerPromptView.mode === 'manual'")
        restored_height = browser.evaluate("document.querySelector('#promptInput').getBoundingClientRect().height")
        if abs(restored_height - initial["height"]) > 2:
            raise AssertionError(f"manual height was not restored: {initial['height']} -> {restored_height}")

        browser.native_click("#promptInput")
        browser.native_press("a", modifiers=("Control",))
        browser.native_type(LONG_PROMPT)
        time.sleep(1)
        typed_prompt = browser.evaluate("promptPlainText()")
        if typed_prompt != LONG_PROMPT:
            raise AssertionError(f"native long prompt text mismatch: {typed_prompt!r}")
        prompt_view_state = browser.evaluate("({mode: composerPromptView.mode, natural: promptNaturalHeight(), limit: promptExpandedHeightLimit(), bodyFlow: document.body.classList.contains('prompt-flow-active')})")
        if prompt_view_state["mode"] != "flow":
            browser.native_click("#composerExpandBtn")
            browser.wait_for("composerPromptView.mode === 'flow' && document.body.classList.contains('prompt-flow-active')", timeout=10)
            prompt_view_state = browser.evaluate("({mode: composerPromptView.mode, natural: promptNaturalHeight(), limit: promptExpandedHeightLimit(), bodyFlow: document.body.classList.contains('prompt-flow-active')})")
        if prompt_view_state["mode"] != "flow" or not prompt_view_state["bodyFlow"]:
            raise AssertionError(f"long prompt did not enter flow mode: {prompt_view_state}")
        browser.native_press("a", modifiers=("Control",))
        selected_before = browser.evaluate("window.getSelection().toString()")
        if selected_before != LONG_PROMPT:
            raise AssertionError("native keyboard selection was not established in focus mode")
        snap("flow-before-exit")
        browser.native_press("Escape")
        browser.wait_for("composerPromptView.mode !== 'flow'")
        if browser.evaluate("window.getSelection().toString()") != LONG_PROMPT:
            raise AssertionError("exiting focus mode lost the live editor selection")
        browser.native_click("#composerFlowBtn")
        browser.wait_for("composerPromptView.mode === 'flow' && document.body.classList.contains('prompt-flow-active')")
        same_editor_and_selection = browser.evaluate(
            f"document.activeElement === promptInput && window.getSelection().toString() === {json.dumps(LONG_PROMPT)}"
        )
        if not same_editor_and_selection:
            raise AssertionError("focus mode replaced the live editor or lost its selection")
        snap("flow")

        browser.native_wheel("#promptInput", 620)
        browser.wait_for("document.querySelector('#promptInput').scrollTop > 0", timeout=10)
        flow_scroll_state = browser.evaluate("(() => { const input=document.querySelector('#promptInput'); return {top:input.scrollTop, max:Math.max(0, input.scrollHeight-input.clientHeight)}; })()")
        flow_scroll_top = flow_scroll_state["top"]
        browser.native_click("#promptInput", relative=(0.5, 0.5))
        browser.evaluate("(() => { const input = document.querySelector('#promptInput'); const range = document.createRange(); range.selectNodeContents(input); range.collapse(false); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); return selection.anchorNode && input.contains(selection.anchorNode); })()")
        inserted = browser.evaluate("document.execCommand('insertText', false, 'x')")
        if not inserted:
            raise AssertionError("browser editing command could not create an undo unit")
        browser.wait_for(f"promptPlainText() === {json.dumps(LONG_PROMPT + 'x')}", timeout=10)

        generate_requests_before_escape = [
            item for item in browser.request_summaries
            if "/api/project-image-generation" in str(item.get("url") or "")
        ]
        composition_started = browser.evaluate(
            "promptInput.dispatchEvent(new CompositionEvent('compositionstart', {data:'候'})); promptCompositionActive"
        )
        if not composition_started:
            raise AssertionError("Composer composition guard did not activate")
        scroll_before_exit = browser.evaluate("(() => { const input=document.querySelector('#promptInput'); return {top:input.scrollTop, max:Math.max(0, input.scrollHeight-input.clientHeight)}; })()")
        browser.native_press("Escape")
        browser.wait_for("composerPromptView.mode !== 'flow' && !document.body.classList.contains('prompt-flow-active')")
        browser.wait_for("document.querySelector('#promptInput').scrollTop > 0", timeout=10)
        browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        scroll_after_exit = browser.evaluate("(() => { const input=document.querySelector('#promptInput'); return {top:input.scrollTop, max:Math.max(0, input.scrollHeight-input.clientHeight)}; })()")
        composition_after_exit = browser.evaluate(
            f"promptCompositionActive && promptPlainText() === {json.dumps(LONG_PROMPT + 'x')}"
        )
        if not composition_after_exit:
            raise AssertionError("exiting focus mode disrupted the active composition or draft")
        browser.native_click("#composerFlowBtn")
        browser.wait_for("composerPromptView.mode === 'flow' && document.body.classList.contains('prompt-flow-active')")
        browser.wait_for("document.querySelector('#promptInput').scrollTop > 0", timeout=10)
        browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        scroll_after_reentry = browser.evaluate("(() => { const input=document.querySelector('#promptInput'); return {top:input.scrollTop, max:Math.max(0, input.scrollHeight-input.clientHeight)}; })()")
        composition_after_reentry = browser.evaluate(
            f"promptCompositionActive && promptPlainText() === {json.dumps(LONG_PROMPT + 'x')}"
        )
        if not composition_after_reentry:
            raise AssertionError("entering focus mode disrupted the active composition or draft")
        browser.evaluate("promptInput.dispatchEvent(new CompositionEvent('compositionend', {data:'候'})); !promptCompositionActive")

        browser.native_press("Escape")
        browser.wait_for("composerPromptView.mode !== 'flow' && !document.body.classList.contains('prompt-flow-active')")
        browser.native_press("z", modifiers=("Control",))
        browser.wait_for(f"promptPlainText() === {json.dumps(LONG_PROMPT)}", timeout=10)
        browser.native_press("z", modifiers=("Control", "Shift"))
        browser.wait_for(f"promptPlainText() === {json.dumps(LONG_PROMPT + 'x')}", timeout=10)
        browser.native_press("z", modifiers=("Control",))
        browser.wait_for(f"promptPlainText() === {json.dumps(LONG_PROMPT)}", timeout=10)
        generate_requests_after_escape = [
            item for item in browser.request_summaries
            if "/api/project-image-generation" in str(item.get("url") or "")
        ]
        if generate_requests_after_escape != generate_requests_before_escape:
            raise AssertionError("Escape triggered generation")
        scroll_intent_preserved = (
            scroll_after_exit["top"] > 0
            and scroll_after_reentry["top"] > 0
            and abs(scroll_after_reentry["top"] - min(scroll_before_exit["top"], scroll_after_reentry["max"])) <= 4
        )
        if not scroll_intent_preserved:
            raise AssertionError(
                f"focus scroll intent was lost: {scroll_before_exit} -> {scroll_after_exit} -> {scroll_after_reentry}"
            )

        wait_for_saved_draft(store, project_id, LONG_PROMPT)
        time.sleep(2)
        if project_draft(store, project_id) != LONG_PROMPT:
            raise AssertionError("a delayed Composer persist overwrote the autosaved draft")
        snapshot = store.open_project(project_id)
        connections = (snapshot.get("canvas") or {}).get("connections") or []
        if not any(edge.get("from") == "flow-source" and edge.get("to") == "flow-target" for edge in connections):
            raise AssertionError("focus mode lost the source relationship")

        browser.reload(timeout=30)
        browser.wait_for("document.querySelector('#shell') && document.querySelector('[data-id=\"flow-target\"]')", timeout=30)
        browser.native_click('[data-id="flow-target"]', relative=(0.5, 0.08))
        browser.wait_for("document.querySelector('#composer.open')", timeout=20)
        restored_draft = browser.evaluate("promptPlainText()")
        if restored_draft != LONG_PROMPT:
            raise AssertionError(f"autosaved draft did not survive project close/reopen: {restored_draft!r}")
        snap("reopened")

        console_errors = [
            item for item in browser.console_errors
            if not (
                str(((item.get("params") or {}).get("entry") or {}).get("url") or "").endswith("/favicon.ico")
                and "404" in str(((item.get("params") or {}).get("entry") or {}).get("text") or "")
            )
        ]
        if console_errors:
            raise AssertionError(f"unexpected console errors: {console_errors}")
        operations_path = attempt_root / "operations.jsonl"
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in browser.operations),
            encoding="utf-8",
        )
        requests_path = attempt_root / "requests.json"
        console_path = attempt_root / "console-errors.json"
        write_json(requests_path, browser.request_summaries)
        write_json(console_path, console_errors)
        lifecycle.update(
            {
                "initial_height": initial["height"],
                "expanded_height": expanded_before,
                "manually_extended_height": expanded_after,
                "restored_height": restored_height,
                "flow_scroll_top": flow_scroll_top,
                "selection_preserved": same_editor_and_selection,
                "ime_composition_preserved": composition_after_exit and composition_after_reentry,
                "undo_redo_preserved": True,
                "scroll_intent_preserved": scroll_intent_preserved,
                "draft_autosaved_and_reopened": restored_draft == LONG_PROMPT,
                "source_relationship_preserved": True,
                "escape_generated": False,
                "provider_calls": 0,
            }
        )
        return {
            "status": "passed",
            "viewport": [width, height],
            "browser_version": browser.browser_version().get("product", ""),
            "operations": relative(operations_path),
            "requests": relative(requests_path),
            "console_errors": relative(console_path),
            "screenshots": screenshots,
            "lifecycle": lifecycle,
        }


def run(artifacts_dir: Path, viewports: tuple[tuple[int, int], ...]) -> int:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_browser("chrome")
    attempts: list[dict] = []
    first_failure: dict | None = None
    with tempfile.TemporaryDirectory(prefix="firefly-composer-flow-") as temp:
        root = Path(temp)
        store = ProjectStore(root / "projects")
        app = harness.load_production_app(root, store)
        try:
            with AsgiBrowserServer(app) as server:
                for viewport in viewports:
                    try:
                        attempts.append(
                            run_viewport(
                                chrome=chrome,
                                server=server,
                                store=store,
                                artifacts_root=artifacts_dir,
                                viewport=viewport,
                            )
                        )
                    except Exception as error:
                        first_failure = {
                            "viewport": list(viewport),
                            "type": type(error).__name__,
                            "message": str(error),
                        }
                        attempts.append({"status": "failed", "viewport": list(viewport)})
                        break
        finally:
            store.close()
    manifest = {
        "schema_version": 1,
        "journey": "composer-expand-flow",
        "status": "failed" if first_failure else "passed",
        "environment": {
            "os": platform.system(),
            "browser": {"name": "Google Chrome", "channel": "stable", "version": next((item.get("browser_version", "") for item in attempts if item.get("status") == "passed"), "")},
            "dpr": 1,
            "color_scheme": "light",
            "provider": "offline-none",
        },
        "attempts": attempts,
        "first_failure": first_failure,
    }
    write_json(artifacts_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if first_failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--viewport", choices=[f"{w}x{h}" for w, h in VIEWPORTS])
    args = parser.parse_args()
    selected = VIEWPORTS
    if args.viewport:
        width, height = (int(value) for value in args.viewport.split("x", 1))
        selected = ((width, height),)
    return run(args.artifacts_dir, selected)


if __name__ == "__main__":
    raise SystemExit(main())
