import pytest
from fastapi.testclient import TestClient

import main
from app.services.agent_ops_undo import undo_ops
from firefly_projects import ProjectStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    pid = store.create_project("reference tools")["id"]
    assets = {key: store.import_media(pid, key.encode(), key + ".png", "image/png")["asset_id"]
              for key in ("base", "product", "detail", "off", "a_遮罩_ID")}
    nodes = [
        {"id": "actor", "type": "smart-agent"},
        {"id": "target", "type": "smart-image", "prompt": "user prompt",
         "manualInputRefs": [{"asset_id": assets["base"]}], "blockedInputRefs": ["old"]},
        {"id": "source", "type": "smart-image", "images": [
            {"asset_id": assets["product"]}, {"asset_id": assets["detail"]}]},
    ]
    store.apply_mutations(pid, [{"op": "upsert_node", "node": node} for node in nodes] + [
        {"op": "upsert_edge", "edge": {"id": "hand-edge", "from": "source", "to": "target", "kind": "input"}},
    ], base_revision=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    try:
        yield (store, pid, assets)
    finally:
        store.close()


def post(pid, tool, payload):
    client = TestClient(main.app, base_url="http://localhost")
    return client.post(f"/api/projects/{pid}/canvas/{tool}",
                       headers={"X-Agent-Project": pid}, json={**payload, "agent_node_id": "actor"})


def test_attach_orders_exact_assets_replays_replaces_and_undoes_atomically(project):
    store, pid, assets = project
    initial = store.canvas(pid)
    params = {"node_id": "target", "refs": [
        {"source": {"node_id": "source", "image_index": 1}, "role": "detail"},
        {"source": {"node_id": "source"}, "role": "product"},
        {"source": {"asset_id": assets["off"]}, "enabled": False},
    ]}
    response = post(pid, "attach_refs", params)
    assert response.status_code == 200, response.text
    attach = response.json()
    assert [(r["n"], r["asset_id"], r["role"], r["enabled"]) for r in attach["refs"]] == [
        (1, assets["product"], "product", True), (2, assets["detail"], "detail", True),
        (None, assets["off"], "general", False),
    ]
    assert "图1" in attach["prefix_preview"] and "图2" in attach["prefix_preview"]
    after = store.canvas(pid)
    assert len(after["connections"]) == 4
    listed = TestClient(main.app, base_url="http://localhost").get(f"/api/projects/{pid}/canvas/nodes")
    assert listed.status_code == 200, listed.text
    target = next(node for node in listed.json()["nodes"] if node["id"] == "target")
    assert target["refs"] == [
        {"n": 1, "asset_id": assets["product"], "role": "product"},
        {"n": 2, "asset_id": assets["detail"], "role": "detail"},
    ]
    assert "op_id" not in post(pid, "attach_refs", params).json()
    assert store.canvas(pid) == after
    replacement = post(pid, "attach_refs", {"node_id": "target", "refs": [], "mode": "replace"})
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["refs"] == []
    assert store.canvas(pid)["connections"] == initial["connections"]
    node = store.find_canvas_node(pid, "target")
    assert node["manualInputRefs"] == [{"asset_id": assets["base"]}]
    assert node["blockedInputRefs"] == ["old"]
    assert node["prompt"] == "user prompt"
    assert node["reference_asset_ids"] == []
    assert undo_ops(store, pid, op_id=replacement.json()["op_id"])["status"] == "reverted"
    assert store.find_canvas_node(pid, "target")["reference_asset_ids"] == [assets["product"], assets["detail"]]
    assert undo_ops(store, pid, op_id=attach["op_id"])["status"] == "reverted"
    assert store.canvas(pid)["connections"] == initial["connections"]


def test_place_media_with_existing_node_appends_once_and_mask_rejection_is_atomic(project):
    store, pid, assets = project
    payload = {"node_id": "source", "asset_id": assets["base"]}
    response = post(pid, "place_media", payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["node_id"] == "source"
    assert [img["asset_id"] for img in store.find_canvas_node(pid, "source")["images"]] == [
        assets["product"], assets["detail"], assets["base"],
    ]
    original = store.canvas(pid)
    replay = post(pid, "place_media", payload)
    assert replay.status_code == 200 and "op_id" not in replay.json()
    invalid = post(pid, "attach_refs", {"node_id": "target", "refs": [
        {"source": {"asset_id": assets["base"]}}, {"source": {"asset_id": assets["a_遮罩_ID"]}},
    ]})
    assert invalid.status_code == 400 and "ASSET_IS_MASK" in invalid.text
    assert store.canvas(pid) == original
    assert undo_ops(store, pid, op_id=result["op_id"])["status"] == "reverted"
    assert len(store.find_canvas_node(pid, "source")["images"]) == 2


def test_order_tie_breaker_and_role_overrides_refresh_upload_numbers(project):
    store, pid, assets = project
    first, second = assets["product"], assets["detail"]
    attached = post(pid, "attach_refs", {"node_id": "target", "refs": [
        {"source": {"asset_id": first}}, {"source": {"asset_id": second}},
    ]})
    assert attached.status_code == 200, attached.text
    reordered = post(pid, "set_ref_order", {"node_id": "target", "order": [second, first]})
    assert reordered.status_code == 200, reordered.text
    assert [(r["n"], r["asset_id"]) for r in reordered.json()["refs"]] == [(1, second), (2, first)]
    changed = post(pid, "set_ref_order", {"node_id": "target", "role_overrides": {first: "product"}})
    assert changed.status_code == 200, changed.text
    assert [(r["n"], r["asset_id"]) for r in changed.json()["refs"]] == [(1, first), (2, second)]
    assert store.find_canvas_node(pid, "target")["reference_asset_ids"] == [first, second]
    unknown = post(pid, "set_ref_order", {"node_id": "target", "order": [assets["base"]]})
    assert unknown.status_code == 400 and "REF_NOT_ATTACHED" in unknown.text
