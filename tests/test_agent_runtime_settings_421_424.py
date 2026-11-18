"""Gate 2 runtime contracts: deterministic native-shaped hosts, never inference.

Shapes checked against pi-stable/src/modes/rpc/rpc-mode.ts and agent-session.ts:
set_model.data is the model object; set_thinking_level ACK has no data;
get_state has model{provider,id}, thinkingLevel, isStreaming, isCompacting,
pendingMessageCount (steer + follow-up). SDK mutations default to session-only.
"""
import asyncio
from copy import deepcopy
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

import main
from firefly_projects import ProjectStore
from app.services import agent_host as ah
from app.services.agent_adapters import CodexAdapter
from app.services.agent_node_host_settings import AgentHostTransaction, build_host_settings_fields


_PREVIOUS_LOOP = None


def setUpModule():
    global _PREVIOUS_LOOP
    try:
        _PREVIOUS_LOOP = asyncio.get_event_loop()
    except RuntimeError:
        _PREVIOUS_LOOP = None


def tearDownModule():
    # IsolatedAsyncioTestCase clears the current loop. Do not break older
    # synchronous suites that deliberately retain their original current loop.
    if _PREVIOUS_LOOP is not None and not _PREVIOUS_LOOP.is_closed():
        asyncio.set_event_loop(_PREVIOUS_LOOP)


class NativePi:
    """Blocking pipes exercised by the actual reader and RPC handler container."""
    def __init__(self, model="native/old", effort="low"):
        provider, ident = model.split("/", 1)
        self.state = {"model": {"provider": provider, "id": ident}, "thinkingLevel": effort,
                      "isStreaming": False, "isCompacting": False, "pendingMessageCount": 0,
                      "sessionId": "pi-native", "sessionFile": "/native/pi.jsonl"}
        self.commands = []
        self.frames = queue.Queue()
        self.returncode = None
        self.stdin = self.stdout = self
        self.stderr = io.StringIO()
        self.hook = None
        self.killed = False

    def write(self, line):
        command = json.loads(line)
        self.commands.append(command)
        kind = command["type"]
        response = {"id": command["id"], "type": "response", "command": kind, "success": True}
        if kind == "set_model":
            self.state["model"] = {"provider": command["provider"], "id": command["modelId"]}
            response["data"] = deepcopy(self.state["model"])
        elif kind == "set_thinking_level":
            self.state["thinkingLevel"] = command["level"]
        elif kind == "get_state":
            response["data"] = deepcopy(self.state)
        if self.hook:
            self.hook(command, response)
        self.frames.put(json.dumps(response) + "\n")

    def readline(self):
        return self.frames.get(timeout=5)

    def flush(self):
        pass

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.frames.put("")

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.frames.put("")

    def wait(self, timeout=None):
        return self.returncode


