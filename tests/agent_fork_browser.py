"""Browser test for Agent session branching (fork) UI - #142.

Verifies (in a real browser, no real pi needed):
- The agent TUI renders the fork button.
- A node with branches renders the branch select (with original + forked branches).
- The fork button click handler is bound (calls showForkPicker).
- The branch select change handler is bound (calls switchAgentBranch).
- The model/autonomy selects still render (regression: #140/#141 not broken).

The actual fork/switch operations (which require a real pi) are covered by the
unit tests in tests/test_agent_fork.py.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402

CLIENT_SCRIPT = ROOT / "tests" / "agent_fork_browser_client.js"


def seed_store(store: ProjectStore) -> dict:
    project = store.create_project("Agent Fork Suite")
    # Seed an agent node that already has branches (simulating a prior fork)
    node = {
        "id": "fork-agent",
        "type": "smart-agent",
        "x": 470,
        "y": 140,
        "w": 382,
        "h": 360,
        "title": "Fork Agent",
        "agentGoal": "",
        "branches": [
            {
                "id": "branch-original",
                "name": "原始",
                "session_file": "/tmp/original-session.jsonl",
                "session_id": "orig-session",
                "fork_from_entry_id": None,
                "created_at": 0,
            },
            {
                "id": "branch-abc123",
                "name": "分支 1",
                "session_file": "/tmp/forked-session.jsonl",
                "session_id": "forked-session",
                "fork_from_entry_id": "entry-1",
                "created_at": 0,
            },
        ],
        "current_branch": "branch-abc123",
    }
    store.apply_mutations(
        project["id"],
        [{"op": "upsert_node", "node": node}],
        base_revision=0,
    )
    store.flush(project["id"])
    return {
        "project_id": project["id"],
        "canvas_id": project["id"],
        "selected_text": "",
    }


def load_app(root: Path, store: ProjectStore):
    import importlib
    import os

    runtime_root = (root / "runtime").resolve()
    projects_root = (root / "unused-projects").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    os.environ["FIREFLY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(projects_root)
    module_name = f"_agent_fork_main_{__import__('uuid').uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot create an isolated production app module")
    previous_main_module = os.environ.get("FIREFLY_MAIN_MODULE")
    os.environ["FIREFLY_MAIN_MODULE"] = module_name
    for _name in list(sys.modules):
        if _name.startswith("app.") or _name in ("app", "main"):
            sys.modules.pop(_name, None)
    production_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = production_main
    try:
        spec.loader.exec_module(production_main)
    finally:
        sys.modules.pop(module_name, None)
        if previous_main_module is None:
            os.environ.pop("FIREFLY_MAIN_MODULE", None)
        else:
            os.environ["FIREFLY_MAIN_MODULE"] = previous_main_module
    previous_store = production_main.FIREFLY_PROJECT_STORE
    production_main.FIREFLY_PROJECT_STORE = store
    if previous_store is not None and previous_store is not store:
        previous_store.close()

    production_main.sync_static_html_versions = lambda: None
    production_main.app.state.issue13_module = production_main
    production_main.app.state.issue13_runtime_root = runtime_root
    production_main.app.state.issue13_projects_root = projects_root
    return production_main.app


def launch(browser: Path, server, viewport: tuple, fixture: dict):
    return harness.launch(browser, server, viewport, fixture, result_timeout=240)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    viewports = [(1424, 900)]
    browser = harness.find_browser()
    for viewport in viewports:
        with tempfile.TemporaryDirectory(prefix="agent-fork-project-") as root:
            store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
            fixture = seed_store(store)
            app = load_app(Path(root) / "runtime", store)
            server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            server.start()
            try:
                result = launch(browser, server, viewport, fixture)
                print(f"{tuple(viewport)} PASS: {result}")
            finally:
                server.close()


if __name__ == "__main__":
    main()
