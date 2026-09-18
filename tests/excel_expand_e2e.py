"""E2E：Excel 导入 → 展开 → 拓扑验证 + 截图自验收。

用法：python tests/excel_expand_e2e.py
产出：
  - 控制台：Plan IR / 展开结果 / 拓扑验证
  - 截图：<temp>/firefly-e2e-canvas.png（展开后的画布）

说明：
  - 用 conversation_browser harness 起隔离生产 app（临时 ProjectStore）。
  - 走真实 API：/api/excel/import-plan（xlsx→Plan IR）→ /api/excel/expand-plan（Plan IR→节点）。
  - 拓扑从 store 直接取（nodes/edges/runSettings），截图用 Chrome headless --screenshot。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from firefly_projects import ProjectStore  # noqa: E402
import conversation_browser as harness  # noqa: E402
from test_excel_importer import make_xlsx_bytes  # noqa: E402

FIXTURE_SHEETS = {
    "图片方案A": [
        ["名称", "要求"],
        ["1、主图1", "整体产品图，白底"],
        ["A+1、卖点模块1", "卖点特写，红色背景"],
        ["A+1、场景模块2", "使用场景，户外"],
    ],
}


def http_post_json(base: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_multipart(base: str, path: str, filename: str, file_bytes: bytes) -> dict:
    boundary = "----fireflye2eboundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode("utf-8")
    body = head + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        base + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def screenshot(browser: str, url: str, out: Path) -> bool:
    cmd = [
        str(browser), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--window-size=1440,900", "--virtual-time-budget=12000",
        f"--screenshot={out}", url,
    ]
    try:
        subprocess.run(cmd, timeout=90, capture_output=True)
        return out.exists() and out.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [screenshot 失败] {exc}", flush=True)
        return False


def main() -> int:
    browser = harness.find_browser()
    tmp = Path(tempfile.mkdtemp(prefix="firefly-e2e-"))
    store = ProjectStore(tmp / "projects", snapshot_delay=0)
    project = store.create_project("excel-e2e-verify")
    pid = project["id"]
    app = harness.load_production_app(tmp, store)
    server = harness.ConversationBrowserServer(store, app, client_script=harness.CLIENT_SCRIPT)
    server.start()
    base = server.base_url
    shot = Path(tempfile.gettempdir()) / "firefly-e2e-canvas.png"
    print(f"server={base}  project={pid}  browser={browser}", flush=True)
    exit_code = 0
    try:
        # 1) import-plan：xlsx → Plan IR
        xlsx = make_xlsx_bytes(FIXTURE_SHEETS)
        plan = http_post_multipart(base, "/api/excel/import-plan", "test.xlsx", xlsx)
        tasks = plan.get("tasks", [])
        print(f"\n[1] import-plan：Plan IR 任务数 = {len(tasks)}", flush=True)
        for t in tasks:
            print(
                f"    {t.get('display_no')} {t.get('name')!r} | "
                f"generation_refs={len(t.get('generation_references') or [])} "
                f"operation_refs={len(t.get('operation_references') or [])} "
                f"ratio={t.get('target_aspect_ratio')!r}",
                flush=True,
            )

        # 2) expand-plan：Plan IR → 节点
        resp = http_post_json(base, "/api/excel/expand-plan", {"project_id": pid, "plan": plan})
        print(f"\n[2] expand-plan：{resp.get('message')}  bounds={resp.get('bounds')}", flush=True)

        # 3) 从 store 取画布，验证拓扑
        canvas = store.open_project(pid)["canvas"]
        raw_dump = Path(tempfile.gettempdir()) / "firefly-e2e-canvas.json"
        raw_dump.write_text(json.dumps(canvas, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [raw canvas dumped] {raw_dump}  top-keys={list(canvas.keys())}", flush=True)
        nodes = canvas.get("nodes") or []
        edges = canvas.get("connections") or canvas.get("edges") or []
        prompts = [n for n in nodes if n.get("type") == "smart-prompt"]
        images = [n for n in nodes if n.get("type") == "smart-image"]
        gen_images = [n for n in images if (n.get("runSettings") or {}).get("prompt")]
        ref_images = [n for n in images if not (n.get("runSettings") or {}).get("prompt")]
        bad_types = [n for n in nodes if n.get("type") not in ("smart-prompt", "smart-image")]

        print(f"\n[3] 拓扑验证（store）：", flush=True)
        print(f"    节点总数={len(nodes)}  边总数={len(edges)}", flush=True)
        print(f"    smart-prompt(提示词)={len(prompts)}", flush=True)
        for n in prompts:
            print(f"      {n.get('title')!r} text={str(n.get('text'))[:30]!r}", flush=True)
        print(f"    smart-image 生图(runSettings)={len(gen_images)}", flush=True)
        for n in gen_images:
            rs = n.get("runSettings") or {}
            print(f"      {n.get('title')!r} model={rs.get('model')!r} ratio={rs.get('aspect_ratio')!r} pending={n.get('pending')}", flush=True)
        print(f"    smart-image 参考图={len(ref_images)}  ← 真实流程应为 0（generation_refs 恒空）", flush=True)
        print(f"    非预期节点类型(excel_plan/excel_agent 等)={len(bad_types)}  ← 应为 0", flush=True)
        for e in edges:
            print(f"      edge: {e.get('from')} -> {e.get('to')} ({e.get('kind')})", flush=True)

        # 断言核心拓扑
        problems = []
        if len(prompts) != len(tasks):
            problems.append(f"提示词节点数 {len(prompts)} != 任务数 {len(tasks)}")
        if len(gen_images) != len(tasks):
            problems.append(f"生图节点数 {len(gen_images)} != 任务数 {len(tasks)}")
        if bad_types:
            problems.append(f"存在非预期节点类型: {[n.get('type') for n in bad_types]}")
        for n in gen_images:
            rs = n.get("runSettings") or {}
            if not rs.get("model") or not rs.get("aspect_ratio"):
                problems.append(f"生图节点 {n.get('title')!r} runSettings 缺 model/ratio")
        if len(edges) < len(tasks):
            problems.append(f"边数 {len(edges)} < 任务数 {len(tasks)}（提示词→生图 应至少 1 条/任务）")

        # 4) 截图
        ok = screenshot(browser, f"{base}/static/smart-canvas.html?id={pid}", shot)
        print(f"\n[4] 截图：{'OK ' + str(shot) if ok else '失败（依赖 store 拓扑验证）'}", flush=True)

        print("\n" + ("✅ 核心拓扑验证通过" if not problems else "❌ 发现问题："), flush=True)
        for p in problems:
            print(f"   - {p}", flush=True)
        if problems:
            exit_code = 1
    finally:
        try:
            server.close()
        except Exception:  # noqa: BLE001
            pass
        store.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
