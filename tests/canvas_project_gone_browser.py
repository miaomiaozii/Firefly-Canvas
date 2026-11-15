"""#377：项目被删后，画布页不能再反复 PATCH 一个不存在的 project_id（真实 headless-Chrome）。

缺陷：项目在另一个标签页被删（回收站 → 彻底删）之后，还开着的画布页按防抖/周期继续
PATCH /api/project-library/<id>/canvas，服务端每次都 404。旧 access 日志里这样刷了
15,183 行（三个项目各约 5,000 次），而且用户看不到任何提示。

这个测试把整条链路都跑真的：真发 PATCH、真走 HTTP 删除路由、真等防抖窗口。
修好前：PATCH 次数 >= 2、没有 #canvasGoneNotice、canvasProjectDeleted 不存在。
修好后：只发一次 404，随后出现常驻提示（导出 / 返回列表），自动保存停掉，
        当前画布内容还能导出成 JSON（不静默丢）。

跑法：
  python tests/canvas_project_gone_browser.py --artifacts-dir DIR --shots-dir DIR
"""
from __future__ import annotations
import sys as _bootstrap_sys
from pathlib import Path as _bootstrap_Path
_REPO_ROOT = str(_bootstrap_Path(__file__).resolve().parents[1])
if _REPO_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _REPO_ROOT)
from tools.process_tree import kill_process_tree
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

CLIENT_SCRIPT = ROOT / "tests" / "canvas_project_gone_client.js"
EXPECTED_ASSERTIONS = 10


def seed(store: ProjectStore, prefix: str) -> tuple[str, str]:
    project = store.create_project(f"{prefix} Project")
    pid = project["id"]
    store.apply_mutations(pid, [
        {"op": "upsert_node", "node": {"id": f"{prefix}-prompt", "type": "smart-prompt", "text": f"{prefix} prompt"}},
    ], base_revision=0)
    store.flush(pid)
    return pid, store.canvas(pid)["id"]


def run_one(browser, store: ProjectStore, work_root: Path, artifacts: Path) -> dict:
    pid, cid = seed(store, "Gone")
    app = harness.load_production_app(work_root, store)
    server = harness.ConversationBrowserServer(store, app)
    server.start()
    profile = Path(tempfile.mkdtemp(prefix="firefly-project-gone-"))
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
        kill_process_tree(proc)
        proc.wait(timeout=10)
        harness.remove_profile(profile)
        server.close()


def static_guards() -> list[dict]:
    """源码层守卫：返回按钮不能被「保存失败」困住（项目没了没地方可存）。

    这条在浏览器里没法安全验（会真的导航走），所以钉源码。
    """
    src = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
    lines = src.splitlines()
    entry = next((i for i, line in enumerate(lines) if line.startswith("async function backToProjectList")), None)
    window = "\n".join(lines[entry:entry + 12]) if entry is not None else ""
    return [{
        "name": "backToProjectList: 项目已删除时直接离开，不再拿保存失败把人困在页面上",
        "pass": entry is not None and "canvasProjectGone" in window,
        "actual": window.strip().splitlines()[0] if entry is not None else "backToProjectList 找不到",
    }]


def main() -> int:
    parser = argparse.ArgumentParser()
    # 默认落在 tmp/（.gitignore 内）：门里不带参数跑也不会弄脏 git，失败时现场还能人手复看。
    # （#377 首轮 trial 门就是被这里坑的：required=True 让步骤 argparse exit 2。）
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "tmp" / "qa" / "canvas-project-gone")
    parser.add_argument("--shots-dir", type=Path, default=ROOT / "tmp" / "qa" / "canvas-project-gone" / "shots")
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    harness.INJECT_PATHS = {harness.HTML_PAGE_PATH}
    browser = harness.find_browser()

    with tempfile.TemporaryDirectory(prefix="firefly-project-gone-projects-") as root:
        store = ProjectStore(Path(root) / "store", snapshot_delay=0)
        try:
            segment = run_one(browser, store, Path(root) / "runtime", args.artifacts_dir)
        finally:
            store.close()

    assertions = (segment.get("assertions") or []) + static_guards()
    for item in assertions:
        print(f"{'PASS' if item.get('pass') else 'FAIL'}  {item.get('name')}  actual={item.get('actual')}")
    for error in segment.get("errors") or []:
        print(f"ERROR  {error}")
    summary = {
        "issue": "377-deleted-project-autosave-loop",
        "pass": bool(segment.get("pass")) and len(assertions) == EXPECTED_ASSERTIONS + 1
                and all(item.get("pass") for item in assertions),
        "assertion_count": len(assertions),
        "patch_statuses": (segment.get("notes") or {}).get("patch_statuses"),
        "errors": segment.get("errors") or [],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