class CodexProcess:
    def __init__(self, argv, *, fail_resume=False):
        self.returncode = None
        self.exit_code = 1 if fail_resume and "resume" in argv else 0
        self.lines = ([] if self.exit_code else [
            {"type": "thread.started", "thread_id": "codex-native"},
            {"type": "turn.started"}, {"type": "turn.completed", "usage": {}},
        ])
        self.stdin = io.StringIO()
        self.stdout = self
        self.stderr = io.StringIO()
        self.killed = False

    def readline(self):
        if self.lines:
            return json.dumps(self.lines.pop(0)) + "\n"
        self.returncode = self.exit_code
        return ""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-421424-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "project.json").write_text("{}", encoding="utf-8")
        self.manager = ah.AgentHostManager()
        self.manager._get_project_dir = lambda project: str(self.root)
        self.session = await self.manager.get_or_create_session("project", "node")
        self.session.model = "native/old"
        self.session.reasoning_effort = "low"
        # Pin the fixture application: another suite may import agent_host
        # before main, when its module reference still points at pytest.
        self.enterContext(patch.object(ah, "_app_main", main))
        # No global store, credentials, warmup process or real native spawn.
        self.enterContext(patch.object(ah._app_main, "project_store_call", None))
        self.enterContext(patch.object(ah, "maybe_warm_codex", return_value=False))
        self.enterContext(patch.object(ah, "MAX_CONSECUTIVE_RESPAWNS", 0))
        self.enterContext(patch.object(ah.subprocess, "Popen", side_effect=AssertionError("real spawn forbidden")))
        self.addAsyncCleanup(self.manager.stop_session, self.session)

    async def attach_pi(self):
        process = NativePi()
        self.session.process = process
        self.session.status = "running"
        self.session._reader_task = asyncio.create_task(self.manager._read_events(self.session))
        await asyncio.sleep(0)
        return process

    def short_rpc(self):
        original = self.manager.run_command

        async def quick(session, command, timeout=10, **kwargs):
            return await original(session, command, timeout=0.025, **kwargs)
        return patch.object(self.manager, "run_command", side_effect=quick)

    async def test_initial_effort_is_requested_only_and_projection_is_fresh(self):
        self.assertEqual(self.session.effort_state, {"requested": "low", "submitted": None,
                                                   "effective": None, "source": None})
        self.session.effort_state["effective"] = "invented"
        self.assertIsNone(self.session.effort_effective)
        self.session.agent_cli = "codex"
        self.session.effort_effective = "invented"
        self.assertIsNone(self.session.effort_state["effective"])

    async def test_offline_and_codex_store_request_without_claiming_submission(self):
        for host in ("pi", "codex"):
            with self.subTest(host=host):
                self.session.agent_cli = host
                result = await self.manager.apply_runtime_settings(self.session, "native/new", "high")
                self.assertEqual(result["requested"], "high")
                self.assertIsNone(result["submitted"])
                self.assertIsNone(result["effective"])
        self.session.effort_submitted = "low"
        result = await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(result["submitted"], "low", "retain actual preceding turn snapshot")

    async def test_unproved_follow_default_never_discards_override(self):
        for host in ("pi", "codex"):
            self.session.agent_cli = host
            for model, effort in (("", "low"), ("native/old", None)):
                with self.subTest(host=host, model=model, effort=effort):
                    with self.assertRaisesRegex(RuntimeError, "default is unverified"):
                        await self.manager.apply_runtime_settings(self.session, model, effort)
                    self.assertEqual((self.session.model, self.session.reasoning_effort), ("native/old", "low"))

    async def test_stale_undo_expectation_never_reaches_live_native_reader_or_metadata(self):
        from fastapi import HTTPException
        process = await self.attach_pi()
        node = {"id": "node", "type": "smart-agent",
                **build_host_settings_fields({}, "pi", "native/newer", "high")}
        update = AsyncMock(side_effect=AssertionError("stale undo cannot save"))
        transaction = AgentHostTransaction(self.manager, "project", "node", find_node=lambda *_: node,
                                           update_settings=update, now_ms=lambda: 123)
        with patch.object(transaction, "capabilities", side_effect=AssertionError("stale undo cannot probe")):
            with self.assertRaises(HTTPException) as failure:
                await transaction.settings({"host": "pi", "model": "native/old", "effort": "low",
                    "expected": {"host": "pi", "model": "native/new", "effort": "high"}}, user_edit=True)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(failure.exception.detail["code"], "agent_settings_conflict")
        self.assertEqual(process.commands, [])
        update.assert_not_awaited()
        self.assertEqual((self.session.model, self.session.reasoning_effort), ("native/old", "low"))

    async def test_strict_native_pair_ack_and_readback(self):
        process = await self.attach_pi()
        result = await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(result, {"requested": "high", "submitted": "high", "effective": "high",
                                  "source": "pi:get_state"})
        self.assertEqual(self.session.model, "native/new")
        self.assertEqual(process.state["model"]["id"], "new")
        kinds = [c["type"] for c in process.commands]
        self.assertEqual(kinds[-3:], ["set_model", "set_thinking_level", "get_state"])
        self.assertEqual(len({c["id"] for c in process.commands}), len(process.commands))
        self.assertTrue(all("persist" not in c for c in process.commands))

    async def test_clamp_readback_restores_both_native_and_requested_values(self):
        process = await self.attach_pi()

        def clamp(command, response):
            if command["type"] == "set_thinking_level" and command["level"] == "high":
                process.state["thinkingLevel"] = "medium"
        process.hook = clamp
        with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
            await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(self.manager._runtime_values(process.state), ("native/old", "low"))
        self.assertEqual((self.session.model, self.session.reasoning_effort), ("native/old", "low"))
        self.assertEqual(self.session.effort_state["effective"], "low")

    async def test_explicit_error_and_mismatched_model_ack_are_failures_with_rollback(self):
        process = await self.attach_pi()
        for fault in ("error", "model"):
            def reject(command, response):
                if command["type"] == "set_model" and command["modelId"] == "new":
                    if fault == "error":
                        response.update(success=False, error="native rejected model")
                    else:
                        response["data"]["id"] = "wrong-model"
            process.hook = reject
            with self.subTest(fault=fault), self.assertRaisesRegex(RuntimeError, "failed|did not match"):
                await self.manager.apply_runtime_settings(self.session, "native/new", "high")
            self.assertEqual(self.manager._runtime_values(process.state), ("native/old", "low"))

    async def test_wrong_id_missing_id_and_wrong_command_never_ack_settings(self):
        process = await self.attach_pi()
        for fault in ("wrong", "missing", "command"):
            def wrong(command, response):
                if command["type"] == "set_thinking_level" and command["level"] == "high":
                    if fault == "missing":
                        response.pop("id")
                    elif fault == "wrong":
                        response["id"] = "previous-request"
                    else:
                        response["command"] = "get_state"
            process.hook = wrong
            with self.subTest(fault=fault), self.short_rpc(), self.assertRaises(TimeoutError):
                await self.manager.apply_runtime_settings(self.session, "native/new", "high")
            self.assertEqual(self.manager._runtime_values(process.state), ("native/old", "low"))
            self.assertFalse(process.killed, "settings timeout must not auto-respawn during rollback")

    async def test_get_state_error_is_reported_without_mutation(self):
        process = await self.attach_pi()
        process.hook = lambda command, response: response.update(success=False, error="state unavailable")
        with self.assertRaisesRegex(RuntimeError, "state unavailable"):
            await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual([c["type"] for c in process.commands], ["get_state"])
        self.assertEqual(self.session.model, "native/old")
        self.assertIsNone(self.session.effort_submitted)

    async def test_concurrent_same_command_rpcs_only_capture_their_exact_id(self):
        await self.attach_pi()
        commands = []
        async def hold(session, command):
            commands.append(command)
        with patch.object(self.manager, "send_command", side_effect=hold):
            first = asyncio.create_task(self.manager._runtime_rpc(self.session, {"type": "get_state"}))
            second = asyncio.create_task(self.manager._runtime_rpc(self.session, {"type": "get_state"}))
            while len(commands) < 2:
                await asyncio.sleep(0)
            for index in (1, 0):
                await self.manager._dispatch_event(self.session, {
                    "type": "response", "command": "get_state", "success": True,
                    "id": commands[index]["id"], "data": {"origin": index}})
            replies = await asyncio.gather(first, second)
        self.assertEqual([reply["data"]["origin"] for reply in replies], [0, 1])

    async def test_wrong_id_post_mutation_readback_rolls_back_instead_of_confirming(self):
        process = await self.attach_pi()
        reads = 0
        def stale(command, response):
            nonlocal reads
            if command["type"] == "get_state":
                reads += 1
                if reads == 3:
                    response["id"] = "old-readback"
        process.hook = stale
        with self.short_rpc(), self.assertRaises(TimeoutError):
            await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(self.manager._runtime_values(process.state), ("native/old", "low"))
        self.assertEqual(self.session.effort_state["requested"], "low")
        self.assertEqual(self.session.effort_state["effective"], "low")

    async def test_wrong_id_readback_cannot_confirm_effort(self):
        process = await self.attach_pi()
        self.session.model = ""
        self.session.reasoning_effort = None
        process.hook = lambda command, response: response.update(id="stale")
        with self.short_rpc(), self.assertRaises(TimeoutError):
            await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(self.session.effort_state, {"requested": None, "submitted": None,
                                                   "effective": None, "source": None})

    async def test_rollback_failure_is_explicit_and_effective_is_unconfirmed(self):
        process = await self.attach_pi()
        def broken(command, response):
            if command["type"] == "set_thinking_level":
                response.update(success=False, error="thinking rejected")
        process.hook = broken
        with self.assertRaisesRegex(RuntimeError, "runtime rollback failed"):
            await self.manager.apply_runtime_settings(self.session, "native/new", "high")
        self.assertEqual(self.session.model, "native/old")
        self.assertEqual(self.session.reasoning_effort, "low")
        self.assertIsNone(self.session.effort_state["effective"])

    async def test_idle_live_pi_is_not_busy_but_native_queues_and_compaction_are(self):
        process = await self.attach_pi()
        self.assertFalse(self.session.is_busy)
        self.assertFalse(await self.manager.is_session_busy(self.session))
        for key, value in (("isStreaming", True), ("isCompacting", True), ("pendingMessageCount", 2)):
            with self.subTest(key=key):
                process.state[key] = value
                self.assertTrue(await self.manager.is_session_busy(self.session))
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    await self.manager.apply_runtime_settings(self.session, "native/new", "high")
                process.state[key] = False if key != "pendingMessageCount" else 0
                self.assertFalse(await self.manager.is_session_busy(self.session))
        self.assertFalse(any(c["type"].startswith("set_") for c in process.commands))

    async def test_local_start_turn_stop_confirm_retry_and_finishing_are_busy(self):
        for status in ("starting", "busy", "waiting_confirm"):
            self.session.status = status
            self.assertTrue(await self.manager.is_session_busy(self.session))
        self.session.status = "running"
        for field in ("_turn_open", "_stopping", "_response_finishing", "_retrying"):
            setattr(self.session, field, True)
            self.assertTrue(self.session.is_busy)
            setattr(self.session, field, False)
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_handler(session, event):
            entered.set()
            await release.wait()
        self.session.event_handlers.append(slow_handler)
        task = asyncio.create_task(self.manager._dispatch_event(self.session, {"type": "agent_end"}))
        await entered.wait()
        self.assertTrue(self.session.is_busy)
        release.set()
        await task
        self.assertFalse(self.session.is_busy)

    async def test_retry_intent_blocks_the_gap_before_native_retry_start(self):
        await self.manager._dispatch_event(self.session, {"type": "agent_end", "willRetry": True})
        self.assertTrue(self.session.is_busy)
        await self.manager._dispatch_event(self.session, {"type": "auto_retry_end"})
        self.assertFalse(self.session.is_busy)

    async def test_native_busy_shape_errors_cannot_be_treated_as_idle(self):
        process = await self.attach_pi()
        process.state["pendingMessageCount"] = "0"
        with self.assertRaisesRegex(RuntimeError, "invalid busy/queue"):
            await self.manager.is_session_busy(self.session)

    async def test_pi_spawn_uses_effort_snapshot_not_concurrent_request_and_no_pretend(self):
        self.session.reasoning_effort = "high"
        self.session.process = None
        process = NativePi(effort="high")
        captured = []
        adapter = self.manager._adapters["pi"]
        build = adapter.build_argv
        def mutate(params):
            captured.append((params.model, params.reasoning_effort))
            argv = build(params)
            self.session.model, self.session.reasoning_effort = "native/later", "low"
            return argv
        with patch.object(adapter, "build_argv", side_effect=mutate), \
                patch.object(ah, "resolve_pi_cli", return_value="fake-pi.exe"), \
                patch.object(ah.subprocess, "Popen", return_value=process):
            self.assertTrue(await self.manager.spawn_session(self.session))
        self.assertEqual(captured, [("native/old", "high")])
        self.assertEqual(self.session.effort_state["requested"], "low")
        self.assertEqual(self.session.effort_state["effective"], "high")

    async def run_codex(self, *, binding=None, fail_resume=False, mutate=False):
        self.session.agent_cli = "codex"
        self.session.ext_session_id = binding
        self.session.model, self.session.reasoning_effort = "gpt-test", "high"
        captured = []
        manager, session = self.manager, self.session
        class RecordingAdapter(CodexAdapter):
            def build_argv(self, params):
                captured.append((params.model, params.reasoning_effort, params.ext_session_id))
                argv = super().build_argv(params)
                if mutate:
                    session.model, session.reasoning_effort = "gpt-later", "low"
                return argv
        manager._adapters["codex"] = RecordingAdapter()
        spawned = []
        def spawn(argv, **kwargs):
            spawned.append(list(argv))
            return CodexProcess(argv, fail_resume=fail_resume)
        with patch.object(ah.shutil, "which", return_value="fake-codex.exe"), \
                patch("app.services.agent_adapters.codex_adapter.codex_spawn_env", return_value={"CODEX_HOME": str(self.root / "home")}), \
                patch("app.services.agent_adapters.codex_adapter.write_home_mcp_config"), \
                patch.object(ah.subprocess, "Popen", side_effect=spawn):
            await manager._codex_turn(session, "fake prompt", ref_paths=[str(self.root / "ref.png")])
            for _ in range(200):
                task = session._reader_task
                if task is None:
                    break
                await asyncio.wait_for(asyncio.shield(task), 3)
            self.assertIsNone(session.process)
        return captured, spawned

    async def test_stop_during_codex_spawn_cannot_attach_late_process(self):
        self.session.agent_cli = "codex"
        entered, release = threading.Event(), threading.Event()
        process = CodexProcess([])
        def slow_spawn(argv, **kwargs):
            entered.set()
            release.wait(timeout=3)
            return process
        with patch.object(ah.shutil, "which", return_value="fake-codex.exe"), \
                patch("app.services.agent_adapters.codex_adapter.codex_spawn_env", return_value={"CODEX_HOME": str(self.root / "home")}), \
                patch("app.services.agent_adapters.codex_adapter.write_home_mcp_config"), \
                patch.object(ah.subprocess, "Popen", side_effect=slow_spawn):
            turn = asyncio.create_task(self.manager._codex_turn(self.session, "fake prompt"))
            await asyncio.to_thread(entered.wait, 1)
            self.assertTrue(self.session.is_busy)
            await self.manager.stop_session(self.session)
            release.set()
            with self.assertRaisesRegex(RuntimeError, "changed during codex spawn"):
                await asyncio.wait_for(turn, 2)
        self.assertTrue(process.killed)
        self.assertIsNone(self.session.process)
        self.assertIsNone(self.session.effort_submitted)
        self.assertEqual(self.session.status, "stopped")

    async def test_codex_first_and_resume_submit_real_snapshot_never_effective(self):
        for binding in (None, "old-thread"):
            with self.subTest(binding=binding):
                captured, spawned = await self.run_codex(binding=binding, mutate=True)
                self.assertEqual(captured, [("gpt-test", "high", binding)])
                argv = spawned[0]
                self.assertIn('model_reasoning_effort="high"', argv)
                self.assertIn("gpt-test", argv)
                if binding:
                    self.assertLess(argv.index('model_reasoning_effort="high"'), argv.index("resume"))
                self.assertEqual(self.session.effort_state["submitted"], "high")
                self.assertEqual(self.session.effort_state["requested"], "low")
                self.assertIsNone(self.session.effort_state["effective"])

    async def test_codex_failed_resume_replay_retry_reuses_pair_and_reference_snapshot(self):
        captured, spawned = await self.run_codex(binding="missing-thread", fail_resume=True, mutate=True)
        self.assertEqual(captured, [("gpt-test", "high", "missing-thread"), ("gpt-test", "high", None)])
        self.assertIn("resume", spawned[0])
        self.assertNotIn("resume", spawned[1])
        for argv in spawned:
            self.assertIn('model_reasoning_effort="high"', argv)
            self.assertIn(str(self.root / "ref.png"), argv)
        self.assertIsNone(self.session.effort_state["effective"])

    async def test_host_history_filter_legacy_pi_node_cursor_and_restart(self):
        path = self.root / "agent-events" / "node.jsonl"
        path.parent.mkdir()
        path.write_text(json.dumps({"seq": 7, "event": {"type": "user_prompt", "text": "legacy pi"}}) + "\n", encoding="utf-8")
        self.session.agent_cli = "codex"
        self.manager._persist_event(self.session, {"type": "user_prompt", "text": "codex only"})
        codex = self.manager.load_events("project", "node", host="codex")
        pi = self.manager.load_events("project", "node", host="pi")
        self.assertEqual([r["seq"] for r in codex["events"]], [8])
        self.assertEqual([r["seq"] for r in pi["events"]], [7])
        self.assertEqual(pi["last_seq"], 8)
        self.assertEqual(pi["events"][0]["event"]["host"], "pi")
        self.assertEqual(self.manager.load_events("project", "node", since=7, host="pi"), {"events": [], "last_seq": 8})
        block, turns = self.manager._build_replay_context(self.session)
        self.assertEqual(turns, 1)
        self.assertIn("codex only", block)
        self.assertNotIn("legacy pi", block)
        handlers = self.session.event_handlers
        await self.manager.stop_session(self.session)
        self.manager.select_runtime(self.session, "pi")
        self.assertIs(self.session.event_handlers, handlers)
        self.manager._persist_event(self.session, {"type": "user_prompt", "text": "pi again"})
        self.assertEqual(self.manager.load_events("project", "node")["last_seq"], 9)
        restarted = ah.AgentSession(project_id="project", node_id="node")
        self.manager._persist_event(restarted, {"type": "agent_end"})
        self.assertEqual(restarted._event_seq, 10)

    async def test_late_frames_and_old_pi_finally_cannot_mutate_or_kill_new_host(self):
        old = NativePi()
        self.session.process = old
        self.session.status = "running"
        entered, release = threading.Event(), threading.Event()
        def late_line():
            entered.set()
            release.wait(timeout=3)
            return json.dumps({"type": "agent_start"}) + "\n"
        old.readline = late_line
        seen = []
        self.session.event_handlers.append(lambda session, event: seen.append(event))
        reader = asyncio.create_task(self.manager._read_events(self.session, old, "pi", 0))
        await asyncio.to_thread(entered.wait, 1)
        # Deliberately violate caller locking to prove reader fencing itself.
        self.session._generation += 1
        self.session.agent_cli = "codex"
        replacement = NativePi()
        self.session.process = replacement
        release.set()
        await asyncio.wait_for(reader, 2)
        self.assertEqual(seen, [])
        self.assertTrue(old.killed)
        self.assertFalse(replacement.killed)
        self.assertEqual(self.session.status, "running")
        await self.manager._dispatch_event(self.session, {"type": "agent_start"}, host="pi", generation=0)
        self.assertEqual(seen, [])
        self.assertFalse((self.root / "agent-events" / "node.jsonl").exists())

    async def test_late_codex_thread_id_cannot_bind_replacement_pi(self):
        old = NativePi()
        self.session.agent_cli = "codex"
        self.session.process = old
        self.session.ext_session_id = "codex-old"
        entered, release = threading.Event(), threading.Event()
        def late_thread():
            entered.set()
            release.wait(timeout=3)
            return json.dumps({"type": "thread.started", "thread_id": "late-old-thread"}) + "\n"
        old.readline = late_thread
        reader = asyncio.create_task(self.manager._read_codex_turn(
            self.session, old, True, "fake", True, host="codex", generation=0))
        await asyncio.to_thread(entered.wait, 1)
        replacement = NativePi()
        self.session.process = replacement
        self.session.agent_cli = "pi"
        self.session.ext_session_id = "saved-pi"
        self.session._generation += 1
        release.set()
        with patch.object(self.manager, "_persist_ext_session_id") as persist:
            await asyncio.wait_for(reader, 2)
            persist.assert_not_called()
        self.assertEqual(self.session.ext_session_id, "saved-pi")
        self.assertFalse(replacement.killed)

    async def test_stop_awaits_reader_and_preserves_ws_container_without_respawn(self):
        await self.attach_pi()
        handlers = self.session.event_handlers
        reader = self.session._reader_task
        with patch.object(self.manager, "spawn_session", new_callable=AsyncMock) as spawn:
            await self.manager.stop_session(self.session)
            self.assertTrue(reader.done())
            spawn.assert_not_awaited()
        self.manager.select_runtime(self.session, "codex", "gpt-test", reasoning_effort="high", ext_session_id="saved-thread")
        self.assertIs(self.session.event_handlers, handlers)
        self.assertIsNone(self.session.effort_state["submitted"])
        self.assertFalse(self.session.is_busy)
        self.assertEqual(self.session.ext_session_id, "saved-thread")
        self.assertEqual(self.session.status, "stopped")

    async def test_stop_failed_codex_resume_does_not_start_degrade_retry(self):
        process = NativePi()
        self.session.agent_cli = "codex"
        self.session.process = process
        self.session._turn_open = True
        task = asyncio.create_task(self.manager._read_codex_turn(
            self.session, process, True, "fake", True, host="codex", generation=0))
        self.session._reader_task = task
        await asyncio.sleep(0)
        with patch.object(self.manager, "_codex_turn", new_callable=AsyncMock) as retry:
            await self.manager.stop_session(self.session)
            retry.assert_not_awaited()
        self.assertTrue(task.done())
        self.assertFalse(self.session.is_busy)

    async def test_select_runtime_refuses_unfinished_stop_or_live_idle_pi(self):
        await self.attach_pi()
        with self.assertRaisesRegex(RuntimeError, "finished stopping"):
            self.manager.select_runtime(self.session, "codex")
        self.session._stopping = True
        with self.assertRaises(RuntimeError):
            self.manager.select_runtime(self.session, "codex")


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bindings-421424-")
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name), snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project("e2e-tabbit-421424-runtime-bindings")["id"]
        node = {"id": "node", "type": "smart-agent", "agentCli": "pi", "model": "native/pi",
                "extSessionId": "pi-old", "branches": [{"id": "pi-branch"}], "current_branch": "pi-branch",
                "createdBy": "user", "userTouchedAt": 123, "agentNodeId": "owner", "promptEditedByUser": True}
        fields = build_host_settings_fields(node, "codex", "gpt-test", "high")
        fields["agentHostSessions"]["codex"] = {"extSessionId": "codex-old", "branches": [], "current_branch": ""}
        fields["extSessionId"] = "codex-old"
        self.node = {**node, **fields}
        self.store.apply_agent_host_mutations(self.project, [{"op": "upsert_node", "node": self.node}], base_revision=0)
        self.manager = ah.AgentHostManager()
        self.manager._get_project_dir = lambda project: str(self.store.root / project)
        self.session = ah.AgentSession(project_id=self.project, node_id="node", agent_cli="pi", ext_session_id="pi-new")
        self.enterContext(patch.object(ah, "_app_main", main))
        self.enterContext(patch.object(ah._app_main, "project_store_call", side_effect=self.call))

    def call(self, method, *args, **kwargs):
        return getattr(self.store, method)(*args, **kwargs)

    def saved(self):
        return self.store.canvas(self.project)["nodes"][0]

    def test_binding_updates_only_owning_host_and_atomic_active_projection(self):
        self.manager._persist_ext_session_id(self.session)
        saved = self.saved()
        self.assertEqual(saved["agentCli"], "codex")
        self.assertEqual(saved["extSessionId"], "codex-old")
        self.assertEqual(saved["agentHostSessions"]["pi"]["extSessionId"], "pi-new")
        self.assertEqual(saved["agentHostSessions"]["codex"]["extSessionId"], "codex-old")
        self.assertEqual(saved["agentHostSessions"]["pi"]["branches"], [{"id": "pi-branch"}])
        for key in ("createdBy", "userTouchedAt", "agentNodeId", "promptEditedByUser"):
            self.assertEqual(saved[key], self.node[key])

    def test_runtime_switch_round_trip_uses_each_persisted_binding_and_branch(self):
        handlers = self.session.event_handlers
        for host in ("pi", "codex", "pi", "codex"):
            canvas = self.store.canvas(self.project)
            saved = canvas["nodes"][0]
            fields = build_host_settings_fields(saved, host, "native/pi" if host == "pi" else "gpt-test", "high")
            self.store.apply_agent_host_mutations(self.project, [{"op": "upsert_node", "node": {**saved, **fields}}],
                                                  base_revision=canvas["revision"])
            self.manager.select_runtime(self.session, host, fields["model"], reasoning_effort="high",
                                        ext_session_id=fields["extSessionId"])
            self.assertEqual(self.session.ext_session_id, "pi-old" if host == "pi" else "codex-old")
            self.assertEqual(fields["current_branch"], "pi-branch" if host == "pi" else "")
            self.assertIs(self.session.event_handlers, handlers)
        self.session.ext_session_id = "codex-new"
        self.manager._persist_ext_session_id(self.session)
        self.assertEqual(self.saved()["agentHostSessions"]["pi"]["extSessionId"], "pi-old")
        self.assertEqual(self.saved()["agentHostSessions"]["codex"]["extSessionId"], "codex-new")

    def test_identity_snapshot_precedes_store_callbacks(self):
        def mutate(method, *args, **kwargs):
            self.session.agent_cli, self.session.ext_session_id = "codex", "late-codex"
            return self.call(method, *args, **kwargs)
        with patch.object(ah._app_main, "project_store_call", side_effect=mutate):
            self.manager._persist_ext_session_id(self.session)
        self.assertEqual(self.saved()["agentHostSessions"]["pi"]["extSessionId"], "pi-new")
        self.assertEqual(self.saved()["extSessionId"], "codex-old")

    def test_deleted_node_is_not_recreated_including_delete_between_read_and_cas(self):
        def delete_on_apply(method, *args, **kwargs):
            if method == "apply_agent_host_mutations":
                canvas = self.store.canvas(self.project)
                self.store.apply_mutations(self.project, [{"op": "remove_node", "node_id": "node"}],
                                           base_revision=canvas["revision"], force_empty=True)
            return self.call(method, *args, **kwargs)
        with patch.object(ah._app_main, "project_store_call", side_effect=delete_on_apply):
            self.manager._persist_ext_session_id(self.session)
        self.assertEqual(self.store.canvas(self.project)["nodes"], [])
        self.manager._persist_ext_session_id(self.session)
        self.assertEqual(self.store.canvas(self.project)["nodes"], [])

    def test_ordinary_undo_keeps_messages_and_settings_but_starts_a_new_pi_session(self):
        old_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        canvas = self.store.canvas(self.project)
        original = {**self.node, **build_host_settings_fields(self.node, "pi", "native/pi", "high"),
                    "messages": [{"role": "user", "content": "retained message"}]}
        original["agentHostSessions"]["pi"]["extSessionId"] = old_id
        original["extSessionId"] = old_id
        self.store.apply_agent_host_mutations(self.project, [{"op": "upsert_node", "node": original}],
                                             base_revision=canvas["revision"])
        canvas = self.store.canvas(self.project)
        self.store.apply_mutations(self.project, [{"op": "remove_node", "node_id": "node"}],
                                   base_revision=canvas["revision"], force_empty=True)
        self.store.apply_mutations(self.project, [{"op": "upsert_node", "node": original}],
                                   base_revision=self.store.canvas(self.project)["revision"])
        restored = self.saved()
        self.assertEqual(restored["messages"], original["messages"])
        self.assertEqual(restored["agentHostSettings"], original["agentHostSettings"])
        self.assertEqual(restored["agentHostSessions"]["pi"]["extSessionId"], "")
        session_dir = self.store.root / self.project / self.session.session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        old_file = session_dir / f"2026-01-01_{old_id}.jsonl"
        old_contents = json.dumps({"type": "session", "id": old_id}) + "\n"
        old_file.write_text(old_contents, encoding="utf-8")
        transaction = AgentHostTransaction(self.manager, self.project, "node", find_node=lambda *_: self.saved(),
                                           update_settings=AsyncMock(), now_ms=lambda: 0)
        transaction.hydrate(self.session, restored)
        self.assertIsNone(self.session.ext_session_id)
        self.assertIsNone(self.session.session_id)
        self.assertFalse(self.session.allow_session_discovery)
        argv = []
        process = NativePi(model="native/pi", effort="high")

        def spawn(command, **kwargs):
            argv.extend(command)
            new_id = command[command.index("--session-id") + 1]
            process.state["sessionId"] = new_id
            process.state["sessionFile"] = str(session_dir / f"2026-01-02_{new_id}.jsonl")
            return process

        async def run():
            try:
                self.assertTrue(await self.manager.spawn_session(self.session, continue_session=True))
            finally:
                await self.manager.stop_session(self.session)

        with patch.object(ah, "resolve_pi_cli", return_value="fake-pi.exe"), \
                patch.object(ah.subprocess, "Popen", side_effect=spawn):
            asyncio.run(run())
        self.assertNotIn("--continue", argv)
        self.assertNotIn(old_id, argv)
        self.assertNotEqual(self.saved()["agentHostSessions"]["pi"]["extSessionId"], old_id)
        self.assertEqual(self.saved()["agentHostSessions"]["pi"]["extSessionId"], process.state["sessionId"])
        self.assertEqual(old_file.read_text(encoding="utf-8"), old_contents)
        self.assertEqual(self.saved()["messages"], original["messages"])
        self.assertEqual(self.saved()["agentHostSettings"], original["agentHostSettings"])

    def test_legacy_fields_migrate_to_their_host_only(self):
        canvas = self.store.canvas(self.project)
        legacy = {"id": "node", "type": "smart-agent", "extSessionId": "legacy-pi", "model": "native/pi"}
        self.store.apply_agent_host_mutations(self.project, [{"op": "upsert_node", "node": legacy}], base_revision=canvas["revision"])
        self.manager._persist_ext_session_id(self.session)
        self.assertEqual(set(self.saved()["agentHostSessions"]), {"pi"})
        self.assertEqual(self.saved()["extSessionId"], "pi-new")


if __name__ == "__main__":
    unittest.main()
