"""#332: POST agent-nodes must persist the node into canvas.json (no evaporation on restart)."""
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore, RevisionConflict
from app.routers import api_agent_nodes
from app.services import agent_model_capabilities
from tests.test_agent_host_settings_421_424 import capabilities
from tests.test_agent_node_settings import SettingsHost


class InterleavingProjectStore(ProjectStore):
    """Schedule a real second writer immediately before a real CAS commit.

    No CAS outcomes are stubbed: the parent store checks revisions, writes to
    the temporary project and raises its own RevisionConflict.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.before_apply = None
        self.revision_conflicts = []

    def _interleave(self, apply, project_id, mutations, *, base_revision, **kwargs):
        hook, self.before_apply = self.before_apply, None
        if hook is not None:
            hook()
        try:
            return apply(project_id, mutations, base_revision=base_revision, **kwargs)
        except RevisionConflict as exc:
            self.revision_conflicts.append((base_revision, exc.canvas["revision"]))
            raise

    def apply_mutations(self, project_id, mutations, *, base_revision, **kwargs):
        return self._interleave(super().apply_mutations, project_id, mutations,
                                base_revision=base_revision, **kwargs)

    def apply_agent_host_mutations(self, project_id, mutations, *, base_revision, **kwargs):
        return self._interleave(super().apply_agent_host_mutations, project_id, mutations,
                                base_revision=base_revision, **kwargs)

    def concurrent_write(self, project_id, mutations, *, trusted=False):
        # A real second writer, bypassing only the scheduling hook, not CAS.
        apply = super().apply_agent_host_mutations if trusted else super().apply_mutations
        return apply(project_id, mutations, base_revision=self.canvas(project_id)["revision"])


def pi_node(node):
    """Mirror the Pi fixture's projections into its authoritative host maps."""
    return {**node, "agentCli": "pi",
            "agentHostSettings": {"pi": {"model": node["model"], "effort": None}},
            "agentHostSessions": {"pi": {key: deepcopy(node[key])
                                         for key in ("extSessionId", "branches", "current_branch")}}}


class TestAgentNodePersistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-332-")
        self.root = Path(self.temp.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("票332持久化")["id"]
        self.client = TestClient(main.app, base_url="http://127.0.0.1",
                                 headers={"origin": "http://127.0.0.1"})

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    async def _fake_spawn(self, session, model="", autonomy=""):
        session.status = "running"
        return True

    def _post(self, node_id, **extra):
        return self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": node_id, **extra},
        )

    def test_phantom_node_persisted_and_survives_reload(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("ghost-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("success"))
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next((n for n in canvas["nodes"] if n.get("id") == "ghost-1"), None)
        self.assertIsNotNone(node, "agent-nodes create must upsert into canvas.json")
        self.assertEqual(node.get("type"), "smart-agent")
        # 磁盘级：flush+新建 store 重载后仍在（=进程重启语义）
        self.store.flush(self.project_id)
        reopened = ProjectStore(self.root, snapshot_delay=0).open_project(self.project_id)
        self.assertTrue(any(n.get("id") == "ghost-1" for n in reopened["canvas"]["nodes"]))

    def test_existing_node_untouched(self):
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "keep-1", "type": "smart-agent", "kind": "agent", "x": 42, "y": 7,
                     "title": "我的卡", "agentCli": "pi", "custom": "keepme"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("keep-1")
        self.assertEqual(r.status_code, 200, r.text)
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next(n for n in canvas["nodes"] if n.get("id") == "keep-1")
        self.assertEqual(node.get("x"), 42)
        self.assertEqual(node.get("custom"), "keepme")
        self.assertEqual(node.get("title"), "我的卡")

    def test_response_echoes_agent_cli(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("echo-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "pi")

    def test_body_agent_cli_conflicts_with_node_attr_400(self):
        # 评审修正：已有节点属性=pi，body 带 codex → 400，不得静默无视/谎回显。
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "cx-conf", "type": "smart-agent", "kind": "agent", "x": 0, "y": 0, "agentCli": "pi"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("cx-conf", agentCli="codex")
        self.assertEqual(r.status_code, 400, r.text)
        detail = (r.json().get("detail") or {})
        self.assertEqual(detail.get("node_agent_cli"), "pi")
        self.assertEqual(detail.get("body_agent_cli"), "codex")

    def test_unknown_project_404_not_masked(self):
        # 评审修正：项目不存在必须 404 原样上报（不得被 upsert 通道吞成 409）。
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self.client.post("/api/projects/no-such-proj/agent-nodes", json={"node_id": "x-1"})
        self.assertIn(r.status_code, (404, 422), r.text)
        self.assertNotEqual(r.status_code, 409)

    def test_echo_reflects_actual_session_host(self):
        # 评审修正：回显=会话实际宿主，不是请求值。
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "echo-host", "type": "smart-agent", "kind": "agent", "x": 0, "y": 0, "agentCli": "pi"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("echo-host")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "pi")

    def test_phantom_nodes_grid_placement_not_stacked(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            self._post("grid-1")
            self._post("grid-2")
        canvas = self.store.open_project(self.project_id)["canvas"]
        pos = [(n.get("x"), n.get("y")) for n in canvas["nodes"]]
        self.assertEqual(len(set(pos)), len(pos), "phantom 节点必须错位摆放")

    def test_body_agent_cli_codex_persisted_and_unknown_400(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("cx-1", agentCli="codex")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "codex")
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next(n for n in canvas["nodes"] if n.get("id") == "cx-1")
        self.assertEqual(node.get("agentCli"), "codex")
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            bad = self._post("bad-1", agentCli="hacker")
        self.assertEqual(bad.status_code, 400)


class TestAgentSettingsCAS(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-settings-cas-392-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "projects"
        self.store = InterleavingProjectStore(self.root, snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.addCleanup(setattr, main, "FIREFLY_PROJECT_STORE", main.FIREFLY_PROJECT_STORE)
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("Settings CAS")["id"]
        self.node = pi_node({"id": "cas-agent", "type": "smart-agent", "x": 0, "y": 0,
                             "model": "old-model", "autonomy": "cautious", "prompt": "old prompt",
                             "createdBy": "agent", "agentNodeId": "creator", "userTouchedAt": None,
                             "promptEditedByUser": False, "updated_at": 17,
                             "branches": [{"id": "original", "session_file": "original.jsonl"},
                                          {"id": "other", "session_file": "other.jsonl"}],
                             "current_branch": "original", "extSessionId": "original"})
        self.store.apply_agent_host_mutations(self.project_id, [
            {"op": "upsert_node", "node": self.node},
            {"op": "upsert_node", "node": {"id": "keep", "type": "smart-prompt"}},
        ], base_revision=0)
        self.assert_saved_agent(self.node)
        self.host = SettingsHost()
        self.model_commands = self.enterContext(patch.object(self.host, "set_model", wraps=self.host.set_model))
        self.switch_commands = self.enterContext(patch.object(self.host, "switch_branch", wraps=self.host.switch_branch))
        self.enterContext(patch.object(api_agent_nodes, "agent_host_manager", self.host))
        # Same offline native capability directory as the host-settings tests.
        # The HTTP transaction still validates selection, performs RPC and commits.
        self.enterContext(patch.object(agent_model_capabilities, "get_capabilities", capabilities))
        self.client = TestClient(main.app, base_url="http://127.0.0.1:3115",
                                 headers={"origin": "http://127.0.0.1:3115"})
        self.addCleanup(self.client.close)

    def assert_saved_agent(self, expected):
        node = next(n for n in self.store.canvas(self.project_id)["nodes"] if n["id"] == "cas-agent")
        self.assertEqual(node, expected, "The trusted write must persist the complete Agent fixture")

    def write_agent(self, node):
        self.store.concurrent_write(self.project_id, [{"op": "upsert_node", "node": node}], trusted=True)
        self.assert_saved_agent(node)

    def post_setting(self, endpoint, body, *, conflicts=0):
        revision = self.store.canvas(self.project_id)["revision"]
        start = len(self.store.revision_conflicts)
        response = self.client.post(f"/api/projects/{self.project_id}/agent-nodes/cas-agent/{endpoint}", json=body)
        self.assertEqual(self.store.revision_conflicts[start:],
                         [(r, r + 1) for r in range(revision, revision + conflicts)], response.text)
        return response

    def add_concurrent_content(self):
        node = next(n for n in self.store.canvas(self.project_id)["nodes"] if n["id"] == "cas-agent")
        node.update(prompt="concurrent prompt", createdBy="user", agentNodeId=None,
                    userTouchedAt=555, promptEditedByUser=True)
        node["branches"].append({"id": "concurrent", "session_file": "concurrent.jsonl"})
        self.write_agent(pi_node(node))

    def test_switch_target_branch_deleted_during_retry_does_not_persist_stale_selection(self):
        def delete_branch():
            node = {**self.node, "branches": [self.node["branches"][0]]}
            self.write_agent(pi_node(node))

        self.store.before_apply = delete_branch
        response = self.post_setting("switch-branch", {"branch_id": "other", "user_edit": True}, conflicts=1)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "NOT_FOUND")
        node = next(n for n in self.store.canvas(self.project_id)["nodes"] if n["id"] == "cas-agent")
        self.assertEqual(node["current_branch"], "original")
        self.assertIsNone(node["userTouchedAt"])
        self.assertEqual([b["id"] for b in node["branches"]], ["original"])
        self.assertEqual(self.host.effects.count("switch"), 1)
        self.assertEqual(self.host.session.session_file, "other.jsonl")  # No fictitious host rollback.

    def test_retry_that_is_already_satisfied_does_not_add_a_touch_or_another_revision(self):
        for endpoint, value in (("model", "new-model"), ("autonomy", "standard"), ("switch-branch", "other")):
            with self.subTest(endpoint=endpoint):
                self.write_agent(self.node)
                before = self.store.canvas(self.project_id)
                field = "current_branch" if endpoint == "switch-branch" else endpoint

                def satisfy():
                    node = {**self.node, field: value, "userTouchedAt": 555, "promptEditedByUser": True}
                    if endpoint == "switch-branch":
                        node["extSessionId"] = "other"
                    self.write_agent(pi_node(node))

                self.store.before_apply = satisfy
                key = "branch_id" if endpoint == "switch-branch" else endpoint
                response = self.post_setting(endpoint, {key: value, "user_edit": True}, conflicts=1)
                self.assertEqual(response.status_code, 200, response.text)
                canvas = self.store.canvas(self.project_id)
                self.assertEqual(canvas["revision"], before["revision"] + 1)
                self.assertEqual(response.json()["canvas"], canvas)
                node = next(n for n in canvas["nodes"] if n["id"] == "cas-agent")
                self.assertEqual(node["userTouchedAt"], 555)
                self.assertTrue(node["promptEditedByUser"])

    def test_success_canvas_is_fresh_after_a_later_writer_not_paired_with_commit_revision(self):
        broadcast_revisions = []

        async def broadcast(project_id, updated_at, source=""):
            revision = self.store.canvas(project_id)["revision"]
            self.add_concurrent_content()
            broadcast_revisions.append(revision)

        requests = [("model", {"model": "new-model"}), ("autonomy", {"autonomy": "standard"}),
                    ("fork", {"entry_id": "entry-1", "name": "manual"}),
                    ("switch-branch", {"branch_id": "other"})]
        for endpoint, body in requests:
            with self.subTest(endpoint=endpoint):
                self.write_agent(self.node)
                before = self.store.canvas(self.project_id)
                broadcast_revisions.clear()
                with patch.object(main.manager, "broadcast_canvas_updated", broadcast):
                    response = self.post_setting(endpoint, {**body, "user_edit": True})
                self.assertEqual(broadcast_revisions, [before["revision"] + 1])
                self.assertEqual(response.status_code, 200, response.text)
                canvas = self.store.canvas(self.project_id)
                self.assertEqual(canvas["revision"], before["revision"] + 2)
                self.assertEqual(response.json()["canvas"], canvas)
                node = next(n for n in response.json()["canvas"]["nodes"] if n["id"] == "cas-agent")
                self.assertEqual(node["prompt"], "concurrent prompt")
                self.assertEqual(node["userTouchedAt"], 555)
                if endpoint == "fork":
                    self.assertEqual(response.json()["branches"], node["branches"])

    def test_project_deleted_during_post_commit_broadcast_returns_factory_404_without_host_rollback(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:3115",
                            headers={"origin": "http://127.0.0.1:3115"}, raise_server_exceptions=False)
        self.addCleanup(client.close)
        committed = []

        async def delete_after_commit(project_id, updated_at, source=""):
            committed.append(self.store.canvas(project_id))
            self.store.trash_project(project_id)

        requests = [("model", {"model": "new-model"}, ["session", "model"], {"model": "new-model"}),
                    ("autonomy", {"autonomy": "standard"}, ["session"], {"autonomy": "standard"}),
                    ("fork", {"entry_id": "entry-1", "name": "manual"}, ["session", "fork"],
                     {"session_file": "forked.jsonl", "session_id": "forked"}),
                    ("switch-branch", {"branch_id": "other"}, ["session", "switch"],
                     {"session_file": "other.jsonl"})]
        for endpoint, body, effects, host_values in requests:
            with self.subTest(endpoint=endpoint):
                self.project_id = self.store.create_project("Deleted after settings commit")["id"]
                self.write_agent(self.node)
                before = self.store.canvas(self.project_id)
                self.host.effects.clear()
                committed.clear()
                with patch.object(main.manager, "broadcast_canvas_updated", delete_after_commit):
                    response = client.post(f"/api/projects/{self.project_id}/agent-nodes/cas-agent/{endpoint}",
                                           json={**body, "user_edit": True})
                self.assertEqual(len(committed), 1)  # The real commit precedes the deletion hook.
                self.assertEqual(committed[0]["revision"], before["revision"] + 1)
                node = next(n for n in committed[0]["nodes"] if n["id"] == "cas-agent")
                if endpoint == "fork":
                    self.assertEqual(len(node["branches"]), 3)
                    self.assertEqual(node["branches"][-1]["session_file"], "forked.jsonl")
                    self.assertEqual(node["current_branch"], node["branches"][-1]["id"])
                else:
                    field = "current_branch" if endpoint == "switch-branch" else endpoint
                    self.assertEqual(node[field], body.get("branch_id", body.get(endpoint)))
                self.assertFalse((self.root / self.project_id).exists(), "Deleted project must not be resurrected")
                self.assertIn(self.project_id, [p["id"] for p in self.store.list_trashed_projects()])
                self.assertEqual(self.host.effects, effects)  # No repeated RPC or compensating host call.
                for field, value in host_values.items():
                    self.assertEqual(getattr(self.host.session, field), value)
                self.assertEqual(response.status_code, 404, response.text)
                detail = response.json()["detail"]
                self.assertEqual(set(detail), {"code", "message", "hint", "retryable"})
                self.assertEqual(detail["code"], "PROJECT_NOT_FOUND")
                self.assertTrue(detail["message"])
                self.assertTrue(detail["hint"])
                self.assertFalse(detail["retryable"])

    def test_model_and_autonomy_retry_keep_concurrent_prompt_ownership_and_branches(self):
        for endpoint, value in (("model", "new-model"), ("autonomy", "standard")):
            for user_edit in (False, True):
                with self.subTest(endpoint=endpoint, user_edit=user_edit):
                    self.write_agent(self.node)
                    self.store.before_apply = self.add_concurrent_content
                    with patch.object(main, "now_ms", return_value=777):
                        response = self.post_setting(endpoint, {endpoint: value, "user_edit": user_edit}, conflicts=1)
                    self.assertEqual(response.status_code, 200, response.text)
                    canvas = self.store.canvas(self.project_id)
                    node = next(n for n in canvas["nodes"] if n["id"] == "cas-agent")
                    self.assertEqual((node["prompt"], node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]),
                                     ("concurrent prompt", "user", None, True))
                    self.assertEqual(node["userTouchedAt"], 777 if user_edit else 555)
                    self.assertEqual([b["id"] for b in node["branches"]], ["original", "other", "concurrent"])
                    self.assertEqual(node[endpoint], value)
                    self.assertEqual(response.json()["canvas"], canvas)

    def test_conflict_exhaustion_returns_conflict_without_ownership_or_duplicate_rpc(self):
        requests = [("model", {"model": "new-model"}), ("autonomy", {"autonomy": "standard"}),
                    ("fork", {"entry_id": "entry-1", "name": "manual"}),
                    ("switch-branch", {"branch_id": "other"})]
        for endpoint, body in requests:
            with self.subTest(endpoint=endpoint):
                self.write_agent(self.node)
                before = self.store.canvas(self.project_id)
                self.host.effects.clear()
                self.model_commands.reset_mock()
                self.switch_commands.reset_mock()

                def conflict():
                    self.store.concurrent_write(self.project_id, [{"op": "upsert_node", "node": {
                        "id": "keep", "type": "smart-prompt", "prompt": "concurrent writer"}}])
                    self.store.before_apply = conflict

                self.store.before_apply = conflict
                try:
                    response = self.post_setting(endpoint, {**body, "user_edit": True}, conflicts=8)
                finally:
                    self.store.before_apply = None
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "CANVAS_CONFLICT")
                self.assertTrue(response.json()["detail"]["retryable"])
                canvas = self.store.canvas(self.project_id)
                self.assertEqual(canvas["revision"], before["revision"] + 8)
                self.assertEqual(next(n for n in canvas["nodes"] if n["id"] == "cas-agent"), self.node)
                if endpoint == "model":
                    self.assertEqual(self.host.session.model, self.node["model"])
                    self.assertEqual([args[1] for args, _ in self.model_commands.await_args_list],
                                     [body["model"], self.node["model"]])
                if endpoint == "fork":
                    self.assertEqual(self.host.effects.count("fork" if endpoint == "fork" else "switch"), 1)
                if endpoint == "switch-branch":
                    self.assertEqual([args[1] for args, _ in self.switch_commands.await_args_list],
                                     ["other.jsonl", "original.jsonl"])

    def test_target_deleted_during_retry_is_not_resurrected_or_reported_as_session_only(self):
        requests = [("model", {"model": "new-model"}), ("autonomy", {"autonomy": "standard"}),
                    ("fork", {"entry_id": "entry-1", "name": "manual"}),
                    ("switch-branch", {"branch_id": "other"})]
        for endpoint, body in requests:
            with self.subTest(endpoint=endpoint):
                self.write_agent(self.node)
                self.host.effects.clear()
                self.model_commands.reset_mock()
                self.switch_commands.reset_mock()
                self.store.before_apply = lambda: self.store.concurrent_write(
                    self.project_id, [{"op": "remove_node", "node_id": "cas-agent"}])
                response = self.post_setting(endpoint, {**body, "user_edit": True}, conflicts=1)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["detail"]["code"], "NODE_NOT_FOUND")
                canvas = self.store.canvas(self.project_id)
                self.assertEqual([n["id"] for n in canvas["nodes"]], ["keep"])
                if endpoint == "model":
                    # ADR 0024: even the legacy /model route compensates a failed CAS.
                    self.assertEqual(self.host.session.model, self.node["model"])
                    self.assertEqual([args[1] for args, _ in self.model_commands.await_args_list],
                                     [body["model"], self.node["model"]])
                if endpoint == "fork":
                    self.assertEqual(self.host.effects.count("fork" if endpoint == "fork" else "switch"), 1)
                if endpoint == "switch-branch":
                    self.assertEqual([args[1] for args, _ in self.switch_commands.await_args_list],
                                     ["other.jsonl", "original.jsonl"])

    def test_model_rollback_failure_is_explicit_after_node_deletion(self):
        with self.subTest(endpoint="model", rollback="unconfirmed"):
            before = self.store.canvas(self.project_id)
            self.store.before_apply = lambda: self.store.concurrent_write(
                self.project_id, [{"op": "remove_node", "node_id": "cas-agent"}])

            async def set_model(session, model):
                if model == self.node["model"]:
                    raise RuntimeError("native rollback unavailable")
                session.model = model

            self.model_commands.side_effect = set_model
            response = self.post_setting("model", {"model": "new-model", "user_edit": True}, conflicts=1)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(response.json()["detail"]["code"], "agent_rollback_failed")
            self.assertIn("native rollback unavailable", response.json()["detail"]["message"])
            self.assertEqual([args[1] for args, _ in self.model_commands.await_args_list],
                             ["new-model", self.node["model"]])
            canvas = self.store.canvas(self.project_id)
            self.assertEqual(canvas["revision"], before["revision"] + 1)
            self.assertEqual([n["id"] for n in canvas["nodes"]], ["keep"])
            self.assertEqual(self.host.session.effort_state["source"], "pi:rollback-unconfirmed")

    def test_switch_preserves_concurrent_content_and_noop_does_not_advance_revision(self):
        self.store.before_apply = self.add_concurrent_content
        with patch.object(main, "now_ms", return_value=888):
            response = self.post_setting("switch-branch", {"branch_id": "other", "user_edit": True}, conflicts=1)
        self.assertEqual(response.status_code, 200, response.text)
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas["nodes"] if n["id"] == "cas-agent")
        self.assertEqual((node["prompt"], node["createdBy"], node["agentNodeId"], node["promptEditedByUser"], node["userTouchedAt"]),
                         ("concurrent prompt", "user", None, True, 888))
        self.assertEqual([b["id"] for b in node["branches"]], ["original", "other", "concurrent"])
        self.assertEqual(response.json()["canvas"], canvas)
        self.assertEqual(response.json()["branch"], {"id": "other", "session_file": "other.jsonl"})
        self.assertEqual(response.json()["current_branch"], "other")
        self.assertEqual(self.host.effects.count("switch"), 1)
        response = self.post_setting("switch-branch", {"branch_id": "other", "user_edit": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["canvas"], canvas)
        self.assertEqual(self.store.canvas(self.project_id), canvas)
        # The original single-body-parameter HTTP shape remains supported.
        response = self.post_setting("switch-branch", "original")
        self.assertEqual(response.status_code, 200, response.text)
        node = next(n for n in response.json()["canvas"]["nodes"] if n["id"] == "cas-agent")
        self.assertEqual(node["current_branch"], "original")
        self.assertEqual(node["userTouchedAt"], 888)

    def test_fork_rebuilds_from_latest_canvas_without_repeating_host_rpc(self):
        self.store.before_apply = self.add_concurrent_content
        with patch.object(main, "now_ms", return_value=999):
            response = self.post_setting("fork", {"entry_id": "entry-1", "name": "manual fork", "user_edit": True},
                                         conflicts=1)
        self.assertEqual(response.status_code, 200, response.text)
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas["nodes"] if n["id"] == "cas-agent")
        self.assertEqual(node["prompt"], "concurrent prompt")
        self.assertEqual((node["createdBy"], node["agentNodeId"], node["promptEditedByUser"], node["userTouchedAt"]),
                         ("user", None, True, 999))
        data = response.json()
        self.assertEqual([b["id"] for b in node["branches"][:3]], ["original", "other", "concurrent"])
        self.assertEqual(len(node["branches"]), 4)
        self.assertEqual(node["branches"][-1], data["branch"])
        self.assertEqual(data["branch"]["name"], "manual fork")
        self.assertEqual(data["branches"], node["branches"])
        self.assertEqual(data["current_branch"], data["branch"]["id"])
        self.assertEqual(data["canvas"], canvas)
        self.assertEqual(self.host.effects.count("fork"), 1)
        self.store.flush(self.project_id)
        reopened = ProjectStore(self.root, snapshot_delay=0)
        try:
            self.assertEqual(reopened.canvas(self.project_id), canvas)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
