"""Legacy admission and live status handling against the real isolated project store."""
import asyncio
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from app.routers import api_project_library
from firefly_projects import ProjectStore
from tests.conversation_legacy_admission import with_legacy_conversation_admission


@pytest.fixture
def api():
    # Deliberately do not run the application's migration/agent-host lifespan.
    with tempfile.TemporaryDirectory(dir=os.environ.get("FIREFLY_PROJECTS_ROOT")) as directory:
        store = ProjectStore(Path(directory), snapshot_delay=0.01)
        runner = main.ProjectConversationTaskRunner()

        @asynccontextmanager
        async def lifespan(_app):
            await runner.start()
            try:
                yield
            finally:
                await runner.stop()

        app = FastAPI(lifespan=lifespan)
        app.include_router(api_project_library.router)

        async def offline_provider(_request):
            return {"text": "offline reply"}

        with patch.object(main, "FIREFLY_PROJECT_STORE", store), \
             patch.object(main, "PROJECT_CONVERSATION_RUNNER", runner), \
             patch.object(main, "project_conversation_adapter", side_effect=offline_provider):
            with TestClient(
                with_legacy_conversation_admission(app),
                base_url="http://127.0.0.1",
                headers={"origin": "http://127.0.0.1"},
            ) as client:
                yield client, store, runner
        store.close()


def seed(api, *, messages=None, outboxes=None, **fields):
    client, store, _runner = api
    project_id = store.create_project("ownership protocol")["id"]
    node = {"id": "conversation", "type": "smart-conversation", "messages": messages or [], **fields}
    if outboxes is not None:
        node["pendingActionResumes"] = outboxes
    store.apply_mutations(project_id, [{"op": "upsert_node", "node": node}], base_revision=0)
    store.flush(project_id)
    return project_id, f"/api/project-library/{project_id}/conversation/conversation/messages"


def local(request_id, text="original"):
    return {"id": f"local-{request_id}", "role": "user", "request_id": request_id,
            "status": "pending", "text": text, "created_at": 7}


def outbox(project_id, request_id, *, human=True, text="original", **overrides):
    return {"actionId": f"send-{request_id}", "kind": "conversation", "payload": {
        "conversationProtocol": 1, "projectId": project_id, "nodeId": "conversation",
        "requestId": request_id, "text": text, "humanIntent": human, **overrides,
    }, "enqueuedAt": 7}


