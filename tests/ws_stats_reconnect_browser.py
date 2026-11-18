"""#382：画布 /ws/stats 断线后，对已删除/不存在的项目要停止重连（真实 headless-Chrome）。

缺陷：项目被删后（回收站 → 彻底删），还开着的画布页每 3 秒无条件重连 /ws/stats，
服务端对不存在的项目的升级请求返回 404，于是无限刷 404（浏览器 close 码只有 1006，
与网络断线无法区分——旧代码分不清，就一直连）。

本测试把链路跑真的：真实 headless-Chrome 里包 WebSocket 构造器数 /ws/stats 请求次数，
走真实 HTTP 路由删项目。修好前：删除后每 3 秒一次 404 持续刷；修好后：删除后最多
收尾一两次，超过 2 个重连周期后计数停止增长，页面认定项目已删除并给出导出/返回出口。
项目还在时断线仍重连（对照，不能被误杀）。

跑法：
  python tests/ws_stats_reconnect_browser.py --artifacts-dir DIR --shots-dir DIR
"""
from __future__ import annotations
import sys as _bootstrap_sys
from pathlib import Path as _bootstrap_Path
_REPO_ROOT = str(_bootstrap_Path(__file__).resolve().parents[1])
if _REPO_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _REPO_ROOT)
from tools.browser_process import browser_popen
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

CLIENT_SCRIPT = ROOT / "tests" / "ws_stats_reconnect_client.js"
EXPECTED_ASSERTIONS = 5


def seed(store: ProjectStore, prefix: str) -> tuple[str, str]:
    project = store.create_project(f"{prefix} Project")
    pid = project["id"]
    store.apply_mutations(pid, [
        {"op": "upsert_node", "node": {"id": f"{prefix}-prompt", "type": "smart-prompt", "text": f"{prefix} prompt"}},
    ], base_revision=0)
    store.flush(pid)
    return pid, store.canvas(pid)["id"]


def run_one(browser, store: ProjectStore, work_root: Path, artifacts: Path) -> dict:
    pid, cid = seed(store, "WsReconnect")
    app = harness.load_production_app(work_root, store)
    server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
    server.start()
    profile = Path(tempfile.mkdtemp(prefix="firefly-ws-reconnect-"))
    url = (f"{server.base_url}{harness.HTML_PAGE_PATH}"
           f"?id={urllib.parse.quote(cid)}&project={urllib.parse.quote(pid)}&browserTest=1")
    proc = browser_popen(
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
    """源码层守卫：钉住桥接层测不到的两条停止路径（真实浏览器行为）。

    桥接层把 WS 升级当普通 GET 转发，永远到不了「连接建立后服务端 1013 主动断开」
    的真实路径，所以这两条在源码里钉死。
    """
    src = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
    lines = src.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("function connectAssetLibrarySyncSocket()")), None)
    body = ""
    if start is not None:
        depth = 0
        for i in range(start, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            body += lines[i] + "\n"
            if depth == 0 and i > start:
                break
    has_gone_gate = "if(canvasProjectGone)" in body
    has_1013_stop = "1013" in body and "markCanvasProjectGone()" in body
    has_meta_probe = "/meta" in body and "404" in body
    return [
        {
            "name": "重连循环被 canvasProjectGone 门住（项目没了不再排重连）",
            "pass": has_gone_gate,
            "actual": "gate found" if has_gone_gate else "connectAssetLibrarySyncSocket 里没有 canvasProjectGone 检查",
        },
        {
            "name": "服务端 1013（项目清理）主动断开时认定项目没了并停止",
            "pass": has_1013_stop,
            "actual": "1013 -> markCanvasProjectGone found" if has_1013_stop else "没有 1013 处理",
        },
        {
            "name": "1006/404 类断开重连前先摸画布 meta，404 则认定没了",
            "pass": has_meta_probe,
            "actual": "meta probe + 404 found" if has_meta_probe else "没有 meta 探测",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    # 默认落在 tmp/（.gitignore 内）：门里不带参数跑也不会弄脏 git，失败时现场还能人手复看。
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "tmp" / "qa" / "ws-stats-reconnect")
    parser.add_argument("--shots-dir", type=Path, default=ROOT / "tmp" / "qa" / "ws-stats-reconnect" / "shots")
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    browser = harness.find_browser()

    with tempfile.TemporaryDirectory(prefix="firefly-ws-reconnect-projects-") as root:
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
        "issue": "382-ws-stats-stop-reconnect-to-deleted-project",
        "pass": bool(segment.get("pass")) and len(assertions) == EXPECTED_ASSERTIONS + 3
                and all(item.get("pass") for item in assertions),
        "assertion_count": len(assertions),
        "ws_notes": (segment.get("notes") or {}),
        "errors": segment.get("errors") or [],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
