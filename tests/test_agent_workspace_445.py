"""#445 S1: real host/adapter wiring, replacing only subprocess pipes.

These checks prove launch/path behavior, not an OS sandbox or model file-tool
security. Stable filesystem redirects are real fixtures, not fake CLI verdicts.
"""
import asyncio
from contextlib import chdir
import io
import json
import os
from pathlib import Path
import queue
import stat
import tempfile
import unittest
from unittest.mock import patch

import main
from firefly_projects import ProjectStore
from app.services import agent_host


class PiPipes:
    """The external RPC pipe boundary; no native CLI or model is started."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.options = kwargs
        self.frames = queue.Queue()
        self.returncode = None
        self.stdin = self.stdout = self
        self.stderr = io.StringIO()

    def write(self, line):
        command = json.loads(line)
        if command["type"] != "get_state":
            raise AssertionError(f"Unexpected RPC: {command['type']}")
        native_id = (self.argv[self.argv.index("--session-id") + 1]
                     if "--session-id" in self.argv else "01a1aaaa-0000-7000-8000-000000000001")
        self.frames.put(json.dumps({
            "id": command["id"], "type": "response", "command": "get_state", "success": True,
            "data": {"sessionId": native_id, "isStreaming": False, "isCompacting": False,
                     "pendingMessageCount": 0},
        }) + "\n")

    def flush(self):
        pass

    def readline(self):
        return self.frames.get(timeout=5)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.frames.put("")

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class AgentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agent-workspace-445-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = ProjectStore(self.base / "library" / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        # Bind the real application/store, never pytest's incidental __main__ module.
        self.enterContext(patch.object(agent_host, "_app_main", main))
        self.enterContext(patch.object(main, "FIREFLY_PROJECT_STORE", self.store))
        self.project_id = self.store.create_project("Workspace 445")["id"]
        self.project = self.store.root / self.project_id
        self.manager = agent_host.AgentHostManager()
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.addCleanup(lambda: self.run_async(self.manager.cleanup_project(self.project_id)))
        self.enterContext(patch.dict(os.environ, {"FIREFLY_PI_CLI": "fixture-pi.exe"}))
        self.launches = []
        self.enterContext(patch.object(agent_host.subprocess, "Popen", side_effect=self.launch))

    def run_async(self, operation):
        return self.loop.run_until_complete(operation)

    def launch(self, argv, **kwargs):
        process = PiPipes(argv, **kwargs)
        self.launches.append(process)
        return process

    def session(self, node_id="node-445", **kwargs):
        return self.run_async(self.manager.get_or_create_session(self.project_id, node_id, **kwargs))

    def snapshot(self, root=None):
        root = self.base if root is None else root
        result = {}

        def visit(directory):
            for path in directory.iterdir():
                metadata = path.lstat()
                key = str(path.relative_to(root))
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                    result[key] = ("redirect", os.readlink(path))
                elif stat.S_ISDIR(metadata.st_mode):
                    result[key] = None
                    visit(path)
                else:
                    result[key] = path.read_bytes()
        visit(root)
        return result

    def test_rejected_root_does_not_write_pending_queue_bookkeeping(self):
        session = self.session(recover_history=False)
        session.status = "busy"
        self.run_async(self.manager.send_follow_up(session, "queued before process exit", request_id="queue-445"))
        session.status = "crashed"
        outside = self.base / "outside-with-project-metadata"
        outside.mkdir()
        (outside / "project.json").write_text("{}", encoding="utf-8")
        (outside / "sentinel.bin").write_bytes(b"untouched")
        parked = self.base / "parked-project"
        self.project.rename(parked)
        self.project.symlink_to(outside, target_is_directory=True)
        try:
            before = self.snapshot()
            self.assertFalse(self.run_async(self.manager.spawn_session(session)))
            self.assertIn("workspace guard", session.last_error)
            self.assertEqual(self.launches, [])
            self.assertEqual(self.snapshot(), before)
        finally:
            self.project.unlink()
            parked.rename(self.project)

    def test_host_control_file_aliases_cannot_redirect_host_writes(self):
        workspace = self.project / "agent_nodes" / "node-445" / "sessions"
        workspace.mkdir(parents=True)
        for kind in ("symlink", "hardlink"):
            for name in ("system-prompt.txt", "system-prompt.txt.tmp",
                         "2026-11-22T00-00-00_01a1aaaa-0000-7000-8000-000000000001.jsonl"):
                with self.subTest(kind=kind, name=name):
                    sentinel = self.base / f"sentinel-{kind}-{name}"
                    sentinel.write_bytes(b"not host-owned\x00\xff")
                    link = workspace / name
                    if link.exists():
                        link.unlink()
                    if kind == "symlink":
                        link.symlink_to(sentinel)
                    else:
                        link.hardlink_to(sentinel)
                    before = self.snapshot()
                    launched = len(self.launches)
                    session = self.session(recover_history=False)
                    try:
                        self.assertFalse(self.run_async(self.manager.spawn_session(session)))
                        self.assertIn("workspace guard", session.last_error)
                        self.assertEqual(len(self.launches), launched)
                        self.assertEqual(self.snapshot(), before)
                        self.assertEqual(sentinel.read_bytes(), b"not host-owned\x00\xff")
                    finally:
                        self.run_async(self.manager.cleanup_project(self.project_id))
                        if link.exists() or link.is_symlink():
                            link.unlink()

    def test_project_identifier_cannot_replace_or_alias_the_server_root(self):
        for bad in ("", ".", "..", "nested/project", "nested\\project", "CON", "project.", str(self.base)):
            with self.subTest(project_id=bad):
                before = self.snapshot()
                session = agent_host.AgentSession(project_id=bad, node_id="node-445")
                try:
                    self.assertFalse(self.run_async(self.manager.spawn_session(session)))
                    self.assertIn("workspace guard", session.last_error)
                    self.assertEqual(self.launches, [])
                    self.assertEqual(self.snapshot(), before)
                finally:
                    self.run_async(self.manager.stop_session(session))

    def test_suspicious_or_missing_project_roots_fail_closed_before_creation(self):
        roots = [self.base / "missing-library", self.base / "missing" / ".." / "escaped",
                 self.base / "odd.", self.base / "CON", self.base / "stream:alias", Path("relative-library")]
        if os.name == "nt":
            roots.extend([Path(self.base.drive + "drive-relative"),
                          Path("\\\\?\\" + str(self.base / "extended-library")),
                          Path("\\\\.\\" + str(self.base / "device-library"))])
        with chdir(self.base):
            for root in roots:
                with self.subTest(root=str(root)), patch.object(self.store, "root", root):
                    before = self.snapshot()
                    session = self.session(recover_history=False)
                    try:
                        self.assertFalse(self.run_async(self.manager.spawn_session(session)))
                        self.assertIn("workspace guard", session.last_error)
                        self.assertEqual(self.launches, [])
                        self.assertEqual(self.snapshot(), before)
                    finally:
                        self.run_async(self.manager.cleanup_project(self.project_id))

    def test_stable_workspace_redirects_are_rejected_before_creation(self):
        redirects = ("symlink", "junction") if os.name == "nt" else ("symlink",)
        for kind in redirects:
            for depth in range(6):
                with self.subTest(kind=kind, depth=depth):
                    target = self.base / f"outside-{kind}-{depth}"
                    target.mkdir()
                    (target / "sentinel.bin").write_bytes(b"outside must stay untouched\x00\xff")
                    paths = [self.store.root.parent, self.store.root, self.project,
                             self.project / "agent_nodes", self.project / "agent_nodes" / "node-445",
                             self.project / "agent_nodes" / "node-445" / "sessions"]
                    link = paths[depth]
                    link.parent.mkdir(parents=True, exist_ok=True)
                    parked = self.base / "parked"
                    existed = link.exists()
                    if existed:
                        link.rename(parked)
                    try:
                        if kind == "junction":
                            from _winapi import CreateJunction
                            CreateJunction(str(target), str(link))
                        else:
                            link.symlink_to(target, target_is_directory=True)
                        before = self.snapshot()
                        launched = len(self.launches)
                        session = self.session(recover_history=False)
                        self.assertFalse(self.run_async(self.manager.spawn_session(session)))
                        self.assertEqual(session.status, "crashed")
                        self.assertIn("workspace guard", session.last_error)
                        self.assertEqual(len(self.launches), launched)
                        self.assertEqual(self.snapshot(), before)
                        self.assertEqual((target / "sentinel.bin").read_bytes(), b"outside must stay untouched\x00\xff")
                    finally:
                        self.run_async(self.manager.cleanup_project(self.project_id))
                        if kind == "junction":
                            link.rmdir()
                        else:
                            link.unlink()
                        if existed:
                            parked.rename(link)

    def test_unsafe_node_components_are_rejected_before_any_creation(self):
        for bad in ("../escape", "..\\escape", "node/escape", "node\\escape", ".", "..", "",
                    "node.", "node ", "CON", "con.txt", "AUX", "NUL.json", "COM1", "lpt9.log",
                    (self.base.drive or "C:") + "relative", str(self.base / "absolute-escape"), "\\\\",
                    "\\\\?\\" + str(self.base / "device-escape"), "node:stream",
                    "node\0bad", "node..alias", "a|b", "a?b", "a*b", "a<b", "a>b", 'a"b'):
            with self.subTest(node_id=bad):
                session = self.session(bad, recover_history=False)
                before = self.snapshot()
                self.assertFalse(self.run_async(self.manager.spawn_session(session)))
                self.assertEqual(session.status, "crashed")
                self.assertTrue(session.last_error)
                self.assertEqual(self.launches, [])
                self.assertEqual(self.snapshot(), before, "Rejected IDs must not create directories or files")

    def test_normal_existing_node_ids_keep_their_exact_workspace(self):
        for node_id in ("01a1e05c-874a-4a9d-9519-af0293905c28", "c0c7341819af4131807e35de9a9d8e4a",
                        "node-1a2b3c4d5e6f", "agent_probe2", "node.a_1"):
            with self.subTest(node_id=node_id):
                session = self.session(node_id)
                self.assertTrue(self.run_async(self.manager.spawn_session(session)))
                launch = self.launches[-1]
                expected = str(self.project / "agent_nodes" / node_id / "sessions")
                self.assertEqual(launch.options["cwd"], expected)
                self.assertEqual(launch.options["env"]["FIREFLY_AGENT_WORKSPACE_ROOT"], expected)
                self.run_async(self.manager.stop_session(session))

    def test_first_launch_resume_and_server_restart_share_the_fixed_workspace(self):
        self.store.apply_mutations(self.project_id, [{"op": "upsert_node", "node": {
            "id": "node-445", "type": "smart-agent", "agentCli": "pi"}}], base_revision=0)
        session = self.session()
        self.assertTrue(self.run_async(self.manager.spawn_session(session)))
        first = self.launches[-1]
        self.assertNotIn("--continue", first.argv)
        self.assertNotIn("--session-id", first.argv)
        native_id = session.ext_session_id
        self.assertEqual(native_id, "01a1aaaa-0000-7000-8000-000000000001")
        workspace = self.project / "agent_nodes" / "node-445" / "sessions"
        native_file = workspace / "2026-11-22T00-00-00_01a1aaaa-0000-7000-8000-000000000001.jsonl"
        native_bytes = b'{"type":"session","id":"01a1aaaa-0000-7000-8000-000000000001"}\n'
        native_file.write_bytes(native_bytes)
        (workspace / "notes.txt").write_text("user workspace content", encoding="utf-8")
        self.run_async(self.manager.stop_session(session))
        self.assertTrue(self.run_async(self.manager.spawn_session(session, continue_session=True)))
        resumed = self.launches[-1]
        self.run_async(self.manager.stop_session(session))

        retained = next(node for node in self.store.canvas(self.project_id)["nodes"] if node["id"] == "node-445")
        self.assertEqual(retained["extSessionId"], native_id)
        restarted_manager = agent_host.AgentHostManager()
        restarted = self.run_async(restarted_manager.get_or_create_session(self.project_id, "node-445"))
        restarted_manager.select_runtime(restarted, "pi", ext_session_id=retained["extSessionId"])
        try:
            self.assertTrue(self.run_async(restarted_manager.spawn_session(restarted)))
            for launch in (resumed, self.launches[-1]):
                self.assertEqual(launch.options["cwd"], str(workspace))
                self.assertEqual(launch.options["env"]["FIREFLY_AGENT_WORKSPACE_ROOT"], str(workspace))
                self.assertEqual(launch.argv[launch.argv.index("--session-id") + 1], native_id)
                self.assertNotIn("--continue", launch.argv)
            self.assertEqual(native_file.read_bytes(), native_bytes)
            self.assertEqual((workspace / "notes.txt").read_text(encoding="utf-8"), "user workspace content")
        finally:
            self.run_async(restarted_manager.cleanup_project(self.project_id))

    def test_existing_non_directory_workspace_component_is_rejected_without_creation(self):
        agent_nodes = self.project / "agent_nodes"
        agent_nodes.mkdir()
        blocked = agent_nodes / "node-445"
        blocked.write_bytes(b"not a directory")
        before = self.snapshot()
        session = self.session(recover_history=False)
        self.assertFalse(self.run_async(self.manager.spawn_session(session)))
        self.assertIn("workspace guard", session.last_error)
        self.assertEqual(self.launches, [])
        self.assertEqual(self.snapshot(), before)

    def test_dangling_workspace_symlink_is_rejected_without_creating_its_target(self):
        agent_nodes = self.project / "agent_nodes"
        agent_nodes.symlink_to(self.base / "missing-target", target_is_directory=True)
        before = self.snapshot()
        session = self.session(recover_history=False)
        self.assertFalse(self.run_async(self.manager.spawn_session(session)))
        self.assertIn("workspace guard", session.last_error)
        self.assertEqual(self.launches, [])
        self.assertEqual(self.snapshot(), before)

    def test_pi_exposes_only_canvas_namespaces_and_restricted_file_tool_names(self):
        session = self.session()
        self.assertTrue(self.run_async(self.manager.spawn_session(session)))
        argv = self.launches[-1].argv
        self.assertIn("--no-builtin-tools", argv)
        self.assertEqual(argv.count("--tools"), 1)
        self.assertEqual(argv[argv.index("--tools") + 1],
                         "canvas.*,generate.*,media.*,viewport.*,agent.*,plan.*,ops.*,chat.*,read,write,edit")
        self.assertFalse({"bash", "powershell", "PowerShell", "codemode", "*"} & set(argv))

    def test_pi_uses_server_owned_node_workspace_for_cwd_and_extension_root(self):
        with patch.dict(os.environ, {"FIREFLY_AGENT_WORKSPACE_ROOT": "Z:/untrusted-override"}):
            session = self.session()
            self.assertTrue(self.run_async(self.manager.spawn_session(session)))
            launch = self.launches[-1]
            expected = str(self.project / "agent_nodes" / "node-445" / "sessions")
            self.assertEqual(launch.options.get("cwd"), expected)
            self.assertEqual(launch.options["env"]["FIREFLY_AGENT_WORKSPACE_ROOT"], expected)
            self.assertEqual(os.environ["FIREFLY_AGENT_WORKSPACE_ROOT"], "Z:/untrusted-override")
            prompt = Path(launch.argv[launch.argv.index("--system-prompt") + 1])
            self.assertEqual(prompt, Path(expected) / "system-prompt.txt")
            self.assertEqual(prompt.read_text(encoding="utf-8"), agent_host.DEFAULT_SYSTEM_PROMPT)