def save_node(api, project_id, **fields):
    client = api[0]
    canvas = client.get(f"/api/project-library/{project_id}").json()["canvas"]
    node = {**canvas["nodes"][0], **fields}
    response = client.patch(f"/api/project-library/{project_id}/canvas", json={
        "base_revision": canvas["revision"], "mutations": [{"op": "upsert_node", "node": node}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True
    return response.json()


@pytest.mark.parametrize("field", ["createdBy", "agentNodeId", "userTouchedAt", "promptEditedByUser", "created_at", "updated_at", "createdAt", "updatedAt"])
def test_request_rejects_client_owned_time_ownership_and_prompt_flags(api, field):
    project_id, endpoint = seed(api)
    response = api[0].post(endpoint, json={"text": "original", "request_id": "forged", field: 123})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ARGUMENT"
    assert {"hint", "retryable"} <= response.json()["detail"].keys()
    assert opened_node(api, project_id)["messages"] == []


@pytest.mark.parametrize("original_flag", [True, False])
@pytest.mark.parametrize("change", [{"text": "changed"}, {"flip_flag": True}])
def test_registered_key_cannot_be_reused_with_different_text_or_flag(api, original_flag, change):
    user = {"id": "receipt", "role": "user", "status": "completed", "request_id": "immutable",
            "text": "original", "created_at": 111, "user_edit": original_flag}
    assistant = {"id": "reply", "role": "assistant", "request_id": "immutable", "text": "reply"}
    project_id, endpoint = seed(api, messages=[user, assistant], userTouchedAt=111)
    body = {"text": "original", "request_id": "immutable", "user_edit": original_flag}
    if "text" in change:
        body["text"] = change["text"]
    else:
        body["user_edit"] = not original_flag
    response = api[0].post(endpoint, json=body)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ARGUMENT"
    assert opened_node(api, project_id)["messages"] == [user, assistant]
    assert opened_node(api, project_id)["userTouchedAt"] == 111


@pytest.mark.parametrize("request_id", [None, "", "   "])
def test_human_registration_requires_original_nonempty_request_id(api, request_id):
    project_id, endpoint = seed(api)
    body = {"text": "original", "user_edit": True}
    if request_id is not None:
        body["request_id"] = request_id
    response = api[0].post(endpoint, json=body)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ARGUMENT"
    assert opened_node(api, project_id)["messages"] == []


@pytest.mark.parametrize("legacy", [True, False])
def test_existing_receipt_cleans_matching_leftovers_without_admission_or_touch(api, legacy):
    user = {"id": "existing", "role": "user", "request_id": "existing", "status": "completed", "text": "original", "created_at": 50}
    if not legacy:
        user["user_edit"] = True
    assistant = {"id": "reply", "role": "assistant", "request_id": "existing", "text": "reply"}
    project_id, endpoint = seed(api, messages=[local("existing"), user, assistant, local("other")],
                                createdBy="agent", agentNodeId="owner", promptEditedByUser=True)
    other_kind = {"kind": "video", "payload": {"requestId": "existing"}}
    leftovers = [outbox(project_id, "existing"), outbox(project_id, "other"), other_kind]
    save_node(api, project_id, pendingActionResumes=leftovers, title="new dirty title")
    api[2].accepting = False
    with patch.object(main, "PROJECT_CONVERSATION_MAX_PER_NODE", 0), \
         patch.object(main, "now_ms", return_value=9999):
        response = api[0].post(endpoint, json={"text": "original", "request_id": "existing", "user_edit": True})
    assert response.status_code == 200, response.text
    assert response.json()["message"] == user
    node = opened_node(api, project_id)
    assert node["messages"] == [user, assistant, local("other")]
    assert node["pendingActionResumes"] == leftovers[1:]
    assert "userTouchedAt" not in node
    assert (node["createdBy"], node["agentNodeId"], node["promptEditedByUser"], node["title"]) == ("agent", "owner", True, "new dirty title")


@pytest.mark.parametrize("legacy", [False, True])
def test_get_formal_receipt_consumes_saved_leftovers_without_restamping(api, legacy):
    user = {"id": "formal", "role": "user", "request_id": "existing", "status": "completed", "text": "original", "created_at": 50}
    if not legacy:
        user["user_edit"] = True
    project_id, endpoint = seed(api, messages=[local("existing"), user, local("other")],
                                userTouchedAt=88, promptEditedByUser=True, createdBy="agent", agentNodeId="owner")
    leftovers = [outbox(project_id, "existing"), outbox(project_id, "other"),
                 {"kind": "video", "payload": {"requestId": "existing"}}]
    save_node(api, project_id, pendingActionResumes=leftovers, title="keep title")
    api[2].accepting = False
    with patch.object(main, "now_ms", return_value=9999):
        response = api[0].get(f"{endpoint}/existing")
        assert response.status_code == 200, response.text
        assert response.json()["message"] == user
        assert response.json()["saved"] is True
    node = opened_node(api, project_id)
    assert node["messages"] == [user, local("other")]
    assert node["pendingActionResumes"] == leftovers[1:]
    assert (node["userTouchedAt"], node["promptEditedByUser"], node["createdBy"], node["agentNodeId"], node["title"]) == (88, True, "agent", "owner", "keep title")
    disk = ProjectStore(api[1].root)
    try:
        saved = disk.open_project(project_id)["canvas"]["nodes"][0]
        assert saved["pendingActionResumes"] == leftovers[1:]
        assert saved["messages"] == [user, local("other")]
        assert saved["userTouchedAt"] == 88
    finally:
        disk.close()


def test_status_get_never_recreates_a_receipt_evicted_after_its_first_read(api):
    user = {"id": "formal", "role": "user", "request_id": "evicted", "status": "completed",
            "text": "original", "created_at": 50, "user_edit": False}
    project_id, endpoint = seed(api, messages=[user], userTouchedAt=88)
    original = main.project_store_call
    evicted = False

    def evict_after_read(method, *args, **kwargs):
        nonlocal evicted
        result = original(method, *args, **kwargs)
        if method == "open_project" and not evicted:
            evicted = True
            node = {**result["canvas"]["nodes"][0], "messages": []}
            api[1].apply_mutations(project_id, [{"op": "upsert_node", "node": node}], base_revision=result["canvas"]["revision"])
            api[1].flush(project_id)
        return result

    with patch.object(main, "project_store_call", side_effect=evict_after_read):
        response = api[0].get(f"{endpoint}/evicted")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CANVAS_CONFLICT"
    node = opened_node(api, project_id)
    assert node["messages"] == []
    assert node["userTouchedAt"] == 88


def test_boolean_protocol_version_does_not_authorize_a_human_registration(api):
    project_id, endpoint = seed(api, messages=[local("version")])
    mark = outbox(project_id, "version", conversationProtocol=True)
    save_node(api, project_id, pendingActionResumes=[mark])
    response = api[0].post(endpoint, json={"text": "original", "request_id": "version", "user_edit": True})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CANVAS_CONFLICT"
    node = opened_node(api, project_id)
    assert node["messages"] == [local("version")]
    assert node["pendingActionResumes"] == [mark]
    assert "userTouchedAt" not in node


def test_legacy_formal_receipt_without_id_or_new_flag_is_still_existing(api):
    user = {"role": "user", "request_id": "old", "status": "completed", "text": "legacy"}
    assistant = {"role": "assistant", "request_id": "old", "text": "old reply"}
    project_id, endpoint = seed(api, messages=[user, assistant])
    api[2].accepting = False
    response = api[0].post(endpoint, json={"text": "legacy", "request_id": "old", "user_edit": True})
    assert response.status_code == 200, response.text
    assert response.json()["message"] == user
    assert "userTouchedAt" not in opened_node(api, project_id)


@pytest.mark.parametrize("method", ["post", "get"])
def test_flush_failure_replay_must_confirm_disk_without_restamping(api, method):
    project_id, endpoint = seed(api, messages=[local("flush")])
    save_node(api, project_id, pendingActionResumes=[outbox(project_id, "flush")])
    store = api[1]
    store.snapshot_delay = 60
    body = {"text": "original", "request_id": "flush", "user_edit": True}
    with patch.object(store, "flush", side_effect=main.ProjectStoreError("injected disk failure")), \
         patch.object(main, "now_ms", return_value=1000):
        first = api[0].post(endpoint, json=body)
        assert first.status_code == 400, first.text
        candidate = opened_node(api, project_id)["messages"][0]
        replay = api[0].post(endpoint, json=body) if method == "post" else api[0].get(f"{endpoint}/flush")
        assert replay.status_code == 400, replay.text
    with patch.object(main, "now_ms", return_value=2000):
        confirmed = api[0].post(endpoint, json=body) if method == "post" else api[0].get(f"{endpoint}/flush")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["saved"] is True
        result = wait_terminal(api, endpoint, "flush")
    assert result["message"]["id"] == candidate["id"]
    assert result["message"]["created_at"] == 1000
    assert result["message"]["user_edit"] is True
    assert opened_node(api, project_id)["userTouchedAt"] == 1000
    disk = ProjectStore(store.root)
    try:
        assert disk.open_project(project_id)["canvas"]["nodes"][0]["userTouchedAt"] == 1000
    finally:
        disk.close()


@pytest.mark.parametrize("human", [True, False])
@pytest.mark.parametrize("override", [{"text": "changed"}, {"projectId": "other-project"}, {"nodeId": "other-node"}, {"humanIntent": "true"}, {"flip_flag": True}])
def test_new_outbox_cannot_change_original_text_flag_or_location(api, human, override):
    project_id, endpoint = seed(api, messages=[local("mismatch")])
    if "flip_flag" in override:
        override = {"humanIntent": not human}
    mark = outbox(project_id, "mismatch", human=human, **override)
    save_node(api, project_id, pendingActionResumes=[mark])
    response = api[0].post(endpoint, json={"text": "original", "request_id": "mismatch", "user_edit": human})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ARGUMENT"
    node = opened_node(api, project_id)
    assert node["messages"] == [local("mismatch")]
    assert node["pendingActionResumes"] == [mark]
    assert "userTouchedAt" not in node


@pytest.mark.parametrize("human_flag", [None, False])
def test_automatic_outbox_registration_retains_old_false_api_compatibility(api, human_flag):
    project_id, endpoint = seed(api, messages=[local("auto")], userTouchedAt=88,
                                createdBy="agent", agentNodeId="owner", promptEditedByUser=True)
    save_node(api, project_id, pendingActionResumes=[outbox(project_id, "auto", human=False)])
    body = {"text": "original", "request_id": "auto", "references": [{"ignored": "legacy extra"}], "legacy_extra": 1}
    if human_flag is not None:
        body["user_edit"] = human_flag
    response = api[0].post(endpoint, json=body)
    assert response.status_code == 200, response.text
    result = wait_terminal(api, endpoint, "auto")
    assert result["message"]["user_edit"] is False
    assert result["message"]["references"] == []
    node = opened_node(api, project_id)
    assert node["userTouchedAt"] == 88
    assert node["pendingActionResumes"] == []
    assert (node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]) == ("agent", "owner", True)


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], {}])
def test_user_edit_requires_a_json_boolean(api, flag):
    project_id, endpoint = seed(api)
    response = api[0].post(endpoint, json={"text": "original", "request_id": "strict", "user_edit": flag})
    assert response.status_code == 422, response.text
    assert opened_node(api, project_id)["messages"] == []


