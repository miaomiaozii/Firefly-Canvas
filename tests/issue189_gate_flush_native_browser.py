"""Native-browser repro + fix verification for issue #189: clicking "generate"
while the pre-generation canvas flush fails (3 consecutive 409s exhaust the
2-retry conflict budget of saveCoreCanvasIncrementally) currently cancels the
user's generation: the durable submission marker is cleared and the user sees
"画布尚未保存，已停止生图".

Scenario (per the issue): edit the prompt -> wait until the save state is back
to "saved" -> arm N stale-base rewrites on the next canvas-save PATCHes (the
real server then returns authentic 409s with the latest canvas) -> click
generate IMMEDIATELY.  The gate's save chain 409s three times and the first
save attempt's flush hits conflictLimitReached, but since #190
saveProjectCanvasUntilSettled() retries (maxAttempts=4) and recovers on later
attempts (3 < 12), so the settle succeeds and the generation entry point takes
the #189 pending path instead of cancelling.

Modes:
  repro   - post-#189 characterization (RED->GREEN history: pre-#190 this mode
            asserted the buggy "已停止生图" behavior; since #190 the armed 409s
            are absorbed by the retry budget, and since #197's clear-on-send
            protocol the old stop toast and marker-lingering timing are both
            unreachable — #216). The mode now asserts the same post-#189
            contract as `fixed`. Passing here == the product keeps the request.
  fixed   - asserts the DESIRED behavior (user decision m03364: the request
            stays pending and generation starts automatically once the save
            settles): no "已停止生图" toast, the marker survives the failed
            flush (carried by the next durable save), the task is registered
            exactly once, the node enters the generating state.
            Red before the fix, green after.
  natural - observational control without injection (single tab, no concurrent
            writer): the gate save normally 200s straight away; reports how
            often the stopped toast appears (expected 0/N).

Evidence contract: per-run subdirectories under --artifacts-dir (manifest.json,
net-log.jsonl, state-samples.jsonl, server-log.jsonl, operations.jsonl,
console-errors.json) and --shots-dir (screenshots).  No real provider is ever
contacted: a test provider pointing at a black-hole base URL is written to the
app's API_PROVIDERS_FILE so that POST /image-generation-tasks routes and
returns 202 (task registration is what this issue covers; the background
provider call failing later is out of scope).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import traceback
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser  # noqa: E402

VIEWPORT = (1424, 900)
NODE_ID = "img-189"
MODEL = "fix189-model"
EDIT_A = "[edit-189-a]"
# #216: DERIVED, not a magic number. INJECT_STALE must EXHAUST one flush's
# rebase budget (1 + maxConflictRetries=2 = 3 save PATCHes) so the gate's first
# save attempt reports conflictLimitReached, while staying BELOW the whole
# settle budget maxAttempts=4 * (1 + maxConflictRetries=2) = 12 so the settle
# still recovers and the #189 pending path (not the old stop toast) is
# exercised. Both product constants live in static/js/smart-canvas.js
# (saveProjectCanvasUntilSettled maxAttempts=4 at :6619, flush
# maxConflictRetries=2 at :6662); tests/test_issue189gates_arm_stale_guard.py
# fails the suite if either side drifts.
INJECT_STALE = 3
STOVED_TOAST_TEXT = "已停止生图"
GATE_SETTLE_S = 2.0        # after the gate save flight clears, before capturing evidence
FIXED_SETTLE_S = 5.0       # after the final readback window
STOPPED_MARKER = "smart.canvasUnsavedImageStopped"
MOCK_IMAGE_DELAY_S = 20    # the mock provider keeps the task in processing this long
# 1x1 red PNG
MOCK_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

INSTRUMENT_JS = r"""
(function(){
  if(window.__fix189Installed) return 'already';
  window.__fix189Installed = true;
  window.__fix189Net = [];
  window.__fix189Toasts = [];
  const NODE_ID = 'img-189';
  const origFetch = window.fetch;
  window.fetch = function(input, init){
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    const method = (init && init.method) || ((typeof input !== 'string' && input && input.method) || 'GET');
    let base = null; let hasMarker = null;
    try {
      if(init && init.body){
        const b = JSON.parse(init.body);
        base = b.base_revision;
        hasMarker = String(init.body).indexOf('generationSubmissions') !== -1;
      }
    } catch(_){}
    const t0 = performance.now();
    return origFetch.apply(this, arguments).then(function(res){
      const t1 = performance.now();
      window.__fix189Net.push({
        t: Math.round(t0), url: url.replace(location.origin, ''), method: method,
        base: base, hasMarker: hasMarker, status: res.status, ms: Math.round(t1 - t0)
      });
      return res;
    });
  };
  setInterval(function(){
    try{
      [].slice.call(document.querySelectorAll('.toast')).forEach(function(t){
        if(t.classList.contains('show')){
          const text = (t.textContent || '').trim();
          const last = window.__fix189Toasts[window.__fix189Toasts.length - 1];
          if(!text) return;
          if(!last || last.text !== text || performance.now() - last.t > 2500){
            window.__fix189Toasts.push({t: Math.round(performance.now()), text: text.slice(0, 240)});
          }
        }
      });
      if(window.__fix189Toasts.length > 400) window.__fix189Toasts.splice(0, 100);
    }catch(_){}
  }, 60);
  window.__fix189Samples = [];
  setInterval(function(){
    try {
      const el = document.getElementById('projectSaveState');
      const node = (window.canvasState && canvasState.nodes || []).find(n => n.id === NODE_ID);
      window.__fix189Samples.push({
        t: Math.round(performance.now()),
        sync: canvasSyncInFlight,
        dirty: contentDirty,
        deb: !!debouncedSaveTimer,
        state: el ? (el.dataset.state || null) : null,
        marker: node ? (node.generationSubmissions || []).length : -1,
        pending: node ? (node.pendingTasks || []).length : -1,
        pendingCount: node ? (node.pending || 0) : -1,
        running: node ? !!node.running : -1
      });
      if(window.__fix189Samples.length > 4000) window.__fix189Samples.splice(0, 1000);
    } catch(e){}
  }, 120);
  return 'installed';
})()
"""


FLUSH_SPY_JS = r"""
(function(){
  if(window.__fix189FlushSpyInstalled) return 'already';
  window.__fix189FlushSpyInstalled = true;
  window.__fix189FlushResults = [];
  const client = window.FireflyProjectCanvasMutations;
  if(!client) return 'no-client';
  const orig = client.flushProjectCanvasMutations;
  client.flushProjectCanvasMutations = function(options){
    return Promise.resolve(orig.call(this, options)).then(function(result){
      window.__fix189FlushResults.push({
        t: Math.round(performance.now()),
        saved: result.saved,
        conflictRetries: result.conflictRetries,
        limitReached: !!result.conflictLimitReached,
        nMut: (result.mutations||[]).length
      });
      return result;
    });
  };
  return 'installed';
})()
"""


INJECT_STALE_JS = r"""
(function(){
  if(window.__fix189InjectInstalled) return 'already';
  window.__fix189InjectInstalled = true;
  window.__fix189Inject = {left: 0, hits: 0, log: []};
  const orig = window.fetch;
  window.fetch = function(input, init){
    try {
      const url = typeof input === 'string' ? input : ((input && input.url) || '');
      const isSavePatch = url.indexOf('project-library') !== -1 &&
        url.split('?')[0].split('/').pop() === 'canvas' &&
        !!(init && init.method === 'PATCH') && !!(init && init.body);
      if(window.__fix189Inject.left > 0 && isSavePatch){
        const body = JSON.parse(init.body);
        if(body && typeof body.base_revision === 'number' && body.base_revision > 0){
          const stale = body.base_revision - 1;
          window.__fix189Inject.left -= 1;
          window.__fix189Inject.hits += 1;
          window.__fix189Inject.log.push({t: Math.round(performance.now()), base: body.base_revision, sent: stale});
          init = Object.assign({}, init, {body: JSON.stringify(Object.assign({}, body, {base_revision: stale}))});
        }
      }
    } catch(_){}
    return orig.apply(this, arguments);
  };
  return 'installed';
})()
"""


def seed(store: ProjectStore) -> str:
    project = store.create_project("Issue 189 Pre-generation Save Gate")
    project_id = project["id"]
    store.apply_mutations(project_id, [
        {"op": "upsert_node", "node": {
            "id": NODE_ID, "type": "smart-image", "name": "Target",
            "x": 240, "y": 120, "w": 360, "h": 300,
            "runPrompt": "repro-189 base prompt",
            "runSettings": {
                "engine": "api", "apiKind": "image", "model": MODEL,
                "ratio": "square", "resolution": "1k", "count": 1,
            },
        }},
        {"op": "set_canvas_meta", "title": "Issue 189"},
    ], base_revision=0)
    store.flush(project_id)
    return project_id


def write_test_provider(app, base_url: str) -> str:
    """Write a provider pointing at the local mock image server so that
    POST /image-generation-tasks routes, returns 202 (registration), and the
    background task completes against the mock (keeps the node visibly in the
    generating state for the whole observation window)."""
    module = app.state.issue13_module
    path = pathlib.Path(module.API_PROVIDERS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    provider = {
        "id": "fix189-http", "name": "fix189-http", "enabled": True,
        "protocol": "openai", "base_url": base_url,
        "api_key": "fix189-fake-key",
        "image_request_mode": "openai",
        "image_generation_endpoint": "", "image_edit_endpoint": "",
        "image_models": [MODEL], "chat_models": [], "video_models": [],
        "ms_loras": [], "ms_defaults_version": 0,
    }
    path.write_text(json.dumps([provider], ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def server_task_terminal(store: ProjectStore, project_id: str):
    """(task_id, status) of the first durable history record, or (None, '')."""
    opened = store.open_project(project_id)
    for record in opened["canvas"].get("history") or []:
        tid = record.get("task_id")
        if tid:
            return str(tid), str(record.get("status") or "")
    return None, ""


def server_node(store: ProjectStore, project_id: str) -> dict:
    opened = store.open_project(project_id)
    for node in opened["canvas"].get("nodes") or []:
        if node.get("id") == NODE_ID:
            return node
    return {}


def server_marker(store: ProjectStore, project_id: str) -> list[dict]:
    return server_node(store, project_id).get("generationSubmissions") or []


def server_task_ids(store: ProjectStore, project_id: str) -> list[str]:
    """Every durable task id on the canvas (node.pendingTasks + history)."""
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


def server_prompt(store: ProjectStore, project_id: str) -> str:
    node = server_node(store, project_id)
    return str(node.get("runPrompt") or "") + "\n" + str(node.get("promptDraftText") or "")


class MockImageServer:
    """Minimal OpenAI-compatible images endpoint: sleeps MOCK_IMAGE_DELAY_S,
    then returns a 1x1 PNG as b64_json (parsed by app/core.py:1899)."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive: the server-side httpx client pools connections

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                time.sleep(MOCK_IMAGE_DELAY_S)
                body = json.dumps({"created": int(time.time()), "data": [{"b64_json": MOCK_PNG_B64, "revised": MOCK_PNG_B64}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = __import__("threading").Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass


class RequestLogger:
    """ASGI middleware that records /api/ request method/path/status/elapsed."""

    API_RE = re.compile(r"/api/(canvases|project-library)/")

    def __init__(self, app):
        self.app = app
        self.entries: list[dict] = []

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        start = time.time()
        status: dict = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if self.API_RE.search(path):
                self.entries.append({
                    "t": round(time.time() * 1000),
                    "method": scope.get("method"),
                    "path": path,
                    "status": status.get("status"),
                    "ms": round((time.time() - start) * 1000),
                })


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")


def write_json(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def state(browser: NativeBrowser, expression: str):
    return browser.evaluate(expression)


def wait_state(browser: NativeBrowser, expression: str, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if browser.evaluate(expression):
            return
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {what}: {expression}")


def wait_state_soft(browser: NativeBrowser, expression: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if browser.evaluate(expression):
            return True
        time.sleep(0.2)
    return False


PROMPT_CLICKABLE_JS = """(function(){
  var el=document.getElementById('promptInput');
  if(!el)return false;
  var r=el.getBoundingClientRect();
  if(r.width<=0||r.height<=0)return false;
  var x=r.left+r.width*0.02, y=r.top+r.height*0.5;
  var hit=document.elementFromPoint(x, y);
  return !!(hit && (el===hit || el.contains(hit)));
})()"""


def wait_prompt_clickable(browser: NativeBrowser, timeout: float = 20, shot: pathlib.Path | None = None) -> None:
    try:
        wait_state(browser, PROMPT_CLICKABLE_JS, timeout, "prompt input unobscured (toast/overlay cleared)")
    except TimeoutError:
        try:
            info = browser.evaluate("""(function(){
              var el=document.getElementById('promptInput');
              var r=el.getBoundingClientRect();
              var x=r.left+r.width*0.02, y=r.top+r.height*0.5;
              var hit=document.elementFromPoint(x, y);
              var out={rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],point:[Math.round(x),Math.round(y)],hit:hit?hit.tagName+'#'+hit.id+'.'+String(hit.className).slice(0,120):'null'};
              out.toasts=[].slice.call(document.querySelectorAll('.toast')).map(function(t){return {show:t.classList.contains('show'),text:t.textContent};});
              return out;
            })()""")
            print("PROMPT-BLOCKED-DEBUG:", json.dumps(info, ensure_ascii=False), file=sys.stderr)
            if shot is not None:
                browser.capture_screenshot(shot)
        except Exception as dbg_error:  # noqa: BLE001
            print("PROMPT-BLOCKED-DEBUG failed:", dbg_error, file=sys.stderr)
        raise


def read_toasts(browser: NativeBrowser) -> list[dict]:
    raw = state(browser, "JSON.stringify(window.__fix189Toasts || [])")
    return json.loads(raw) if isinstance(raw, str) and raw else []


def read_flush_spy(browser: NativeBrowser) -> list[dict]:
    raw = state(browser, "JSON.stringify(window.__fix189FlushResults || [])")
    return json.loads(raw) if isinstance(raw, str) and raw else []


def read_inject_state(browser: NativeBrowser) -> dict | None:
    raw = state(browser, "JSON.stringify(window.__fix189Inject || null)")
    return json.loads(raw) if isinstance(raw, str) and raw and raw != "null" else None


def read_net(browser: NativeBrowser) -> list[dict]:
    raw = state(browser, "JSON.stringify(window.__fix189Net)")
    return json.loads(raw) if isinstance(raw, str) and raw else []


def read_samples(browser: NativeBrowser) -> list[dict]:
    raw = state(browser, "JSON.stringify(window.__fix189Samples)")
    return json.loads(raw) if isinstance(raw, str) and raw else []


PILL_JS = "(function(){var el=document.getElementById('projectSaveState');return {state:el?el.dataset.state:null,text:el?el.textContent:''};})()"


PAGE_NODE_STATE_JS = (
    "(function(){var n=(window.canvasState&&canvasState.nodes||[]).find(function(x){return x.id===" + json.dumps(NODE_ID) +
    "});if(!n)return {found:false};"
    "return {found:true,marker:(n.generationSubmissions||[]).length,"
    "pending:(n.pendingTasks||[]).length,pendingCount:(n.pending||0),running:!!n.running,"
    "taskIds:(n.pendingTasks||[]).map(function(t){return t.taskId;})};})()"
)


CLEAR_SPY_JS = r"""
(function(){
  if(window.__fix189ClearSpyInstalled) return 'already';
  window.__fix189ClearSpyInstalled = true;
  window.__fix189ClearLog = [];
  const orig = clearSmartGenerationSubmission;
  if(!orig) return 'no-fn';
  clearSmartGenerationSubmission = function(node, key){
    const entry = {
      t: Math.round(performance.now()), key: String(key || ''), nodeId: node && node.id,
      before: (typeof smartGenerationSubmissions === 'function' ? smartGenerationSubmissions(node) : (node && node.generationSubmissions) || []).map(s => s.idempotencyKey),
      sameObjectAsCanvas: (window.canvasState && canvasState.nodes || []).some(n => n === node)
    };
    window.__fix189ClearLog.push(entry);
    const r = orig(node, key);
    const last = window.__fix189ClearLog[window.__fix189ClearLog.length - 1];
    last.after = (typeof smartGenerationSubmissions === 'function' ? smartGenerationSubmissions(node) : (node && node.generationSubmissions) || []).map(s => s.idempotencyKey);
    const canvasNode = (window.canvasState && canvasState.nodes || []).find(n => n && n.id === (node && node.id));
    last.canvasAfter = canvasNode ? (canvasNode.generationSubmissions || []).length : null;
    return r;
  };
  return 'installed';
})()
"""


def run_journey(run_id: str, artifacts: pathlib.Path, shots_dir: pathlib.Path, *, mode: str, inject_stale: int) -> dict:
    run_artifacts = artifacts / run_id
    run_shots = shots_dir / run_id
    run_artifacts.mkdir(parents=True, exist_ok=True)
    run_shots.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "run": run_id,
        "mode": mode,
        "inject_stale": inject_stale,
        "passed": False,
        "steps": {},
        "failure": None,
        "net": [],
        "server": [],
        "samples": [],
    }
    browser: NativeBrowser | None = None
    store: ProjectStore | None = None
    logger: RequestLogger | None = None
    mock = MockImageServer()
    try:
        mock_base_url = mock.start()
        # The server resolves a provider's API key from the environment (app/core.py:443
        # provider_key_env -> API_PROVIDER_<ID>_KEY), not from the provider file; the mock
        # server ignores auth entirely, so a fake key is enough to get past api_headers().
        os.environ["API_PROVIDER_FIX189_HTTP_KEY"] = "fix189-fake-key"
        with tempfile.TemporaryDirectory(prefix=f"firefly-189-{run_id}-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id = seed(store)
            app = harness.load_production_app(root / "runtime-root", store)
            result["provider-file"] = write_test_provider(app, mock_base_url)
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
                browser.wait_for(f'document.querySelector(".image-node[data-id=\\"{NODE_ID}\\"]")', timeout=30)
                time.sleep(1.0)
                browser.evaluate(INSTRUMENT_JS)
                browser.evaluate(FLUSH_SPY_JS)
                browser.evaluate(CLEAR_SPY_JS)
                if inject_stale:
                    browser.evaluate(INJECT_STALE_JS)
                wait_state(browser, "contentDirty===false && canvasSyncInFlight===false", 25, "initial save settle")
                wait_state(browser, "document.getElementById('projectSaveState').dataset.state==='saved'", 10, "pill saved after load")
                result["steps"]["loaded-settled"] = True
                browser.capture_screenshot(run_shots / "00-opened.png")

                # --- step 1: prompt edit, then wait for the save state to return to "saved"
                browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                time.sleep(0.8)
                wait_prompt_clickable(browser, shot=run_shots / "debug-prompt-blocked-1.png")
                browser.native_click("#promptInput", relative=(0.02, 0.5))
                browser.native_press("End")
                browser.native_type(EDIT_A)
                wait_state(browser, "contentDirty===true", 10, "edit marks canvas dirty")
                result["steps"]["edit-a"] = True
                browser.capture_screenshot(run_shots / "01-edit-a.png")
                wait_state(browser, "contentDirty===false && canvasSyncInFlight===false", 15, "debounced autosave settled")
                wait_state(browser, "document.getElementById('projectSaveState').dataset.state==='saved'", 10, "pill back to saved")
                result["steps"]["save-settled"] = True

                # --- step 2: arm the stale-base rewrites, then click generate IMMEDIATELY
                if inject_stale:
                    state(browser, f"window.__fix189Inject.left = {int(inject_stale)}")
                    result["steps"]["inject-armed"] = True
                browser.capture_screenshot(run_shots / "02-pre-click.png")
                browser.native_click("#runBtn")
                result["steps"]["generate-clicked"] = True
                browser.capture_screenshot(run_shots / "03-post-click.png")

                # --- step 3: the gate save chain runs (3 x 409 -> budget exhausted)
                wait_state(browser, "canvasSyncInFlight===false", 40, "gate save flight cleared")
                time.sleep(GATE_SETTLE_S)
                toasts_gate = read_toasts(browser)
                net_gate = read_net(browser)
                flushes_gate = read_flush_spy(browser)
                page_node_gate = state(browser, PAGE_NODE_STATE_JS)
                result["gate"] = {
                    "toasts": toasts_gate,
                    "stopped_toast": any(STOVED_TOAST_TEXT in t.get("text", "") for t in toasts_gate),
                    "flush-spy": flushes_gate,
                    "limit_reached": any(f.get("limitReached") for f in flushes_gate),
                    "gate_409s": [n for n in net_gate if n.get("method") == "PATCH" and n.get("url", "").rstrip("/").endswith("/canvas") and n.get("status") == 409],
                    "page-node": page_node_gate,
                    "server-marker": server_marker(store, project_id),
                    "pill": state(browser, PILL_JS),
                }
                result["gate"]["gate_409_count"] = len(result["gate"]["gate_409s"])
                gate_t = max([n.get("t", 0) for n in result["gate"]["gate_409s"]] +
                             [f.get("t", 0) for f in flushes_gate if f.get("limitReached")], default=0)
                result["gate"]["gate-t"] = gate_t
                browser.capture_screenshot(run_shots / "04-gate-result.png")

                # --- step 4: let the (re)saved state settle; in repro/fixed mode
                # wait for the auto-resume (#216: repro asserts the same
                # post-#189 contract as fixed, so it needs the same waits)
                if mode in ("fixed", "repro"):
                    saved_again = wait_state_soft(browser, "document.getElementById('projectSaveState').dataset.state==='saved'", 40)
                    result["fixed"] = {"saved-again": saved_again}
                    registered = wait_state_soft(browser,
                        "(function(){var n=(window.canvasState&&canvasState.nodes||[]).find(function(x){return x.id===" + json.dumps(NODE_ID) +
                        "});return n&&(n.pendingTasks||[]).length>=1;})()", 40)
                    result["fixed"]["task-registered-page"] = registered
                    time.sleep(FIXED_SETTLE_S)
                else:
                    time.sleep(FIXED_SETTLE_S)

                toasts_final = read_toasts(browser)
                net_final = read_net(browser)
                flushes_final = read_flush_spy(browser)
                page_node_final = state(browser, PAGE_NODE_STATE_JS)
                result["gate"]["marker_carried_after_gate"] = any(
                    n.get("method") == "PATCH" and n.get("hasMarker") and n.get("t", 0) > result["gate"]["gate-t"]
                    for n in net_final
                )
                task_ids = server_task_ids(store, project_id)
                post_entries = [e for e in (logger.entries if logger else [])
                                if e.get("method") == "POST" and e.get("path", "").rstrip("/").endswith("/image-generation-tasks")]
                result["final"] = {
                    "toasts": toasts_final,
                    "stopped_toast": any(STOVED_TOAST_TEXT in t.get("text", "") for t in toasts_final),
                    "page-node": page_node_final,
                    "server-marker": server_marker(store, project_id),
                    "task-ids": task_ids,
                    "task-count": len(task_ids),
                    "task-ids-unique": len(set(task_ids)) == len(task_ids),
                    "post-entries": post_entries,
                    "post-202": [e for e in post_entries if e.get("status") == 202],
                    "edit-a-persisted": EDIT_A in server_prompt(store, project_id),
                    "pill": state(browser, PILL_JS),
                }
                if mode in ("fixed", "repro"):
                    terminal_id, terminal_status = "", ""
                    terminal_deadline = time.monotonic() + 50
                    while time.monotonic() < terminal_deadline:
                        terminal_id, terminal_status = server_task_terminal(store, project_id)
                        if terminal_status and terminal_status not in ("pending", "processing"):
                            break
                        time.sleep(0.5)
                    result["final"]["task-terminal"] = {"task_id": terminal_id, "status": terminal_status}
                browser.capture_screenshot(run_shots / "05-after-settle.png")

                result["net"] = net_final
                result["samples"] = read_samples(browser)
                result["flush-spy"] = flushes_final
                raw_clear = state(browser, "JSON.stringify(window.__fix189ClearLog || [])")
                result["clear-spy"] = json.loads(raw_clear) if isinstance(raw_clear, str) and raw_clear else []
                result["inject-state"] = read_inject_state(browser) if inject_stale else None

                # --- verdict
                if mode in ("repro", "fixed"):
                    # #216: repro and fixed assert the SAME post-#189 contract.
                    # Pre-#190, repro asserted the buggy stop toast; since #190 the
                    # armed 409s are absorbed by the retry budget, so that path is
                    # unreachable. And the old `page_node_gate.marker == 1` check
                    # (pre-#197 timing) is replaced by a marker/task consistency
                    # invariant, because #197's clear-on-send protocol (67e458f)
                    # makes the replay consume the marker BEFORE the gate evidence
                    # capture (measured on 98eb32d: clears at t=9466/10601 vs
                    # gate-t=7497 + 2s settle). The #189 guarantee that survives
                    # the timing change: a cleared marker must always be
                    # accompanied by a registered task (the pre-#189 bug cleared
                    # the marker AND lost the task). A lingering marker is
                    # legitimate (consumed by the next durable save; the server
                    # dedupes replays by idempotency_key in
                    # reserve_project_image_task, so it cannot double-generate).
                    marker_final = page_node_final.get("marker")
                    marker_consistent = (
                        marker_final == 1 or (marker_final == 0 and bool(task_ids))
                    )
                    result["passed"] = bool(
                        not result["final"]["stopped_toast"]
                        and result["gate"]["limit_reached"]
                        and result["gate"]["gate_409_count"] >= 3
                        and marker_consistent
                        and result["gate"]["marker_carried_after_gate"]
                        and result["fixed"]["task-registered-page"]
                        and result["final"]["task-count"] == 1
                        and result["final"]["task-ids-unique"]
                        and bool(result["final"]["post-202"])
                        and result["final"]["edit-a-persisted"]
                    )
                else:
                    result["passed"] = True  # observational
    except Exception as error:  # noqa: BLE001 — journey evidence must capture any failure
        result["failure"] = {"message": str(error), "traceback": traceback.format_exc()}
        print(f"FAILED [{run_id}]: {error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        if browser is not None:
            try:
                if logger is not None:
                    result["server"] = list(logger.entries)
            finally:
                try:
                    write_jsonl(run_artifacts / "operations.jsonl", list(browser.operations))
                    write_json(run_artifacts / "console-errors.json", list(browser.console_errors))
                finally:
                    browser.close()
        mock.stop()
        write_jsonl(run_artifacts / "net-log.jsonl", result["net"])
        write_jsonl(run_artifacts / "state-samples.jsonl", result["samples"])
        write_jsonl(run_artifacts / "server-log.jsonl", result["server"])
        write_json(run_artifacts / "manifest.json", {k: v for k, v in result.items() if k not in ("net", "server", "samples")})
        summary = {k: result.get(k) for k in ("run", "mode", "passed", "failure")}
        if "gate" in result:
            summary["stopped_toast"] = result["gate"]["stopped_toast"]
            summary["limit_reached"] = result["gate"]["limit_reached"]
            summary["gate_409_count"] = result["gate"]["gate_409_count"]
            summary["marker_carried_after_gate"] = result["gate"]["marker_carried_after_gate"]
        if "final" in result:
            summary["task-count"] = result["final"]["task-count"]
            summary["task-ids"] = result["final"]["task-ids"]
            summary["post-202"] = len(result["final"]["post-202"])
            summary["edit-a-persisted"] = result["final"]["edit-a-persisted"]
        print(json.dumps(summary, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    parser.add_argument("--shots-dir", type=pathlib.Path, required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--mode", choices=["repro", "fixed", "natural"], default="repro")
    parser.add_argument("--inject-stale", type=int, default=None,
                        help="override the stale-base rewrites armed before the generate click "
                             "(default: 3 for repro/fixed, 0 for natural)")
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    shots_dir = args.shots_dir.resolve()
    inject = args.inject_stale if args.inject_stale is not None else (0 if args.mode == "natural" else INJECT_STALE)
    results = []
    for i in range(1, args.runs + 1):
        results.append(run_journey(f"run{i:02d}", artifacts, shots_dir, mode=args.mode, inject_stale=inject))
    stopped = [r.get("gate", {}).get("stopped_toast") for r in results if "gate" in r]
    passed = [r["passed"] for r in results]
    summary = {
        "status": "passed" if results and all(p for p in passed) else "failed",
        "mode": args.mode,
        "runs": len(results),
        "passed": f"{sum(p for p in passed)}/{len(passed)}",
        "stopped-toast": f"{sum(1 for s in stopped if s)}/{len(stopped)}",
        "results": [{"run": r.get("run"), "mode": r.get("mode"), "passed": r.get("passed"),
                     "stopped_toast": r.get("gate", {}).get("stopped_toast"),
                     "limit_reached": r.get("gate", {}).get("limit_reached"),
                     "gate_409_count": r.get("gate", {}).get("gate_409_count"),
                     "marker_carried_after_gate": r.get("gate", {}).get("marker_carried_after_gate"),
                     "task-count": r.get("final", {}).get("task-count"),
                     "post-202": len(r.get("final", {}).get("post-202") or []),
                     "failure": (r.get("failure") or {}).get("message") if r.get("failure") else None}
                    for r in results],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
