"""A7 integration through the isolated store and in-process REST/MCP transports."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools as tools
from app.services.agent_host import AgentSession, agent_host_manager
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_node_state import ownership_fields
from firefly_projects import ProjectStore


class LayoutTests(unittest.TestCase):
    def setUp(self):
        qa_projects = os.environ.get("FIREFLY_PROJECTS_ROOT")
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="a7-test-", dir=qa_projects))
        self.store = ProjectStore(Path(tmp) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.enterContext(patch.object(main, "FIREFLY_PROJECT_STORE", self.store))
        self.client = self.enterContext(TestClient(main.app, base_url="http://127.0.0.1:3197"))
        self.pid = self.store.create_project("synthetic A7")["id"]
        tab = object()
        register_project_session(tab, self.pid)
        self.addCleanup(unregister_project_session, tab)
        self.write({"id": "agent", "type": "smart-agent", "x": -1000, "y": 0},
                   {"id": "other", "type": "smart-agent", "x": -500, "y": 0})

    def write(self, *nodes, edges=()):
        mutations = [{"op": "upsert_node", "node": n} for n in nodes]
        mutations.extend({"op": "upsert_edge", "edge": e} for e in edges)
        self.store.apply_mutations(self.pid, mutations, base_revision=self.canvas()["revision"])

    @staticmethod
    def card(identity, x=0, y=0, *, owner="agent", kind="smart-prompt", w=316, h=240, **extra):
        return {"id": identity, "type": kind, "title": identity, "x": x, "y": y, "w": w, "h": h,
                **(ownership_fields(owner) if owner is not None else {}), **extra}

    def canvas(self):
        return self.store.canvas(self.pid)

    def nodes(self):
        return {n["id"]: n for n in self.canvas()["nodes"]}

    def request(self, tool, args, *, mcp=False, owner="agent"):
        if mcp:
            reply = self.client.post(f"/api/mcp?project={self.pid}&node={owner}", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": f"canvas.{tool}", "arguments": args},
            })
            self.assertEqual(reply.status_code, 200, reply.text)
            res = reply.json()["result"]
            return res["structuredContent"] if res.get("isError") else json.loads(res["content"][0]["text"])
        reply = self.client.post(f"/api/projects/{self.pid}/canvas/{tool}?agent_node_id={owner}", json=args,
                                 headers={"X-Agent-Project": self.pid})
        return reply.json().get("detail") if reply.status_code >= 400 else reply.json()

    def ok(self, tool, args, *, mcp=False, owner="agent"):
        result = self.request(tool, args, mcp=mcp, owner=owner)
        self.assertNotIn("code", result, result)
        return result

    def create(self, title, ids=(), *, x=100, y=100, mcp=False, **extra):
        return self.ok("group_create", {"title": title, "node_ids": list(ids), "anchor": {"x": x, "y": y},
                                        **extra}, mcp=mcp)

    def undo(self, op_id):
        return self.client.post(f"/api/projects/{self.pid}/ops/undo", json={"op_id": op_id},
                                headers={"X-Agent-Project": self.pid}).json()

    def test_main_and_a_plus_row_grid_visible_fit_and_atomic_undo(self):
        edge = {"id": "ab", "from": "a", "to": "b", "kind": "input"}
        # h=140 is the saved placeholder, but the browser enforces a 178px min-height.
        self.write(self.card("a", kind="smart-image", w=136, h=140), self.card("b", w=400, h=270),
                   self.card("c", w=260, h=250), self.card("d", w=300, h=420),
                   self.card("e", w=350, h=295), self.card("protected", 37, 49, owner=""), edges=[edge])
        before = self.canvas()
        main_group = self.create("Main", ["a", "b", "c", "d", "protected"], plan_section="主图")
        self.assertEqual(main_group["skipped"], [{"id": "protected", "reason": "USER_NODE"}])
        a_plus = self.create("A+", ["e"], x=180, y=960, mcp=True)
        g1, g2 = main_group["group_id"], a_plus["group_id"]
        initial = self.nodes()
        self.assertEqual(initial[g1]["items"], ["a", "b", "c", "d"])
        self.assertIs(initial[g1]["layoutFrame"], True)
        self.assertEqual(initial[g1]["planSection"], "主图")
        self.assertEqual(initial["d"]["y"], initial["b"]["y"] + 270 + 16)
        self.assertEqual(initial["b"]["x"], initial["a"]["x"] + 300 + 16)
        args = {"group_ids": [g1, g2], "direction": "row", "gap": 32}
        arranged = self.ok("arrange_groups", args, mcp=True)
        after = self.nodes()
        self.assertEqual(after[g1]["y"], after[g2]["y"])
        self.assertEqual(after[g2]["x"], after[g1]["x"] + after[g1]["w"] + 32)
        self.assertEqual(after["e"]["x"] - after[g2]["x"], initial["e"]["x"] - initial[g2]["x"])
        self.assertEqual(after["e"]["y"] - after[g2]["y"], initial["e"]["y"] - initial[g2]["y"])
        for group in (after[g1], after[g2]):
            for identity in group["items"]:
                member = after[identity]
                self.assertGreaterEqual(member["x"], group["x"] + 20)
                self.assertGreaterEqual(member["y"], group["y"] + 60)
                self.assertLessEqual(member["x"] + member["w"], group["x"] + group["w"] - 20)
                self.assertLessEqual(member["y"] + max(178, member["h"]), group["y"] + group["h"] - 20)
        self.assertEqual(after["protected"], initial["protected"])
        self.assertEqual(self.canvas()["connections"], [edge])
        self.assertEqual(set(arranged["touched_node_ids"]), {g2, "e"})
        revision = self.canvas()["revision"]
        self.assertNotIn("op_id", self.ok("arrange_groups", args))
        self.assertEqual(self.canvas()["revision"], revision)
        self.assertEqual(self.undo(arranged["op_id"])["reverted"], [arranged["op_id"]])
        self.assertEqual(self.nodes(), initial)
        self.assertEqual(self.undo(main_group["op_id"])["status"], "reverted")
        for card in before["nodes"]:
            if card["id"] != "e":
                self.assertEqual(card, self.nodes()[card["id"]])
        self.assertNotIn(g1, self.nodes())
        self.assertEqual(self.canvas()["connections"], [edge])

    def test_undo_refuses_entire_group_move_if_a_member_was_edited(self):
        self.write(self.card("a"), self.card("b"))
        first = self.create("one", ["a"])["group_id"]
        second = self.create("two", ["b"], x=800, y=900)["group_id"]
        result = self.ok("arrange_groups", {"group_ids": [first, second], "direction": "row"})
        self.write({**self.nodes()["b"], "x": 901, "userTouchedAt": 100})
        before = self.canvas()
        self.assertEqual(self.undo(result["op_id"])["detail"]["code"], "UNDO_CONFLICT")
        self.assertEqual(self.canvas(), before)

    def test_grid_uses_css_minimum_and_sort_orders(self):
        self.write(self.card("b", kind="smart-image", w=136, h=140, planOrder=2),
                   self.card("a", kind="smart-image", w=136, h=140, planOrder=3),
                   self.card("c", kind="smart-image", w=136, h=140, planOrder=1),
                   self.card("z", kind="smart-image", w=136, h=140, planOrder=4))
        group_id = self.create("sizes", ["b", "a", "c", "z"])["group_id"]
        args = {"group_id": group_id, "layout": "grid", "sort_by": "plan_order", "gap": 7}
        self.ok("group_arrange", args, mcp=True)
        n = self.nodes()
        self.assertEqual(n["z"]["y"], n["c"]["y"] + 178 + 7)
        self.assertEqual(n["c"]["x"], n["z"]["x"])
        self.assertLess(n["c"]["x"], n["b"]["x"])
        self.assertLess(n["b"]["x"], n["a"]["x"])
        self.assertNotIn("op_id", self.ok("group_arrange", args))
        self.ok("group_arrange", {**args, "layout": "column", "sort_by": "title"})
        n = self.nodes()
        self.assertEqual(n["c"]["y"], n["a"]["y"] + 2 * (178 + 7))
        self.assertEqual(n[group_id]["h"], 60 + 4 * 178 + 3 * 7 + 20)
        self.assertEqual(n["c"]["h"], 140)

    def test_same_title_reuse_requires_owner_and_mode_and_does_not_add_members(self):
        self.write(self.card("a"), self.card("b"),
                   self.card("old", kind="smart-group", title="Main", items=[]),
                   self.card("chat", kind="smart-group", title="Main", chatFrame=True, items=[]),
                   self.card("foreign", kind="smart-group", title="Main", owner="other", layoutFrame=True, items=[]))
        group = self.create(" Main ", ["a"])
        initial = self.canvas()
        replay = self.create("Main", ["a", "b"], x=900, y=900, mcp=True)
        self.assertEqual(replay["group_id"], group["group_id"])
        self.assertEqual(replay["skipped"], [{"id": "b", "reason": "NOT_IN_REUSED_GROUP"}])
        self.assertNotIn("op_id", replay)
        self.assertEqual(initial, self.canvas())
        for identity in ("old", "chat"):
            err = self.request("group_arrange", {"group_id": identity, "layout": "row"})
            self.assertEqual(err["details"], {"group_id": identity})
            self.assertIn("canvas.group_create", err["hint"])
        self.assertEqual(self.request("group_arrange", {"group_id": "missing", "layout": "row"})["code"], "NODE_NOT_FOUND")
        self.assertEqual(self.request("group_create", {"title": "nested", "node_ids": [group["group_id"]]})["code"], "INVALID_ARGUMENT")

    def test_protected_members_skip_in_group_and_block_indirect_movement(self):
        self.write(self.card("a"), self.card("b"), self.card("c"), self.card("d"),
                   self.card("old", kind="smart-group", items=[]))
        occupied = self.create("occupied", ["a", "b", "c"])["group_id"]
        free = self.create("free", ["d"], x=1200, y=900)["group_id"]
        n = self.nodes()
        self.write({**n["a"], "userTouchedAt": 100}, {**n["b"], "agentNodeId": "other"})
        before = self.nodes()
        result = self.ok("group_arrange", {"group_id": occupied, "layout": "row"})
        self.assertEqual(result["skipped"], [{"id": "a", "reason": "USER_NODE"}, {"id": "b", "reason": "OTHER_AGENT"}])
        n = self.nodes()
        self.assertEqual(n["a"], before["a"])
        self.assertEqual(n["b"], before["b"])
        self.assertGreaterEqual(n["c"]["y"], n["a"]["y"] + n["a"]["h"] + 16)
        result = self.ok("arrange_groups", {"group_ids": [free, occupied, "old"], "direction": "column"})
        self.assertEqual(result["skipped"], [{"id": occupied, "reason": "PROTECTED_MEMBER"},
                                             {"id": "old", "reason": "LEGACY_GROUP_USE_GROUP_CREATE"}])
        self.assertNotIn("op_id", result)
        self.assertEqual(self.nodes(), n)

    def test_alignment_distribution_default_skip_and_retries(self):
        self.write(self.card("a", 20, 10), self.card("b", 400, 40, w=350), self.card("c", 1100, 50),
                   self.card("user", 100, 60, owner=""), self.card("legacy", 30, 80, owner=None))
        ids = ["a", "b", "c", "user", "legacy"]
        aligned = self.ok("align", {"node_ids": ids, "edge": "top"}, mcp=True)
        self.assertEqual(aligned["moved"], ["b", "c"])
        self.assertEqual([x["id"] for x in aligned["skipped"]], ["user", "legacy"])
        self.assertNotIn("op_id", self.ok("align", {"node_ids": ids, "edge": "top"}))
        args = {"node_ids": ids, "axis": "x"}
        self.ok("distribute", args)
        n = self.nodes()
        self.assertAlmostEqual(n["b"]["x"] - n["a"]["x"] - 316, n["c"]["x"] - n["b"]["x"] - 350)
        self.assertEqual((n["a"]["x"], n["c"]["x"], n["user"]["y"], n["legacy"]["y"]), (20, 1100, 60, 80))
        self.assertNotIn("op_id", self.ok("distribute", args, mcp=True))
        self.ok("distribute", {**args, "gap": 0})
        n = self.nodes()
        self.assertEqual((n["b"]["x"], n["c"]["x"]), (336, 686))
        self.assertEqual(self.request("distribute", {"axis": "x", "node_ids": ["a", "b"]})["code"], "TOO_FEW_NODES")

    def confirmation(self, *, approved=True, before_response=None, autonomy="cautious", named=()):
        events = []
        session = AgentSession(node_id="agent", project_id=self.pid, autonomy=autonomy)
        session._current_turn_id = "hosted-turn"
        session._turn_layout_refs = set(named)
        self.enterContext(patch.dict(agent_host_manager.sessions, {f"{self.pid}:agent": session}))
        async def on_event(session, event):
            if event["type"] == "confirm":
                events.append(event)
                if before_response:
                    before_response()
                for handler in list(session.confirm_handlers):
                    await handler(event["confirm_id"], approved)
        session.event_handlers.append(on_event)
        self.enterContext(patch.object(agent_host_manager, "get_or_create_session", new=AsyncMock(return_value=session)))
        self.enterContext(patch.object(agent_host_manager, "_persist_event"))
        return events

    def test_named_user_confirmation_is_shared_and_other_agent_never_included(self):
        self.write(self.card("a", 0, 50), self.card("manual", 200, 13, owner=""),
                   self.card("other-card", 400, 30, owner="other"))
        events = self.confirmation()
        args = {"node_ids": ["a", "manual", "other-card"], "edge": "top", "include_user_nodes": True}
        aligned = self.ok("align", args, mcp=True)
        self.assertEqual(aligned["moved"], ["manual"])
        self.assertEqual([n["id"] for n in events[0]["params"]["nodes"]], ["manual"])
        self.assertEqual(events[0]['target_node_ids'], ['manual'])
        self.assertIn("manual", events[0]["message"])
        self.assertEqual(self.nodes()["other-card"]["y"], 30)
        self.assertNotIn("op_id", self.ok("align", args))
        self.assertEqual(len(events), 1)
        self.assertEqual(self.undo(aligned["op_id"])["status"], "reverted")
        self.assertEqual(self.nodes()["manual"]["y"], 13)
        grouped = self.create("named", ["a", "manual"], include_user_nodes=True)
        self.assertEqual([n["id"] for n in events[1]["params"]["nodes"]], ["manual"])
        self.assertEqual(events[1]['target_node_ids'], ['a', 'manual'])
        self.assertEqual(self.nodes()[grouped["group_id"]]["items"], ["a", "manual"])

    def test_rejection_and_autonomous_named_only(self):
        self.write(self.card("a", 1, 30), self.card("user", 7, 20, owner=None),
                   self.card("unmentioned", 8, 40, owner=""))
        before = self.canvas()
        self.confirmation(approved=False)
        args = {"node_ids": ["a", "user"], "edge": "left", "include_user_nodes": True}
        self.assertEqual(self.request("align", args)["code"], "CONFIRM_REJECTED")
        self.assertEqual(self.canvas(), before)
        events = self.confirmation(approved=False, autonomy="autonomous", named=["user"])
        self.assertEqual(self.request("align", args, mcp=True)["code"], "CONFIRM_REJECTED")
        self.assertEqual(len(events), 1)  # External MCP cannot borrow hosted refs.
        unverified = {**args, "node_ids": ["a", "unmentioned"]}
        self.assertEqual(self.request("align", unverified)["code"], "CONFIRM_REJECTED")
        self.assertEqual(len(events), 2)  # A tool argument alone is not evidence of a user mention.
        result = self.ok("align", args)
        self.assertEqual(result["moved"], ["user"])
        self.assertEqual(len(events), 2)
        self.assertEqual(self.nodes()["unmentioned"]["x"], 8)

    def test_hosted_layout_names_belong_to_the_active_input_turn(self):
        session = AgentSession(node_id="agent", project_id=self.pid)
        self.enterContext(patch.dict(agent_host_manager.sessions, {f"{self.pid}:agent": session}))
        self.enterContext(patch.object(agent_host_manager, "_send_prompt_now", new=AsyncMock()))
        self.enterContext(patch.object(agent_host_manager, "send_command", new=AsyncMock()))
        self.enterContext(patch.object(agent_host_manager, "_persist_event"))
        async def scenario():
            await agent_host_manager._start_logical_turn(session, "layout", node_refs=[{"node_id": "named"}])
            turn = session._current_turn_id
            self.assertEqual(agent_host_manager.layout_named_nodes(self.pid, "agent", turn), {"named"})
            await agent_host_manager.send_steer(session, "also this", node_refs=[{"node_id": "steered"}])
            self.assertEqual(agent_host_manager.layout_named_nodes(self.pid, "agent", turn), {"named", "steered"})
            await agent_host_manager._start_logical_turn(session, "new turn")
            self.assertEqual(agent_host_manager.layout_named_nodes(self.pid, "agent", turn), set())
            self.assertEqual(agent_host_manager.layout_named_nodes(self.pid, "agent", session._current_turn_id), set())
        asyncio.run(scenario())

    def test_stale_confirmation_and_cas_conflict_have_no_partial_write(self):
        self.write(self.card("a", 100, 30), self.card("b", 300, 60, owner=""))
        args = {"node_ids": ["a", "b"], "edge": "top", "include_user_nodes": True}
        events = self.confirmation(before_response=lambda: self.write({**self.nodes()["b"], "y": 72}))
        self.assertEqual(self.request("align", args)["code"], "CONFIRM_STALE")
        self.assertEqual(self.nodes()["b"]["y"], 72)
        self.assertEqual(len(events), 1)
        self.confirmation()
        notified = []
        async def concurrent_edit(_project, **kwargs):
            notified.append(kwargs)
            if kwargs.get("phase") == "start":
                self.write({**self.nodes()["a"], "title": "Concurrent edit", "userTouchedAt": 50})
        with patch.object(tools, "_broadcast_canvas_updated", side_effect=concurrent_edit):
            self.assertEqual(self.request("align", args, mcp=True)["code"], "CONFIRM_STALE")
        self.assertEqual([n["phase"] for n in notified], ["start", "end"])
        self.assertEqual(notified[-1]["touched_node_ids"], [])
        self.assertEqual(self.nodes()["b"]["y"], 72)
        self.assertEqual(self.nodes()["a"]["title"], "Concurrent edit")
        ops = self.client.get(f"/api/projects/{self.pid}/ops").json()["operations"]
        self.assertEqual(ops, [])
        fresh = self.ok("align", args)
        self.assertEqual(fresh["moved"], ["b"])
        self.assertEqual(self.nodes()["b"]["y"], 30)

    def test_concurrent_same_title_creation_conflicts_then_reuses_winner(self):
        self.write(self.card("a"))
        args = {"title": "race", "node_ids": ["a"]}
        winner = None
        started = False
        async def race(_project, **kwargs):
            nonlocal started, winner
            if kwargs.get("phase") == "start" and not started:
                started = True
                with agent_ops_scope(project_id=self.pid, agent_node_id="agent", tool="canvas.group_create", args=args):
                    winner = await tools.layout_tool_core(self.pid, "agent", "canvas.group_create", args)
        with patch.object(tools, "_broadcast_canvas_updated", side_effect=race):
            self.assertEqual(self.request("group_create", args)["code"], "CANVAS_CONFLICT")
        self.assertEqual([n["id"] for n in self.nodes().values() if n.get("layoutFrame")], [winner["group_id"]])
        replay = self.ok("group_create", args, mcp=True)
        self.assertEqual(replay["group_id"], winner["group_id"])
        self.assertNotIn("op_id", replay)
        ops = self.client.get(f"/api/projects/{self.pid}/ops").json()["operations"]
        self.assertEqual([op["op_id"] for op in ops], [winner["op_id"]])

    def test_schema_and_generic_mode_are_fail_closed(self):
        self.write(self.card("a"))
        bad_args = [
            ("group_create", []), ("group_create", {"title": "x", "anchor": {"x": 5}}),
            ("group_create", {"title": "x", "anchor": {"x": True, "y": 0}}),
            ("group_create", {"title": "x", "layoutFrame": True}),
            ("align", {"node_ids": ["a", "a"], "edge": "left"}),
            ("align", {"node_ids": ["a", {}], "edge": "left"}),
            ("align", {"node_ids": ["a", "b"], "edge": "left", "include_user_nodes": "yes"}),
            ("group_arrange", {"group_id": "x", "layout": {}, "gap": -1}),
            ("distribute", {"node_ids": ["a", "b"], "axis": "x", "gap": 10**50}),
            ("arrange_groups", {"group_ids": ["a"], "direction": "row", "agent_node_id": "other"}),
        ]
        before = self.canvas()
        for name, args in bad_args:
            with self.subTest(tool=name, args=args):
                self.assertEqual(self.request(name, args)["code"], "INVALID_ARGUMENT")
                self.assertEqual(self.request(name, args, mcp=True)["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.request("group_create", {"title": "wrong"}, owner="a")["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.request("create_node", {"node": {"type": "smart-group", "layoutFrame": True}}, mcp=True)["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.request("update_node", {"node_id": "a", "updates": {"layoutFrame": True}}, mcp=True)["code"], "INVALID_ARGUMENT")
        for method, route, payload in (
            ("POST", "nodes", {"node": {"type": "smart-group", "layoutFrame": True}, "agent_node_id": "agent"}),
            ("PATCH", "nodes/a?agent_node_id=agent", {"layoutFrame": True}),
        ):
            reply = self.client.request(method, f"/api/projects/{self.pid}/canvas/{route}", json=payload,
                                        headers={"X-Agent-Project": self.pid})
            self.assertEqual(reply.status_code, 400, reply.text)
            detail = reply.json()["detail"]
            self.assertEqual(detail["code"], "INVALID_ARGUMENT")
            self.assertIn("layoutFrame", detail["message"])
        self.assertEqual(before, self.canvas())
