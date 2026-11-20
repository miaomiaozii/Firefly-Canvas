import base64
import copy
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools, api_mcp
from firefly_projects import ProjectStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    caps = {"aspect_ratios": ["1:1", "16:9", "9:21"], "resolutions": ["1k", "2k"],
            "arbitrary_size": True, "max_references": 20}
    providers = [{"id": "mock", "name": "Mock", "protocol": "openai", "enabled": True,
                  "base_url": "https://example.invalid/v1", "api_key": "mock-only",
                  "image_models": ["fallback", "picked"],
                  "image_model_capabilities": {"fallback": caps, "picked": caps}}]
    monkeypatch.setattr(main, "load_api_providers", lambda: providers)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    png = buf.getvalue()
    calls = []

    async def provider(prompt, size, quality, model, refs, provider_id, **kwargs):
        calls.append({"prompt": prompt, "size": size, "quality": quality, "model": model,
                      "refs": [r["url"] for r in refs], "provider": provider_id,
                      "parameters": kwargs.get("image_parameters"), "preserve_model": kwargs.get("preserve_model")})
        return {"type": "b64", "value": base64.b64encode(png).decode(), "mime_type": "image/png"}, {}

    monkeypatch.setattr(main, "generate_ai_image", provider)
    pid = store.create_project("A6")['id']
    ids = [store.import_media(pid, png, f"ref{i}.png", "image/png")["asset_id"] for i in range(3)]
    # Importing identical bytes deduplicates on some store implementations.
    if len(set(ids)) != 3:
        ids = [store.import_media(pid, png + bytes([i]), f"ref{i}.png", "image/png")["asset_id"] for i in range(3)]
    settings = {"engine": "api", "provider_id": "mock", "model": "picked", "routingMode": "manual",
                "ratio": "portrait14", "resolution": "2k", "count": "2", "quality": "auto"}
    source = {"id": "source", "type": "smart-image", "prompt": "A firefly",
              "createdBy": "agent", "agentNodeId": "actor", "runSettings": settings,
              "reference_asset_ids": [ids[2]]}
    nodes = [source, {"id": "actor", "type": "smart-agent"}, {"id": "browser-result", "type": "smart-image"},
             {"id": "ref", "type": "smart-image", "images": [{"asset_id": asset} for asset in ids]}]
    edges = [{"id": f"ref{i}", "kind": "input", "agentRef": True, "from": "ref", "to": "source",
              "order": 2-i, "role": role, "enabled": i != 2, "asset_id": ids[i]}
             for i, role in enumerate(("detail", "product", "layout"))]
    store.apply_mutations(pid, [{"op": "upsert_node", "node": n} for n in nodes] +
                          [{"op": "upsert_edge", "edge": e} for e in edges], base_revision=0)
    token = object()
    register_project_session(token, pid)
    try:
        with TestClient(main.app, base_url="http://localhost", headers={"origin": "http://localhost"}) as client:
            yield client, store, pid, calls, ids, providers
    finally:
        unregister_project_session(token)
        api_agent_tools.reset_generation_counts(pid)
        store.close()


def rest(client, pid, suffix, args, method="POST"):
    return client.request(method, f"/api/projects/{pid}/{suffix}?agent_node_id=actor", json=args,
                          headers={"X-Agent-Project": pid})


