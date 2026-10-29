"""#190：saveProjectCanvasUntilSettled 的重试循环必须是活的（真实 headless-Chrome）。

缺陷：循环里 `if(saved !== true) return false;` 让第一次失败就直接退出，
`maxAttempts` 参数完全无效。调用方（手动保存、四个挂起动作持久化）因此
在可重试的失败上过早放弃。

跑法：
  python tests/canvas_save_settle_retry_browser.py --artifacts-dir DIR --shots-dir DIR
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / "tests" / "canvas_save_settle_retry_client.js"


def seed(store: ProjectStore, prefix: str) -> tuple[str, str]:
    project = store.create_project(f"{prefix} Project")
    pid = project["id"]
    store.apply_mutations(pid, [
        {"op": "upsert_node", "node": {"id": f"{prefix}-prompt", "type": "smart-prompt", "text": f"{prefix} prompt"}},
    ], base_revision=0)
    store.flush(pid)
    return pid, store.canvas(pid)["id"]


def run_one(browser, store: ProjectStore, work_root: Path, artifacts: Path, shots: Path) -> dict:
    pid, cid = seed(store, "Settle")
    app = harness.load_production_app(work_root, store)
    server = harness.ConversationBrowserServer(store, app)
    server.start()
    profile = Path(tempfile.mkdtemp(prefix="firefly-settle-retry-"))
    url = (f"{server.base_url}{harness.HTML_PAGE_PATH}"
           f"?id={urllib.parse.quote(cid)}&project={urllib.parse.quote(pid)}&browserTest=1")
    proc = subprocess.Popen(
        [str(browser), "--headless=new", "--disable-gpu", "--disable-background-networking",
         "--disable-extensions", "--no-first-run", "--window-size=1424,900",
         f"--user-data-dir={profile}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )
    try:
        segment = server.wait_for_result(0, timeout=180)
        (artifacts / "segment.json").write_text(json.dumps(segment, ensure_ascii=False, indent=2), encoding="utf-8")
        return segment
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        harness.remove_profile(profile)
        server.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--shots-dir", type=Path, required=True)
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    harness.INJECT_PATHS = {harness.HTML_PAGE_PATH}
    browser = harness.find_browser()

    with tempfile.TemporaryDirectory(prefix="firefly-settle-retry-projects-") as root:
        store = ProjectStore(Path(root) / "store", snapshot_delay=0)
        try:
            segment = run_one(browser, store, Path(root) / "runtime", args.artifacts_dir, args.shots_dir)
        finally:
            store.close()

    assertions = segment.get("assertions") or []
    for item in assertions:
        print(f"{'PASS' if item.get('pass') else 'FAIL'}  {item.get('name')}  actual={item.get('actual')}")
    for error in segment.get("errors") or []:
        print(f"ERROR  {error}")
    summary = {
        "issue": "190-save-settle-retry-loop",
        "pass": bool(segment.get("pass")) and len(assertions) == 7,
        "assertion_count": len(assertions),
        "errors": segment.get("errors") or [],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
