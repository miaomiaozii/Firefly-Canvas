"""Native-browser journey for issue #190: deleting generation history/originals while the
canvas save is in flight must not leave the canvas in a permanent "cannot save" error state.

Repro path (per the issue):
  1. Seed one image node + five generation-history records (each with a generated
     original / project media asset).
  2. Make an unsaved prompt edit -> the 3s debounced autosave goes in flight
     (PATCH /api/project-library/{id}/canvas, base_revision=R).
  3. While that save is in flight, delete history records one after another
     through the history drawer's "delete log + media" buttons (native clicks,
     native confirm() acceptance).  Each delete is a separate server operation
     that advances canvas.revision; the in-flight save keeps 409-ing and its
     2-retry conflict budget is exhausted -> save state stuck on
     "canvas incremental save conflict" / "save failed".
     In storm mode the store's apply_mutations is slowed (simulated disk
     latency, test-double only) so the in-flight save window stays open while
     the deletes land first - the same interleaving that happens on slow
     machines/networks in the field (the issue is intermittent).
  4. ACCEPTANCE (the red->green assertion): after the conflict, the user makes
     another edit and saves.  The edit MUST reach the server WITHOUT a page
     refresh (server readback via the in-process store).  Before the fix the
     issue reports this never happens (only a hard refresh recovers); after
     the fix it must.  A reload probe doubles as the control that the
     "refresh recovers" symptom was present at all.

Evidence contract: writes ``manifest.json``, ``screenshots/*.png``,
``operations.jsonl``, ``console-errors.json``, ``net-log.jsonl``,
``state-samples.jsonl`` and ``server-log.jsonl`` under ``--artifacts-dir``
(per-run subdirectories), screenshots under ``--shots-dir`` (per-run
subdirectories).  No real provider is ever contacted.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
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
NODE_ID = "img-190"
N_RECORDS = 12
EDIT_A = "[edit-190-a]"
EDIT_B = "[edit-190-b]"
EDIT_C = "[edit-190-c]"
SAVE_SETTLE_S = 8.0        # wait after the manual save before the readback
RETRY_WAIT_S = 15.0        # extra wait to see whether anything self-heals

INSTRUMENT_JS = r"""
(function(){
  if(window.__fix190Installed) return 'already';
  window.__fix190Installed = true;
  window.__fix190Net = [];
  const origFetch = window.fetch;
  window.fetch = function(input, init){
    const url = typeof input === 'string' ? input : ((input && input.url) || '');
    const method = (init && init.method) || ((typeof input !== 'string' && input && input.method) || 'GET');
    let base = null; let nMut = null;
    try {
      if(init && init.body){ const b = JSON.parse(init.body); base = b.base_revision; nMut = (b.mutations || []).length; }
    } catch(_){}
    const t0 = performance.now();
    return origFetch.apply(this, arguments).then(function(res){
      const t1 = performance.now();
      let rRev = null; let rSaved = null; let rDetail = null;
      if(/\/(api\/)?(canvases|project-library)\//.test(url)){
        res.clone().json().then(function(j){
          window.__fix190Net[window.__fix190Net.length - 1] && (window.__fix190Net[window.__fix190Net.length - 1].rRev = (j.revision != null) ? j.revision : ((j.canvas && j.canvas.revision) != null ? j.canvas.revision : null));
          window.__fix190Net[window.__fix190Net.length - 1] && (window.__fix190Net[window.__fix190Net.length - 1].rSaved = (j.saved != null) ? j.saved : null);
          if(j.detail) window.__fix190Net[window.__fix190Net.length - 1].rDetail = (typeof j.detail === 'string') ? String(j.detail).slice(0, 160) : {hasCanvas: !!j.detail.canvas, message: j.detail.message || ''};
        }).catch(function(){});
      }
      window.__fix190Net.push({t: Math.round(t0), url: url.replace(location.origin, ''), method: method, base: base, nMut: nMut, status: res.status, ms: Math.round(t1 - t0), rRev: rRev, rSaved: rSaved, rDetail: rDetail});
      return res;
    });
  };
  window.__fix190Samples = [];
  setInterval(function(){
    try {
      const el = document.getElementById('projectSaveState');
      window.__fix190Samples.push({
        t: Math.round(performance.now()),
        sync: canvasSyncInFlight,
        dirty: contentDirty,
        deb: !!debouncedSaveTimer,
        state: el ? (el.dataset.state || null) : null,
        rev: (canvas && canvas.revision != null) ? canvas.revision : null,
        busy: smartHistoryDeletingBusy,
        hist: ((canvas && canvas.history) || []).length
      });
      if(window.__fix190Samples.length > 4000) window.__fix190Samples.splice(0, 1000);
    } catch(e){}
  }, 120);
  return 'installed';
})()
"""


FLUSH_SPY_JS = r"""
(function(){
  if(window.__fix190FlushSpyInstalled) return 'already';
  window.__fix190FlushSpyInstalled = true;
  window.__fix190FlushResults = [];
  const client = window.FireflyProjectCanvasMutations;
  if(!client) return 'no-client';
  const orig = client.flushProjectCanvasMutations;
  client.flushProjectCanvasMutations = function(options){
    return Promise.resolve(orig.call(this, options)).then(function(result){
      window.__fix190FlushResults.push({
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
  if(window.__fix190InjectInstalled) return 'already';
  window.__fix190InjectInstalled = true;
  window.__fix190Inject = {left: 0, hits: 0, log: []};
  const orig = window.fetch;
  window.fetch = function(input, init){
    try {
      const url = typeof input === 'string' ? input : ((input && input.url) || '');
      const isSavePatch = url.indexOf('project-library') !== -1 &&
        url.split('?')[0].split('/').pop() === 'canvas' &&
        !!(init && init.method === 'PATCH') && !!(init && init.body);
      if(window.__fix190Inject.left > 0 && isSavePatch){
        const body = JSON.parse(init.body);
        if(body && typeof body.base_revision === 'number' && body.base_revision > 0){
          const stale = body.base_revision - 1;
          window.__fix190Inject.left -= 1;
          window.__fix190Inject.hits += 1;
          window.__fix190Inject.log.push({t: Math.round(performance.now()), base: body.base_revision, sent: stale});
          init = Object.assign({}, init, {body: JSON.stringify(Object.assign({}, body, {base_revision: stale}))});
        }
      }
    } catch(_){}
    return orig.apply(this, arguments);
  };
  return 'installed';
})()
"""


def solid_png(color: tuple[int, int, int, int], size: tuple[int, int] = (160, 120)) -> bytes:
    from PIL import Image

    image = Image.new("RGBA", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def seed(store: ProjectStore, n_records: int = N_RECORDS) -> str:
    project = store.create_project("Issue 190 History Delete Save Conflict")
    project_id = project["id"]
    now = int(time.time() * 1000)
    history = []
    for i in range(n_records):
        record = store.import_media(project_id, solid_png((40 + 45 * i, 90, 210, 255)), f"gen-{i}.png", "image/png")
        history.append({
            "id": f"hist-{i}",
            "type": "smart-image",
            "status": "succeeded",
            "prompt": f"repro-190 prompt {i}",
            "created_at": now - (n_records - i) * 1000,
            "completed_at": now - (n_records - i) * 1000 + 120,
            "platform": "repro-190",
            "model": "repro-model",
            "outputs": [{
                "output_id": f"out-{i}",
                "asset_id": record["asset_id"],
                "name": f"gen-{i}.png",
                "kind": "image",
            }],
        })
    node_asset = store.import_media(project_id, solid_png((205, 60, 60, 255)), "node-base.png", "image/png")["asset_id"]
    store.apply_mutations(project_id, [
        {"op": "upsert_node", "node": {
            "id": NODE_ID, "type": "smart-image", "name": "Target",
            "x": 240, "y": 120, "w": 360, "h": 300,
            "runPrompt": "repro-190 base prompt",
            "runSettings": {"engine": "api", "apiKind": "image", "model": "repro-model"},
            "images": [{"asset_id": node_asset, "kind": "image", "name": "node-base.png"}],
        }},
        {"op": "set_canvas_meta", "title": "Issue 190", "history": history},
    ], base_revision=0)
    store.flush(project_id)
    return project_id


def server_prompt(store: ProjectStore, project_id: str) -> str:
    """The prompt text as persisted: runPrompt plus the live draft (promptInput
    edits land in promptDraftText first; runPrompt commits later)."""
    opened = store.open_project(project_id)
    for node in opened["canvas"].get("nodes") or []:
        if node.get("id") == NODE_ID:
            return str(node.get("runPrompt") or "") + "\n" + str(node.get("promptDraftText") or "")
    return ""


def server_history_ids(store: ProjectStore, project_id: str) -> list[str]:
    opened = store.open_project(project_id)
    return [str(item.get("id") or "") for item in opened["canvas"].get("history") or []]


def server_revision(store: ProjectStore, project_id: str) -> int:
    return int(store.open_project(project_id)["canvas"].get("revision") or 0)


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


def slow_down(store: ProjectStore, methods: tuple[str, ...], delay_ms: int) -> None:
    """Test-double: simulate slow disk on the given store methods (save path)."""
    for name in methods:
        original = getattr(store, name)

        def make(orig, n):
            def wrapper(*args, **kwargs):
                time.sleep(delay_ms / 1000.0)
                return orig(*args, **kwargs)

            wrapper.__name__ = f"slow_{n}"
            return wrapper

        setattr(store, name, make(original, name))


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
              out.openModals=[].slice.call(document.querySelectorAll('.open')).map(function(m){return m.tagName+'#'+m.id+'.'+String(m.className).slice(0,80);}).slice(0,10);
              return out;
            })()""")
            print("PROMPT-BLOCKED-DEBUG:", json.dumps(info, ensure_ascii=False), file=sys.stderr)
            if shot is not None:
                browser.capture_screenshot(shot)
        except Exception as dbg_error:  # noqa: BLE001
            print("PROMPT-BLOCKED-DEBUG failed:", dbg_error, file=sys.stderr)
        raise


def close_history_drawer(browser: NativeBrowser) -> None:
    """The open history drawer covers the prompt panel; close it through its X button."""
    if not browser.evaluate('document.getElementById("smartHistoryModal").classList.contains("open")'):
        return
    browser.native_click('#smartHistoryModal .log-head-actions .preview-icon-btn:last-child')
    wait_state(browser, '!document.getElementById("smartHistoryModal").classList.contains("open")', 15, "history drawer closed")


def read_flush_spy(browser: NativeBrowser) -> list[dict]:
    raw = state(browser, "JSON.stringify(window.__fix190FlushResults || [])")
    return json.loads(raw) if isinstance(raw, str) and raw else []


def read_inject_state(browser: NativeBrowser) -> dict | None:
    raw = state(browser, "JSON.stringify(window.__fix190Inject || null)")
    return json.loads(raw) if isinstance(raw, str) and raw and raw != "null" else None


def run_journey(
    run_id: str,
    artifacts: pathlib.Path,
    shots_dir: pathlib.Path,
    *,
    slow_apply_ms: int,
    inject_stale: int = 0,
) -> dict:
    run_artifacts = artifacts / run_id
    run_shots = shots_dir / run_id
    run_artifacts.mkdir(parents=True, exist_ok=True)
    run_shots.mkdir(parents=True, exist_ok=True)
    if inject_stale:
        mode = "inject-stale"
    elif slow_apply_ms:
        mode = "storm"
    else:
        mode = "natural"
    result: dict = {
        "run": run_id,
        "mode": mode,
        "slow_apply_ms": slow_apply_ms,
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
    try:
        with tempfile.TemporaryDirectory(prefix=f"firefly-190-{run_id}-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id = seed(store)
            if slow_apply_ms:
                slow_down(store, ("apply_mutations",), slow_apply_ms)
            app = harness.load_production_app(root / "runtime-root", store)
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
                if inject_stale:
                    browser.evaluate(INJECT_STALE_JS)
                # let the initial hydration settle (load marks the canvas dirty briefly)
                wait_state(browser, "contentDirty===false && canvasSyncInFlight===false", 25, "initial save settle")
                result["steps"]["loaded-settled"] = True
                browser.capture_screenshot(run_shots / "00-opened.png")
                result["rev-trace"] = [{"phase": "settled", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")}]

                # --- step 1: unsaved prompt edit (drawer is still closed, prompt is clickable)
                browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                time.sleep(0.8)  # let the node panel animation settle before clicking into the prompt
                wait_prompt_clickable(browser, shot=run_shots / "debug-prompt-blocked-1.png")
                browser.native_click("#promptInput", relative=(0.02, 0.5))
                browser.native_press("End")
                browser.native_type(EDIT_A)
                wait_state(browser, "contentDirty===true", 10, "edit marks canvas dirty")
                result["steps"]["edit-a"] = True
                result["rev-trace"].append({"phase": "edit-a", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")})
                browser.capture_screenshot(run_shots / "01-edit-a.png")

                # --- step 2: open the history drawer (top toolbar stays clickable)
                browser.native_click("#smartHistoryToggle")
                wait_state(browser, 'document.getElementById("smartHistoryModal").classList.contains("open") && document.querySelectorAll(\'[data-history-delete="media"]\').length >= 1', 20, "history drawer with delete buttons")

                # --- step 3: manual save starts immediately -> canvasSyncInFlight=true
                if inject_stale:
                    # deterministic exhaustion: arm N stale-base rewrites on the
                    # save PATCH; each rewrite makes the real server return an
                    # authentic 409 with the latest canvas in the body.
                    state(browser, f"window.__fix190Inject.left = {int(inject_stale)}")
                browser.native_click("#smartSaveButton")
                wait_state(browser, "canvasSyncInFlight===true", 10, "manual save in flight")
                result["steps"]["save-in-flight"] = True
                result["rev-trace"].append({"phase": "save-in-flight", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")})

                # --- step 4: delete history records (+media) one after another
                # while the manual save is in flight: every delete is a separate
                # server operation advancing canvas.revision, so the in-flight
                # save's 409-retry chain keeps colliding.
                history_before = server_history_ids(store, project_id)
                deletes_done = 0
                deletes_conflicted = 0
                attempts = 0
                for attempts in range(1, N_RECORDS + 3):
                    button_js = "(function(){var b=document.querySelector('[data-history-delete=\"media\"]');if(!b)return null;var r=b.getBoundingClientRect();return (r.width>0&&r.height>0)?true:false;})()"
                    if not browser.evaluate(button_js):
                        break
                    browser.native_click('[data-history-delete="media"]', accept_dialog=True)
                    wait_state(browser, "smartHistoryDeletingBusy===false", 20, f"delete {attempts} settled")
                    deletes_done += 1
                    time.sleep(0.15)
                    if attempts == 3:
                        browser.capture_screenshot(run_shots / "02-mid-storm.png")
                net_so_far = state(browser, "JSON.stringify(window.__fix190Net)")
                net_entries = json.loads(net_so_far or "[]")
                deletes_conflicted = sum(1 for item in net_entries if "delete-with-media" in item.get("url", "") and item.get("status") == 409)
                history_after_storm = server_history_ids(store, project_id)
                result["steps"]["delete-storm"] = True
                result["deletes"] = {"attempts": attempts - 1, "succeeded_server_side": N_RECORDS - len(history_after_storm), "conflicted_409": deletes_conflicted}
                browser.capture_screenshot(run_shots / "03-after-storm.png")
                result["rev-trace"].append({"phase": "after-storm", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")})

                # --- step 5: let the save attempts settle, capture the stuck state
                wait_state(browser, "canvasSyncInFlight===false", 30, "save flight cleared")
                time.sleep(4.0)  # beyond the 3s debounce, so a self-heal retry already fired
                stuck = state(browser, "(function(){var el=document.getElementById('projectSaveState');return {state:el?el.dataset.state:null,text:el?el.textContent:''};})()")
                result["stuck_state"] = stuck
                result["flush-spy"] = read_flush_spy(browser)
                result["inject-state"] = read_inject_state(browser) if inject_stale else None
                prompt_after_storm = server_prompt(store, project_id)
                result["edit-a-persisted"] = EDIT_A in prompt_after_storm
                result["rev-trace"].append({"phase": "stuck-capture", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision"), "prompt": prompt_after_storm[-60:]})

                # --- step 6 (THE acceptance): new edit + manual save, NO refresh
                close_history_drawer(browser)
                browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                time.sleep(0.8)
                wait_prompt_clickable(browser, shot=run_shots / "debug-prompt-blocked-2.png")
                browser.native_click("#promptInput", relative=(0.02, 0.5))
                browser.native_press("End")
                browser.native_type(EDIT_B)
                wait_state(browser, "contentDirty===true", 10, "edit-b marks canvas dirty")
                browser.capture_screenshot(run_shots / "04-edit-b.png")
                browser.native_click("#smartSaveButton")
                time.sleep(SAVE_SETTLE_S)
                saved_b = EDIT_B in server_prompt(store, project_id)
                time.sleep(RETRY_WAIT_S)  # give any debounced/manual retry a real chance
                saved_b_late = EDIT_B in server_prompt(store, project_id)
                result["rev-trace"].append({"phase": "after-retry-wait", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")})
                final_state = state(browser, "(function(){var el=document.getElementById('projectSaveState');return {state:el?el.dataset.state:null,text:el?el.textContent:''};})()")
                result["edit-b-persisted-no-refresh"] = saved_b or saved_b_late
                result["edit-b-persisted-immediately"] = saved_b
                result["final_state_no_refresh"] = final_state
                browser.capture_screenshot(run_shots / "05-after-save-attempt.png")
                result["rev-trace"].append({"phase": "after-save-attempt", "server": server_revision(store, project_id), "page": state(browser, "canvas && canvas.revision")})

                # capture the pre-reload evidence (reload wipes the JS state)
                pre_reload_flush = read_flush_spy(browser)
                pre_reload_inject = read_inject_state(browser) if inject_stale else None
                if pre_reload_inject is not None:
                    result["inject-state"] = pre_reload_inject
                result["flush-spy"] = pre_reload_flush or result.get("flush-spy", [])
                pre_reload_net = state(browser, "JSON.stringify(window.__fix190Net)") or "[]"
                pre_reload_samples = state(browser, "JSON.stringify(window.__fix190Samples)") or "[]"
                result["net"] = json.loads(pre_reload_net if isinstance(pre_reload_net, str) else "[]")
                result["samples"] = json.loads(pre_reload_samples if isinstance(pre_reload_samples, str) else "[]")

                # --- step 7 (control): hard refresh must recover the page
                close_history_drawer(browser)
                browser.reload(timeout=40)
                browser.wait_for(f'document.querySelector(".image-node[data-id=\\"{NODE_ID}\\"]")', timeout=30)
                time.sleep(1.5)
                browser.evaluate(INSTRUMENT_JS)
                wait_state(browser, "contentDirty===false && canvasSyncInFlight===false", 25, "post-reload settle")
                browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                time.sleep(0.8)
                wait_prompt_clickable(browser, shot=run_shots / "debug-prompt-blocked-3.png")
                browser.native_click("#promptInput", relative=(0.02, 0.5))
                browser.native_press("End")
                browser.native_type(EDIT_C)
                browser.native_click("#smartSaveButton")
                time.sleep(SAVE_SETTLE_S)
                result["edit-c-persisted-after-reload"] = EDIT_C in server_prompt(store, project_id)
                browser.capture_screenshot(run_shots / "06-after-reload.png")

                # --- verdict
                net_after_reload = state(browser, "JSON.stringify(window.__fix190Net)") or "[]"
                result["net"] = result["net"] + json.loads(net_after_reload if isinstance(net_after_reload, str) else "[]")
                samples_after_reload = state(browser, "JSON.stringify(window.__fix190Samples)") or "[]"
                result["samples"] = result["samples"] + json.loads(samples_after_reload if isinstance(samples_after_reload, str) else "[]")
                conflict_409s = [item for item in result["net"] if item.get("status") == 409]
                result["conflict_409_count"] = len(conflict_409s)
                result["conflict_observed"] = bool(conflict_409s)
                result["passed"] = bool(result.get("edit-b-persisted-no-refresh")) and result.get("conflict_observed", False)
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
                    net_tail = state(browser, "JSON.stringify(window.__fix190Net)")
                    samples_tail = state(browser, "JSON.stringify(window.__fix190Samples)")
                    if isinstance(net_tail, str) and net_tail and net_tail != "[]":
                        tail = json.loads(net_tail)
                        if not result["net"]:
                            result["net"] = tail
                        else:
                            # post-reload tail (performance.now restarted): keep both halves
                            result["net"] = result["net"] + tail
                    if isinstance(samples_tail, str) and samples_tail and samples_tail != "[]":
                        tail = json.loads(samples_tail)
                        if not result["samples"]:
                            result["samples"] = tail
                        elif tail and (result["samples"][-1].get("t", 0) > tail[0].get("t", 0)):
                            result["samples"] = result["samples"] + tail
                except Exception:  # noqa: BLE001 — page may already be gone
                    pass
                try:
                    tail_flush = read_flush_spy(browser)
                    if tail_flush and result.get("flush-spy"):
                        if result["flush-spy"][-1].get("t", 0) > tail_flush[0].get("t", 0):
                            result["flush-spy"] = result["flush-spy"] + tail_flush
                    elif tail_flush and not result.get("flush-spy"):
                        result["flush-spy"] = tail_flush
                except Exception:  # noqa: BLE001 — page may already be gone
                    pass
                try:
                    write_jsonl(run_artifacts / "operations.jsonl", list(browser.operations))
                    write_json(run_artifacts / "console-errors.json", list(browser.console_errors))
                finally:
                    browser.close()
        write_jsonl(run_artifacts / "net-log.jsonl", result["net"])
        write_jsonl(run_artifacts / "state-samples.jsonl", result["samples"])
        write_jsonl(run_artifacts / "server-log.jsonl", result["server"])
        write_json(run_artifacts / "manifest.json", {k: v for k, v in result.items() if k not in ("net", "server", "samples")})
        print(json.dumps({k: result.get(k) for k in ("run", "mode", "passed", "conflict_observed", "conflict_409_count", "edit-a-persisted", "edit-b-persisted-no-refresh", "final_state_no_refresh", "edit-c-persisted-after-reload", "failure", "inject-state", "flush-spy")}, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    parser.add_argument("--shots-dir", type=pathlib.Path, required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--slow-apply-ms", type=int, default=1500,
                        help="test-double delay on store.apply_mutations (ms); 0 = natural timing")
    parser.add_argument("--inject-stale", type=int, default=0,
                        help="rewrite the first N canvas-save PATCHes to a stale base_revision "
                             "so the real server returns authentic 409s (0 = no injection)")
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    shots_dir = args.shots_dir.resolve()
    results = []
    for i in range(1, args.runs + 1):
        results.append(run_journey(
            f"run{i:02d}", artifacts, shots_dir,
            slow_apply_ms=0 if args.inject_stale else args.slow_apply_ms,
            inject_stale=args.inject_stale,
        ))
    dead = [r for r in results if r.get("conflict_observed") and not r.get("edit-b-persisted-no-refresh")]
    conflicted = [r for r in results if r.get("conflict_observed")]
    summary = {
        "status": "passed" if results and all(r["passed"] for r in results) else "failed",
        "runs": len(results),
        "conflicts": len(conflicted),
        "dead_states": len(dead),
        "results": [{k: r.get(k) for k in ("run", "mode", "passed", "conflict_observed", "edit-b-persisted-no-refresh", "final_state_no_refresh")} for r in results],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
