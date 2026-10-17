"""Native-browser evidence for the #189 pending-resume expansion: the four remaining save gates.

Each gate flushes the canvas before sending a request and, before the #189 follow-up fix,
cancelled the request when the flush failed:

  conversation: POST /api/project-library/canvases/<id>/messages              (client request_id; server dedupes)
  photoshop:    POST /api/project-library/canvases/<id>/photoshop/round-trips (no server dedup -> single-shot)
  local-edit:   POST /api/project-library/canvases/<id>/local-edit             (no server dedup -> single-shot)
  video:        POST /api/project-library/canvases/<id>/video-generation-tasks (no server dedup -> single-shot)

Strategy (m03364/m03420): a gate failure must NOT cancel the user's request. The request is
queued in memory (canvas-generation-tasks.js: smartPendingActionResumes), the canvas save is
scheduled, and when the save settles (smart-canvas.js:6671 markAutosaveSaved),
resumeSmartPendingActionResumes() replays queued requests for live nodes. Entries are
single-shot: each is removed from the table BEFORE its fetch fires (video / local-edit /
photoshop endpoints have no server-side dedup). Node deletion drops the entry; there is no
timeout and no cancellation by design.

--mode repro: pre-fix characterization — the old stop toast fires and, after the save
             settles, the request never arrives (0 gate POSTs). Exit 0 when characterized.
--mode fixed: post-fix guarantees — a pending entry is queued at the gate, 0 gate POSTs
             before settle, exactly one gate POST after settle, the pending table is drained
             with a 'replayed' log event, and the server shows no duplicate task. Exit 0.
--mode refresh (#197): persistence guarantee — the gate fails, the in-session replay is
             stubbed out (pure test instrumentation), the canvas save settles and the
             pending mark must land on the SERVER canvas (pendingActionResumes has exactly
             one entry for this gate, 0 gate POSTs), then the page is RELOADED (native
             full page load) and the load path must replay the request exactly once:
             exactly one gate POST across both sessions, exactly one server task/artifact,
             the server mark drained, 'replayed' log event in the reloaded session.
             RED before the #197 persistence work (the mark never reaches the server).
--mode refresh-sent (#197): duplicate-prevention guarantee — the in-session replay fires
             normally (one gate POST, mark cleared and persisted), then the page is
             RELOADED and the assertions demand NO second gate POST across both sessions
             and the server task count still exactly 1 (a request that was already sent
             must never be replayed).

Usage:
  python tests/issue189gates_pending_resume_native_browser.py --gate conversation --mode fixed \
      --artifacts-dir C:/Users/Administrator/.cache/fix189gates/runs/g1 \
      --shots-dir C:/Users/Administrator/.cache/fix189gates/shots/g1 --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402
from tests.conversation_browser import audio_bytes, image_bytes  # noqa: E402
from tests.issue189_gate_flush_native_browser import (  # noqa: E402
    FLUSH_SPY_JS,
    INJECT_STALE_JS,
    INSTRUMENT_JS,
    MockImageServer,
    RequestLogger,
    wait_prompt_clickable,
    wait_state,
    wait_state_soft,
    write_json,
    write_jsonl,
)
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser  # noqa: E402

VIEWPORT = (1520, 940)
ARM_STALE = 3
GATE_SETTLE_S = 3.0
FIXED_SETTLE_S = 8.0

# #197: the persisted pending-mark field, ANCHORED ON THE NODE (node.pendingActionResumes).
# The incremental save path carries whole nodes via upsert_node mutations, while top-level
# canvas fields are NOT part of the mutation snapshot (snapshotFromCanvas meta whitelist
# is {title, icon, history} only — project-canvas-mutations.js). The server-side
# _apply_mutation (firefly_projects.py) merges the whole node dict, so node fields land.
PENDING_FIELD = "pendingActionResumes"
# #197 test instrumentation ONLY: stub out the in-session replay dispatcher so the
# persisted mark (written by the gate) survives the reload. Classic-script top-level
# function declarations live on the global object, and markAutosaveSaved calls the
# dispatcher unqualified, so this reassignment is observed by the page code. The
# reloaded page is a fresh context and gets the real dispatcher back.
STUB_RESUME_JS = (
    "if(typeof window.resumeSmartPendingActionResumes!=='function') throw new Error('resumeSmartPendingActionResumes missing');"
    "window.__fcResumeOriginal=window.resumeSmartPendingActionResumes;"
    "window.resumeSmartPendingActionResumes=function(){return Promise.resolve();};"
)

DIRTY_NODE = "g189-dirty"
CONV_NODE = "g189-conv"
CONV_AUDIO = "g189-conv-audio"
PS_NODE = "g189-ps"
LE_SRC = "g189-src"
LE_NODE = "g189-le"
VID_NODE = "g189-vid"

CONV_TEXT = "issue189 gates conversation message"
DIRTY_TEXT = "issue189 gates dirty edit"
VIDEO_EDIT = " issue189 gates video edit"
LE_PROMPT = "issue189 gates local edit prompt"
IMAGE_MODEL = "fix189-http-model"
VIDEO_MODEL = "fix189g-video-model"
PROVIDER_ID = "fix189-http"

STOP_TOAST = {
    "conversation": "画布尚未保存，对话消息未发送",
    "photoshop": "画布尚未保存，无法发送到 Photoshop",
    "local-edit": "画布尚未保存，局部编辑未提交",
    "video": "画布尚未保存，已停止视频生成",
}
PENDING_TOAST = "已挂起"
KIND = {
    "conversation": "conversation",
    "photoshop": "photoshop",
    "local-edit": "local-edit",
    "video": "video",
}
GATE_URL_PART = {
    "conversation": "/messages",
    "photoshop": "/photoshop/round-trips",
    "local-edit": "/local-edit",
    "video": "/video-generation-tasks",
}
GATE_POST_TAIL = {
    "conversation": "/messages",
    "photoshop": "/photoshop/round-trips",
    "local-edit": "/local-edit",
    "video": "/video-generation-tasks",
}
FIRST_WAIT = {
    "conversation": 'document.querySelector("[data-conversation-input]")',
    "photoshop": 'document.querySelector(".image-node[data-id=\\"%s\\"]")' % PS_NODE,
    "local-edit": 'document.querySelector("[data-local-edit-node=\\"%s\\"]")' % LE_NODE,
    "video": 'document.querySelector(".image-node[data-id=\\"%s\\"]")' % VID_NODE,
}


def capture_js(gate: str) -> str:
    """Page-side evidence snapshot (JSON string)."""
    state_js = {
        "conversation": (
            "var t=document.querySelector('[data-conversation-input]');"
            "out.state={input:t?(t.value===undefined?null:t.value):null,"
            "userMsgs:(live.messages||[]).filter(function(m){return m.role==='user';}).map(function(m){return {text:m.text,status:m.status};})};"
        ),
        "photoshop": (
            "out.state={pill:(function(){var el=document.getElementById('projectSaveState');return el?el.dataset.state:null;})()};"
        ),
        "local-edit": (
            "out.state={busy:!!live._leBusy,output:!!live.output_asset_id,"
            "images:(live.images||[]).length,mode:live.mode};"
        ),
        "video": (
            "out.state={pending:(live.pendingTasks||[]).length,running:!!live.running,"
            "pendingCount:live.pending||0,taskIds:(live.pendingTasks||[]).map(function(t){return t.taskId;})};"
        ),
    }[gate]
    return (
        "(function(){"
        "var out={"
        "toasts:(window.__fix189Toasts||[]),"
        "flush:(window.__fix189FlushResults||[]),"
        "net:(window.__fix189Net||[]).filter(function(e){return (e.url||'').indexOf(" + json.dumps(GATE_URL_PART[gate]) + ")!==-1;}),"
        "pending:(function(){try{var m=window.__fcPendingActionResumes;if(!(m instanceof Map))return {present:false,size:0,entries:[]};var e=[];m.forEach(function(x){e.push({kind:x.kind,nodeId:x.nodeId});});return {present:true,size:m.size,entries:e};}catch(err){return {present:false,error:String(err)};}})(),"
        "log:(window.__fcPendingActionResumeLog||[]).map(function(e){return {event:e.event,kind:e.kind,nodeId:e.nodeId};})"
        "};"
        "var live=(window.canvasState&&canvasState.nodes||[]).find(function(n){return n.id===" + json.dumps(NODE_ID_OF(gate)) + ";});"
        "if(!live){out.state={found:false};return JSON.stringify(out);}"
        "out.state={found:true};" + state_js +
        "return JSON.stringify(out);"
        "})()"
    )


def NODE_ID_OF(gate: str) -> str:
    return {
        "conversation": CONV_NODE,
        "photoshop": PS_NODE,
        "local-edit": LE_NODE,
        "video": VID_NODE,
    }[gate]


def seed(store: ProjectStore, gate: str) -> str:
    project = store.create_project("Issue 189 gates " + gate)
    pid = project["id"]
    nodes: list[dict] = []
    edges: list[dict] = []
    if gate == "conversation":
        # The harness fake conversation adapter (tests/conversation_browser.py:594-596)
        # raises unless the request carries a connected audio reference, and the server
        # resolves those references only from canvas edges pointing at the conversation
        # node (app/routers/api_project_library.py:3157-3171). Without this edge the
        # message reaches the server but the task ends status="failed", which would make
        # the green run meaningless. Mirrors tests/conversation_browser.py:194-201,247-251.
        audio = store.import_media(pid, audio_bytes(), "reference.wav", "audio/wav",
                                   source_info={"type": "issue-189-gates-fixture", "name": "reference.wav"})
        nodes = [
            {"id": DIRTY_NODE, "type": "smart-image", "name": "Dirty", "x": 800, "y": 40, "w": 300, "h": 200,
             "runPrompt": "issue189 gates dirty node",
             "runSettings": {"engine": "api", "apiKind": "image", "model": IMAGE_MODEL,
                             "ratio": "square", "resolution": "1k", "count": 1}},
            {"id": CONV_NODE, "type": "smart-conversation", "name": "Conversation", "x": 40, "y": 300, "w": 430, "h": 300,
             "messages": [
                 {"id": "m-seed-1", "role": "user", "text": "hello"},
                 {"id": "m-seed-2", "role": "assistant", "text": "hi there"},
             ]},
            {"id": CONV_AUDIO, "type": "smart-image", "name": "Audio ref", "x": 40, "y": 640, "w": 180, "h": 120,
             "scale": 1, "images": [{"asset_id": audio["asset_id"], "kind": "audio", "name": "reference.wav"}]},
        ]
        edges = [{"id": f"{CONV_AUDIO}-{CONV_NODE}", "from": CONV_AUDIO, "to": CONV_NODE, "kind": "input"}]
    elif gate == "photoshop":
        asset = store.import_media(pid, image_bytes("G189", (46, 92, 138)), "g189.png", "image/png",
                                   source_info={"type": "issue-189-gates-fixture", "name": "g189.png"})
        nodes = [
            {"id": DIRTY_NODE, "type": "smart-image", "name": "Dirty", "x": 800, "y": 40, "w": 300, "h": 200,
             "runPrompt": "issue189 gates dirty node",
             "runSettings": {"engine": "api", "apiKind": "image", "model": IMAGE_MODEL,
                             "ratio": "square", "resolution": "1k", "count": 1}},
            {"id": PS_NODE, "type": "smart-image", "name": "PS source", "x": 40, "y": 300, "w": 260, "h": 200,
             "images": [{"asset_id": asset["asset_id"], "kind": "image", "name": "g189.png"}]},
        ]
    elif gate == "local-edit":
        asset = store.import_media(pid, image_bytes("G189", (46, 92, 138)), "g189.png", "image/png",
                                   source_info={"type": "issue-189-gates-fixture", "name": "g189.png"})
        nodes = [
            {"id": DIRTY_NODE, "type": "smart-image", "name": "Dirty", "x": 800, "y": 40, "w": 300, "h": 200,
             "runPrompt": "issue189 gates dirty node",
             "runSettings": {"engine": "api", "apiKind": "image", "model": IMAGE_MODEL,
                             "ratio": "square", "resolution": "1k", "count": 1}},
            {"id": LE_SRC, "type": "smart-image", "name": "LE source", "x": 40, "y": 300, "w": 260, "h": 200,
             "images": [{"asset_id": asset["asset_id"], "kind": "image", "name": "g189.png"}]},
            {"id": LE_NODE, "type": "local-edit", "name": "LE target", "x": 380, "y": 300, "w": 460, "h": 220,
             "original_asset_id": asset["asset_id"],
             "localEditInputRoles": {"originalNode": LE_SRC, "referenceNodes": [], "promptNode": "", "promptNodes": []},
             "prompt": LE_PROMPT, "selection": [12, 18, 112, 118], "mode": "modify",
             "selectionType": "polygon", "feather_radius": 8, "generation_size": "1024x1024",
             "generation_resolution": "1k", "aspect_ratio": "1:1",
             "runSettings": {"engine": "api", "apiKind": "image", "model": IMAGE_MODEL,
                             "ratio": "square", "resolution": "1k", "count": 1}},
        ]
    elif gate == "video":
        nodes = [
            {"id": VID_NODE, "type": "smart-image", "name": "Video", "x": 800, "y": 40, "w": 320, "h": 240,
             "runPrompt": "issue189 gates video prompt",
             "runSettings": {"engine": "api", "apiKind": "video", "model": VIDEO_MODEL,
                             "videoModel": VIDEO_MODEL, "videoRoutingMode": "manual",
                             "videoProvider": PROVIDER_ID, "videoAspect": "16:9",
                             "videoResolution": "720p", "videoDuration": 5, "count": 1}},
        ]
    else:
        raise ValueError(f"unknown gate: {gate}")
    mutations = [{"op": "upsert_node", "node": n} for n in nodes]
    mutations += [{"op": "upsert_edge", "edge": e} for e in edges]
    store.apply_mutations(pid, mutations, base_revision=0)
    if gate == "local-edit":
        # syncLocalEditInputRoles (smart-canvas.js) re-derives the role map from
        # canvas.connection edges on every render and OVERWRITES the node field;
        # without the edge the original role resolves empty, the submit button
        # stays disabled, and native clicks on a disabled button never produce
        # an observable mousedown.
        store.apply_mutations(pid, [{
            "op": "upsert_edge",
            "edge": {"from": LE_SRC, "to": LE_NODE, "kind": "input", "role": "original"},
        }], base_revision=1)
    store.flush(pid)
    return pid


def write_test_provider(app, base_url: str, with_video: bool) -> str:
    module = app.state.issue13_module
    path = pathlib.Path(module.API_PROVIDERS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    provider = {
        "id": PROVIDER_ID, "name": PROVIDER_ID, "enabled": True,
        "protocol": "openai", "base_url": base_url,
        "api_key": "fix189g-fake-key",
        "image_request_mode": "openai",
        "image_generation_endpoint": "", "image_edit_endpoint": "",
        "image_models": [IMAGE_MODEL], "chat_models": [],
        "video_models": [VIDEO_MODEL] if with_video else [],
        "ms_loras": [], "ms_defaults_version": 0,
    }
    path.write_text(json.dumps([provider], ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def gate_posts(logger: RequestLogger | None, gate: str) -> list[dict]:
    tail = GATE_POST_TAIL[gate]
    return [e for e in (logger.entries if logger else [])
            if e.get("method") == "POST" and e.get("path", "").rstrip("/").endswith(tail)]


def server_pending_field(store: ProjectStore, project_id: str) -> list[dict]:
    """#197: the pending marks as persisted on the SERVER canvas nodes (authoritative
    readback). Each entry is node.pendingActionResumes[i] plus the nodeId it sits on."""
    opened = store.open_project(project_id)
    canvas = opened.get("canvas") or {}
    entries: list[dict] = []
    for node in canvas.get("nodes") or []:
        node_id = str(node.get("id") or "")
        for rec in (node.get(PENDING_FIELD) or []):
            if isinstance(rec, dict):
                entries.append({**rec, "nodeId": node_id})
    return entries


def poll_server_field(store: ProjectStore, project_id: str, expected_len: int,
                      timeout: float = 30.0) -> list[dict]:
    deadline = time.time() + timeout
    last: list[dict] = []
    while time.time() < deadline:
        last = server_pending_field(store, project_id)
        if len(last) == expected_len:
            return last
        time.sleep(0.5)
    return last


def server_task_ids(store: ProjectStore, project_id: str) -> list[str]:
    opened = store.open_project(project_id)
    ids: list[str] = []
    for node in opened["canvas"].get("nodes") or []:
        for task in node.get("pendingTasks") or []:
            if task.get("taskId"):
                ids.append(str(task["taskId"]))
    for record in opened["canvas"].get("history") or []:
        if record.get("task_id"):
            ids.append(str(record["task_id"]))
    return ids


def server_readback(store: ProjectStore, project_id: str, gate: str) -> dict:
    if gate == "conversation":
        opened = store.open_project(project_id)
        node = next((n for n in opened["canvas"].get("nodes") or [] if n.get("id") == CONV_NODE), {})
        users = [m for m in (node.get("messages") or []) if m.get("role") == "user"]
        return {
            "conv-user-messages": [
                {"text": m.get("text"), "status": m.get("status")} for m in users
            ],
            "conv-gate-text-count": sum(1 for m in users if m.get("text") == CONV_TEXT),
            "conv-gate-statuses": [m.get("status") for m in users if m.get("text") == CONV_TEXT],
            "conv-assistant-replies": sum(1 for m in (node.get("messages") or [])
                                          if m.get("role") == "assistant" and m.get("text") != "hi there"),
        }
    if gate == "photoshop":
        trips = store.load_photoshop_round_trips(project_id)
        return {"ps-round-trips": len(trips or [])}
    if gate == "local-edit":
        opened = store.open_project(project_id)
        node = next((n for n in opened["canvas"].get("nodes") or [] if n.get("id") == LE_NODE), {})
        return {"le-output-asset": bool(node.get("output_asset_id"))}
    # video
    ids = server_task_ids(store, project_id)
    return {"video-task-ids": ids, "video-task-count": len(ids),
            "video-task-ids-unique": len(set(ids)) == len(ids)}


def wait_rect_stable(browser: NativeBrowser, selector: str, timeout: float = 8.0,
                     settle_polls: int = 3, interval: float = 0.2) -> None:
    encoded = json.dumps(selector)
    rect_js = (
        "(function(){var el=document.querySelector(" + encoded + ");"
        "if(!el)return 'null';"
        "var r=el.getBoundingClientRect();"
        "return r.left+','+r.top+','+r.width+','+r.height;})()"
    )
    deadline = time.time() + timeout
    last = None
    stable = 0
    while time.time() < deadline:
        current = browser.evaluate(rect_js)
        if current and current != "null" and current == last:
            stable += 1
            if stable >= settle_polls:
                return
        else:
            stable = 0
        last = current
        time.sleep(interval)
    raise TimeoutError(f"element rect never settled: {selector}")


def native_click_retry(browser: NativeBrowser, selector: str,
                       relative: tuple[float, float] | None = None,
                       attempts: int = 3) -> dict:
    """native_click with a re-settle + retry loop.

    The page's main thread can be briefly blocked right after the pre-gate steps
    (debounced autosave serialization / 409 rebase cycles), which makes the runner's
    1s mousedown-observation window expire before the queued event is delivered.
    Re-measuring the target and retrying the click is pure input robustness; no
    assertion is touched."""
    last: RuntimeError | None = None
    for _ in range(attempts):
        try:
            if relative is not None:
                return browser.native_click(selector, relative=relative)
            return browser.native_click(selector)
        except RuntimeError as error:
            last = error
            text = str(error)
            if "did not observe" not in text and "did not settle" not in text:
                raise
            wait_rect_stable(browser, selector, timeout=5.0)
            time.sleep(0.6)
    assert last is not None
    # Failure diagnostics: dump target rect, hit chain at the click point, and the
    # page-side native event log so a "did not observe" failure is debuggable from
    # the run log alone.
    try:
        encoded = json.dumps(selector)
        dump = browser.evaluate(
            "(function(){var el=document.querySelector(" + encoded + ");"
            "var r=el?el.getBoundingClientRect():null;"
            "var x=r?r.left+r.width/2:-1,y=r?r.top+r.height/2:-1;"
            "var chain=[];var p=r?document.elementFromPoint(x,y):null;"
            "while(p&&chain.length<6){chain.push(p.tagName+'.'+String(p.className).slice(0,45));p=p.parentElement;}"
            "return JSON.stringify({rect:r?[r.left,r.top,r.width,r.height]:null,"
            "center:[Math.round(x),Math.round(y)],hitChain:chain,"
            "vp:[window.innerWidth,window.innerHeight],"
            "events:(window.__fireflyNativeEvents||[]).slice(-3)});})()"
        )
        print(f"[click-dump] {selector}: {dump}", file=sys.stderr)
    except Exception:
        pass
    raise last


def run_gate(run_id: str, artifacts: pathlib.Path, shots_dir: pathlib.Path,
             gate: str, mode: str) -> dict:
    result: dict = {
        "run": run_id, "gate": gate, "mode": mode, "passed": False,
        "steps": {}, "failure": None,
    }
    run_artifacts = artifacts / run_id
    run_shots = shots_dir / run_id
    run_artifacts.mkdir(parents=True, exist_ok=True)
    run_shots.mkdir(parents=True, exist_ok=True)
    browser: NativeBrowser | None = None
    logger: RequestLogger | None = None
    mock = MockImageServer()
    try:
        mock_base_url = mock.start()
        os.environ["API_PROVIDER_FIX189G_HTTP_KEY"] = "fix189g-fake-key"
        os.environ["API_PROVIDER_FIX189_HTTP_KEY"] = "fix189g-fake-key"
        with tempfile.TemporaryDirectory(prefix=f"firefly-189g-{gate}-{run_id}-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id = seed(store, gate)
            app = harness.load_production_app(root / "runtime-root", store)
            result["provider-file"] = write_test_provider(app, mock_base_url, with_video=(gate == "video"))
            logger = RequestLogger(app)
            with AsgiBrowserServer(logger) as server:
                browser = NativeBrowser.launch(find_browser())
                browser.set_environment(VIEWPORT)
                url = (
                    f"{server.base_url}/static/smart-canvas.html"
                    f"?id={urllib.parse.quote(project_id)}&project={urllib.parse.quote(project_id)}"
                    f"&project_mode=1"
                )
                browser.navigate(url)
                browser.wait_for(FIRST_WAIT[gate], timeout=30)
                time.sleep(1.0)
                browser.evaluate(INSTRUMENT_JS)
                browser.evaluate(FLUSH_SPY_JS)
                browser.evaluate(INJECT_STALE_JS)
                wait_state(browser, "contentDirty===false && canvasSyncInFlight===false", 25, "initial save settle")
                wait_state(browser, "document.getElementById('projectSaveState').dataset.state==='saved'",
                           10, "pill saved after load")
                result["steps"]["loaded-settled"] = True
                browser.capture_screenshot(run_shots / "00-opened.png")

                # ---- make the canvas dirty (proves the gate has work to flush)
                if gate == "video":
                    wait_rect_stable(browser, f'.image-node[data-id="{VID_NODE}"]')
                    browser.native_click(f'.image-node[data-id="{VID_NODE}"]')
                else:
                    wait_rect_stable(browser, f'.image-node[data-id="{DIRTY_NODE}"]')
                    browser.native_click(f'.image-node[data-id="{DIRTY_NODE}"]')
                time.sleep(0.8)
                wait_prompt_clickable(browser, shot=run_shots / "debug-prompt-blocked.png")
                wait_rect_stable(browser, "#promptInput")
                browser.native_click("#promptInput", relative=(0.02, 0.5))
                browser.native_press("End")
                browser.native_type(VIDEO_EDIT if gate == "video" else DIRTY_TEXT)
                wait_state(browser, "contentDirty===true", 10, "edit marks canvas dirty")
                result["steps"]["dirty"] = True

                # ---- deselect the dirty node: while selected its composer card floats over
                # the canvas and can obscure the gate target node's click area.
                # Exception: for the video gate the dirty node IS the video node, and
                # #runBtn lives inside its composer card — deselecting would hide the target.
                if gate != "video":
                    browser.evaluate("clearSelection(); render();")
                    time.sleep(0.5)

                # ---- arm the stale-base 409 rewrites, then fire the gate action immediately
                browser.evaluate(f"window.__fix189Inject.left = {ARM_STALE}")
                result["steps"]["armed"] = True
                if mode == "refresh":
                    # #197: block the in-session replay so the persisted mark is
                    # forced across the reload boundary (the stub is page-context
                    # only; the reloaded session gets the real dispatcher).
                    browser.evaluate(STUB_RESUME_JS)
                    result["steps"]["resume-stubbed"] = True
                browser.capture_screenshot(run_shots / "01-pre-gate.png")

                if gate == "conversation":
                    wait_rect_stable(browser, "[data-conversation-input]")
                    native_click_retry(browser, "[data-conversation-input]", relative=(0.5, 0.5))
                    time.sleep(0.3)
                    browser.native_type(CONV_TEXT)
                    wait_rect_stable(browser, "[data-conversation-send]")
                    native_click_retry(browser, "[data-conversation-send]", relative=(0.5, 0.5))
                elif gate == "photoshop":
                    wait_rect_stable(browser, f'.image-node[data-id="{PS_NODE}"]')
                    native_click_retry(browser, f'.image-node[data-id="{PS_NODE}"]')
                    browser.wait_for('document.querySelector("button[data-smart-node-action=\\"send-ps\\"]")', timeout=20)
                    wait_rect_stable(browser, 'button[data-smart-node-action="send-ps"]')
                    native_click_retry(browser, 'button[data-smart-node-action="send-ps"]')
                elif gate == "local-edit":
                    # No node-selection click: any click inside the card triggers
                    # select + full re-render, which replaces the element tree between
                    # mousedown and mouseup and breaks the runner's owner-integrity
                    # check. The LE control card is always rendered and the submit
                    # button is enabled without selection (verified by probe), so the
                    # native submit click below is the faithful user action.
                    browser.wait_for(
                        "(function(){var b=document.querySelector('[data-local-edit-node=\"g189-le\"] [data-local-edit-submit]');"
                        "if(!b)return false;var r=b.getBoundingClientRect();"
                        "if(r.width<=0||r.height<=0)return false;"
                        "if(b.hasAttribute('disabled'))return false;"
                        "var x=r.left+r.width*0.5,y=r.top+r.height*0.5;"
                        "var hit=document.elementFromPoint(x,y);return !!(hit&&(b===hit||b.contains(hit)));})()",
                        timeout=20)
                    wait_rect_stable(browser, "[data-local-edit-submit]")
                    native_click_retry(browser, "[data-local-edit-submit]")
                elif gate == "video":
                    wait_rect_stable(browser, "#runBtn")
                    native_click_retry(browser, "#runBtn")
                result["steps"]["gate-fired"] = True
                browser.capture_screenshot(run_shots / "02-post-gate.png")

                # ---- the gate save chain runs (3 x 409 -> budget exhausted -> gate fails)
                wait_state(browser, "canvasSyncInFlight===false", 40, "gate save flight cleared")
                time.sleep(GATE_SETTLE_S)
                cap1 = json.loads(browser.evaluate(capture_js(gate)))
                result["gate"] = cap1
                browser.capture_screenshot(run_shots / "03-gate-result.png")

                # ---- let the (re)saved state settle; in fixed mode the pending replay fires here
                saved_again = wait_state_soft(
                    browser, "document.getElementById('projectSaveState').dataset.state==='saved'", 40)
                result["steps"]["saved-again"] = bool(saved_again)
                post_seen = wait_state_soft(
                    browser,
                    "(function(){return (window.__fix189Net||[]).some(function(e){return (e.url||'').indexOf("
                    + json.dumps(GATE_URL_PART[gate]) + ")!==-1;});})()",
                    30) if mode in ("fixed", "refresh-sent") else False
                result["steps"]["post-arrived"] = bool(post_seen)
                time.sleep(FIXED_SETTLE_S if mode in ("fixed", "refresh-sent") else 3.0)
                cap2 = json.loads(browser.evaluate(capture_js(gate)))
                result["phase1"] = cap2
                result["server"] = list(logger.entries)
                result["gate-posts"] = gate_posts(logger, gate)
                result["readback"] = server_readback(store, project_id, gate)
                browser.capture_screenshot(run_shots / "04-after-settle.png")

                posts_phase1 = gate_posts(logger, gate)
                cap3 = cap2
                if mode in ("refresh", "refresh-sent"):
                    # ---- #197 phase 1 server-side mark check, then a NATIVE reload
                    # of the same page. refresh: exactly one persisted entry must
                    # exist (the in-session replay is stubbed, so nothing may have
                    # been sent). refresh-sent: the in-session replay already fired
                    # and the cleared mark must be persisted before the reload.
                    want_p1 = 1 if mode == "refresh" else 0
                    result["field-phase1"] = poll_server_field(store, project_id, want_p1)
                    browser.navigate(url)
                    browser.wait_for(FIRST_WAIT[gate], timeout=30)
                    time.sleep(1.0)
                    browser.evaluate(INSTRUMENT_JS)
                    browser.evaluate(FLUSH_SPY_JS)
                    wait_state(browser, "contentDirty===false && canvasSyncInFlight===false",
                               25, "reload settle")
                    wait_state(browser, "document.getElementById('projectSaveState').dataset.state==='saved'",
                               20, "pill saved after reload")
                    result["steps"]["reloaded"] = True
                    reload_seen = wait_state_soft(
                        browser,
                        "(function(){return (window.__fix189Net||[]).some(function(e){return (e.url||'').indexOf("
                        + json.dumps(GATE_URL_PART[gate]) + ")!==-1;});})()",
                        30) if mode == "refresh" else False
                    result["steps"]["reload-post-arrived"] = bool(reload_seen)
                    time.sleep(FIXED_SETTLE_S if mode == "refresh" else 3.0)
                    cap3 = json.loads(browser.evaluate(capture_js(gate)))
                    result["final"] = cap3
                    browser.capture_screenshot(run_shots / "05-after-reload.png")
                    result["field-phase2"] = poll_server_field(store, project_id, 0, timeout=40)
                    result["server"] = list(logger.entries)
                    result["gate-posts"] = gate_posts(logger, gate)
                    result["readback"] = server_readback(store, project_id, gate)

                # ---- verdict
                toasts_gate = [t.get("text", "") for t in cap1["toasts"]]
                toasts_final = [t.get("text", "") for t in cap2["toasts"]]
                gate_flush_failed = any(f.get("limitReached") for f in cap1["flush"])
                stop_seen = any(STOP_TOAST[gate] in t for t in toasts_final)
                pending_seen = any(PENDING_TOAST in t for t in (toasts_gate + toasts_final))
                pending1 = cap1["pending"]
                pending2 = cap2["pending"]
                pre_posts = len([e for e in cap1["net"] if e.get("method") == "POST"])
                post_posts = len([e for e in cap2["net"] if e.get("method") == "POST"])
                posts = result["gate-posts"]
                replayed = any(e.get("event") == "replayed" and e.get("kind") == KIND[gate]
                               for e in cap2["log"])
                dropped = any(e.get("event") == "dropped-node-gone" for e in cap2["log"])

                result["checks"] = {
                    "gate-flush-failed": gate_flush_failed,
                    "stop-toast": stop_seen,
                    "pending-toast": pending_seen,
                    "pending-queued-at-gate": (pending1.get("present") is True and pending1.get("size") == 1
                                               and any(e.get("kind") == KIND[gate] for e in pending1.get("entries", []))),
                    "pending-drained": (pending2.get("present") is True and pending2.get("size") == 0),
                    "pre-settle-posts": pre_posts,
                    "post-settle-posts": post_posts,
                    "server-post-count": len(posts),
                    "server-post-statuses": [p.get("status") for p in posts],
                    "replayed": replayed,
                    "dropped-node-gone": dropped,
                }
                rb = result["readback"]
                if gate == "conversation":
                    result["checks"]["conv-gate-text-count"] = rb.get("conv-gate-text-count")
                    result["checks"]["input-cleared"] = cap2["state"].get("input") in ("", None)
                elif gate == "photoshop":
                    result["checks"]["ps-round-trips"] = rb.get("ps-round-trips")
                elif gate == "local-edit":
                    result["checks"]["le-busy-cleared"] = cap2["state"].get("busy") is False
                    result["checks"]["le-output"] = rb.get("le-output-asset")
                else:
                    result["checks"]["video-task-count"] = rb.get("video-task-count")
                    result["checks"]["video-task-ids-unique"] = rb.get("video-task-ids-unique")
                    result["checks"]["video-task-ids"] = rb.get("video-task-ids")

                if mode == "repro":
                    result["passed"] = bool(
                        stop_seen
                        and gate_flush_failed
                        and pre_posts == 0
                        and post_posts == 0
                        and len(posts) == 0
                        and (pending1.get("present") is False or pending1.get("size") == 0)
                        and (
                            # Measured pre-fix behaviour (git checkout 1208c0b -- static/js/*,
                            # RED-conversation run01..03): stop-toast=True, server-post-count=0,
                            # input-cleared=True. The typed text is LOST, not left in the box,
                            # so the old bug is worse than "message not sent".
                            (gate == "conversation" and cap2["state"].get("input") in ("", None))
                            or gate != "conversation"
                        )
                    )
                elif mode == "refresh":
                    # #197 acceptance 1: gate fails -> the persisted mark survives a
                    # reload -> the request is sent exactly once (across BOTH sessions)
                    # and the server ends with exactly one task/artifact.
                    field1 = result["field-phase1"]
                    field1_ok = (
                        len(field1) == 1
                        and any(e.get("kind") == KIND[gate] and e.get("nodeId") == NODE_ID_OF(gate)
                                for e in field1)
                    )
                    net_p1 = len([e for e in cap2["net"] if e.get("method") == "POST"])
                    phase1_ok = (
                        gate_flush_failed and not stop_seen and pending_seen
                        and result["checks"]["pending-queued-at-gate"]
                        and pre_posts == 0 and net_p1 == 0
                        and len(posts_phase1) == 0 and field1_ok
                    )
                    net_p2 = len([e for e in cap3["net"] if e.get("method") == "POST"])
                    replayed2 = any(e.get("event") == "replayed" and e.get("kind") == KIND[gate]
                                    for e in cap3["log"])
                    dropped2 = any(e.get("event") == "dropped-node-gone" for e in cap3["log"])
                    pending3 = cap3["pending"]
                    phase2_ok = (
                        net_p2 == 1 and len(posts) == 1 and replayed2 and not dropped2
                        and (pending3.get("present") is True and pending3.get("size") == 0)
                        and len(result["field-phase2"]) == 0
                    )
                    rb = result["readback"]
                    if gate == "conversation":
                        phase2_ok = phase2_ok and rb.get("conv-gate-text-count") == 1 \
                            and "failed" not in (rb.get("conv-gate-statuses") or []) \
                            and (rb.get("conv-assistant-replies") or 0) >= 1
                    elif gate == "photoshop":
                        phase2_ok = phase2_ok and rb.get("ps-round-trips") == 1 \
                            and posts[0].get("status") == 200
                    elif gate == "local-edit":
                        phase2_ok = phase2_ok and posts[0].get("status") == 200 \
                            and rb.get("le-output-asset")
                    else:
                        ids = rb.get("video-task-ids") or []
                        phase2_ok = phase2_ok and rb.get("video-task-ids-unique") is True \
                            and len(set(ids)) <= 1 and len(ids) >= 1
                    result["checks"]["phase1"] = phase1_ok
                    result["checks"]["phase2"] = phase2_ok
                    result["checks"]["server-field-phase1"] = field1
                    result["checks"]["server-field-phase2"] = result["field-phase2"]
                    result["checks"]["session1-net-posts"] = net_p1
                    result["checks"]["session2-net-posts"] = net_p2
                    result["checks"]["total-server-posts"] = len(posts)
                    result["passed"] = phase1_ok and phase2_ok
                elif mode == "refresh-sent":
                    # #197 acceptance 2 (duplicate prevention): the request was ALREADY
                    # sent in session 1 and the cleared mark is persisted; a reload
                    # must NOT send a second time and the server task count stays 1.
                    field1 = result["field-phase1"]
                    net_p1 = len([e for e in cap2["net"] if e.get("method") == "POST"])
                    phase1_ok = (
                        gate_flush_failed and not stop_seen and pending_seen
                        and result["checks"]["pending-queued-at-gate"]
                        and pre_posts == 0 and net_p1 == 1
                        and len(posts_phase1) == 1 and replayed and not dropped
                        and len(field1) == 0
                    )
                    net_p2 = len([e for e in cap3["net"] if e.get("method") == "POST"])
                    replayed2 = any(e.get("event") == "replayed" and e.get("kind") == KIND[gate]
                                    for e in cap3["log"])
                    phase2_ok = (
                        net_p2 == 0 and len(posts) == 1 and not replayed2
                        and len(result["field-phase2"]) == 0
                    )
                    rb = result["readback"]
                    if gate == "conversation":
                        phase2_ok = phase2_ok and rb.get("conv-gate-text-count") == 1
                    elif gate == "photoshop":
                        phase2_ok = phase2_ok and rb.get("ps-round-trips") == 1
                    elif gate == "local-edit":
                        phase2_ok = phase2_ok and rb.get("le-output-asset")
                    else:
                        ids = rb.get("video-task-ids") or []
                        phase2_ok = phase2_ok and rb.get("video-task-ids-unique") is True \
                            and len(set(ids)) <= 1 and len(ids) >= 1
                    result["checks"]["phase1"] = phase1_ok
                    result["checks"]["phase2"] = phase2_ok
                    result["checks"]["server-field-phase1"] = field1
                    result["checks"]["server-field-phase2"] = result["field-phase2"]
                    result["checks"]["session1-net-posts"] = net_p1
                    result["checks"]["session2-net-posts"] = net_p2
                    result["checks"]["total-server-posts"] = len(posts)
                    result["passed"] = phase1_ok and phase2_ok
                else:
                    common_ok = (
                        gate_flush_failed
                        and not stop_seen
                        and pending_seen
                        and result["checks"]["pending-queued-at-gate"]
                        and result["checks"]["pending-drained"]
                        and pre_posts == 0
                        and post_posts == 1
                        and len(posts) == 1
                        and replayed
                        and not dropped
                    )
                    if gate == "conversation":
                        # The replay must land a task that actually runs, not merely a
                        # request that was accepted once: status must not be "failed"
                        # and the adapter must have produced an assistant reply.
                        result["passed"] = common_ok and rb.get("conv-gate-text-count") == 1 \
                            and cap2["state"].get("input") in ("", None) \
                            and "failed" not in (rb.get("conv-gate-statuses") or []) \
                            and (rb.get("conv-assistant-replies") or 0) >= 1
                    elif gate == "photoshop":
                        result["passed"] = common_ok and rb.get("ps-round-trips") == 1 \
                            and posts[0].get("status") == 200
                    elif gate == "local-edit":
                        # The seed selection must satisfy the endpoint's validate_rect
                        # (local_edit.py:47): inside the 480x320 source image AND match
                        # aspect_ratio 1:1 within 0.02. [12,18,112,118] is a 100x100
                        # square. With a valid seed the offline adapter composites
                        # deterministically, so 200 + output asset is the green bar.
                        result["passed"] = common_ok and cap2["state"].get("busy") is False \
                            and posts[0].get("status") == 200 and rb.get("le-output-asset")
                    else:
                        result["passed"] = common_ok and rb.get("video-task-ids-unique") is True
    except Exception as error:  # noqa: BLE001 — journey evidence must capture any failure
        result["failure"] = {"message": str(error), "traceback": traceback.format_exc()}
        print(f"FAILED [{run_id}/{gate}]: {error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        if browser is not None:
            try:
                write_jsonl(run_artifacts / "operations.jsonl", list(browser.operations))
                write_json(run_artifacts / "console-errors.json", list(browser.console_errors))
            finally:
                browser.close()
        mock.stop()
        write_jsonl(run_artifacts / "server-log.jsonl", result.get("server") or [])
        write_jsonl(run_artifacts / "gate-posts.jsonl", result.get("gate-posts") or [])
        write_json(run_artifacts / "manifest.json",
                   {k: v for k, v in result.items() if k not in ("server",)})
        summary = {"run": run_id, "gate": gate, "mode": mode, "passed": result["passed"],
                   "checks": result.get("checks"),
                   "failure": (result.get("failure") or {}).get("message")}
        print(json.dumps(summary, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", choices=["conversation", "photoshop", "local-edit", "video"], required=True)
    parser.add_argument("--mode", choices=["repro", "fixed", "refresh", "refresh-sent"], default="repro")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    parser.add_argument("--shots-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    shots_dir = args.shots_dir.resolve()
    results = [
        run_gate(f"run{i:02d}", artifacts, shots_dir, args.gate, args.mode)
        for i in range(1, args.runs + 1)
    ]
    passed = [r["passed"] for r in results]
    status = "passed" if results and all(passed) else "failed"
    print(json.dumps({
        "status": status, "gate": args.gate, "mode": args.mode,
        "runs": len(results), "passed": f"{sum(passed)}/{len(passed)}",
        "results": [
            {"run": r.get("run"), "passed": r.get("passed"),
             "checks": r.get("checks"),
             "failure": (r.get("failure") or {}).get("message")}
            for r in results
        ],
    }, ensure_ascii=False))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
