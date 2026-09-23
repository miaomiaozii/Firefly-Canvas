"""Issue #33: five-viewport native prompt-node and read-only long-content journey."""
from __future__ import annotations

import argparse
import contextlib
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
EDITABLE_PROMPT = "提示词节点正文：" + "保持角色、构图、光线、材质与镜头连续，不丢失任何创作约束。" * 180
EDIT_MARKER = "x"
UPSTREAM_PROMPT = "来源提示词：连接关系在完整编辑前后必须保持。"
HISTORY_PROMPT = "生成历史提示词：" + "完整保留镜头语言、角色动作、空间关系与色彩层次。" * 150
ERROR_TEXT = "生成失败详情：" + "上游模型拒绝当前组合，但原始诊断信息必须完整可读且不能修改项目状态。" * 150


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
        harness.image_bytes("READ", (68, 96, 144)),
        "long-content.png",
        "image/png",
        {"type": "long-content-browser-fixture", "name": "long-content.png"},
    )
    filler_logs = [
        {
            "id": f"reader-filler-{index:03d}",
            "type": "online-image",
            "status": "succeeded",
            "prompt": f"历史填充 {index}",
            "createdAt": 10_000 + index,
            "outputs": [],
        }
        for index in range(180)
    ]
    logs = [
        *filler_logs,
        {
            "id": "reader-error",
            "type": "online-image",
            "status": "failed",
            "prompt": "失败任务提示词",
            "error": ERROR_TEXT,
            "createdAt": 2_000_000,
            "outputs": [],
        },
        {
            "id": "reader-history",
            "type": "online-image",
            "status": "succeeded",
            "prompt": HISTORY_PROMPT,
            "createdAt": 3_000_000,
            "outputs": [{"asset_id": media["asset_id"], "kind": "image", "name": "long-content.png"}],
            "result_node_id": "reader-image",
        },
    ]
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "reader-source",
                    "type": "smart-prompt",
                    "x": 80,
                    "y": 120,
                    "w": 316,
                    "h": 180,
                    "text": UPSTREAM_PROMPT,
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "reader-prompt",
                    "type": "smart-prompt",
                    "x": 450,
                    "y": 120,
                    "w": 316,
                    "h": 220,
                    "text": EDITABLE_PROMPT,
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "reader-image",
                    "type": "smart-image",
                    "x": 820,
                    "y": 120,
                    "w": 316,
                    "h": 194,
                    "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "long-content.png"}],
                    "runSettings": {"promptH": 84},
                },
            },
            {"op": "upsert_edge", "edge": {"from": "reader-source", "to": "reader-prompt", "kind": "input"}},
            {"op": "upsert_edge", "edge": {"from": "reader-prompt", "to": "reader-image", "kind": "input"}},
            {"op": "set_canvas_meta", "history": logs},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def project_prompt(store: ProjectStore, project_id: str) -> str:
    canvas = store.open_project(project_id).get("canvas") or {}
    node = next(item for item in canvas.get("nodes") or [] if item.get("id") == "reader-prompt")
    return str(node.get("text") or "")