def mcp(client, pid, tool, args):
    response = client.post(f"/api/mcp?project={pid}&node=actor", json={"jsonrpc": "2.0", "id": 1,
                           "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return (True, result["structuredContent"]) if result["isError"] else (False, json.loads(result["content"][0]["text"]))


def approve(monkeypatch, fn):
    monkeypatch.setattr(api_agent_tools, "request_confirmation_core", fn)
    monkeypatch.setattr(api_mcp, "request_confirmation_core", fn)


def wait(client, url):
    for _ in range(200):
        response = client.get(url)
        assert response.is_success, response.text
        if response.json()["status"] in ("succeeded", "failed"):
            assert response.json()["status"] == "succeeded", response.text
            return
        time.sleep(.02)
    pytest.fail("generation did not finish")


def test_actual_browser_payload_and_agent_provider_request_match(run, monkeypatch):
    client, store, pid, calls, ids, providers = run
    if not shutil.which("node"):
        pytest.fail("Node is required to check the actual browser payload")
    async def yes(*args):
        return {"approved": True}
    approve(monkeypatch, yes)
    fixture = {"project": pid, "canvas": store.canvas(pid), "providers": providers}
    raw = subprocess.check_output(["node", str(ROOT / "tests/canvas_module_units/generation-payload-396.mjs")],
                                  cwd=ROOT, input=json.dumps(fixture).encode())
    payload = json.loads(raw)
    assert payload["reference_asset_ids"] == [ids[1], ids[0]]
    assert payload["prompt"] == "A firefly"
    assert payload["reference_roles"] == ["product", "detail"]
    assert (payload["aspect_ratio"], payload["n"], payload["model"]) == ("9:21", 2, "picked")
    browser = client.post(f"/api/project-library/{pid}/image-generation-tasks", json=payload)
    assert browser.status_code == 202, browser.text
    wait(client, f"/api/project-library/{pid}/image-generation-tasks/{browser.json()['task_id']}")
    agent = rest(client, pid, "generate/trigger", {"node_id": "source"})
    assert agent.status_code == 200, agent.text
    wait(client, f"/api/projects/{pid}/generate/tasks/{agent.json()['task_id']}")
    assert len(calls) == 4
    assert calls == [calls[0]] * 4
    assert calls[0]["prompt"] == "参考图说明：图1：产品主体；图2：细节。\n\nA firefly"
    assert calls[0]["refs"] == [f"/api/project-library/{pid}/media/{asset}" for asset in [ids[1], ids[0]]]
    assert store.find_canvas_node(pid, "source")["reference_asset_ids"] == [ids[2]]


@pytest.mark.parametrize("transport", ["rest", "mcp", "rest-batch", "mcp-batch"])
def test_gate_budget_and_dispatch_share_count_snapshot(run, monkeypatch, transport):
    client, store, pid, calls, _, _ = run
    confirmations = []
    async def yes(project, actor, tool, params, message, timeout):
        confirmations.append(message)
        node = store.find_canvas_node(pid, "source")
        node["runSettings"] = {**node["runSettings"], "count": 8, "routingMode": "auto", "ratio": "square"}
        store.apply_mutations(pid, [{"op": "upsert_node", "node": node}], base_revision=store.canvas(pid)["revision"])
        return {"approved": True}
    approve(monkeypatch, yes)
    monkeypatch.setattr(api_agent_tools, "GENERATION_SESSION_LIMIT", 2)
    batch = transport.endswith("batch")
    args = {"node_ids": ["source"], "prompt": "Batch"} if batch else {"node_id": "source"}
    def trigger():
        if transport.startswith("rest"):
            response = rest(client, pid, "canvas/batch_edit" if batch else "generate/trigger", args)
            return not response.is_success, response.json()
        return mcp(client, pid, "canvas.batch_edit" if batch else "generate.trigger", args)
    error, result = trigger()
    assert not error, result
    task = result["tasks"][0] if batch else result
    wait(client, f"/api/projects/{pid}/generate/tasks/{task['task_id']}")
    assert "2 张" in confirmations[0]
    assert len(calls) == 2 and all(c["model"] == "picked" for c in calls)
    assert trigger()[0]
    assert len(calls) == 2
    node = store.find_canvas_node(pid, "source")
    assert node["runSettings"]["count"] == 8
    assert node["runSettings"]["routingMode"] == "auto"


def test_update_run_settings_merge_prev_and_journal_noop(run):
    client, store, pid, _, _, _ = run
    before = copy.deepcopy(store.find_canvas_node(pid, "source")["runSettings"])
    args = {"runSettings": {"count": 3, "aspect_ratio": "16:9"}}
    response = rest(client, pid, "canvas/nodes/source", args, method="PATCH")
    assert response.is_success, response.text
    result = response.json()
    assert result["prev"]["runSettings"]["count"] == before["count"]
    assert result["prev"]["runSettings"]["aspect_ratio"] == "1:4"
    assert result["op_id"]
    changed = store.find_canvas_node(pid, "source")["runSettings"]
    assert {k: changed[k] for k in ("engine", "model", "provider_id", "resolution", "quality")} == {
        k: before[k] for k in ("engine", "model", "provider_id", "resolution", "quality")}
    assert (changed["ratio"], changed["count"]) == ("wide", 3)
    revision = store.canvas(pid)["revision"]
    error, again = mcp(client, pid, "canvas.update_node", {"node_id": "source", **args})
    assert not error and "op_id" not in again
    for value in ({"apiKey": "no"}, {"count": True}, {"routingMode": "unknown"}, None):
        assert rest(client, pid, "canvas/nodes/source", {"runSettings": value}, method="PATCH").status_code == 400
        assert mcp(client, pid, "canvas.update_node", {"node_id": "source", "runSettings": value})[0]
    assert store.canvas(pid)["revision"] == revision


def test_paid_arguments_cannot_override_settings_or_gate(run, monkeypatch):
    client, store, pid, calls, _, _ = run
    async def no(*args):
        return {"approved": False, "reason": "rejected"}
    approve(monkeypatch, no)
    args = {"node_id": "source", "n": 1, "approved": True, "routingMode": "auto", "batch_id": "forged"}
    assert rest(client, pid, "generate/trigger", args).status_code in (400, 409)
    assert mcp(client, pid, "generate.trigger", args)[0]
    assert not calls and store.find_canvas_node(pid, "source")["runSettings"]["count"] == "2"