@pytest.mark.parametrize("reject_after_registration", [False, True])
def test_429_distinguishes_admission_rejection_from_registered_receipt(api, reject_after_registration):
    project_id, endpoint = seed(api, messages=[local("queue")])
    mark = outbox(project_id, "queue")
    save_node(api, project_id, pendingActionResumes=[mark])
    calls = []

    async def provider(request):
        calls.append(request)
        return {"text": "must not run"}

    original_flush = api[1].flush

    def shutdown_at_durable_save(*args, **kwargs):
        result = original_flush(*args, **kwargs)
        api[2].accepting = False
        return result

    with patch.object(main, "now_ms", return_value=1000), \
         patch.object(main, "project_conversation_adapter", side_effect=provider):
        if reject_after_registration:
            with patch.object(api[1], "flush", side_effect=shutdown_at_durable_save):
                response = api[0].post(endpoint, json={"text": "original", "request_id": "queue", "user_edit": True})
        else:
            with patch.object(main, "PROJECT_CONVERSATION_MAX_PER_NODE", 0):
                response = api[0].post(endpoint, json={"text": "original", "request_id": "queue", "user_edit": True})
        assert response.status_code == 429, response.text
        lookup = api[0].get(f"{endpoint}/queue")
    node = opened_node(api, project_id)
    assert calls == []
    if reject_after_registration:
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["status"] == "failed"
        assert lookup.json()["message"]["user_edit"] is True
        assert lookup.json()["message"]["created_at"] == 1000
        assert node["userTouchedAt"] == 1000
        assert node["pendingActionResumes"] == []
        with patch.object(main, "now_ms", return_value=2000):
            replay = api[0].post(endpoint, json={"text": "original", "request_id": "queue", "user_edit": True})
        assert replay.status_code == 200, replay.text
        assert replay.json()["message"] == lookup.json()["message"]
        assert opened_node(api, project_id)["userTouchedAt"] == 1000
    else:
        assert lookup.status_code == 404, lookup.text
        assert node["messages"] == [local("queue")]
        assert node["pendingActionResumes"] == [mark]
        assert "userTouchedAt" not in node