def project_content_snapshot(store: ProjectStore, project_id: str) -> str:
    canvas = store.open_project(project_id).get("canvas") or {}
    durable = {
        "nodes": canvas.get("nodes") or [],
        "connections": canvas.get("connections") or [],
        "history": canvas.get("history") or [],
    }
    return json.dumps(durable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def wait_for_prompt(store: ProjectStore, project_id: str, expected: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if project_prompt(store, project_id) == expected:
            return
        time.sleep(0.1)
    actual = project_prompt(store, project_id)
    raise TimeoutError(f"prompt node did not autosave: expected_len={len(expected)}, actual_len={len(actual)}")


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
    project_name = f"Long Content {width}x{height}"
    project_id = seed_project(store, project_name)
    screenshots: list[str] = []

    def relative(path: Path) -> str:
        return path.resolve().relative_to(artifacts_root.resolve()).as_posix()

    with launched_native_browser(chrome, viewport) as browser:
        def snap(name: str) -> None:
            path = browser.capture_screenshot(screenshot_root / f"{len(screenshots) + 1:02d}-{name}.png")
            screenshots.append(relative(path))

        browser.navigate(server.base_url + "/")
        browser.wait_for(
            f"[...document.querySelectorAll('#projectGrid .project-card h2')].some(item => item.textContent === {json.dumps(project_name)})",
            timeout=20,
        )
        card_index = browser.evaluate(
            f"[...document.querySelectorAll('#projectGrid .project-card h2')].findIndex(item => item.textContent === {json.dumps(project_name)})"
        )
        browser.native_click(f"#projectGrid .project-card:nth-child({card_index + 1}) .project-open")
        browser.wait_for("document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text')", timeout=20)

        prompt_selector = '[data-id="reader-prompt"] .prompt-node-text'
        flow_selector = '[data-id="reader-prompt"] .prompt-node-flow-toggle'
        browser.native_click(prompt_selector)
        browser.native_press("a", modifiers=("Control",))
        selection_before = browser.evaluate(
            "(() => { const t=document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text'); window.__t13PromptTextarea=t; return {start:t.selectionStart,end:t.selectionEnd,length:t.value.length}; })()"
        )
        if selection_before["start"] != 0 or selection_before["end"] != selection_before["length"]:
            raise AssertionError(f"native prompt-node selection was not established: {selection_before}")
        composition_started = browser.evaluate(
            "window.__t13PromptTextarea.dispatchEvent(new CompositionEvent('compositionstart', {data:'候'})); promptNodeFlowView.compositionActive"
        )
        if not composition_started:
            raise AssertionError("prompt-node composition guard did not activate")
        browser.native_click(flow_selector)
        browser.wait_for("promptNodeFlowView.mode === 'flow' && document.body.classList.contains('prompt-node-flow-active')", timeout=10)
        prompt_flow = browser.evaluate(
            "(() => { const t=document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text'); const r=t.getBoundingClientRect(); return {same:t===window.__t13PromptTextarea,focused:document.activeElement===t,start:t.selectionStart,end:t.selectionEnd,length:t.value.length,composition:promptNodeFlowView.compositionActive,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},editable:!t.readOnly}; })()"
        )
        if not all((prompt_flow["same"], prompt_flow["focused"], prompt_flow["composition"], prompt_flow["editable"])):
            raise AssertionError(f"prompt-node flow replaced or disabled the live textarea: {prompt_flow}")
        if prompt_flow["start"] != 0 or prompt_flow["end"] != prompt_flow["length"]:
            raise AssertionError(f"prompt-node flow lost selection: {prompt_flow}")
        if prompt_flow["rect"]["left"] < 0 or prompt_flow["rect"]["top"] < 0 or prompt_flow["rect"]["right"] > width or prompt_flow["rect"]["bottom"] > height:
            raise AssertionError(f"prompt-node flow editor escaped viewport: {prompt_flow['rect']}")
        snap("prompt-node-flow")

        browser.native_wheel(prompt_selector, 720)
        browser.wait_for(f"document.querySelector({json.dumps(prompt_selector)}).scrollTop > 0", timeout=10)
        prompt_scroll_top = browser.evaluate(f"document.querySelector({json.dumps(prompt_selector)}).scrollTop")
        browser.native_press("Escape")
        browser.wait_for("promptNodeFlowView.mode === 'idle' && !document.body.classList.contains('prompt-node-flow-active')")
        after_exit = browser.evaluate(
            "(() => { const t=document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text'); return {same:t===window.__t13PromptTextarea,focused:document.activeElement===t,start:t.selectionStart,end:t.selectionEnd,length:t.value.length,composition:promptNodeFlowView.compositionActive}; })()"
        )
        if not all((after_exit["same"], after_exit["focused"], after_exit["composition"])):
            raise AssertionError(f"prompt-node flow exit lost DOM, focus, or composition: {after_exit}")
        if after_exit["start"] != 0 or after_exit["end"] != after_exit["length"]:
            raise AssertionError(f"prompt-node flow exit lost selection: {after_exit}")
        browser.evaluate("window.__t13PromptTextarea.dispatchEvent(new CompositionEvent('compositionend', {data:'候'})); !promptNodeFlowView.compositionActive")

        browser.native_click(flow_selector)
        browser.wait_for("promptNodeFlowView.mode === 'flow'")
        browser.evaluate("window.__t13PromptTextarea.focus(); window.__t13PromptTextarea.setSelectionRange(window.__t13PromptTextarea.value.length, window.__t13PromptTextarea.value.length)")
        browser.native_type(EDIT_MARKER)
        expected_prompt = EDITABLE_PROMPT + EDIT_MARKER
        time.sleep(0.3)
        typed_state = browser.evaluate("({value:window.__t13PromptTextarea.value,start:window.__t13PromptTextarea.selectionStart,end:window.__t13PromptTextarea.selectionEnd,flowNodeId:promptNodeFlowView.nodeId,same:promptNodeFlowView.textarea===window.__t13PromptTextarea})")
        if typed_state["value"] != expected_prompt:
            raise AssertionError(
                "native prompt-node edit mismatch: "
                f"expected_len={len(expected_prompt)}, actual_len={len(typed_state['value'])}, "
                f"actual_prefix={typed_state['value'][:24]!r}, actual_suffix={typed_state['value'][-40:]!r}, state={typed_state}"
            )
        browser.native_press("z", modifiers=("Control",))
        browser.wait_for(f"window.__t13PromptTextarea.value === {json.dumps(EDITABLE_PROMPT)}", timeout=10)
        browser.native_press("z", modifiers=("Control", "Shift"))
        browser.wait_for(f"window.__t13PromptTextarea.value === {json.dumps(expected_prompt)}", timeout=10)
        browser.native_press("Escape")
        browser.wait_for("promptNodeFlowView.mode === 'idle'")
        wait_for_prompt(store, project_id, expected_prompt)
        prompt_snapshot = store.open_project(project_id).get("canvas") or {}
        connections = prompt_snapshot.get("connections") or []
        source_relationship_preserved = (
            any(edge.get("from") == "reader-source" and edge.get("to") == "reader-prompt" for edge in connections)
            and any(edge.get("from") == "reader-prompt" and edge.get("to") == "reader-image" for edge in connections)
        )
        if not source_relationship_preserved:
            raise AssertionError("prompt-node flow lost source relationships")

        browser.reload(timeout=30)
        browser.wait_for("document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text')", timeout=30)
        reopened_prompt = browser.evaluate("document.querySelector('[data-id=\"reader-prompt\"] .prompt-node-text').value")
        if reopened_prompt != expected_prompt:
            raise AssertionError("prompt-node autosave did not survive reload")
        snap("prompt-node-reopened")

        browser.native_click('[data-id="reader-image"]', relative=(0.5, 0.08))
        browser.wait_for("document.querySelector('#composer.open') && document.querySelector('[data-long-content-open=\"input-prompt\"]')", timeout=20)
        browser.wait_for("composerUpdateTimer === 0", timeout=20)
        readonly_snapshot = project_content_snapshot(store, project_id)
        connection_text = browser.evaluate("inputPromptTextFor(nodes.find(node => node.id === 'reader-image')).trim()")
        browser.native_click('[data-long-content-open="input-prompt"]')
        browser.wait_for("longContentSession.mode === 'readonly' && document.querySelector('#longContentReader.open')", timeout=10)
        connection_reader = browser.evaluate(
            "(() => { const body=document.querySelector('#longContentReaderBody'); const r=body.getBoundingClientRect(); return {text:body.textContent,editable:body.isContentEditable,readonly:body.getAttribute('aria-readonly'),focused:document.activeElement===body,scrollable:body.scrollHeight>body.clientHeight,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom}}; })()"
        )
        if connection_reader["text"] != connection_text or connection_reader["editable"] or connection_reader["readonly"] != "true":
            raise AssertionError("connection prompt reader was incomplete or editable")
        if connection_reader["rect"]["left"] < 0 or connection_reader["rect"]["top"] < 0 or connection_reader["rect"]["right"] > width or connection_reader["rect"]["bottom"] > height:
            raise AssertionError(f"connection reader escaped viewport: {connection_reader['rect']}")
        browser.native_wheel("#longContentReaderBody", 760)
        browser.wait_for("document.querySelector('#longContentReaderBody').scrollTop > 0", timeout=10)
        connection_scroll_top = browser.evaluate("document.querySelector('#longContentReaderBody').scrollTop")
        snap("connection-prompt-reader")
        browser.native_press("Escape")
        browser.wait_for("longContentSession.mode === 'idle' && document.activeElement?.matches('[data-long-content-open=\"input-prompt\"]')", timeout=10)

        browser.native_click("#smartHistoryToggle")
        browser.wait_for("document.querySelector('#smartHistoryModal.open') && document.querySelector('[data-canvas-history-id=\"reader-history\"] [data-long-content-open=\"history-prompt\"]')", timeout=20)
        history_prompt_selector = '[data-canvas-history-id="reader-history"] [data-long-content-open="history-prompt"]'
        browser.evaluate(f"window.__t13HistoryTrigger=document.querySelector({json.dumps(history_prompt_selector)})")
        browser.native_click(history_prompt_selector)
        browser.wait_for("longContentSession.kind === 'history-prompt'")
        history_reader = browser.evaluate(
            "({text:longContentReaderBody.textContent,editable:longContentReaderBody.isContentEditable,scrollable:longContentReaderBody.scrollHeight>longContentReaderBody.clientHeight})"
        )
        if history_reader["text"] != HISTORY_PROMPT or history_reader["editable"]:
            raise AssertionError("history prompt reader was incomplete or editable")
        browser.native_wheel("#longContentReaderBody", 760)
        browser.wait_for("longContentReaderBody.scrollTop > 0", timeout=10)
        history_scroll_top = browser.evaluate("longContentReaderBody.scrollTop")
        snap("history-prompt-reader")
        trigger_was_virtualized = browser.evaluate(
            "smartHistoryList.scrollLeft=smartHistoryList.scrollWidth; renderSmartHistoryWindow(true); !window.__t13HistoryTrigger.isConnected"
        )
        if not trigger_was_virtualized:
            raise AssertionError("history virtualization did not replace the original reader trigger")
        browser.native_press("Escape")
        browser.wait_for(
            "longContentSession.mode === 'idle' && document.activeElement?.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId === 'reader-history' && document.activeElement?.dataset.longContentOpen === 'history-prompt'",
            timeout=10,
        )

        error_selector = '[data-canvas-history-id="reader-error"] [data-long-content-open="history-error"]'
        browser.wait_for(f"document.querySelector({json.dumps(error_selector)})", timeout=10)
        browser.evaluate(f"document.querySelector({json.dumps(error_selector)}).focus()")
        browser.native_press("Enter")
        browser.wait_for("longContentSession.kind === 'history-error'", timeout=10)
        error_reader = browser.evaluate(
            "({text:longContentReaderBody.textContent,editable:longContentReaderBody.isContentEditable,scrollable:longContentReaderBody.scrollHeight>longContentReaderBody.clientHeight})"
        )
        if error_reader["text"] != ERROR_TEXT or error_reader["editable"]:
            raise AssertionError("generation error reader was incomplete or editable")
        browser.native_wheel("#longContentReaderBody", 760)
        browser.wait_for("longContentReaderBody.scrollTop > 0", timeout=10)
        error_scroll_top = browser.evaluate("longContentReaderBody.scrollTop")
        snap("generation-error-reader")
        browser.native_click("#longContentReaderClose")
        browser.wait_for(
            "longContentSession.mode === 'idle' && document.activeElement?.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId === 'reader-error' && document.activeElement?.dataset.longContentOpen === 'history-error'",
            timeout=10,
        )

        if project_content_snapshot(store, project_id) != readonly_snapshot:
            raise AssertionError("read-only long-content views mutated project nodes, history, or source relationships")

        generation_requests = [
            item
            for item in browser.request_summaries
            if any(
                marker in str(item.get("url") or "")
                for marker in ("/image-generation-tasks", "/api/generate", "/api/canvas-llm")
            )
        ]
        if generation_requests:
            raise AssertionError(f"long-content interactions triggered provider requests: {generation_requests}")
        console_errors = [
            item
            for item in browser.console_errors
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
        return {
            "status": "passed",
            "viewport": [width, height],
            "browser_version": browser.browser_version().get("product", ""),
            "operations": relative(operations_path),
            "requests": relative(requests_path),
            "console_errors": relative(console_path),
            "screenshots": screenshots,
            "lifecycle": {
                "project_id": project_id,
                "prompt_same_dom": prompt_flow["same"] and after_exit["same"],
                "prompt_selection_preserved": after_exit["start"] == 0 and after_exit["end"] == after_exit["length"],
                "prompt_ime_preserved": after_exit["composition"],
                "prompt_undo_redo_preserved": True,
                "prompt_scroll_top": prompt_scroll_top,
                "prompt_autosaved_and_reopened": reopened_prompt == expected_prompt,
                "source_relationships_preserved": source_relationship_preserved,
                "connection_reader_complete": connection_reader["text"] == connection_text,
                "connection_reader_scroll_top": connection_scroll_top,
                "history_prompt_reader_complete": history_reader["text"] == HISTORY_PROMPT,
                "history_prompt_scroll_top": history_scroll_top,
                "history_focus_restored_after_virtualization": True,
                "error_reader_complete": error_reader["text"] == ERROR_TEXT,
                "error_reader_scroll_top": error_scroll_top,
                "readonly_views_immutable": True,
                "provider_calls": 0,
            },
        }


def run(artifacts_dir: Path, viewports: tuple[tuple[int, int], ...]) -> int:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_browser("chrome")
    attempts: list[dict] = []
    first_failure: dict | None = None
    with tempfile.TemporaryDirectory(prefix="firefly-long-content-") as temp:
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
        "journey": "long-content-reading",
        "status": "failed" if first_failure else "passed",
        "environment": {
            "os": platform.system(),
            "browser": {
                "name": "Google Chrome",
                "channel": "stable",
                "version": next((item.get("browser_version", "") for item in attempts if item.get("status") == "passed"), ""),
            },
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
    parser.add_argument("--viewport", choices=[f"{width}x{height}" for width, height in VIEWPORTS])
    args = parser.parse_args()
    selected = VIEWPORTS
    if args.viewport:
        width, height = (int(value) for value in args.viewport.split("x", 1))
        selected = ((width, height),)
    return run(args.artifacts_dir, selected)


if __name__ == "__main__":
    raise SystemExit(main())
