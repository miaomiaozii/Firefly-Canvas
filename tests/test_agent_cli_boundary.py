"""#179 boundary contract: unit tests must never start a real agent CLI subprocess.

``app/services/agent_host.py`` resolves the pi CLI from ``FIREFLY_PI_CLI`` and
defaults to a bare ``"pi"``. On Windows that resolves through PATH to
``pi.cmd``, which the quality gate's executable guard refuses and records as a
``subprocess-executable`` boundary event. A unit test that reaches
``AgentHostManager.spawn_session`` therefore turns a green unittest run into a
red gate step (the step's own unittest summary still says ``OK``).

This module re-runs the agent unit-test modules inside the gate's own guard
(``tools.quality_gate.GUARD_SOURCE``) and asserts the run produces zero
``subprocess-executable`` events. It is the regression net: any future test that
spawns a real CLI (pi, node, an agent binary, …) fails here instead of only
showing up as an unexplained gate failure.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.quality_gate import (
    GUARD_SOURCE,
    trusted_executable_paths,
    trusted_tool_executable_paths,
)
from tests.fake_agent_host import install_stub_pi_cli, restore_stub_pi_cli


def setUpModule():
    """#179: this module asserts the stub is installed, so install it too."""
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


# The test modules that exercise app.services.agent_host. They must stay free of
# real subprocess spawns; the gate shards them into unit-contract-remainder.
AGENT_TEST_MODULES = (
    "tests.test_agent_tui",
    "tests.test_agent_concurrency",
    "tests.test_agent_fork",
    "tests.test_agent_node_settings",
)

# The guard is loaded by exec'ing a generated file before unittest starts, so the
# test run does not depend on PYTHONPATH (the gate rewrites PYTHONPATH for every
# child process, which would otherwise replace our own guard copy).
BOOTSTRAP = (
    "import runpy, sys;"
    "exec(open(sys.argv[1], encoding='utf-8').read(), {'__name__': '__main__'});"
    "sys.argv = ['unittest'] + sys.argv[2:];"
    "runpy.run_module('unittest', run_name='__main__')"
)


class TestPiCliIsNeverResolvedFromPath(unittest.TestCase):
    """The test environment must pin the agent host's CLI path to the stub.

    ``app.services.agent_host.PI_CLI`` defaults to the bare name ``"pi"``, which
    Windows resolves through PATH to ``pi.cmd``. A test that reaches
    ``spawn_session`` with that default launches a real agent, so the default
    must not survive inside the test environment.
    """

    def test_pi_cli_points_at_the_inert_stub(self):
        from app.services import agent_host

        stub = ROOT / "tests" / "stubs" / "pi_cli_stub.cmd"
        self.assertTrue(
            stub.is_file(),
            "tests/stubs/pi_cli_stub.cmd must exist as the inert pi CLI",
        )
        self.assertNotEqual(
            agent_host.PI_CLI,
            "pi",
            "agent_host.PI_CLI must not stay the bare 'pi' inside tests: PATH "
            f"resolves it to {shutil.which('pi')!r}",
        )
        self.assertEqual(
            os.path.normcase(str(agent_host.PI_CLI)),
            os.path.normcase(str(stub)),
            "agent_host.PI_CLI must point at the inert stub executable",
        )
        self.assertEqual(
            os.environ.get("FIREFLY_PI_CLI"),
            str(stub),
            "FIREFLY_PI_CLI must be pinned to the inert stub for child processes",
        )


class TestAgentCliSpawnBoundary(unittest.TestCase):
    """Agent unit tests must not trigger any guarded subprocess spawn."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-boundary-")
        self.work_root = Path(self.temp_dir.name)
        self.guard_events = self.work_root / "guard-events.jsonl"
        self.child_events = self.work_root / "child-events.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_guard(self) -> Path:
        allowed = trusted_executable_paths() + trusted_tool_executable_paths()
        tool_paths = trusted_tool_executable_paths()
        prelude = (
            "import json, os\n"
            f"os.environ['FIREFLY_QUALITY_GATE_GUARD_EVENTS'] = {str(self.guard_events)!r}\n"
            f"os.environ['FIREFLY_QUALITY_GATE_CHILD_EVENTS'] = {str(self.child_events)!r}\n"
            f"os.environ['FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES'] = json.dumps({allowed!r})\n"
            f"os.environ['FIREFLY_QUALITY_GATE_TOOL_EXECUTABLES'] = json.dumps({tool_paths!r})\n"
            "os.environ['FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES'] = '[]'\n"
            "os.environ['FIREFLY_QUALITY_GATE_NODE_OPTIONS'] = ''\n"
        )
        guard_script = self.work_root / "guard.py"
        guard_script.write_text(prelude + GUARD_SOURCE.lstrip(), encoding="utf-8")
        return guard_script

    def _run_guarded(self) -> tuple[int, str]:
        guard_script = self._write_guard()
        process = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, str(guard_script), "-b", *AGENT_TEST_MODULES],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        return process.returncode, (process.stdout or "") + (process.stderr or "")

    def test_agent_unit_tests_produce_no_subprocess_executable_events(self):
        returncode, output = self._run_guarded()

        # Guard against a vacuous pass: the guarded run must really have executed
        # the agent test modules and they must still be green on their own.
        self.assertEqual(returncode, 0, f"guarded test run failed:\n{output}")
        self.assertIn("Ran ", output, f"guarded run did not execute tests:\n{output}")
        self.assertIn("OK", output, f"guarded run was not green:\n{output}")

        events = []
        if self.guard_events.exists():
            events = [
                json.loads(line)
                for line in self.guard_events.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        violations = [event for event in events if event.get("kind") == "subprocess-executable"]
        self.assertEqual(
            violations,
            [],
            "agent unit tests started a real subprocess (boundary event): "
            f"{violations}\n--- guarded run output ---\n{output}",
        )


if __name__ == "__main__":
    unittest.main()