def test_get_mixed_local_and_formal_legacy_pending_uses_only_formal_turn(api):
    formal = {"id": "formal", "role": "user", "status": "pending", "request_id": "mixed", "text": "formal text", "created_at": 20}
    project_id, endpoint = seed(api, messages=[local("mixed"), formal, local("next")])
    calls = []

    async def provider(request):
        calls.append(request)
        return {"text": "formal reply"}

    with patch.object(main, "project_conversation_adapter", side_effect=provider):
        first = api[0].get(f"{endpoint}/mixed")
        assert first.status_code == 200, first.text
        assert first.json()["message"]["id"] == "formal"
        result = wait_terminal(api, endpoint, "mixed")
    assert result["message"]["id"] == "formal"
    assert result["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["messages"] == [formal]
    node = opened_node(api, project_id)
    assert node["messages"][0]["id"] == "formal"
    assert local("next") in node["messages"]
    assert all(item.get("id") != "local-mixed" for item in node["messages"])
    assert "userTouchedAt" not in node
    assert "user_edit" not in result["message"]


def test_registered_messages_keep_existing_500_message_limit(api):
    history = [{"id": f"history-{index}", "role": "assistant", "status": "completed", "text": "old history"} for index in range(500)]
    project_id, endpoint = seed(api, messages=history)
    save_node(api, project_id, pendingActionResumes=[outbox(project_id, "window")])
    response = api[0].post(endpoint, json={"text": "original", "request_id": "window", "user_edit": True})
    assert response.status_code == 200, response.text
    assert len(response.json()["canvas"]["nodes"][0]["messages"]) == 500
    result = wait_terminal(api, endpoint, "window")
    node = opened_node(api, project_id)
    assert len(node["messages"]) == 500
    assert node["messages"][-2:] == [result["message"], result["assistant"]]


def test_legacy_automatic_request_without_key_still_gets_a_server_key(api):
    project_id, endpoint = seed(api)
    response = api[0].post(endpoint, json={"text": "old automatic caller", "legacy_extra": "ignored"})
    assert response.status_code == 200, response.text
    request_id = response.json()["request_id"]
    assert request_id
    result = wait_terminal(api, endpoint, request_id)
    assert result["message"]["user_edit"] is False
    assert "userTouchedAt" not in opened_node(api, project_id)


@pytest.mark.parametrize("evicted", [False, True])
def test_human_without_saved_outbox_or_retained_receipt_is_a_conflict(api, evicted):
    project_id, endpoint = seed(api, messages=[local("lost")])
    if evicted:
        save_node(api, project_id, pendingActionResumes=[outbox(project_id, "lost")])
        with patch.object(main, "now_ms", return_value=1000):
            first = api[0].post(endpoint, json={"text": "original", "request_id": "lost", "user_edit": True})
            assert first.status_code == 200, first.text
            wait_terminal(api, endpoint, "lost")
        history = [{"id": f"history-{index}", "role": "assistant", "status": "completed", "text": "later history"} for index in range(500)]
        save_node(api, project_id, messages=history)
    before = opened_node(api, project_id)
    with patch.object(main, "now_ms", return_value=2000):
        response = api[0].post(endpoint, json={"text": "original", "request_id": "lost", "user_edit": True})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CANVAS_CONFLICT"
    assert opened_node(api, project_id) == before
    assert api[0].get(f"{endpoint}/lost").status_code == 404
    if evicted:
        assert len(before["messages"]) == 500
        assert before["userTouchedAt"] == 1000


def test_queue_stopping_before_locked_admission_does_not_register(api):
    project_id, endpoint = seed(api, messages=[local("stopping")])
    mark = outbox(project_id, "stopping")
    save_node(api, project_id, pendingActionResumes=[mark])
    original_open = api[1].open_project
    reads = 0

    def stop_before_registration(*args, **kwargs):
        nonlocal reads
        reads += 1
        result = original_open(*args, **kwargs)
        if reads == 2:
            api[2].accepting = False
        return result

    with patch.object(api[1], "open_project", side_effect=stop_before_registration):
        response = api[0].post(endpoint, json={"text": "original", "request_id": "stopping", "user_edit": True})
    assert response.status_code == 503, response.text
    node = opened_node(api, project_id)
    assert node["messages"] == [local("stopping")]
    assert node["pendingActionResumes"] == [mark]
    assert "userTouchedAt" not in node


def test_unsaved_outbox_disk_failure_does_not_register_or_stamp(api):
    project_id, endpoint = seed(api, messages=[local("unsaved")])
    api[1].snapshot_delay = 60
    canvas = api[0].get(f"/api/project-library/{project_id}").json()["canvas"]
    mark = outbox(project_id, "unsaved")
    response = api[0].patch(f"/api/project-library/{project_id}/canvas", json={
        "base_revision": canvas["revision"], "mutations": [{"op": "upsert_node", "node": {
            **canvas["nodes"][0], "pendingActionResumes": [mark],
        }}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is False
    with patch.object(api[1], "flush", side_effect=main.ProjectStoreError("outbox not durable")):
        rejected = api[0].post(endpoint, json={"text": "original", "request_id": "unsaved", "user_edit": True})
    assert rejected.status_code == 400, rejected.text
    node = opened_node(api, project_id)
    assert node["messages"] == [local("unsaved")]
    assert "userTouchedAt" not in node
    assert node["pendingActionResumes"] == [mark]


def test_exhausted_registration_conflicts_keep_original_outbox_and_no_touch(api):
    project_id, endpoint = seed(api, messages=[local("busy")])
    mark = outbox(project_id, "busy")
    save_node(api, project_id, pendingActionResumes=[mark])
    store = api[1]
    original_apply = store.apply_mutations

    def concurrent_edit_then_cas(*args, **kwargs):
        current = store.canvas(project_id)
        node = {**current["nodes"][0], "title": f"edit at {current['revision']}"}
        original_apply(project_id, [{"op": "upsert_node", "node": node}], base_revision=current["revision"])
        return original_apply(*args, **kwargs)

    with patch.object(store, "apply_mutations", side_effect=concurrent_edit_then_cas):
        response = api[0].post(endpoint, json={"text": "original", "request_id": "busy", "user_edit": True})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CANVAS_CONFLICT"
    node = opened_node(api, project_id)
    assert node["messages"] == [local("busy")]
    assert node["pendingActionResumes"] == [mark]
    assert "userTouchedAt" not in node


def test_registration_cas_retry_reuses_time_and_preserves_newer_node_edits(api):
    project_id, endpoint = seed(api, messages=[local("cas")], userTouchedAt=100,
                                createdBy="agent", agentNodeId="old-owner", promptEditedByUser=False)
    marks = [outbox(project_id, "cas"), outbox(project_id, "other", human=False)]
    save_node(api, project_id, pendingActionResumes=marks)
    store = api[1]
    original_apply = store.apply_mutations
    injected = False
    ticks = iter(range(1000, 100000, 1000))

    def concurrent_edit_then_cas(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            current = store.canvas(project_id)
            latest = {**current["nodes"][0], "userTouchedAt": 5000, "title": "newer edit",
                      "createdBy": "user", "agentNodeId": "new-owner", "promptEditedByUser": True,
                      "customDirtyField": {"keep": "latest"},
                      "conversation_adapter": {"provider": "new-provider", "model": "new-model"}}
            original_apply(project_id, [{"op": "upsert_node", "node": latest}], base_revision=current["revision"])
        return original_apply(*args, **kwargs)

    with patch.object(store, "apply_mutations", side_effect=concurrent_edit_then_cas), \
         patch.object(main, "now_ms", side_effect=lambda: next(ticks)):
        response = api[0].post(endpoint, json={"text": "original", "request_id": "cas", "user_edit": True})
        assert response.status_code == 200, response.text
        result = wait_terminal(api, endpoint, "cas")
    assert injected
    assert result["message"]["created_at"] == 1000
    node = opened_node(api, project_id)
    assert node["userTouchedAt"] == 5000
    assert (node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]) == ("user", "new-owner", True)
    assert node["title"] == "newer edit"
    assert node["customDirtyField"] == {"keep": "latest"}
    assert node["conversation_adapter"] == {"provider": "new-provider", "model": "new-model"}
    assert node["pendingActionResumes"] == marks[1:]


def test_concurrent_same_key_replays_have_one_durable_receipt_and_one_provider_turn(api):
    project_id, endpoint = seed(api, messages=[local("parallel")])
    save_node(api, project_id, pendingActionResumes=[outbox(project_id, "parallel")])
    calls = []
    release = threading.Event()
    started = threading.Event()
    barrier = threading.Barrier(6)
    responses = []
    errors = []

    async def provider(request):
        calls.append(request)
        started.set()
        await asyncio.to_thread(release.wait, 4)
        return {"text": "one reply"}

    def post():
        try:
            barrier.wait(3)
            responses.append(api[0].post(endpoint, json={"text": "original", "request_id": "parallel", "user_edit": True}))
        except Exception as exc:
            errors.append(exc)

    with patch.object(main, "project_conversation_adapter", side_effect=provider), \
         patch.object(main, "now_ms", return_value=1234):
        threads = [threading.Thread(target=post) for _ in range(6)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            assert not errors, errors
            assert all(not thread.is_alive() for thread in threads)
            assert len(responses) == 6
            assert [response.status_code for response in responses] == [200] * 6, [response.text for response in responses]
            assert len({response.json()["message"]["id"] for response in responses}) == 1
            assert started.wait(2)
        finally:
            release.set()
        result = wait_terminal(api, endpoint, "parallel")
    assert result["status"] == "completed"
    assert len(calls) == 1
    node = opened_node(api, project_id)
    assert node["userTouchedAt"] == 1234
    assert len(node["messages"]) == 2
    assert node["pendingActionResumes"] == []


def test_first_human_registration_consumes_only_its_outbox_and_stamps_once(api):
    project_id, endpoint = seed(api, messages=[local("human"), local("other")],
                                createdBy="agent", agentNodeId="owner", promptEditedByUser=False)
    marks = [outbox(project_id, "human"), outbox(project_id, "other", human=False)]
    save_node(api, project_id, pendingActionResumes=marks)
    assert "userTouchedAt" not in opened_node(api, project_id)

    async def provider(_request):
        return {"text": "reply"}

    with patch.object(main, "now_ms", return_value=1000), \
         patch.object(main, "project_conversation_adapter", side_effect=provider):
        first = api[0].post(endpoint, json={"text": "original", "request_id": "human", "user_edit": True})
        assert first.status_code == 200, first.text
        receipt = first.json()["message"]
        assert receipt["user_edit"] is True
        assert receipt["created_at"] == 1000
        assert not receipt["id"].startswith("local-")
        assert first.json()["saved"] is True
        result = wait_terminal(api, endpoint, "human")
    with patch.object(main, "now_ms", return_value=2000):
        replay = api[0].post(endpoint, json={"text": "original", "request_id": "human", "user_edit": True})
    assert replay.status_code == 200, replay.text
    assert replay.json()["message"] == result["message"]
    node = opened_node(api, project_id)
    assert node["userTouchedAt"] == 1000
    assert (node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]) == ("agent", "owner", False)
    assert node["pendingActionResumes"] == marks[1:]
    assert [item["id"] for item in node["messages"] if item["role"] == "user"] == ["local-other", receipt["id"]]
    # Reopen through a fresh real store to verify persistence, not just the cache.
    disk = ProjectStore(api[1].root, snapshot_delay=60)
    try:
        assert disk.open_project(project_id)["canvas"]["nodes"][0] == node
    finally:
        disk.close()


def opened_node(api, project_id):
    response = api[0].get(f"/api/project-library/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()["canvas"]["nodes"][0]


def wait_terminal(api, endpoint, request_id):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = api[0].get(f"{endpoint}/{request_id}")
        if response.status_code == 200 and response.json()["status"] in {"completed", "failed"}:
            return response.json()
        time.sleep(0.01)
    pytest.fail(f"no terminal receipt: {response.text}")


def test_local_placeholders_do_not_take_quota_fifo_or_provider_history(api):
    history = {"id": "legacy-history", "role": "user", "text": "old history"}
    project_id, endpoint = seed(api, messages=[history, local("earlier")])
    calls = []

    async def provider(request):
        calls.append(request)
        return {"text": "registered reply"}

    with patch.object(main, "PROJECT_CONVERSATION_MAX_PER_NODE", 1), \
         patch.object(main, "project_conversation_adapter", side_effect=provider):
        response = api[0].post(endpoint, json={"text": "automatic", "request_id": "formal"})
        assert response.status_code == 200, response.text
        result = wait_terminal(api, endpoint, "formal")
    assert result["status"] == "completed"
    assert [request["request_id"] for request in calls] == ["formal"]
    assert [message["text"] for message in calls[0]["messages"]] == ["old history", "automatic"]
    assert "userTouchedAt" not in opened_node(api, project_id)


def test_get_local_only_is_not_a_registered_task_and_keeps_lifecycle_guard(api):
    project_id, endpoint = seed(api, messages=[local("only")])
    calls = []

    async def provider(request):
        calls.append(request)
        return {"text": "must not run"}

    with patch.object(main, "project_conversation_adapter", side_effect=provider):
        response = api[0].get(f"{endpoint}/only")
        assert response.status_code == 404, response.text
    assert calls == []
    assert "userTouchedAt" not in opened_node(api, project_id)
    assert api[0].delete(f"/api/project-library/{project_id}").status_code == 409
