"""Public canvas.update_node safety regression; no provider or Agent process."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from queue import Queue
from threading import Event

import pytest

import main
from app.constants import NODE_TYPE_PROMPT
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import env
from tests.test_agent_plan_apply_484 import drain, track_lifetime


class UpdateHTTP486:
    def __init__(self, environment, monkeypatch):
        self.client, self.store, self.host, (self.project, self.foreign) = environment
        self.store.apply_mutations(self.project, [{"op": "upsert_node", "node": {
            "id": "human-prompt", "type": NODE_TYPE_PROMPT, "x": 100, "y": 100,
            "title": "Human prompt", "text": "Keep the human-authored wording",
            "createdBy": "user", "agentNodeId": None,
            "userTouchedAt": 9876, "promptEditedByUser": True,
        }}], base_revision=self.store.canvas(self.project)["revision"])
        self.session = self.client.portal.call(self.host.get_or_create_session, self.project, "actor")
        self.session.autonomy = "autonomous"
        self.events = Queue()
        self.seen = []
        self.tasks = track_lifetime(monkeypatch)

        async def observe(_session, event):
            if event["type"] == "confirm" and event["tool"] == "canvas.update_node":
                self.seen.append(deepcopy(event))
                self.events.put(deepcopy(event))

        self.session.event_handlers.append(observe)
        monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 0)

    def update(self, patch, *, node="human-prompt", actor="actor"):
        return self.client.patch(f"/api/projects/{self.project}/canvas/nodes/{node}",
            params={"agent_node_id": actor}, headers={"X-Agent-Project": self.project}, json=patch)

    def node(self, node="human-prompt"):
        response = self.client.get(f"/api/projects/{self.project}/canvas/nodes/{node}")
        assert response.status_code == 200, response.text
        return response.json()

    def confirm(self, event, approved):
        response = self.client.post(f"/api/projects/{self.project}/agent-nodes/actor/confirm",
            json={"confirm_id": event["confirm_id"], "approved": approved})
        assert response.status_code == 200, response.text
        return response

    def settle(self):
        drain(self.client, self.tasks)

    def human_change(self, changes, *, node="human-prompt"):
        self.store.apply_mutations(self.project, [{"op": "upsert_node", "node": {**self.node(node), **changes}}],
            base_revision=self.store.canvas(self.project)["revision"])

    def add_owned(self):
        node = {"id": "owned-prompt", "type": NODE_TYPE_PROMPT, "title": "Agent prompt",
                "text": "An Agent-authored prompt", "createdBy": "agent", "agentNodeId": "actor",
                "userTouchedAt": None, "promptEditedByUser": False, "locked": False}
        self.store.apply_mutations(self.project, [{"op": "upsert_node", "node": node}],
            base_revision=self.store.canvas(self.project)["revision"])
        return node["id"]

    def operations(self):
        response = self.client.get(f"/api/projects/{self.project}/ops")
        assert response.status_code == 200, response.text
        return response.json()["operations"]


@pytest.fixture
def update_http_486(env, monkeypatch):
    http = UpdateHTTP486(env, monkeypatch)
    try:
        yield http
    finally:
        for event in http.seen:
            http.confirm(event, False)
        http.settle()


@pytest.fixture
def ownership_http_486(update_http_486):
    return update_http_486.client, update_http_486.project


def test_update_node_does_not_overwrite_human_text_without_approval(ownership_http_486):
    client, project_id = ownership_http_486
    url = f"/api/projects/{project_id}/canvas/nodes/human-prompt"
    before = client.get(url)
    assert before.status_code == 200, before.text

    response = client.patch(url, params={"agent_node_id": "actor"},
                            headers={"X-Agent-Project": project_id},
                            json={"text": "Agent replaced the protected text"})

    after = client.get(url)
    assert after.status_code == 200, after.text
    assert after.json() == before.json(), (
        "canvas.update_node changed human content without approval; "
        f"status={response.status_code}, response={response.text}, "
        f"before={before.json()}, after={after.json()}"
    )


def test_protected_update_returns_live_pending_even_when_autonomous(update_http_486):
    http = update_http_486
    before = http.node()
    response = http.update({"text": "Agent requested a reviewed replacement"})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["code"] == "CONFIRM_PENDING", receipt
    assert receipt["approved"] is False and receipt["reason"] == "pending"
    assert receipt["status"] == "awaiting_confirmation"
    event = http.events.get(timeout=5)
    assert event["confirm_id"] == receipt["confirm_id"]
    assert event["target_node_ids"] == ["human-prompt"]
    assert http.node() == before
    assert len(http.session.confirm_handlers) == 1


def test_approval_binds_exact_snapshot_and_patch_then_journals_and_undoes(update_http_486):
    http = update_http_486
    before = http.node()
    response = http.update({"text": "An explicitly approved replacement"})
    assert response.json()["code"] == "CONFIRM_PENDING", response.text
    event = http.events.get(timeout=5)
    params = event["params"]
    assert params["before_sha256"] == "956754c998ae22d56ae3f914e2b43fd9e1b4e38be02417e3269ccbd4fdba849e"
    assert params["patch_sha256"] == "fe6679bf1a5c1c7aa170aadb9fd040a3d69bc2383e2f4871cafcd37dea2a283a"
    assert params["patch"] == {"text": "An explicitly approved replacement"}
    assert params["fields"] == ["text"]
    assert params["before"] == {"text": {"present": True, "value": "Keep the human-authored wording"}}
    assert params["project_id"] == http.project and params["agent_node_id"] == "actor"
    assert params["node_id"] == "human-prompt" and params["tool"] == "canvas.update_node"
    assert http.node() == before and http.operations() == []
    http.confirm(event, True)
    http.settle()
    assert http.node()["text"] == "An explicitly approved replacement"
    operation, = http.operations()
    assert operation["op_id"] == params["op_id"]
    assert operation["tool"] == "canvas.update_node" and operation["agent_node_id"] == "actor"
    assert operation["touched_node_ids"] == ["human-prompt"]
    assert operation["args"] == {"node_id": "human-prompt", "updates": {"text": "An explicitly approved replacement"}}
    undo = http.client.post(f"/api/projects/{http.project}/ops/undo",
        headers={"X-Agent-Project": http.project}, json={"op_id": operation["op_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["reverted"] == [operation["op_id"]]
    assert http.node() == before
    assert not http.session.confirm_handlers


@pytest.mark.parametrize("changed_input", ["target", "ownership", "protection", "generation"])
def test_owned_update_rechecks_bound_inputs_after_start_before_cas(update_http_486, monkeypatch, changed_input):
    http = update_http_486
    node_id = http.add_owned()
    before = http.node(node_id)
    started, release, phases = Queue(), Event(), []
    normal = main.manager.broadcast_canvas_updated

    async def broadcast(project, *args, **kwargs):
        phases.append(deepcopy(kwargs))
        if kwargs.get("phase") == "start":
            started.put(kwargs)
            assert await asyncio.to_thread(release.wait, 5)
        return await normal(project, *args, **kwargs)

    monkeypatch.setattr(main.manager, "broadcast_canvas_updated", broadcast)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http.update, {"text": "Unapproved stale replacement"}, node=node_id)
        try:
            started.get(timeout=5)
            if changed_input == "target":
                http.store.apply_mutations(http.project, [{"op": "upsert_node", "node": {
                    **before, "text": "A newer human edit", "userTouchedAt": 10001,
                    "promptEditedByUser": True, "customField": {"keep": "human"},
                }}], base_revision=http.store.canvas(http.project)["revision"])
            elif changed_input in ("ownership", "protection"):
                changes = {"agentNodeId": "other"} if changed_input == "ownership" else {"locked": True}
                http.human_change(changes, node=node_id)
                assert http.node(node_id) == {**before, **changes}
            else:
                http.session._generation += 1
            winner = http.node(node_id)
            http.store.flush(http.project)
            path = http.store.root / http.project / "canvas.json"
            winner_disk = path.read_bytes()
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CONFIRM_STALE", response.text
    assert http.node(node_id) == winner and http.operations() == []
    assert path.read_bytes() == winner_disk
    assert next(node for node in json.loads(winner_disk)["nodes"] if node["id"] == node_id) == winner
    assert [phase["phase"] for phase in phases] == ["start", "end"]
    assert phases[0]["op_id"] == phases[1]["op_id"]
    assert phases[1]["touched_node_ids"] == []
    assert http.events.empty()


@pytest.mark.parametrize("mixed", [None, "title", "text"],
                         ids=["settings-only", "settings-and-title", "settings-and-text"])
def test_protected_settings_use_the_same_confirmation_and_undo(update_http_486, mixed):
    http = update_http_486
    http.human_change({"runSettings": {"model": "fixture", "count": 1, "quality": "auto"}})
    before = http.node()
    revision = http.store.canvas(http.project)["revision"]
    http.store.flush(http.project)
    path = http.store.root / http.project / "canvas.json"
    before_disk = path.read_bytes()
    patch = {"runSettings": {"count": 2}}
    if mixed:
        patch[mixed] = "A reviewed title" if mixed == "title" else "A reviewed prompt text"
    response = http.update(patch)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "CONFIRM_PENDING", response.text
    event = http.events.get(timeout=5)
    expected = {"runSettings": {"model": "fixture", "count": 2, "quality": "auto"}}
    if mixed:
        expected[mixed] = patch[mixed]
    assert event["params"]["patch"] == expected
    assert event["params"]["fields"] == sorted(expected)
    assert http.node() == before and http.operations() == []
    assert path.read_bytes() == before_disk
    http.confirm(event, True)
    http.settle()
    after = http.node()
    assert after == {**before, **expected, "updated_at": after["updated_at"]}
    disk = json.loads(path.read_bytes())
    assert disk["revision"] == http.store.canvas(http.project)["revision"] == revision + 1
    assert next(node for node in disk["nodes"] if node["id"] == "human-prompt") == after
    operation, = http.operations()
    undo = http.client.post(f"/api/projects/{http.project}/ops/undo",
        headers={"X-Agent-Project": http.project}, json={"op_id": operation["op_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["reverted"] == [operation["op_id"]]
    restored = next(node for node in json.loads(path.read_bytes())["nodes"] if node["id"] == "human-prompt")
    assert http.node() == restored == before


def test_empty_settings_patch_mixed_with_title_does_not_invent_a_stored_key(update_http_486):
    http = update_http_486
    before = http.node()
    assert "runSettings" not in before
    response = http.update({"title": "A reviewed title", "runSettings": {}})
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "CONFIRM_PENDING", response.text
    event = http.events.get(timeout=5)
    assert event["params"]["patch"] == {"title": "A reviewed title"}
    http.confirm(event, True)
    http.settle()
    after = http.node()
    assert after == {**before, "title": "A reviewed title", "updated_at": after["updated_at"]}
    assert "runSettings" not in after
    assert len(http.operations()) == 1
